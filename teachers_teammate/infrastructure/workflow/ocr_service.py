"""OCR stage service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from ...exceptions import OCRError, RunStoppedError, stop_requested
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

    def run_pages(
        self,
        ocr_inputs: list[Path],
        language: str,
        on_page: Callable[[int, int], None] | None = None,
    ) -> tuple[list[str], str | None]:
        """Run OCR on each input path; return ``(page_texts, error_message | None)``.

        *on_page* is called as ``on_page(page_number, total_pages)`` (1-based) right
        before each page is sent to the OCR engine.

        Raises:
            :exc:`~teachers_teammate.exceptions.RunStoppedError`: when the run is stopped
                before or while a page is read.  Not an error of the engine, so it is not
                returned as a message.
        """
        page_texts: list[str] = []
        for i, ocr_path in enumerate(ocr_inputs):
            if stop_requested(self._stop_event):
                raise RunStoppedError
            if on_page is not None:
                on_page(i + 1, len(ocr_inputs))
            try:
                result = self._processor.process_image(ocr_path, language=language)
            except OCRError as exc:
                if stop_requested(self._stop_event):
                    # The request was cut off by the stop (or failed while stopping): not the
                    # engine's fault.
                    raise RunStoppedError from exc
                label = f"page {i + 1}" if len(ocr_inputs) > 1 else "image"
                return [], f"OCR failed ({label}): {exc}"
            page_texts.append(clean_ocr_text(result))
        return page_texts, None
