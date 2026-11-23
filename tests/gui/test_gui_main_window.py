"""GUI tests for MainWindow orchestration using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import dataclasses
from pathlib import Path
from teachers_teammate.domain.install_item import InstallItem, InstallKind
from teachers_teammate.domain.stages import Stage
import threading
from typing import Any, ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QShortcut
from PySide6.QtWidgets import QAbstractButton, QApplication, QLabel, QWidget

from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.application.ollama_server import OllamaServerService
from teachers_teammate.application.queue import QueueRow, StageState
from teachers_teammate.application.speed_estimates import SpeedSummary
from teachers_teammate.application.documents import WorkedOn
from teachers_teammate.application.export import NothingToExport, WouldOverwriteInput
from teachers_teammate.application.model_recommendations import ModelRecommendationService
from teachers_teammate.application.run import RunPreflight, RunService
from teachers_teammate.application.setup_plan import SetupService
from teachers_teammate.config import broken_settings_copy, load_config_file
from teachers_teammate.settings_schema import LoadedSettings
from teachers_teammate.domain.requirements import IssueKind, RequirementIssue
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    RECOMMENDED,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
)
from teachers_teammate.gui import _dialogs, _handles
from teachers_teammate.gui import _export_controller as export_controller_module
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui import _menu_bar as menu_bar_module
from teachers_teammate.gui import _preprocess_preview as preprocess_module
from teachers_teammate.gui import _readiness_controller as readiness_controller_module
from teachers_teammate.gui import _run_controller as run_controller_module
from teachers_teammate.gui import _run_session as run_session_module
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._config_panel import ConfigPanel
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._run_start import RunStartController
from teachers_teammate.gui._readiness_controller import ReadinessController
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._setup_launcher import SetupLauncher
from teachers_teammate.gui._types import FileDoneEvent
from teachers_teammate.gui.settings import SettingsPage, SettingsResult
from teachers_teammate.gui.settings._page import (
    FOCUS_LOCAL_MODELS,
    FOCUS_ONLINE_SERVICES,
    FOCUS_PROOFREADING_KEY,
)
from teachers_teammate.gui.settings._window import SUB_PAGES
from teachers_teammate.domain.ollama_discovery import (
    OLLAMA_DOWNLOAD_URL,
    WaitDetail,
    WaitKind,
)
from teachers_teammate.domain.run_summary import (
    FileOutcome,
    FileStatus,
    KeepAwakeState,
    RunSummary,
)
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import Answer, Notice, Question, StandardButton
from teachers_teammate.texts.issues import issue_text
from tests.conftest import make_config

from ._gui_harness import (
    DummyWorker,
    fake_pull_threads,
    install_scripted_settings_window,
    main_window,
    no_network_in_gui_tests,
    no_privacy_prompt,
    no_update_check,
    pretend_a_run_is_going,
    queue_row,
    real_service,
    record_notices,
    record_update_checks,
    SharedMemorySettings,
    store_document,
    stored,
)

#: How long a background thread and the notice it queues may take: a loaded runner is slow.
THREAD_WAIT_MS = 10_000


def _answer_questions(monkeypatch, *, yes: bool) -> list[Question]:
    """Answer every question with *yes*; return the questions asked."""
    asked: list[Question] = []

    def ask(_parent: object, question: Question) -> bool:
        asked.append(question)
        return yes

    monkeypatch.setattr(_dialogs, "ask", ask)
    return asked


_REAL_CONFIRM_DATA_FLOW = RunStartController._confirm_data_flow_then


def _wait_for(predicate, timeout_ms: int = 3000) -> None:
    """Spin the Qt event loop until *predicate* holds (background results arrive queued)."""
    from PySide6.QtTest import QTest  # noqa: PLC0415

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert predicate(), "condition not reached in time"


@pytest.mark.gui
def test_main_window_run_shows_error_for_invalid_config(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow whose ConfigPanel raises a configuration error
    When   the Run action is triggered
    Then   a warning is shown and no worker is created
    """
    monkeypatch.setattr(
        main_window._config_panel,
        "to_config",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    shown = record_notices(monkeypatch)

    main_window._run_start.start()

    assert [notice.title for notice in shown] == ["Configuration error"]
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_main_window_run_shows_info_when_no_files(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a MainWindow with a valid config but no discoverable files
    When   the Run action is triggered
    Then   an information dialog is shown and processing does not start
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    shown = record_notices(monkeypatch)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert [notice.title for notice in shown] == ["No files found"]
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_main_window_run_starts_worker_and_updates_ui(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a MainWindow with valid config and queued files
    When   the Run action is triggered
    Then   a worker is started and run/stop/progress UI enters active state
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    first, second = tmp_path / "a.png", tmp_path / "b.txt"
    main_window._results_table.set_queue(
        [queue_row(str(first.resolve())), queue_row(str(second.resolve()))]
    )
    main_window._results_table.selectRow(0)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: main_window._run_controller._worker is not None, timeout=3000)

    assert isinstance(main_window._run_controller._worker, DummyWorker)
    assert main_window._run_controller._worker.files == [first]
    assert main_window._run_btn.isEnabled() is False
    assert main_window._stop_btn.isEnabled() is True
    assert main_window._progress.maximum() == 1


@pytest.mark.gui
def test_main_window_passes_its_service_to_the_worker(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a MainWindow with its own application service
    When   a run starts the worker
    Then   the worker gets that service, so a run fills the anonymization cache the
           window's previews and exports read
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)

    main_window._results_table.set_queue([queue_row("a.png")])
    main_window._results_table.selectRow(0)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: main_window._run_controller._worker is not None, timeout=3000)

    assert main_window._run_controller._worker.app_service is main_window._app_service


@pytest.mark.gui
def test_main_window_run_shows_info_when_no_selection(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a MainWindow with queued files but no selected rows
    When   the Run action is triggered
    Then   an information dialog is shown and no worker is started
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    shown = record_notices(monkeypatch)
    main_window._results_table.set_queue([queue_row("a.png"), queue_row("b.txt")])

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert [notice.title for notice in shown] == ["No files selected"]
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_main_window_refreshes_queue_on_input_folder_change(main_window, tmp_path: Path) -> None:
    """
    Given  input/output fields and an input directory with supported files
    When   the input folder field is updated
    Then   the queue is refreshed immediately with discovered files
    """
    cfg = make_config(tmp_path)
    (cfg.input_dir / "a.png").touch()
    (cfg.input_dir / "b.pdf").touch()

    main_window._config_panel.load_from_dict(
        {
            "input": str(cfg.input_dir),
            "output": str(cfg.output_dir),
            "recursive": False,
        }
    )
    main_window._config_panel.set_input_dir(str(cfg.input_dir))

    assert main_window._results_table.rowCount() == 2


@pytest.mark.gui
def test_main_window_stop_is_a_two_step_button(main_window) -> None:
    """
    Given  a MainWindow with a running worker
    When   Stop is clicked once and then again
    Then   the first click only asks to stop after this document and turns the button into
           "Stop now"; the second sets the worker's stop_event and disables the button
    """
    worker = DummyWorker(None)
    worker.start()
    main_window._run_controller._worker = worker
    main_window._run_controller.set_stop_stage(run_session_module.StopStage.RUNNING)
    assert main_window._stop_btn.text() == "Stop"

    main_window._run_controller.on_stop_clicked()

    assert worker.stop_after_file_event.is_set() is True
    assert worker.stop_event.is_set() is False
    assert main_window._stop_btn.text() == "Stop now"
    assert main_window._stop_btn.isEnabled() is True

    main_window._run_controller.on_stop_clicked()

    assert worker.stop_event.is_set() is True
    assert main_window._stop_btn.isEnabled() is False
    assert "Stopping" in main_window._progress.format()


@pytest.mark.gui
def test_main_window_progress_says_it_stops_after_this_document(main_window) -> None:
    """
    Given  a run whose Stop was clicked once
    When   the progress text changes with the next phase
    Then   the text keeps the note that the run stops after this document
    """
    worker = DummyWorker(None)
    worker.start()
    main_window._run_controller._worker = worker
    main_window._run_controller.set_stop_stage(run_session_module.StopStage.RUNNING)
    main_window._run_controller.on_stop_clicked()

    main_window._run_controller._on_stage_started("/tmp/a.png", "a.png", "ocr", 1, 3)

    assert main_window._progress.format() == "[1/3] a.png — OCR… — stopping after this document"


@pytest.mark.gui
def test_a_stopped_file_opens_no_error_dialog_and_shows_where_it_stopped(
    main_window, monkeypatch
) -> None:
    """
    Given  a queued scan whose recognition is running when the run is stopped
    When   _on_file_done reports it as stopped
    Then   no "Processing error" dialog opens, the recognition cell says "stopped", and the
           row is neither green nor red
    """
    shown = record_notices(monkeypatch)
    monkeypatch.setattr(main_window._app_service.documents, "load_view", lambda *_a, **_k: None)
    source = "/tmp/scan.png"
    main_window._results_table.set_queue([queue_row(source, "scan.png")])
    main_window._results_table.mark_processing(source)

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=source,
                name="scan.png",
                status=FileStatus.STOPPED,
                stopped_stage="ocr",
            ),
            ocr_s=1.0,
            correction_s=0.0,
        )
    )

    assert shown == []
    assert main_window._results_table.item(0, 1).text() == "stopped"
    assert "Stopped before the text was read." in main_window._results_table.item(0, 1).toolTip()
    assert main_window._results_table.item(0, 0).background().color().name() == "#2b2b2b"


@pytest.mark.gui
def test_a_stopped_file_keeps_the_tick_of_the_step_that_finished(main_window, monkeypatch) -> None:
    """
    Given  a file whose text was read, then proofreading was cut short by "Stop now"
    When   _on_file_done reports it as stopped, carrying the stored text
    Then   the recognition cell keeps its tick, the proofreading cell says "stopped", and the
           review cell stays pending
    """
    source = "/tmp/essay.png"
    monkeypatch.setattr(
        main_window._app_service.documents,
        "queue_rows",
        lambda *_a, **_k: [queue_row(source, "essay.png", ocr=StageState.DONE)],
    )
    main_window._results_table.set_queue([queue_row(source, "essay.png")])
    main_window._results_table.mark_processing(source)
    main_window._results_table.mark_stage_running(source, "correction")

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=source,
                name="essay.png",
                status=FileStatus.STOPPED,
                stopped_stage="correction",
            ),
            ocr_s=1.0,
            correction_s=0.0,
        )
    )

    table = main_window._results_table
    assert [table.item(0, col).text() for col in (1, 2, 3)] == ["✓", "stopped", "-"]


@pytest.mark.gui
def test_main_window_finished_updates_progress_and_tray(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow that already processed one file
    When   the run finishes with a successful summary
    Then   buttons/progress are reset and a tray notification is sent
    """
    main_window._run_session.names = ["a.png"]
    main_window._progress.setRange(0, 2)
    seen: list[str] = []
    monkeypatch.setattr(main_window._tray, "showMessage", lambda *_args: seen.append("tray"))

    main_window._run_controller._on_run_finished(RunSummary(succeeded=("a.png",)))

    assert main_window._run_btn.isEnabled() is True
    assert main_window._stop_btn.isEnabled() is False
    assert main_window._stop_btn.text() == "Stop"
    assert main_window._progress.value() == main_window._progress.maximum()
    assert "Done" in main_window._progress.format()
    assert seen == ["tray"]


@pytest.mark.gui
def test_main_window_a_stopped_run_is_announced_as_stopped_not_as_an_error(
    main_window, monkeypatch
) -> None:
    """
    Given  a run that was stopped after one of four documents
    When   the run finishes
    Then   the progress bar and the notification say "Stopped" with the counts, the bar is not
           filled, and nothing says "error"
    """
    main_window._run_session.names = ["a.png", "b.png"]
    main_window._progress.setRange(0, 8)
    main_window._progress.setValue(3)
    messages: list[str] = []
    monkeypatch.setattr(
        main_window._tray, "showMessage", lambda _title, text, *_a: messages.append(text)
    )

    main_window._run_controller._on_run_finished(
        RunSummary(succeeded=("a.png",), stopped=("b.png",), not_started=("c.png", "d.png"))
    )

    expected = "Stopped — 1 of 4 done, 1 unfinished, 2 not started"
    assert main_window._progress.format() == expected
    assert messages == [expected]
    assert main_window._progress.value() == 3
    assert "error" not in main_window._progress.format().lower()
    assert main_window._run_btn.isEnabled() is True


@pytest.mark.gui
def test_the_awake_note_shows_during_a_run_and_warns_on_battery(main_window) -> None:
    """
    Given  a run that holds the computer awake on battery power
    When   the keep-awake state arrives, and later the run finishes
    Then   the note and the battery warning show, and both are hidden after the run
    """
    main_window._run_controller._on_keep_awake(
        KeepAwakeState(enabled=True, held=True, on_battery=True)
    )

    assert main_window._run_controller.awake_note.isHidden() is False
    assert "awake" in main_window._run_controller.awake_note.text()
    assert main_window._run_controller.battery_note.isHidden() is False

    main_window._run_controller._on_run_finished(RunSummary())

    assert main_window._run_controller.awake_note.isHidden() is True
    assert main_window._run_controller.battery_note.isHidden() is True


@pytest.mark.gui
def test_the_awake_note_says_so_when_the_system_refused(main_window) -> None:
    """
    Given  the system did not allow staying awake
    When   the keep-awake state arrives
    Then   the note says the computer may go to sleep, and there is no battery warning
    """
    main_window._run_controller._on_keep_awake(
        KeepAwakeState(enabled=True, held=False, on_battery=False)
    )

    assert "may go to sleep" in main_window._run_controller.awake_note.text()
    assert main_window._run_controller.battery_note.isHidden() is True


@pytest.mark.gui
def test_the_awake_note_does_not_blame_the_system_when_nothing_was_requested(main_window) -> None:
    """
    Given  a computer with no way to ask for staying awake (nothing was requested)
    When   the keep-awake state arrives
    Then   the note says the app cannot do it, not that the system did not allow it
    """
    main_window._run_controller._on_keep_awake(
        KeepAwakeState(enabled=True, held=False, available=False)
    )

    text = main_window._run_controller.awake_note.text()
    assert "cannot keep this computer awake" in text
    assert "did not allow" not in text


@pytest.mark.gui
def test_no_awake_note_when_keep_awake_is_off(main_window) -> None:
    """
    Given  keep_awake switched off
    When   the keep-awake state arrives
    Then   no note and no battery warning are shown
    """
    main_window._run_controller._on_keep_awake(
        KeepAwakeState(enabled=False, held=False, on_battery=True)
    )

    assert main_window._run_controller.awake_note.isHidden() is True
    assert main_window._run_controller.battery_note.isHidden() is True


class _SpontaneousClose:
    """A close event as the window's X sends it (``spontaneous()`` is true)."""

    def __init__(self) -> None:
        self.ignored = False
        self.accepted = False

    def spontaneous(self) -> bool:
        return True

    def ignore(self) -> None:
        self.ignored = True

    def accept(self) -> None:
        self.accepted = True


def _running_worker(main_window) -> DummyWorker:
    worker = DummyWorker(None)
    worker.start()
    main_window._run_controller._worker = worker
    return worker


@pytest.mark.gui
def test_closing_during_a_run_asks_and_keep_running_changes_nothing(
    main_window, monkeypatch
) -> None:
    """
    Given  a running worker and a teacher who answers "Keep running"
    When   the window's X is pressed
    Then   the close is ignored and the worker is not asked to stop
    """
    worker = _running_worker(main_window)
    monkeypatch.setattr(main_window, "_confirm_close_during_run", lambda: False)
    event = _SpontaneousClose()

    main_window.closeEvent(event)

    assert event.ignored is True
    assert event.accepted is False
    assert worker.stop_event.is_set() is False
    assert worker.isRunning() is True


@pytest.mark.gui
def test_closing_during_a_run_stops_at_once_and_does_not_wait_for_the_run(
    main_window, monkeypatch
) -> None:
    """
    Given  a running worker and a teacher who answers "Stop and close"
    When   the window's X is pressed
    Then   the worker is asked to stop now, given only the short grace period, its signals are
           disconnected (no dialog on a hidden window) and the close is accepted
    """
    worker = _running_worker(main_window)
    waits: list[int] = []
    monkeypatch.setattr(main_window, "_confirm_close_during_run", lambda: True)
    monkeypatch.setattr(worker, "wait", lambda ms: waits.append(ms) or True)
    event = _SpontaneousClose()

    main_window.closeEvent(event)

    assert event.accepted is True
    assert worker.stop_event.is_set() is True
    assert waits == [run_controller_module.CLOSE_GRACE_MS]
    assert run_controller_module.CLOSE_GRACE_MS <= 5000
    assert worker.file_done._subs == []


@pytest.mark.gui
def test_closing_detaches_a_worker_that_does_not_stop_in_time(main_window, monkeypatch) -> None:
    """
    Given  a worker that is still running after the grace period (an online service)
    When   the window is closed
    Then   the worker is detached, so the running thread is not destroyed with the window
    """
    worker = _running_worker(main_window)
    detached: list = []
    monkeypatch.setattr(main_window, "_confirm_close_during_run", lambda: True)
    monkeypatch.setattr(worker, "wait", lambda _ms: False)
    monkeypatch.setattr(run_controller_module, "detach_running_worker", detached.append)

    main_window.closeEvent(_SpontaneousClose())

    assert detached == [worker]


@pytest.mark.gui
def test_closing_without_a_run_asks_nothing(main_window, monkeypatch) -> None:
    """
    Given  no running worker
    When   the window's X is pressed
    Then   no question is asked and the close is accepted
    """
    monkeypatch.setattr(
        main_window,
        "_confirm_close_during_run",
        lambda: (_ for _ in ()).throw(AssertionError("must not ask")),
    )
    event = _SpontaneousClose()

    main_window.closeEvent(event)

    assert event.accepted is True


@pytest.mark.gui
def test_a_programmatic_close_stops_the_run_without_asking(main_window, monkeypatch) -> None:
    """
    Given  a running worker
    When   the window is closed by code (not the X)
    Then   no question is asked, the run is stopped at once
    """
    worker = _running_worker(main_window)
    monkeypatch.setattr(
        main_window,
        "_confirm_close_during_run",
        lambda: (_ for _ in ()).throw(AssertionError("must not ask")),
    )

    main_window.close()

    assert worker.stop_event.is_set() is True


@pytest.mark.gui
def test_quit_asks_while_a_run_is_going(main_window, monkeypatch) -> None:
    """
    Given  a running worker and a teacher who answers "Keep running" to File → Quit
    When   Quit is triggered
    Then   the window stays open and the run is untouched
    """
    worker = _running_worker(main_window)
    monkeypatch.setattr(main_window, "_confirm_close_during_run", lambda: False)

    main_window._on_quit_requested()

    assert main_window._close_confirmed is False
    assert worker.stop_event.is_set() is False
    assert worker.isRunning() is True


@pytest.mark.gui
def test_the_close_question_defaults_to_keep_running(main_window, monkeypatch) -> None:
    """
    Given  the close question
    When   it is shown and answered with "Stop and close", then with "Keep running"
    Then   "Keep running" is the default button; the first answer stops, the second does not
    """
    question = dlg.close_during_run()
    assert [choice.label for choice in question.choices] == ["Stop and close", "Keep running"]
    assert [choice.default for choice in question.choices] == [False, True]

    monkeypatch.setattr(_dialogs, "ask", lambda *_a, **_k: True)
    stop_and_close = main_window._confirm_close_during_run()

    monkeypatch.setattr(_dialogs, "ask", lambda *_a, **_k: False)
    keep_running = main_window._confirm_close_during_run()

    assert stop_and_close is True
    assert keep_running is False


@pytest.mark.gui
def test_main_window_file_done_records_txt_artifacts(main_window) -> None:
    """
    Given  a MainWindow receiving a file_done event for a text input
    When   _on_file_done is called with raw text output paths
    Then   the result row is recorded and selected paths expose the txt artifact
    """
    main_window._results_table.set_queue([queue_row("/tmp/note.txt", "note.txt")])

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id="/tmp/note.txt",
                name="note.txt",
                status=FileStatus.SAVED,
                warnings=("saved",),
            ),
            ocr_s=0.3,
            correction_s=0.0,
        )
    )

    main_window._results_table.selectRow(0)
    assert main_window._results_table.rowCount() == 1
    assert main_window._results_table.queued_file_names() == ["note.txt"]


@pytest.mark.gui
def test_main_window_file_done_reloads_the_open_document(main_window, monkeypatch) -> None:
    """
    Given  the document that is open in the Check tab
    When   _on_file_done reports it
    Then   it is loaded again, so the tab shows the new result
    """
    seen: list[str] = []
    main_window._results_table.set_queue([queue_row("/tmp/page.png", "page.png")])
    main_window._results_table.selectRow(0)
    main_window._check_controller._open = "/tmp/page.png"

    monkeypatch.setattr(main_window._check_controller, "load", seen.append)

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id="/tmp/page.png",
                name="page.png",
                status=FileStatus.SAVED,
                warnings=("saved",),
            ),
            ocr_s=0.3,
            correction_s=0.2,
        )
    )

    assert seen == ["/tmp/page.png"]


@pytest.mark.gui
def test_main_window_file_done_shows_how_the_finished_results_were_sent(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a file whose proofreading was just started (its cell was reset) and whose finished
           state records that it went to an online service with two names hidden
    When   _on_file_done reports it
    Then   the proofreading cell says how the result was sent, not only after a reload
    """
    from teachers_teammate.domain.data_location import (  # noqa: PLC0415
        SentRecord,
        SentStatus,
    )
    from teachers_teammate.application.document_view import DocumentStateView  # noqa: PLC0415

    source = tmp_path / "page.png"
    source.write_bytes(b"x")
    source_id = str(source)
    main_window._results_table.set_queue([queue_row(source_id, "page.png")])
    main_window._results_table.mark_stage_running(source_id, "correction")
    view = DocumentStateView(
        source_id=source_id,
        name="page.png",
        preview_img="",
        raw_text="",
        correction_text="",
        evaluation_text="",
        ocr_done=True,
        correction_done=True,
        evaluation_done=False,
        sent={
            Stage.CORRECTION: SentStatus(
                SentRecord(provider="mistral_eu", model="m", names_hidden=2, privacy="p")
            )
        },
    )
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service.documents,
        "queue_rows",
        lambda *_a, **_k: [
            queue_row(
                source_id,
                "page.png",
                ocr=StageState.DONE,
                correction=StageState.DONE,
                has_text=True,
                sent=view.sent,
            )
        ],
    )

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=source_id,
                name="page.png",
                status=FileStatus.SAVED,
                warnings=("saved",),
            ),
            ocr_s=0.1,
            correction_s=0.1,
        )
    )

    item = main_window._results_table.item(0, main_window._results_table._COL_CORRECTION)
    assert item is not None
    assert "Mistral (EU), 2 names hidden" in item.toolTip()


@pytest.mark.gui
def test_main_window_file_done_leaves_another_open_document_alone(main_window, monkeypatch) -> None:
    """
    Given  a document open in the Check tab and a run that finishes a different file
    When   _on_file_done reports that file
    Then   the open document is not reloaded and no tab is switched
    """
    seen: list[str] = []
    main_window._results_table.set_queue(
        [queue_row("/tmp/a.png", "a.png"), queue_row("/tmp/b.png", "b.png")]
    )
    main_window._check_controller._open = "/tmp/a.png"
    monkeypatch.setattr(main_window._check_controller, "load", seen.append)

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id="/tmp/b.png",
                name="b.png",
                status=FileStatus.SAVED,
                warnings=("saved",),
            ),
            ocr_s=0.3,
            correction_s=0.2,
        )
    )

    assert seen == []
    assert main_window._check_controller.current == "/tmp/a.png"


@pytest.mark.gui
def test_a_typed_edit_is_stored_before_a_stage_run_clears_the_cache(
    main_window, monkeypatch, stored, qtbot
) -> None:
    """
    Given  an open document with text typed but not yet stored
    When   "Proofread again" invalidates the cache and starts the run
    Then   the typed edit is stored first, then the cache is cleared, then the worker starts
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    order: list[str] = []
    monkeypatch.setattr(
        main_window._check_controller.page, "flush_pending_edit", lambda: order.append("flush")
    )
    monkeypatch.setattr(
        main_window._app_service.documents, "worked_on_documents", lambda *_a, **_k: WorkedOn()
    )

    def _invalidate(*_a, **_k):
        order.append("invalidate")
        return ()

    monkeypatch.setattr(main_window._app_service.commands, "invalidate_from_stage", _invalidate)
    monkeypatch.setattr(
        main_window._run_controller, "start", lambda *_a, **_k: order.append("start")
    )

    main_window._run_start._run_stage(make_config(Path("/tmp")), [a], "correction")
    qtbot.waitUntil(lambda: order == ["flush", "invalidate", "start"], timeout=2000)

    assert order == ["flush", "invalidate", "start"]


@pytest.mark.gui
def test_main_window_on_finished_shows_error_format(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow
    When   the run finishes with a failed file
    Then   progress bar format shows 'error'
    """
    seen: list[str] = []
    monkeypatch.setattr(main_window._tray, "showMessage", lambda *_args: seen.append("tray"))
    main_window._run_session.names = []
    main_window._run_controller._on_run_finished(RunSummary(failed=("a.png",)))
    assert "error" in main_window._progress.format().lower()


@pytest.mark.gui
def test_main_window_save_settings_logs_success(main_window, tmp_path, monkeypatch) -> None:
    """
    Given  a MainWindow with a config_path pointing to a writable location
    When   _save_settings() is called
    Then   the log shows 'Settings saved'
    """
    out = tmp_path / "ocr.toml"
    main_window._settings_file._config_path = out

    main_window._save_settings()

    assert out.exists()
    assert "Settings saved" in main_window._log.toPlainText()


@pytest.mark.gui
def test_main_window_save_settings_shows_warning_on_error(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow with a config_path that cannot be written
    When   _save_settings() is called
    Then   a warning dialog is shown
    """
    main_window._settings_file._config_path = Path("/nonexistent/dir/ocr.toml")
    shown = record_notices(monkeypatch)

    main_window._save_settings()

    assert [notice.title for notice in shown] == ["Save failed"]


@pytest.mark.gui
def test_main_window_on_file_started_updates_progress(main_window) -> None:
    """
    Given  a MainWindow with steps_per_file=1
    When   _on_file_started is called for file 2 of 3
    Then   progress value advances and format shows file index
    """
    main_window._run_session.steps_per_file = 1
    main_window._progress.setRange(0, 3)
    main_window._results_table.set_queue(
        [queue_row("a.png"), queue_row("b.png"), queue_row("c.png")]
    )

    main_window._run_controller._on_file_started("b.png", "b.png", 2, 3)

    assert "2/3" in main_window._progress.format()


@pytest.mark.gui
def test_the_bar_and_the_time_left_follow_the_clock(main_window, monkeypatch) -> None:
    """
    Given  a run of one file whose time is estimated at 100 seconds, started at second 990 and
           proofread from second 1000 (the model loaded until then)
    When   the clock reaches second 1050 and the timer ticks
    Then   the bar stands at half of its way and the time left reads about 50 s; a second later
           both have moved on
    """
    from teachers_teammate.domain.performance_estimate import TimeRange  # noqa: PLC0415
    from teachers_teammate.gui._run_status import RunPace  # noqa: PLC0415

    clock = {"now": 1000.0}
    monkeypatch.setattr(run_controller_module.time, "monotonic", lambda: clock["now"])
    pretend_a_run_is_going(main_window, monkeypatch)
    main_window._run_session.pace = RunPace(TimeRange(100.0, 100.0))
    main_window._run_session.pace.set_files(1)
    main_window._run_session.pace.set_pages(1)
    main_window._results_table.set_queue([queue_row("a.png")])
    main_window._progress.setRange(0, 2)

    clock["now"] = 990.0
    main_window._run_controller._on_file_started("a.png", "a.png", 1, 1)
    clock["now"] = 1000.0
    main_window._run_controller._on_stage_started("a.png", "a.png", "correction", 1, 1)
    clock["now"] = 1050.0
    main_window._run_controller._refresh_progress()

    assert main_window._progress.maximum() == run_session_module.PROGRESS_RESOLUTION
    assert main_window._progress.value() == 500
    assert "about 50 s left" in main_window._progress.format()

    clock["now"] = 1051.0
    main_window._run_controller._refresh_progress()

    assert main_window._progress.value() == 510
    assert "about 49 s left" in main_window._progress.format()

    # An event of the run does not push the bar back to a step count.
    main_window._run_controller._on_ocr_done("a.png", "a.png", 1, 1)
    assert main_window._progress.value() == 510


@pytest.mark.gui
def test_the_clock_waits_for_the_first_token_of_a_local_model(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run whose proofreading waits for a local Ollama server, on a document open in Check
    When   the proofreading starts, the server says it is busy with another model, and then the
           first token arrives
    Then   the bar and the strip say what the server is doing, and the run's clock starts only
           at the first token, so neither figure moves during the wait
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(run_controller_module.time, "monotonic", lambda: clock["now"])
    pretend_a_run_is_going(main_window, monkeypatch)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._run_session.waits_for_ollama = True
    main_window._run_controller._on_file_started(a, "a.png", 1, 1)

    clock["now"] = 1005.0
    main_window._run_controller._on_stage_started(a, "a.png", "correction", 1, 1)

    assert main_window._run_session.pace._work_started_at is None

    clock["now"] = 1010.0
    main_window._run_controller._on_stage_detail(
        a, WaitDetail(WaitKind.BUSY, "gemma4:e4b", "ministral-3:8b")
    )

    assert "still working with ministral-3:8b" in main_window._progress.format()
    assert "still working with ministral-3:8b" in main_window._check_controller.page._run_strip.text
    assert main_window._run_session.pace._work_started_at is None

    clock["now"] = 1020.0
    main_window._run_controller._on_first_token(a)

    assert main_window._run_session.pace._work_started_at == 1020.0
    assert "correcting" in main_window._progress.format()
    assert "still working" not in main_window._check_controller.page._run_strip.text


@pytest.mark.gui
def test_a_wait_note_of_another_stage_goes_with_the_first_token(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run that reads with a local model, which shows a loading note while it waits
    When   the first token arrives
    Then   the note is gone and the bar and the strip say what the reading says again, not what
           the proofreading says
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    pretend_a_run_is_going(main_window, monkeypatch)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._run_controller._on_file_started(a, "a.png", 1, 1)
    main_window._run_controller._on_stage_started(a, "a.png", "ocr", 1, 1)

    main_window._run_controller._on_stage_detail(a, WaitDetail(WaitKind.LOADING, "qwen3-vl:8b"))

    assert "Loading qwen3-vl:8b" in main_window._progress.format()

    main_window._run_controller._on_first_token(a)

    assert "Loading" not in main_window._progress.format()
    assert "OCR" in main_window._progress.format()
    assert "Loading" not in main_window._check_controller.page._run_strip.text
    assert main_window._run_session.pace._work_started_at is not None


@pytest.mark.gui
def test_a_run_without_a_local_model_starts_its_clock_at_the_stage(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run that does not proofread on a local server
    When   a stage of its file starts
    Then   the clock starts there, so a run that never waits for a model is not held back
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(run_controller_module.time, "monotonic", lambda: clock["now"])
    pretend_a_run_is_going(main_window, monkeypatch)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._run_controller._on_file_started(a, "a.png", 1, 1)

    clock["now"] = 1005.0
    main_window._run_controller._on_stage_started(a, "a.png", "correction", 1, 1)

    assert main_window._run_session.pace._work_started_at == 1005.0


@pytest.mark.gui
def test_main_window_on_ocr_done_updates_progress_in_two_step(main_window) -> None:
    """
    Given  a MainWindow with steps_per_file=2
    When   _on_ocr_done is called for file 1 of 2
    Then   progress value advances to 1 and format mentions correcting
    """
    main_window._run_session.steps_per_file = 2
    main_window._progress.setRange(0, 4)
    main_window._results_table.set_queue([queue_row("a.png"), queue_row("b.png")])

    main_window._run_controller._on_ocr_done("a.png", "a.png", 1, 2)

    assert main_window._progress.value() == 1
    assert "correct" in main_window._progress.format().lower()


@pytest.mark.gui
def test_ok_in_settings_applies_saves_and_rechecks(main_window, monkeypatch) -> None:
    """
    Given  the Settings window closes with OK and new values from the pages it showed
    When   open_settings() returns
    Then   the config panel holds the new values, the settings are saved once, the services
           are checked again and the queue is refreshed
    """
    install_scripted_settings_window(
        monkeypatch,
        result=SettingsResult(settings={"ocr_model": "my-reader:1b", "hardware_profile": "16gb"}),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        main_window._readiness_controller, "start_check", lambda **_kw: calls.append("check")
    )
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: calls.append("save"))
    monkeypatch.setattr(main_window, "_refresh_queue_from_inputs", lambda: calls.append("queue"))

    main_window.open_settings(SettingsPage.AI_MODELS)

    settings = main_window._config_panel.get_settings_dict()
    assert settings["ocr_model"] == "my-reader:1b"
    assert settings["hardware_profile"] == "16gb"
    assert calls == ["save", "check", "queue"]


@pytest.mark.gui
def test_the_preview_asked_for_in_settings_opens_over_the_settings_window(
    main_window, monkeypatch
) -> None:
    """
    Given  the Settings window is open and its scan page asks for the image preparation preview
    When   the window emits the request
    Then   the preview is made with the Settings window as the owner of its dialogs
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    scripted = install_scripted_settings_window(monkeypatch, accepted=False)
    asked: list[tuple[object, object]] = []
    monkeypatch.setattr(
        main_window._preprocess_preview,
        "request",
        lambda ocr_config, *, parent=None: asked.append((ocr_config, parent)),
    )

    main_window.open_settings(SettingsPage.SCAN_QUALITY)
    window = scripted.instances[0]
    ocr_config = OcrConfig(preprocess_method=PreprocessMethod.NONE)
    window.preprocess_preview_requested.emit(ocr_config)

    assert asked == [(ocr_config, window)]


@pytest.mark.gui
def test_ok_in_settings_can_turn_on_removal_of_personal_info(main_window, monkeypatch) -> None:
    """
    Given  the Settings window closes with OK and asks to turn "Remove personal info" on
    When   open_settings() returns
    Then   the box is ticked through its normal path (the stage guard runs for it), and the
           setting is saved
    """
    install_scripted_settings_window(
        monkeypatch, result=SettingsResult(settings={}, turn_on_anonymization=True)
    )
    panel = main_window._config_panel
    guarded: list[str] = []
    monkeypatch.setattr(panel, "_guard_stage", lambda stage: guarded.append(stage))
    saved: list[bool] = []
    monkeypatch.setattr(
        main_window,
        "_save_settings",
        lambda **_kw: saved.append(panel.get_settings_dict()["anonymization_enabled"]),
    )
    assert not panel.get_settings_dict()["anonymization_enabled"]

    main_window.open_settings(SettingsPage.AI_MODELS)

    assert panel._anonymization_enabled.isChecked()
    assert guarded == ["anonymizer"]
    assert saved == [True]


@pytest.mark.gui
def test_cancel_in_settings_changes_nothing(main_window, monkeypatch) -> None:
    """
    Given  the Settings window closes with Cancel although its pages hold other values
    When   open_settings() returns
    Then   the config panel is unchanged and nothing is saved or re-checked
    """
    install_scripted_settings_window(
        monkeypatch, accepted=False, result=SettingsResult(settings={"ocr_model": "other"})
    )
    before = main_window._config_panel.get_settings_dict()
    calls: list[str] = []
    monkeypatch.setattr(
        main_window._readiness_controller, "start_check", lambda **_kw: calls.append("check")
    )
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: calls.append("save"))

    main_window.open_settings(SettingsPage.AI_MODELS)

    assert main_window._config_panel.get_settings_dict() == before
    assert calls == []


@pytest.mark.gui
def test_ok_in_settings_persists_a_profile_change_to_ocr_toml(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  the Settings window closes with OK after "This computer" was set to 16 GB
    When   open_settings() returns
    Then   the value is stored in the config panel and saved to ocr.toml
    """
    monkeypatch.setattr(
        SetupService,
        "default_config_path",
        lambda self: tmp_path / "ocr.toml",
    )
    install_scripted_settings_window(
        monkeypatch, result=SettingsResult(settings={"hardware_profile": "16gb"})
    )

    main_window.open_settings(SettingsPage.AI_SETUP)

    assert main_window._config_panel.get_settings_dict()["hardware_profile"] == "16gb"
    assert 'hardware_profile = "16gb"' in (tmp_path / "ocr.toml").read_text(encoding="utf-8")


@pytest.mark.gui
def test_restore_all_resets_the_panel_and_saves(main_window, monkeypatch) -> None:
    """
    Given  a changed panel and a Settings window that closed with "Restore defaults → All"
    When   open_settings() returns
    Then   every setting is back to its default, saved, with no further question
    """
    main_window._config_panel.update_settings(
        {
            "ocr_model": "my-reader:1b",
            "docx_enabled": not main_window_module.DEFAULTS["docx_enabled"],
        }
    )
    install_scripted_settings_window(
        monkeypatch,
        accepted=False,
        result=SettingsResult(settings={"ocr_model": "ignored"}, restore_all=True),
    )
    saved: list[bool] = []
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: saved.append(True))
    monkeypatch.setattr(_dialogs, "ask", lambda *a, **k: 1 / 0)

    main_window.open_settings(SettingsPage.PRIVACY)

    settings = main_window._config_panel.get_settings_dict()
    assert settings["ocr_model"] == main_window_module.DEFAULTS["ocr_model"]
    assert settings["docx_enabled"] is main_window_module.DEFAULTS["docx_enabled"]
    assert saved == [True]


@pytest.mark.gui
def test_restore_all_also_forgets_the_remembered_answers_and_the_hide_choice(
    main_window, monkeypatch
) -> None:
    """
    Given  remembered "Don't ask again" answers and "hide personal information" switched off
    When   the Settings window closes with "Restore defaults → All settings"
    Then   the remembered answers are forgotten and the hide choice is back to its default,
           so "Reset everything" really resets what it says
    """
    from teachers_teammate.gui._privacy_settings import (  # noqa: PLC0415
        SETTINGS_KEY,
        FlowAcknowledgements,
    )

    from ._gui_harness import MemorySettings  # noqa: PLC0415

    store = MemorySettings()
    acknowledgements = FlowAcknowledgements(lambda: store)
    store.setValue(SETTINGS_KEY, "abc123\tProofreading: online")
    assert acknowledgements.fingerprints() == {"abc123"}
    monkeypatch.setattr(main_window, "_acknowledgements", lambda: acknowledgements)
    shown: list[bool] = []
    monkeypatch.setattr(main_window._privacy_controller, "apply_display_anonymized", shown.append)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    install_scripted_settings_window(
        monkeypatch, accepted=False, result=SettingsResult(settings={}, restore_all=True)
    )

    main_window.open_settings(SettingsPage.PRIVACY)

    assert acknowledgements.fingerprints() == set()
    assert shown == [main_window_module.DISPLAY_ANONYMIZED_DEFAULT]


@pytest.mark.gui
def test_confirming_instructions_saves_the_settings_at_once(main_window, monkeypatch) -> None:
    """
    Given  the main window
    When   an Instructions dialog of the workflow panel is confirmed
    Then   the settings are saved right away, as they were when confirming Proofreading
           Settings (not only when the app closes or a run starts)
    """
    saved: list[bool] = []
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: saved.append(True))

    main_window._config_panel.instructions_changed.emit()

    assert saved == [True]


@pytest.mark.gui
def test_the_pull_manager_pulls_the_whole_queue_on_its_own(qtbot, monkeypatch) -> None:
    """
    Given  a pull of three models started through the manager (nothing else is listening,
           as after the Settings window was closed)
    When   each pull finishes in turn
    Then   the next model starts by itself, subscribers see the queue as it was when a pull
           ended, and a failed pull drops the rest
    """
    threads = fake_pull_threads(monkeypatch)
    manager = main_window_module.PullManager(service=real_service())
    started: list[str] = []
    queue_seen: list[list[str]] = []
    manager.started.connect(started.append)
    manager.finished.connect(lambda _name: queue_seen.append(manager.queued))

    manager.start("http://host:11434", "a:1b", ["b:1b", "c:1b"])
    threads[0].finished_ok.emit("a:1b")
    threads[1].finished_ok.emit("b:1b")

    assert started == ["a:1b", "b:1b", "c:1b"]
    assert [t.url for t in threads] == ["http://host:11434"] * 3
    assert queue_seen == [["b:1b", "c:1b"], ["c:1b"]]

    errors: list[str] = []
    manager.error.connect(errors.append)
    threads[2].finished_err.emit("boom")
    assert errors == ["boom"]
    assert manager.queued == []


@pytest.mark.gui
def test_a_pull_thread_outlives_the_manager_that_started_it(qtbot, monkeypatch) -> None:
    """
    Given  a pull that reported "finished" from run(), so the manager let go of its thread
    When   the thread is still running its last lines
    Then   the thread is still held (it is parked until it ends), because dropping the last
           reference to a running QThread aborts the whole process
    """
    from teachers_teammate.gui import _long_job  # noqa: PLC0415

    threads = fake_pull_threads(monkeypatch)
    manager = main_window_module.PullManager(service=real_service())
    held_before = set(_long_job._LIVE)

    manager.start("http://host:11434", "a:1b")
    thread = threads[0]
    thread.finished_ok.emit("a:1b")

    try:
        assert manager._thread is None
        assert thread in _long_job._LIVE
    finally:
        thread.wait(5000)  # the hold may only go once the native thread has ended
        _long_job._LIVE.intersection_update(held_before)


@pytest.mark.gui
def test_aborting_a_pull_drops_the_queue(qtbot, monkeypatch) -> None:
    """
    Given  a running pull with two more models queued
    When   it is aborted
    Then   nothing else is queued, so the aborted round does not go on with the next model
    """
    fake_pull_threads(monkeypatch)
    manager = main_window_module.PullManager(service=real_service())
    manager.start("http://host:11434", "a:1b", ["b:1b", "c:1b"])

    manager.abort()

    assert manager.queued == []


@pytest.mark.gui
def test_opening_settings_while_it_is_open_only_switches_the_page(main_window, monkeypatch) -> None:
    """
    Given  a Settings window that is open (a dialog on top of it asks for Add-ons)
    When   open_settings() is called again
    Then   no second window is created; the open one shows the page with the focus
    """
    scripted = install_scripted_settings_window(monkeypatch)
    scripted.isVisible = lambda self: True  # ty: ignore[invalid-assignment]
    first = scripted(
        main_window._settings_launcher.context(), main_window, page=SettingsPage.AI_MODELS
    )
    main_window._settings_launcher._open_window = first
    created_before = len(scripted.instances)

    main_window.open_settings(SettingsPage.ADDONS, focus=FOCUS_LOCAL_MODELS, preselect=["x"])

    assert len(scripted.instances) == created_before
    assert first.shown == [(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, ["x"])]


@pytest.mark.gui
def test_the_settings_window_gets_the_current_state(main_window, monkeypatch) -> None:
    """
    Given  a main window with its own pull manager and a changed output folder
    When   Settings is opened
    Then   the window is built from a copy of the panel's settings, the language, the anonymizer
           rules, the acknowledgements and the window's pull manager
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)
    main_window._config_panel.update_settings({"output": "/tmp/out"})

    main_window.open_settings(SettingsPage.PRIVACY)

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    context = window.context
    assert context.settings["output"] == "/tmp/out"
    assert context.language == main_window._config_panel.lookup_language()
    assert context.pull_manager is main_window._pull_manager
    assert context.app_service is main_window._app_service
    assert context.anonymizer_config == main_window._config_panel.build_anonymizer_config()


# ── menu bar ───────────────────────────────────────────────────────────────────


def _menu(window: MainWindow, title: str):
    return next(a.menu() for a in window.menuBar().actions() if a.text() == title)


@pytest.mark.gui
def test_menu_bar_has_file_settings_tools_and_help(main_window) -> None:
    """
    Given  the main window
    When   its menu bar is read
    Then   it has File, Settings, Tools and Help, and no Advanced Settings menu
    """
    titles = [a.text() for a in main_window.menuBar().actions()]

    assert titles == ["File", "Settings", "Tools", "Help"]


@pytest.mark.gui
def test_help_opens_the_folder_the_log_is_written_to(main_window, monkeypatch, tmp_path) -> None:
    """
    Given  the main window and the folder this process logs into
    When   Help → Open Log Folder is triggered
    Then   the file manager is sent exactly that folder
    """
    folder = tmp_path / "data" / "logs"
    opened: list[str] = []
    monkeypatch.setattr(main_window_module, "log_folder", lambda: folder)
    monkeypatch.setattr(
        main_window_module.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile())
    )

    main_window._menu_bar.log_folder_action.trigger()

    assert opened == [str(folder)]
    assert folder.is_dir()


@pytest.mark.gui
def test_tools_menu_lists_benchmark_and_the_system_monitor(main_window) -> None:
    """
    Given  the main window
    When   its Tools menu is read
    Then   it offers Benchmark… and System Monitor, then the Setup Assistant on its own
    """
    labels = [a.text() for a in _menu(main_window, "Tools").actions()]

    assert labels == ["Benchmark\u2026", "System Monitor", "", "Setup Assistant\u2026"]


@pytest.mark.gui
def test_the_window_has_shortcuts_for_both_tabs(main_window) -> None:
    """
    Given  the main window
    When   its shortcuts are read
    Then   Ctrl+1 and Ctrl+2 reach the two tabs
    """
    keys = {shortcut.key().toString() for shortcut in main_window.findChildren(QShortcut)}

    assert {"Ctrl+1", "Ctrl+2"} <= keys


@pytest.mark.gui
def test_the_first_start_opens_maximized_and_a_saved_geometry_wins(
    qtbot, main_window, monkeypatch
) -> None:
    """
    Given  a window and a settings store, empty at first and then holding a normal geometry
    When   the window restores its state
    Then   it is maximized without a saved geometry, and not maximized with one
    """
    values: dict[str, object] = {}

    class _Store:
        def __init__(self, *_args: object) -> None:
            pass

        def value(self, key: str, default: object = None) -> object:
            return values.get(key, default)

    monkeypatch.setattr(main_window_module, "app_settings", lambda: _Store())
    main_window.setWindowState(Qt.WindowState.WindowNoState)

    main_window._settings_file.restore_gui_state()
    assert main_window.isMaximized()

    main_window.setWindowState(Qt.WindowState.WindowNoState)
    main_window.resize(900, 700)
    values["gui/geometry"] = main_window.saveGeometry()
    main_window._settings_file.restore_gui_state()
    assert not main_window.isMaximized()


@pytest.mark.gui
def test_the_redone_document_is_shown_again_when_its_result_arrives(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  an open document that a run has emptied
    When   the run reports the document done
    Then   it is loaded again, on the tab the teacher is on
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=a,
                name="a.png",
                status=FileStatus.SAVED,
                warnings=("saved",),
            ),
            ocr_s=0.1,
            correction_s=0.1,
        )
    )

    assert main_window._run_session.pending == set()
    assert main_window._tabs.currentIndex() == 0


@pytest.mark.gui
def test_a_run_that_ends_before_the_open_document_shows_it_again(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  an open document that a run emptied and never reached
    When   the run finishes
    Then   the document is loaded again from what it has, so Check is not left empty
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    seen: list[str] = []
    monkeypatch.setattr(main_window._check_controller, "load", seen.append)

    main_window._run_controller._on_run_finished(RunSummary())

    assert seen == [a]


class _FakeBenchmarkWindow(QWidget):
    """A Benchmark window that records how often it was shown and closed."""

    created: ClassVar[list[_FakeBenchmarkWindow]] = []

    def __init__(self, *, app_services: object = None) -> None:
        super().__init__()
        self.app_services = app_services
        self.closed = 0
        self.shown = 0
        self.reloaded = 0
        _FakeBenchmarkWindow.created.append(self)

    def reload_settings(self) -> None:
        self.reloaded += 1

    def show(self) -> None:
        self.shown += 1
        super().show()

    def close(self) -> bool:
        self.closed += 1
        return super().close()


@pytest.fixture
def fake_benchmark(monkeypatch):
    """Replace the Benchmark window factory with a recording stand-in."""
    from teachers_teammate.gui.benchmark import composition  # noqa: PLC0415

    _FakeBenchmarkWindow.created = []
    monkeypatch.setattr(composition, "build_benchmark_window", _FakeBenchmarkWindow)
    return _FakeBenchmarkWindow


@pytest.mark.gui
def test_tools_benchmark_opens_one_window_and_a_second_click_reuses_it(
    qtbot, main_window, fake_benchmark
) -> None:
    """
    Given  the main window
    When   Tools > Benchmark… is chosen twice
    Then   one parentless Benchmark window exists and was shown both times
    """
    main_window._menu_bar.benchmark_action.trigger()
    main_window._menu_bar.benchmark_action.trigger()

    assert len(fake_benchmark.created) == 1
    window = fake_benchmark.created[0]
    qtbot.addWidget(window)
    assert window.shown == 2
    assert window.parent() is None
    assert window.reloaded == 0  # it never went away: nothing to reload


@pytest.mark.gui
def test_the_benchmark_window_shares_the_main_windows_service(
    qtbot, main_window, fake_benchmark
) -> None:
    """
    Given  the main window
    When   Tools > Benchmark… opens the Benchmark window
    Then   it is built with the main window's own application service, so both share one set of
           caches
    """
    main_window._menu_bar.benchmark_action.trigger()

    window = fake_benchmark.created[0]
    qtbot.addWidget(window)
    assert window.app_services is main_window._app_service


@pytest.mark.gui
def test_reopening_a_closed_benchmark_window_follows_the_settings_again(
    qtbot, main_window, fake_benchmark
) -> None:
    """
    Given  a Benchmark window the teacher closed
    When   Tools > Benchmark… is chosen again
    Then   the same window is shown again after it reloaded the saved server and profile
    """
    main_window._menu_bar.benchmark_action.trigger()
    window = fake_benchmark.created[0]
    qtbot.addWidget(window)
    window.hide()  # closed by the teacher

    main_window._menu_bar.benchmark_action.trigger()

    assert len(fake_benchmark.created) == 1
    assert window.reloaded == 1
    assert window.isVisible()


@pytest.mark.gui
def test_closing_the_main_window_closes_the_benchmark_window(
    qtbot, main_window, fake_benchmark
) -> None:
    """
    Given  an open Benchmark window
    When   the main window is closed
    Then   the Benchmark window's close() ran, so its worker is stopped
    """
    main_window._menu_bar.benchmark_action.trigger()
    window = fake_benchmark.created[0]
    qtbot.addWidget(window)

    main_window.close()

    assert window.closed == 1


@pytest.mark.gui
def test_the_system_monitor_is_hidden_until_the_menu_entry_shows_it(qtbot, main_window) -> None:
    """
    Given  the main window, whose monitor is built at start-up
    When   Tools > System Monitor is chosen
    Then   the monitor window was hidden before and is visible after, as a window of its
           own that no other window owns (an owned window stays on top of its owner)
    """
    monitor = main_window._monitor
    assert not monitor.isVisible()

    main_window._menu_bar.monitor_action.trigger()

    assert monitor.isVisible()
    assert monitor.isWindow()
    assert monitor.parent() is None


@pytest.mark.gui
def test_closing_the_main_window_closes_an_open_system_monitor(qtbot, main_window) -> None:
    """
    Given  an open System Monitor next to the main window
    When   the main window is closed
    Then   the monitor is closed with it, so no window keeps the app alive
    """
    main_window._menu_bar.monitor_action.trigger()
    assert main_window._monitor.isVisible()

    main_window.close()

    assert not main_window._monitor.isVisible()


@pytest.mark.gui
def test_the_system_monitor_stays_open_while_the_main_window_is_used(qtbot, main_window) -> None:
    """
    Given  an open System Monitor
    When   the main window is activated and the menu entry is chosen a second time
    Then   the monitor stays open (it can run in parallel) and is raised, not duplicated
    """
    main_window._menu_bar.monitor_action.trigger()
    main_window.activateWindow()
    main_window._menu_bar.monitor_action.trigger()

    assert main_window._monitor.isVisible()


@pytest.mark.gui
def test_the_stats_poll_only_while_the_monitor_is_visible(qtbot, main_window) -> None:
    """
    Given  the main window and its System Monitor
    When   the monitor is shown and hidden again
    Then   the stats timer runs only in between
    """
    pytest.importorskip("psutil")
    stats = main_window._stats
    assert not stats._timer.isActive()

    main_window._monitor.show()
    assert stats._timer.isActive()

    main_window._monitor.hide()
    assert not stats._timer.isActive()


@pytest.mark.gui
def test_the_chart_collects_a_run_while_the_monitor_is_hidden(main_window) -> None:
    """
    Given  a hidden System Monitor
    When   a file finishes and the chart is updated
    Then   the monitor's own chart holds the data, ready for when it is opened
    """
    assert main_window._chart is main_window._monitor.chart

    main_window._chart.update_data(["a.png"], [1.0], [2.0])

    assert main_window._monitor.chart._names == ["a.png"]


@pytest.mark.gui
def test_the_monitor_remembers_its_geometry(qtbot, main_window, monkeypatch) -> None:
    """
    Given  a System Monitor that was shown and hidden
    When   a new monitor window is built
    Then   it is given the geometry the first one saved
    """
    from teachers_teammate.gui import _system_monitor  # noqa: PLC0415

    stored: dict[str, object] = {}

    class MemorySettings:
        def __init__(self, *_args: object) -> None:
            pass

        def value(self, key: str, default: object = None) -> object:
            return stored.get(key, default)

        def setValue(self, key: str, value: object) -> None:
            stored[key] = value

    monkeypatch.setattr(_system_monitor, "app_settings", lambda: MemorySettings())
    restored: list[object] = []
    monkeypatch.setattr(
        _system_monitor.SystemMonitorWindow,
        "restoreGeometry",
        lambda self, data: restored.append(data) or True,
    )
    monitor = _system_monitor.SystemMonitorWindow(lambda: None)
    qtbot.addWidget(monitor)
    monitor.show()
    monitor.hide()
    assert _system_monitor.GEOMETRY_KEY in stored

    other = _system_monitor.SystemMonitorWindow(lambda: None)
    qtbot.addWidget(other)

    assert restored == [stored[_system_monitor.GEOMETRY_KEY]]


@pytest.mark.gui
def test_settings_menu_lists_the_pages_then_all_settings(main_window) -> None:
    """
    Given  the main window
    When   the Settings menu is read
    Then   it lists the five main pages by name (not the two sub-pages that sit under AI models
           in the Settings window), a separator and "All settings…" on Ctrl+,
    """
    menu = _menu(main_window, "Settings")

    entries = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert entries == [
        "General\u2026",
        "AI models\u2026",
        "Scan quality\u2026",
        "Privacy\u2026",
        "Add-ons\u2026",
        "All settings\u2026",
    ]
    all_settings = menu.actions()[-1]
    assert all_settings.shortcut().toString() == "Ctrl+,"


@pytest.mark.gui
@pytest.mark.parametrize("page", [p for p in SettingsPage if p not in SUB_PAGES])
def test_each_settings_menu_entry_opens_its_page(main_window, monkeypatch, page) -> None:
    """
    Given  the Settings menu
    When   the entry of a page is triggered
    Then   the Settings window opens on exactly that page
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._menu_bar.settings_actions[page].trigger()

    assert [w.page for w in scripted.instances] == [page]


@pytest.mark.gui
def test_all_settings_opens_the_page_used_last(main_window, monkeypatch) -> None:
    """
    Given  the page used last was Privacy
    When   "All settings…" is triggered
    Then   the Settings window opens on Privacy
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)
    monkeypatch.setattr(menu_bar_module, "last_page", lambda: SettingsPage.PRIVACY)

    main_window._menu_bar.all_settings_action.trigger()

    assert [w.page for w in scripted.instances] == [SettingsPage.PRIVACY]


@pytest.mark.gui
def test_file_menu_opens_documents_and_exports(main_window, monkeypatch) -> None:
    """
    Given  the File menu
    When   "Open Folder…" and "Open Files…" are triggered
    Then   the panel's folder and file selection are started, and Export ▸ lists the export kinds
    """
    calls: list[str] = []
    selection = main_window._config_panel._selection
    monkeypatch.setattr(selection, "choose_folder", lambda: calls.append("folder"))
    monkeypatch.setattr(selection, "choose_files", lambda: calls.append("files"))
    menu = _menu(main_window, "File")
    by_text = {a.text(): a for a in menu.actions()}

    by_text["Open Folder\u2026"].trigger()
    by_text["Open Files\u2026"].trigger()

    assert calls == ["folder", "files"]
    export = by_text["Export"].menu()
    assert [a.text() for a in export.actions()] == [
        a.text() for a in main_window._export_btn.menu().actions()
    ]
    assert "Quit" in by_text


@pytest.mark.gui
def test_file_export_entries_follow_the_export_button(main_window) -> None:
    """
    Given  a window with nothing to export
    When   a result arrives
    Then   the Export button and the File → Export entries are enabled together
    """
    entries = _menu(main_window, "File")
    export = next(a for a in entries.actions() if a.text() == "Export").menu()
    assert not main_window._export_btn.isEnabled()
    assert not any(a.isEnabled() for a in export.actions())

    main_window._export_controller.set_enabled(True)

    assert main_window._export_btn.isEnabled()
    assert all(a.isEnabled() for a in export.actions())


@pytest.mark.gui
def test_the_main_panel_has_no_restore_defaults_button(main_window) -> None:
    """
    Given  the main window
    When   its buttons are listed
    Then   Restore Defaults is gone from the left panel (it lives in the Settings window)
    """
    from PySide6.QtWidgets import QPushButton  # noqa: PLC0415

    texts = [b.text() for b in main_window.findChildren(QPushButton)]

    assert "Restore Defaults" not in texts


@pytest.mark.gui
def test_results_step_change_saves_and_refreshes_the_queue(main_window, monkeypatch) -> None:
    """
    Given  the Results step in the main panel
    When   the user ticks text files
    Then   the settings are saved and the queue is refreshed (as the old Output dialog's OK did)
    """
    calls: list[str] = []
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: calls.append("save"))
    monkeypatch.setattr(main_window, "_refresh_queue_from_inputs", lambda: calls.append("queue"))

    main_window._config_panel._results._text_enabled.setChecked(True)

    assert calls == ["save", "queue"]


@pytest.mark.gui
def test_the_preview_opens_on_top_of_the_settings_window(main_window, monkeypatch) -> None:
    """
    Given  the Settings window asks for a preview of the image preparation
    When   the main window shows its result
    Then   the dialogs belong to the Settings window, so they cannot open behind it
    """
    from PySide6.QtGui import QPixmap  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    owner = QWidget()
    created: list[object] = []

    class _FakeDialog(QDialog):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            created.append(parent)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(preprocess_module, "QDialog", _FakeDialog)
    monkeypatch.setattr(preprocess_module, "load_pixmap", lambda _p: QPixmap())
    owner.show()

    main_window._preprocess_preview._on_done(
        Path("a.png"), Path("b.png"), ["deskew"], "", OcrConfig(), Path("/tmp/none"), parent=owner
    )

    assert created == [owner]


@pytest.mark.gui
def test_main_window_load_toml_if_present_loads_values(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a valid TOML config file exists at the resolved config path
    When   MainWindow is initialised (calls _load_toml_if_present internally)
    Then   the config path is set to the resolved path
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text('language = "German"\n', encoding="utf-8")

    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(
        "teachers_teammate.application.setup_plan.SetupService.resolve_config_path",
        lambda self, _: config_path,
    )
    monkeypatch.setattr(
        "teachers_teammate.gui._settings_file.load_config_file",
        lambda _path: LoadedSettings({"language": "German"}),
    )
    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)
    assert win._settings_file.config_path == config_path


@pytest.mark.gui
def test_main_window_load_toml_if_present_no_path(qtbot, monkeypatch) -> None:
    """
    Given  resolve_config_path returns None
    When   _load_toml_if_present is called
    Then   method returns immediately without setting a config path
    """
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)

    monkeypatch.setattr(
        "teachers_teammate.application.setup_plan.SetupService.resolve_config_path",
        lambda self, _: None,
    )
    win._settings_file._config_path = None
    win._load_toml_if_present()
    assert win._settings_file.config_path is None


@pytest.mark.gui
def test_main_window_load_toml_if_present_exception_logged(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  load_config_file raises an exception
    When   _load_toml_if_present is called
    Then   the warning is logged and no crash occurs
    """
    config_path = tmp_path / "bad.toml"
    config_path.write_text("bad content", encoding="utf-8")

    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)

    monkeypatch.setattr(
        "teachers_teammate.application.setup_plan.SetupService.resolve_config_path",
        lambda self, _: config_path,
    )

    def _raise(_):
        raise ValueError("parse error")

    monkeypatch.setattr(
        "teachers_teammate.gui._settings_file.load_config_file",
        _raise,
    )
    win._load_toml_if_present()  # should not raise


@pytest.mark.gui
def test_main_window_drag_enter_event_accepts_urls(main_window) -> None:
    """
    Given  a drag event carrying URL data
    When   dragEnterEvent is called
    Then   the proposed action is accepted
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    event = MagicMock()
    mime = MagicMock()
    mime.hasUrls.return_value = True
    event.mimeData.return_value = mime
    main_window.dragEnterEvent(event)
    event.acceptProposedAction.assert_called_once()


@pytest.mark.gui
def test_main_window_drop_event_sets_input_dir(main_window, monkeypatch, tmp_path) -> None:
    """
    Given  a drop event with a directory path URL
    When   dropEvent is called
    Then   the config panel input dir is updated
    """
    from unittest.mock import MagicMock  # noqa: PLC0415
    from PySide6.QtCore import QUrl  # noqa: PLC0415

    spy = MagicMock()
    monkeypatch.setattr(main_window._config_panel, "set_input_dir", spy)

    event = MagicMock()
    mime = MagicMock()
    url = QUrl.fromLocalFile(str(tmp_path))
    mime.urls.return_value = [url]
    event.mimeData.return_value = mime
    main_window.dropEvent(event)
    spy.assert_called_once_with(str(tmp_path))


@pytest.mark.gui
def test_main_window_on_preprocess_preview_no_source_user_cancels(
    main_window, monkeypatch, tmp_path
) -> None:
    """
    Given  input dir has no images and user cancels the file dialog
    When   PreprocessPreview.request is called
    Then   method returns without error and nothing is prepared
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))
    monkeypatch.setattr(_dialogs, "pick_file", lambda *_a, **_k: None)
    main_window._preprocess_preview.request(
        OcrConfig(preprocess_method=PreprocessMethod.NONE)
    )  # should not raise
    assert not main_window._preprocess_preview._busy


@pytest.mark.gui
def test_main_window_on_preprocess_preview_preprocess_error(
    main_window, monkeypatch, tmp_path, qtbot
) -> None:
    """
    Given  preprocess_preview raises an exception
    When   PreprocessPreview.request runs its background task to completion
    Then   a warning dialog is shown and no crash occurs

    The preparation runs off the GUI thread, so the warning only appears once its
    answer has been delivered.
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"fake")
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        main_window._app_service.reader,
        "preprocess_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad file")),
    )
    shown = record_notices(monkeypatch)
    main_window._preprocess_preview.request(OcrConfig(preprocess_method=PreprocessMethod.NONE))
    assert main_window._preprocess_preview._busy
    assert wait_for_background_tasks(THREAD_WAIT_MS), "the preview never finished"
    qtbot.waitUntil(lambda: bool(shown), timeout=THREAD_WAIT_MS)

    assert [notice.title for notice in shown] == ["Preview failed"]


@pytest.mark.gui
def test_main_window_on_preprocess_preview_ignores_concurrent_click(
    main_window, monkeypatch, tmp_path, qtbot
) -> None:
    """
    Given  a preprocessing preview already running in the background
    When   PreprocessPreview.request is called again before it finishes
    Then   the second call is ignored: the preparation runs once, and the error dialog
           for it appears exactly once
    """
    import time  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"fake")
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))

    call_count = {"n": 0}

    def _slow_failing_preview(*_args, **_kwargs):
        call_count["n"] += 1
        time.sleep(0.3)
        raise ValueError("bad file")

    monkeypatch.setattr(
        main_window._app_service.reader, "preprocess_preview", _slow_failing_preview
    )

    shown = record_notices(monkeypatch)
    main_window._preprocess_preview.request(OcrConfig(preprocess_method=PreprocessMethod.NONE))
    assert main_window._preprocess_preview._busy
    main_window._preprocess_preview.request(OcrConfig(preprocess_method=PreprocessMethod.NONE))

    assert wait_for_background_tasks(THREAD_WAIT_MS), "the preview never finished"
    qtbot.waitUntil(lambda: bool(shown), timeout=THREAD_WAIT_MS)
    assert call_count["n"] == 1
    assert len(shown) == 1


# ── start_stage branches ─────────────────────────────────────────


@pytest.mark.gui
def test_stage_run_warns_on_config_error(main_window, monkeypatch) -> None:
    """
    Given  the config panel cannot build a Config
    When   start_stage is invoked
    Then   a warning dialog is shown and no worker starts
    """
    from unittest.mock import patch  # noqa: PLC0415

    monkeypatch.setattr(
        main_window._config_panel,
        "to_config",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))

    shown = record_notices(monkeypatch)
    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")

    assert [notice.title for notice in shown] == ["Configuration error"]
    assert started == []


@pytest.mark.gui
def test_stage_run_force_runs_correction_when_checkbox_disabled(main_window, monkeypatch) -> None:
    """
    Given  correction is disabled in the config
    When   start_stage is invoked for the correction stage (right-click)
    Then   the stage runs anyway with a config where correction_enabled is forced True
    """
    cfg = make_config(Path("/tmp"), correction_enabled=False)
    _queue_a(main_window)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    captured: list = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda config, **k: captured.append(config) or (),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))

    main_window._run_start.start_stage(["/tmp/a.png"], "correction")
    _wait_for(lambda: bool(started))

    assert captured and captured[0].correction_enabled is True
    assert started  # worker started despite the checkbox being off


@pytest.mark.gui
def test_stage_run_force_runs_evaluation_when_checkbox_disabled(main_window, monkeypatch) -> None:
    """
    Given  evaluation is disabled in the config
    When   start_stage is invoked for the evaluation stage (right-click)
    Then   the stage runs with evaluation_enabled and correction_enabled forced True
    """
    cfg = make_config(Path("/tmp"), evaluation_enabled=False, correction_enabled=False)
    _queue_a(main_window)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    captured: list = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda config, **k: captured.append(config) or (),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))

    main_window._run_start.start_stage(["/tmp/a.png"], "evaluation")
    _wait_for(lambda: bool(started))

    assert captured
    assert captured[0].evaluation_enabled is True
    assert captured[0].correction_enabled is True
    assert started


@pytest.mark.gui
def test_queued_files_of_an_empty_selection_is_no_file(main_window) -> None:
    """
    Given  a queue of one file
    When   the files of an empty id list, of the queued id and of no list are asked
    Then   the empty list names no file, only None names the whole queue
    """
    _queue_a(main_window)

    assert main_window._queued_files([]) == []
    assert main_window._queued_files(["/tmp/a.png"]) == [Path("/tmp/a.png")]
    assert main_window._queued_files() == [Path("/tmp/a.png")]


@pytest.mark.gui
def test_stage_run_informs_when_the_selection_is_not_in_the_queue(main_window, monkeypatch) -> None:
    """
    Given  selected rows that are no longer in the queue
    When   start_stage is invoked for the OCR stage
    Then   a 'Selection mismatch' dialog is shown, nothing is invalidated and no worker starts
    """
    cfg = make_config(Path("/tmp"))
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    invalidated: list = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *a, **k: invalidated.append(a) or (),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))

    shown = record_notices(monkeypatch)
    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")
    _wait_for(lambda: bool(shown))

    assert [notice.title for notice in shown] == ["Selection mismatch"]
    assert invalidated == []
    assert started == []


@pytest.mark.gui
def test_stage_run_warns_on_invalidation_failure(main_window, monkeypatch) -> None:
    """
    Given  invalidate_from_stage reports failures
    When   start_stage is invoked
    Then   a warning dialog is shown and no worker starts
    """
    from unittest.mock import patch  # noqa: PLC0415

    cfg = make_config(Path("/tmp"))
    _queue_a(main_window)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *a, **k: (("a.png", "locked"),),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))

    shown = record_notices(monkeypatch)
    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")
    _wait_for(lambda: bool(shown))

    assert [notice.title for notice in shown] == ["Cache invalidation failed"]
    assert started == []


@pytest.mark.gui
def test_stage_run_starts_worker_on_success(main_window, monkeypatch) -> None:
    """
    Given  invalidate_from_stage returns runnable source ids with no failures
    When   start_stage is invoked
    Then   the worker is started with those source ids
    """
    cfg = make_config(Path("/tmp"), correction_enabled=False)
    _queue_a(main_window)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *a, **k: (),
    )
    started: list = []
    monkeypatch.setattr(
        main_window._run_controller,
        "start",
        lambda config, ids, _files=None: started.append((config, ids)),
    )

    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")
    _wait_for(lambda: bool(started))

    assert started == [(cfg, ["/tmp/a.png"])]


# ── Credentials loading ────────────────────────────────────────────────────


@pytest.mark.gui
def test_main_window_loads_credentials_on_startup(qtbot, monkeypatch, credential_file) -> None:
    """
    Given  a stored API key for a service (saved by an earlier session)
    When   MainWindow is created
    Then   the app holds the key, and the environment is left alone
    """
    import os  # noqa: PLC0415

    credential_file["credentials/OPENAI_API_KEY"] = "test-value-from-settings"
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)

    assert win._app_service.keys.held("OPENAI_API_KEY") == "test-value-from-settings"
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.gui
def test_main_window_env_var_takes_priority_over_stored_credential(
    qtbot, monkeypatch, credential_file
) -> None:
    """
    Given  an env var is already set AND the settings file has a different value for that key
    When   MainWindow is created
    Then   the key of the environment is the one used, and the stored value is not held
    """
    credential_file["credentials/OPENAI_API_KEY"] = "stored-value"
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setenv("OPENAI_API_KEY", "env-value")

    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)

    assert win._app_service.keys.key("OPENAI_API_KEY") == "env-value"
    assert win._app_service.keys.held("OPENAI_API_KEY") is None


# ── first-start model choice (AC12/AC13) ──────────────────────────────────


def _make_first_start_window(qtbot, monkeypatch, tmp_path: Path, detection) -> MainWindow:
    """Create a MainWindow that takes the first-start path (no ocr.toml).

    The assistant opens on top of the window; it is cancelled here, which is the way out
    that leaves today's setup in place.
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    no_update_check(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupAssistant, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, explicit: None)
    monkeypatch.setattr(
        SetupService,
        "default_config_path",
        lambda self: tmp_path / "ocr.toml",
    )
    monkeypatch.setattr(
        ModelRecommendationService, "detect_hardware", lambda self, url, **_kw: detection
    )
    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)
    return win


def test_a_settings_file_from_the_installer_is_still_a_first_start(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  an ocr.toml that holds only check_for_updates = false, written by the installer
    When   the main window starts and then saves
    Then   the Setup Assistant still opens, the update check stays off, and the saved file
           keeps that choice
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    settings = tmp_path / "ocr.toml"
    settings.write_text("check_for_updates = false\n", encoding="utf-8")
    opened: list[bool] = []
    no_update_check(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(
        SetupLauncher, "_apply_first_start_defaults", lambda self: self._hooks.save()
    )
    monkeypatch.setattr(
        SetupAssistant,
        "exec",
        lambda self: opened.append(True) or QDialog.DialogCode.Rejected,
    )
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, explicit: settings)

    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)
    qtbot.waitUntil(lambda: bool(opened), timeout=5000)
    qtbot.waitUntil(lambda: "check_for_updates = false" in settings.read_text("utf-8"))

    assert win._config_panel.get_settings_dict()["check_for_updates"] is False


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_first_start_selects_best_models_for_profile(qtbot, monkeypatch, tmp_path: Path) -> None:
    """
    Given  a first start (no ocr.toml) on a detected 16 GB machine
    When   background detection finishes
    Then   the models are set to the 16 GB BEST picks, the choice is logged,
           and settings are saved (AC12)
    """
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4090",
        memory_gb=24.0,
    )
    win = _make_first_start_window(qtbot, monkeypatch, tmp_path, detection)
    qtbot.waitUntil(lambda: "First start" in win._log.toPlainText(), timeout=5000)

    settings = win._config_panel.get_settings_dict()
    best_16gb = RECOMMENDED["general"][HardwareProfile.CARD_16GB][0]  # not the default: 8 GB
    assert settings["ocr_model"] == DEFAULT_OCR_MODEL
    assert settings["correction_model"] == best_16gb
    assert settings["evaluate_model"] == best_16gb
    assert (
        f"chose reading model {DEFAULT_OCR_MODEL} and writing model {best_16gb}"
        in win._log.toPlainText()
    )
    saved = (tmp_path / "ocr.toml").read_text(encoding="utf-8")
    assert f'ocr_model = "{DEFAULT_OCR_MODEL}"' in saved


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_existing_config_models_untouched(qtbot, monkeypatch, tmp_path: Path) -> None:
    """
    Given  an existing ocr.toml with a custom model
    When   the window starts
    Then   no detection-based model change happens (AC13)
    """

    config_path = tmp_path / "ocr.toml"
    config_path.write_text(
        'ocr_model = "my-model:latest"\n'
        'correction_model = "my-writer:latest"\n'
        'evaluate_model = "my-writer:latest"\n',
        encoding="utf-8",
    )
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4090",
        memory_gb=24.0,
    )
    detect_calls: list = []
    no_update_check(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, explicit: config_path)
    monkeypatch.setattr(
        SetupService,
        "default_config_path",
        lambda self: config_path,
    )

    def _spy_detect(self, url, **_kw):
        detect_calls.append(url)
        return detection

    monkeypatch.setattr(ModelRecommendationService, "detect_hardware", _spy_detect)
    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)

    settings = win._config_panel.get_settings_dict()
    assert settings["ocr_model"] == "my-model:latest"
    assert settings["correction_model"] == "my-writer:latest"
    assert detect_calls == []


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_first_start_non_ollama_correction_provider_untouched(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a first start where the correction provider is not Ollama
    When   background detection finishes
    Then   correction_model is not replaced
    """
    from teachers_teammate.config import DEFAULTS  # noqa: PLC0415
    from teachers_teammate.gui import _config_panel  # noqa: PLC0415

    # The panel seeds its settings from this module's ``DEFAULTS``: patch it there.
    monkeypatch.setattr(_config_panel, "DEFAULTS", {**DEFAULTS, "correction_provider": "openai"})
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4090",
        memory_gb=24.0,
    )
    win = _make_first_start_window(qtbot, monkeypatch, tmp_path, detection)
    qtbot.waitUntil(lambda: "First start" in win._log.toPlainText(), timeout=5000)
    settings = win._config_panel.get_settings_dict()
    assert settings["correction_model"] == DEFAULTS["correction_model"]
    assert settings["ocr_model"] == DEFAULT_OCR_MODEL  # ocr provider still updated


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_cancelling_the_assistant_on_a_first_start_keeps_todays_setup(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a first start where the teacher closes the assistant without setting anything up
    When   the window goes on
    Then   the system language and the best models for this computer are saved, so the app
           works without the assistant
    """
    monkeypatch.setattr(SetupService, "system_language", lambda self: "German")
    detection = HardwareDetection(
        profile=HardwareProfile.CARD_16GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="RTX 4090",
        memory_gb=24.0,
    )
    win = _make_first_start_window(qtbot, monkeypatch, tmp_path, detection)
    qtbot.waitUntil(lambda: "First start" in win._log.toPlainText(), timeout=5000)

    saved = (tmp_path / "ocr.toml").read_text(encoding="utf-8")
    best_16gb = RECOMMENDED["general"][HardwareProfile.CARD_16GB][0]
    assert 'language = "German"' in saved
    assert f'correction_model = "{best_16gb}"' in saved


@pytest.mark.gui
def test_cancelling_the_assistant_from_the_tools_menu_saves_nothing(
    main_window, monkeypatch
) -> None:
    """
    Given  a window that already has settings
    When   the teacher opens the assistant from the Tools menu and closes it
    Then   nothing is written and no model is chosen: the assistant only changes what the
           teacher agreed to
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    written: list = []
    monkeypatch.setattr(SetupAssistant, "exec", lambda self: QDialog.DialogCode.Rejected)
    monkeypatch.setattr(MainWindow, "_save_settings", lambda self, **_kw: written.append("save"))
    monkeypatch.setattr(
        SetupLauncher, "_apply_first_start_model_settings", lambda self: written.append("models")
    )

    main_window._setup_launcher.open()

    assert written == []


@pytest.mark.gui
def test_an_idle_install_round_does_not_outlive_the_assistant(main_window, monkeypatch) -> None:
    """
    Given  an assistant that was opened and closed without installing anything
    When   the assistant is gone
    Then   its install round is deleted too, so opening the assistant again and again leaves
           nothing behind on the window
    """
    from PySide6.QtCore import QCoreApplication, QEvent  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui._install_round import InstallRound  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    monkeypatch.setattr(SetupAssistant, "exec", lambda self: QDialog.DialogCode.Rejected)
    before = len(main_window.findChildren(InstallRound))

    main_window._setup_launcher.open()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    assert len(main_window.findChildren(InstallRound)) == before


@pytest.mark.gui
def test_closing_the_assistant_after_set_up_keeps_the_setup_on_a_first_start(
    main_window, monkeypatch
) -> None:
    """
    Given  a first start where the teacher pressed Set up
    When   the done page is closed with the window's X (a reject)
    Then   the first-start defaults do not run over the setup the teacher agreed to
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    ran: list = []

    def _exec(self) -> QDialog.DialogCode:
        self._committed = True
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SetupAssistant, "exec", _exec)
    monkeypatch.setattr(SetupLauncher, "_apply_first_start_defaults", lambda self: ran.append(1))

    main_window._setup_launcher.open(first_start=True)

    assert ran == []


@pytest.mark.gui
def test_the_tools_menu_opens_the_assistant_on_the_current_settings(
    main_window, monkeypatch
) -> None:
    """
    Given  a window whose settings name a writing model of the teacher's own
    When   the teacher picks the Setup Assistant from the Tools menu
    Then   the assistant opens on those settings, not on a first start
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415
    from teachers_teammate.gui.setup import SetupAssistant  # noqa: PLC0415

    opened: list = []

    def _record(self) -> QDialog.DialogCode:
        opened.append(self)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(SetupAssistant, "exec", _record)
    main_window._config_panel.update_settings({"correction_model": "my-writer:latest"})

    main_window._menu_bar.setup_action.trigger()

    assert len(opened) == 1
    context = opened[0]._context
    assert context.first_start is False
    assert context.settings["correction_model"] == "my-writer:latest"


@pytest.mark.gui
def test_the_setup_result_is_applied_without_a_guard_dialog(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a setup plan the teacher agreed to, with a new language and a new writing model
    When   the window takes the result over
    Then   the settings and the boxes change without a guard dialog, the window saves and
           checks readiness, and the language's instructions follow
    """
    from teachers_teammate.gui.setup import SetupResult  # noqa: PLC0415

    guarded: list = []
    saved: list = []
    checked: list = []

    def _record_guard(stage: str) -> None:
        # The real guard does nothing while saved settings are being loaded; record only the
        # calls that would have checked a requirement and possibly opened a dialog.
        if not main_window._config_panel._loading:
            guarded.append(stage)

    monkeypatch.setattr(main_window._config_panel, "_guard_stage", _record_guard)
    monkeypatch.setattr(MainWindow, "_save_settings", lambda self, **kw: saved.append(kw))
    monkeypatch.setattr(
        ReadinessController, "start_check", lambda self, **_kw: checked.append("check")
    )
    main_window._settings_file._config_path = tmp_path / "ocr.toml"

    main_window._setup_launcher._apply_result(
        SetupResult(
            settings={"correction_provider": "ollama", "correction_model": "qwen3:8b"},
            panel={"language": "German", "anonymization_enabled": True},
        )
    )

    settings = main_window._config_panel.get_settings_dict()
    assert settings["correction_model"] == "qwen3:8b"
    assert main_window._config_panel.lookup_language() == "German"
    assert guarded == []
    assert saved == [{"notify": False}]
    assert checked == ["check"]
    assert "Names in German" in main_window._config_panel._anonymization_uses.text()


# ── add-ons wiring (review findings #1, #9, #10 and the GPU tab) ───────────


@pytest.mark.use_case("Model_Category_Guidance")
def test_finished_pull_evicts_cached_model_list(main_window, monkeypatch) -> None:
    """
    Given  a pull that finished (possibly after the Settings window was closed)
    When   the pull manager reports it
    Then   the main window evicts the model list of the server it pulled from, so
           the pre-run "model missing" check does not use a stale cache
    """
    evicted: list[str] = []
    monkeypatch.setattr(
        ModelRecommendationService,
        "on_ollama_models_changed",
        lambda self, url: evicted.append(url),
    )
    main_window._pull_manager._current_url = "http://gpu-box:11434"
    main_window._on_pull_finished("qwen3-vl:8b")
    assert evicted == ["http://gpu-box:11434"]


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_missing_model_dialog_opens_addons_with_models_preselected(
    qtbot, main_window, monkeypatch
) -> None:
    """
    Given  a run whose configured models are not installed
    When   the user presses "Download…" in the dialog that stops the run
    Then   Settings opens on Add-ons, on the local models, with exactly the missing models
           preselected
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    from teachers_teammate.gui._dependency_guard_dialog import (  # noqa: PLC0415
        DependencyGuardDialog,
    )

    monkeypatch.setattr(
        DependencyGuardDialog, "exec", lambda self: self.action_chosen.emit(self.fix_actions[0])
    )
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: MagicMock())
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(missing_models=("qwen3-vl:8b", "qwen3.5:9b")),
    )
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(scripted.instances), timeout=3000)

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert window.page is SettingsPage.ADDONS
    assert window.focus == FOCUS_LOCAL_MODELS
    assert window.preselect == [
        InstallItem(InstallKind.OLLAMA, "qwen3-vl:8b"),
        InstallItem(InstallKind.OLLAMA, "qwen3.5:9b"),
    ]


@pytest.mark.gui
def test_the_panels_dependency_guard_opens_addons_with_its_focus(main_window, monkeypatch) -> None:
    """
    Given  the config panel's dependency guard asking for the name-detection add-on
    When   the panel emits open_settings_requested for Add-ons
    Then   Settings opens on Add-ons with that focus and entries
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._config_panel.open_settings_requested.emit(
        SettingsPage.ADDONS,
        FOCUS_ONLINE_SERVICES,
        [InstallItem(InstallKind.PROVIDER, "mistral")],
    )

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert window.page is SettingsPage.ADDONS
    assert window.focus == FOCUS_ONLINE_SERVICES
    assert window.preselect == [InstallItem(InstallKind.PROVIDER, "mistral")]


@pytest.mark.gui
def test_the_privacy_rules_button_opens_settings_on_the_detection_tab(
    main_window, monkeypatch
) -> None:
    """
    Given  anonymization switched on in the main panel
    When   "Privacy rules…" is clicked
    Then   Settings opens on Privacy, focused on detection
    """
    from teachers_teammate.gui.settings._page import FOCUS_DETECTION  # noqa: PLC0415

    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._config_panel._anon_configure_btn.click()

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert (window.page, window.focus) == (SettingsPage.PRIVACY, FOCUS_DETECTION)


def test_close_waits_for_background_tasks(main_window, monkeypatch, tmp_path: Path) -> None:
    """
    Given  a MainWindow
    When   it is closed
    Then   running background tasks get a bounded moment to finish
    """
    monkeypatch.setattr(SetupService, "default_config_path", lambda self: tmp_path / "ocr.toml")
    waited: list[bool] = []
    monkeypatch.setattr(
        main_window_module, "wait_for_background_tasks", lambda *a, **k: waited.append(True)
    )
    main_window.close()
    assert waited == [True]


@pytest.mark.gui
def test_main_window_file_done_keeps_the_row_when_the_store_cannot_be_read(
    main_window, monkeypatch
) -> None:
    """
    Given  a row that shows Markdown texts
    When   _on_file_done runs and the store cannot be read back
    Then   the row keeps the Markdown format instead of falling back to plain
    """
    source = "/tmp/page.png"
    main_window._results_table.set_queue(
        [queue_row(source, "page.png", ocr=StageState.DONE, has_text=True, text_format="markdown")]
    )

    def _unreadable(*_args: object, **_kwargs: object) -> list[QueueRow]:
        raise OSError("the state folder is gone")

    monkeypatch.setattr(main_window._app_service.documents, "queue_rows", _unreadable)

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=source,
                name="page.png",
                status=FileStatus.SAVED,
            ),
            ocr_s=0.0,
            correction_s=0.0,
        )
    )

    assert main_window._results_table.text_format_for_source(source) == "markdown"


@pytest.mark.gui
def test_main_window_file_started_keeps_the_text_format_of_the_shown_texts(main_window) -> None:
    """
    Given  a row that shows Markdown OCR text from the previous run
    When   the file is started again (re-run) and the user then edits its OCR text
    Then   the edit is stored with the "markdown" format, not reset to plain: the row still
           holds the old Markdown text until the new result replaces it
    """
    source = "/tmp/rerun.png"
    main_window._results_table.set_queue(
        [queue_row(source, "rerun.png", ocr=StageState.DONE, has_text=True, text_format="markdown")]
    )

    main_window._run_controller._on_file_started(source, "rerun.png", 1, 1)

    assert main_window._results_table.text_format_for_source(source) == "markdown"


# ── Re-run only the requested stage ──────────────────────────────────────────


def _queue_a(main_window) -> None:
    """Queue /tmp/a.png without a scan of the folder."""
    main_window._results_table.set_queue([queue_row("/tmp/a.png")])
    main_window._results_table.selectRow(0)


def _capture_stage_run(main_window, monkeypatch, cfg, stage: str):
    """Trigger a right-click stage run and return the config the worker would get."""
    _queue_a(main_window)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda config, **k: (),
    )
    started: list = []
    monkeypatch.setattr(main_window._run_controller, "start", lambda *a, **k: started.append(a))
    main_window._run_start.start_stage(["/tmp/a.png"], stage)
    _wait_for(lambda: bool(started))
    return started[0][0]


@pytest.mark.gui
def test_ocr_rerun_does_not_run_later_stages(main_window, monkeypatch) -> None:
    """
    Given  proofreading, content review and anonymization are all enabled
    When   "Re-run Text Recognition" is chosen from the queue context menu
    Then   the run is limited to text recognition (no later stage is enabled)
    """
    cfg = make_config(
        Path("/tmp"),
        correction_enabled=True,
        evaluation_enabled=True,
        anonymization_enabled=True,
    )

    run_config = _capture_stage_run(main_window, monkeypatch, cfg, "ocr")

    assert run_config.correction_enabled is False
    assert run_config.evaluation_enabled is False
    assert run_config.anonymization_enabled is False
    assert main_window._run_session.steps_per_file == 1


@pytest.mark.gui
def test_correction_rerun_does_not_run_evaluation(main_window, monkeypatch) -> None:
    """
    Given  content review is enabled in the config
    When   "Re-run Proofreading" is chosen from the queue context menu
    Then   proofreading runs and content review is not enabled for that run
    """
    cfg = make_config(Path("/tmp"), correction_enabled=False, evaluation_enabled=True)

    run_config = _capture_stage_run(main_window, monkeypatch, cfg, "correction")

    assert run_config.correction_enabled is True
    assert run_config.evaluation_enabled is False


@pytest.mark.gui
def test_ocr_rerun_keeps_panel_settings_untouched(main_window, monkeypatch) -> None:
    """
    Given  proofreading is ticked in the panel
    When   text recognition is re-run from the context menu
    Then   the panel's checkboxes (and so the saved settings) are unchanged
    """
    cfg = make_config(Path("/tmp"), correction_enabled=True)
    main_window._config_panel._correction_enabled.setChecked(True)

    _capture_stage_run(main_window, monkeypatch, cfg, "ocr")

    assert main_window._config_panel._correction_enabled.isChecked() is True


# ── Run-time requirement preflight ───────────────────────────────────────────


@pytest.mark.gui
def test_run_with_ollama_unreachable_shows_clear_message_and_does_not_start(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  Ollama is needed but not reachable
    When   Run is pressed
    Then   the dialog says Ollama is not running and offers the server address, no worker
           starts and the Run button is usable again
    """
    from teachers_teammate.gui._dependency_guard_dialog import (  # noqa: PLC0415
        DependencyGuardDialog,
    )

    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(ollama_unreachable="✗ Cannot connect to Ollama"),
    )
    shown: list[DependencyGuardDialog] = []
    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: shown.append(self))

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    dialog = shown[0]
    assert dialog.windowTitle() == "The run can't start"
    assert "Cannot connect to Ollama" in issue_text(dialog.issues[0])
    assert [a.label for a in dialog.fix_actions] == ["Find server…"]  # "Check again" is no button
    assert main_window._run_controller._worker is None
    assert main_window._run_btn.isEnabled() is True


@pytest.mark.gui
def test_run_checks_requirements_off_the_gui_thread(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a requirement check that blocks until released
    When   Run is pressed
    Then   _on_run returns immediately (the GUI thread is free) and the run only
           continues after the check finished
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    main_window._results_table.set_queue([queue_row("/tmp/a.png", "a.png")])
    main_window._results_table.selectRow(0)
    release = threading.Event()

    def _slow_preflight(self, config, **_kw):
        assert release.wait(timeout=5), "test never released the check"
        return RunPreflight()

    monkeypatch.setattr(RunService, "check_run_preflight", _slow_preflight)

    main_window._run_start.start()

    assert main_window._run_controller._worker is None  # still checking
    assert main_window._run_btn.isEnabled() is False
    release.set()
    qtbot.waitUntil(lambda: main_window._run_controller._worker is not None, timeout=3000)


@pytest.mark.gui
def test_run_shows_unmet_stage_requirement_dialog(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a stage whose requirements are not met
    When   Run is pressed
    Then   the dependency guard dialog is shown and no worker starts
    """
    from teachers_teammate.gui._dependency_guard_dialog import (  # noqa: PLC0415
        DependencyGuardDialog,
    )

    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(
            stage="correction",
            stage_issues=(RequirementIssue(IssueKind.MODEL_MISSING),),
        ),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        DependencyGuardDialog, "exec", lambda self: shown.append(self.windowTitle())
    )

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert shown == ["The run can't start"]
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_insufficient_memory_runs_anyway_only_when_the_user_says_so(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run whose models do not fit the memory that is free now
    When   the user presses "Run anyway" in the warning
    Then   the run continues (a worker starts) — the warning is not a refusal
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(memory_warning="qwen3.5:9b needs about 7 GB."),
    )
    asked = _answer_questions(monkeypatch, yes=True)
    main_window._results_table.set_queue([queue_row(str(tmp_path / "a.png"), "a.png")])
    main_window._results_table.selectRow(0)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: main_window._run_controller._worker is not None, timeout=3000)

    assert asked[0].title == "Not enough free memory"
    assert asked[0].body == "qwen3.5:9b needs about 7 GB."
    assert asked[0].choices[0].label == "Run anyway"
    # Enter must not start a run that could swap: the default is the platform's Cancel.
    assert asked[0].choices[1].standard is StandardButton.CANCEL
    assert asked[0].choices[1].default is True


@pytest.mark.gui
def test_cancelling_the_memory_warning_does_not_start_the_run(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  the same memory warning
    When   the user presses "Cancel" (the default button)
    Then   no run starts and the Run button is usable again
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(memory_warning="qwen3.5:9b needs about 7 GB."),
    )
    asked = _answer_questions(monkeypatch, yes=False)
    main_window._results_table.set_queue([queue_row(str(tmp_path / "a.png"), "a.png")])
    main_window._results_table.selectRow(0)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(asked), timeout=3000)
    assert main_window._run_controller._worker is None
    assert main_window._run_btn.isEnabled() is True


# ── Where the documents are processed ────────────────────────────────────────


def _online_proofreading_config(tmp_path: Path):
    return make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="openai",
    )


class _Confirmations:
    """Stand-in for the data-flow question: records the body, answers with a chosen button.

    ``clicks`` is the label of the button to press; ``"Cancel"`` stands for the platform's
    Cancel button, which carries no label of its own.
    """

    clicks = "Continue"
    remember = False
    texts: ClassVar[list[str]] = []


@pytest.fixture
def confirm_box(monkeypatch):
    """Replace the data-flow question and the settings store; return the recorder."""
    _Confirmations.clicks, _Confirmations.remember, _Confirmations.texts = "Continue", False, []
    SharedMemorySettings.store = {}
    monkeypatch.setattr(RunStartController, "_confirm_data_flow_then", _REAL_CONFIRM_DATA_FLOW)

    def choose(_parent: object, question: Question) -> Answer:
        _Confirmations.texts.append(question.body)
        if _Confirmations.clicks == "Cancel":
            return Answer(None, _Confirmations.remember)
        return Answer(question.choices[0], _Confirmations.remember)

    monkeypatch.setattr(_dialogs, "choose", choose)
    monkeypatch.setattr(main_window_module, "app_settings", SharedMemorySettings)
    return _Confirmations


@pytest.mark.gui
def test_run_refuses_online_ocr_with_an_explanation(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a preflight that refuses text recognition (e.g. an Ollama cloud model)
    When   Run is pressed
    Then   the reason is shown with a way to choose another model, no worker starts and Run
           is usable again
    """
    from teachers_teammate.gui._dependency_guard_dialog import (  # noqa: PLC0415
        DependencyGuardDialog,
    )

    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(ocr_not_allowed="Runs on Ollama's servers."),
    )
    shown: list[DependencyGuardDialog] = []
    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: shown.append(self))

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert "Runs on Ollama's servers." in issue_text(shown[0].issues[0])
    assert [a.label for a in shown[0].fix_actions] == ["Choose a model…"]
    assert main_window._run_controller._worker is None
    assert main_window._run_btn.isEnabled() is True


@pytest.mark.gui
def test_run_with_text_files_in_the_input_folder_is_refused(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  text files switched on with the output folder being the input folder
    When   Run is pressed
    Then   the reason and where to change it are shown, no check or worker starts, and
           Run stays usable
    """
    cfg = make_config(tmp_path, output_dir=tmp_path / "input", text_enabled=True)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    preflight: list[object] = []
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config: preflight.append(config) or RunPreflight(),
    )
    shown = record_notices(monkeypatch)

    main_window._run_start.start()

    assert shown[0].title == "Choose another output folder"
    assert "next run would read them as new documents" in shown[0].body
    assert "Results" in shown[0].body
    assert preflight == []
    assert main_window._run_controller._worker is None
    assert main_window._run_btn.isEnabled() is True


@pytest.mark.gui
def test_local_run_starts_without_a_privacy_question(
    qtbot, main_window, confirm_box, tmp_path: Path
) -> None:
    """
    Given  a run where everything stays on this computer
    When   the data flow is confirmed
    Then   the run continues straight away and nothing is asked
    """
    started: list[int] = []

    main_window._run_start._confirm_data_flow_then(make_config(tmp_path), lambda: started.append(1))

    assert started == [1]
    assert confirm_box.texts == []


@pytest.mark.gui
def test_online_run_asks_first_and_cancel_stops_it(
    qtbot, main_window, confirm_box, tmp_path: Path
) -> None:
    """
    Given  proofreading on an online service
    When   the user is asked and presses Cancel
    Then   the question names the step and says names are as written, and the run does not
           start
    """
    confirm_box.clicks = "Cancel"
    started: list[int] = []

    main_window._run_start._confirm_data_flow_then(
        _online_proofreading_config(tmp_path), lambda: started.append(1)
    )

    assert started == []
    assert "Proofreading" in confirm_box.texts[0]
    assert "Online service (OpenAI (ChatGPT))" in confirm_box.texts[0]
    assert "names as written" in confirm_box.texts[0]
    assert "Remove personal info" in confirm_box.texts[0]


@pytest.mark.gui
def test_online_run_continues_and_is_asked_again_by_default(
    qtbot, main_window, confirm_box, tmp_path: Path
) -> None:
    """
    Given  an online run the user confirms without ticking "don't ask again"
    When   the same run is started twice
    Then   it continues both times and is asked both times
    """
    started: list[int] = []
    cfg = _online_proofreading_config(tmp_path)

    main_window._run_start._confirm_data_flow_then(cfg, lambda: started.append(1))
    main_window._run_start._confirm_data_flow_then(cfg, lambda: started.append(1))

    assert started == [1, 1]
    assert len(confirm_box.texts) == 2


@pytest.mark.gui
def test_dont_ask_again_applies_only_to_the_same_data_flow(
    qtbot, main_window, confirm_box, tmp_path: Path
) -> None:
    """
    Given  an online run confirmed with "don't ask again"
    When   the same run starts again, then a run that also reviews online
    Then   the first is not asked, the changed one is
    """
    confirm_box.remember = True
    started: list[int] = []
    cfg = _online_proofreading_config(tmp_path)

    main_window._run_start._confirm_data_flow_then(cfg, lambda: started.append(1))
    asked_before = len(confirm_box.texts)
    main_window._run_start._confirm_data_flow_then(cfg, lambda: started.append(2))
    main_window._run_start._confirm_data_flow_then(
        dataclasses.replace(cfg, evaluation_enabled=True, evaluate_provider="openai"),
        lambda: started.append(3),
    )

    assert started == [1, 2, 3]
    assert asked_before == 1
    assert len(confirm_box.texts) == 2


@pytest.mark.gui
def test_taking_back_the_answer_in_privacy_settings_asks_again(
    qtbot, main_window, confirm_box, tmp_path: Path, monkeypatch
) -> None:
    """
    Given  an online run confirmed with "don't ask again"
    When   the user opens Settings → Privacy, presses "Ask again for all" and OK
    Then   the page opens on the sending tab, and the same run is asked about again
    """
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    from teachers_teammate.gui.settings import SettingsWindow  # noqa: PLC0415
    from teachers_teammate.gui.settings._page import FOCUS_SENDING  # noqa: PLC0415

    confirm_box.remember = True
    cfg = _online_proofreading_config(tmp_path)
    main_window._run_start._confirm_data_flow_then(cfg, lambda: None)
    main_window._run_start._confirm_data_flow_then(cfg, lambda: None)
    assert len(confirm_box.texts) == 1

    opened_on: list[str] = []

    def fake_exec(window: SettingsWindow) -> QDialog.DialogCode:
        page = window.page(SettingsPage.PRIVACY)
        tabs = page.rules().tabs()
        opened_on.append(tabs.tabText(tabs.currentIndex()))
        page.sending_tab()._forget_all_btn.click()
        window.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsWindow, "exec", fake_exec)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    with patch(
        "teachers_teammate.gui._anonymizer_config_widget.AppServices",
        MagicMock(),
    ) as service:
        service.return_value.list_installed_spacy_models.return_value = []
        main_window.open_settings(SettingsPage.PRIVACY, focus=FOCUS_SENDING)
    main_window._run_start._confirm_data_flow_then(cfg, lambda: None)

    assert opened_on == ["Sending data away"]
    assert len(confirm_box.texts) == 2


@pytest.mark.gui
def test_a_remembered_answer_is_kept_with_a_readable_summary(
    qtbot, main_window, confirm_box, tmp_path: Path
) -> None:
    """
    Given  an online run confirmed with "don't ask again"
    When   the remembered answers are read
    Then   the answer carries a sentence naming the step and the service, which Privacy Settings
           lists
    """
    confirm_box.remember = True

    main_window._run_start._confirm_data_flow_then(
        _online_proofreading_config(tmp_path), lambda: None
    )

    [entry] = main_window._acknowledgements().remembered()
    assert entry.summary.startswith("Proofreading: Online service (OpenAI")
    assert "names sent as written" in entry.summary


@pytest.mark.gui
def test_the_settings_menu_has_one_privacy_entry(qtbot, main_window) -> None:
    """
    Given  the main window
    When   the Settings menu is read
    Then   it has "Privacy…" and neither the old "Anonymizer Settings…" nor the separate
           "Ask Before Sending Data Away Again" entry
    """
    entries = [a.text() for a in _menu(main_window, "Settings").actions()]

    assert "Privacy\u2026" in entries
    assert "Anonymizer Settings\u2026" not in entries
    assert "Ask Before Sending Data Away Again" not in entries


@pytest.mark.gui
def test_online_ocr_engine_in_saved_settings_is_announced(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a settings file from an older version that names an online OCR engine
    When   the window loads it
    Then   the teacher is told the engine was replaced, instead of it changing silently
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text('ocr_engine = "openai"\nocr_model = "gpt-4o"\n', encoding="utf-8")
    shown = record_notices(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, _: config_path)

    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)

    assert shown and shown[0].title == "Text recognition changed"
    assert "openai" in shown[0].body
    assert "ollama" in shown[0].body


@pytest.mark.gui
@pytest.mark.parametrize(("old", "name"), [("tesseract", "Tesseract"), ("paddleocr", "PaddleOCR")])
def test_a_removed_local_engine_in_saved_settings_is_announced(
    qtbot, monkeypatch, tmp_path: Path, old: str, name: str
) -> None:
    """
    Given  a settings file from an older version that names Tesseract or PaddleOCR
    When   the window loads it
    Then   the teacher is told, once, that the built-in reader replaced it and where to
           download it (31 MB), and the panel uses the reader
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text(f'ocr_engine = "{old}"\n', encoding="utf-8")
    shown = record_notices(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, _: config_path)

    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)

    assert [notice.title for notice in shown] == ["Text recognition changed"]
    text = shown[0].body
    assert f"used {name} for text recognition" in text
    assert "built-in reader" in text and "31 MB" in text
    assert "Settings → Add-ons → Reading models" in text
    assert win._config_panel._settings_dict["ocr_engine"] == "ppocr"


@pytest.mark.gui
def test_local_ocr_engine_in_saved_settings_is_not_announced(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a settings file that names a local OCR engine
    When   the window loads it
    Then   no notice is shown
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text('ocr_engine = "ppocr"\n', encoding="utf-8")
    shown = record_notices(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, _: config_path)

    qtbot.addWidget(MainWindow(app_service=real_service()))

    assert "Text recognition changed" not in [notice.title for notice in shown]


@pytest.mark.gui
def test_an_unusable_saved_value_is_logged_not_shown_as_a_dialog(
    qtbot, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a settings file with a usable setting and a value its key cannot take (a text for
           a switch)
    When   the window loads it
    Then   the log says which setting was not used, in a sentence, and no dialog interrupts
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text('language = "German"\ndebug = "false"\n', encoding="utf-8")
    shown = record_notices(monkeypatch)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(SetupService, "resolve_config_path", lambda self, _: config_path)

    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)

    assert shown == []
    log = win._log.toPlainText()
    assert "The saved setting “debug” has a value the app cannot use (false)" in log
    assert "Traceback" not in log and "ValueError" not in log


# ── Run / re-run guards ──────────────────────────────────────────────────────


@pytest.mark.gui
def test_stage_rerun_with_unmet_requirement_keeps_the_cached_results(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  Ollama is not running
    When   "Re-run Proofreading" is chosen from the queue context menu
    Then   the dialog that says the run can't start is shown and the cache is left untouched
    """
    from teachers_teammate.gui._dependency_guard_dialog import (  # noqa: PLC0415
        DependencyGuardDialog,
    )

    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        RunService,
        "check_run_preflight",
        lambda self, config, **_kw: RunPreflight(ollama_unreachable="✗ Cannot connect to Ollama"),
    )
    invalidated: list = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *a, **k: invalidated.append(a) or (),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        DependencyGuardDialog, "exec", lambda self: shown.append(self.windowTitle())
    )

    main_window._run_start.start_stage(["/tmp/a.png"], "correction")
    _wait_for(lambda: bool(shown))

    assert shown == ["The run can't start"]
    assert invalidated == []
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_run_pressed_while_requirements_are_checked_starts_nothing_twice(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a requirement check that is still in flight
    When   a right-click re-run is requested and Run is pressed again
    Then   both are refused, and only the first run starts a worker once the check ended
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    main_window._results_table.set_queue([queue_row("/tmp/a.png", "a.png")])
    main_window._results_table.selectRow(0)
    release = threading.Event()
    checks: list[int] = []

    def _slow_preflight(self, config, **_kw):
        checks.append(1)
        assert release.wait(timeout=5), "test never released the check"
        return RunPreflight()

    monkeypatch.setattr(RunService, "check_run_preflight", _slow_preflight)
    busy = record_notices(monkeypatch)

    main_window._run_start.start()
    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")
    main_window._run_start.start()

    assert [notice.title for notice in busy] == ["Please wait"]
    release.set()
    qtbot.waitUntil(lambda: main_window._run_controller._worker is not None, timeout=3000)
    first_worker = main_window._run_controller._worker
    qtbot.wait(100)
    assert main_window._run_controller._worker is first_worker
    assert len(checks) == 1


@pytest.mark.gui
def test_stage_rerun_is_refused_while_a_worker_runs(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a worker that is running
    When   a right-click re-run is requested
    Then   the user is told to wait; no check runs and the cache is untouched
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    worker = DummyWorker(cfg)
    worker.start()
    main_window._run_controller._worker = worker
    invalidated: list = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *a, **k: invalidated.append(a) or (),
    )
    busy = record_notices(monkeypatch)

    main_window._run_start.start_stage(["/tmp/a.png"], "ocr")

    assert [notice.title for notice in busy] == ["Please wait"]
    assert invalidated == []
    assert main_window._run_start.preflight_running is False


# ── Export menu ───────────────────────────────────────────────────────────


def _queue_with_text(window: MainWindow, folder: Path, texts: dict[str, str]) -> list[str]:
    """Queue one row per *texts* key (a file name in *folder*) holding that extracted text.

    Each row gets a real stored document, because every writer reads the texts from the store
    rather than from the row.
    """
    folder.mkdir(parents=True, exist_ok=True)
    cfg = make_config(folder.parent)
    window._config_panel.to_config = lambda: cfg  # ty: ignore[invalid-assignment]
    ids = [str(folder / name) for name in texts]
    rows: list[QueueRow] = []
    for source_id, name, text in zip(ids, texts, texts.values(), strict=True):
        source = Path(source_id)
        # A test may have written the file already; its content is what the work is keyed by.
        if not source.exists():
            source.write_text(f"page of {source.name}", encoding="utf-8")
        if text:
            store_document(cfg, source, raw=text)
        rows.append(
            queue_row(
                source_id,
                name,
                ocr=StageState.DONE if text else StageState.NONE,
                has_text=bool(text),
            )
        )
    window._results_table.set_queue(rows)
    return ids


def _pick_folder(monkeypatch, folder: Path) -> None:
    monkeypatch.setattr(_dialogs, "pick_folder", lambda *_a, **_k: folder)


def _pick_file(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(_dialogs, "save_file", lambda *_a, **_k: path)


@pytest.mark.gui
def test_export_button_offers_word_text_and_anonymized_text(main_window) -> None:
    """
    Given  the main window
    When   the Export button's menu is inspected
    Then   the button is called Export and offers Word, text and anonymized text
    """
    menu = main_window._export_btn.menu()

    assert main_window._export_btn.text() == "Export"
    assert [a.text() for a in menu.actions()] == [
        "Word Document…",
        "Text File…",
        "Anonymized Text File…",
    ]


@pytest.mark.gui
def test_export_text_writes_one_file_per_row_and_skips_rows_without_text(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two rows with extracted text and one without
    When   the text export runs into a chosen folder
    Then   a .txt file per row with text is written and the empty row is reported
    """
    out = tmp_path / "out"
    ids = _queue_with_text(
        main_window, tmp_path / "scans", {"a.png": "alpha", "b.png": "beta", "c.png": ""}
    )
    _pick_folder(monkeypatch, out)
    warnings = record_notices(monkeypatch)

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._export_controller.running and bool(warnings))

    assert (out / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (out / "b.txt").read_text(encoding="utf-8") == "beta"
    assert not (out / "c.txt").exists()
    assert "c.png" in warnings[0].body


@pytest.mark.gui
def test_export_to_a_folder_opens_the_picker_in_the_output_folder(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two rows and a results folder from the last run
    When   the text export asks for a folder
    Then   the folder picker starts in the results folder, as the Save As dialog does
    """
    results = tmp_path / "results"
    monkeypatch.setattr(main_window._results_table, "get_output_dir", lambda: results)
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "alpha", "b.png": "beta"})
    prompts = []
    monkeypatch.setattr(_dialogs, "pick_folder", lambda _parent, prompt: prompts.append(prompt))

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)

    assert [prompt.start for prompt in prompts] == [str(results)]


@pytest.mark.gui
def test_export_of_one_file_proposes_a_name_in_the_output_folder(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  one row and a results folder from the last run
    When   the text export asks where to save
    Then   the Save As dialog proposes <name>.txt in the results folder
    """
    results = tmp_path / "results"
    monkeypatch.setattr(main_window._results_table, "get_output_dir", lambda: results)
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "alpha"})
    prompts = []
    monkeypatch.setattr(_dialogs, "save_file", lambda _parent, prompt: prompts.append(prompt))

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)

    assert [prompt.start for prompt in prompts] == [str(results / "a.txt")]


@pytest.mark.gui
def test_export_anonymized_text_writes_the_anonymized_file(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a row with extracted text and an anonymizer that hides "Anna"
    When   the anonymized text export runs
    Then   <stem>_anonymized.txt holds the anonymized text
    """
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    out = tmp_path / "out"
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "Dear Anna"})
    _pick_file(monkeypatch, out / "a_anonymized.txt")
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("Dear [PERSON_1]", {"[PERSON_1]": "Anna"})
    warnings = record_notices(monkeypatch)

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        main_window._export_controller.on_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: not main_window._export_controller.running)

    assert warnings == []
    assert (out / "a_anonymized.txt").read_text(encoding="utf-8") == "Dear [PERSON_1]"


@pytest.mark.gui
def test_export_anonymized_text_explains_a_missing_spacy_once(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two rows and no spaCy installed
    When   the anonymized text export runs
    Then   one message explains spaCy is missing and no file is written
    """
    from unittest.mock import patch  # noqa: PLC0415

    out = tmp_path / "out"
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "x", "b.png": "y"})
    _pick_folder(monkeypatch, out)
    warnings = record_notices(monkeypatch)

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
        side_effect=ImportError("No module named 'spacy'"),
    ):
        main_window._export_controller.on_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: bool(warnings))

    assert len(warnings) == 1
    assert "spaCy" in warnings[0].body
    assert not out.exists()


@pytest.mark.gui
def test_export_text_into_the_input_folder_can_be_declined(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  an input folder, and a text export aimed at that same folder
    When   the user answers "No" to the warning that the files would be read as inputs
    Then   nothing is written
    """
    scans = tmp_path / "scans"
    scans.mkdir()
    ids = _queue_with_text(main_window, scans, {"a.png": "alpha"})
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(scans))
    _pick_file(monkeypatch, scans / "a.txt")
    asked = _answer_questions(monkeypatch, yes=False)

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)

    assert [question.title for question in asked] == ["Save into the input folder?"]
    assert not (scans / "a.txt").exists()
    assert not main_window._export_controller.running


def _capture_warnings(monkeypatch) -> list[Notice]:
    return record_notices(monkeypatch)


@pytest.mark.gui
def test_export_toolbar_exports_the_selected_rows_else_all_rows(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two queued rows
    When   an Export menu entry is chosen with nothing selected, then with one row selected
    Then   the first export covers both rows and the second only the selected one
    """
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "x", "b.png": "y"})
    calls: list[tuple[list[str], ExportKind]] = []
    monkeypatch.setattr(
        main_window._export_controller,
        "on_requested",
        lambda source_ids, kind: calls.append((source_ids, kind)),
    )

    main_window._export_controller.on_toolbar_action(ExportKind.TEXT)
    main_window._results_table.selectRow(1)
    main_window._export_controller.on_toolbar_action(ExportKind.DOCX)

    assert calls == [(ids, ExportKind.TEXT), ([ids[1]], ExportKind.DOCX)]


@pytest.mark.gui
def test_export_toolbar_menu_entry_starts_that_export(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a queued row
    When   the "Anonymized Text File…" entry of the Export button's menu is triggered
    Then   an anonymized text export is requested for that row
    """
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "x"})
    calls: list[tuple[list[str], ExportKind]] = []
    monkeypatch.setattr(
        main_window._export_controller,
        "on_requested",
        lambda source_ids, kind: calls.append((source_ids, kind)),
    )

    main_window._export_btn.menu().actions()[2].trigger()

    assert calls == [(ids, ExportKind.TEXT_ANONYMIZED)]


@pytest.mark.gui
@pytest.mark.parametrize("kind", [ExportKind.DOCX, ExportKind.TEXT])
def test_export_skips_documents_that_would_share_an_output_file(
    main_window, monkeypatch, tmp_path: Path, kind: ExportKind
) -> None:
    """
    Given  x.png and x.pdf, which both map to the same output name in one folder
    When   they are exported together
    Then   only the first is exported and the second is reported instead of overwriting it
    """
    scans, out = tmp_path / "scans", tmp_path / "out"
    ids = _queue_with_text(main_window, scans, {"x.png": "alpha", "x.pdf": "beta"})
    _pick_folder(monkeypatch, out)
    warnings = _capture_warnings(monkeypatch)
    written: list[Path] = []
    service = main_window._app_service
    monkeypatch.setattr(
        service.export,
        "export_docx",
        lambda _config, _source, path, **_k: written.append(path),
    )
    monkeypatch.setattr(
        service.export,
        "export_text",
        lambda _config, _source, path, **_k: written.append(path),
    )

    main_window._export_controller.on_requested(ids, kind)
    _wait_for(lambda: bool(warnings))

    assert written == [out / ("x" + kind.file_suffix)]
    assert "x.pdf" in warnings[0].body
    assert "x.png" not in warnings[0].body


@pytest.mark.gui
@pytest.mark.parametrize(("answer", "expected"), [(False, "old"), (True, "alpha")])
def test_export_into_a_folder_asks_before_replacing_existing_files(
    main_window, monkeypatch, tmp_path: Path, answer: bool, expected: str
) -> None:
    """
    Given  a.txt already exists in the chosen folder and two rows are exported there
    When   the user is asked whether to replace it
    Then   "No" keeps the old file and exports nothing, "Yes" replaces it
    """
    out = tmp_path / "out"
    out.mkdir()
    (out / "a.txt").write_text("old", encoding="utf-8")
    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "alpha", "b.png": "beta"})
    _pick_folder(monkeypatch, out)
    asked = _answer_questions(monkeypatch, yes=answer)

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._export_controller.running)

    assert [question.title for question in asked] == ["Replace existing files?"]
    assert (out / "a.txt").read_text(encoding="utf-8") == expected
    assert (out / "b.txt").exists() is (expected == "alpha")


@pytest.mark.gui
def test_replace_question_names_files_by_their_subfolder(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two same-named exports in different subfolders of the chosen folder already exist
    When   the user is asked whether to replace them
    Then   the question tells them apart by subfolder instead of listing "a.txt" twice
    """
    out = tmp_path / "out"
    for name in ("class1", "class2"):
        (out / name).mkdir(parents=True)
        (out / name / "a.txt").write_text("old", encoding="utf-8")
    targets = [("s1", out / "class1" / "a.txt"), ("s2", out / "class2" / "a.txt")]
    asked = _answer_questions(monkeypatch, yes=False)

    assert main_window._export_controller._confirm_replace_existing(targets) is False

    assert "class1/a.txt" in asked[0].body
    assert "class2/a.txt" in asked[0].body


@pytest.mark.gui
def test_export_text_never_replaces_another_input_document(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a.png and a.txt are both inputs, and a.txt holds the teacher's original text
    When   a.png is exported as a text file named a.txt
    Then   a.txt keeps its content and the export reports it as an input document
    """
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "a.txt").write_text("original", encoding="utf-8")
    ids = _queue_with_text(main_window, scans, {"a.png": "ocr result", "a.txt": "original"})
    _pick_file(monkeypatch, scans / "a.txt")
    warnings = _capture_warnings(monkeypatch)

    main_window._export_controller.on_requested([ids[0]], ExportKind.TEXT)
    _wait_for(lambda: bool(warnings))

    assert (scans / "a.txt").read_text(encoding="utf-8") == "original"
    assert "a.png" in warnings[0].body
    assert "input document" in warnings[0].body


@pytest.mark.gui
def test_export_text_into_the_input_folder_writes_after_confirming(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  an input folder, and a text export aimed at a subfolder of it
    When   the user answers "Yes" to the warning that the files would be read as inputs
    Then   the file is written
    """
    scans = tmp_path / "scans"
    ids = _queue_with_text(main_window, scans, {"a.png": "alpha"})
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(scans))
    _pick_file(monkeypatch, scans / "sub" / "a.txt")
    asked = _answer_questions(monkeypatch, yes=True)

    main_window._export_controller.on_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._export_controller.running)

    assert [question.title for question in asked] == ["Save into the input folder?"]
    assert (scans / "sub" / "a.txt").read_text(encoding="utf-8") == "alpha"


@pytest.mark.gui
@pytest.mark.parametrize("folder_mode", [False, True])
def test_input_folder_warning_only_applies_to_folder_and_text_exports(
    main_window, monkeypatch, tmp_path: Path, folder_mode: bool
) -> None:
    """
    Given  an export into the input folder, either as an explicit file selection or a Word export
    When   it is started
    Then   no input-folder warning is shown, since a run would not read the new file
    """
    scans = tmp_path / "scans"
    scans.mkdir()
    ids = _queue_with_text(main_window, scans, {"a.png": "alpha"})
    panel = main_window._config_panel
    monkeypatch.setattr(panel, "get_input_dir", lambda: str(scans))
    monkeypatch.setattr(
        panel, "get_selected_files", lambda: None if folder_mode else [scans / "a.png"]
    )
    monkeypatch.setattr(main_window._app_service.export, "export_docx", lambda *_a, **_k: None)
    asked = _answer_questions(monkeypatch, yes=False)
    kind = ExportKind.DOCX if folder_mode else ExportKind.TEXT
    _pick_file(monkeypatch, scans / ("a" + kind.file_suffix))

    main_window._export_controller.on_requested(ids, kind)
    _wait_for(lambda: not main_window._export_controller.running)

    assert asked == []


@pytest.mark.gui
def test_export_anonymized_text_with_missing_spacy_also_lists_skipped_rows(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  one row with text, one without, and no spaCy installed
    When   the anonymized text export runs
    Then   the single message explains the missing spaCy and also lists the skipped row
    """
    from unittest.mock import patch  # noqa: PLC0415

    ids = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "x", "c.png": ""})
    _pick_folder(monkeypatch, tmp_path / "out")
    warnings = _capture_warnings(monkeypatch)

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
        side_effect=ImportError("No module named 'spacy'"),
    ):
        main_window._export_controller.on_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: bool(warnings))

    assert len(warnings) == 1
    assert "spaCy" in warnings[0].body
    assert "c.png" in warnings[0].body


@pytest.mark.gui
@pytest.mark.parametrize(
    ("explanation_language", "text_language", "expected"),
    [("", "German", "German"), ("French", "German", "French")],
)
def test_exported_docx_labels_follow_the_explanation_language(
    main_window,
    monkeypatch,
    tmp_path: Path,
    explanation_language: str,
    text_language: str,
    expected: str,
) -> None:
    """
    Given  a German text and an explanation language that is empty ("Same as the text") or French
    When   the document is exported as a DOCX
    Then   the headings language is the text's language respectively the explanation language
    """
    sid = _queue_with_text(main_window, tmp_path / "scans", {"a.png": "text"})[0]
    main_window._config_panel._language.setCurrentText(text_language)
    main_window._config_panel.update_settings({"explanation_language": explanation_language})
    _pick_file(monkeypatch, tmp_path / "out" / "a.docx")
    seen: list[str] = []
    monkeypatch.setattr(
        main_window._app_service.export,
        "export_docx",
        lambda _config, _source, _path, options=None, **_k: seen.append(options.labels_language),
    )

    main_window._export_controller.on_requested([sid], ExportKind.DOCX)
    _wait_for(lambda: bool(seen))

    assert seen == [expected]


@pytest.mark.gui
def test_a_pull_requested_while_one_runs_is_queued_not_dropped(qtbot, monkeypatch) -> None:
    """
    Given  a pull that is still running (for example one that outlived the Settings window)
    When   more models are requested, one of them twice and one being the running model
    Then   they wait in the queue once each and are pulled after the running one, instead of
           the request being ignored
    """
    threads = fake_pull_threads(monkeypatch)
    manager = main_window_module.PullManager(service=real_service())
    manager.start("http://host:11434", "a:1b")

    manager.start("http://host:11434", "a:1b", ["b:1b"])
    manager.start("http://host:11434", "b:1b", ["c:1b"])

    assert [t.model for t in threads] == ["a:1b"]
    assert manager.queued == ["b:1b", "c:1b"]
    threads[0].finished_ok.emit("a:1b")
    assert [t.model for t in threads] == ["a:1b", "b:1b"]


def _detection() -> HardwareDetection:
    """Return a detection of an 8 GB card, as a finished probe would."""
    return HardwareDetection(
        profile=HardwareProfile.CARD_8GB,
        reason=DetectionReason.GPU_DETECTED,
        gpu_name="Test GPU",
        memory_gb=8.0,
    )


@pytest.mark.gui
def test_main_window_probes_this_computer_in_the_background_for_the_cache_keys(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a main window whose hardware has not been probed yet
    When   the queue is refreshed
    Then   the probe runs on a background thread, never the GUI thread, and the queue is
           shown again with the keys of the detected computer once it has finished
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    service = main_window._app_service
    loads: list[int] = []
    monkeypatch.setattr(service.documents, "queue_rows", lambda *_a, **_k: loads.append(1) or [])
    probed = False
    detect_threads: list[threading.Thread] = []

    def cached(*_args: object, **_kwargs: object) -> HardwareDetection | None:
        return _detection() if probed else None

    def detect(*_args: object, **_kwargs: object) -> HardwareDetection:
        nonlocal probed
        detect_threads.append(threading.current_thread())
        probed = True
        return _detection()

    monkeypatch.setattr(service.recommendations, "cached_hardware_profile", cached)
    monkeypatch.setattr(service.recommendations, "detect_hardware", detect)
    before = len(loads)

    main_window._refresh_queue_from_inputs()

    qtbot.waitUntil(lambda: len(loads) >= before + 2, timeout=3000)
    assert len(detect_threads) == 1
    assert detect_threads[0] is not threading.main_thread()


@pytest.mark.gui
def test_main_window_does_not_probe_again_and_again_when_the_probe_fails(
    qtbot, main_window, monkeypatch, stored
) -> None:
    """
    Given  a computer whose probe fails every time
    When   the queue is refreshed and a document with text is opened
    Then   a failed probe is not retried by itself, so the calls stop instead of looping
    """
    [source_id] = stored("page.png", raw="raw text")
    service = main_window._app_service
    calls: list[int] = []

    def failing(*_args: object, **_kwargs: object) -> HardwareDetection:
        calls.append(1)
        raise RuntimeError("nvidia-smi crashed")

    monkeypatch.setattr(service.recommendations, "cached_hardware_profile", lambda *_a, **_k: None)
    monkeypatch.setattr(service.recommendations, "detect_hardware", failing)

    main_window._refresh_queue_from_inputs()
    main_window._results_table.set_queue(
        [queue_row(source_id, "page.png", ocr=StageState.DONE, has_text=True)]
    )
    main_window._results_table.selectRow(0)
    main_window._check_controller.load(source_id)
    qtbot.wait(600)
    settled = len(calls)
    qtbot.wait(600)

    assert settled == 1
    assert len(calls) == settled


# ── Readiness line ───────────────────────────────────────────────────────────


@pytest.fixture
def ready_window(qtbot, monkeypatch) -> MainWindow:
    """A MainWindow whose readiness check is the real one (the shared fixture silences it)."""
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    no_update_check(monkeypatch)
    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)
    return win


def _preflight_returns(monkeypatch, result: RunPreflight) -> list[dict]:
    """Make every preflight answer *result*; return the keyword arguments of each call."""
    calls: list[dict] = []

    def answer(self, config, **kwargs):
        calls.append(kwargs)
        return result

    monkeypatch.setattr(RunService, "check_run_preflight", answer)
    return calls


_MISSING_KEY = RunPreflight(
    stage="correction",
    stage_issues=(RequirementIssue(IssueKind.KEY_MISSING, provider="mistral_eu"),),
)


def _line(window: MainWindow) -> str:
    return window._readiness_line.label.text()


def _buttons(window: MainWindow) -> list[str]:
    return [b.text() for b in window._readiness_line._buttons if not b.isHidden()]


@pytest.mark.gui
def test_the_line_names_a_missing_key_and_its_button_opens_the_key_row(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  proofreading at an online service whose key is not set
    When   the readiness check answers
    Then   the line says so, asks for no memory check, and its "Enter key…" button opens AI
           models on the proofreading key row
    """
    calls = _preflight_returns(monkeypatch, _MISSING_KEY)
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: "No API key for Mistral (EU) yet" in _line(ready_window), timeout=3000)

    assert _buttons(ready_window) == ["Enter key…"]
    assert calls[-1] == {
        "memory": False,
        "recognition": True,
        "fresh": False,
        "files": [],
        "recognised": set(),
    }
    ready_window._readiness_line._buttons[0].click()
    window = scripted.instances[0]
    assert (window.page, window.focus) == (SettingsPage.AI_MODELS, FOCUS_PROOFREADING_KEY)


@pytest.mark.gui
def test_the_readiness_check_reads_the_queue_on_the_gui_thread(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  a queue in the table and a readiness check whose answer comes from a background task
    When   the check is started
    Then   the table is only read on the GUI thread, and the task gets what was read there
    """
    gui_thread = threading.get_ident()
    readers: set[int] = set()
    read_queue = ResultsTable.queued_source_ids

    def recording(self) -> list[str]:
        readers.add(threading.get_ident())
        return read_queue(self)

    monkeypatch.setattr(ResultsTable, "queued_source_ids", recording)
    calls = _preflight_returns(monkeypatch, RunPreflight())

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: bool(calls), timeout=3000)
    qtbot.wait(50)

    assert readers == {gui_thread}
    assert calls[-1]["recognition"] is True
    assert calls[-1]["files"] == []


@pytest.mark.gui
def test_the_line_offers_the_server_address_and_check_again_for_ollama(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  an Ollama that cannot be reached
    When   the readiness check answers, and "Check again" is pressed
    Then   both buttons show, and pressing "Check again" asks again at once
    """
    _preflight_returns(monkeypatch, RunPreflight(ollama_unreachable="✗ Cannot connect"))
    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: "Ollama isn't reachable" in _line(ready_window), timeout=3000)
    checks: list[int] = []
    monkeypatch.setattr(
        ready_window._readiness_controller, "start_check", lambda **_kw: checks.append(1)
    )

    assert _buttons(ready_window) == ["Check again", "Find server…"]
    ready_window._readiness_line._buttons[0].click()

    assert checks == [1]


@pytest.mark.gui
def test_the_line_asks_for_a_results_folder_and_choose_opens_the_dialog(
    qtbot, ready_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  "Word document" ticked while no results folder is chosen
    When   the line updates, and "Choose…" is pressed
    Then   the folder dialog opens, and the chosen folder lands in the Results step
    """
    from teachers_teammate.gui import _config_panel  # noqa: PLC0415

    panel = ready_window._config_panel
    panel._results._docx_enabled.setChecked(True)
    ready_window._readiness_controller.update()
    assert "Choose where to save the results" in _line(ready_window)
    assert _buttons(ready_window) == ["Choose…"]
    monkeypatch.setattr(_dialogs, "pick_folder", lambda *_a, **_k: tmp_path)

    ready_window._readiness_line._buttons[0].click()

    assert panel.get_output_dir() == str(tmp_path)
    assert "Choose where to save the results" not in _line(ready_window)


@pytest.mark.gui
def test_selecting_rows_updates_the_count_without_a_new_check(
    qtbot, ready_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  three documents in the queue and a first check that found nothing in the way
    When   rows are selected
    Then   the line counts them at once, and the progress bar stays "Idle"
    """
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).touch()
    checks: list[int] = []
    monkeypatch.setattr(
        ready_window._readiness_controller, "start_check", lambda **_kw: checks.append(1)
    )
    ready_window._readiness_controller._first_check_answered = True

    ready_window._config_panel.set_input_dir(str(tmp_path))
    assert _line(ready_window) == "Select the documents to run in the queue"
    ready_window._results_table.selectRow(0)
    assert _line(ready_window) == "✓ Ready · 1 of 3 documents"
    ready_window._results_table.selectAll()

    assert _line(ready_window) == "✓ Ready · 3 documents"
    assert checks == []
    assert ready_window._progress.format() == "Idle"


@pytest.mark.gui
def test_a_check_before_any_documents_are_chosen_says_what_to_do_first(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  a window without documents
    When   the first check finds nothing wrong
    Then   the line asks for documents
    """
    _preflight_returns(monkeypatch, RunPreflight())

    ready_window._readiness_controller.start_check()

    qtbot.waitUntil(lambda: _line(ready_window) == "Select documents to start", timeout=3000)


@pytest.mark.gui
def test_the_line_says_checking_only_when_the_first_answer_is_slow(
    qtbot, ready_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  documents in the queue and a first check that is slow
    When   the delay passes, and later the answer arrives
    Then   the line says "Checking…" meanwhile and the green line afterwards
    """
    release = threading.Event()

    def slow(self, config, **_kw):
        assert release.wait(timeout=5), "test never released the check"
        return RunPreflight()

    monkeypatch.setattr(RunService, "check_run_preflight", slow)
    panel = ready_window._config_panel
    panel._selection.set_folder(tmp_path)  # no scan of the folder: the queue is the queue below
    ready_window._results_table.set_queue([queue_row(str(tmp_path / "a.png"), "a.png")])
    ready_window._results_table.selectRow(0)

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: _line(ready_window) == "Checking…", timeout=3000)
    release.set()

    qtbot.waitUntil(lambda: _line(ready_window) == "✓ Ready · 1 document", timeout=3000)


@pytest.mark.gui
def test_a_stale_readiness_answer_is_dropped(qtbot, ready_window, monkeypatch) -> None:
    """
    Given  two checks in flight, the first of which reports a blocker and answers last
    When   both have answered
    Then   only the newer (clear) answer counts: no warning is shown
    """
    gates = [threading.Event(), threading.Event()]
    answers = [_MISSING_KEY, RunPreflight()]
    started = {"n": 0}
    lock = threading.Lock()

    def preflight(self, config, **_kw):
        with lock:
            index = started["n"]
            started["n"] += 1
        assert gates[index].wait(timeout=5), "test never released the check"
        return answers[index]

    monkeypatch.setattr(RunService, "check_run_preflight", preflight)
    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: started["n"] == 1, timeout=3000)
    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: started["n"] == 2, timeout=3000)

    gates[1].set()
    qtbot.wait(100)
    gates[0].set()
    qtbot.wait(200)

    assert ready_window._readiness_controller.blocker is None
    assert "No API key" not in _line(ready_window)


@pytest.mark.gui
def test_an_unreachable_ollama_is_checked_again_until_it_answers(ready_window) -> None:
    """
    Given  an answer saying Ollama cannot be reached, and later a clear one
    When   the answers arrive
    Then   a retry is scheduled while it is unreachable and cancelled afterwards; another
           kind of blocker schedules none
    """
    unreachable = RunPreflight(ollama_unreachable="✗ down")
    ready_window._readiness_controller.on_check_done(unreachable, None)
    assert ready_window._readiness_controller._ollama_retry.isActive() is True

    ready_window._readiness_controller.on_check_done(_MISSING_KEY, None)
    assert ready_window._readiness_controller._ollama_retry.isActive() is False
    ready_window._readiness_controller.on_check_done(unreachable, None)
    ready_window._readiness_controller.on_check_done(RunPreflight(), None)
    assert ready_window._readiness_controller._ollama_retry.isActive() is False


@pytest.mark.gui
@pytest.mark.parametrize(
    "kind",
    [IssueKind.OLLAMA_UNREACHABLE, IssueKind.OLLAMA_NOT_FOUND, IssueKind.OLLAMA_NOT_RUNNING],
)
def test_the_retry_runs_for_every_kind_that_means_ollama_is_not_answering(
    ready_window, kind: IssueKind
) -> None:
    """
    Given  a blocker saying Ollama cannot be reached, was not found, or is not running
    When   the answer arrives
    Then   a retry is scheduled in each case, so an Ollama installed or started meanwhile
           turns the line green without a click
    """
    result = RunPreflight(
        ollama_unreachable="✗ down", ollama_issue=RequirementIssue(kind, reason="✗ down")
    )

    ready_window._readiness_controller.on_check_done(result, None)

    assert ready_window._readiness_controller._ollama_retry.isActive() is True


@pytest.mark.gui
def test_get_ollama_opens_the_download_page(qtbot, ready_window, monkeypatch) -> None:
    """
    Given  a blocker saying Ollama wasn't found on this computer
    When   "Get Ollama…" is pressed
    Then   the download page is opened in the browser, and nothing is installed by the app
    """
    opened: list[str] = []
    monkeypatch.setattr(
        readiness_controller_module.QDesktopServices,
        "openUrl",
        lambda url: opened.append(url.toString()),
    )
    result = RunPreflight(
        ollama_unreachable="✗ down",
        ollama_issue=RequirementIssue(IssueKind.OLLAMA_NOT_FOUND),
    )
    ready_window._readiness_controller.on_check_done(result, None)

    ready_window._readiness_line._buttons[0].click()

    assert opened == [OLLAMA_DOWNLOAD_URL]


@pytest.mark.gui
def test_start_ollama_starts_it_in_the_background_and_checks_again(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  a blocker saying Ollama is installed but isn't running
    When   "Start Ollama" is pressed
    Then   the program is started off the GUI thread, and a fresh check follows a moment later
    """
    started: list[int] = []
    checks: list[dict] = []
    monkeypatch.setattr(ready_window._app_service.ollama, "start_ollama", lambda: started.append(1))
    monkeypatch.setattr(
        readiness_controller_module,
        "run_in_background",
        lambda fn, on_done, **_kw: (fn(), on_done(None, None)),
    )
    monkeypatch.setattr(readiness_controller_module.QTimer, "singleShot", lambda _ms, fn: fn())
    monkeypatch.setattr(
        ready_window._readiness_controller, "start_check", lambda **kw: checks.append(kw)
    )
    result = RunPreflight(
        ollama_unreachable="✗ down",
        ollama_issue=RequirementIssue(IssueKind.OLLAMA_NOT_RUNNING, startable=True),
    )
    ready_window._readiness_controller.on_check_done(result, None)

    ready_window._readiness_line._buttons[0].click()

    assert started == [1]
    assert checks == [{"fresh": True}]


@pytest.mark.gui
def test_activating_the_window_checks_again_only_while_something_blocks(
    ready_window, monkeypatch
) -> None:
    """
    Given  the window becoming active again
    When   nothing blocked the last check, and then when a requirement was missing
    Then   a new check starts only in the second case
    """
    from PySide6.QtCore import QEvent  # noqa: PLC0415

    checks: list[int] = []
    monkeypatch.setattr(
        ready_window._readiness_controller, "start_check", lambda **_kw: checks.append(1)
    )
    monkeypatch.setattr(ready_window, "isActiveWindow", lambda: True)
    event = QEvent(QEvent.Type.ActivationChange)

    ready_window._readiness_controller.blocker = None
    ready_window.changeEvent(event)
    assert checks == []

    ready_window._readiness_controller.on_check_done(_MISSING_KEY, None)
    ready_window.changeEvent(event)
    assert checks == [1]


@pytest.mark.gui
def test_check_again_asks_the_server_for_a_fresh_model_list(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  an Ollama that cannot be reached
    When   "Check again" is pressed
    Then   the new check asks for fresh data (a model pulled in a terminal is then seen)
    """
    calls = _preflight_returns(monkeypatch, RunPreflight(ollama_unreachable="✗ Cannot connect"))
    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: "Ollama isn't reachable" in _line(ready_window), timeout=3000)
    calls.clear()

    ready_window._readiness_line._buttons[0].click()

    qtbot.waitUntil(lambda: len(calls) >= 1, timeout=3000)
    assert calls[-1]["fresh"] is True


@pytest.mark.gui
def test_the_check_asks_for_the_engine_only_when_a_page_is_to_be_read(
    qtbot, ready_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a folder with one text file, later also an image, and the image's text recognised
    When   the readiness check starts each time
    Then   it asks for the recognition engine only while an image is still to be read
    """
    (tmp_path / "a.txt").write_text("hello")
    calls = _preflight_returns(monkeypatch, RunPreflight())
    ready_window._config_panel.set_input_dir(str(tmp_path))

    def asks_for_engine() -> bool:
        calls.clear()
        ready_window._readiness_controller.start_check()
        qtbot.waitUntil(lambda: len(calls) >= 1, timeout=3000)
        return calls[-1]["recognition"]

    assert asks_for_engine() is False
    (tmp_path / "b.png").touch()
    ready_window._refresh_queue_from_inputs()
    assert asks_for_engine() is True
    ready_window._results_table.set_queue(
        [
            queue_row(str(path.resolve()), path.name, ocr=StageState.DONE, has_text=True)
            for path in (tmp_path / "a.txt", tmp_path / "b.png")
        ]
    )
    assert asks_for_engine() is False


@pytest.mark.gui
def test_ticking_a_results_box_asks_the_readiness_check_again(ready_window, monkeypatch) -> None:
    """
    Given  the main window
    When   "Text file, anonymized" is ticked (it needs name detection)
    Then   a new readiness check is scheduled, so the line follows what Run will require
    """
    scheduled: list[int] = []
    monkeypatch.setattr(
        ready_window._readiness_controller, "schedule_check", lambda: scheduled.append(1)
    )
    panel = ready_window._config_panel

    panel._results._text_enabled.setChecked(True)
    panel._results._text_mode.setCurrentText("anonymized")

    assert scheduled


@pytest.mark.gui
def test_the_line_warns_about_text_files_saved_in_a_subfolder_of_the_documents(
    qtbot, ready_window, tmp_path: Path
) -> None:
    """
    Given  documents in a folder and a results folder inside it, with "Text file" ticked
    When   the readiness line updates
    Then   it warns like Run would (Run looks into subfolders), instead of saying "Ready"
    """
    (tmp_path / "a.png").touch()
    panel = ready_window._config_panel
    panel.set_input_dir(str(tmp_path))
    results = str(tmp_path / "results")
    panel._results._output_dir.setText(results)
    panel._settings_dict["output"] = results
    panel._results._text_enabled.setChecked(True)
    ready_window._results_table.selectAll()

    ready_window._readiness_controller.update()

    assert "outside the documents folder" in _line(ready_window)


@pytest.mark.gui
def test_a_settings_change_checks_once_after_a_pause(qtbot, ready_window, monkeypatch) -> None:
    """
    Given  a window whose settings change twice in a row
    When   the pause passes
    Then   one readiness check ran (the changes were bundled)
    """
    calls = _preflight_returns(monkeypatch, RunPreflight())
    qtbot.waitUntil(lambda: len(calls) >= 1, timeout=3000)
    # Start-up asks twice: the debounced data-flow check and the window's 500 ms one. Wait
    # until both have run, or a late one counts as a check the two changes below made.
    qtbot.wait(800)
    calls.clear()

    ready_window._config_panel._correction_enabled.setChecked(False)
    ready_window._config_panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: len(calls) >= 1, timeout=3000)
    qtbot.wait(600)

    assert len(calls) == 1


@pytest.mark.gui
def test_a_download_takes_over_the_line_and_a_new_check_follows(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  a model download that starts and makes progress
    When   the line is read, and then the download finishes
    Then   the line showed the download with its progress, and ends with a new readiness check
    """
    checks: list[int] = []
    monkeypatch.setattr(
        ready_window._readiness_controller, "start_check", lambda **_kw: checks.append(1)
    )
    monkeypatch.setattr(
        ready_window._app_service.recommendations, "on_ollama_models_changed", lambda _url: None
    )

    ready_window._on_pull_started("qwen3:8b")
    ready_window._on_pull_progress("pulling", 50, 200)

    assert _line(ready_window) == "Downloading qwen3:8b"
    assert ready_window._readiness_line._download_bar.value() == 25
    ready_window._on_pull_finished("qwen3:8b")
    qtbot.waitUntil(lambda: checks == [1], timeout=3000)
    assert ready_window._readiness_line.downloading is False


@pytest.mark.gui
def test_closing_the_window_stops_the_retries_and_pending_checks(ready_window) -> None:
    """
    Given  an unreachable Ollama with a retry scheduled and a check waiting for its pause
    When   the window is closed
    Then   both timers are stopped
    """
    from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

    ready_window._readiness_controller.on_check_done(
        RunPreflight(ollama_unreachable="✗ down"), None
    )
    ready_window._readiness_controller.schedule_check()
    assert ready_window._readiness_controller._ollama_retry.isActive() is True

    ready_window.closeEvent(QCloseEvent())

    assert ready_window._readiness_controller._ollama_retry.isActive() is False
    assert ready_window._readiness_controller._debounce.isActive() is False


# ── Text recognition switched off ────────────────────────────────────────────


@pytest.mark.gui
def test_a_skipped_file_opens_no_error_dialog_and_is_counted_apart(
    main_window, monkeypatch
) -> None:
    """
    Given  a queued scan and a run that leaves it out because text recognition is off
    When   _on_file_done reports it as skipped
    Then   no "Processing error" dialog opens, the row says "skipped", and the finished run
           names the skipped file
    """
    shown = record_notices(monkeypatch)
    source = "/tmp/scan.png"
    monkeypatch.setattr(
        main_window._app_service.documents,
        "queue_rows",
        lambda *_a, **_k: [queue_row(source, "scan.png")],
    )
    main_window._results_table.set_queue([queue_row(source, "scan.png")])

    main_window._run_controller._on_file_done(
        FileDoneEvent(
            outcome=FileOutcome(
                source_id=source,
                name="scan.png",
                status=FileStatus.SKIPPED,
                error="Needs text recognition, which is switched off.",
            ),
            ocr_s=0.0,
            correction_s=0.0,
        )
    )

    assert shown == []
    assert main_window._results_table.item(0, 1).text() == "skipped"


@pytest.mark.gui
def test_the_line_offers_to_turn_recognition_on_when_there_is_nothing_to_read(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  text recognition switched off and a check that finds no document with text
    When   the readiness check answers and "Turn on" is pressed
    Then   the line says there is nothing to read, and the button ticks "Recognize text"
    """
    ready_window._config_panel._recognition_enabled.setChecked(False)
    _preflight_returns(monkeypatch, RunPreflight(nothing_to_read=True))

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: "Nothing to read" in _line(ready_window), timeout=3000)

    assert _buttons(ready_window) == ["Turn on"]
    ready_window._readiness_line._buttons[0].click()
    assert ready_window._config_panel._recognition_enabled.isChecked() is True


@pytest.mark.gui
def test_the_note_counts_the_scans_the_check_found_and_marks_their_rows(
    qtbot, ready_window, monkeypatch, tmp_path
) -> None:
    """
    Given  recognition switched off, a queue of a scan and a text file, and a check that
           reports the scan as skipped
    When   the readiness check answers
    Then   the note under the switch counts one scan, and only the scan's row says "skipped"
    """
    scan, note = tmp_path / "scan.png", tmp_path / "note.txt"
    ready_window._results_table.set_queue(
        [
            queue_row(str(scan.resolve()), "scan.png"),
            queue_row(str(note.resolve()), "note.txt"),
        ]
    )
    ready_window._config_panel._recognition_enabled.setChecked(False)
    _preflight_returns(monkeypatch, RunPreflight(skipped=(scan,)))

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(
        lambda: ready_window._results_table.item(0, 1).text() == "skipped", timeout=3000
    )

    assert "1 scan will be skipped" in ready_window._config_panel._recognition_note.text()
    assert ready_window._results_table.item(1, 1).text() == "-"


# ── Finding Ollama ────────────────────────────────────────────────────────────


@pytest.mark.gui
def test_an_unreachable_server_triggers_one_silent_search_and_offers_the_hit(
    qtbot, ready_window, monkeypatch
) -> None:
    """
    Given  a saved address that does not answer, and Ollama running on the Windows computer
    When   the readiness check answers "unreachable" (twice)
    Then   this computer was searched once, the line offers "Use it" without changing the
           address, and pressing it saves the found address
    """
    from teachers_teammate.domain.ollama_discovery import DiscoveredServer, ServerKind  # noqa: PLC0415

    searched: list[object] = []
    hit = DiscoveredServer("http://172.20.0.1:11434", ServerKind.WINDOWS_HOST, "0.12.3", 2)

    def discover(self, scope, **_kw):
        searched.append(scope)
        return [hit]

    monkeypatch.setattr(OllamaServerService, "discover_ollama_servers", discover)
    _preflight_returns(monkeypatch, RunPreflight(ollama_unreachable="✗ Cannot connect"))

    ready_window._readiness_controller.start_check()
    qtbot.waitUntil(lambda: _buttons(ready_window) == ["Use it", "Find server…"], timeout=3000)
    ready_window._readiness_controller.start_check()
    qtbot.wait(200)

    assert len(searched) == 1
    assert "the Windows computer" in _line(ready_window)
    assert ready_window._current_ollama_url() != hit.url
    ready_window._readiness_line._buttons[0].click()
    assert ready_window._current_ollama_url() == hit.url


@pytest.mark.gui
def test_the_time_left_is_estimated_for_the_steps_the_run_really_runs(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run that only recognises text (a right-click on "Re-run text recognition":
           proofreading and the content review are switched off)
    When   the time left is estimated
    Then   the estimate is asked for the reading model alone, not for the panel's other ticks
    """
    config = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="glm-ocr:q8_0",
        recognition_enabled=True,
        correction_enabled=False,
        correction_provider="ollama",
        evaluation_enabled=False,
        evaluate_provider="ollama",
    )
    asked: list[object] = []
    monkeypatch.setattr(
        main_window._app_service.speeds,
        "speed_summary",
        lambda choices, **_kw: asked.append(choices) or SpeedSummary(),
    )

    main_window._run_controller._estimate_per_page(config)

    assert len(asked) == 1
    choices = asked[0]
    assert choices.reading_model == "glm-ocr:q8_0"
    assert choices.writing_model == ""
    assert choices.review_model == ""


# ── The update check switch ──────────────────────────────────────────────────


def _window_with_update_setting(qtbot, monkeypatch, value: bool | None) -> tuple[MainWindow, list]:
    """Build a MainWindow whose panel reports *value* for ``check_for_updates``.

    ``None`` leaves the key out of the settings dict, which is what an ``ocr.toml``
    written before the switch existed looks like.  Returns the window and the list of
    update checks it asked for.
    """
    asked = record_update_checks(monkeypatch)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    real = ConfigPanel.get_settings_dict

    def settings_dict(self) -> dict:
        settings = real(self)
        if value is None:
            settings.pop("check_for_updates", None)
        else:
            settings["check_for_updates"] = value
        return settings

    monkeypatch.setattr(ConfigPanel, "get_settings_dict", settings_dict)
    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)
    return win, asked


@pytest.mark.gui
def test_the_update_check_runs_when_the_switch_is_on(qtbot, monkeypatch) -> None:
    """
    Given  settings with check_for_updates on
    When   the main window is built
    Then   the window asks for a newer release once, with the banner as its callback
    """
    win, asked = _window_with_update_setting(qtbot, monkeypatch, True)

    assert asked == [win._show_update_banner]


@pytest.mark.gui
def test_no_update_check_runs_when_the_switch_is_off(qtbot, monkeypatch) -> None:
    """
    Given  settings with check_for_updates off
    When   the main window is built
    Then   the window does not ask for a newer release
    """
    _win, asked = _window_with_update_setting(qtbot, monkeypatch, False)

    assert asked == []


@pytest.mark.gui
def test_the_update_banner_escapes_what_the_release_feed_sent(qtbot, monkeypatch) -> None:
    """
    Given  a release whose version and address carry markup and a quote
    When   the banner is shown
    Then   the markup is text, so the link cannot be broken open or extended
    """
    win, _asked = _window_with_update_setting(qtbot, monkeypatch, True)

    win._show_update_banner("1.0<i>", 'https://x.test/"><b>pwn</b>')

    text = win._update_banner.findChild(QLabel, "UpdateBannerLabel").text()
    assert "<i>" not in text and "<b>pwn</b>" not in text
    assert "&lt;i&gt;" in text and "&quot;" in text


@pytest.mark.gui
def test_an_old_config_without_the_key_still_checks(qtbot, monkeypatch) -> None:
    """
    Given  settings written before the switch existed (no check_for_updates key)
    When   the main window is built
    Then   the check runs, because the default is on
    """
    win, asked = _window_with_update_setting(qtbot, monkeypatch, None)

    assert asked == [win._show_update_banner]


def _answer_with(monkeypatch, *, index: int = 0, cancel: bool = False) -> list[Question]:
    """Answer every question with its *index*-th button, or with Cancel; return what was asked."""
    asked: list[Question] = []

    def choose(_parent: object, question: Question) -> Answer:
        asked.append(question)
        return Answer(None if cancel else question.choices[index], False)

    monkeypatch.setattr(_dialogs, "choose", choose)
    return asked


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_keeps_the_changes_by_default(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run whose proofreading settings changed and two documents the teacher worked on
    When   the run-start question is answered with *Keep my changes*
    Then   the answer is recorded for those documents and the run goes ahead
    """
    cfg = make_config(tmp_path)
    asked = _answer_with(monkeypatch)
    kept: list[list[str]] = []
    started: list[bool] = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "keep_teacher_work",
        lambda _cfg, *, files: kept.append([str(path) for path in files]),
    )
    worked_on = WorkedOn(
        kept_stale=(Path("/in/a.png"), Path("/in/b.png")),
        unanswered=(Path("/in/a.png"), Path("/in/b.png")),
    )

    main_window._run_start._confirm_teacher_work_then(cfg, worked_on, lambda: started.append(True))

    assert asked[0].title == "You worked on some of these corrections"
    assert "a.png" in asked[0].body and "b.png" in asked[0].body
    assert asked[0].choices[0].id == dlg.KEEP_MY_CHANGES
    assert asked[0].choices[0].default is True
    assert kept == [["/in/a.png", "/in/b.png"]]
    assert started == [True]


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_proofreads_again_when_asked(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run whose proofreading settings changed and a document the teacher worked on
    When   the run-start question is answered with *Proofread again*
    Then   that document's proofreading is dropped and the run goes ahead
    """
    cfg = make_config(tmp_path)
    _answer_with(monkeypatch, index=1)
    dropped: list[tuple[list[str], str]] = []
    started: list[bool] = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda _cfg, *, files, stage: (
            dropped.append(([str(file.resolve()) for file in files], stage)) or ()
        ),
    )
    worked_on = WorkedOn(kept_stale=(Path("/in/a.png"),), unanswered=(Path("/in/a.png"),))

    main_window._run_start._confirm_teacher_work_then(cfg, worked_on, lambda: started.append(True))

    assert dropped == [(["/in/a.png"], "correction")]
    assert started == [True]


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_cancels_the_run(main_window, monkeypatch, tmp_path: Path) -> None:
    """
    Given  a run whose proofreading settings changed and a document the teacher worked on
    When   the run-start question is cancelled
    Then   nothing is written and the run does not start
    """
    cfg = make_config(tmp_path)
    _answer_with(monkeypatch, cancel=True)
    started: list[bool] = []

    main_window._run_start._confirm_teacher_work_then(
        cfg,
        WorkedOn(kept_stale=(Path("/in/a.png"),), unanswered=(Path("/in/a.png"),)),
        lambda: started.append(True),
    )

    assert started == []


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_only_continues_when_nothing_is_stored(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run that proofreads and a document whose corrections belong to no stored answer
    When   the run-start question is shown
    Then   it only says so and offers Continue, and the run goes ahead
    """
    cfg = make_config(tmp_path)
    asked = _answer_with(monkeypatch)
    started: list[bool] = []

    main_window._run_start._confirm_teacher_work_then(
        cfg, WorkedOn(unbound=(Path("/in/a.png"),)), lambda: started.append(True)
    )

    assert [choice.id for choice in asked[0].choices] == [dlg.CONTINUE, ""]
    assert "not stored here" in asked[0].body
    assert started == [True]


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_no_question_when_the_teacher_worked_on_nothing(main_window, monkeypatch, tmp_path) -> None:
    """
    Given  a run over documents the teacher did nothing to
    When   the run-start step is reached
    Then   nothing is asked and the run goes ahead
    """
    cfg = make_config(tmp_path)
    asked = _answer_with(monkeypatch)
    started: list[bool] = []

    main_window._run_start._confirm_teacher_work_then(cfg, WorkedOn(), lambda: started.append(True))

    assert asked == []
    assert started == [True]


@pytest.mark.gui
def test_rerunning_the_proofreading_asks_before_it_drops_the_changes(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  two documents whose corrections the teacher worked on
    When   *Re-run Proofreading* is asked for and the question is cancelled
    Then   one dialog names both and the cache is not touched
    """
    cfg = make_config(tmp_path)
    asked: list[Question] = []
    ran: list[str] = []
    monkeypatch.setattr(_dialogs, "ask", lambda _parent, question: asked.append(question) or False)
    monkeypatch.setattr(
        main_window._run_start, "_invalidate_and_run", lambda *_a: ran.append("run")
    )

    main_window._run_start._confirm_rerun_then(
        cfg,
        ["/in/a.png", "/in/b.png"],
        "correction",
        WorkedOn(worked_on=(Path("/in/a.png"), Path("/in/b.png"))),
        None,
    )

    assert asked[0].title == "Proofread again?"
    assert "a.png" in asked[0].body and "b.png" in asked[0].body
    assert asked[0].choices[0].id == dlg.PROOFREAD_AGAIN
    assert asked[0].choices[1].declines is True
    assert ran == []


@pytest.mark.gui
def test_rerunning_the_proofreading_goes_ahead_when_confirmed(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document whose corrections the teacher worked on
    When   *Re-run Proofreading* is confirmed
    Then   the cache is invalidated and the run starts
    """
    cfg = make_config(tmp_path)
    ran: list[str] = []
    monkeypatch.setattr(_dialogs, "ask", lambda *_a, **_k: True)
    monkeypatch.setattr(
        main_window._run_start, "_invalidate_and_run", lambda *_a: ran.append("run")
    )

    main_window._run_start._confirm_rerun_then(
        cfg, ["/in/a.png"], "correction", WorkedOn(worked_on=(Path("/in/a.png"),)), None
    )

    assert ran == ["run"]


@pytest.mark.gui
def test_rerunning_the_recognition_asks_before_it_replaces_the_transcription(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document whose transcription the teacher fixed
    When   *Re-run Text Recognition* is asked for and the question is cancelled
    Then   the question says what is replaced and the cache is not touched
    """
    cfg = make_config(tmp_path)
    asked: list[Question] = []
    ran: list[str] = []
    monkeypatch.setattr(_dialogs, "ask", lambda _parent, question: asked.append(question) or False)
    monkeypatch.setattr(
        main_window._run_start, "_invalidate_and_run", lambda *_a: ran.append("run")
    )

    main_window._run_start._confirm_rerun_then(
        cfg, ["/in/a.png"], "ocr", WorkedOn(transcribed=(Path("/in/a.png"),)), None
    )

    assert asked[0].title == "Read again?"
    assert "a.png" in asked[0].body
    assert asked[0].choices[0].id == dlg.READ_AGAIN
    assert ran == []


@pytest.mark.gui
def test_rerunning_a_stage_the_teacher_did_not_touch_asks_nothing(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document the teacher did nothing to
    When   a stage is re-run
    Then   nothing is asked and the run starts
    """
    cfg = make_config(tmp_path)
    asked: list[Question] = []
    ran: list[str] = []
    monkeypatch.setattr(_dialogs, "ask", lambda _parent, question: asked.append(question) or True)
    monkeypatch.setattr(
        main_window._run_start, "_invalidate_and_run", lambda *_a: ran.append("run")
    )

    main_window._run_start._confirm_rerun_then(cfg, ["/in/a.png"], "correction", WorkedOn(), None)

    assert asked == []
    assert ran == ["run"]


@pytest.mark.gui
def test_reading_again_clears_the_rows_before_the_run_starts(main_window, stored) -> None:
    """
    Given  two queued documents with a stored reading
    When   *Read again* is confirmed for both
    Then   neither row shows a reading any more, so a run that stops early leaves none behind
    """
    source_ids = stored("a.png", "b.png")
    cfg = main_window._config_panel.to_config()

    main_window._run_start._invalidate_and_run(cfg, source_ids, Stage.OCR)

    assert [main_window._results_table.row_for(sid).ocr for sid in source_ids] == [
        StageState.NONE,
        StageState.NONE,
    ]


@pytest.mark.gui
def test_a_queue_refresh_between_run_and_start_does_not_lose_the_selection(
    main_window, stored, monkeypatch
) -> None:
    """
    Given  two queued documents, both selected, and Run pressed while the requirements are
           still being checked
    When   the queue is refreshed (the background probe of this computer finished) and the
           check then lets the run through
    Then   the run starts on both documents instead of saying that no file is selected
    """
    source_ids = stored("a.png", "b.png")
    main_window._config_panel.set_input_dir(str(Path(source_ids[0]).parent))
    main_window._results_table.selectAll()
    started: list[list[str]] = []
    monkeypatch.setattr(
        main_window._run_controller, "start", lambda _cfg, ids, *_a, **_k: started.append(ids)
    )
    shown = record_notices(monkeypatch)

    main_window._refresh_queue_from_inputs()
    assert main_window._results_table.queued_source_ids() == source_ids
    main_window._run_start._continue_run(main_window._config_panel.to_config())

    assert started == [source_ids]
    assert shown == []


# ── What the review of the branch found ───────────────────────────────────────


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_does_not_start_when_the_proofreading_cannot_be_dropped(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run-start question answered with *Proofread again*
    When   the cache of that document cannot be invalidated
    Then   the failure is shown and the run does not start
    """
    cfg = make_config(tmp_path)
    _answer_with(monkeypatch, index=1)
    shown: list[object] = []
    started: list[bool] = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda *_a, **_k: (("a.png", "denied"),),
    )
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))

    main_window._run_start._confirm_teacher_work_then(
        cfg,
        WorkedOn(kept_stale=(Path("/in/a.png"),), unanswered=(Path("/in/a.png"),)),
        lambda: started.append(True),
    )

    assert len(shown) == 1
    assert started == []


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_run_start_question_names_a_document_by_its_resolved_path(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document listed through a symbolic link, so that its path is not its resolved one
    When   the run-start question is answered with *Proofread again*
    Then   the cache is asked about the resolved path, the id the queue is keyed by
    """
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)
    listed = tmp_path / "link" / "a.png"
    cfg = make_config(tmp_path)
    _answer_with(monkeypatch, index=1)
    asked_for: list[list[str]] = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "invalidate_from_stage",
        lambda _cfg, *, files, stage: (
            asked_for.append([str(file.resolve()) for file in files]) or ()
        ),
    )

    main_window._run_start._confirm_teacher_work_then(
        cfg,
        WorkedOn(kept_stale=(listed,), unanswered=(listed,)),
        lambda: None,
    )

    assert asked_for == [[str(listed.resolve())]]
    assert str(listed) != str(listed.resolve())


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_the_corrections_of_no_stored_answer_are_dropped_once_the_teacher_went_on(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run-start question about corrections that belong to no stored proofreading
    When   it is answered with *Continue*, and, in another run, cancelled
    Then   they are dropped only for the answered one, so the question does not come back
    """
    cfg = make_config(tmp_path)
    dropped: list[list[Path]] = []
    monkeypatch.setattr(
        main_window._app_service.commands,
        "drop_unbound_corrections",
        lambda _cfg, *, files: dropped.append(list(files)),
    )
    worked_on = WorkedOn(unbound=(Path("/in/a.png"),))

    _answer_with(monkeypatch, cancel=True)
    main_window._run_start._confirm_teacher_work_then(cfg, worked_on, lambda: None)
    assert dropped == []

    _answer_with(monkeypatch)
    main_window._run_start._confirm_teacher_work_then(cfg, worked_on, lambda: None)
    assert dropped == [[Path("/in/a.png")]]


@pytest.mark.gui
def test_rerunning_the_recognition_also_asks_for_corrections_without_a_transcription(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a document whose corrections the teacher worked on, and no fixed transcription
    When   *Re-run Text Recognition* is asked for
    Then   the question names it, because the new reading removes those corrections too
    """
    cfg = make_config(tmp_path)
    asked: list[Question] = []
    monkeypatch.setattr(_dialogs, "ask", lambda _parent, question: asked.append(question) or False)

    main_window._run_start._confirm_rerun_then(
        cfg, ["/in/a.png"], "ocr", WorkedOn(worked_on=(Path("/in/a.png"),)), None
    )

    assert asked[0].title == "Read again?"
    assert "a.png" in asked[0].body
    assert "corrections" in asked[0].body


@pytest.mark.gui
def test_a_rerun_does_not_go_ahead_when_what_was_worked_on_is_unknown(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a re-run whose look at the teacher's work failed
    When   the answer arrives
    Then   nothing is invalidated, and the teacher is told why
    """
    cfg = make_config(tmp_path)
    shown: list[object] = []
    ran: list[str] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))
    monkeypatch.setattr(
        main_window._run_start, "_invalidate_and_run", lambda *_a: ran.append("run")
    )

    main_window._run_start._confirm_rerun_then(
        cfg, ["/in/a.png"], "correction", None, OSError("disk")
    )

    assert ran == []
    assert len(shown) == 1


@pytest.mark.gui
def test_the_queue_shows_the_checked_mark_when_it_is_loaded(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document the teacher marked as checked in an earlier session
    When   the queue is built again from the folder
    Then   the Checked column says so, without a run or an edit in between
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    main_window._check_controller.page._checked_btn.click()
    monkeypatch.setattr(main_window, "_scan_input_files", lambda: [Path(source_id)])

    main_window._refresh_queue_from_inputs()

    assert main_window._results_table.item(0, ResultsTable._COL_CHECKED).text() == "Checked"
    assert main_window._results_table.checked_source_ids() == [source_id]


@pytest.mark.gui
def test_a_run_asks_about_the_teachers_work(main_window, monkeypatch, stored) -> None:
    """
    Given  a queue of documents with one selected
    When   Run is pressed
    Then   its requirement check is told to look for the work the teacher did
    """
    stored("a.png")
    main_window._results_table.selectRow(0)
    captured: dict = {}
    monkeypatch.setattr(
        main_window._run_start,
        "_check_requirements_then",
        lambda _config, _on_ready, **kwargs: captured.update(kwargs),
    )

    main_window._run_start.start()

    assert captured["ask_about_work"] is True


# ── The room the Check tab's panes take ──────────────────────────────────────


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NothingToExport("note.png"), "note.png: No OCR text"),
        (WouldOverwriteInput(Path("/in/essay.txt")), "note.png: essay.txt is an input document"),
        (OSError("disk full"), "note.png: disk full"),
    ],
)
def test_an_export_error_is_told_in_the_teachers_words(error: Exception, expected: str) -> None:
    """
    Given  an export that failed for a document, for each kind of failure
    When   the window words the failure
    Then   the two refusals use the sentences of texts/ and any other error its own message
    """
    assert export_controller_module._export_failure("note.png", error).startswith(expected)


# ── What the app walkthrough looks up ────────────────────────────────────────

#: Everything inside the window that the app walkthrough finds by object name.
_WALKTHROUGH_WIDGETS = (
    _handles.RUN_BUTTON,
    _handles.STOP_BUTTON,
    _handles.EXPORT_BUTTON,
    _handles.MAIN_TABS,
    _handles.RESULTS_TABLE,
    _handles.INPUT_FOLDER_BUTTON,
    _handles.INPUT_PATH,
    _handles.OUTPUT_PATH,
    _handles.OUTPUT_FOLDER_BUTTON,
    _handles.CHECK_PAGE,
    _handles.READ_VIEW,
    _handles.FINAL_VIEW,
    _handles.FINAL_VIEW_BUTTON,
    _handles.ITEM_LIST,
    _handles.CHECKED_BUTTON,
)

#: The menu entries it triggers.
_WALKTHROUGH_ACTIONS = (
    _handles.ALL_SETTINGS,
    _handles.BENCHMARK,
    _handles.SYSTEM_MONITOR,
    _handles.SETUP_ASSISTANT,
    _handles.USER_GUIDE,
    _handles.LICENSES,
    _handles.ABOUT,
)


def _top_level(name: str) -> list[QWidget]:
    """Return the windows of the app, visible or not, that carry the object name *name*."""
    return [widget for widget in QApplication.topLevelWidgets() if widget.objectName() == name]


@pytest.mark.gui
def test_the_walkthrough_finds_every_handle_exactly_once(qtbot, main_window, stored) -> None:
    """
    Given  the main window with one queued document, and the System Monitor it built
    When   the walkthrough looks up each handle of ``gui/_handles.py`` by object name
    Then   each is found exactly once: the buttons, the fields, the tabs, the table, the row's
           Open button, the menu entries, and the window the main window does not own
    """
    [a] = stored("a.png")

    for name in _WALKTHROUGH_WIDGETS:
        assert len(main_window.findChildren(QWidget, name)) == 1, name
    for name in _WALKTHROUGH_ACTIONS:
        assert len(main_window.findChildren(QAction, name)) == 1, name

    opened = main_window.findChildren(QAbstractButton, _handles.OPEN_DOCUMENT)
    assert [button.property(_handles.SOURCE_ID_PROPERTY) for button in opened] == [a]

    # Among the top-level widgets, because the main window does not own it; this test process
    # keeps the monitors of the tests before it, so the walkthrough's own window is checked for.
    assert main_window._monitor in _top_level(_handles.MONITOR_WINDOW)


# ── A settings file that cannot be read ──────────────────────────────────────


def _settings_file_window(
    qtbot, monkeypatch, folder: Path, *, content: bytes = b"this is not toml"
) -> MainWindow:
    """Open a MainWindow whose ``ocr.toml`` in *folder* is a file the app cannot read.

    The window reads that file as it opens, so a test never loads a second time: a second load
    would leave a second copy beside the file.
    """
    config_path = folder / "ocr.toml"
    config_path.write_bytes(content)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(
        "teachers_teammate.application.setup_plan.SetupService.resolve_config_path",
        lambda self, _: config_path,
    )
    win = MainWindow(app_service=real_service())
    qtbot.addWidget(win)
    return win


def _files_in(folder: Path) -> list[str]:
    """Return the names of the files in *folder*, sorted."""
    return sorted(path.name for path in folder.iterdir())


def _refuse_every_copy(monkeypatch) -> None:
    """Make keeping a copy of the settings file impossible, as a full disk does."""

    def refuse(_path):
        raise OSError("no room for a copy")

    monkeypatch.setattr("teachers_teammate.gui._settings_file.broken_settings_copy", refuse)


def _refuse_the_first_copy(monkeypatch) -> None:
    """Refuse the first copy and let the next one through, as a file freed in between."""
    refusals = 1

    def copy_once_refused(path):
        nonlocal refusals
        if refusals:
            refusals -= 1
            raise OSError("no room for a copy")
        return broken_settings_copy(path)

    monkeypatch.setattr(
        "teachers_teammate.gui._settings_file.broken_settings_copy", copy_once_refused
    )


@pytest.mark.gui
def test_an_unreadable_settings_file_is_kept_and_copied(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a settings file the app cannot parse
    When   the window opens and the teacher then saves
    Then   the file is left untouched, one copy sits beside it, and the save writes a fresh file
    """
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)

    assert (tmp_path / "ocr.toml").read_text(encoding="utf-8") == "this is not toml"
    assert (tmp_path / "ocr.toml.broken").read_text(encoding="utf-8") == "this is not toml"
    assert _files_in(tmp_path) == ["ocr.toml", "ocr.toml.broken"]

    win._settings_file.save(notify=False)

    assert load_config_file(tmp_path / "ocr.toml").values
    assert _files_in(tmp_path) == ["ocr.toml", "ocr.toml.broken"]


@pytest.mark.gui
def test_a_settings_file_that_is_not_utf8_is_kept_and_copied(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a settings file an older Windows editor saved in a code page rather than UTF-8
    When   the window opens it and the teacher then saves
    Then   the file keeps its bytes, a copy holds them, and the save writes a file that reads
    """
    kept = 'language = "Français"\n'.encode("cp1252")
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path, content=kept)

    assert (tmp_path / "ocr.toml").read_bytes() == kept
    assert (tmp_path / "ocr.toml.broken").read_bytes() == kept

    win._settings_file.save(notify=False)

    assert load_config_file(tmp_path / "ocr.toml").values


@pytest.mark.gui
def test_settings_are_not_written_over_a_file_whose_copy_failed(
    qtbot, monkeypatch, tmp_path
) -> None:
    """
    Given  a settings file that cannot be read and cannot be copied either
    When   the window opens and the teacher then saves
    Then   the file keeps its bytes
    """
    _refuse_every_copy(monkeypatch)
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)

    win._settings_file.save(notify=False)

    assert (tmp_path / "ocr.toml").read_text(encoding="utf-8") == "this is not toml"
    assert not (tmp_path / "ocr.toml.broken").exists()


@pytest.mark.gui
def test_a_save_copies_the_file_that_could_not_be_copied_at_start(
    qtbot, monkeypatch, tmp_path
) -> None:
    """
    Given  a settings file that could not be read, and a copy that failed while the window opened
    When   the teacher saves once the file can be copied
    Then   the copy is made and the save goes ahead, so the session is not lost to the refusal
    """
    _refuse_the_first_copy(monkeypatch)
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)
    assert not (tmp_path / "ocr.toml.broken").exists()

    win._settings_file.save(notify=False)

    assert (tmp_path / "ocr.toml.broken").read_text(encoding="utf-8") == "this is not toml"
    assert load_config_file(tmp_path / "ocr.toml").values


@pytest.mark.gui
def test_a_save_goes_ahead_when_the_unreadable_file_is_gone(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a settings file that could neither be read nor copied, and that is removed afterwards
    When   the teacher saves
    Then   the settings are written: a file that is gone takes nothing with it
    """
    _refuse_every_copy(monkeypatch)
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)
    (tmp_path / "ocr.toml").unlink()

    win._settings_file.save(notify=False)

    assert load_config_file(tmp_path / "ocr.toml").values


@pytest.mark.gui
def test_a_save_keeps_the_copy_again_when_the_copy_is_gone(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  an unreadable settings file whose copy the teacher then deletes
    When   the teacher saves
    Then   a copy is made again before the file is written over
    """
    record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)
    (tmp_path / "ocr.toml.broken").unlink()

    win._settings_file.save(notify=False)

    assert (tmp_path / "ocr.toml.broken").read_text(encoding="utf-8") == "this is not toml"
    assert load_config_file(tmp_path / "ocr.toml").values


@pytest.mark.gui
def test_the_unreadable_settings_file_is_announced_once(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a settings file that can neither be read nor copied, saved on every ordinary change
    When   the teacher changes three settings
    Then   one box tells them, instead of a box per save that they can only dismiss
    """
    _refuse_every_copy(monkeypatch)
    shown = record_notices(monkeypatch)
    win = _settings_file_window(qtbot, monkeypatch, tmp_path)

    for _ in range(3):
        win._settings_file.save(notify=False)

    assert [notice.title for notice in shown] == ["Settings file kept"]
    assert (tmp_path / "ocr.toml").read_text(encoding="utf-8") == "this is not toml"
