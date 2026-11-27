"""OCR stage service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

from ...domain.page_layout import PageReading
from ...domain.stages import Stage
from ...exceptions import OCRError, RunStoppedError, StageError, stop_requested
from ...interfaces import OCRProcessor
from ...messages import message_of, ocr_failed
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

    def read_pages(
        self,
        ocr_inputs: list[Path],
        language: str,
        on_page: Callable[[int, int], None] | None = None,
        on_page_done: Callable[[str], None] | None = None,
    ) -> list[PageReading]:
        """Run OCR on each input path and return what every page gave: its text, and its lines.

        The lines are empty for an engine that has no layout to give.  *on_page* is called as
        ``on_page(page_number, total_pages)`` (1-based) right before each page is sent to the OCR
        engine, *on_page_done* with the page's text right after it was read.

        Raises:
            :exc:`~teachers_teammate.exceptions.StageError`: when the engine could not read a
                page.  The message names the page; no page text is returned, because a document
                read only in part is not a transcription.
            :exc:`~teachers_teammate.exceptions.RunStoppedError`: when the run is stopped
                before or while a page is read.  Not an error of the engine.
        """
        readings: list[PageReading] = []
        for i, ocr_path in enumerate(ocr_inputs):
            if stop_requested(self._stop_event):
                raise RunStoppedError
            if on_page is not None:
                on_page(i + 1, len(ocr_inputs))
            try:
                result = self._processor.read_page(ocr_path, language=language)
            except OCRError as exc:
                if stop_requested(self._stop_event):
                    # The request was cut off by the stop (or failed while stopping): not the
                    # engine's fault.
                    raise RunStoppedError from exc
                page = i + 1 if len(ocr_inputs) > 1 else None
                raise StageError(
                    ocr_failed(page, message_of(exc)), kind=exc.kind, stage=Stage.OCR
                ) from exc
            reading = PageReading(text=clean_ocr_text(result.text), lines=result.lines)
            readings.append(reading)
            if on_page_done is not None:
                on_page_done(reading.text)
        return readings
