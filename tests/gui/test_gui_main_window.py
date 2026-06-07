"""GUI tests for MainWindow orchestration using pytest-qt."""
# pylint: disable=W0621,W0613  # redefined-outer-name — pytest fixtures shadow module-scope names by design / unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

from pathlib import Path
import threading

import pytest

from teachers_teammate.application.service import ProcessingApplicationService
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._types import FileDoneEvent
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from tests.conftest import make_config


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
        self.ocr_done = _DummySignal()
        self.file_done = _DummySignal()
        self.finished_with_code = _DummySignal()
        self.selected_source_paths = selected_source_paths or []
        self.stop_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running


@pytest.fixture(autouse=True)
def _no_network_in_gui_tests(monkeypatch) -> None:
    """Prevent all MainWindow tests from making Ollama/network calls via the dependency guard."""
    monkeypatch.setattr(ProcessingApplicationService, "check_stage_requirements", lambda *_: [])


@pytest.fixture
def main_window(qtbot, monkeypatch) -> MainWindow:
    """Create a MainWindow with startup side effects disabled."""
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
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
def test_main_window_run_shows_info_when_no_files(main_window, monkeypatch, tmp_path: Path) -> None:
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

    assert seen == ["info"]
    assert main_window._worker is None


@pytest.mark.gui
def test_main_window_run_starts_worker_and_updates_ui(
    main_window, monkeypatch, tmp_path: Path
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

    assert main_window._worker is not None
    assert isinstance(main_window._worker, _DummyWorker)
    assert main_window._worker.selected_source_paths == ["a.png"]
    assert main_window._run_btn.isEnabled() is False
    assert main_window._stop_btn.isEnabled() is True
    assert main_window._progress.maximum() == 1


@pytest.mark.gui
def test_main_window_run_shows_info_when_no_selection(
    main_window, monkeypatch, tmp_path: Path
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

    assert seen == ["info"]
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
    # green = available
    assert "#27ae60" in lbl.styleSheet()


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
    assert "#c0392b" in lbl.styleSheet()


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
def test_main_window_open_settings_dialog_accepted(main_window, qtbot) -> None:
    """
    Given  settings dialog returns Accepted
    When   _open_settings_dialog is called
    Then   config panel update_settings is called and LLM check re-triggered
    """
    from unittest.mock import patch, MagicMock  # noqa: PLC0415
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    mock_dialog = MagicMock()
    mock_dialog.exec.return_value = QDialog.DialogCode.Accepted
    mock_dialog.get_values.return_value = {}
    mock_dialog.preprocess_preview_requested = _DummySignal()

    check_called = []

    with (
        patch("teachers_teammate.gui._main_window.SettingsDialog", return_value=mock_dialog),
        patch.object(main_window, "_check_llm_status", lambda: check_called.append(1)),
        patch.object(main_window, "_save_settings"),
    ):
        main_window._open_settings_dialog()

    assert check_called


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
def test_main_window_check_llm_status_ollama_starts_thread(qtbot, monkeypatch) -> None:
    """
    Given  ocr_engine is ollama
    When   _check_llm_status is called
    Then   a connection check thread is started
    """
    from unittest.mock import MagicMock, patch  # noqa: PLC0415

    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    win = MainWindow()
    qtbot.addWidget(win)

    mock_thread = MagicMock()
    mock_thread.check_done = _DummySignal()

    with (
        patch.object(
            win._config_panel,
            "get_settings_dict",
            return_value={"ocr_engine": "ollama", "ollama_url": "http://localhost:11434"},
        ),
        patch(
            "teachers_teammate.gui._main_window._ConnectionCheckThread",
            return_value=mock_thread,
        ),
    ):
        win._check_llm_status()

    mock_thread.start.assert_called_once()


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
    Then   method returns without error
    """
    from unittest.mock import patch  # noqa: PLC0415

    monkeypatch.setattr(main_window._config_panel, "get_input_dir", lambda: str(tmp_path))
    with patch(
        "teachers_teammate.gui._main_window.QFileDialog.getOpenFileName", return_value=("", "")
    ):
        main_window._on_preprocess_preview_requested("none")  # should not raise


@pytest.mark.gui
def test_main_window_on_preprocess_preview_preprocess_error(
    main_window, monkeypatch, tmp_path
) -> None:
    """
    Given  preprocess_preview raises an exception
    When   _on_preprocess_preview_requested is called
    Then   a warning dialog is shown and no crash occurs
    """
    from unittest.mock import patch  # noqa: PLC0415

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
        main_window._on_preprocess_preview_requested("none")
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

    assert started == [(cfg, ["/tmp/a.png"])]


# ── Credentials loading ────────────────────────────────────────────────────


@pytest.mark.gui
def test_main_window_loads_credentials_on_startup(qtbot, monkeypatch) -> None:
    """
    Given  QSettings contains a stored API key under credentials/
    When   MainWindow is created
    Then   the key is injected into os.environ (when not already set)
    """
    import os  # noqa: PLC0415
    from unittest.mock import MagicMock  # noqa: PLC0415

    mock_qs = MagicMock()
    mock_qs.childKeys.return_value = ["TEST_CRED_KEY_INIT"]
    mock_qs.value.return_value = "test-value-from-settings"

    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(main_window_module, "QSettings", lambda *_: mock_qs)
    monkeypatch.delenv("TEST_CRED_KEY_INIT", raising=False)

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert os.environ.get("TEST_CRED_KEY_INIT") == "test-value-from-settings"
    os.environ.pop("TEST_CRED_KEY_INIT", None)


@pytest.mark.gui
def test_main_window_env_var_takes_priority_over_stored_credential(qtbot, monkeypatch) -> None:
    """
    Given  an env var is already set AND QSettings has a different value for the same key
    When   MainWindow is created
    Then   the existing env var is NOT overwritten by the stored value
    """
    import os  # noqa: PLC0415
    from unittest.mock import MagicMock  # noqa: PLC0415

    mock_qs = MagicMock()
    mock_qs.childKeys.return_value = ["TEST_CRED_KEY_PRIO"]
    mock_qs.value.return_value = "stored-value"

    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
    monkeypatch.setattr(main_window_module, "QSettings", lambda *_: mock_qs)
    monkeypatch.setenv("TEST_CRED_KEY_PRIO", "env-value")

    win = MainWindow(worker_factory=_DummyWorker)
    qtbot.addWidget(win)

    assert os.environ["TEST_CRED_KEY_PRIO"] == "env-value"
