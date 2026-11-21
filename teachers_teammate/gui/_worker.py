"""OCR worker thread and stream helpers."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import threading
import time
from typing import Any

from PySide6.QtCore import QThread, Signal

from ..application.composition import AppServices
from ..application.service import ProgressCallbacks
from ..config import Config
from ..domain.run_summary import FileOutcome, KeepAwakeState, RunSummary
from ..interfaces import CallbackReporter
from ..texts.run import BATTERY_WARNING
from ._types import FileDoneEvent


class OCRWorker(QThread):
    """Runs :class:`~teachers_teammate.infrastructure.pipeline.OCRPipeline` in a background thread.

    Signals
    -------
    log_line(text)
    file_started(source_id, name, idx, total)
    stage_started(source_id, name, stage, idx, total)
    ocr_done(source_id, name, idx, total)
    file_done(FileDoneEvent)
    keep_awake_changed(KeepAwakeState)
    run_finished(RunSummary)

    Two events stop a run: ``stop_after_file_event`` lets the file being processed finish and
    starts no other; ``stop_event`` ("Stop now") also stops that file at its next step.
    :meth:`request_stop` sets them.
    """

    log_line = Signal(str)
    file_started = Signal(str, str, int, int)  # (source_id, name, idx, total)
    # stage is "preprocessing" or "ocr"; not emitted when OCR comes from the cache.
    stage_started = Signal(str, str, str, int, int)  # (source_id, name, stage, idx, total)
    ocr_done = Signal(str, str, int, int)  # (source_id, name, idx, total)
    # What Ollama is doing while a request has no answer yet (a WaitDetail).
    stage_detail = Signal(str, object)  # (source_id, detail)
    first_token = Signal(str)  # (source_id)
    file_done = Signal(object)  # FileDoneEvent
    keep_awake_changed = Signal(object)  # KeepAwakeState, once at the start of the run
    # Not "finished": QThread has a signal of that name.
    run_finished = Signal(object)  # RunSummary

    def __init__(
        self,
        config: Config,
        files: Sequence[Path],
        *,
        app_service: AppServices,
    ) -> None:
        super().__init__()
        self._config = config
        self._files = list(files)
        self._app_service = app_service
        self.stop_event = threading.Event()
        self.stop_after_file_event = threading.Event()

    def request_stop(self, *, now: bool) -> None:
        """Ask the run to stop: after the file being processed, or (*now*) at its next step."""
        self.stop_after_file_event.set()
        if now:
            self.stop_event.set()

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

        def on_stage_started(source_id: str, stage: str) -> None:
            idx, total = file_indices.get(source_id, (0, 0))
            self.stage_started.emit(source_id, Path(source_id).name, stage, idx, total)

        def on_ocr_done(source_id: str, name: str) -> None:
            ocr_done_times[source_id] = time.monotonic()
            idx, total = file_indices.get(source_id, (0, 0))
            self.ocr_done.emit(source_id, name, idx, total)

        def on_stage_detail(source_id: str, detail: object) -> None:
            self.stage_detail.emit(source_id, detail)

        def on_first_token(source_id: str) -> None:
            self.first_token.emit(source_id)

        def on_file_done(outcome: FileOutcome) -> None:
            source_id = outcome.source_id
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
                FileDoneEvent(outcome=outcome, ocr_s=ocr_s, correction_s=correction_s)
            )

        def on_keep_awake(state: KeepAwakeState) -> None:
            self.keep_awake_changed.emit(state)

        try:
            pipeline_kwargs: dict[str, Any] = {
                "stop_event": self.stop_event,
                "stop_after_file_event": self.stop_after_file_event,
                "reporter": reporter,
                "battery_warning": BATTERY_WARNING,
                "progress": ProgressCallbacks(
                    on_file_started=on_file_started,
                    on_stage_started=on_stage_started,
                    on_ocr_done=on_ocr_done,
                    on_file_done=on_file_done,
                    on_keep_awake=on_keep_awake,
                    on_stage_detail=on_stage_detail,
                    on_first_token=on_first_token,
                ),
            }
            summary = self._app_service.processing.run_selected(
                self._config, self._files, **pipeline_kwargs
            )
        except (Exception, SystemExit) as exc:  # noqa: BLE001  # catch SystemExit to prevent the worker thread from killing the GUI process; also catches unexpected exceptions
            self.log_line.emit(f"FATAL ERROR: {exc}\n")
            summary = RunSummary(error=str(exc) or "unexpected error")
        self.run_finished.emit(summary)


#: Workers that were still running when their window closed.  A running ``QThread`` must not be
#: destroyed (that aborts the process), so it is kept here until the process ends; see
#: :func:`~teachers_teammate.gui._app_bootstrap.finish_process`.
_DETACHED: list[QThread] = []


def detach_running_worker(worker: QThread) -> None:
    """Keep *worker* alive on its own so closing its window cannot destroy a running thread."""
    worker.setParent(None)
    if worker not in _DETACHED:
        _DETACHED.append(worker)


def detached_workers_running() -> bool:
    """Return whether a detached worker is still running."""
    return any(worker.isRunning() for worker in _DETACHED)


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
        app_service: AppServices,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._provider = provider
        self._url = url
        self._model = model
        self._app_service = app_service

    def run(self) -> None:
        connected, model_ok, msg = self._app_service.processing.check_connection(
            engine=self._engine,
            provider=self._provider,
            url=self._url,
            model=self._model,
        )
        self.check_done.emit(connected, model_ok, msg)
