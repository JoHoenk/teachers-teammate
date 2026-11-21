"""GUI tests for the Check tab and the controller that owns it, using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import dataclasses
from pathlib import Path
import threading
from typing import Any, ClassVar

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QApplication, QWidget

from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.application.ollama_server import OllamaServerService
from teachers_teammate.application.queue import QueueRow, StageState
from teachers_teammate.application.speed_estimates import SpeedSummary
from teachers_teammate.application.documents import WorkedOn
from teachers_teammate.application.export import NothingToExport, WouldOverwriteInput
from teachers_teammate.application.model_recommendations import ModelRecommendationService
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.application.run import IssueKind, RequirementIssue, RunPreflight, RunService
from teachers_teammate.application.setup_plan import SetupService
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    RECOMMENDED,
    DetectionReason,
    HardwareDetection,
)
from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from teachers_teammate.domain.teacher_work import WorkTooNewError
from teachers_teammate.gui import _dialogs
from teachers_teammate.gui import _export_controller as export_controller_module
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui import _preprocess_preview as preprocess_module
from teachers_teammate.gui import _readiness_controller as readiness_controller_module
from teachers_teammate.gui import _run_controller as run_controller_module
from teachers_teammate.gui import _run_session as run_session_module
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._config_panel import ConfigPanel
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._readiness_controller import ReadinessController
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._types import FileDoneEvent
from teachers_teammate.gui.check import CheckLayout, CopyKind
from teachers_teammate.gui.settings import SettingsPage, SettingsResult
from teachers_teammate.gui.settings._page import (
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
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
from teachers_teammate.infrastructure.state_repository import (
    StateRepository,
    compute_file_hash,
)
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore
from teachers_teammate.texts import check as check_text
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import Answer, Notice, Question, StandardButton
from tests.conftest import make_config

from ._gui_harness import install_scripted_settings_window, real_service


def _queue_row(
    source_id: str,
    name: str = "",
    *,
    ocr: StageState = StageState.NONE,
    correction: StageState = StageState.NONE,
    evaluation: StageState = StageState.NONE,
    has_text: bool = False,
    has_scan: bool = False,
    **extra: Any,
) -> QueueRow:
    """Return a queue row for *source_id*, named after the file unless told otherwise."""
    return QueueRow(
        source_id=source_id,
        name=name or Path(source_id).name,
        ocr=ocr,
        correction=correction,
        evaluation=evaluation,
        has_text=has_text,
        has_scan=has_scan,
        **extra,
    )


class _DummySignal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn):
        self._subs.append(fn)

    def disconnect(self) -> None:
        self._subs.clear()

    def emit(self, *args):
        for fn in self._subs:
            fn(*args)


class _DummyWorker:
    def __init__(self, _config, files=None, app_service=None) -> None:
        self.log_line = _DummySignal()
        self.file_started = _DummySignal()
        self.stage_started = _DummySignal()
        self.ocr_done = _DummySignal()
        self.stage_detail = _DummySignal()
        self.first_token = _DummySignal()
        self.file_done = _DummySignal()
        self.keep_awake_changed = _DummySignal()
        self.run_finished = _DummySignal()
        self.files = list(files or [])
        self.app_service = app_service
        self.stop_event = threading.Event()
        self.stop_after_file_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def request_stop(self, *, now: bool) -> None:
        self.stop_after_file_event.set()
        if now:
            self.stop_event.set()

    def wait(self, _msecs: int) -> bool:
        self._running = False
        return True


class _AlwaysRunning:
    """A worker that reports it is running, for the figures a run shows meanwhile."""

    def isRunning(self) -> bool:
        return True

    def request_stop(self, *, now: bool) -> None:
        pass

    def wait(self, _msecs: int) -> bool:
        return True


def _pretend_a_run_is_going(main_window, monkeypatch) -> None:
    """Give the run controller a running worker without starting one."""
    monkeypatch.setattr(main_window._run_controller, "_worker", _AlwaysRunning())


def _record_notices(monkeypatch) -> list[Notice]:
    """Record every notice the window shows; return the list it appends to."""
    shown: list[Notice] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))
    return shown


@pytest.fixture(autouse=True)
def _no_network_in_gui_tests(monkeypatch) -> None:
    """Prevent all MainWindow tests from making Ollama/network calls via the dependency guard."""
    monkeypatch.setattr(RunService, "check_stage_requirements", lambda *_: [])
    monkeypatch.setattr(RunService, "check_run_preflight", lambda *_args, **_kw: RunPreflight())
    # The silent server search must not probe the real computer or its network.
    monkeypatch.setattr(OllamaServerService, "discover_ollama_servers", lambda *_a, **_kw: [])
    # The status row checks its services in the background; never probe a real server.
    monkeypatch.setattr(
        ProviderService, "check_connection", lambda self, **_kw: (False, False, "offline (test)")
    )


@pytest.fixture(autouse=True)
def _no_privacy_prompt(monkeypatch) -> None:
    """Skip the "documents will leave this computer" question: tests must never block on it.

    ``make_config`` proofreads with an online provider, so many runs would ask.  The tests
    of the question itself restore the real method through the ``confirm_box`` fixture.
    """
    monkeypatch.setattr(
        MainWindow, "_confirm_data_flow_then", lambda self, _config, on_ready: on_ready()
    )


class _DummyUpdateThread:
    """Stand-in for UpdateCheckThread that never touches the network or starts a QThread.

    Without this, MainWindow.__init__ schedules a real UpdateCheckThread via
    QTimer.singleShot(0, ...); if a test only spins the event loop briefly, that timer
    never fires within the test, but it can fire during a *later* test's event-loop
    spin (the same QApplication persists across the whole session) and touch a
    MainWindow instance whose widgets are already torn down, crashing the process.
    """

    def __init__(self, _parent=None) -> None:
        self.update_available = _DummySignal()

    def start(self) -> None:
        pass


@pytest.fixture
def main_window(qtbot, monkeypatch) -> MainWindow:
    """Create a MainWindow with startup side effects disabled."""
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)
    win = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(win)
    return win


@pytest.mark.gui
def test_main_window_load_check_warns_about_a_document_without_state(
    main_window, monkeypatch
) -> None:
    """
    Given  a row whose document has no stored state
    When   _load_check is called for it
    Then   the teacher is told, and the tab stays where it is
    """
    main_window._results_table.set_queue([_queue_row("/tmp/page.png", "page.png")])
    shown = _record_notices(monkeypatch)

    main_window._check_controller.load("/tmp/page.png")

    assert len(shown) == 1
    assert "page.png" in shown[0].body
    assert "/tmp" not in shown[0].body  # it names the document, not a path as the "reason"
    assert main_window._tabs.currentIndex() == 0


@pytest.mark.gui
def test_a_failed_load_does_not_leave_the_open_document_locked(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  an open document that was locked for a run, and a run that has ended
    When   its reload fails because nothing usable is stored any more
    Then   the teacher is told and the lock of the finished run is lifted
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    main_window._run_session.pending = {a}
    main_window._check_controller.apply_lock()
    assert main_window._check_controller.page.body.banner.text == txt.WAITING_FOR_RUN
    main_window._run_session.pending = set()
    monkeypatch.setattr(
        main_window._app_service.documents, "check_document", lambda *_a, **_k: None
    )
    shown = _record_notices(monkeypatch)

    assert main_window._check_controller.load(a) is False

    assert len(shown) == 1
    assert main_window._check_controller.page.body.banner.isHidden()
    assert main_window._check_controller.current == a


@pytest.mark.gui
def test_a_document_opens_although_the_settings_are_not_valid_yet(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a stored document and settings the window cannot turn into a run config yet
    When   the document is opened in the Check tab
    Then   it is shown, because reading stored results needs no valid run settings
    """
    [a] = stored("a.png", raw="what was read")
    cfg = main_window._config_panel.to_config()

    def _invalid():
        raise ValueError("no output folder")

    monkeypatch.setattr(main_window._config_panel, "to_config", _invalid)
    monkeypatch.setattr(main_window._config_panel, "lookup_config", lambda: cfg)

    assert main_window._check_controller.load(a) is True
    assert "what was read" in main_window._check_controller.page.body.read_view.toPlainText()


@pytest.mark.gui
def test_a_stored_edit_reads_the_document_state_once(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab
    When   the teacher's edit of the final text is stored
    Then   the stored state is read once for the row and the page together
    """
    [a] = stored("a.png", raw="raw text", corrected="corr old")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(a)
    real = main_window._app_service.documents.load_view
    calls: list[str] = []

    def _counting(*args, **kwargs):
        calls.append("load_view")
        return real(*args, **kwargs)

    monkeypatch.setattr(main_window._app_service.documents, "load_view", _counting)

    main_window._check_controller.on_corrected_text_edited("corr edited")

    assert calls == ["load_view"]


@pytest.mark.gui
def test_main_window_ocr_edit_is_stored_as_the_teachers_work(main_window, stored) -> None:
    """
    Given  a document open in the Check tab with a cached proofreading
    When   OCR text is edited
    Then   the teacher's text is stored apart from the cache, whose proofreading is reset
    """
    [source_id] = stored("page.png", raw="raw old", corrected="corr old")
    source = Path(source_id)
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)

    main_window._check_controller.on_raw_text_edited("raw edited")

    cfg = main_window._config_panel.to_config()
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.raw_text == "raw old"
    assert state.correction_done is False
    assert state.correction_text == ""
    work = TeacherWorkStore().load(compute_file_hash(source))
    assert work is not None
    assert work.transcription is not None
    assert work.transcription.text == "raw edited"

    row = main_window._results_table.row_for(source_id)
    assert row is not None
    assert row.has_text
    assert row.correction is not StageState.DONE


@pytest.mark.gui
def test_main_window_correction_edit_is_stored_as_the_teachers_work(
    main_window,
    stored,
) -> None:
    """
    Given  a document open in the Check tab with a stored proofreading
    When   the final text is edited
    Then   the teacher's text is stored apart from the cache, which keeps the machine's
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr old")
    source = Path(source_id)
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)

    main_window._check_controller.on_corrected_text_edited("corr edited")

    cfg = main_window._config_panel.to_config()
    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.correction_done is True
    assert state.correction_text == "corr old"
    work = TeacherWorkStore().load(compute_file_hash(source))
    assert work is not None
    assert work.corrections is not None
    assert work.corrections.text == "corr edited"

    row = main_window._results_table.row_for(source_id)
    assert row is not None
    assert row.correction is not StageState.NONE


@pytest.mark.gui
def test_main_window_correction_edit_keeps_the_stored_warning_banner(
    main_window,
    stored,
) -> None:
    """
    Given  a file whose proofreading fell back to corrections only and stored a warning
    When   the teacher edits the proofread text and dismisses the reading-fix banner
    Then   the stored warning is still shown after the reload
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr old")
    source = Path(source_id)
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    cfg = main_window._config_panel.to_config()
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    state = repo.load(source)
    assert state is not None
    repo.save(source, dataclasses.replace(state, correction_warning="No explanations."))

    main_window._check_controller.on_corrected_text_edited("corr edited")

    assert main_window._check_controller.page.body.banner.text == txt.READING_FIX_WARNING
    main_window._check_controller.page.body.banner.action_clicked.emit()
    assert main_window._check_controller.page.body.banner.text == "No explanations."


# ── Additional coverage ─────────────────────────────────────────────────────


@pytest.mark.gui
def test_main_window_load_check_shows_the_text_of_the_document(main_window, stored) -> None:
    """
    Given  a row whose document has extracted text
    When   _load_check is called for it
    Then   the Check tab shows that text and the header names the document
    """
    [source_id] = stored("page.png", raw="ocr content")

    main_window._check_controller.load(source_id)

    assert "ocr content" in main_window._check_controller.page.body.read_view.toPlainText()
    assert main_window._check_controller.page._name_label.text() == "page.png"
    assert main_window._check_controller.page._position_label.text() == "1 of 1"


@pytest.mark.gui
def test_main_window_load_check_hands_the_page_the_stored_review(main_window, stored) -> None:
    """
    Given  a row whose document has a stored review
    When   _load_check is called for it
    Then   the Check tab lists the correction that review explains
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(4, 8, "have", "has"),),
                sentences=(),
                category="grammar",
                explanation='"she" is 3rd person singular.',
            ),
        )
    )
    [source_id] = stored(
        "page.png", raw="She have a dog.", corrected="She has a dog.", review=review
    )

    main_window._check_controller.load(source_id)

    assert main_window._check_controller.page.body.panel.row_count == 1


@pytest.mark.gui
def test_the_check_strip_counts_down_with_the_bar(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab, a run over it estimated at 100 seconds, and the
           proofreading started at second 1000
    When   the clock moves on without any event of the run
    Then   the strip's figure counts down with the bar's, because both read one snapshot
    """
    from teachers_teammate.domain.performance_estimate import TimeRange  # noqa: PLC0415
    from teachers_teammate.gui._run_status import RunPace  # noqa: PLC0415

    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    clock = {"now": 1000.0}
    monkeypatch.setattr(run_controller_module.time, "monotonic", lambda: clock["now"])
    _pretend_a_run_is_going(main_window, monkeypatch)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._run_session.pace = RunPace(TimeRange(100.0, 100.0))
    main_window._run_session.pace.set_files(1)
    main_window._run_session.pace.set_pages(1)
    main_window._run_controller._on_file_started(a, "a.png", 1, 1)
    main_window._run_controller._on_stage_started(a, "a.png", "correction", 1, 1)
    strip = main_window._check_controller.page._run_strip

    clock["now"] = 1050.0
    main_window._run_controller._refresh_progress()

    assert "about 50 s left" in strip.text
    assert "about 50 s left" in main_window._progress.format()

    clock["now"] = 1070.0
    main_window._run_controller._refresh_progress()

    assert "about 30 s left" in strip.text
    assert "about 30 s left" in main_window._progress.format()


@pytest.mark.gui
def test_the_window_has_a_documents_tab_and_a_check_tab(main_window) -> None:
    """
    Given  the main window
    When   its tabs are read
    Then   the work and one document to check are the two tabs of the window
    """
    tabs = [main_window._tabs.tabText(i) for i in range(main_window._tabs.count())]

    assert tabs == ["Documents", "Check"]
    assert main_window._tabs.widget(0) is main_window._main_splitter
    assert main_window._tabs.widget(1) is main_window._check_controller.page


def _store_document(
    cfg,
    source: Path,
    *,
    raw: str = "",
    corrected: str = "",
    review: CorrectionReview | None = None,
    warning: str = "",
    text_format: str = "plain",
) -> None:
    """Write a stored state for *source*, so the Check tab has something to read."""
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    state = repo.create(source, compute_file_hash(source))
    repo.save(
        source,
        dataclasses.replace(
            state,
            raw_text=raw,
            correction_text=corrected,
            correction_review=review.to_dict() if review is not None else None,
            correction_warning=warning,
            raw_text_format=text_format,
            ocr_done=bool(raw),
            correction_done=bool(corrected),
        ),
    )


@pytest.fixture
def stored(main_window, monkeypatch, tmp_path: Path):
    """Return a factory that makes real documents with a stored state and queues them."""

    def _make(
        *names: str,
        raw: str = "what was read",
        corrected: str = "",
        review: CorrectionReview | None = None,
        warning: str = "",
        text_format: str = "plain",
    ) -> list[str]:
        cfg = make_config(tmp_path)
        cfg.input_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
        source_ids: list[str] = []
        for name in names:
            source = cfg.input_dir / name
            # One essay per file: the work is keyed by content, so two files must differ.
            source.write_text(f"page of {name}", encoding="utf-8")
            _store_document(
                cfg,
                source,
                raw=raw,
                corrected=corrected,
                review=review,
                warning=warning,
                text_format=text_format,
            )
            source_ids.append(str(source))
        main_window._results_table.set_queue(
            [
                _queue_row(
                    source_id,
                    name,
                    ocr=StageState.DONE if raw else StageState.NONE,
                    correction=StageState.DONE if corrected else StageState.NONE,
                    has_text=bool(raw),
                )
                for source_id, name in zip(source_ids, names, strict=True)
            ]
        )
        main_window._check_controller.update_tab()
        return source_ids

    return _make


@pytest.mark.gui
def test_check_tab_is_disabled_until_a_document_has_a_result(main_window) -> None:
    """
    Given  a queue whose only row has no result yet
    When   the tabs are read
    Then   Check cannot be opened, because there would be nothing to check
    """
    main_window._results_table.set_queue([_queue_row("/tmp/a.png", "a.png")])
    main_window._check_controller.update_tab()

    assert main_window._tabs.isTabEnabled(1) is False

    main_window._results_table.update_row(
        _queue_row("/tmp/a.png", "a.png", ocr=StageState.DONE, has_text=True)
    )
    main_window._check_controller.update_tab()

    assert main_window._tabs.isTabEnabled(1) is True


@pytest.mark.gui
def test_opening_a_document_shows_the_check_tab_and_labels_it(main_window, stored) -> None:
    """
    Given  a queue with two documents that have results
    When   the second one is opened
    Then   the Check tab is in front and its label names the document
    """
    _a, b = stored("a.png", "b.png")

    main_window._check_controller.open_document(b)

    assert main_window._tabs.currentIndex() == 1
    assert main_window._tabs.tabText(1) == "Check \u2014 b.png"
    assert main_window._tabs.tabToolTip(1) == "b.png\nCheck (Ctrl+2)"
    assert main_window._check_controller.page._position_label.text() == "2 of 2"


@pytest.mark.gui
def test_the_check_tab_label_shortens_a_long_name(main_window, stored) -> None:
    """
    Given  a document with a name longer than the tab label holds
    When   it is opened
    Then   the label is shortened and the tooltip keeps the whole name
    """
    name = "a_very_long_document_name_that_does_not_fit.pdf"
    [source_id] = stored(name)

    main_window._check_controller.open_document(source_id)

    label = main_window._tabs.tabText(1)
    assert label.startswith("Check \u2014 ")
    assert label.endswith("\u2026")
    assert len(label) == len("Check \u2014 ") + 28
    assert main_window._tabs.tabToolTip(1) == f"{name}\nCheck (Ctrl+2)"


@pytest.mark.gui
def test_clicking_the_check_tab_opens_the_current_row(main_window, stored) -> None:
    """
    Given  a queue with two documents that have results, the second one selected
    When   the Check tab is clicked
    Then   it shows the selected document and leaves the selection alone
    """
    _a, b = stored("a.png", "b.png")
    main_window._results_table.selectRow(1)

    main_window._tabs.setCurrentIndex(1)

    assert main_window._check_controller.current == b
    assert main_window._results_table.currentRow() == 1


@pytest.mark.gui
def test_check_target_falls_back_to_the_first_document_with_a_result(main_window) -> None:
    """
    Given  a queue whose first row has no result and whose second has one
    When   the Check tab asks which document to open
    Then   it takes the first one that has a result
    """
    main_window._results_table.set_queue(
        [
            _queue_row("/tmp/a.png", "a.png"),
            _queue_row("/tmp/b.png", "b.png", ocr=StageState.DONE, has_text=True),
        ]
    )
    main_window._results_table.selectRow(0)

    assert main_window._check_controller._target() == "/tmp/b.png"


@pytest.mark.gui
def test_ctrl_2_opens_a_document_when_none_is_open(main_window, stored) -> None:
    """
    Given  a queue with a document that has a result and no open document
    When   the Check tab is asked for
    Then   it opens the current row and shows the tab
    """
    [a] = stored("a.png")
    main_window._results_table.selectRow(0)

    main_window._check_controller.show_check()

    assert main_window._tabs.currentIndex() == 1
    assert main_window._check_controller.current == a


@pytest.mark.gui
def test_ctrl_2_does_nothing_while_no_document_can_be_opened(main_window) -> None:
    """
    Given  a queue without a result
    When   the Check tab is asked for
    Then   nothing happens: the tab stays disabled and Documents stays in front
    """
    main_window._results_table.set_queue([_queue_row("/tmp/a.png", "a.png")])
    main_window._check_controller.update_tab()

    main_window._check_controller.show_check()

    assert main_window._tabs.currentIndex() == 0


@pytest.mark.gui
def test_escape_in_the_check_tab_goes_back_to_documents(main_window, stored) -> None:
    """
    Given  a document open in the Check tab
    When   the page asks to go back
    Then   Documents is in front and the document stays open
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)

    main_window._check_controller.page.back_requested.emit()

    assert main_window._tabs.currentIndex() == 0
    assert main_window._check_controller.current == a


@pytest.mark.gui
def test_the_arrows_move_through_the_documents_and_select_the_row(main_window, stored) -> None:
    """
    Given  three documents with results, the first one open
    When   the next arrow is used twice and then the previous one
    Then   the open document and the selected row follow, and stop at the ends
    """
    a, b, c = stored("a.png", "b.png", "c.png")
    main_window._check_controller.open_document(a)

    main_window._check_controller.next_document()
    assert main_window._check_controller.current == b
    assert main_window._results_table.currentRow() == 1

    main_window._check_controller.next_document()
    main_window._check_controller.next_document()  # already at the end
    assert main_window._check_controller.current == c

    main_window._check_controller.previous_document()
    assert main_window._check_controller.current == b
    assert main_window._results_table.currentRow() == 1


@pytest.mark.gui
def test_a_run_start_locks_the_document_it_redoes_and_keeps_it_open(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab
    When   a run starts that includes it
    Then   Documents is in front, the document stays the open one, and it is read-only
           until its own result arrives
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)

    main_window._run_controller.start(make_config(Path("/tmp")), [a])

    assert main_window._check_controller.current == a
    assert main_window._tabs.currentIndex() == 0
    assert main_window._run_session.pending == {a}
    assert main_window._check_controller.page.body.banner.text == txt.WAITING_FOR_RUN


@pytest.mark.gui
def test_a_run_started_from_the_check_header_keeps_the_check_tab(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab, with the Check tab in front
    When   the teacher presses Proofread this document and the run starts
    Then   the Check tab stays in front, and the document is locked there
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    main_window._tabs.setCurrentIndex(1)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    monkeypatch.setattr(
        main_window,
        "_on_stage_run_requested",
        lambda ids, _stage: main_window._run_controller.start(make_config(Path("/tmp")), ids),
    )

    main_window._check_controller.on_proofread_requested()

    assert main_window._tabs.currentIndex() == 1
    assert main_window._run_session.pending == {a}


@pytest.mark.gui
def test_a_run_started_from_documents_goes_on_showing_documents_after_a_check_run(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run that was started from the Check header and has begun
    When   the next run is started from the queue while the Check tab is in front
    Then   Documents comes to the front, because the Check request was used up by the first run
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    main_window._run_session.from_check = True
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._tabs.setCurrentIndex(1)

    main_window._run_controller.start(make_config(Path("/tmp")), [a])

    assert main_window._tabs.currentIndex() == 0


@pytest.mark.gui
def test_a_proofread_request_from_check_during_a_run_is_refused_and_not_remembered(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run that is in progress and a document open in Check
    When   proofreading is asked for from the Check header
    Then   the busy warning is shown, and the tab is not marked as the origin of a run, so the
           next run started in Documents is not mistaken for one started in Check
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    warned: list[bool] = []
    _pretend_a_run_is_going(main_window, monkeypatch)
    monkeypatch.setattr(main_window._run_controller, "warn_busy", lambda: warned.append(True))

    main_window._check_controller.on_proofread_requested()

    assert warned == [True]
    assert main_window._run_session.from_check is False


@pytest.mark.gui
def test_the_check_tab_shows_the_run_while_the_open_document_is_in_it(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab, and a run over two documents that includes it
    When   the run starts, reaches the open document, reads it and ends
    Then   the run strip waits, then says what is done with the document, then is gone
    """
    a, b = stored("a.png", "b.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    strip = main_window._check_controller.page._run_strip

    config = dataclasses.replace(make_config(Path("/tmp")), correction_enabled=True)
    main_window._run_controller.start(config, [b, a])
    assert not strip.isHidden()
    assert strip.text == check_text.RUN_WAITING

    main_window._run_controller._on_file_started(a, "a.png", 2, 2)
    assert strip.text.startswith("Proofreading with ")

    main_window._run_controller._on_stage_started(a, "a.png", "ocr", 2, 2)
    assert strip.text == check_text.RUN_READING

    main_window._run_controller._on_ocr_done(a, "a.png", 2, 2)
    assert strip.text.startswith("Proofreading with ")

    main_window._run_controller._on_run_finished(RunSummary(succeeded=("a.png", "b.png")))
    assert strip.isHidden()


@pytest.mark.gui
def test_a_run_that_leaves_the_open_document_alone_shows_no_run_in_check(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab
    When   a run starts over another document
    Then   the Check tab shows no run strip
    """
    a, b = stored("a.png", "b.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)

    main_window._run_controller.start(make_config(Path("/tmp")), [b])

    assert main_window._check_controller.page._run_strip.isHidden()


@pytest.mark.gui
def test_the_check_stop_button_stops_the_run_like_the_documents_tab(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a run in which the open document is being worked on
    When   Stop is pressed in the Check tab
    Then   the run stops after the file, as with the button in Documents, and the strip offers
           Stop now
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    main_window._run_controller.start(make_config(Path("/tmp")), [a])
    main_window._run_controller._on_file_started(a, "a.png", 1, 1)

    main_window._check_controller.page._run_strip.stop_button.click()

    assert main_window._run_controller._worker.stop_after_file_event.is_set()
    assert main_window._check_controller.page._run_strip.stop_button.text() == "Stop now"


@pytest.mark.gui
def test_the_copy_menu_follows_the_privacy_add_on_for_the_open_document(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab while names cannot be hidden
    When   the privacy availability turns true
    Then   the "names hidden" copy entries of the open document are enabled
    """
    [a] = stored("a.png")
    monkeypatch.setattr(main_window._privacy_controller, "_available", False)
    main_window._check_controller.open_document(a)
    kind = CopyKind.RAW_ANONYMIZED
    assert main_window._check_controller.page._copy_actions[kind].isEnabled() is False

    main_window._app_service.anonymization.anonymizer_available = lambda _language, _config=None: (
        True
    )
    main_window._privacy_controller.refresh_availability()

    assert main_window._check_controller.page._copy_actions[kind].isEnabled() is True


@pytest.mark.gui
def test_a_run_start_keeps_a_document_it_leaves_alone(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab
    When   a run starts over other documents
    Then   the open document stays open, so the teacher can go on working on it
    """
    a, b = stored("a.png", "b.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)

    main_window._run_controller.start(make_config(Path("/tmp")), [b])

    assert main_window._check_controller.current == a
    assert main_window._check_controller.page.body.banner.text != txt.WAITING_FOR_RUN


@pytest.mark.gui
def test_a_rebuilt_queue_without_the_open_document_closes_check(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab
    When   the queue is rebuilt without it
    Then   the tab is closed and Documents is in front
    """
    _a, b = stored("a.png", "b.png")
    main_window._check_controller.open_document(b)
    monkeypatch.setattr(main_window, "_scan_input_files", lambda: [])

    main_window._refresh_queue_from_inputs()

    assert main_window._check_controller.current == ""
    assert main_window._tabs.currentIndex() == 0


@pytest.mark.gui
def test_the_documents_tab_label_counts_the_run(main_window, monkeypatch, stored) -> None:
    """
    Given  a run over three documents that has finished one
    When   the tab labels are read
    Then   Documents says how far the run has got
    """
    a, b, c = stored("a.png", "b.png", "c.png")
    monkeypatch.setattr(main_window._run_controller, "_start_pace", lambda *_a, **_kw: None)
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: None)
    main_window._run_controller.start(make_config(Path("/tmp")), [a, b, c])

    main_window._run_session.names = ["a.png"]
    main_window._check_controller.update_tab()

    assert main_window._tabs.tabText(0) == "Documents \u00b7 1 of 3"


@pytest.mark.gui
def test_copy_puts_what_was_read_on_the_clipboard(main_window, stored) -> None:
    """
    Given  a document open in the Check tab
    When   the teacher copies what was read
    Then   the clipboard holds that text and the header confirms it
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)

    main_window._check_controller.on_copy_requested(CopyKind.RAW)

    assert QApplication.clipboard().text() == "what was read"
    assert main_window._check_controller.page._copied_label.isHidden() is False


@pytest.mark.gui
def test_save_goes_through_the_export_path(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab
    When   the teacher saves what was read
    Then   the window's own export path is used, for that one document
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    asked: list[tuple[list[str], ExportKind]] = []
    monkeypatch.setattr(
        main_window._export_controller, "on_requested", lambda ids, kind: asked.append((ids, kind))
    )

    main_window._check_controller.on_save_requested(ExportKind.TEXT)

    assert asked == [([a], ExportKind.TEXT)]


@pytest.mark.gui
def test_a_double_click_in_the_queue_opens_the_document_in_the_check_tab(
    main_window, stored
) -> None:
    """
    Given  a queue with a document that has a result
    When   the queue asks for it to be checked
    Then   the Check tab is in front, shows that document and selects its row
    """
    _a, b = stored("a.png", "b.png")

    main_window._results_table.check_requested.emit(b)

    assert main_window._tabs.currentIndex() == 1
    assert main_window._check_controller.current == b
    assert main_window._results_table.currentRow() == 1


@pytest.mark.gui
def test_an_edit_goes_to_the_open_document_not_to_the_selected_row(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  document A open in the Check tab and document B selected in the queue
    When   the teacher edits the text
    Then   A's text is stored and B's is not

    The selection is how rows are chosen for a run; the edit belongs to what is on screen.
    """
    source_a, source_b = (Path(sid) for sid in stored("a.png", "b.png", raw="raw old"))
    main_window._check_controller.open_document(str(source_a))
    main_window._results_table.selectRow(1)

    main_window._check_controller.on_raw_text_edited("raw edited")

    work_a = TeacherWorkStore().load(compute_file_hash(source_a))
    work_b = TeacherWorkStore().load(compute_file_hash(source_b))
    assert work_a is not None
    assert work_a.transcription is not None
    assert work_a.transcription.text == "raw edited"
    assert work_b is None


@pytest.mark.gui
def test_copy_with_names_hidden_puts_the_anonymized_text_on_the_clipboard(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab and names that can be hidden
    When   the teacher copies it with names hidden
    Then   the clipboard holds the text the anonymizer produced
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    monkeypatch.setattr(main_window._privacy_controller, "_available", True)
    monkeypatch.setattr(
        main_window._app_service.export,
        "text_for_copy",
        lambda view, **_kw: f"hidden:{view.raw_text}",
    )
    monkeypatch.setattr(
        "teachers_teammate.gui._check_controller.run_in_background",
        lambda fn, on_done, **_kw: on_done(fn(), None),
    )

    main_window._check_controller.on_copy_requested(CopyKind.RAW_ANONYMIZED)

    assert QApplication.clipboard().text() == "hidden:what was read"


@pytest.mark.gui
def test_a_failing_anonymizer_leaves_the_clipboard_alone(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab and an anonymizer that fails
    When   the teacher copies it with names hidden
    Then   the failure is explained and the clipboard keeps what it had
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    QApplication.clipboard().setText("before")
    warned: list[Notice] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: warned.append(notice))

    def _fail(*_args, **_kwargs):
        raise RuntimeError("no model")

    monkeypatch.setattr(main_window._app_service.export, "text_for_copy", _fail)
    monkeypatch.setattr(
        "teachers_teammate.gui._check_controller.run_in_background",
        lambda fn, on_done, **_kw: _call_and_report(fn, on_done),
    )

    main_window._check_controller.on_copy_requested(CopyKind.RAW_ANONYMIZED)

    assert warned
    assert QApplication.clipboard().text() == "before"


def _call_and_report(fn, on_done) -> None:
    """Run *fn* the way the background pool would, error and all."""
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001  # the pool reports every failure to on_done
        on_done(None, exc)
        return
    on_done(result, None)


@pytest.mark.gui
def test_copy_saves_a_half_typed_edit_first(main_window, monkeypatch, stored) -> None:
    """
    Given  a document open in the Check tab with typing that is not saved yet
    When   the teacher copies it
    Then   the pending edit is flushed before the text is read
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)
    flushed: list[bool] = []
    monkeypatch.setattr(
        main_window._check_controller.page, "flush_pending_edit", lambda: flushed.append(True)
    )

    main_window._check_controller.on_copy_requested(CopyKind.RAW)

    assert flushed == [True]


@pytest.mark.gui
def test_a_new_result_joins_the_arrows(main_window, stored) -> None:
    """
    Given  two documents with results, the second one open
    When   a third document finishes
    Then   the header counts it and the next arrow reaches it
    """
    _a, b = stored("a.png", "b.png")
    main_window._check_controller.open_document(b)
    assert main_window._check_controller.page._position_label.text() == "2 of 2"

    _a, b, c = stored("a.png", "b.png", "c.png")
    main_window._check_controller.load(b)

    assert main_window._check_controller.page._position_label.text() == "2 of 3"

    main_window._check_controller.next_document()

    assert main_window._check_controller.current == c


@pytest.mark.gui
def test_the_header_follows_a_rename_of_the_row(main_window, stored) -> None:
    """
    Given  a document open in the Check tab
    When   its row is renamed
    Then   the tab label and the header show the new name
    """
    [a] = stored("a.png")
    main_window._check_controller.open_document(a)

    main_window._results_table.set_queue(
        [_queue_row(a, "renamed.png", ocr=StageState.DONE, has_text=True)]
    )
    main_window._check_controller.update_tab()

    assert main_window._tabs.tabText(1) == "Check \u2014 renamed.png"


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_checked_marks_the_corrections_and_shows_in_the_queue(main_window, stored) -> None:
    """
    Given  a document with a stored proofreading, open in the Check tab
    When   the teacher presses *Checked*
    Then   the corrections are marked as checked in the work, and the queue says so
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    source = Path(source_id)
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)

    main_window._check_controller.page._checked_btn.click()

    work = TeacherWorkStore().load(compute_file_hash(source))
    assert work is not None
    assert work.corrections is not None
    assert work.corrections.checked is True
    assert main_window._results_table.item(0, ResultsTable._COL_CHECKED).text() == "Checked"
    assert main_window._check_controller.page._checked_btn.text() == txt.CHECKED_BUTTON


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_checked_without_a_proofreading_marks_the_text_and_keeps_a_copy(
    main_window, stored
) -> None:
    """
    Given  a document that was read but not proofread, open in the Check tab
    When   the teacher presses *Checked*
    Then   the transcription is checked and stored as a copy, so the check names that text
    """
    [source_id] = stored("page.png", raw="raw text")
    source = Path(source_id)
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)

    main_window._check_controller.page._checked_btn.click()

    work = TeacherWorkStore().load(compute_file_hash(source))
    assert work is not None
    assert work.transcription is not None
    assert work.transcription.checked is True
    assert work.transcription.text == "raw text"
    assert main_window._results_table.item(0, ResultsTable._COL_CHECKED).text() == "Text checked"


@pytest.mark.gui
def test_showing_a_card_records_that_the_teacher_looked_at_it(main_window, stored) -> None:
    """
    Given  a document with one explained correction
    When   the Check tab opens it
    Then   the item the card shows is stored as looked at, and the footer counts it
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(4, 8, "have", "has"),),
                sentences=(),
                category="grammar",
                explanation='"she" is 3rd person singular.',
            ),
        )
    )
    [source_id] = stored(
        "page.png", raw="She have a dog.", corrected="She has a dog.", review=review
    )

    main_window._check_controller.open_document(source_id)

    work = TeacherWorkStore().load(compute_file_hash(Path(source_id)))
    assert work is not None
    assert work.corrections is not None
    assert work.corrections.looked_at == frozenset({"edit:1"})
    assert main_window._check_controller.page.body.panel._counts.text().endswith("1 of 1 looked at")


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_checked_is_its_own_undo(main_window, stored) -> None:
    """
    Given  a document marked as checked
    When   the teacher presses *Checked* again
    Then   the mark is gone from the work and from the queue
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    main_window._check_controller.page._checked_btn.click()

    main_window._check_controller.page._checked_btn.click()

    work = TeacherWorkStore().load(compute_file_hash(Path(source_id)))
    assert work is None or work.corrections is None or work.corrections.checked is False
    assert main_window._results_table.item(0, ResultsTable._COL_CHECKED).text() == ""


# ── The questions about what the teacher worked on ─────────────────────────


@pytest.mark.gui
@pytest.mark.parametrize("kind", ["raw", "corrected"])
def test_an_edit_that_cannot_be_stored_says_so(main_window, monkeypatch, stored, kind: str) -> None:
    """
    Given  a document open in the Check tab, whose work file was saved by a newer version
    When   the teacher's edit is committed
    Then   the teacher is told it was not saved, and nothing was written
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    shown: list[Notice] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))

    def refuse(self, *_args, **_kwargs):
        raise WorkTooNewError("newer")

    monkeypatch.setattr(TeacherWorkStore, "update", refuse)

    if kind == "raw":
        main_window._check_controller.on_raw_text_edited("raw edited")
    else:
        main_window._check_controller.on_corrected_text_edited("corr edited")

    assert len(shown) == 1
    assert shown[0].title == "Not saved"
    assert "newer version" in shown[0].body
    stored_work = TeacherWorkStore().load(compute_file_hash(Path(source_id)))
    # Looking at the first card was stored before the store started to refuse; the edit was not.
    assert stored_work is None or (
        stored_work.transcription is None
        and (stored_work.corrections is None or stored_work.corrections.text is None)
    )


@pytest.mark.gui
def test_checked_that_cannot_be_stored_says_so_and_the_button_goes_back(
    main_window, monkeypatch, stored
) -> None:
    """
    Given  a document open in the Check tab, whose work cannot be written
    When   the teacher presses *Checked*
    Then   the teacher is told, and the button shows the document as unchecked again
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    shown: list[Notice] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))

    def refuse(self, *_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(TeacherWorkStore, "update", refuse)

    main_window._check_controller.page._checked_btn.click()

    assert len(shown) == 1
    assert shown[0].title == "Not saved"
    assert main_window._check_controller.page._checked_btn.isChecked() is False
    assert main_window._check_controller.page._checked_btn.text() == txt.MARK_AS_CHECKED


@pytest.mark.gui
def test_checked_stores_a_typed_edit_first(main_window, stored) -> None:
    """
    Given  a document with text typed in the final text that is not stored yet
    When   the teacher presses *Checked*
    Then   the edit is stored before the mark, so the mark names the text the teacher sees
    """
    [source_id] = stored("page.png", raw="raw text", corrected="corr")
    main_window._results_table.selectRow(0)
    main_window._check_controller.open_document(source_id)
    main_window._check_controller.page.body.final_view.setPlainText("typed text")

    main_window._check_controller.page._checked_btn.click()

    work = TeacherWorkStore().load(compute_file_hash(Path(source_id)))
    assert work is not None and work.corrections is not None
    assert work.corrections.text == "typed text"
    assert work.corrections.checked is True


class _MemorySettings:
    """In-memory QSettings stand-in so tests never touch the user's real settings."""

    store: ClassVar[dict] = {}

    def __init__(self, *_args) -> None:
        pass

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, value) -> None:
        self.store[key] = value

    def remove(self, key) -> None:
        self.store.pop(key, None)


@pytest.mark.gui
def test_the_check_layout_survives_a_restart(qtbot, monkeypatch) -> None:
    """
    Given  a window whose Check tab the teacher sized
    When   the window is opened again
    Then   the panes and the card have the sizes they were left at
    """
    _MemorySettings.store = {}
    monkeypatch.setattr(main_window_module, "QSettings", _MemorySettings)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)
    first = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(first)

    first._check_controller.page.layout_changed.emit(
        CheckLayout(split=(300, 100, 400), card_split=(100, 100))
    )
    first._check_controller.page.layout_changed.emit(
        CheckLayout(split=(300, 500, 400), card_split=(200, 300))
    )

    assert "view/check_split" not in _MemorySettings.store  # nothing is written mid-drag

    qtbot.waitUntil(lambda: "view/check_split" in _MemorySettings.store)

    assert _MemorySettings.store["view/check_split"] == "300,500,400"
    assert _MemorySettings.store["view/check_card_split"] == "200,300"

    second = MainWindow(app_service=real_service(), worker_factory=_DummyWorker)
    qtbot.addWidget(second)

    assert second._check_controller.page.body._stored_split == (300, 500, 400)
    assert second._check_controller.page.body.panel._card_split == (200, 300)
