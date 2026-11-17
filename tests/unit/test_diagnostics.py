"""Tests for the opt-in diagnostics: when they are on, what they write, what they leave out."""

from __future__ import annotations

from pathlib import Path
import threading

import pytest

from teachers_teammate.infrastructure import diagnostics
from teachers_teammate.infrastructure.diagnostics import Diagnostics


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def _make(tmp_path: Path, clock: _Clock) -> Diagnostics:
    return Diagnostics(
        tmp_path / "diagnostics.log", clock=clock, gui_thread_id=threading.get_ident()
    )


def test_the_diagnostics_are_off_unless_asked_for() -> None:
    """
    Given  a start without the switch and without the environment variable
    When   the diagnostics are asked whether they are requested
    Then   the answer is no
    """
    assert diagnostics.requested(["app"], {}) is False
    assert diagnostics.requested(["app"], {diagnostics.ENVIRONMENT_VARIABLE: "0"}) is False


def test_the_switch_and_the_environment_variable_turn_them_on() -> None:
    """
    Given  a start with --diagnose, or with the environment variable set to 1
    When   the diagnostics are asked whether they are requested
    Then   the answer is yes in both cases
    """
    assert diagnostics.requested(["app", "--diagnose"], {}) is True
    assert diagnostics.requested(["app"], {diagnostics.ENVIRONMENT_VARIABLE: "1"}) is True


def test_a_phase_is_written_with_the_time_since_the_start(tmp_path: Path) -> None:
    """
    Given  diagnostics that began a moment ago
    When   a phase is written
    Then   the log has one line with the phase and the seconds since the start
    """
    clock = _Clock()
    log = _make(tmp_path, clock)
    clock.now += 1.5

    log.phase("main window built")
    log.stop()

    text = (tmp_path / "diagnostics.log").read_text(encoding="utf-8")
    assert "[+  1.500 s] phase: main window built" in text


def test_a_silent_gui_thread_is_reported_with_where_it_is(tmp_path: Path) -> None:
    """
    Given  a GUI thread whose heartbeat stopped for longer than the stall limit
    When   the watcher looks
    Then   the log names the stall and the function the GUI thread is in, and when the
           heartbeat returns it says how long the stall lasted
    """
    clock = _Clock()
    log = _make(tmp_path, clock)
    clock.now += 0.5

    def _a_slow_call_of_the_gui_thread() -> None:
        log.check()

    _a_slow_call_of_the_gui_thread()
    clock.now += 0.5
    log.beat()
    log.stop()

    text = (tmp_path / "diagnostics.log").read_text(encoding="utf-8")
    assert "the GUI thread has not answered for 0.50 s" in text
    assert "_a_slow_call_of_the_gui_thread" in text
    assert "the GUI thread answers again after" in text


def test_a_short_pause_is_not_reported(tmp_path: Path) -> None:
    """
    Given  a heartbeat that is late by less than the stall limit
    When   the watcher looks
    Then   nothing is written
    """
    clock = _Clock()
    log = _make(tmp_path, clock)
    clock.now += 0.1

    log.check()
    log.stop()

    assert (tmp_path / "diagnostics.log").read_text(encoding="utf-8") == ""


def test_a_long_stall_is_reported_again_only_at_the_set_lengths(tmp_path: Path) -> None:
    """
    Given  a GUI thread that stays silent for a long time
    When   the watcher looks every 0.1 s for 2 s
    Then   it writes two reports (at 0.3 s and at 1 s), not twenty
    """
    clock = _Clock()
    log = _make(tmp_path, clock)

    for _ in range(20):
        clock.now += 0.1
        log.check()
    log.stop()

    text = (tmp_path / "diagnostics.log").read_text(encoding="utf-8")
    assert text.count("the GUI thread has not answered") == 2


def test_the_log_holds_code_places_and_no_folder_of_a_user(tmp_path: Path) -> None:
    """
    Given  a stall reported from a test file under the user's folders
    When   the log is read
    Then   the stack names the file by its name, without the folders in front of it
    """
    clock = _Clock()
    log = _make(tmp_path, clock)
    clock.now += 1.0

    log.check()
    log.stop()

    text = (tmp_path / "diagnostics.log").read_text(encoding="utf-8")
    assert "test_diagnostics.py" in text
    assert str(tmp_path) not in text


def test_an_old_big_log_is_cleared_at_the_start(tmp_path: Path) -> None:
    """
    Given  a log of an earlier session that is bigger than the limit
    When   the diagnostics start
    Then   the file begins empty
    """
    path = tmp_path / "diagnostics.log"
    path.write_text("x" * (diagnostics.MAX_LOG_BYTES + 1), encoding="utf-8")

    Diagnostics(path).stop()

    assert path.read_text(encoding="utf-8") == ""


def test_the_module_helpers_do_nothing_while_the_diagnostics_are_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  no diagnostics were started in this process
    When   a phase is written through the module function
    Then   nothing happens and nothing fails
    """
    monkeypatch.setattr(diagnostics, "_instance", None)

    diagnostics.phase("anything")

    assert diagnostics.get() is None
