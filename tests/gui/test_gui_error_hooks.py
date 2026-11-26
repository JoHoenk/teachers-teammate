"""What Qt does with an exception nobody caught: it reaches the hook that writes the log.

The installed window has no console, so an exception raised in a slot or in a ``QThread.run``
would reach nobody.  PySide6 hands it to ``sys.excepthook``, which
``infrastructure/logging_setup`` replaces with the net that writes the file — this holds the two
halves together, so a Qt upgrade that stops calling the hook fails here instead of silently
losing the line.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QThread, Signal, Slot

from teachers_teammate.infrastructure import logging_setup

pytestmark = pytest.mark.usefixtures("process_logging_untouched")


#: What an exception quotes when it chokes on a text: a line of the log never holds it.
SECRET = "the student wrote: secret"


class _RaisingSlot(QObject):
    """An object whose slot raises: what a widget does when the code behind it fails."""

    go = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.go.connect(self._read)

    @Slot()
    def _read(self) -> None:
        raise ValueError(SECRET)


class _RaisingThread(QThread):
    """A thread whose ``run`` raises: nothing in the window can catch it."""

    def run(self) -> None:
        raise ValueError(SECRET)


def _written(path: Path) -> str:
    """Return the whole log file, after the handlers flushed it."""
    logging.shutdown()
    return path.read_text(encoding="utf-8")


@pytest.mark.gui
def test_an_exception_in_a_slot_reaches_the_log_file(qtbot, tmp_path: Path) -> None:
    """
    Given  a process whose logging was started, and a slot that raises
    When   Qt calls the slot
    Then   the file names the type and the frames of the error, and not its message
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None
    raiser = _RaisingSlot()

    raiser.go.emit()

    written = _written(path)
    assert logging_setup.WHAT_UNEXPECTED in written
    assert "ValueError" in written
    assert "in _read" in written
    assert "secret" not in written


@pytest.mark.gui
def test_an_exception_in_a_thread_run_reaches_the_log_file(qtbot, tmp_path: Path) -> None:
    """
    Given  a process whose logging was started, and a QThread whose run raises
    When   the thread runs
    Then   the file names the type and the frames of the error, and not its message
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None
    thread = _RaisingThread()

    thread.start()
    assert thread.wait(5000)

    written = _written(path)
    assert logging_setup.WHAT_UNEXPECTED in written
    assert "ValueError" in written
    assert "in run" in written
    assert "secret" not in written
