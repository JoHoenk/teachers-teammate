"""Cache reconciliation service for per-file pipeline state management."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

from ...config import Config, OcrConfig
from ..ocr_processor import get_ocr_prompt
from ..preview_image_store import PreviewImageStore
from ..state_repository import (
    DocumentState,
    OcrResultRecord,
    RuntimeConfigSnapshot,
    StateRepository,
    compute_config_hash,
    compute_file_hash,
)

# ── Module-level hash helpers (shared with application layer) ─────────────────


def _preprocess_fingerprint(ocr: OcrConfig) -> tuple[str, ...]:
    """Return the preprocessing settings that change the image fed to OCR.

    Every entry must be included in both the OCR cache hash and the preview hash so a
    toggle (pre-step or PDF DPI) invalidates the cached result instead of returning stale
    OCR text or a stale preview image.
    """
    return (
        ocr.preprocess_method,
        str(ocr.pdf_render_dpi),
        str(ocr.dewarp),
        str(ocr.deskew),
        str(ocr.border_crop),
        str(ocr.denoise),
        str(ocr.gamma),
    )


def compute_ocr_config_hash(config: Config, ocr_prompt: str) -> str:
    """Return the OCR stage config hash for *config* and *ocr_prompt*."""
    ocr = config.ocr
    provider = ocr.provider if ocr.engine == "langchain" else ocr.engine
    model = ocr.effective_model if ocr.engine == "langchain" else ocr.model
    return compute_config_hash(
        "ocr",
        ocr.engine,
        provider,
        model,
        *_preprocess_fingerprint(ocr),
        config.language,
        ocr_prompt,
        format(ocr.temperature, ".1f"),
    )


def compute_correction_config_hash(config: Config) -> str:
    """Return the correction stage config hash for *config*, or '' when disabled."""
    if not config.correction_enabled:
        return ""
    return compute_config_hash(
        "correction",
        config.correction_provider,
        config.effective_correction_model,
        config.language,
        config.correction_prompt,
        format(config.correction_temperature, ".1f"),
    )


def compute_eval_config_hash(config: Config) -> str:
    """Return the evaluation stage config hash for *config*, or '' when disabled."""
    if not config.evaluation_enabled:
        return ""
    return compute_config_hash(
        "evaluation",
        config.evaluate_provider,
        config.effective_evaluate_model,
        config.language,
        config.evaluate_prompt,
        format(config.evaluate_temperature, ".1f"),
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


# ── Service ───────────────────────────────────────────────────────────────────


class CacheReconciliationService:
    """Loads, reconciles, and persists per-file cache state for the pipeline."""

    def __init__(
        self,
        *,
        state_repo: StateRepository,
        artifact_store: PreviewImageStore,
        config: Config,
    ) -> None:
        self._repo = state_repo
        self._artifacts = artifact_store
        self._config = config

    # ── Config hash helpers (delegate to module-level functions) ──────────────

    def _ocr_config_hash(self, ocr_prompt: str) -> str:
        return compute_ocr_config_hash(self._config, ocr_prompt)

    def _correction_config_hash(self) -> str:
        return compute_correction_config_hash(self._config)

    def _eval_config_hash(self) -> str:
        return compute_eval_config_hash(self._config)

    # ── Public API ────────────────────────────────────────────────────────────

    def prepare(self, file: Path) -> CacheContext:
        """Compute hashes, load state, reconcile with runtime config."""
        cfg = self._config
        source_hash = compute_file_hash(file)
        state = self._repo.load_or_create(file, source_hash)

        ocr_prompt = get_ocr_prompt(cfg.language)
        ocr_config_hash = self._ocr_config_hash(ocr_prompt)
        correction_config_hash = self._correction_config_hash()
        eval_config_hash = self._eval_config_hash()
        preview_config_hash = compute_config_hash(
            "preview", source_hash, *_preprocess_fingerprint(cfg.ocr)
        )

        # Capture artifact paths before reconciliation so we can delete them if cleared.
        old_preview_img = state.preview_img
        old_source_image = state.source_image

        state = self._repo.reconcile_runtime_config(
            file,
            state,
            RuntimeConfigSnapshot(
                source_hash=source_hash,
                ocr_prompt=ocr_prompt,
                correction_prompt=cfg.correction_prompt,
                evaluate_prompt=cfg.evaluate_prompt,
                ocr_config_hash=ocr_config_hash,
                correction_config_hash=correction_config_hash,
                eval_config_hash=eval_config_hash,
                preview_config_hash=preview_config_hash,
            ),
        )

        # If reconciliation cleared the preview artifacts, delete the actual files.
        if old_preview_img and not state.preview_img:
            self._artifacts.delete_preview_artifacts(old_preview_img, old_source_image)

        return CacheContext(
            state=state,
            source_hash=source_hash,
            ocr_prompt=ocr_prompt,
            ocr_config_hash=ocr_config_hash,
            correction_config_hash=correction_config_hash,
            eval_config_hash=eval_config_hash,
            preview_config_hash=preview_config_hash,
        )

    def persist_preview_artifact(
        self,
        file: Path,
        ctx: CacheContext,
        source_path: Path,
    ) -> tuple[str, CacheContext]:
        """Copy *source_path* to the stable artifact location; return (preview_img, updated_ctx)."""
        stable = self._artifacts.persist_preview_image(stem=file.stem, source_path=source_path)
        preview_img = str(stable)
        new_state = self._repo.record_preview_artifact(
            file,
            ctx.state,
            preview_img=preview_img,
            source_image=preview_img,
            preview_config_hash=ctx.preview_config_hash,
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
    ) -> CacheContext:
        """Persist OCR result and return updated context."""
        cfg = self._config
        new_state = self._repo.record_ocr_result(
            file,
            ctx.state,
            OcrResultRecord(
                source_hash=ctx.source_hash,
                raw_text=raw_text,
                preview_img=preview_img,
                source_image=source_image,
                ocr_prompt=ctx.ocr_prompt,
                correction_prompt=cfg.correction_prompt,
                evaluate_prompt=cfg.evaluate_prompt,
                ocr_config_hash=ctx.ocr_config_hash,
                preview_config_hash=ctx.preview_config_hash,
            ),
        )
        return dataclasses.replace(ctx, state=new_state)

    def record_correction(
        self,
        file: Path,
        ctx: CacheContext,
        *,
        correction_text: str,
    ) -> CacheContext:
        """Persist correction result and return updated context."""
        new_state = self._repo.record_correction_result(
            file,
            ctx.state,
            correction_text=correction_text,
            correction_prompt=self._config.correction_prompt,
            correction_config_hash=ctx.correction_config_hash,
        )
        return dataclasses.replace(ctx, state=new_state)

    def record_evaluation(
        self,
        file: Path,
        ctx: CacheContext,
        *,
        evaluation_text: str,
    ) -> CacheContext:
        """Persist evaluation result and return updated context."""
        new_state = self._repo.record_evaluation_result(
            file,
            ctx.state,
            evaluation_text=evaluation_text,
            evaluate_prompt=self._config.evaluate_prompt,
            eval_config_hash=ctx.eval_config_hash,
        )
        return dataclasses.replace(ctx, state=new_state)
