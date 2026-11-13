"""Cache reconciliation service for per-file pipeline state management."""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from dataclasses import dataclass
from pathlib import Path

from ...config import Config, OcrConfig
from ...domain.context_budget import RunWindows, window_from_setting
from ..correction import instruction_parts
from ..correction_schema import CONTRACT_VERSION
from ..input_provider_factory import InputOptions, input_settings_fingerprint
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

# Bump a step's entry here whenever *its* algorithm changes (not just its on/off
# setting), so cached results computed with the old behaviour are treated as stale
# even though the user-visible flag didn't change. Steps not listed here have never
# changed their algorithm, so their fingerprint entry stays a plain "True"/"False".
# See image_preprocessor.py history for what changed at each version.
_PRESTEP_ALGO_VERSIONS = {"deskew": "2", "border_crop": "2"}


def _prestep_fingerprint(name: str, enabled: bool) -> str:
    """Return the fingerprint entry for one pre-step flag.

    Only folds the algorithm version in when the step is enabled, so configs that
    never used it (the common case) keep the same hash -- and cached results -- as
    before that step's algorithm changed.
    """
    if not enabled:
        return "False"
    version = _PRESTEP_ALGO_VERSIONS.get(name)
    return f"True@v{version}" if version else "True"


def _preprocess_fingerprint(ocr: OcrConfig, source: Path) -> tuple[str, ...]:
    """Return the preprocessing settings that change the image fed to OCR.

    Every entry must be included in both the OCR cache hash and the preview hash so a
    toggle (pre-step or PDF DPI) invalidates the cached result instead of returning stale
    OCR text or a stale preview image.

    Method "none" skips every pre-step (see HandwritingPreprocessor.preprocess), so the
    pre-step flags are folded to "False" in that case -- toggling them has no effect on
    the image and must not force a re-run.

    *source* is the file being processed: format-specific input settings (the PDF text-layer
    mode) are only included for the formats they affect, so changing one does not invalidate
    results of other files.
    """
    runs_presteps = ocr.preprocess_method != "none"
    text_layer = input_settings_fingerprint(source.suffix, InputOptions.from_ocr(ocr))
    return (
        ocr.preprocess_method,
        str(ocr.pdf_render_dpi),
        *text_layer,
        _prestep_fingerprint("dewarp", runs_presteps and ocr.dewarp),
        _prestep_fingerprint("deskew", runs_presteps and ocr.deskew),
        _prestep_fingerprint("border_crop", runs_presteps and ocr.border_crop),
        _prestep_fingerprint("denoise", runs_presteps and ocr.denoise),
        _prestep_fingerprint("gamma", runs_presteps and ocr.gamma),
    )


def compute_ocr_config_hash(config: Config, ocr_prompt: str, source: Path) -> str:
    """Return the OCR stage config hash for *config* and *ocr_prompt* when OCR-ing *source*."""
    ocr = config.ocr
    return compute_config_hash(
        "ocr",
        ocr.engine,
        # The engine is hashed twice on purpose: a "provider" slot used to sit here, and
        # keeping the shape keeps every existing cached OCR result valid.
        ocr.engine,
        ocr.model,
        *_preprocess_fingerprint(ocr, source),
        config.language,
        ocr_prompt,
        format(ocr.temperature, ".1f"),
    )


def _anonymization_fingerprint(config: Config) -> tuple[str, ...]:
    """Return the hash parts for anonymisation: correction and evaluation both see its text.

    Empty when off, so results cached before the flag was hashed stay valid.  The resolved
    models and patterns are included: they decide which spans become placeholders, so a
    changed rule sends a different text and an older proofread of it must not be reused.
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


def _window_fingerprint(config: Config, windows: RunWindows | None = None) -> tuple[str, ...]:
    """Return the cache-key part for the context window of the proofreading requests.

    *windows* are the windows the run really uses, resolved from the settings and the
    computer.  Without them only the settings are known, which gives the pinned window and
    the base step for ``"auto"`` — the same key a run at the base window produces.
    """
    if config.correction_provider != "ollama":
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
    if config.evaluate_provider != "ollama" or windows is None:
        return ()
    return (f"num-ctx-{windows.evaluation}",)


def compute_correction_config_hash(config: Config, windows: RunWindows | None = None) -> str:
    """Return the correction stage config hash for *config*, or '' when disabled."""
    if not config.correction_enabled:
        return ""
    # What is sent, not the setting: "" means "the built-in text", which can change with the
    # app, so the resolved instructions and answer formats are what must key the cache.  The
    # model's request profile is resolved here too, so a profile change invalidates the cached
    # corrections of that model and no other.
    profile = config.correction_profile
    policy, review_format = instruction_parts(
        config.correction_prompt,
        config.language,
        level=config.correction_level,
        text_format="plain",
        mode="review",
        answer_format=config.answer_format_review,
        compact_json=profile.compact_json,
    )
    _, text_format = instruction_parts(
        config.correction_prompt,
        config.language,
        level=config.correction_level,
        text_format="plain",
        mode="text",
        answer_format=config.answer_format_text,
    )
    return compute_config_hash(
        "correction",
        config.correction_provider,
        config.effective_correction_model,
        config.language,
        policy,
        review_format,
        text_format,
        format(profile.temperature, "g"),
        str(profile.think),
        # Review settings change the answer itself, so they belong in the key.
        config.correction_level,
        config.explanation_language,
        config.correction_output,
        f"contract-{CONTRACT_VERSION}",
        # The window decides how a long text is split into requests, so a changed window must
        # proofread again.  Only an Ollama corrector uses it (an online provider has its own),
        # and only the window it really gets counts: a setting above the cap changes nothing.
        # *windows* is the window the run resolved from the settings and the computer; without
        # it the settings alone decide, which is the base step for "auto".
        *_window_fingerprint(config, windows),
        *_anonymization_fingerprint(config),
    )


def compute_eval_config_hash(config: Config, windows: RunWindows | None = None) -> str:
    """Return the evaluation stage config hash for *config*, or '' when disabled."""
    if not config.evaluation_enabled:
        return ""
    return compute_config_hash(
        "evaluation",
        config.evaluate_provider,
        config.effective_evaluate_model,
        config.language,
        config.evaluate_prompt,
        format(config.evaluate_temperature, "g"),
        str(config.evaluate_think),
        *_evaluation_window_fingerprint(config, windows),
        *_anonymization_fingerprint(config),
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
        windows: RunWindows | None = None,
    ) -> None:
        self._repo = state_repo
        self._artifacts = artifact_store
        self._config = config
        #: The context windows this run resolved from the settings and the computer, so the
        #: cache key follows the window the run really uses.  ``None`` — not resolved, which
        #: keys the settings alone (the base step for ``"auto"``).
        self._windows = windows

    # ── Config hash helpers (delegate to module-level functions) ──────────────

    def _ocr_config_hash(self, ocr_prompt: str, source: Path) -> str:
        return compute_ocr_config_hash(self._config, ocr_prompt, source)

    def _correction_config_hash(self) -> str:
        return compute_correction_config_hash(self._config, self._windows)

    def _eval_config_hash(self) -> str:
        return compute_eval_config_hash(self._config, self._windows)

    # ── Public API ────────────────────────────────────────────────────────────

    def prepare(self, file: Path) -> CacheContext:
        """Compute hashes, load state, reconcile with runtime config."""
        cfg = self._config
        source_hash = compute_file_hash(file)
        state = self._repo.load_or_create(file, source_hash)

        ocr_prompt = get_ocr_prompt(cfg.language)
        ocr_config_hash = self._ocr_config_hash(ocr_prompt, file)
        correction_config_hash = self._correction_config_hash()
        eval_config_hash = self._eval_config_hash()
        preview_config_hash = compute_config_hash(
            "preview", source_hash, *_preprocess_fingerprint(cfg.ocr, file)
        )

        # Capture artifact paths before reconciliation so we can delete them if cleared.
        old_preview_img = state.preview_img
        old_source_image = state.source_image
        old_original_img = state.original_img

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
            self._artifacts.delete_preview_artifacts(
                old_preview_img, old_source_image, old_original_img
            )

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
        *,
        original_path: Path | None = None,
        preprocess_steps: Sequence[str] | None = None,
    ) -> tuple[str, CacheContext]:
        """Copy *source_path* to the stable artifact location; return (preview_img, updated_ctx).

        *original_path* is the un-preprocessed page; when given it is stored alongside the
        preview so the GUI can toggle between both.  *preprocess_steps* records which steps
        produced the preview (``None`` = unknown, ``[]`` = none applied).
        """
        stable = self._artifacts.persist_preview_image(stem=file.stem, source_path=source_path)
        preview_img = str(stable)
        original_img = ""
        if original_path is not None:
            # The original is only for the GUI comparison toggle: never fail the file over it.
            try:
                original_img = str(
                    self._artifacts.persist_original_image(
                        stem=file.stem, source_path=original_path
                    )
                )
            except OSError:
                original_img = ""
        if not original_img:
            self._artifacts.prune_original_images(file.stem)
        new_state = self._repo.record_preview_artifact(
            file,
            ctx.state,
            preview_img=preview_img,
            source_image=preview_img,
            preview_config_hash=ctx.preview_config_hash,
            original_img=original_img,
            preprocess_steps=preprocess_steps,
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
    ) -> CacheContext:
        """Persist correction result and return updated context."""
        new_state = self._repo.record_correction_result(
            file,
            ctx.state,
            correction_text=correction_text,
            correction_prompt=self._config.correction_prompt,
            correction_config_hash=ctx.correction_config_hash,
            correction_review=correction_review,
            correction_warning=correction_warning,
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
