"""Cache reconciliation service for per-file pipeline state management."""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from ...config import Config, OcrConfig
from ...domain.context_budget import RunWindows, window_from_setting
from ...domain.correction_policy import CorrectionOutput
from ...domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR, PROVIDER_OLLAMA, SentRecord
from ...domain.document_state import DocumentState
from ...domain.freshness import (
    ExpectedHashes,
    compute_config_hash,
    correction_kept_over_settings,
    first_stale_stage_for_hashes,
    stage_hashes,
)
from ...domain.local_models import auto_identity, reader_fingerprint
from ...domain.ocr_prompt import get_ocr_prompt
from ...domain.ollama_address import ollama_host
from ...domain.pdf_text_layer import input_fingerprint
from ...domain.stages import Stage
from ...domain.teacher_work import TeacherWork, text_identity
from ..onnx.store import ModelStore
from ..preprocessing.identity import available_page_steps
from ..preview_image_store import PreviewImageStore
from ..proofreading.corrector import exchange_identity
from ..state_repository import OcrResultRecord, StateRepository, compute_file_hash
from ..storage_root import document_stem
from ..teacher_work_store import TeacherWorkStore

# ── Module-level hash helpers (shared with application layer) ─────────────────


def _preprocess_fingerprint(
    ocr: OcrConfig, source: Path, store: ModelStore | None = None
) -> tuple[str, ...]:
    """Return the preprocessing settings that change the image fed to OCR.

    Every entry must be included in both the OCR cache hash and the preview hash so a changed
    setting (the method, the PDF DPI) invalidates the cached result instead of returning stale
    OCR text or a stale preview image.

    The automatic preparation adds what decides its pixels (``auto_identity``): its version,
    whether the engine gets gray or colour, and which page models are downloaded — so a new
    version of the steps, or downloading the page models, reads pages again once.  *store* is
    where the page models are (``None`` = the default folder).  "Original page" is just
    ``none``.

    *source* is the file being processed: format-specific input settings (the PDF text-layer
    mode) are only included for the formats they affect, so changing one does not invalidate
    results of other files.
    """
    text_layer = input_fingerprint(source.suffix, ocr.pdf_text_layer)
    identity = (
        auto_identity(ocr.engine, available_page_steps(store or ModelStore()))
        if ocr.preprocess_method == "auto"
        else ()
    )
    return (ocr.preprocess_method, *identity, str(ocr.pdf_render_dpi), *text_layer)


def compute_ocr_config_hash(
    config: Config, ocr_prompt: str, source: Path, store: ModelStore | None = None
) -> str:
    """Return the OCR stage config hash for *config* and *ocr_prompt* when OCR-ing *source*.

    *store* is where the page models are; it only matters for the automatic preparation.
    """
    ocr = config.ocr
    if ocr.engine == ENGINE_PPOCR:
        # The reader has no model tag, language or prompt of its own: a new model revision or
        # reader version re-reads the pages, a changed language or Ollama model does not.
        return compute_config_hash(
            Stage.OCR,
            ocr.engine,
            ocr.engine,
            reader_fingerprint(),
            *_preprocess_fingerprint(ocr, source, store),
            "-",
            "reader",
            "-",
        )
    return compute_config_hash(
        Stage.OCR,
        ocr.engine,
        # Two slots for one value: the second is the provider slot the key has room for, and
        # dropping it would change every OCR cache key.
        ocr.engine,
        ocr.model,
        *_preprocess_fingerprint(ocr, source, store),
        config.language,
        ocr_prompt,
        format(ocr.temperature, ".1f"),
    )


def anonymization_fingerprint(config: Config) -> tuple[str, ...]:
    """Return the hash parts of the anonymizer settings: what a text is made with.

    Empty when off.  The resolved models and patterns are included: they decide which spans
    become placeholders, so two texts made with different rules are different texts.

    Deliberately *not* part of a stage hash: a stored result is valid for the text it was
    made from, not for the setting that produced that text.  Hashing the setting would send
    every text again whenever a teacher turns names-hiding on or improves a pattern — and
    what was already sent cannot be unsent.  The window compares the settings instead
    (:func:`privacy_fingerprint`).
    """
    if not config.anonymization_enabled:
        return ()
    from ..anonymizer import anonymizer_config_from, resolve_anonymizer_config  # noqa: PLC0415
    from ..system_locale import system_language  # noqa: PLC0415

    # Deliberately without the installed models: hashing must not import spaCy.  So the key
    # holds the model the settings ask for, whether or not it is downloaded; downloading a
    # missing automatic model later does not change it.
    resolved = resolve_anonymizer_config(
        anonymizer_config_from(config),
        document_language=config.language,
        system_language=system_language(),
    )
    return (
        "anonymized",
        resolved.primary_model or "",
        resolved.secondary_model or "",
        *(f"{tag}={pattern}" for tag, pattern in resolved.patterns),
    )


def privacy_fingerprint(config: Config) -> str:
    """Return the hash of the anonymizer settings a text is made with.

    Compared with the fingerprint a stored result recorded, so the window can say that a
    result was made with other privacy settings.  Computed from the settings alone (no
    spaCy), so the comparison needs no run.
    """
    return compute_config_hash(*anonymization_fingerprint(config))


def sent_record(config: Config, stage: Stage, *, names_hidden: int = 0) -> SentRecord:
    """Return how a result of *stage* is sent with *config*.

    *names_hidden* is the number of placeholders the text was sent with (0 when names went
    as written).  The endpoint is what decides the location besides provider and model: the
    Ollama host.  A provider whose region depends on a setting (a custom base URL, a regional
    endpoint) adds that setting here; none does today, so an online provider has no endpoint.
    """
    if stage == Stage.EVALUATION:
        provider, model = config.evaluate_provider, config.effective_evaluate_model
    else:
        provider, model = config.correction_provider, config.effective_correction_model
    return SentRecord(
        provider=provider,
        model=model,
        endpoint=ollama_host(config.ollama_url) if provider == ENGINE_OLLAMA else "",
        names_hidden=names_hidden,
        hiding_on=config.anonymization_enabled,
        privacy=privacy_fingerprint(config),
    )


def _window_fingerprint(config: Config, windows: RunWindows | None = None) -> tuple[str, ...]:
    """Return the cache-key part for the context window of the proofreading requests.

    *windows* are the windows the run really uses, resolved from the settings and the
    computer.  Without them only the settings are known, which gives the pinned window and
    the base step for ``"auto"`` — the same key a run at the base window produces.
    """
    if config.correction_provider != PROVIDER_OLLAMA:
        return ()
    window = (
        windows.proofreading
        if windows is not None
        else window_from_setting(config.proofreading_context)
    )
    return (f"num-ctx-{window}",)


def _evaluation_window_fingerprint(
    config: Config, windows: RunWindows | None = None
) -> tuple[str, ...]:
    """Return the cache-key part for the context window of the content review.

    A larger window can make a review succeed that failed with "too long", so the cached
    failure of a smaller window must not be reused.  An online provider brings its own
    window, which the app does not set.
    """
    if config.evaluate_provider != PROVIDER_OLLAMA or windows is None:
        return ()
    return (f"num-ctx-{windows.evaluation}",)


def compute_correction_config_hash(config: Config, windows: RunWindows | None = None) -> str:
    """Return the correction stage config hash for *config*.

    The hash describes the settings, whatever the switch: a switched-off stage is "this run
    does not do it", not "throw the stored result away".  Only a stage that is enabled can
    make a stored result stale (see :func:`first_stale_stage_for_hashes`).
    """
    # What is sent, not the setting: the exchange's fixed prompts and the model's request
    # profile are what decide the requests, so both key the cache.  The profile carries every
    # temperature, the thinking and the sampling values of the steps, so a change of any of
    # them invalidates the cached corrections of that model and no other.
    profile = config.correction_profile
    return compute_config_hash(
        Stage.CORRECTION,
        config.correction_provider,
        config.effective_correction_model,
        config.language,
        exchange_identity(
            profile, corrections_only=config.correction_output == CorrectionOutput.TEXT
        ),
        # What the teacher set changes the answer itself, so it belongs in the key.
        config.correction_level,
        config.explanation_language,
        config.correction_output,
        config.correction_notes.strip(),
        # The window decides how a long text is split into requests, so a changed window must
        # proofread again.  Only an Ollama corrector uses it (an online provider has its own),
        # and only the window it really gets counts: a setting above the cap changes nothing.
        # *windows* is the window the run resolved from the settings and the computer; without
        # it the settings alone decide, which is the base step for "auto".
        *_window_fingerprint(config, windows),
    )


def compute_eval_config_hash(config: Config, windows: RunWindows | None = None) -> str:
    """Return the evaluation stage config hash for *config*.

    Like the proofreading's, it describes the settings whatever the switch.
    """
    return compute_config_hash(
        Stage.EVALUATION,
        config.evaluate_provider,
        config.effective_evaluate_model,
        config.language,
        config.evaluate_prompt,
        format(config.evaluate_temperature, "g"),
        str(config.evaluate_think),
        *_evaluation_window_fingerprint(config, windows),
    )


def expected_hashes(
    config: Config, file: Path, windows: RunWindows | None, model_store: ModelStore | None
) -> ExpectedHashes:
    """Return the stage hashes the settings of *config* ask for, for the document *file*.

    *windows* is ``None`` while this computer has not been probed; the hashes then key the
    settings alone, as the run's own base step does.
    """
    return ExpectedHashes(
        ocr_config_hash=compute_ocr_config_hash(
            config, get_ocr_prompt(config.language), file, model_store
        ),
        correction_config_hash=compute_correction_config_hash(config, windows),
        eval_config_hash=compute_eval_config_hash(config, windows),
        correction_enabled=config.correction_enabled,
        evaluation_enabled=config.evaluation_enabled,
    )


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
        model_store: ModelStore | None = None,
        work_store: TeacherWorkStore | None = None,
    ) -> None:
        self._repo = state_repo
        #: Where the teacher's work is; ``None`` reads none, which is what a tool that only
        #: wants the machine's results asks for.
        self._work_store = work_store
        #: Where the page models are; the automatic preparation's identity depends on them.
        self._model_store = model_store
        self._artifacts = artifact_store
        self._config = config
        #: The context windows this run resolved from the settings and the computer, so the
        #: cache key follows the window the run really uses.  ``None`` — not resolved, which
        #: keys the settings alone (the base step for ``"auto"``).
        self._windows = windows

    # ── Config hash helpers (delegate to module-level functions) ──────────────

    def _ocr_config_hash(self, ocr_prompt: str, source: Path) -> str:
        return compute_ocr_config_hash(self._config, ocr_prompt, source, self._model_store)

    def _correction_config_hash(self) -> str:
        return compute_correction_config_hash(self._config, self._windows)

    def _eval_config_hash(self) -> str:
        return compute_eval_config_hash(self._config, self._windows)

    # ── Public API ────────────────────────────────────────────────────────────

    def prepare(self, file: Path) -> CacheContext:
        """Compute hashes, load state and the teacher's work, reconcile with runtime config."""
        cfg = self._config
        source_hash = compute_file_hash(file)
        work = self._work_store.load(source_hash) if self._work_store is not None else None
        state = self._repo.load_or_create(file, source_hash)

        ocr_prompt = get_ocr_prompt(cfg.language)
        ocr_config_hash = self._ocr_config_hash(ocr_prompt, file)
        correction_config_hash = self._correction_config_hash()
        eval_config_hash = self._eval_config_hash()
        preview_config_hash = compute_config_hash(
            "preview", source_hash, *_preprocess_fingerprint(cfg.ocr, file, self._model_store)
        )

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
    ) -> CacheContext:
        """Persist OCR result and return updated context."""
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
            sent=sent_record(self._config, Stage.CORRECTION, names_hidden=len(sent_map or {})),
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
            sent=sent_record(self._config, Stage.EVALUATION, names_hidden=names_hidden),
        )
        return dataclasses.replace(ctx, state=new_state)
