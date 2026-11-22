"""The app walkthrough run in this process: the real window, the real reader, no teacher.

What is real here: :class:`~teachers_teammate.gui._main_window.MainWindow` built by
:func:`~teachers_teammate.gui.composition.build_main_window`, every controller, the real
``OCRWorker`` thread, the real pipeline with the pinned reader and page models, the store under
one temporary folder, ``ocr.toml`` on disk and the Word files a run writes.

What is replaced: nothing of the app.  The script plays the teacher — it clicks the buttons,
types into the Check tab and answers the boxes
(:func:`~teachers_teammate.gui._dialogs.answering`) — so what these tests exercise is the app as
it is started, not a harness around it.  Only the model folder is pointed at what Bazel fetched
(``TEACHERS_TEAMMATE_MODELS_DIR``), which is how the app finds a downloaded model.

The script itself is ``tests/e2e/walkthrough.py``: the bundle runs the same file with
``--_run_script``, so a step that passes here passes there, and this test runs the same entry
point (``main``) the runner calls.

The failure tests replace one thing each and check that the walk reports it: a reader that
raises, a box the step did not expect, a window the step did not open, and a slot that raises.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import threading
import time
from typing import Any

from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog
import pytest

from teachers_teammate.application.composition import build_app_services
from teachers_teammate.gui import _dialogs, _handles
from teachers_teammate.gui._app_bootstrap import create_app
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._run_controller import RunController
from teachers_teammate.gui.composition import build_main_window
from teachers_teammate.infrastructure import stage_builder
from teachers_teammate.infrastructure.onnx.store import MODELS_DIR_ENV
from teachers_teammate.texts import dialogs as dlg
from tests.conftest import models_store, skip_no_page_models, skip_no_reader_models
from tests.e2e import walkthrough

pytestmark = [pytest.mark.gui]


def _walk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[int, dict[str, Any]]:
    """Run the walk on the fetched models; return its exit code and what it wrote."""
    monkeypatch.setenv(MODELS_DIR_ENV, str(models_store(tmp_path).root))
    out = tmp_path / "e2e.json"
    code = walkthrough.main([str(out)])
    return code, json.loads(out.read_text(encoding="utf-8"))


def _pump(condition: Callable[[], bool], *, what: str, seconds: float = 20.0) -> None:
    """Process events until *condition* holds, or fail the test naming *what*."""
    deadline = time.monotonic() + seconds
    while not condition():
        QApplication.processEvents()
        assert time.monotonic() < deadline, f"{what} did not happen"
        time.sleep(0.01)


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_the_walk_passes_on_the_real_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the reader's and the page models' real files
    When   the walk plays teacher in the main window and writes its result
    Then   every step passes, nothing was unexpected, and the exit code says so
    """
    code, result = _walk(tmp_path, monkeypatch)

    assert result["failed"] == []
    assert [step["name"] for step in result["steps"]] == [
        name for name, _method in walkthrough.STEPS
    ]
    assert all(step["ok"] for step in result["steps"]), [
        step for step in result["steps"] if not step["ok"]
    ]
    assert result["ok"] is True
    assert code == 0
    assert result["modals"] == []
    assert result["exceptions"] == []
    assert all(entry["expected"] for entry in result["dialogs"]), [
        entry for entry in result["dialogs"] if not entry["expected"]
    ]
    assert result["platform"]
    assert result["versions"]["app"] and result["versions"]["qt"]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_a_reader_that_raises_fails_the_run_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a text reader that raises
    When   the walk runs the queue
    Then   the run step fails and the result names the step and the reader's message
    """

    def broken(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("the reader is broken")

    monkeypatch.setattr(stage_builder, "PpOcrProcessor", broken)
    code, result = _walk(tmp_path, monkeypatch)

    assert code == 1
    assert result["ok"] is False
    assert result["failed"], result
    assert result["failed"][0].startswith("run: ")
    assert "the reader is broken" in " ".join(result["failed"])


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_unexpected_dialog_fails_the_step_it_happened_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the app asks something the step did not announce
    When   the walk runs
    Then   that step fails and the call is in the record as unexpected
    """
    run_as_asked = MainWindow._on_run

    def noisy_on_run(self: MainWindow) -> None:
        _dialogs.notify(self, dlg.no_files_selected())
        run_as_asked(self)

    monkeypatch.setattr(MainWindow, "_on_run", noisy_on_run)
    code, result = _walk(tmp_path, monkeypatch)

    assert code == 1
    assert result["failed"][0].startswith("run: "), result["failed"]
    unexpected = [entry for entry in result["dialogs"] if not entry["expected"]]
    assert [(entry["step"], entry["call"]) for entry in unexpected] == [("run", "notify")]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_unexpected_window_is_closed_and_fails_its_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  the app opens a window of its own during a step
    When   the walk runs
    Then   the window is closed, the walk goes on, and the step that opened it fails
    """
    run_as_asked = MainWindow._on_run

    def modal_on_run(self: MainWindow) -> None:
        QDialog(self).exec()
        run_as_asked(self)

    monkeypatch.setattr(MainWindow, "_on_run", modal_on_run)
    code, result = _walk(tmp_path, monkeypatch)

    assert code == 1
    assert result["modals"] == ["QDialog"]
    assert result["failed"][0].startswith("run: "), result["failed"]
    # The window was closed rather than answered badly, so the run itself went through.
    assert result["steps"][-1]["name"] == "run"


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_exception_in_a_slot_fails_the_step_it_happened_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a slot of the window raises while the run goes on
    When   the walk runs
    Then   the exception is in the result and the step it happened in fails
    """

    def broken(self: RunController, _steps: int) -> None:
        raise RuntimeError("a slot broke")

    monkeypatch.setattr(RunController, "_set_steps_done", broken)
    code, result = _walk(tmp_path, monkeypatch)

    assert code == 1
    assert "RuntimeError: a slot broke" in result["exceptions"], result["exceptions"]
    assert result["failed"][0].startswith("run: "), result["failed"]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_the_walk_leaves_no_thread_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  a walk that ends by closing the window
    When   the entry point returns
    Then   no thread the walk started is still running
    """
    before = set(threading.enumerate())
    code, result = _walk(tmp_path, monkeypatch)

    assert code == 0, result["failed"]
    assert set(threading.enumerate()) - before == set()


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_saved_config_names_the_same_documents_as_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a window whose documents folder was chosen and whose settings were then saved
    When   the saved settings file is read back as a configuration
    Then   it names the same documents, in the same state, as the window shows
    """
    create_app()
    monkeypatch.setenv(MODELS_DIR_ENV, str(models_store(tmp_path).root))
    with walkthrough.isolated() as root:
        walkthrough.write_settings(root)
        inputs = root / walkthrough.INPUT_FOLDER
        walkthrough.write_inputs(inputs)
        service = build_app_services()
        window = build_main_window(app_service=service)
        window.show()
        table = window.findChild(ResultsTable, _handles.RESULTS_TABLE)
        button = window.findChild(QAbstractButton, _handles.INPUT_FOLDER_BUTTON)
        assert table is not None
        assert button is not None
        with _dialogs.answering(lambda asked: inputs if asked.call == "pick_folder" else None):
            button.click()
            _pump(lambda: len(table.queued_source_ids()) == 3, what="the three rows")
        shown = [
            (source, table.row_for(source).ocr, table.has_text(source))
            for source in table.queued_source_ids()
        ]
        window.close()
        config = walkthrough.saved_config(root)
        saved = service.documents.queue_rows(config, [Path(source) for source, _o, _t in shown])

    assert Path(config.input_dir).resolve() == inputs.resolve()
    assert [row.name for row in saved] == [Path(source).name for source, _o, _t in shown]
    for row, (_source, ocr, has_text) in zip(saved, shown, strict=True):
        assert row.ocr == ocr
        assert row.has_text == has_text
