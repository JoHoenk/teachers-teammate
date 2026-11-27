"""The one surface the window shows a failure through: it writes the line and asks once.

A notice the teacher closes is gone, so the line it stands for goes to the log file in the same
call.  A failure a catch point already wrote — a background task, the per-file net of a run —
is not written twice, and a bug that arrives over and over asks the teacher only once.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path
import threading

import pytest

from teachers_teammate.gui import _dialogs, _errors
from teachers_teammate.messages import log_failure
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import Answer, Notice

#: A value an error quotes when it chokes on it: no line of the log holds it.
SECRET = "the student wrote: secret"


@pytest.fixture(autouse=True)
def _forget_shown_bugs(monkeypatch) -> None:
    """Start every test as a process that has shown nothing yet."""
    monkeypatch.setattr(_errors._state, "last_shown", {})
    monkeypatch.setattr(_errors._state, "showing", False)


def _shown(monkeypatch) -> list[Notice]:
    """Record the notices the window would show; return the list they land in."""
    shown: list = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))
    return shown


@pytest.mark.gui
def test_a_failure_is_written_down_and_asked_about_once(monkeypatch, caplog) -> None:
    """
    Given  a failure an action hit, which no catch point has written yet
    When   the window shows it
    Then   the log holds its kind at ERROR without its words, and one notice asks the teacher
    """
    shown = _shown(monkeypatch)
    error = OSError(SECRET)

    with caplog.at_level(logging.ERROR, logger="teachers_teammate.gui._errors"):
        _errors.show_failure(None, dlg.save_failed("the settings file"), error)

    assert len(shown) == 1
    assert "OSError" in caplog.text
    assert SECRET not in caplog.text


@pytest.mark.gui
def test_a_failure_a_catch_point_wrote_is_not_written_again(monkeypatch, caplog) -> None:
    """
    Given  a failure a background task already wrote down
    When   the window shows it
    Then   the file keeps one line about it, and the teacher still gets the notice
    """
    shown = _shown(monkeypatch)
    error = OSError(SECRET)
    log_failure(logging.getLogger("teachers_teammate.gui._errors"), "The task ended", error)

    with caplog.at_level(logging.ERROR, logger="teachers_teammate.gui._errors"):
        _errors.show_failure(None, dlg.save_failed("the settings file"), error)

    assert len(shown) == 1
    assert caplog.text.count("OSError") == 1


@pytest.mark.gui
def test_a_bug_offers_the_folder_the_log_is_written_to(monkeypatch) -> None:
    """
    Given  a bug that reached no handler
    When   the window says so
    Then   the teacher is offered the folder of the log, and no word of the error is shown
    """
    opened: list[bool] = []
    monkeypatch.setattr(_errors, "open_log_folder", lambda: opened.append(True))
    asked: list = []
    monkeypatch.setattr(
        _dialogs,
        "choose",
        lambda _parent, question: asked.append(question) or Answer(dlg.OPEN_LOG_FOLDER_CHOICE),
    )

    _errors.show_unexpected(None, ValueError(SECRET))

    assert len(asked) == 1
    assert SECRET not in asked[0].body
    assert opened == [True]


@pytest.mark.gui
def test_the_same_kind_of_bug_is_shown_once_in_ten_seconds(monkeypatch) -> None:
    """
    Given  a bug of one kind that the window has just shown
    When   another bug of the same kind arrives within ten seconds
    Then   nothing is asked: a slot that fires with every key press does not bury the teacher
    """
    asked: list = []
    monkeypatch.setattr(
        _dialogs, "choose", lambda _parent, question: asked.append(question) or Answer()
    )

    _errors.show_unexpected(None, ValueError("first"))
    _errors.show_unexpected(None, ValueError("second"))

    assert len(asked) == 1


@pytest.mark.gui
def test_a_bug_that_keeps_coming_is_shown_again_after_the_window(monkeypatch) -> None:
    """
    Given  a bug of one kind shown at t=0 and the same kind held back at t=5
    When   the same kind arrives at t=11
    Then   it is shown again: a held-back bug does not restart the quiet window
    """
    asked: list = []
    monkeypatch.setattr(
        _dialogs, "choose", lambda _parent, question: asked.append(question) or Answer()
    )
    clock = iter([0.0, 5.0, 11.0])
    monkeypatch.setattr(_errors.time, "monotonic", lambda: next(clock))

    for _ in range(3):
        _errors.show_unexpected(None, ValueError("again"))

    assert len(asked) == 2


@pytest.mark.gui
def test_another_kind_of_bug_is_shown_at_once(monkeypatch) -> None:
    """
    Given  a bug of one kind the window has just shown
    When   a bug of another kind arrives
    Then   it is put to the teacher too: it may come from another place in the app
    """
    asked: list = []
    monkeypatch.setattr(
        _dialogs, "choose", lambda _parent, question: asked.append(question) or Answer()
    )

    _errors.show_unexpected(None, ValueError("a"))
    _errors.show_unexpected(None, KeyError("b"))

    assert len(asked) == 2


@pytest.mark.gui
def test_a_bug_from_another_thread_reaches_the_window(qtbot, monkeypatch) -> None:
    """
    Given  a window whose notices are installed
    When   a bug ends on another thread, as a hook may see it
    Then   the window is asked about it on the GUI thread
    """
    asked: list = []
    _errors.install_notices()
    seen_on: list[threading.Thread] = []
    monkeypatch.setattr(
        _dialogs,
        "choose",
        lambda _parent, question: (
            seen_on.append(threading.current_thread()),
            asked.append(question),
            Answer(),
        )[-1],
    )

    worker = threading.Thread(target=_errors.show_unexpected_anywhere, args=(ValueError(SECRET),))
    worker.start()
    worker.join()
    assert asked == []  # queued: nothing runs on the thread that raised it
    qtbot.waitUntil(lambda: bool(asked), timeout=5000)

    assert len(asked) == 1
    assert seen_on == [threading.main_thread()]


@pytest.mark.gui
def test_a_bug_before_a_window_exists_is_only_written_down(monkeypatch) -> None:
    """
    Given  a process that never got as far as its window
    When   a bug ends on a hook
    Then   nothing is asked, so a start that failed early reports itself on stderr alone
    """
    monkeypatch.setattr(_errors._state, "bridge", None)
    asked: list = []
    monkeypatch.setattr(_dialogs, "choose", lambda _parent, question: asked.append(question))

    _errors.show_unexpected_anywhere(ValueError(SECRET))

    assert asked == []


@pytest.mark.gui
def test_only_the_error_surface_shows_a_failure_notice() -> None:
    """
    Given  every module of ``teachers_teammate/gui``
    When   the calls to ``_dialogs.notify`` are read
    Then   ``gui/_errors.py`` is the only one that makes them: one home for what a failure says
    """
    gui = Path(_errors.__file__).parent
    offenders = sorted(
        f"{path.relative_to(gui.parent.parent)}:{line}"
        for path in gui.rglob("*.py")
        if path.name != "_errors.py"
        for line in _notify_lines(path)
    )
    assert not offenders, "\n".join(offenders)


def _notify_lines(path: Path) -> list[int]:
    """Return the lines of *path* that call ``_dialogs.notify``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "notify"
    ]
