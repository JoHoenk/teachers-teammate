"""Background worker that runs one OCR config and stores the result."""

from __future__ import annotations

import contextlib
from pathlib import Path
import threading

from PySide6.QtCore import QThread, Signal

from ...application.benchmark_service import BenchmarkApplicationService
from ...config import DEFAULTS, OcrConfig
from .._qt_stream import QtStream


class BenchmarkWorker(QThread):
    """Runs :meth:`BenchmarkApplicationService.run_and_store` off the GUI thread.

    Signals
    -------
    log_line(text)
    finished_ok(StoredRun)
    finished_err(message)
    """

    log_line = Signal(str)
    finished_ok = Signal(object)  # StoredRun
    finished_err = Signal(str)

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
        stream = QtStream(self.log_line)
        try:
            with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                run = self._service.run_and_store(
                    self._source,
                    ocr=self._ocr,
                    language=self._language,
                    ollama_url=self._ollama_url,
                    stop_event=self.stop_event,
                    on_progress=lambda msg: self.log_line.emit(msg + "\n"),
                )
        except (Exception, SystemExit) as exc:  # noqa: BLE001  # surface any failure to the GUI without crashing
            self.finished_err.emit(str(exc))
            return
        self.finished_ok.emit(run)
