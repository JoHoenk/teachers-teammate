"""Cache reconciliation service for per-file pipeline state management."""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from ...cache_keys import (
    correction_key,
    ocr_key,
    preview_key,
    review_key,
    sent_record,
    with_task_text,
)
from ...config import Config
from ...domain.context_budget import RunWindows
from ...domain.document_state import DocumentState, task_text_of
from ...domain.freshness import (
    ExpectedHashes,
    correction_kept_over_settings,
    first_stale_stage_for_hashes,
    stage_hashes,
)
from ...domain.key_facts import KeyFacts
from ...domain.ocr_prompt import get_ocr_prompt
from ...domain.page_layout import ScanBox
from ...domain.stages import Stage
from ...domain.teacher_work import TeacherWork, text_identity
from ..ocr_formats import plain_text_of
from ..preview_image_store import PreviewImageStore
from ..state_repository import OcrResultRecord, StateRepository, compute_file_hash
from ..storage_root import document_stem
from ..teacher_work_store import TeacherWorkStore

# ── Context dataclass ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CacheContext:
    """All hash/state data resolved for one source file before stage execution."""

    state: DocumentState
    source_hash: str
    ocr_prompt: str
    ocr_config_hash: str
    correction_config_hash: str
    eval_config_hash: str
    preview_config_hash: str
    work: TeacherWork | None = None
    """What the teacher did to this document, as it was when the run prepared it."""
    proofreading_kept: bool = False
    """Whether the stored proofreading is kept although the settings changed (the teacher
    worked on its corrections).  The run says so instead of "using cached output"."""
    task_marks: tuple[str, ...] = ()
    """The printed text this run leaves alone; empty while ``skip_task_text`` is off."""
    not_task: tuple[str, ...] = ()
    """The lines the teacher proofreads after all, although a mark covers them."""


# ── Service ───────────────────────────────────────────────────────────────────


class CacheReconciliationService:
    """Loads, reconciles, and persists per-file cache state for the pipeline."""

    def __init__(
        self,
        *,
        state_repo: StateRepository,
        artifact_store: PreviewImageStore,
        config: Config,
        windows: RunWindows | None = None,
        facts: KeyFacts,
        work_store: TeacherWorkStore | None = None,
    ) -> None:
        self._repo = state_repo
        #: Where the teacher's work is; ``None`` reads none, which is what a tool that only
        #: wants the machine's results asks for.
        self._work_store = work_store
        #: What this computer adds to a cache key: its page models, its system language, the
        #: proofreading exchange.
        self._facts = facts
        self._artifacts = artifact_store
        self._config = config
        #: The context windows this run resolved from the settings and the computer, so the
        #: cache key follows the window the run really uses.  ``None`` — not resolved, which
        #: keys the settings alone (the base step for ``"auto"``).
        self._windows = windows

    # ── The keys of this document ────────────────────────────────────────────

    def _ocr_config_hash(self, source: Path) -> str:
        return ocr_key(self._config, source, self._facts)

    def _correction_config_hash(self) -> str:
        return correction_key(self._config, self._windows, self._facts)

    def _eval_config_hash(self) -> str:
        return review_key(self._config, self._windows)

    # ── Public API ────────────────────────────────────────────────────────────

    def prepare(self, file: Path) -> CacheContext:
        """Compute hashes, load state and the teacher's work, reconcile with runtime config."""
        cfg = self._config
        source_hash = compute_file_hash(file)
        work = self._work_store.load(source_hash) if self._work_store is not None else None
        state = self._repo.load_or_create(file, source_hash)

        ocr_prompt = get_ocr_prompt(cfg.language)
        ocr_config_hash = self._ocr_config_hash(file)
        # The task text this document leaves alone belongs to its proofreading key alone.
        correction_config_hash = with_task_text(
            cfg, self._correction_config_hash(), task_text_of(state, work, plain_text_of)
        )
        eval_config_hash = self._eval_config_hash()
        preview_config_hash = preview_key(cfg, file, source_hash, self._facts)

        # Capture artifact paths before reconciliation so we can delete them if cleared.
        old_preview_img = state.preview_img
        old_source_image = state.source_image
        old_original_img = state.original_img
        old_page_images = state.page_images
        old_original_images = state.original_images

        flags = stage_hashes(
            state,
            work,
            ExpectedHashes(
                ocr_config_hash=ocr_config_hash,
                correction_config_hash=correction_config_hash,
                eval_config_hash=eval_config_hash,
                correction_enabled=cfg.correction_enabled,
                evaluation_enabled=cfg.evaluation_enabled,
            ),
        )
        state = self._repo.reconcile(
            file,
            state,
            source_hash=source_hash,
            stale_stage=first_stale_stage_for_hashes(flags),
            preview_config_hash=preview_config_hash,
        )

        # If reconciliation cleared the preview artifacts, delete the actual files.
        if old_preview_img and not state.preview_img:
            self._artifacts.delete_preview_artifacts(
                old_preview_img,
                old_source_image,
                old_original_img,
                old_page_images,
                old_original_images,
            )

        return CacheContext(
            state=state,
            source_hash=source_hash,
            ocr_prompt=ocr_prompt,
            ocr_config_hash=ocr_config_hash,
            correction_config_hash=correction_config_hash,
            eval_config_hash=eval_config_hash,
            preview_config_hash=preview_config_hash,
            work=work,
            proofreading_kept=correction_kept_over_settings(flags),
            task_marks=state.task_marks if cfg.skip_task_text else (),
            not_task=work.not_task if cfg.skip_task_text and work is not None else (),
        )

    def persist_preview_artifact(
        self,
        file: Path,
        ctx: CacheContext,
        source_path: Path,
        *,
        original_paths: Sequence[Path] = (),
        preprocess_steps: Sequence[str] | None = None,
        pages: Sequence[Path] = (),
    ) -> tuple[str, CacheContext]:
        """Copy *source_path* to the stable artifact location; return (preview_img, updated_ctx).

        *original_paths* are the pages as they were scanned, in the order of *pages*; when
        given they are stored alongside the preview so the GUI can toggle between both.
        *preprocess_steps* records which steps produced the preview (``None`` = unknown,
        ``[]`` = none applied).

        *pages* are all prepared pages of the document in page order; page 1 is
        *source_path*.  Without it only that one page is stored.
        """
        page_images: list[str] = []
        if pages:
            stored = self._artifacts.persist_page_images(stem=document_stem(file), sources=pages)
            preview_img = str(stored[0])
            page_images = [str(page) for page in stored]
        else:
            stable = self._artifacts.persist_preview_image(
                stem=document_stem(file), source_path=source_path
            )
            preview_img = str(stable)
        original_images: list[str] = []
        if original_paths:
            # The originals are only for the GUI comparison toggle: never fail the file over
            # one of them.
            try:
                original_images = [
                    str(page)
                    for page in self._artifacts.persist_original_images(
                        stem=document_stem(file), sources=original_paths
                    )
                ]
            except OSError:
                original_images = []
        if not original_images:
            self._artifacts.prune_original_images(document_stem(file))
        new_state = self._repo.record_preview_artifact(
            file,
            ctx.state,
            preview_img=preview_img,
            source_image=preview_img,
            preview_config_hash=ctx.preview_config_hash,
            preprocess_steps=preprocess_steps,
            page_images=page_images,
            original_images=original_images,
        )
        return preview_img, dataclasses.replace(ctx, state=new_state)

    def record_ocr(
        self,
        file: Path,
        ctx: CacheContext,
        *,
        raw_text: str,
        preview_img: str,
        source_image: str,
        raw_text_format: str = "plain",
        layout: Sequence[ScanBox] = (),
    ) -> CacheContext:
        """Persist OCR result and return updated context.

        *layout* is where the lines of the reading stand in it, for the scan's marks; empty when
        none could be placed, which costs the marks and nothing else.
        """
        new_state = self._repo.record_ocr_result(
            file,
            ctx.state,
            OcrResultRecord(
                source_hash=ctx.source_hash,
                raw_text=raw_text,
                preview_img=preview_img,
                source_image=source_image,
                ocr_config_hash=ctx.ocr_config_hash,
                preview_config_hash=ctx.preview_config_hash,
                raw_text_format=raw_text_format,
                layout=tuple(layout),
            ),
        )
        return dataclasses.replace(ctx, state=new_state)

    def record_correction(
        self,
        file: Path,
        ctx: CacheContext,
        *,
        correction_text: str,
        correction_review: dict | None = None,
        correction_warning: str = "",
        sent_text: str = "",
        sent_map: dict[str, str] | None = None,
        source_text: str = "",
    ) -> CacheContext:
        """Persist correction result and return updated context.

        *sent_text* and *sent_map* are the text the proofreading was made from and its
        placeholder map — only when names were hidden.  With hiding off both stay empty: the
        text that was sent is the extracted text itself, and an edit of it clears the result,
        so the two cannot drift apart.

        *source_text* is the transcription the answer was made from; its identity is stored
        with the result, so a proofreading made from a text the document no longer has is
        stale (the teacher's transcription may be gone while the cache still holds one).
        """
        new_state = self._repo.record_correction_result(
            file,
            ctx.state,
            correction_text=correction_text,
            correction_config_hash=ctx.correction_config_hash,
            correction_review=correction_review,
            correction_warning=correction_warning,
            sent_text=sent_text,
            sent_map=sent_map,
            sent=sent_record(
                self._config,
                Stage.CORRECTION,
                names_hidden=len(sent_map or {}),
                facts=self._facts,
            ),
            source_hash=text_identity(source_text),
        )
        return dataclasses.replace(ctx, state=new_state)

    def record_evaluation(
        self,
        file: Path,
        ctx: CacheContext,
        *,
        evaluation_text: str,
        evaluation_warning: str = "",
        names_hidden: int = 0,
    ) -> CacheContext:
        """Persist evaluation result and return updated context.

        *names_hidden* is the number of placeholders the reviewed text was sent with.  The
        text itself is not stored: the content review is being redesigned, and what it sends
        is stored with that design.
        """
        new_state = self._repo.record_evaluation_result(
            file,
            ctx.state,
            evaluation_text=evaluation_text,
            eval_config_hash=ctx.eval_config_hash,
            evaluation_warning=evaluation_warning,
            sent=sent_record(
                self._config,
                Stage.EVALUATION,
                names_hidden=names_hidden,
                facts=self._facts,
            ),
        )
        return dataclasses.replace(ctx, state=new_state)
