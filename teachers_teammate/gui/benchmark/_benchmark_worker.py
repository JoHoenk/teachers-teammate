"""Background worker that runs one OCR config and stores the result."""

from __future__ import annotations

import logging
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from ...application.benchmark_service import BenchmarkApplicationService, BenchmarkRunStopped
from ...config import OcrConfig
from ...messages import log_failure, message_of
from ...settings_schema import DEFAULTS

_logger = logging.getLogger(__name__)


class BenchmarkWorker(QThread):
    """Runs :meth:`BenchmarkApplicationService.run_and_store` off the GUI thread.

    Signals
    -------
    progress(text)
    finished_ok(StoredRun)
    finished_err(message)
    finished_stopped()
    """

    progress = Signal(str)  # what the run is doing, one line at a time
    finished_ok = Signal(object)  # StoredRun
    finished_err = Signal(str)  # what to tell the teacher; the log holds the failure
    finished_stopped = Signal()

    def __init__(
        self,
        source: Path,
        ocr: OcrConfig,
        language: str,
        *,
        ollama_url: str = DEFAULTS["ollama_url"],
        app_service: BenchmarkApplicationService,
    ) -> None:
        super().__init__()
        self._source = source
        self._ocr = ocr
        self._language = language
        self._ollama_url = ollama_url
        self._service = app_service
        self.stop_event = threading.Event()

    def run(self) -> None:
        try:
            run = self._service.run_and_store(
                self._source,
                ocr=self._ocr,
                language=self._language,
                ollama_url=self._ollama_url,
                stop_event=self.stop_event,
                on_progress=self._on_progress,
            )
        except BenchmarkRunStopped:
            self.finished_stopped.emit()
            return
        except (Exception, SystemExit) as exc:  # noqa: BLE001  # a failure goes to the window, never out of the thread
            log_failure(_logger, "The benchmark run ended with an error", exc)
            self.finished_err.emit(str(message_of(exc)))
            return
        self.finished_ok.emit(run)

    def _on_progress(self, message: str) -> None:
        """Hand one line of what the run is doing to the window."""
        self.progress.emit(message)
