"""OCR-only runner for the benchmark app.

Runs a single OCR configuration over one document and returns the extracted
text, reusing the existing pipeline stages exactly as
:meth:`~teachers_teammate.infrastructure.pipeline.OCRPipeline._setup_services`
wires them — but without correction, evaluation, DOCX, or cache reconciliation.

Errors are caught and returned on the result (warn-don't-crash) so a single bad
engine never aborts a benchmarking session.  The :class:`PipelineComponentFactory`
is injectable, which keeps the runner unit-testable with a stub OCR processor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

from ...config import Config
from ..ollama_utils import OllamaClient
from ..stage_builder import PipelineComponentFactory, StageBuilder
from ..storage_root import resolve_artifact_dir
from ..workflow.ocr_service import OCRStageService


@dataclass(frozen=True)
class OcrRunResult:
    """Outcome of running one OCR config over one document."""

    raw_text: str
    preview_img: Path | None
    elapsed_s: float
    error: str | None


def run_ocr(
    config: Config,
    source: Path,
    *,
    component_factory: PipelineComponentFactory | None = None,
    stop_event: threading.Event | None = None,
) -> OcrRunResult:
    """Run OCR for *source* using *config* and return an :class:`OcrRunResult`.

    Args:
        config: A Config whose ``ocr`` slice selects the engine/model/preprocess.
            Correction/evaluation/DOCX flags are ignored (OCR-only path).
        source: The document to OCR (PDF, image, or TXT).
        component_factory: Optional stage-constructor overrides (test injection).
        stop_event: Optional cooperative cancellation signal.
    """
    factory = component_factory or PipelineComponentFactory()
    tmp_dir = resolve_artifact_dir(config.output_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    builder = StageBuilder(config, factory)
    start = time.monotonic()
    try:
        ollama_client = OllamaClient(config.ollama_url) if config.ocr.engine == "ollama" else None
        ocr = builder.build_ocr(ollama_client)
        preprocess_service = builder.build_preprocessor_service(tmp_dir)
        ocr_inputs, _steps, source_image, raw_text_hint = preprocess_service.preprocess_input(
            source
        )

        if raw_text_hint is not None:
            return OcrRunResult(raw_text_hint, None, time.monotonic() - start, None)

        ocr_stage = OCRStageService(processor=ocr, stop_event=stop_event)
        page_texts, error = ocr_stage.run_pages(ocr_inputs, config.language)
        if error is not None:
            return OcrRunResult("", None, time.monotonic() - start, error)

        preview = source_image or (ocr_inputs[0] if ocr_inputs else None)
        return OcrRunResult("\n\n".join(page_texts), preview, time.monotonic() - start, None)
    except Exception as exc:  # noqa: BLE001  # any stage may raise (engine missing, IO, provider); report, don't crash
        return OcrRunResult("", None, time.monotonic() - start, str(exc))
