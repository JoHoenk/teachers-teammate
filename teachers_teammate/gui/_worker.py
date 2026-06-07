"""OCR worker thread and stream helpers."""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

from PySide6.QtCore import QThread, Signal

from ..application.service import ProcessingApplicationService
from ..config import Config
from ..infrastructure.reporting import CallbackReporter
from ._types import FileDoneEvent


class _RunSelectedService(Protocol):
    def run_selected(
        self,
        config: Config,
        /,
        *,
        selected_source_paths: list[str] | None = None,
        stop_event: threading.Event | None = None,
        reporter=None,
        on_file_started=None,
        on_ocr_done=None,
        on_file_done=None,
    ) -> int: ...


class _ConnectionService(Protocol):
    def check_connection(
        self,
        *,
        engine: str = "",
        provider: str = "",
        url: str = "http://127.0.0.1:11434",
        model: str = "",
    ) -> tuple[bool, bool, str]: ...


class OCRWorker(QThread):
    """Runs :class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline` in a background thread.

    Signals
    -------
    log_line(text)
    file_started(source_id, name, idx, total)
    ocr_done(source_id, name, idx, total)
    file_done(FileDoneEvent)
    finished_with_code(rc)
    """

    log_line = Signal(str)
    file_started = Signal(str, str, int, int)  # (source_id, name, idx, total)
    ocr_done = Signal(str, str, int, int)  # (source_id, name, idx, total)
    file_done = Signal(object)  # FileDoneEvent
    finished_with_code = Signal(int)

    def __init__(
        self,
        config: Config,
        selected_source_paths: list[str] | None = None,
        *,
        app_service: _RunSelectedService | None = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._selected_source_paths = selected_source_paths
        self._app_service = app_service or ProcessingApplicationService()
        self.stop_event = threading.Event()

    def run(self) -> None:
        # The pipeline narrative arrives through an explicit reporter (no stdout
        # scraping); forward each line to the log pane, restoring the trailing
        # newline the log widget splits on.
        reporter = CallbackReporter(
            lambda line: self.log_line.emit(line if line.endswith("\n") else line + "\n")
        )

        # Per-file timing tracked via structured callbacks — no stdout parsing.
        file_start_times: dict[str, float] = {}
        ocr_done_times: dict[str, float] = {}
        file_indices: dict[str, tuple[int, int]] = {}  # source_id → (idx, total)

        def on_file_started(source_id: str, name: str, idx: int, total: int) -> None:
            file_start_times[source_id] = time.monotonic()
            ocr_done_times.pop(source_id, None)
            file_indices[source_id] = (idx, total)
            self.file_started.emit(source_id, name, idx, total)

        def on_ocr_done(source_id: str, name: str) -> None:
            ocr_done_times[source_id] = time.monotonic()
            idx, total = file_indices.get(source_id, (0, 0))
            self.ocr_done.emit(source_id, name, idx, total)

        def on_file_done(
            source_id: str,
            name: str,
            ok: bool,
            msg: str,
            preview_img: str,
            raw_txt: str,
            corr_txt: str,
            eval_txt: str,
        ) -> None:
            now = time.monotonic()
            start = file_start_times.get(source_id, now)
            ocr_t = ocr_done_times.get(source_id, now)
            if source_id in ocr_done_times:
                ocr_s = ocr_t - start
                correction_s = now - ocr_t
            else:
                ocr_s = now - start
                correction_s = 0.0
            self.file_done.emit(
                FileDoneEvent(
                    source_id=source_id,
                    name=name,
                    ok=ok,
                    message=msg,
                    ocr_s=ocr_s,
                    correction_s=correction_s,
                    preview_img=preview_img,
                    raw_txt=raw_txt,
                    corr_txt=corr_txt,
                    eval_txt=eval_txt,
                )
            )

        try:
            pipeline_kwargs: dict[str, Any] = {
                "stop_event": self.stop_event,
                "reporter": reporter,
                "on_file_started": on_file_started,
                "on_ocr_done": on_ocr_done,
                "on_file_done": on_file_done,
            }
            if self._selected_source_paths is not None:
                pipeline_kwargs["selected_source_paths"] = self._selected_source_paths
            rc = self._app_service.run_selected(self._config, **pipeline_kwargs)
        except (Exception, SystemExit) as exc:  # noqa: BLE001  # catch SystemExit to prevent the worker thread from killing the GUI process; also catches unexpected exceptions
            self.log_line.emit(f"FATAL ERROR: {exc}\n")
            rc = 1
        self.finished_with_code.emit(rc)


class _ConnectionCheckThread(QThread):
    """Background check: OCR engine reachability / correction provider auth."""

    check_done = Signal(bool, bool, str)  # (connected, model_ok, message)

    def __init__(
        self,
        *,
        engine: str = "",
        provider: str = "",
        url: str = "http://127.0.0.1:11434",
        model: str = "",
        app_service: _ConnectionService | None = None,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._provider = provider
        self._url = url
        self._model = model
        self._app_service = app_service or ProcessingApplicationService()

    def run(self) -> None:
        connected, model_ok, msg = self._app_service.check_connection(
            engine=self._engine,
            provider=self._provider,
            url=self._url,
            model=self._model,
        )
        self.check_done.emit(connected, model_ok, msg)
