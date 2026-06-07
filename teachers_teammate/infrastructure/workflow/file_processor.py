"""Per-file processing logic for the OCR pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from pathlib import Path
import threading

from ...exceptions import PipelineInputError
from ...interfaces import AnonymizationMap, Anonymizer, Corrector, DocumentCreator, Evaluator
from ..reporting import Reporter, StdoutReporter
from .cache_service import CacheContext, CacheReconciliationService
from .ocr_service import OCRStageService
from .preprocess_service import PreprocessService

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessingResult:
    """Result of processing a single input file through all pipeline stages."""

    ok: bool
    message: str
    preview_img: str
    raw_text: str
    correction_text: str
    evaluation_text: str
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileProcessorConfig:
    """Construction-time dependencies for :class:`FileProcessor`."""

    output_dir: Path
    language: str
    cache_service: CacheReconciliationService
    preprocess_service: PreprocessService
    ocr_stage_service: OCRStageService
    correction: Corrector | None
    evaluation: Evaluator | None
    doc_creator: DocumentCreator | None
    anonymizer: Anonymizer | None = None
    stop_event: threading.Event | None = None
    on_ocr_done: Callable[[str, str], None] | None = None
    reporter: Reporter = field(default_factory=StdoutReporter)


class _StageAbort(Exception):
    """Raised by stage helpers to short-circuit the pipeline with a ready result."""

    def __init__(self, result: ProcessingResult) -> None:
        super().__init__()
        self.result = result


class FileProcessor:
    """Executes all pipeline stages for a single input file.

    Receives pre-built services and optional components; returns a
    :class:`ProcessingResult` to the coordinator for dispatch.
    """

    def __init__(self, config: FileProcessorConfig) -> None:
        self._output_dir = config.output_dir
        self._language = config.language
        self._cache_service = config.cache_service
        self._preprocess_service = config.preprocess_service
        self._ocr_stage_service = config.ocr_stage_service
        self._correction = config.correction
        self._anonymizer = config.anonymizer
        self._evaluation = config.evaluation
        self._doc_creator = config.doc_creator
        self._stop_event = config.stop_event
        self._on_ocr_done = config.on_ocr_done
        self._reporter = config.reporter

    def process(self, file: Path) -> ProcessingResult:
        """Process *file* through all pipeline stages."""
        docx_file = self._output_dir / (file.stem + ".docx")
        try:
            ctx = self._prepare_cache(file)
            raw_text, preview_img, source_image, ctx = self._run_preprocess_or_cache(file, ctx)

            if self._stop_event and self._stop_event.is_set():
                raise _StageAbort(
                    ProcessingResult(False, "Stopped by user.", preview_img, raw_text, "", "")
                )

            correction_text, ctx, correction_warnings = self._run_correction_or_cache(
                file, raw_text, preview_img, ctx
            )
            evaluation_text, eval_warnings = self._run_evaluation_or_cache(
                file, correction_text, ctx
            )
            all_warnings = tuple(correction_warnings + eval_warnings)
        except _StageAbort as abort:
            return abort.result

        return self._write_docx(
            docx_file,
            file.stem,
            raw_text,
            correction_text,
            evaluation_text,
            preview_img,
            source_image,
            all_warnings,
        )

    # ── Stage helpers ─────────────────────────────────────────────────────

    def _prepare_cache(self, file: Path) -> CacheContext:
        try:
            return self._cache_service.prepare(file)
        except OSError as exc:
            raise _StageAbort(
                ProcessingResult(False, f"Could not read input file: {exc}", "", "", "", "")
            ) from exc

    def _run_preprocess_or_cache(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        """Handle cache-hit or full preprocess+OCR path.

        Returns (raw_text, preview_img, source_image, ctx) on success.
        """
        cache_state = ctx.state

        if cache_state.ocr_done and cache_state.raw_text:
            return self._load_ocr_from_cache(file, ctx)

        return self._run_preprocess_and_ocr(file, ctx)

    def _load_ocr_from_cache(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        cache_state = ctx.state
        raw_text = cache_state.raw_text
        preview_img = cache_state.preview_img
        source_image_str = cache_state.source_image or preview_img
        source_image = Path(source_image_str) if source_image_str else None
        if not preview_img and source_image is not None and source_image.exists():
            try:
                preview_img, ctx = self._cache_service.persist_preview_artifact(
                    file, ctx, source_image
                )
            except OSError:
                pass
        self._reporter.status("       → Using cached OCR output")
        if self._on_ocr_done:
            self._on_ocr_done(str(file.resolve()), file.name)
        return raw_text, preview_img, source_image, ctx

    def _run_preprocess_and_ocr(
        self, file: Path, ctx: CacheContext
    ) -> tuple[str, str, Path | None, CacheContext]:
        try:
            ocr_inputs, prep_steps, source_image, raw_text_hint = (
                self._preprocess_service.preprocess_input(file)
            )
        except (OSError, ValueError, RuntimeError, PipelineInputError) as exc:
            raise _StageAbort(
                ProcessingResult(False, f"Preprocessing failed: {exc}", "", "", "", "")
            ) from exc

        preview_img = ""
        preview_source = ocr_inputs[0] if ocr_inputs else source_image
        if preview_source is not None:
            try:
                preview_img, ctx = self._cache_service.persist_preview_artifact(
                    file, ctx, preview_source
                )
            except OSError as exc:
                raise _StageAbort(
                    ProcessingResult(
                        False, f"Could not persist preview image: {exc}", "", "", "", ""
                    )
                ) from exc

        if prep_steps:
            self._reporter.status(f"       → Preprocessing: {', '.join(prep_steps)}")

        if self._stop_event and self._stop_event.is_set():
            raise _StageAbort(ProcessingResult(False, "Stopped by user.", preview_img, "", "", ""))

        raw_text, ctx = self._run_ocr(
            file, ocr_inputs, raw_text_hint, preview_img, source_image, ctx
        )
        return raw_text, preview_img, source_image, ctx

    def _run_ocr(
        self,
        file: Path,
        ocr_inputs: list[Path],
        raw_text_hint: str | None,
        preview_img: str,
        source_image: Path | None,
        ctx: CacheContext,
    ) -> tuple[str, CacheContext]:
        page_count = 0
        if raw_text_hint is not None:
            raw_text = raw_text_hint
            self._reporter.status("       → Text input loaded (OCR skipped)")
        else:
            page_texts, ocr_err = self._ocr_stage_service.run_pages(ocr_inputs, self._language)
            if ocr_err:
                raise _StageAbort(ProcessingResult(False, ocr_err, preview_img, "", "", ""))
            page_count = len(page_texts)
            raw_text = (
                "\n".join(f"Page {i + 1}:\n{t}" for i, t in enumerate(page_texts))
                if page_count > 1
                else page_texts[0]
            )

        if not raw_text:
            raise _StageAbort(
                ProcessingResult(False, "Empty response from OCR model.", preview_img, "", "", "")
            )

        try:
            ctx = self._cache_service.record_ocr(
                file,
                ctx,
                raw_text=raw_text,
                preview_img=preview_img,
                source_image=preview_img or (str(source_image) if source_image else ""),
            )
        except OSError as exc:
            raise _StageAbort(
                ProcessingResult(
                    False, f"Could not persist OCR cache state: {exc}", preview_img, "", "", ""
                )
            ) from exc

        if raw_text_hint is None:
            self._reporter.status(f"       → OCR done ({page_count} page(s))")
            if self._on_ocr_done:
                self._on_ocr_done(str(file.resolve()), file.name)

        return raw_text, ctx

    def _run_correction_or_cache(
        self, file: Path, raw_text: str, preview_img: str, ctx: CacheContext
    ) -> tuple[str, CacheContext, list[str]]:
        if self._correction is None:
            return "", ctx, []

        cache_state = ctx.state
        if cache_state.correction_done and cache_state.correction_text:
            self._reporter.status("       → Using cached correction output")
            return cache_state.correction_text, ctx, []

        self._reporter.status("       → Running correction...")
        anon_map: AnonymizationMap = {}
        text_for_correction = raw_text
        if self._anonymizer is not None:
            text_for_correction, anon_map = self._anonymizer.anonymize(raw_text)
            self._reporter.status("       → PII anonymized before correction")

        corrected_text, corr_warning = self._correction.correct(text_for_correction, self._language)
        corrected_text = corrected_text or ""
        corr_warnings: list[str] = [corr_warning] if corr_warning else []

        if anon_map and corrected_text and self._anonymizer is not None:
            try:
                corrected_text = self._anonymizer.restore(corrected_text, anon_map)
            except (ValueError, KeyError) as exc:
                raise _StageAbort(
                    ProcessingResult(
                        False, f"PII restore failed: {exc}", preview_img, raw_text, "", ""
                    )
                ) from exc

        if corrected_text:
            try:
                ctx = self._cache_service.record_correction(
                    file, ctx, correction_text=corrected_text
                )
            except OSError as exc:
                _logger.warning("Could not save correction state for %s: %s", file.name, exc)

        return corrected_text, ctx, corr_warnings

    def _run_evaluation_or_cache(
        self,
        file: Path,
        correction_text: str,
        ctx: CacheContext,
    ) -> tuple[str, list[str]]:
        if self._evaluation is None or not correction_text:
            return "", []

        cache_state = ctx.state
        if cache_state.evaluation_done and cache_state.evaluation_text:
            self._reporter.status("       → Using cached evaluation output")
            return cache_state.evaluation_text, []

        self._reporter.status("       → Running evaluation...")
        eval_text, eval_warning = self._evaluation.evaluate(correction_text, self._language)
        eval_warnings: list[str] = [eval_warning] if eval_warning else []
        if eval_text:
            try:
                self._cache_service.record_evaluation(file, ctx, evaluation_text=eval_text)
            except OSError as exc:
                _logger.warning("Could not save evaluation state for %s: %s", file.name, exc)
        return eval_text or "", eval_warnings

    def _write_docx(
        self,
        docx_file: Path,
        stem: str,
        raw_text: str,
        corrected_text: str,
        evaluation_text: str,
        preview_img: str,
        source_image: Path | None,
        warnings: tuple[str, ...] = (),
    ) -> ProcessingResult:
        try:
            if self._doc_creator is not None:
                self._doc_creator.create(
                    raw_text,
                    corrected_text,
                    docx_file,
                    stem,
                    source_image=source_image,
                )
                saved = docx_file.name
            else:
                saved = "cache state updated"
        except Exception as exc:  # noqa: BLE001  # python-docx / PIL errors vary widely; surface message without crashing
            return ProcessingResult(
                False,
                f"DOCX creation failed: {exc}",
                preview_img,
                raw_text,
                corrected_text,
                evaluation_text,
            )
        return ProcessingResult(
            True, saved, preview_img, raw_text, corrected_text, evaluation_text, warnings
        )
