"""OCR stage service."""

from __future__ import annotations

from pathlib import Path
import threading

from ...exceptions import OCRError
from ...interfaces import OCRProcessor
from ..ocr_text_cleaner import clean_ocr_text


class OCRStageService:
    """Runs OCR extraction over one or more prepared image paths."""

    def __init__(
        self,
        *,
        processor: OCRProcessor,
        stop_event: threading.Event | None,
    ) -> None:
        self._processor = processor
        self._stop_event = stop_event

    def run_pages(self, ocr_inputs: list[Path], language: str) -> tuple[list[str], str | None]:
        """Run OCR on each input path; return ``(page_texts, error_message | None)``."""
        page_texts: list[str] = []
        for i, ocr_path in enumerate(ocr_inputs):
            if self._stop_event and self._stop_event.is_set():
                return [], "Stopped by user."
            try:
                result = self._processor.process_image(ocr_path, language=language)
            except OCRError as exc:
                label = f"page {i + 1}" if len(ocr_inputs) > 1 else "image"
                return [], f"OCR failed ({label}): {exc}"
            page_texts.append(clean_ocr_text(result))
        return page_texts, None
