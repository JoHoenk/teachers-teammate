"""One run of the batch: the worker behind it, and what the window shows meanwhile.

The window decides that a run should start and asks the questions around it; this controller
runs it.  It owns the worker, the progress bar's figures and the two-step Stop button, and it
reads what a run is doing from a :class:`~._run_session.RunSession`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Any, Protocol, cast

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QLabel, QProgressBar, QPushButton, QStyle, QWidget

from ..application.composition import AppServices
from ..config import Config
from ..domain.ollama_discovery import WaitDetail
from ..domain.performance_estimate import TimeRange
from ..domain.run_summary import KeepAwakeState, RunSummary
from ..domain.stages import Stage
from ..texts import check as check_text
from ..texts import dialogs as dlg
from ..texts import main_window as txt
from ..texts import run
from ..texts.run import BATTERY_WARNING
from . import _handles
from ._background import LatestTask
from ._chart_widget import ChartWidget
from ._errors import show_notice
from ._qt import present
from ._results_table import ResultsTable
from ._run_session import (
    PROGRESS_RESOLUTION,
    PROGRESS_TICK_MS,
    RunSession,
    StopStage,
)
from ._run_status import RunProgress, RunTone, describe_run
from ._types import FileDoneEvent
from ._worker import OCRWorker, detach_running_worker

#: How long closing the window waits for a stopping run (milliseconds): enough for a cancelled
#: Ollama request and a state write, never for the run itself.
CLOSE_GRACE_MS = 3000


class WorkerLike(Protocol):
    """What the controller asks of the object that runs the batch (a test may pass its own)."""

    file_started: Any
    read_first: Any
    stage_started: Any
    ocr_done: Any
    stage_detail: Any
    first_token: Any
    file_done: Any
    keep_awake_changed: Any
    run_finished: Any
    stop_event: Any
    stop_after_file_event: Any

    def start(self) -> None: ...

    def request_stop(self, *, now: bool) -> None: ...

    def wait(self, msecs: int, /) -> bool: ...

    def isRunning(self) -> bool: ...


@dataclass(frozen=True)
class RunHooks:
    """What a run asks of the window it belongs to, as the window hands it over."""

    apply_lock: Callable[[], None]
    """Redraw which rows the run locks."""
    update_check_tab: Callable[[], None]
    """Relabel both tabs."""
    refresh_check_run: Callable[[], None]
    """Tell the Check tab what the run does with the open document."""
    sync_row: Callable[..., None]
    """Show what the store says about the documents given, one row each, read in one pass."""
    load_check: Callable[[str], bool]
    """Show one document in the Check tab; whether it could be shown."""
    current_source_id: Callable[[], str]
    """The document the Check tab has open."""
    queued_files: Callable[[list[str]], list[Path]]
    """The files of the given rows."""
    save_settings: Callable[..., None]
    """Write the settings file."""
    show_documents: Callable[[], None]
    """Bring the Documents tab to the front."""
    enable_export: Callable[[], None]
    """Let the Export button be pressed: there is a result now."""
    announce: Callable[[str, bool], None]
    """Say how the run ended in the tray; the flag asks for the warning icon."""
    worker_factory: Callable[[], Any]
    """The worker class to start, or ``None`` for the real one (a test may replace it)."""
    preflight_running: Callable[[], bool]
    """Whether a requirement check is in flight, so no run may start."""


class RunController(QObject):
    """The worker of one run and everything the window shows while it goes."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        table: ResultsTable,
        session: RunSession,
        chart: ChartWidget,
        hooks: RunHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._table = table
        self._session = session
        self._chart = chart
        self._hooks = hooks
        style = present(window.style(), "style")
        #: Run and Stop, and what the window shows around them: a run's own widgets.
        self.run_button = QPushButton(txt.RUN_SELECTED)
        self.run_button.setObjectName(_handles.RUN_BUTTON)
        self.run_button.setIcon(
            QIcon.fromTheme(
                "media-playback-start",
                style.standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            )
        )
        self.run_button.setStyleSheet(
            "QPushButton { background: #27ae60; color: white; font-weight: bold; "
            "font-size: 10pt; padding: 6px 12px; border-radius: 4px; } "
            "QPushButton:hover { background: #219a52; } "
            "QPushButton:disabled { background: #95a5a6; }"
        )
        self.stop_button = QPushButton(run.STOP)
        self.stop_button.setObjectName(_handles.STOP_BUTTON)
        self.stop_button.setToolTip(run.STOP_TOOLTIP)
        self.stop_button.setIcon(
            QIcon.fromTheme(
                "media-playback-stop",
                style.standardIcon(QStyle.StandardPixmap.SP_MediaStop),
            )
        )
        self.stop_button.setMinimumHeight(32)
        self.stop_button.setEnabled(False)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Idle")
        self.awake_note = QLabel()
        self.awake_note.setWordWrap(True)
        self.awake_note.setStyleSheet("color: #888888; font-size: 11px;")
        self.awake_note.setVisible(False)
        self.battery_note = QLabel(BATTERY_WARNING)
        self.battery_note.setWordWrap(True)
        self.battery_note.setStyleSheet("color: #e6a23c; font-size: 11px;")
        self.battery_note.setVisible(False)
        self._worker: WorkerLike | None = None
        self._reading_first = ""  # the file the reading pass is on, until it leaves it
        self._pages_task = LatestTask(self)
        #: Moves the bar and counts the time left down between two events of the run.
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(PROGRESS_TICK_MS)
        self._progress_timer.timeout.connect(self._refresh_progress)

    # ── What the window asks ──────────────────────────────────────────────

    @property
    def active(self) -> bool:
        """Whether a worker is running right now."""
        return bool(self._worker and self._worker.isRunning())

    @property
    def busy(self) -> bool:
        """Whether a requirement check or a run is going, so no second run may start."""
        return self._hooks.preflight_running() or self.active

    def show_idle(self) -> None:
        """Put the bar back to the state before a run; nothing of a run is on."""
        self.run_button.setEnabled(True)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Idle")

    def show_checking(self) -> None:
        """Say on the bar that the requirements of a run are being checked."""
        self.run_button.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("Checking requirements…")

    def warn_busy(self) -> None:
        """Say on the window that a run is already going."""
        show_notice(self._window, dlg.busy_with_run())

    def on_stop_clicked(self) -> None:
        """First click: finish the document being processed; second click: stop at once."""
        if not self.active:
            return
        worker = present(self._worker, "run worker")
        if self._session.stop_stage is StopStage.RUNNING:
            worker.request_stop(now=False)
            self.set_stop_stage(StopStage.AFTER_FILE)
        elif self._session.stop_stage is StopStage.AFTER_FILE:
            worker.request_stop(now=True)
            self.set_stop_stage(StopStage.NOW)

    def stop_for_close(self) -> None:
        """Stop the run at once, forget its signals and give it a moment to end.

        Never waits for the run to finish: a worker that is still running after the grace
        period (an online service cannot be interrupted) is detached, and the process ends
        without destroying it (see :func:`~._app_bootstrap.finish_process`).
        """
        worker = self._worker
        if worker is None or not worker.isRunning():
            return
        worker.request_stop(now=True)
        for name in (
            "file_started",
            "read_first",
            "stage_started",
            "ocr_done",
            "file_done",
            "keep_awake_changed",
            "run_finished",
        ):
            try:
                getattr(worker, name).disconnect()
            except RuntimeError, TypeError, AttributeError:
                pass  # nothing connected, or a test double without disconnect
        if not worker.wait(CLOSE_GRACE_MS):
            detach_running_worker(worker)  # ty: ignore[invalid-argument-type]  # a QThread

    def start(
        self,
        config: Config,
        selected_source_ids: list[str],
        files: list[Path] | None = None,
    ) -> None:
        """Start an OCR worker for *selected_source_ids*; *files* are their queued files."""
        if files is None:
            files = self._hooks.queued_files(selected_source_ids)
        self._hooks.save_settings(notify=False)
        self._table.set_output_dir(config.output_dir)
        self._chart.clear_data()
        # What these rows showed may be gone from the store since (a re-run invalidates it): a
        # row is its answer, so they say so now and a run that stops early leaves none behind.
        self._hooks.sync_row(*selected_source_ids)
        # A document the run redoes is read-only from the run's start until its own result
        # arrives: an edit stored meanwhile would race the run. It stays the open document, so
        # its result reloads it (without a tab switch), and a run that ends before reaching it
        # reloads it too. Documents the run leaves alone stay open for work.
        self._session.start(config, selected_source_ids)
        self._hooks.apply_lock()
        self.progress_bar.setRange(0, self._session.steps_total)
        self.progress_bar.setValue(0)
        self._start_pace(config, files)
        self._progress_timer.start()
        self._set_progress_text(self._session.progress_base)
        if self._session.from_check:
            # Started from the Check header: the teacher stays with the document.
            self._session.from_check = False
        else:
            self._hooks.show_documents()
        self._hooks.update_check_tab()

        worker_factory = self._hooks.worker_factory() or OCRWorker
        self._worker = worker_factory(
            config,
            files,
            app_service=self._services,
        )
        self._worker.file_started.connect(self._on_file_started)
        self._worker.read_first.connect(self._on_read_first)
        self._worker.stage_started.connect(self._on_stage_started)
        self._worker.ocr_done.connect(self._on_ocr_done)
        self._worker.stage_detail.connect(self._on_stage_detail)
        self._worker.first_token.connect(self._on_first_token)
        self._worker.file_done.connect(self._on_file_done)
        self._worker.keep_awake_changed.connect(self._on_keep_awake)
        self._worker.run_finished.connect(self._on_run_finished)

        self.run_button.setEnabled(False)
        self.set_stop_stage(StopStage.RUNNING)
        self.awake_note.setVisible(False)
        self.battery_note.setVisible(False)
        self._worker.start()

    def set_stop_stage(self, stage: StopStage) -> None:
        """Move the Stop button to *stage*: its text, tooltip and whether it can be pressed."""
        self._session.stop_stage = stage
        if stage is StopStage.AFTER_FILE:
            self.stop_button.setText(run.STOP_NOW)
            self.stop_button.setToolTip(run.STOP_NOW_TOOLTIP)
            self.stop_button.setEnabled(True)
            self._set_progress_text(self._session.progress_base)
        elif stage is StopStage.NOW:
            self.stop_button.setEnabled(False)
            self.progress_bar.setFormat(run.STOPPING)
        else:
            self.stop_button.setText(run.STOP)
            self.stop_button.setToolTip(run.STOP_TOOLTIP)
            self.stop_button.setEnabled(stage is StopStage.RUNNING)
        self._hooks.refresh_check_run()

    # ── The progress line ─────────────────────────────────────────────────

    def _start_pace(self, config: Config, files: list[Path]) -> None:
        """Start the time-left figure: the estimate from the store, then the page counts.

        The estimate is for *config*, the configuration this run really starts with (after a
        right-click on a single step has switched the others off), not for the panel's ticks.

        The estimate is read from the store on this thread (a small file); the page counts open
        PDFs, so they are counted in the background and the figure is redrawn when they arrive.
        """
        first, further = self._estimate_per_page(config)
        self._session.start_pace(first, len(files), further)
        if not files:
            return
        service = self._services
        self._pages_task.submit(lambda: service.run.page_counts(files), self._on_pages_known)

    def _estimate_per_page(self, config: Config) -> tuple[TimeRange | None, TimeRange | None]:
        """Return how long a file's first page and each further page take on this computer.

        ``(None, None)`` when nothing is known.
        """
        try:
            choices = self._services.speeds.choices_for_config(config)
            summary = self._services.speeds.speed_summary(choices)
        except Exception:  # noqa: BLE001  # an estimate must never keep a run from starting
            return None, None
        return summary.per_page, summary.further_page

    def _on_pages_known(self, result: object, error: BaseException | None) -> None:
        """Take the page counts of the run's files and redraw the time left."""
        if error is not None or not isinstance(result, dict):
            return
        pages = cast("dict[str, int]", result)
        self._session.pages = pages
        self._session.pace.set_pages(sum(pages.values()))
        self._take_snapshot()
        self._set_progress_text(self._session.progress_base)
        self._hooks.refresh_check_run()

    def _set_progress_text(self, text: str) -> None:
        """Show *text* in the progress bar, with a note while the run stops after this file."""
        self._session.progress_base = text
        if self._session.stop_stage is StopStage.NOW:
            return
        suffix = run.STOPPING_SUFFIX if self._session.stop_stage is StopStage.AFTER_FILE else ""
        self.progress_bar.setFormat(text + self._time_left_suffix() + suffix)

    def _time_left_suffix(self) -> str:
        """Return the note about the time left, and put its range in the bar's tooltip.

        One figure in the bar, the range behind it: a range in the bar itself would not fit,
        and the teacher who wants to know how sure the figure is can hover over it.
        """
        snapshot = self._session.snapshot
        self.progress_bar.setToolTip(snapshot.tooltip)
        return " · " + snapshot.note if snapshot.note else ""

    def _take_snapshot(self) -> RunProgress:
        """Ask the pace where the run is now, and keep the answer for both displays."""
        self._session.snapshot = self._session.pace.snapshot(time.monotonic())
        return self._session.snapshot

    def _set_steps_done(self, steps: int) -> None:
        """Move the bar to *steps* finished steps, unless it follows the time of the run."""
        if not self._session.time_driven:
            self.progress_bar.setValue(steps)

    def _refresh_progress(self) -> None:
        """Move the bar and the time left to where the clock says the run is.

        Without an estimate the pace has no figure and the bar keeps counting steps.
        """
        fraction = self._take_snapshot().fraction
        if fraction is None or not self.busy:
            return
        if not self._session.time_driven:
            self._session.time_driven = True
            self.progress_bar.setRange(0, PROGRESS_RESOLUTION)
        # Full only when the run says so: a file that is nearly done is not yet done.
        self.progress_bar.setValue(
            min(round(fraction * PROGRESS_RESOLUTION), PROGRESS_RESOLUTION - 1)
        )
        self._set_progress_text(self._session.progress_base)
        self._hooks.refresh_check_run()

    def _on_keep_awake(self, state: KeepAwakeState) -> None:
        """Say under the progress bar whether the computer is kept awake, and warn on battery."""
        if state.enabled:
            if state.held:
                text = run.AWAKE_HELD
            elif state.available:
                text = run.AWAKE_REFUSED
            else:
                text = run.AWAKE_UNAVAILABLE
            self.awake_note.setText(text)
        self.awake_note.setVisible(state.enabled)
        self.battery_note.setVisible(bool(state.enabled and state.on_battery))

    # ── Worker signal handlers ────────────────────────────────────────────

    def _on_read_first(self, source_id: str, name: str, idx: int, total: int) -> None:
        """The reading pass reads *source_id* now: its row and the progress line say so."""
        self._end_read_first()
        self._reading_first = source_id
        self._set_progress_text(self._session.read_first(source_id, name, idx, total))
        self._table.mark_processing(source_id)
        self._hooks.apply_lock()
        self._hooks.refresh_check_run()

    def _end_read_first(self) -> None:
        """The reading pass left its file: the row shows the reading it stored."""
        source_id, self._reading_first = self._reading_first, ""
        if source_id:
            self._table.mark_stage_running(source_id, "")
            self._hooks.sync_row(source_id)

    def _on_file_started(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._end_read_first()
        self._set_steps_done((idx - 1) * self._session.steps_per_file)
        self._session.file_started(source_id, name, idx, total)
        self._set_progress_text(self._session.progress_base)
        self._table.mark_processing(source_id)
        self._hooks.apply_lock()
        # The text format is deliberately kept: the row still shows the previous texts, and
        # a format that no longer matches them would save an edit in the wrong notation.

    def _on_stage_started(
        self, _source_id: str, name: str, stage: str, idx: int, total: int
    ) -> None:
        text = self._session.stage_started(name, stage, idx, total, time.monotonic())
        if text is not None:
            self._set_progress_text(text)
        self._hooks.refresh_check_run()

    def _on_stage_detail(self, source_id: str, detail: object) -> None:
        """Say what Ollama is doing with the request, in the bar and in the Check tab."""
        if not isinstance(detail, WaitDetail):
            return
        text = self._session.stage_detail(source_id, check_text.wait_detail_text(detail))
        if text is None:
            return
        self._set_progress_text(text)
        self._hooks.refresh_check_run()

    def _on_first_token(self, source_id: str) -> None:
        """The model began answering: a wait note goes, and the proofreading's clock starts."""
        if source_id != self._session.current:
            return
        text = self._session.first_token(source_id, time.monotonic())
        if text is not None:
            self._set_progress_text(text)
        self._hooks.refresh_check_run()

    def _on_ocr_done(self, source_id: str, name: str, idx: int, total: int) -> None:
        self._session.ocr_done()
        self._hooks.refresh_check_run()
        if self._session.steps_per_file > 1:
            self._set_steps_done((idx - 1) * 2 + 1)
            self._set_progress_text(f"[{idx}/{total}] {name} — {check_text.PROGRESS_CORRECTING}")
            # The reading is stored, so the row can be opened while the proofreading runs.
            self._hooks.sync_row(source_id)
            self._table.mark_stage_running(source_id, Stage.CORRECTION)

    def _on_file_done(self, event: FileDoneEvent) -> None:
        outcome = event.outcome
        source_id = outcome.source_id
        self._table.add_result(event)
        self._hooks.enable_export()
        # A failed, skipped or stopped file is not a question: its row says why, and the final
        # line and the tray count them.  One box per failed file would cover the window.
        self._session.file_done(event)
        self._set_steps_done(self._session.steps_done())
        self._set_progress_text(self._session.progress_base)
        self._refresh_progress()
        self._chart.update_data(
            self._session.names,
            self._session.ocr_times,
            self._session.correction_times,
        )

        # Only the open document is reloaded: a finished file never takes the tab away from
        # the document the teacher is working on, and never switches tabs.
        # The open document is read once, for the page and the row together; any other row
        # is read for itself.  Both refresh from the persisted state even on failure or stop,
        # when on_ocr_done never fired.
        current = self._hooks.current_source_id()
        if not (source_id == current and self._hooks.load_check(source_id)):
            self._hooks.sync_row(source_id)
            self._hooks.apply_lock()
        self._hooks.update_check_tab()

    def _on_run_finished(self, summary: RunSummary) -> None:
        """Announce how the run ended: done, stopped (not an error) or finished with errors."""
        self._end_read_first()
        self.run_button.setEnabled(True)
        self.set_stop_stage(StopStage.IDLE)
        self.awake_note.setVisible(False)
        self.battery_note.setVisible(False)
        self._progress_timer.stop()
        text = describe_run(summary)
        if text.complete:
            self.progress_bar.setValue(self.progress_bar.maximum())
        self._session.progress_base = text.progress
        self.progress_bar.setFormat(text.progress)
        self.progress_bar.setToolTip("")
        reload = self._hooks.current_source_id() in self._session.pending
        self._session.finished()
        if reload:
            # The run ended (stopped, failed) before it reached the open document: show what
            # it still has instead of leaving the tab locked.
            self._hooks.load_check(self._hooks.current_source_id())
        else:
            self._hooks.apply_lock()
        self._hooks.update_check_tab()
        self._hooks.announce(text.tray, text.tone is RunTone.WARNING)
