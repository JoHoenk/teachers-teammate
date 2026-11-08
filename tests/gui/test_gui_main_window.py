"""GUI tests for MainWindow orchestration using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import dataclasses
from pathlib import Path
import threading
from typing import Any, ClassVar

import pytest

from teachers_teammate.application.service import (
    ProcessingApplicationService,
    RequirementIssue,
    RunPreflight,
)
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    RECOMMENDED,
)
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._types import FileDoneEvent
from teachers_teammate.gui.settings import SettingsPage, SettingsResult
from teachers_teammate.gui.settings._page import (
    FOCUS_GPU,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_ONLINE_SERVICES,
)
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config

from ._gui_harness import install_scripted_settings_window


class _DummySignal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn):
        self._subs.append(fn)

    def emit(self, *args):
        for fn in self._subs:
            fn(*args)


class _DummyWorker:
    def __init__(self, _config, selected_source_paths=None, app_service=None) -> None:
        self.log_line = _DummySignal()
        self.file_started = _DummySignal()
        self.stage_started = _DummySignal()
        self.ocr_done = _DummySignal()
        self.file_done = _DummySignal()
        self.finished_with_code = _DummySignal()
        self.selected_source_paths = selected_source_paths or []
        self.app_service = app_service
        self.stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running


class _RecordingMessageBox:
    """Stand-in for ``QMessageBox`` that records the buttons and answers with one.

    ``clickedButton()`` returns the very object ``addButton`` returned, because the window
    compares the two by identity.
    """

    class Icon:
        Warning = "warning"
        Information = "information"

    class ButtonRole:
        AcceptRole = "accept"
        RejectRole = "reject"
        ActionRole = "action"

    last: ClassVar[_RecordingMessageBox | None] = None

    def __init__(self, _parent=None) -> None:
        self.buttons: list[str] = []
        self.default: str | None = None
        self.title = ""
        self.text = ""
        self.answer_index = 0
        _RecordingMessageBox.last = self

    def setIcon(self, _icon) -> None: ...
    def setCheckBox(self, _box) -> None: ...

    def setWindowTitle(self, title) -> None:
        self.title = title

    def setText(self, text) -> None:
        self.text = text

    def addButton(self, text, _role):
        self.buttons.append(text)
        return text

    def setDefaultButton(self, button) -> None:
        self.default = button

    def exec(self) -> int:
        return 0

    def clickedButton(self):
        return self.buttons[self.answer_index]


@pytest.fixture(autouse=True)
def _no_network_in_gui_tests(monkeypatch) -> None:
    """Prevent all MainWindow tests from making Ollama/network calls via the dependency guard."""
    monkeypatch.setattr(ProcessingApplicationService, "check_stage_requirements", lambda *_: [])
    monkeypatch.setattr(
        ProcessingApplicationService, "check_run_preflight", lambda *_: RunPreflight()
    )
    # The status row checks its services in the background; never probe a real server.
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_connection",
        lambda self, **_kw: (False, False, "offline (test)"),
    )


_REAL_CONFIRM_DATA_FLOW = MainWindow._confirm_data_flow_then


@pytest.fixture(autouse=True)
def _no_privacy_prompt(monkeypatch) -> None:
    """Skip the "documents will leave this computer" question: tests must never block on it.

    ``make_config`` proofreads with an online provider, so many runs would ask.  The tests
    of the question itself restore the real method through the ``confirm_box`` fixture.
    """
    monkeypatch.setattr(
        MainWindow, "_confirm_data_flow_then", lambda self, _config, on_ready: on_ready()
    )


def _wait_for(predicate, timeout_ms: int = 3000) -> None:
    """Spin the Qt event loop until *predicate* holds (background results arrive queued)."""
    from PySide6.QtTest import QTest  # noqa: PLC0415

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(10)
        waited += 10
    assert predicate(), "condition not reached in time"


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
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)
    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)
    return win


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
    seen: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: seen.append("warning"),
    )

    main_window._on_run()

    assert seen == ["warning"]
    assert main_window._worker is None


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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: [])
    seen: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda *_args: seen.append("info"),
    )

    main_window._on_run()
    qtbot.waitUntil(lambda: seen == ["info"], timeout=3000)

    assert main_window._worker is None


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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: ["a.png", "b.txt"])

    main_window._results_table.set_queue(["a.png", "b.txt"])
    main_window._results_table.selectRow(0)

    main_window._on_run()
    qtbot.waitUntil(lambda: main_window._worker is not None, timeout=3000)

    assert isinstance(main_window._worker, _DummyWorker)
    assert main_window._worker.selected_source_paths == ["a.png"]
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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: ["a.png"])

    main_window._results_table.set_queue(["a.png"])
    main_window._results_table.selectRow(0)

    main_window._on_run()
    qtbot.waitUntil(lambda: main_window._worker is not None, timeout=3000)

    assert main_window._worker.app_service is main_window._app_service


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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: ["a.png", "b.txt"])
    seen: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda *_args: seen.append("info"),
    )
    main_window._results_table.set_queue(["a.png", "b.txt"])

    main_window._on_run()
    qtbot.waitUntil(lambda: seen == ["info"], timeout=3000)

    assert main_window._worker is None


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
def test_main_window_stop_sets_worker_stop_event(main_window) -> None:
    """
    Given  a MainWindow with a running worker
    When   Stop is triggered
    Then   the worker stop_event is set and the stop button is disabled
    """
    worker = _DummyWorker(None)
    worker.start()
    main_window._worker = worker

    main_window._on_stop()

    assert worker.stop_event.is_set() is True
    assert main_window._stop_btn.isEnabled() is False


@pytest.mark.gui
def test_main_window_finished_updates_progress_and_tray(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow that already processed one file
    When   finished is signalled with success
    Then   buttons/progress are reset and a tray notification is sent
    """
    main_window._result_names = ["a.png"]
    main_window._progress.setRange(0, 2)
    seen: list[str] = []
    monkeypatch.setattr(main_window._tray, "showMessage", lambda *_args: seen.append("tray"))

    main_window._on_finished(0)

    assert main_window._run_btn.isEnabled() is True
    assert main_window._stop_btn.isEnabled() is False
    assert main_window._progress.value() == main_window._progress.maximum()
    assert "Done" in main_window._progress.format()
    assert seen == ["tray"]


@pytest.mark.gui
def test_main_window_file_done_records_txt_artifacts(main_window) -> None:
    """
    Given  a MainWindow receiving a file_done event for a text input
    When   _on_file_done is called with raw text output paths
    Then   the result row is recorded and selected paths expose the txt artifact
    """
    main_window._results_table.set_queue(["note.txt"], source_ids=["/tmp/note.txt"])

    main_window._on_file_done(
        FileDoneEvent(
            source_id="/tmp/note.txt",
            name="note.txt",
            ok=True,
            message="saved",
            ocr_s=0.3,
            correction_s=0.0,
            preview_img="",
            raw_txt="/tmp/note_ocr.txt",
            corr_txt="",
            eval_txt="",
        )
    )

    main_window._results_table.selectRow(0)
    _name, _preview, raw_txt, corr_txt, _eval = main_window._results_table.selected_paths()
    assert main_window._results_table.rowCount() == 1
    assert raw_txt.endswith("note_ocr.txt")
    assert corr_txt == ""


@pytest.mark.gui
def test_main_window_file_done_refreshes_preview_for_selected_row(main_window, monkeypatch) -> None:
    """
    Given  a selected result row for the same file name
    When   _on_file_done is called
    Then   preview is refreshed with that row's artifact paths
    """
    seen: list[tuple[str, str, str]] = []
    main_window._results_table.set_queue(["page.png"], source_ids=["/tmp/page.png"])
    main_window._results_table.selectRow(0)

    monkeypatch.setattr(
        main_window,
        "_on_preview_requested",
        lambda _pix, raw, corr, eval_txt: seen.append((raw, corr, eval_txt)),
    )

    main_window._on_file_done(
        FileDoneEvent(
            source_id="/tmp/page.png",
            name="page.png",
            ok=True,
            message="saved",
            ocr_s=0.3,
            correction_s=0.2,
            preview_img="",
            raw_txt="/tmp/page_ocr.txt",
            corr_txt="/tmp/page_correction.txt",
            eval_txt="/tmp/page_evaluation.txt",
        )
    )

    assert seen == [
        (
            "/tmp/page_ocr.txt",
            "/tmp/page_correction.txt",
            "/tmp/page_evaluation.txt",
        )
    ]


@pytest.mark.gui
def test_main_window_file_done_clears_stale_preprocessing_info(main_window, monkeypatch) -> None:
    """
    Given  a row that still holds preprocessing info from an earlier run
    When   _on_file_done reports a failed run and no valid state exists any more
    Then   the row's preprocessing info is reset instead of keeping the old original/steps
    """
    from teachers_teammate.gui._types import PreprocessInfo  # noqa: PLC0415

    source = "/tmp/page.png"
    main_window._results_table.set_queue(["page.png"], source_ids=[source])
    main_window._results_table.set_preprocessing(
        source, PreprocessInfo("/tmp/old.png", ("deskew",))
    )
    monkeypatch.setattr(main_window._app_service, "load_view", lambda *_a, **_k: None)

    main_window._on_file_done(
        FileDoneEvent(
            source_id=source,
            name="page.png",
            ok=False,
            message="",
            ocr_s=0.0,
            correction_s=0.0,
            preview_img="",
            raw_txt="",
            corr_txt="",
            eval_txt="",
        )
    )

    assert main_window._results_table.preprocessing_for_source(source) == PreprocessInfo()


@pytest.mark.gui
def test_main_window_file_done_refreshes_preview_without_selection(
    main_window, monkeypatch
) -> None:
    """
    Given  no selected row in the results table
    When   _on_file_done is called
    Then   preview is refreshed from the just-completed file payloads
    """
    seen: list[tuple[str, str, str]] = []
    main_window._results_table.set_queue(["page.png"])

    monkeypatch.setattr(
        main_window,
        "_on_preview_requested",
        lambda _pix, raw, corr, eval_txt: seen.append((raw, corr, eval_txt)),
    )

    main_window._on_file_done(
        FileDoneEvent(
            source_id="/tmp/page.png",
            name="page.png",
            ok=True,
            message="saved",
            ocr_s=0.3,
            correction_s=0.2,
            preview_img="",
            raw_txt="raw cached text",
            corr_txt="corr cached text",
            eval_txt="eval cached text",
        )
    )

    assert seen == [
        (
            "raw cached text",
            "corr cached text",
            "eval cached text",
        )
    ]


@pytest.mark.gui
def test_main_window_preview_requested_handles_empty_paths(main_window) -> None:
    """
    Given  empty preview paths for an in-progress row
    When   _on_preview_requested is called
    Then   no exception is raised and preview tab is selected
    """
    from PySide6.QtGui import QPixmap  # noqa: PLC0415

    main_window._on_preview_requested(QPixmap(), "", "", "")

    assert main_window._tabs.currentIndex() == 1


@pytest.mark.gui
def test_main_window_ocr_edit_persists_and_invalidates_downstream(
    main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    Given  a selected row mapped to a source file with cached correction/evaluation
    When   OCR text is edited
    Then   OCR text is persisted and correction/evaluation are invalidated
    """
    cfg = make_config(tmp_path)
    source = cfg.input_dir / "page.png"
    source.touch()
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)

    main_window._results_table.set_queue(["page.png"], source_ids=[str(source)])
    main_window._results_table.set_row_artifacts(
        str(source),
        "",
        "raw old",
        "corr old",
        "eval old",
    )
    main_window._results_table.selectRow(0)

    main_window._on_ocr_text_edited("raw edited")

    from teachers_teammate.infrastructure.state_repository import StateRepository  # noqa: PLC0415

    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.raw_text == "raw edited"
    assert state.correction_done is False
    assert state.correction_text == ""
    assert state.evaluation_done is False
    assert state.evaluation_text == ""

    _name, _preview, raw_txt, corr_txt, eval_txt = main_window._results_table.selected_paths()
    assert raw_txt == "raw edited"
    assert corr_txt == ""
    assert eval_txt == ""


@pytest.mark.gui
def test_main_window_correction_edit_persists_and_clears_evaluation(
    main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    Given  a selected row with OCR/correction/evaluation payloads
    When   correction text is edited
    Then   correction is persisted and evaluation is invalidated
    """
    cfg = make_config(tmp_path)
    source = cfg.input_dir / "page.png"
    source.touch()
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)

    main_window._results_table.set_queue(["page.png"], source_ids=[str(source)])
    main_window._results_table.set_row_artifacts(
        str(source),
        "",
        "raw text",
        "corr old",
        "eval old",
    )
    main_window._results_table.selectRow(0)

    main_window._on_correction_text_edited("corr edited")

    from teachers_teammate.infrastructure.state_repository import StateRepository  # noqa: PLC0415

    state = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state").load(source)
    assert state is not None
    assert state.correction_done is True
    assert state.correction_text == "corr edited"
    assert state.evaluation_done is False
    assert state.evaluation_text == ""

    _name, _preview, raw_txt, corr_txt, eval_txt = main_window._results_table.selected_paths()
    assert raw_txt == "raw text"
    assert corr_txt == "corr edited"
    assert eval_txt == ""


@pytest.mark.gui
def test_main_window_correction_edit_keeps_the_stored_warning_banner(
    main_window,
    monkeypatch,
    tmp_path: Path,
) -> None:
    """
    Given  a file whose proofreading fell back to corrections only and stored a warning
    When   the teacher edits the proofread text
    Then   the warning banner above the changes view is still shown after the reload
    """
    from teachers_teammate.infrastructure.state_repository import StateRepository  # noqa: PLC0415

    cfg = make_config(tmp_path)
    source = cfg.input_dir / "page.png"
    source.touch()
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    main_window._results_table.set_queue(["page.png"], source_ids=[str(source)])
    main_window._results_table.set_row_artifacts(str(source), "", "raw text", "corr old", "")
    main_window._results_table.selectRow(0)
    main_window._on_correction_text_edited("corr first")  # creates the state record
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    state = repo.load(source)
    assert state is not None
    repo.save(source, dataclasses.replace(state, correction_warning="No explanations."))

    main_window._on_correction_text_edited("corr edited")

    assert not main_window._preview._review_banner.isHidden()
    assert main_window._preview._review_banner.text() == "No explanations."


# ── Additional coverage for _check_llm_status and related ─────────────────


@pytest.mark.gui
@pytest.mark.use_case("Service_Availability_Check")
def test_main_window_check_llm_status_tesseract_ok(qtbot, monkeypatch) -> None:
    """
    Given  a MainWindow configured for tesseract OCR with no correction
    When   _check_llm_status() runs
    Then   the status label for tesseract is created with a green colour
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(win._app_service, "check_connection", lambda **_kw: (True, True, "✓ OK"))
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "tesseract",
        "correction_enabled": False,
        "evaluation_enabled": False,
    }

    win._check_llm_status()

    assert "tesseract" in win._status_labels
    lbl = win._status_labels["tesseract"]
    # green = available (the result arrives from a background task)
    _wait_for(lambda: "#27ae60" in lbl.styleSheet())


@pytest.mark.gui
def test_main_window_names_providers_as_they_declare_themselves(qtbot, monkeypatch) -> None:
    """
    Given  a provider whose PROVIDER_INFO declares a display name
    When   the status row names it
    Then   that name is used, as in the data-flow box, and engines keep their own names
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)
    monkeypatch.setattr(
        win._app_service, "get_provider_info", lambda p: {"display_name": "OpenAI (ChatGPT)"}
    )

    assert win._service_display_name("openai") == "OpenAI (ChatGPT)"
    assert win._service_display_name("tesseract") == "Tesseract"


@pytest.mark.gui
def test_main_window_check_llm_status_tesseract_missing(qtbot, monkeypatch) -> None:
    """
    Given  a MainWindow configured for tesseract OCR but tesseract is not on PATH
    When   _check_llm_status() runs
    Then   the status label for tesseract is red
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(
        win._app_service, "check_connection", lambda **_kw: (False, False, "✗ not found")
    )
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "tesseract",
        "correction_enabled": False,
        "evaluation_enabled": False,
    }

    win._check_llm_status()

    assert "tesseract" in win._status_labels
    lbl = win._status_labels["tesseract"]
    _wait_for(lambda: "#c0392b" in lbl.styleSheet())


@pytest.mark.gui
def test_main_window_on_llm_check_done_sets_label_green(main_window) -> None:
    """
    Given  an 'ollama' entry exists in _status_labels
    When   _on_llm_check_done(True, '') is called
    Then   the label turns green
    """
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    lbl = QLabel("● Ollama")
    main_window._status_labels["ollama"] = lbl
    main_window._on_llm_check_done(True, True, "ok")
    assert "#27ae60" in lbl.styleSheet()


@pytest.mark.gui
def test_main_window_on_finished_shows_error_format(main_window, monkeypatch) -> None:
    """
    Given  a MainWindow
    When   _on_finished(1) is called (non-zero exit code)
    Then   progress bar format shows 'error'
    """
    seen: list[str] = []
    monkeypatch.setattr(main_window._tray, "showMessage", lambda *_args: seen.append("tray"))
    main_window._result_names = []
    main_window._on_finished(1)
    assert "error" in main_window._progress.format().lower()


@pytest.mark.gui
def test_main_window_save_settings_logs_success(main_window, tmp_path, monkeypatch) -> None:
    """
    Given  a MainWindow with a config_path pointing to a writable location
    When   _save_settings() is called
    Then   the log shows 'Settings saved'
    """
    out = tmp_path / "ocr.toml"
    main_window._config_path = out

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
    main_window._config_path = Path("/nonexistent/dir/ocr.toml")
    seen: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda *_args: seen.append("warning"),
    )

    main_window._save_settings()

    assert seen == ["warning"]


@pytest.mark.gui
def test_main_window_on_preview_requested_loads_preview(main_window, tmp_path) -> None:
    """
    Given  a MainWindow and OCR/correction text payloads
    When   _on_preview_requested is called
    Then   the preview panel shows the OCR text and the preview tab is selected
    """
    from PySide6.QtGui import QPixmap  # noqa: PLC0415

    main_window._on_preview_requested(QPixmap(), "ocr content", "", "")

    assert main_window._tabs.currentIndex() == 1
    assert "ocr content" in main_window._preview._ocr_text.toPlainText()


@pytest.mark.gui
def test_main_window_on_file_started_updates_progress(main_window) -> None:
    """
    Given  a MainWindow with steps_per_file=1
    When   _on_file_started is called for file 2 of 3
    Then   progress value advances and format shows file index
    """
    main_window._steps_per_file = 1
    main_window._progress.setRange(0, 3)
    main_window._results_table.set_queue(["a.png", "b.png", "c.png"])

    main_window._on_file_started("b.png", "b.png", 2, 3)

    assert "2/3" in main_window._progress.format()


@pytest.mark.gui
def test_main_window_on_ocr_done_updates_progress_in_two_step(main_window) -> None:
    """
    Given  a MainWindow with steps_per_file=2
    When   _on_ocr_done is called for file 1 of 2
    Then   progress value advances to 1 and format mentions correcting
    """
    main_window._steps_per_file = 2
    main_window._progress.setRange(0, 4)
    main_window._results_table.set_queue(["a.png", "b.png"])

    main_window._on_ocr_done("a.png", "a.png", 1, 2)

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
    monkeypatch.setattr(main_window, "_check_llm_status", lambda: calls.append("check"))
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: calls.append("save"))
    monkeypatch.setattr(main_window, "_refresh_queue_from_inputs", lambda: calls.append("queue"))

    main_window.open_settings(SettingsPage.AI_MODELS)

    settings = main_window._config_panel.get_settings_dict()
    assert settings["ocr_model"] == "my-reader:1b"
    assert settings["hardware_profile"] == "16gb"
    assert calls == ["save", "check", "queue"]


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
    monkeypatch.setattr(main_window, "_check_llm_status", lambda: calls.append("check"))
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: calls.append("save"))

    main_window.open_settings(SettingsPage.AI_MODELS)

    assert main_window._config_panel.get_settings_dict() == before
    assert calls == []


@pytest.mark.gui
def test_ok_in_settings_persists_a_profile_change_to_ocr_toml(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  the Settings window closes with OK after "Where the AI runs" was set to 16 GB
    When   open_settings() returns
    Then   the value is stored in the config panel and saved to ocr.toml
    """
    monkeypatch.setattr(
        ProcessingApplicationService,
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
    monkeypatch.setattr(main_window_module.QMessageBox, "question", lambda *a, **k: 1 / 0)

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
    monkeypatch.setattr(main_window, "_apply_display_anonymized", shown.append)
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
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    from teachers_teammate.gui import _addon_installer_dialog  # noqa: PLC0415

    threads: list[Any] = []

    class _FakePull(QObject):
        progress = Signal(str, float, float)
        finished_ok = Signal(str)
        finished_err = Signal(str)

        def __init__(self, url: str, model: str) -> None:
            super().__init__()
            self.url, self.model = url, model
            threads.append(self)

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:
            return True

        def request_abort(self) -> None:
            pass

    monkeypatch.setattr(_addon_installer_dialog, "_OllamaPullThread", _FakePull)
    manager = main_window_module.OllamaDownloadManager()
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
def test_aborting_a_pull_drops_the_queue(qtbot, monkeypatch) -> None:
    """
    Given  a running pull with two more models queued
    When   it is aborted
    Then   nothing else is queued, so the aborted round does not go on with the next model
    """
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    from teachers_teammate.gui import _addon_installer_dialog  # noqa: PLC0415

    class _FakePull(QObject):
        progress = Signal(str, float, float)
        finished_ok = Signal(str)
        finished_err = Signal(str)

        def __init__(self, url: str, model: str) -> None:
            super().__init__()

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:
            return True

        def request_abort(self) -> None:
            pass

    monkeypatch.setattr(_addon_installer_dialog, "_OllamaPullThread", _FakePull)
    manager = main_window_module.OllamaDownloadManager()
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
    first = scripted(main_window._settings_context(), main_window, page=SettingsPage.AI_MODELS)
    main_window._settings_window = first
    created_before = len(scripted.instances)

    main_window.open_settings(SettingsPage.ADDONS, focus=FOCUS_GPU, preselect=["x"])

    assert len(scripted.instances) == created_before
    assert first.shown == [(SettingsPage.ADDONS, FOCUS_GPU, ["x"])]


@pytest.mark.gui
def test_the_settings_window_gets_the_current_state(main_window, monkeypatch) -> None:
    """
    Given  a main window with its own pull manager and a changed output folder
    When   Settings is opened
    Then   the window is built from a copy of the panel's settings, the language, the anonymizer
           rules, the acknowledgements and the window's pull manager
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)
    main_window._config_panel.update_settings({"output_dir": "/tmp/out"})

    main_window.open_settings(SettingsPage.PRIVACY)

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    context = window.context
    assert context.settings["output_dir"] == "/tmp/out"
    assert context.language == main_window._config_panel.lookup_language()
    assert context.pull_manager is main_window._pull_manager
    assert context.app_service is main_window._app_service
    assert context.anonymizer_config == main_window._config_panel.build_anonymizer_config()


# ── menu bar ───────────────────────────────────────────────────────────────────


def _menu(window: MainWindow, title: str):
    return next(a.menu() for a in window.menuBar().actions() if a.text() == title)


@pytest.mark.gui
def test_menu_bar_has_file_settings_and_help(main_window) -> None:
    """
    Given  the main window
    When   its menu bar is read
    Then   it has File, Settings and Help, and no Advanced Settings menu
    """
    titles = [a.text() for a in main_window.menuBar().actions()]

    assert titles == ["File", "Settings", "Help"]


@pytest.mark.gui
def test_settings_menu_lists_the_pages_then_all_settings(main_window) -> None:
    """
    Given  the main window
    When   the Settings menu is read
    Then   it lists the five pages by name, a separator and "All settings…" on Ctrl+,
    """
    menu = _menu(main_window, "Settings")

    entries = [a.text() for a in menu.actions() if not a.isSeparator()]

    assert entries == [
        "AI models\u2026",
        "Where the AI runs\u2026",
        "Scan quality\u2026",
        "Privacy\u2026",
        "Add-ons\u2026",
        "All settings\u2026",
    ]
    all_settings = menu.actions()[-1]
    assert all_settings.shortcut().toString() == "Ctrl+,"


@pytest.mark.gui
@pytest.mark.parametrize("page", list(SettingsPage))
def test_each_settings_menu_entry_opens_its_page(main_window, monkeypatch, page) -> None:
    """
    Given  the Settings menu
    When   the entry of a page is triggered
    Then   the Settings window opens on exactly that page
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._settings_actions[page].trigger()

    assert [w.page for w in scripted.instances] == [page]


@pytest.mark.gui
def test_all_settings_opens_the_page_used_last(main_window, monkeypatch) -> None:
    """
    Given  the page used last was Privacy
    When   "All settings…" is triggered
    Then   the Settings window opens on Privacy
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)
    monkeypatch.setattr(main_window_module, "last_page", lambda: SettingsPage.PRIVACY)

    main_window._all_settings_action.trigger()

    assert [w.page for w in scripted.instances] == [SettingsPage.PRIVACY]


@pytest.mark.gui
def test_file_menu_opens_documents_and_exports(main_window, monkeypatch) -> None:
    """
    Given  the File menu
    When   "Open Folder…" and "Open Files…" are triggered
    Then   the panel's folder and file selection are started, and Export ▸ lists the export kinds
    """
    calls: list[str] = []
    monkeypatch.setattr(
        main_window._config_panel, "_on_select_folder", lambda: calls.append("folder")
    )
    monkeypatch.setattr(
        main_window._config_panel, "_on_select_files", lambda: calls.append("files")
    )
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

    main_window._set_export_enabled(True)

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

    main_window._config_panel._text_enabled.setChecked(True)

    assert calls == ["save", "queue"]


@pytest.mark.gui
def test_the_preview_opens_on_top_of_the_settings_window(main_window, monkeypatch) -> None:
    """
    Given  the Settings window asks for a preview of the image preparation
    When   the main window shows its result
    Then   the dialogs belong to the Settings window, so they cannot open behind it
    """
    from PySide6.QtWidgets import QDialog, QWidget  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    owner = QWidget()
    created: list[object] = []

    class _FakeDialog(QDialog):
        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            created.append(parent)

        def exec(self) -> int:
            return 0

    monkeypatch.setattr(main_window_module, "QDialog", _FakeDialog)
    monkeypatch.setattr(main_window_module, "load_pixmap", lambda _p: main_window_module.QPixmap())
    owner.show()

    main_window._on_preprocess_preview_done(
        Path("a.png"), Path("b.png"), ["deskew"], "", OcrConfig(), Path("/tmp/none"), parent=owner
    )

    assert created == [owner]


@pytest.mark.gui
def test_main_window_check_llm_status_paddleocr_ok(qtbot, monkeypatch) -> None:
    """
    Given  ocr_engine is paddleocr and paddleocr is importable
    When   _check_llm_status is called
    Then   paddleocr status label is set to green
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(win._app_service, "check_connection", lambda **_kw: (True, True, "✓ OK"))
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "paddleocr",
        "correction_enabled": False,
        "evaluation_enabled": False,
        "ollama_url": "http://localhost:11434",
    }

    win._check_llm_status()

    assert "paddleocr" in win._status_labels


@pytest.mark.gui
def test_main_window_load_toml_if_present_loads_values(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  a valid TOML config file exists at the resolved config path
    When   MainWindow is initialised (calls _load_toml_if_present internally)
    Then   _config_path is set to the resolved path
    """
    config_path = tmp_path / "ocr.toml"
    config_path.write_text('language = "German"\n', encoding="utf-8")

    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(
        "teachers_teammate.application.service.ProcessingApplicationService.resolve_config_path",
        lambda self, _: config_path,
    )
    monkeypatch.setattr(
        "teachers_teammate.gui._main_window.load_config_file",
        lambda _path: {"language": "German"},
    )
    win = MainWindow()
    qtbot.addWidget(win)
    assert win._config_path == config_path


@pytest.mark.gui
def test_main_window_load_toml_if_present_no_path(qtbot, monkeypatch) -> None:
    """
    Given  resolve_config_path returns None
    When   _load_toml_if_present is called
    Then   method returns immediately without setting _config_path
    """
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(
        "teachers_teammate.application.service.ProcessingApplicationService.resolve_config_path",
        lambda self, _: None,
    )
    win._config_path = None
    win._load_toml_if_present()
    assert win._config_path is None


@pytest.mark.gui
def test_main_window_load_toml_if_present_exception_logged(qtbot, monkeypatch, tmp_path) -> None:
    """
    Given  load_config_file raises an exception
    When   _load_toml_if_present is called
    Then   the warning is logged and no crash occurs
    """
    config_path = tmp_path / "bad.toml"
    config_path.write_text("bad content", encoding="utf-8")

    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(
        "teachers_teammate.application.service.ProcessingApplicationService.resolve_config_path",
        lambda self, _: config_path,
    )

    def _raise(_):
        raise ValueError("parse error")

    monkeypatch.setattr(
        "teachers_teammate.gui._main_window.load_config_file",
        _raise,
    )
    win._load_toml_if_present()  # should not raise


@pytest.mark.gui
def test_main_window_check_llm_status_openai_with_env(qtbot, monkeypatch) -> None:
    """
    Given  correction_provider is openai and OPENAI_API_KEY is set
    When   _check_llm_status is called
    Then   openai label is added to _status_labels
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    monkeypatch.setattr(win._app_service, "check_connection", lambda **_kw: (True, True, "✓ OK"))
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "tesseract",
        "correction_enabled": True,
        "correction_provider": "openai",
        "ollama_url": "http://localhost:11434",
    }

    win._check_llm_status()

    assert "openai" in win._status_labels


@pytest.mark.gui
def test_main_window_check_llm_status_ollama_checks_in_background(qtbot, monkeypatch) -> None:
    """
    Given  ocr_engine is ollama
    When   _check_llm_status is called
    Then   the server is checked in the background and the label turns green on the result
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    calls: list[dict] = []
    main_thread = threading.get_ident()

    def _check(**kwargs):
        calls.append({**kwargs, "on_gui_thread": threading.get_ident() == main_thread})
        return True, True, "✓ OK"

    monkeypatch.setattr(win._app_service, "check_connection", _check)
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "ollama",
        "correction_enabled": False,
        "evaluation_enabled": False,
        "ollama_url": "http://localhost:11434",
    }

    win._check_llm_status()

    _wait_for(lambda: "#27ae60" in win._status_labels["ollama"].styleSheet())
    assert calls[-1]["engine"] == "ollama"
    assert calls[-1]["url"] == "http://localhost:11434"
    assert calls[-1]["on_gui_thread"] is False


@pytest.mark.gui
def test_slow_status_checks_never_run_on_the_gui_thread(qtbot, monkeypatch) -> None:
    """
    Given  a paddleocr engine whose availability check blocks (imports paddle, probes CUDA)
    When   _check_llm_status is called
    Then   it returns at once; the label only changes once the check was released
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)
    release = threading.Event()

    def _slow_check(**_kwargs):
        assert release.wait(timeout=5), "test never released the check"
        return True, True, "✓ OK"

    monkeypatch.setattr(win._app_service, "check_connection", _slow_check)
    win._config_panel.get_settings_dict = lambda: {  # ty: ignore[invalid-assignment]  # test monkeypatches the method with a stub lambda
        "ocr_engine": "paddleocr",
        "correction_enabled": False,
        "evaluation_enabled": False,
    }

    win._check_llm_status()

    lbl = win._status_labels["paddleocr"]
    assert "#27ae60" not in lbl.styleSheet()
    release.set()
    _wait_for(lambda: "#27ae60" in lbl.styleSheet())


@pytest.mark.gui
def test_status_check_uses_the_service_model_selection(qtbot, monkeypatch) -> None:
    """
    Given  Ollama does OCR with one model and proofreading with another
    When   the Ollama status check starts
    Then   the first model the service lists as configured is the one that is checked
    """
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)
    seen: list[str] = []
    monkeypatch.setattr(
        win._app_service,
        "check_connection",
        lambda **kw: seen.append(kw["model"]) or (True, True, "✓"),
    )
    monkeypatch.setattr(
        win._app_service, "configured_ollama_models", lambda _config: ["ocr:1", "fix:2"]
    )

    win._start_ollama_check()

    _wait_for(lambda: seen)
    assert seen == ["ocr:1"]


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
    When   _on_preprocess_preview_requested is called
    Then   method returns without error and no background thread is started
    """
    from unittest.mock import patch  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))
    with patch(
        "teachers_teammate.gui._main_window.QFileDialog.getOpenFileName", return_value=("", "")
    ):
        main_window._on_preprocess_preview_requested(
            OcrConfig(preprocess_method="none")
        )  # should not raise
    assert main_window._preprocess_preview_thread is None


@pytest.mark.gui
def test_main_window_on_preprocess_preview_preprocess_error(
    main_window, monkeypatch, tmp_path, qtbot
) -> None:
    """
    Given  preprocess_preview raises an exception
    When   _on_preprocess_preview_requested runs its background thread to completion
    Then   a warning dialog is shown and no crash occurs

    preprocess_preview now runs off the GUI thread (see _PreprocessPreviewThread), so
    the warning only appears once the thread's done signal has been delivered.
    """
    from unittest.mock import patch  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"fake")
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))
    monkeypatch.setattr(
        main_window._app_service,
        "preprocess_preview",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad file")),
    )
    with (
        patch("teachers_teammate.gui._main_window.QMessageBox") as mock_mb,
    ):
        main_window._on_preprocess_preview_requested(OcrConfig(preprocess_method="none"))
        qtbot.waitUntil(lambda: mock_mb.warning.called, timeout=2000)
    mock_mb.warning.assert_called_once()


@pytest.mark.gui
def test_main_window_on_preprocess_preview_ignores_concurrent_click(
    main_window, monkeypatch, tmp_path, qtbot
) -> None:
    """
    Given  a preprocessing preview already running in the background
    When   _on_preprocess_preview_requested is called again before it finishes
    Then   the second call is ignored: no second thread replaces the first, and the
           error dialog for the one background run appears exactly once
    """
    import time  # noqa: PLC0415
    from unittest.mock import patch  # noqa: PLC0415

    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"fake")
    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))

    call_count = {"n": 0}

    def _slow_failing_preview(*_args, **_kwargs):
        call_count["n"] += 1
        time.sleep(0.3)
        raise ValueError("bad file")

    monkeypatch.setattr(main_window._app_service, "preprocess_preview", _slow_failing_preview)

    with patch("teachers_teammate.gui._main_window.QMessageBox") as mock_mb:
        main_window._on_preprocess_preview_requested(OcrConfig(preprocess_method="none"))
        first_thread = main_window._preprocess_preview_thread
        main_window._on_preprocess_preview_requested(OcrConfig(preprocess_method="none"))
        assert main_window._preprocess_preview_thread is first_thread

        qtbot.waitUntil(lambda: mock_mb.warning.called, timeout=2000)
    assert call_count["n"] == 1
    mock_mb.warning.assert_called_once()


# ── _on_stage_run_requested branches ─────────────────────────────────────────


@pytest.mark.gui
def test_stage_run_warns_on_config_error(main_window, monkeypatch) -> None:
    """
    Given  the config panel cannot build a Config
    When   _on_stage_run_requested is invoked
    Then   a warning dialog is shown and no worker starts
    """
    from unittest.mock import patch  # noqa: PLC0415

    monkeypatch.setattr(
        main_window._config_panel,
        "to_config",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))

    with patch("teachers_teammate.gui._main_window.QMessageBox") as mb:
        main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")

    mb.warning.assert_called_once()
    assert started == []


@pytest.mark.gui
def test_stage_run_force_runs_correction_when_checkbox_disabled(main_window, monkeypatch) -> None:
    """
    Given  correction is disabled in the config
    When   _on_stage_run_requested is invoked for the correction stage (right-click)
    Then   the stage runs anyway with a config where correction_enabled is forced True
    """
    cfg = make_config(Path("/tmp"), correction_enabled=False)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    captured: list = []
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda config, **k: captured.append(config) or (["/tmp/a.png"], [], False),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))

    main_window._on_stage_run_requested(["/tmp/a.png"], "correction")
    _wait_for(lambda: bool(started))

    assert captured and captured[0].correction_enabled is True
    assert started  # worker started despite the checkbox being off


@pytest.mark.gui
def test_stage_run_force_runs_evaluation_when_checkbox_disabled(main_window, monkeypatch) -> None:
    """
    Given  evaluation is disabled in the config
    When   _on_stage_run_requested is invoked for the evaluation stage (right-click)
    Then   the stage runs with evaluation_enabled and correction_enabled forced True
    """
    cfg = make_config(Path("/tmp"), evaluation_enabled=False, correction_enabled=False)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    captured: list = []
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda config, **k: captured.append(config) or (["/tmp/a.png"], [], False),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))

    main_window._on_stage_run_requested(["/tmp/a.png"], "evaluation")
    _wait_for(lambda: bool(started))

    assert captured
    assert captured[0].evaluation_enabled is True
    assert captured[0].correction_enabled is True
    assert started


@pytest.mark.gui
def test_stage_run_informs_when_no_files(main_window, monkeypatch) -> None:
    """
    Given  invalidate_from_stage reports no files
    When   _on_stage_run_requested is invoked for the OCR stage
    Then   a 'No files found' dialog is shown and no worker starts
    """
    from unittest.mock import patch  # noqa: PLC0415

    cfg = make_config(Path("/tmp"))
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda *a, **k: ([], [], True),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))

    with patch("teachers_teammate.gui._main_window.QMessageBox") as mb:
        main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")
        _wait_for(lambda: mb.information.called)

    mb.information.assert_called_once()
    assert started == []


@pytest.mark.gui
def test_stage_run_warns_on_invalidation_failure(main_window, monkeypatch) -> None:
    """
    Given  invalidate_from_stage reports failures
    When   _on_stage_run_requested is invoked
    Then   a warning dialog is shown and no worker starts
    """
    from unittest.mock import patch  # noqa: PLC0415

    cfg = make_config(Path("/tmp"))
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda *a, **k: (["/tmp/a.png"], ["a.png: locked"], False),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))

    with patch("teachers_teammate.gui._main_window.QMessageBox") as mb:
        main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")
        _wait_for(lambda: mb.warning.called)

    mb.warning.assert_called_once()
    assert started == []


@pytest.mark.gui
def test_stage_run_starts_worker_on_success(main_window, monkeypatch) -> None:
    """
    Given  invalidate_from_stage returns runnable source ids with no failures
    When   _on_stage_run_requested is invoked
    Then   the worker is started with those source ids
    """
    cfg = make_config(Path("/tmp"), correction_enabled=False)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda *a, **k: (["/tmp/a.png"], [], False),
    )
    started: list = []
    monkeypatch.setattr(
        main_window, "_start_worker", lambda config, ids: started.append((config, ids))
    )

    main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")
    _wait_for(lambda: bool(started))

    assert started == [(cfg, ["/tmp/a.png"])]


# ── Credentials loading ────────────────────────────────────────────────────


@pytest.mark.gui
def test_main_window_loads_credentials_on_startup(qtbot, monkeypatch, credential_file) -> None:
    """
    Given  a stored API key for a service (saved by an earlier session)
    When   MainWindow is created
    Then   the key is injected into os.environ (when not already set)
    """
    import os  # noqa: PLC0415

    credential_file["credentials/OPENAI_API_KEY"] = "test-value-from-settings"
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert os.environ.get("OPENAI_API_KEY") == "test-value-from-settings"
    os.environ.pop("OPENAI_API_KEY", None)


@pytest.mark.gui
def test_main_window_env_var_takes_priority_over_stored_credential(
    qtbot, monkeypatch, credential_file
) -> None:
    """
    Given  an env var is already set AND QSettings has a different value for the same key
    When   MainWindow is created
    Then   the existing env var is NOT overwritten by the stored value
    """
    import os  # noqa: PLC0415

    credential_file["credentials/OPENAI_API_KEY"] = "stored-value"
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setenv("OPENAI_API_KEY", "env-value")

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert os.environ["OPENAI_API_KEY"] == "env-value"


# ── first-start model choice (AC12/AC13) ──────────────────────────────────


def _make_first_start_window(qtbot, monkeypatch, tmp_path: Path, detection) -> MainWindow:
    """Create a MainWindow that takes the first-start path (no ocr.toml)."""
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(
        ProcessingApplicationService, "resolve_config_path", lambda self, explicit: None
    )
    monkeypatch.setattr(
        ProcessingApplicationService,
        "default_config_path",
        lambda self: tmp_path / "ocr.toml",
    )
    monkeypatch.setattr(
        ProcessingApplicationService, "detect_hardware", lambda self, url, **_kw: detection
    )
    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)
    return win


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_first_start_selects_best_models_for_profile(qtbot, monkeypatch, tmp_path: Path) -> None:
    """
    Given  a first start (no ocr.toml) on a detected 16 GB machine
    When   background detection finishes
    Then   the models are set to the 16 GB BEST picks, the choice is logged,
           and settings are saved (AC12)
    """
    from teachers_teammate.domain.model_catalog import DetectionReason, HardwareDetection  # noqa: PLC0415

    detection = HardwareDetection(
        profile="16gb", reason=DetectionReason.GPU_DETECTED, gpu_name="RTX 4090", memory_gb=24.0
    )
    win = _make_first_start_window(qtbot, monkeypatch, tmp_path, detection)
    qtbot.waitUntil(lambda: "First start" in win._log.toPlainText(), timeout=5000)

    settings = win._config_panel.get_settings_dict()
    best_16gb = RECOMMENDED["general"]["16gb"][0]  # not the default: that is the 8 GB pick
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
    from teachers_teammate.domain.model_catalog import DetectionReason, HardwareDetection  # noqa: PLC0415

    config_path = tmp_path / "ocr.toml"
    config_path.write_text(
        'ocr_model = "my-model:latest"\n'
        'correction_model = "my-writer:latest"\n'
        'evaluate_model = "my-writer:latest"\n',
        encoding="utf-8",
    )
    detection = HardwareDetection(
        profile="16gb", reason=DetectionReason.GPU_DETECTED, gpu_name="RTX 4090", memory_gb=24.0
    )
    detect_calls: list = []
    monkeypatch.setattr(main_window_module, "UpdateCheckThread", _DummyUpdateThread)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(
        ProcessingApplicationService, "resolve_config_path", lambda self, explicit: config_path
    )
    monkeypatch.setattr(
        ProcessingApplicationService,
        "default_config_path",
        lambda self: config_path,
    )

    def _spy_detect(self, url, **_kw):
        detect_calls.append(url)
        return detection

    monkeypatch.setattr(ProcessingApplicationService, "detect_hardware", _spy_detect)
    win = MainWindow(worker_factory=_DummyWorker)
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
    from teachers_teammate.domain.model_catalog import DetectionReason, HardwareDetection  # noqa: PLC0415

    monkeypatch.setitem(DEFAULTS, "correction_provider", "openai")
    detection = HardwareDetection(
        profile="16gb", reason=DetectionReason.GPU_DETECTED, gpu_name="RTX 4090", memory_gb=24.0
    )
    win = _make_first_start_window(qtbot, monkeypatch, tmp_path, detection)
    qtbot.waitUntil(lambda: "First start" in win._log.toPlainText(), timeout=5000)
    settings = win._config_panel.get_settings_dict()
    assert settings["correction_model"] == DEFAULTS["correction_model"]
    assert settings["ocr_model"] == DEFAULT_OCR_MODEL  # ocr provider still updated


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
        ProcessingApplicationService,
        "on_ollama_models_changed",
        lambda self, url: evicted.append(url),
    )
    main_window._pull_manager._current_url = "http://gpu-box:11434"
    main_window._on_pull_finished("qwen3-vl:8b")
    assert evicted == ["http://gpu-box:11434"]


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_missing_model_box_opens_addons_with_models_preselected(
    qtbot, main_window, monkeypatch
) -> None:
    """
    Given  a run whose configured models are not installed
    When   the user chooses "Open Add-ons…" in the warning
    Then   Settings opens on Add-ons, on the local models, with exactly the missing models
           preselected
    """
    from unittest.mock import MagicMock  # noqa: PLC0415

    class _Box:
        class Icon:
            Warning = None

        class ButtonRole:
            RejectRole = None
            ActionRole = None

        def __init__(self, *_a, **_k) -> None:
            self._buttons: list[object] = []

        def setWindowTitle(self, _t: str) -> None: ...
        def setText(self, _t: str) -> None: ...
        def setIcon(self, _i: object) -> None: ...

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            self._buttons.append(button)
            return button

        def exec(self) -> int:
            return 0

        def clickedButton(self) -> object:
            return self._buttons[-1]  # "Open Add-ons…"

    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: MagicMock())
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(missing_models=("qwen3-vl:8b", "qwen3.5:9b")),
    )
    monkeypatch.setattr(main_window_module, "QMessageBox", _Box)
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._on_run()
    qtbot.waitUntil(lambda: bool(scripted.instances), timeout=3000)

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert window.page is SettingsPage.ADDONS
    assert window.focus == FOCUS_LOCAL_MODELS
    assert window.preselect == ["qwen3-vl:8b", "qwen3.5:9b"]


@pytest.mark.use_case("Runtime_Addon_Installation")
def test_stats_gpu_button_opens_the_gpu_addons_tab(main_window, monkeypatch) -> None:
    """
    Given  the GPU button of the system-stats widget
    When   it requests Add-ons
    Then   Settings opens on Add-ons, on the performance-monitoring tab
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._stats.open_downloads_requested.emit()

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert (window.page, window.focus) == (SettingsPage.ADDONS, FOCUS_GPU)


@pytest.mark.gui
def test_the_panels_dependency_guard_opens_addons_with_its_focus(main_window, monkeypatch) -> None:
    """
    Given  the config panel's dependency guard asking for the name-detection add-on
    When   the panel emits open_downloads_requested
    Then   Settings opens on Add-ons with that focus and entries
    """
    scripted = install_scripted_settings_window(monkeypatch, accepted=False)

    main_window._config_panel.open_downloads_requested.emit(
        FOCUS_ONLINE_SERVICES, ["provider:mistral"]
    )

    assert len(scripted.instances) == 1
    window = scripted.instances[0]
    assert window.page is SettingsPage.ADDONS
    assert window.focus == FOCUS_ONLINE_SERVICES
    assert window.preselect == ["provider:mistral"]


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
    monkeypatch.setattr(
        ProcessingApplicationService, "default_config_path", lambda self: tmp_path / "ocr.toml"
    )
    waited: list[bool] = []
    monkeypatch.setattr(
        main_window_module, "wait_for_background_tasks", lambda *a, **k: waited.append(True)
    )
    main_window.close()
    assert waited == [True]


@pytest.mark.gui
def test_main_window_file_done_keeps_the_reported_text_format_without_state(
    main_window, monkeypatch
) -> None:
    """
    Given  a finished file whose Markdown format the pipeline reported
    When   _on_file_done runs and the persisted state cannot be read back
    Then   the row keeps the Markdown format instead of falling back to plain
    """
    source = "/tmp/page.png"
    main_window._results_table.set_queue(["page.png"], source_ids=[source])
    monkeypatch.setattr(main_window._app_service, "load_view", lambda *_a, **_k: None)

    main_window._on_file_done(
        FileDoneEvent(
            source_id=source,
            name="page.png",
            ok=True,
            message="",
            ocr_s=0.0,
            correction_s=0.0,
            raw_txt="**x**",
            text_format="markdown",
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
    main_window._results_table.set_queue(["rerun.png"], source_ids=[source])
    main_window._results_table.set_text_format(source, "markdown")

    main_window._on_file_started(source, "rerun.png", 1, 1)

    assert main_window._results_table.text_format_for_source(source) == "markdown"


# ── Re-run only the requested stage ──────────────────────────────────────────


def _capture_stage_run(main_window, monkeypatch, cfg, stage: str):
    """Trigger a right-click stage run and return the config the worker would get."""
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda config, **k: (["/tmp/a.png"], [], False),
    )
    started: list = []
    monkeypatch.setattr(main_window, "_start_worker", lambda *a, **k: started.append(a))
    main_window._on_stage_run_requested(["/tmp/a.png"], stage)
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
    assert main_window._steps_per_file == 1


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
    Then   a clear "Ollama is not running" message is shown, no worker starts and
           the Run button is usable again
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(ollama_unreachable="✗ Cannot connect to Ollama"),
    )
    monkeypatch.setattr(main_window, "_start_ollama_check", lambda: None)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: shown.append((title, text)),
    )

    main_window._on_run()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert shown[0][0] == "Ollama is not running"
    assert "ollama serve" in shown[0][1]
    assert main_window._worker is None
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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: ["a.png"])
    main_window._results_table.set_queue(["a.png"])
    main_window._results_table.selectRow(0)
    release = threading.Event()

    def _slow_preflight(self, config):
        assert release.wait(timeout=5), "test never released the check"
        return RunPreflight()

    monkeypatch.setattr(ProcessingApplicationService, "check_run_preflight", _slow_preflight)

    main_window._on_run()

    assert main_window._worker is None  # still checking
    assert main_window._run_btn.isEnabled() is False
    release.set()
    qtbot.waitUntil(lambda: main_window._worker is not None, timeout=3000)


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
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(
            stage="correction",
            stage_issues=(RequirementIssue("Model is not downloaded.", True),),
        ),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        DependencyGuardDialog, "exec", lambda self: shown.append(self.windowTitle())
    )

    main_window._on_run()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert shown == ["Correction — Missing Requirements"]
    assert main_window._worker is None


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
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(memory_warning="qwen3.5:9b needs about 7 GB."),
    )
    monkeypatch.setattr(main_window_module, "QMessageBox", _RecordingMessageBox)
    main_window._results_table.set_queue(["a.png"], [str(tmp_path / "a.png")])
    main_window._results_table.selectRow(0)

    _RecordingMessageBox.last = None
    main_window._on_run()
    qtbot.waitUntil(lambda: main_window._worker is not None, timeout=3000)

    box = _RecordingMessageBox.last
    assert box is not None
    assert box.title == "Not enough free memory"
    assert box.text == "qwen3.5:9b needs about 7 GB."
    assert box.buttons == ["Run anyway", "Cancel"]
    assert box.default == "Cancel"  # Enter must not start a run that could swap


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
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(memory_warning="qwen3.5:9b needs about 7 GB."),
    )
    monkeypatch.setattr(main_window_module, "QMessageBox", _RecordingMessageBox)
    main_window._results_table.set_queue(["a.png"], [str(tmp_path / "a.png")])
    main_window._results_table.selectRow(0)

    _RecordingMessageBox.last = None

    class _CancellingBox(_RecordingMessageBox):
        """Answers the question with its second button, "Cancel"."""

        def __init__(self, _parent=None) -> None:
            super().__init__(_parent)
            self.answer_index = 1

    monkeypatch.setattr(main_window_module, "QMessageBox", _CancellingBox)

    def _cancel_shown() -> bool:
        box = _RecordingMessageBox.last
        return box is not None and box.buttons == ["Run anyway", "Cancel"]

    main_window._on_run()
    qtbot.waitUntil(_cancel_shown, timeout=3000)
    assert main_window._worker is None
    assert main_window._run_btn.isEnabled() is True


# ── Where the documents are processed ────────────────────────────────────────


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


def _online_proofreading_config(tmp_path: Path):
    return make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        correction_provider="openai",
    )


class _ConfirmBox:
    """Stand-in for the confirmation QMessageBox: records the text, clicks a chosen button."""

    clicks = "Continue"
    remember = False
    texts: ClassVar[list[str]] = []

    class Icon:
        Warning = 0

    class ButtonRole:
        AcceptRole = 0
        RejectRole = 1

    def __init__(self, _parent=None) -> None:
        self._buttons: dict[str, object] = {}
        self._checkbox = None
        self._clicked = None

    def setIcon(self, _icon) -> None:
        pass

    def setWindowTitle(self, _title) -> None:
        pass

    def setText(self, text) -> None:
        self.texts.append(text)

    def setCheckBox(self, box) -> None:
        self._checkbox = box
        box.setChecked(type(self).remember)

    def addButton(self, label, _role):
        button = object()
        self._buttons[label] = button
        return button

    def setDefaultButton(self, _button) -> None:
        pass

    def exec(self) -> None:
        self._clicked = self._buttons[type(self).clicks]

    def clickedButton(self):
        return self._clicked


@pytest.fixture
def confirm_box(monkeypatch):
    """Replace the confirmation dialog and settings store; return the dialog class."""
    _ConfirmBox.clicks, _ConfirmBox.remember, _ConfirmBox.texts = "Continue", False, []
    _MemorySettings.store = {}
    monkeypatch.setattr(MainWindow, "_confirm_data_flow_then", _REAL_CONFIRM_DATA_FLOW)
    monkeypatch.setattr(main_window_module, "QMessageBox", _ConfirmBox)
    monkeypatch.setattr(main_window_module, "QSettings", _MemorySettings)
    return _ConfirmBox


@pytest.mark.gui
def test_run_refuses_online_ocr_with_an_explanation(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a preflight that refuses text recognition (e.g. an Ollama cloud model)
    When   Run is pressed
    Then   the reason is shown, no worker starts and Run is usable again
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(ocr_not_allowed="Runs on Ollama's servers."),
    )
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: shown.append((title, text)),
    )

    main_window._on_run()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    assert shown[0][0] == "Text recognition must stay on your computer"
    assert "Runs on Ollama's servers." in shown[0][1]
    assert main_window._worker is None
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
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: preflight.append(config) or RunPreflight(),
    )
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "warning",
        lambda _parent, title, text: shown.append((title, text)),
    )

    main_window._on_run()

    assert shown[0][0] == "Choose another output folder"
    assert "next run would read them as new documents" in shown[0][1]
    assert "Results" in shown[0][1]
    assert preflight == []
    assert main_window._worker is None
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

    main_window._confirm_data_flow_then(make_config(tmp_path), lambda: started.append(1))

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

    main_window._confirm_data_flow_then(
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

    main_window._confirm_data_flow_then(cfg, lambda: started.append(1))
    main_window._confirm_data_flow_then(cfg, lambda: started.append(1))

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

    main_window._confirm_data_flow_then(cfg, lambda: started.append(1))
    asked_before = len(confirm_box.texts)
    main_window._confirm_data_flow_then(cfg, lambda: started.append(2))
    main_window._confirm_data_flow_then(
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
    main_window._confirm_data_flow_then(cfg, lambda: None)
    main_window._confirm_data_flow_then(cfg, lambda: None)
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
        "teachers_teammate.gui._anonymizer_config_widget.ProcessingApplicationService",
        MagicMock(),
    ) as service:
        service.return_value.list_installed_spacy_models.return_value = []
        main_window.open_settings(SettingsPage.PRIVACY, focus=FOCUS_SENDING)
    main_window._confirm_data_flow_then(cfg, lambda: None)

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

    main_window._confirm_data_flow_then(_online_proofreading_config(tmp_path), lambda: None)

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
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(
        ProcessingApplicationService, "resolve_config_path", lambda self, _: config_path
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda _parent, title, text: shown.append((title, text)),
    )

    win = MainWindow()
    qtbot.addWidget(win)

    assert shown and shown[0][0] == "Text recognition changed"
    assert "openai" in shown[0][1]
    assert "ollama" in shown[0][1]


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
    config_path.write_text('ocr_engine = "tesseract"\n', encoding="utf-8")
    shown: list[str] = []
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(
        ProcessingApplicationService, "resolve_config_path", lambda self, _: config_path
    )
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda _parent, title, _text: shown.append(title),
    )

    qtbot.addWidget(MainWindow())

    assert "Text recognition changed" not in shown


# ── Ollama status heals itself ───────────────────────────────────────────────


@pytest.mark.gui
def test_red_ollama_status_is_rechecked_until_it_turns_green(main_window) -> None:
    """
    Given  an Ollama status that came back "not connected"
    When   the check result arrives, and later a result says "connected"
    Then   a retry is scheduled while red and cancelled once green
    """
    main_window._on_llm_check_done(False, False, "✗")
    assert main_window._ollama_retry_timer.isActive() is True

    main_window._on_llm_check_done(True, True, "✓")
    assert main_window._ollama_retry_timer.isActive() is False
    assert main_window._ollama_connected is True


@pytest.mark.gui
def test_recheck_runs_only_while_ollama_is_red(main_window, monkeypatch) -> None:
    """
    Given  a status row with an Ollama label
    When   a recheck is requested while Ollama is connected, then while it is not
    Then   a background check is only started in the second case
    """
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415

    started: list[int] = []
    monkeypatch.setattr(main_window, "_start_ollama_check", lambda: started.append(1))
    main_window._status_labels = {"ollama": QLabel("●")}

    main_window._ollama_connected = True
    main_window._recheck_ollama_status()
    assert started == []

    main_window._ollama_connected = False
    main_window._recheck_ollama_status()
    assert started == [1]


# ── Run / re-run guards ──────────────────────────────────────────────────────


@pytest.mark.gui
def test_stage_rerun_with_unmet_requirement_keeps_the_cached_results(
    main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  Ollama is not running
    When   "Re-run Proofreading" is chosen from the queue context menu
    Then   the "not running" message is shown and the cache is left untouched
    """
    cfg = make_config(tmp_path)
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
    monkeypatch.setattr(
        ProcessingApplicationService,
        "check_run_preflight",
        lambda self, config: RunPreflight(ollama_unreachable="✗ Cannot connect to Ollama"),
    )
    monkeypatch.setattr(main_window, "_start_ollama_check", lambda: None)
    invalidated: list = []
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda *a, **k: invalidated.append(a) or (["/tmp/a.png"], [], False),
    )
    shown: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", lambda _p, title, _t: shown.append(title)
    )

    main_window._on_stage_run_requested(["/tmp/a.png"], "correction")
    _wait_for(lambda: bool(shown))

    assert shown == ["Ollama is not running"]
    assert invalidated == []
    assert main_window._worker is None


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
    monkeypatch.setattr(main_window, "_get_file_names", lambda _cfg: ["a.png"])
    main_window._results_table.set_queue(["a.png"])
    main_window._results_table.selectRow(0)
    release = threading.Event()
    checks: list[int] = []

    def _slow_preflight(self, config):
        checks.append(1)
        assert release.wait(timeout=5), "test never released the check"
        return RunPreflight()

    monkeypatch.setattr(ProcessingApplicationService, "check_run_preflight", _slow_preflight)
    busy: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "information", lambda _p, title, _t: busy.append(title)
    )

    main_window._on_run()
    main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")
    main_window._on_run()

    assert busy == ["Please wait"]
    release.set()
    qtbot.waitUntil(lambda: main_window._worker is not None, timeout=3000)
    first_worker = main_window._worker
    qtbot.wait(100)
    assert main_window._worker is first_worker
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
    worker = _DummyWorker(cfg)
    worker.start()
    main_window._worker = worker
    invalidated: list = []
    monkeypatch.setattr(
        main_window._app_service,
        "invalidate_from_stage",
        lambda *a, **k: invalidated.append(a) or (["/tmp/a.png"], [], False),
    )
    busy: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "information", lambda _p, title, _t: busy.append(title)
    )

    main_window._on_stage_run_requested(["/tmp/a.png"], "ocr")

    assert busy == ["Please wait"]
    assert invalidated == []
    assert main_window._preflight_running is False


@pytest.mark.gui
def test_closing_the_window_stops_the_ollama_retries(main_window) -> None:
    """
    Given  a red Ollama status with a retry scheduled
    When   the window is closed
    Then   the retry timer is stopped and a late check result schedules nothing new
    """
    from PySide6.QtGui import QCloseEvent  # noqa: PLC0415

    main_window._on_llm_check_done(False, False, "✗")
    assert main_window._ollama_retry_timer.isActive() is True

    main_window.closeEvent(QCloseEvent())

    assert main_window._ollama_retry_timer.isActive() is False


def _gpu_monitoring_window(main_window, monkeypatch):
    """Isolate settings, record installs/refreshes and capture the consent question."""
    store: dict = {}

    class _Settings(_MemorySettings):
        pass

    _Settings.store = store
    monkeypatch.setattr(main_window_module, "QSettings", _Settings)
    installs: list[str] = []
    asked: list[str] = []
    refreshed: list[bool] = []
    monkeypatch.setattr(
        main_window._app_service, "install_gpu_monitoring", lambda addon: installs.append(addon)
    )
    monkeypatch.setattr(
        main_window._stats, "refresh_gpu_monitoring", lambda: refreshed.append(True) or True
    )
    # Run the "background" install synchronously.
    monkeypatch.setattr(
        main_window_module,
        "run_in_background",
        lambda fn, on_done, owner=None: on_done(fn(), None),
    )

    def answer(answer_yes: bool):
        def question(_parent, title, _text, *_a, **_k):
            asked.append(title)
            button = main_window_module.QMessageBox.StandardButton
            return button.Yes if answer_yes else button.No

        monkeypatch.setattr(main_window_module.QMessageBox, "question", staticmethod(question))

    return store, installs, asked, refreshed, answer


@pytest.mark.gui
def test_gpu_monitoring_is_downloaded_after_the_user_agrees(main_window, monkeypatch) -> None:
    """
    Given  a GPU-monitoring package is missing and the user was not asked yet
    When   the main window learns about it and the user answers Yes
    Then   the package is installed, the answer is remembered and System Stats is refreshed
    """
    store, installs, asked, refreshed, answer = _gpu_monitoring_window(main_window, monkeypatch)
    answer(True)

    main_window._on_gpu_monitoring_needed("nvidia", None)

    assert asked == ["Show GPU usage?"]
    assert store[main_window._GPU_MONITORING_CHOICE_KEY] == "yes"
    assert installs == ["nvidia"]
    assert refreshed == [True]
    assert "Installed GPU monitoring" in main_window._log.toPlainText()


@pytest.mark.gui
def test_gpu_monitoring_is_not_downloaded_after_the_user_declines(main_window, monkeypatch) -> None:
    """
    Given  a GPU-monitoring package is missing and the user was not asked yet
    When   the user answers No, and the window learns about it again at the next start
    Then   nothing is downloaded, and the user is not asked a second time
    """
    store, installs, asked, _refreshed, answer = _gpu_monitoring_window(main_window, monkeypatch)
    answer(False)

    main_window._on_gpu_monitoring_needed("nvidia", None)
    main_window._on_gpu_monitoring_needed("nvidia", None)

    assert asked == ["Show GPU usage?"]
    assert store[main_window._GPU_MONITORING_CHOICE_KEY] == "no"
    assert installs == []


@pytest.mark.gui
def test_gpu_monitoring_is_downloaded_without_asking_after_an_earlier_yes(
    main_window, monkeypatch
) -> None:
    """
    Given  the user agreed to the download in an earlier session
    When   the main window learns that the package is missing again
    Then   it is downloaded without asking again
    """
    store, installs, asked, _refreshed, _answer = _gpu_monitoring_window(main_window, monkeypatch)
    store[main_window._GPU_MONITORING_CHOICE_KEY] = "yes"

    main_window._on_gpu_monitoring_needed("amd", None)

    assert asked == []
    assert installs == ["amd"]


@pytest.mark.gui
def test_gpu_monitoring_failure_is_remembered_and_not_retried(main_window, monkeypatch) -> None:
    """
    Given  the user agreed but pip fails (for example offline)
    When   the failure arrives, and the next start finds the package missing again
    Then   a notice is logged once, the failure is remembered and no new download starts
    """
    store, installs, asked, refreshed, _answer = _gpu_monitoring_window(main_window, monkeypatch)
    store[main_window._GPU_MONITORING_CHOICE_KEY] = "yes"

    main_window._on_gpu_monitoring_installed("nvidia", RuntimeError("pip exited with code 1"))

    log = main_window._log.toPlainText()
    assert "could not be installed" in log
    assert "Add-ons" in log
    assert refreshed == []
    assert store[main_window._GPU_MONITORING_FAILED_KEY] == main_window_module.__version__

    main_window._on_gpu_monitoring_needed("nvidia", None)

    assert installs == []
    assert asked == []


@pytest.mark.gui
def test_gpu_monitoring_retries_after_an_app_update(main_window, monkeypatch) -> None:
    """
    Given  the download failed in an older app version
    When   the main window learns that the package is missing
    Then   the download is tried again
    """
    store, installs, _asked, _refreshed, _answer = _gpu_monitoring_window(main_window, monkeypatch)
    store[main_window._GPU_MONITORING_CHOICE_KEY] = "yes"
    store[main_window._GPU_MONITORING_FAILED_KEY] = "0.0.0-older"

    main_window._on_gpu_monitoring_needed("nvidia", None)

    assert installs == ["nvidia"]


@pytest.mark.gui
def test_gpu_monitoring_nothing_to_do_is_silent(main_window, monkeypatch) -> None:
    """
    Given  nothing needs installing (no GPU, package present, or source checkout)
    When   the main window receives the None result, or an error from the probe
    Then   the user is not asked and nothing is logged
    """
    _store, installs, asked, refreshed, _answer = _gpu_monitoring_window(main_window, monkeypatch)
    before = main_window._log.toPlainText()

    main_window._on_gpu_monitoring_needed(None, None)
    main_window._on_gpu_monitoring_needed(None, RuntimeError("probe failed"))

    assert (installs, asked, refreshed) == ([], [], [])
    assert main_window._log.toPlainText() == before


@pytest.mark.gui
def test_gpu_monitoring_installed_but_unreadable_is_reported_honestly(
    main_window, monkeypatch
) -> None:
    """
    Given  the package installed but the driver gives it no readings (refresh finds no rows)
    When   the main window receives the result
    Then   the log says so instead of claiming System Stats shows the GPU
    """
    monkeypatch.setattr(main_window._stats, "refresh_gpu_monitoring", lambda: False)

    main_window._on_gpu_monitoring_installed("nvidia", None)

    log = main_window._log.toPlainText()
    assert "no readings" in log
    assert "Installed GPU monitoring" not in log


# ── Export menu ───────────────────────────────────────────────────────────


def _queue_with_text(window: MainWindow, folder: Path, texts: dict[str, str]) -> list[str]:
    """Queue one row per *texts* key (a file name in *folder*) holding that extracted text."""
    ids = [str(folder / name) for name in texts]
    window._results_table.set_queue(list(texts), source_ids=ids)
    for source_id, text in zip(ids, texts.values(), strict=True):
        window._results_table.set_row_artifacts(source_id, "", text, "", "")
    return ids


def _pick_folder(monkeypatch, folder: Path) -> None:
    monkeypatch.setattr(
        main_window_module.QFileDialog, "getExistingDirectory", lambda *_a, **_k: str(folder)
    )


def _pick_file(monkeypatch, path: Path) -> None:
    monkeypatch.setattr(
        main_window_module.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(path), "")
    )


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
    warnings: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", lambda _p, _t, msg: warnings.append(msg)
    )

    main_window._on_export_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._text_export_running and bool(warnings))

    assert (out / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (out / "b.txt").read_text(encoding="utf-8") == "beta"
    assert not (out / "c.txt").exists()
    assert "c.png" in warnings[0]


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
    warnings: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", lambda _p, _t, msg: warnings.append(msg)
    )

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        main_window._on_export_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: not main_window._text_export_running)

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
    warnings: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", lambda _p, _t, msg: warnings.append(msg)
    )

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
        side_effect=ImportError("No module named 'spacy'"),
    ):
        main_window._on_export_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: bool(warnings))

    assert len(warnings) == 1
    assert "spaCy" in warnings[0]
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
    asked: list[str] = []

    def _no(_parent, title, _text, *_a):
        asked.append(title)
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", _no)

    main_window._on_export_requested(ids, ExportKind.TEXT)

    assert asked == ["Save into the input folder?"]
    assert not (scans / "a.txt").exists()
    assert not main_window._text_export_running


def _capture_warnings(monkeypatch) -> list[str]:
    warnings: list[str] = []
    monkeypatch.setattr(
        main_window_module.QMessageBox, "warning", lambda _p, _t, msg: warnings.append(msg)
    )
    return warnings


def _answer_questions(monkeypatch, answer) -> list[str]:
    """Answer every QMessageBox.question with *answer*; return the titles asked."""
    asked: list[str] = []

    def _answer(_parent, title, _text, *_a):
        asked.append(title)
        return answer

    monkeypatch.setattr(main_window_module.QMessageBox, "question", _answer)
    return asked


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
        main_window,
        "_on_export_requested",
        lambda source_ids, kind: calls.append((source_ids, kind)),
    )

    main_window._on_export_toolbar(ExportKind.TEXT)
    main_window._results_table.selectRow(1)
    main_window._on_export_toolbar(ExportKind.DOCX)

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
        main_window,
        "_on_export_requested",
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
    ids = [str(scans / "x.png"), str(scans / "x.pdf")]
    main_window._results_table.set_queue(["x.png", "x.pdf"], source_ids=ids)
    for sid in ids:
        main_window._results_table.set_row_artifacts(sid, "", "text", "", "")
    _pick_folder(monkeypatch, out)
    warnings = _capture_warnings(monkeypatch)
    written: list[Path] = []
    service = main_window._app_service
    monkeypatch.setattr(
        service, "export_docx", lambda _s, _r, _c, _i, path, **_k: written.append(path)
    )
    monkeypatch.setattr(service, "export_text", lambda _s, _r, path, **_k: written.append(path))

    main_window._on_export_requested(ids, kind)
    _wait_for(lambda: bool(warnings))

    assert written == [out / ("x" + kind.file_suffix)]
    assert "x.pdf" in warnings[0]
    assert "x.png" not in warnings[0]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        (main_window_module.QMessageBox.StandardButton.No, "old"),
        (main_window_module.QMessageBox.StandardButton.Yes, "alpha"),
    ],
)
def test_export_into_a_folder_asks_before_replacing_existing_files(
    main_window, monkeypatch, tmp_path: Path, answer, expected: str
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
    asked = _answer_questions(monkeypatch, answer)

    main_window._on_export_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._text_export_running)

    assert asked == ["Replace existing files?"]
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
    texts: list[str] = []

    def _question(_parent, _title, text, *_args):
        texts.append(text)
        return main_window_module.QMessageBox.StandardButton.No

    monkeypatch.setattr(main_window_module.QMessageBox, "question", _question)

    assert main_window._confirm_replace_existing(targets) is False

    assert "class1/a.txt" in texts[0]
    assert "class2/a.txt" in texts[0]


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

    main_window._on_export_requested([ids[0]], ExportKind.TEXT)
    _wait_for(lambda: bool(warnings))

    assert (scans / "a.txt").read_text(encoding="utf-8") == "original"
    assert "a.png" in warnings[0]
    assert "input document" in warnings[0]


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
    asked = _answer_questions(monkeypatch, main_window_module.QMessageBox.StandardButton.Yes)

    main_window._on_export_requested(ids, ExportKind.TEXT)
    _wait_for(lambda: not main_window._text_export_running)

    assert asked == ["Save into the input folder?"]
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
    monkeypatch.setattr(main_window._app_service, "export_docx", lambda *_a, **_k: None)
    asked = _answer_questions(monkeypatch, main_window_module.QMessageBox.StandardButton.No)
    kind = ExportKind.DOCX if folder_mode else ExportKind.TEXT
    _pick_file(monkeypatch, scans / ("a" + kind.file_suffix))

    main_window._on_export_requested(ids, kind)
    _wait_for(lambda: not main_window._text_export_running)

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
        main_window._on_export_requested(ids, ExportKind.TEXT_ANONYMIZED)
        _wait_for(lambda: bool(warnings))

    assert len(warnings) == 1
    assert "spaCy" in warnings[0]
    assert "c.png" in warnings[0]


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
    sid = str(tmp_path / "scans" / "a.png")
    main_window._results_table.set_queue(["a.png"], source_ids=[sid])
    main_window._results_table.set_row_artifacts(sid, "", "text", "corrected", "")
    main_window._config_panel._language.setCurrentText(text_language)
    main_window._config_panel.update_settings({"explanation_language": explanation_language})
    _pick_file(monkeypatch, tmp_path / "out" / "a.docx")
    seen: list[str] = []
    monkeypatch.setattr(
        main_window._app_service,
        "export_docx",
        lambda _s, _r, _c, _i, _path, options=None, **_k: seen.append(options.labels_language),
    )

    main_window._on_export_requested([sid], ExportKind.DOCX)
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
    from PySide6.QtCore import QObject, Signal  # noqa: PLC0415

    from teachers_teammate.gui import _addon_installer_dialog  # noqa: PLC0415

    threads: list[Any] = []

    class _FakePull(QObject):
        progress = Signal(str, float, float)
        finished_ok = Signal(str)
        finished_err = Signal(str)

        def __init__(self, url: str, model: str) -> None:
            super().__init__()
            self.model = model
            threads.append(self)

        def start(self) -> None:
            pass

        def isRunning(self) -> bool:
            return True

        def request_abort(self) -> None:
            pass

    monkeypatch.setattr(_addon_installer_dialog, "_OllamaPullThread", _FakePull)
    manager = main_window_module.OllamaDownloadManager()
    manager.start("http://host:11434", "a:1b")

    manager.start("http://host:11434", "a:1b", ["b:1b"])
    manager.start("http://host:11434", "b:1b", ["c:1b"])

    assert [t.model for t in threads] == ["a:1b"]
    assert manager.queued == ["b:1b", "c:1b"]
    threads[0].finished_ok.emit("a:1b")
    assert [t.model for t in threads] == ["a:1b", "b:1b"]
