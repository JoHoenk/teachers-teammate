"""The app walkthrough run as the bundle runs it: the real window, the real reader, no teacher.

What is real here: :class:`~teachers_teammate.gui._main_window.MainWindow` built by
:func:`~teachers_teammate.gui.composition.build_main_window`, every controller, the real
``OCRWorker`` thread, the real pipeline with the pinned reader and page models, the store under
one temporary folder, ``ocr.toml`` on disk and the Word files a run writes.

What is replaced: nothing of the app.  The script plays the teacher — it clicks the buttons,
types into the Check tab and answers the boxes
(:func:`~teachers_teammate.gui._dialogs.answering`) — so what these tests exercise is the app as
it is started, not a harness around it.  Only the model folder is pointed at what Bazel fetched
(``TEACHERS_TEAMMATE_MODELS_DIR``), which is how the app finds a downloaded model.

Every walk is a process of its own (``tests/e2e/walk_checked.py``, which calls the same
``walkthrough.main`` the bundle runner calls): a walk leaves windows, pool threads and queued
signals behind, and a second walk in the same process would wait for what the first one left;
a hung or aborted walk ends only its own process, and the test prints the result it left.

The failure tests break one thing each in the child and check that the walk reports it: a reader
that raises, a box the step did not expect, a window the step did not open, a slot that raises,
and a reader that never returns.  Three tests need no child: a wait that runs out says what the
guards caught, a window is quoted as the teacher read it, and a slot's exception is reported once.
The plain walk also checks that no thread of Python's is left running (the child exits with 3
if one is).

One walk proofreads: the child starts a stand-in Ollama server
(:class:`~tests.fakes.fake_ollama.FakeOllama`) and the walk reads the items, ticks one and spoils
a stored review, so the app has to drop it.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from PySide6.QtWidgets import QAbstractButton, QApplication, QDialog, QLabel, QVBoxLayout
import pytest

from teachers_teammate.application.composition import build_app_services
from teachers_teammate.gui import _dialogs, _handles
from teachers_teammate.gui._app_bootstrap import create_app
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui.composition import build_main_window
from teachers_teammate.infrastructure.onnx.store import MODELS_DIR_ENV
from tests.conftest import models_store, skip_no_page_models, skip_no_reader_models
from tests.e2e import walkthrough

pytestmark = [pytest.mark.gui]

#: Longer than the child's own watchdog, so the child writes its result before this gives up.
CHILD_S = walkthrough.WALK_S + 120.0


def _walk(tmp_path: Path, fault: str = "plain") -> tuple[int, dict[str, Any], str]:
    """Walk in a process of its own; return its exit code, what it wrote and what it printed."""
    out = tmp_path / "e2e.json"
    env = dict(os.environ)
    env[MODELS_DIR_ENV] = str(models_store(tmp_path).root)
    # What Bazel puts on this process's path is what the child imports the app and the tests from.
    env["PYTHONPATH"] = os.pathsep.join(entry for entry in sys.path if entry)
    try:
        done = subprocess.run(
            [sys.executable, "-m", "tests.e2e.walk_checked", fault, str(out)],
            env=env,
            capture_output=True,
            text=True,
            timeout=CHILD_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"the walk ({fault}) ran past {CHILD_S:g} s: {exc.stdout!r} {exc.stderr!r}")
    printed = f"{done.stdout}\n{done.stderr}"
    assert out.is_file(), f"the walk ({fault}) wrote no result (exit {done.returncode}): {printed}"
    return done.returncode, json.loads(out.read_text(encoding="utf-8")), printed


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
def test_the_walk_passes_on_the_real_models(tmp_path: Path) -> None:
    """
    Given  the reader's and the page models' real files
    When   the walk plays teacher in the main window and writes its result
    Then   every step passes, nothing was unexpected, and the exit code says so
    """
    code, result, printed = _walk(tmp_path)

    assert result["failed"] == [], printed
    assert [step["name"] for step in result["steps"]] == [
        name for name, _method in walkthrough.walk_steps(proofreading=False)
    ]
    assert all(step["ok"] for step in result["steps"]), [
        step for step in result["steps"] if not step["ok"]
    ]
    assert result["ok"] is True
    assert code == 0, printed
    assert result["modals"] == []
    assert result["exceptions"] == []
    assert all(entry["expected"] for entry in result["dialogs"]), [
        entry for entry in result["dialogs"] if not entry["expected"]
    ]
    assert result["platform"]
    assert result["versions"]["app"] and result["versions"]["qt"]
    assert result["hang"] == {}
    # What the reader made of the pages is in the result, so a slack that hides a misreading shows.
    assert set(result["readings"]) == {
        Path(name).stem + walkthrough.DOCX_SUFFIX
        for name in (walkthrough.PNG_NAME, walkthrough.PDF_NAME)
    }


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_the_walk_passes_with_proofreading(tmp_path: Path) -> None:
    """
    Given  a stand-in Ollama server that answers the proofreading exchange
    When   the walk runs and the teacher opens, reads and ticks what the model changed
    Then   every step passes, the review of a document that no longer fits its text is dropped
           with a warning, and the app kept nothing half right
    """
    code, result, printed = _walk(tmp_path, "proofreading")

    assert result["failed"] == [], printed
    assert [step["name"] for step in result["steps"]] == [
        name for name, _method in walkthrough.walk_steps(proofreading=True)
    ]
    assert result["ok"] is True
    assert code == 0, printed
    assert result["modals"] == []
    assert result["exceptions"] == []
    assert all(entry["expected"] for entry in result["dialogs"]), [
        entry for entry in result["dialogs"] if not entry["expected"]
    ]
    # The review that no longer fits was dropped, and the walk's own assertions caught the rest:
    # it would have failed the step had the app shown the moved explanations.
    assert any("Dropped the review" in line for line in result["warnings"]), result["warnings"]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_a_reader_that_raises_fails_the_run_step(tmp_path: Path) -> None:
    """
    Given  a text reader that raises
    When   the walk runs the queue
    Then   the run step fails and the result names the step and the reader's message
    """
    code, result, _printed = _walk(tmp_path, "reader-raises")

    assert code == 1
    assert result["ok"] is False
    assert result["failed"], result
    assert result["failed"][0].startswith("run: ")
    assert "the reader is broken" in " ".join(result["failed"])


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_unexpected_dialog_fails_the_step_it_happened_in(tmp_path: Path) -> None:
    """
    Given  the app asks something the step did not announce
    When   the walk runs
    Then   that step fails and the call is in the record as unexpected
    """
    code, result, _printed = _walk(tmp_path, "unexpected-dialog")

    assert code == 1
    assert result["failed"][0].startswith("run: "), result["failed"]
    unexpected = [entry for entry in result["dialogs"] if not entry["expected"]]
    assert [(entry["step"], entry["call"]) for entry in unexpected] == [("run", "notify")]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_unexpected_window_is_closed_and_fails_its_step(tmp_path: Path) -> None:
    """
    Given  a run has started and the app opens a window of its own, saying what it wants
    When   the walk runs
    Then   the window is closed, and the step fails with the window's own title and sentences
    """
    code, result, _printed = _walk(tmp_path, "unexpected-window")

    assert code == 1
    assert result["modals"] == [
        "QDialog: The run can't start / The built-in reader is not downloaded yet."
    ], result
    assert result["failed"][0].startswith("run: "), result["failed"]
    assert "The built-in reader is not downloaded yet." in result["failed"][0]
    # The window was closed rather than answered badly, so the run itself went through.
    assert result["steps"][-1]["name"] == "run"


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_an_exception_in_a_slot_fails_the_step_it_happened_in(tmp_path: Path) -> None:
    """
    Given  a slot of the window raises while the run goes on
    When   the walk runs
    Then   the exception is in the result and the step it happened in fails
    """
    code, result, _printed = _walk(tmp_path, "slot-raises")

    assert code == 1
    assert "RuntimeError: a slot broke" in result["exceptions"], result["failed"]
    assert result["failed"][0].startswith("run: "), result["failed"]


@skip_no_reader_models
@skip_no_page_models
@pytest.mark.needs_reader_models
@pytest.mark.needs_page_models
def test_a_walk_that_hangs_is_ended_with_its_stacks_in_the_result(tmp_path: Path) -> None:
    """
    Given  a reader that never returns and a watchdog set to a minute
    When   the walk runs
    Then   the process ends with exit 1, and the result names the step, every thread's stack and
           what the app was running
    """
    code, result, _printed = _walk(tmp_path, "hangs")

    assert code == 1
    assert result["ok"] is False
    assert "timed out in step run" in result["failed"], result["failed"]
    assert "Thread" in result["hang"]["stacks"]
    assert result["hang"]["step"] == "run"
    assert result["hang"]["worker_running"] is True
    assert set(result["hang"]["background_tasks"]) == {"local", "network"}


def test_a_wait_that_runs_out_names_what_the_guards_caught(qtbot, tmp_path: Path) -> None:
    """
    Given  a walk whose guards caught a window the step did not open
    When   a wait runs out before the step's condition holds
    Then   the failure names the window and its words, not only the wait
    """
    walk = walkthrough.Walk(root=tmp_path, service=build_app_services())
    window = QDialog()
    qtbot.addWidget(window)
    window.setWindowTitle("The run can't start")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("<b>The run can't start:</b>"))
    layout.addWidget(QLabel("The built-in reader is not downloaded yet."))

    walk._modals.caught(window)
    with pytest.raises(walkthrough._Failed) as caught:
        walk._settle(lambda: False, "the run to end", 0.01)

    assert str(caught.value).startswith("the run to end did not happen within 0.01 s")
    assert (
        "a window opened: QDialog: The run can't start / "
        "The built-in reader is not downloaded yet." in str(caught.value)
    )


def test_a_closed_window_is_quoted_as_the_teacher_read_it(qtbot) -> None:
    """
    Given  a window with a bold sentence, a text holding "<" and "&", a line break and a hidden label
    When   the walk records it as closed
    Then   the record has the visible words only, with entities decoded and lines apart
    """
    walk = walkthrough.Modals()
    window = QDialog()
    qtbot.addWidget(window)
    window.setWindowTitle("Title")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("<b>Title:</b>"))
    layout.addWidget(QLabel("Files < 5 MB are read"))
    layout.addWidget(QLabel("Tom &amp; Jerry<br>second line"))
    hidden = QLabel("on a page nobody saw")
    layout.addWidget(hidden)
    window.show()
    hidden.hide()

    walk.caught(window)

    assert walk.record == ["QDialog: Title / Files < 5 MB are read / Tom & Jerry second line"], (
        walk.record
    )


def test_a_slot_exception_is_reported_by_one_guard_only(qtbot, tmp_path: Path) -> None:
    """
    Given  a slot raised and a guard already reported it
    When   a later guard looks for what no step expected
    Then   it finds nothing, until another slot raises, and the result keeps both
    """
    walk = walkthrough.Walk(root=tmp_path, service=build_app_services())

    walk._on_exception(RuntimeError, RuntimeError("first"), None)
    first = walk._unexpected()
    again = walk._unexpected()
    walk._on_exception(ValueError, ValueError("second"), None)
    second = walk._unexpected()

    assert "an exception in a slot: RuntimeError: first" in first
    assert again == []
    assert second
    assert all(line.startswith("an exception in a slot:") and "second" in line for line in second)
    kept = walk.result()["exceptions"]
    assert "RuntimeError: first" in kept
    assert "ValueError: second" in kept


@pytest.mark.parametrize(
    ("text", "words", "expected"),
    [
        ("Hello World", "Hello World", True),
        ("hello   world\n", "Hello World", True),
        ("Document: Hello Wor1d", "Hello World", True),
        ("Hello Warld", "Hello World", True),
        ("First Pag", "First Page", True),
        ("Goodbye", "Hello World", False),
        ("Hello", "Hello World", False),
        ("", "Hello World", False),
    ],
)
def test_a_reading_is_compared_with_the_slack_of_one_character_in_eight(
    text: str, words: str, expected: bool
) -> None:
    """
    Given  a text the reader made of a page and the words drawn on it
    When   they are compared
    Then   case, spacing and one wrong, missing or extra character do not matter, a word does
    """
    assert walkthrough._reads(text, words) is expected


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


# ── The steps each system adds ───────────────────────────────────────────────

#: The walk's own steps, in order, for the expectations below.
_OWN = [
    "start",
    "choose-folder",
    "run",
    "open",
    "correct",
    "export",
    "run-again",
    "stop-after-file",
    "stop-now",
    "finish",
    "restart",
    "other-windows",
    "shut-down",
]


def _names(plan: tuple[tuple[str, str], ...]) -> list[str]:
    """Return the step names of *plan*."""
    return [name for name, _method in plan]


def test_a_linux_walk_runs_the_results_folder_past_the_limit_only() -> None:
    """
    Given  the steps of the walk
    When   the step list is built for a Linux machine
    Then   the long results folder follows the first run — the run into it is the same work there
           — and the system's other two steps, which need Windows or macOS, do not run
    """
    expected = [*_OWN[:3], "long-output-path", *_OWN[3:]]

    assert _names(walkthrough.walk_steps(proofreading=False, platform="linux")) == expected


def test_a_windows_walk_runs_its_export_steps_and_its_two_spellings() -> None:
    """
    Given  the steps of the walk
    When   the step list is built for Windows
    Then   the long output path follows the first run, the held-open export follows the export,
           and the folder reached through two spellings follows the restart
    """
    expected = [
        *_OWN[:3],
        "long-output-path",
        *_OWN[3:6],
        "export-held-open",
        *_OWN[6:10],
        "restart",
        "case-spelling",
        *_OWN[11:],
    ]

    assert _names(walkthrough.walk_steps(proofreading=False, platform="win32")) == expected


def test_a_macos_walk_runs_the_two_spellings_only() -> None:
    """
    Given  the steps of the walk
    When   the step list is built for macOS
    Then   only the folder reached through two spellings is added, after the restart
    """
    expected = [*_OWN[:11], "case-spelling", *_OWN[11:]]

    assert _names(walkthrough.walk_steps(proofreading=False, platform="darwin")) == expected


def test_the_proofreading_steps_and_the_systems_own_both_run() -> None:
    """
    Given  a walk that proofreads, on a Windows machine
    When   the step list is built
    Then   the proofreading steps follow the steps they belong to, and so do the system's own
    """
    expected = [
        "start",
        "choose-folder",
        "run",
        "long-output-path",
        "open",
        "proofreading-items",
        "correct",
        "proofreading-checked",
        "export",
        "export-held-open",
        "run-again",
        "stop-after-file",
        "stop-now",
        "finish",
        "restart",
        "review-dropped",
        "case-spelling",
        "other-windows",
        "shut-down",
    ]

    assert _names(walkthrough.walk_steps(proofreading=True, platform="win32")) == expected


def test_the_long_results_folder_is_past_the_windows_limit(tmp_path: Path) -> None:
    """
    Given  a results folder
    When   the walk makes it long enough for the Windows step
    Then   the path is past 260 characters, and every name in it is one a file system takes
    """
    folder = walkthrough._deep_folder(tmp_path)

    assert len(str(folder)) > 260
    assert all(len(part) < 200 for part in folder.relative_to(tmp_path).parts)
