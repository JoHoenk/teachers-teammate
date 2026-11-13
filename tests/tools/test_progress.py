"""Tests of the progress display: the line, the estimate, the waiting thread and the wiring.

Everything here runs offline and without sleeping: the clock is a fake a test moves by hand, the
stream is a ``StringIO``, and the waiting display is driven by :meth:`Progress.tick` instead of by
its thread.
"""

from __future__ import annotations

from dataclasses import replace
import io
from pathlib import Path

from tests.tools.test_runner import (
    STAMP,
    _Harness,
    _matrix,
    _records,
    _row,
    _session,
    _write_session,
)
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from tools.model_eval import results, runner
from tools.model_eval.progress import Progress
from tools.model_eval.smoke import Smoke
from tools.model_eval.structured_check import FIXTURES, StructuredOutputCheck
from tools.verify_model_catalog import _report_request


class _Terminal(io.StringIO):
    """A stream that says it is a terminal, so the display rewrites one line in place."""

    def isatty(self) -> bool:
        """Say that this stream is a terminal."""
        return True


class _Clock:
    """A clock a test moves by hand, so no test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the time the test set."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Move the clock forward by *seconds*."""
        self.now += seconds


def _stats() -> ChatStats:
    """Return the statistics of a finished answer."""
    return ChatStats(done_reason="stop", prompt_eval_count=100, eval_count=100)


class _ScriptedChat:
    """A chat port that answers with an empty review instead of asking a server."""

    def __init__(self) -> None:
        self.num_ctx = 8192

    def chat_with_stats(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        options: dict | None = None,
        think: bool | str = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Return an empty but valid review answer."""
        return '{"sentences": [], "unclear": []}', _stats()


# ── the line ──────────────────────────────────────────────────────────────────


def test_the_line_shows_position_label_and_time_left() -> None:
    """
    Given  a run of 126 requests of which 41 are done after 23 minutes
    When   the next request starts
    Then   the line shows the bar, the position, the label, the elapsed time and a rough time
           left in minutes
    """
    clock = _Clock()
    terminal = _Terminal()
    progress = Progress(126, stream=terminal, clock=clock)
    for number in range(41):
        progress.start(f"m · text {number}")
        clock.advance(23 * 60 / 41)
        progress.finish()
    terminal.seek(0)
    terminal.truncate(0)

    progress.start("gemma4:e4b · full page DE · run 2")

    line = terminal.getvalue()
    assert line.startswith("\r[")
    assert "#" in line and "." in line
    assert "41/126" in line
    assert "gemma4:e4b · full page DE · run 2" in line
    assert "23 min" in line
    assert "~48 min left" in line
    assert "waiting 0s" in line


def test_growing_the_total_keeps_the_position() -> None:
    """
    Given  a run of 18 requests of which 6 are done
    When   a second round adds 18 more
    Then   the position stays at 6 done and the total grows, so the bar does not jump back
    """
    clock = _Clock()
    stream = io.StringIO()
    progress = Progress(18, stream=stream, clock=clock, style="lines")
    for number in range(6):
        progress.start(f"m · text {number}")
        clock.advance(10)
        progress.finish()

    progress.grow(18)

    assert (progress.done, progress.total) == (6, 36)
    progress.start("m · text 6")
    progress.finish()
    assert stream.getvalue().splitlines()[-1].startswith("7/36  m · text 6")


def test_skipping_requests_closes_the_run_at_a_full_bar() -> None:
    """
    Given  a run of 18 requests of which 5 are done, then a setup that will send no more
    When   the 13 requests it will never send are skipped
    Then   the total falls to 5, the bar is full, the line is closed once, and the estimate no
           longer counts requests that will not be made
    """
    clock = _Clock()
    stream = _Terminal()
    progress = Progress(18, stream=stream, clock=clock)
    for _ in range(5):
        progress.start("m · plain DE")
        clock.advance(10)
        progress.finish()

    progress.skip(13)
    progress.skip(0)

    assert progress.total == 5
    assert progress.done == 5
    assert "5/5" in stream.getvalue().split("\r")[-1]
    assert stream.getvalue().endswith("\n")
    assert stream.getvalue().count("\n") == 1


def test_skipping_never_takes_the_total_below_what_is_done() -> None:
    """
    Given  a run with 3 requests done
    When   more are skipped than remain
    Then   the total stops at the position
    """
    progress = Progress(4, stream=io.StringIO(), clock=_Clock(), style="lines")
    for _ in range(3):
        progress.start("m")
        progress.finish()

    progress.skip(10)

    assert progress.total == 3


def test_redirected_output_prints_one_line_per_request() -> None:
    """
    Given  a stream that is not a terminal
    When   three requests run
    Then   one plain line per request is printed, without a bar and without a carriage return
    """
    clock = _Clock()
    stream = io.StringIO()
    progress = Progress(3, stream=stream, clock=clock)
    for number in range(3):
        progress.start(f"m · text {number}")
        clock.advance(60)
        progress.finish()

    lines = stream.getvalue().splitlines()

    assert len(lines) == 3
    assert lines[0].startswith("1/3  m · text 0")
    assert lines[-1].startswith("3/3  m · text 2")
    assert "\r" not in stream.getvalue()
    assert "[" not in stream.getvalue()


def test_a_long_request_prints_still_waiting() -> None:
    """
    Given  a redirected run whose request has been waiting for 61 seconds
    When   the waiting display ticks
    Then   it prints one "still waiting" line, and nothing before the minute is over
    """
    clock = _Clock()
    stream = io.StringIO()
    progress = Progress(2, stream=stream, clock=clock, style="lines")
    progress.start("m · full page DE")

    clock.advance(30)
    progress.tick()
    assert stream.getvalue() == ""

    clock.advance(31)
    progress.tick()
    assert stream.getvalue().splitlines() == ["0/2  m · full page DE · still waiting 61s"]


def test_progress_off_prints_nothing() -> None:
    """
    Given  a run whose progress is off
    When   requests start, wait and finish
    Then   nothing is written at all, and the position is still counted
    """
    clock = _Clock()
    stream = io.StringIO()
    progress = Progress(2, stream=stream, clock=clock, style="off")

    progress.start("m · text")
    clock.advance(120)
    progress.tick()
    progress.finish()

    assert stream.getvalue() == ""
    assert progress.done == 1


# ── the wiring ────────────────────────────────────────────────────────────────


def test_gate_one_reports_every_request() -> None:
    """
    Given  a gate-1 check of one model at temperature 0, with a progress display
    When   the check runs
    Then   every request is named before it is sent and counted when its run is judged
    """
    stream = io.StringIO()
    progress = Progress(0, stream=stream, clock=_Clock(), style="lines")
    progress.grow(len(FIXTURES))
    check = StructuredOutputCheck(_ScriptedChat())

    runs = check.run(
        "m",
        0.0,
        on_run=lambda _run, _number, _runs: progress.finish(),
        on_request=_report_request(progress),
    )

    assert progress.done == len(runs) == len(FIXTURES)
    lines = stream.getvalue().splitlines()
    assert [line.split("  ")[0] for line in lines] == [
        f"{number}/{len(FIXTURES)}" for number in range(1, len(FIXTURES) + 1)
    ]
    assert lines[0].split("  ")[1] == f"m · {FIXTURES[0].name} · run 1"


def test_the_run_reports_every_missing_item(tmp_path: Path) -> None:
    """
    Given  a run of two texts of which one is already stored
    When   the run is started with a progress display
    Then   the display is built with the one missing text, and that text is named and counted
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _session(matrix)
    matrix.out.mkdir(parents=True)
    results.write_rows(item.rows_path, [_row("de/de-01")])
    _write_session(matrix, item, harness.probe)
    stream = io.StringIO()
    built: list[int] = []

    def new_progress(total: int) -> Progress:
        built.append(total)
        return Progress(total, stream=stream, clock=_Clock(), style="lines")

    result = runner.run_matrix(
        matrix, _records(), STAMP, harness.seams(), new_progress=new_progress
    )

    assert built == [1]
    assert result.rows == 1
    assert stream.getvalue().splitlines() == ["1/1  gemma4:e2b · de-02  <1 min"]


def test_a_session_skipped_by_a_failed_smoke_is_taken_off_the_total(tmp_path: Path) -> None:
    """
    Given  a run of one model at two temperatures, where the arm at 0.7 fails the smoke check
    When   the run is started with a progress display
    Then   the display is built with the texts of both arms, and the skipped arm's are taken off
           the total, so the run ends at 2/2 and not at 2/4
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7))
    stream = io.StringIO()
    shown: list[Progress] = []

    def new_progress(total: int) -> Progress:
        shown.append(Progress(total, stream=stream, clock=_Clock(), style="lines"))
        return shown[0]

    def smoke(_model, _mode, temperature) -> Smoke:
        return Smoke(passed=temperature == 0.2, reason="")

    seams = replace(harness.seams(), smoke=smoke)

    runner.run_matrix(matrix, _records(), STAMP, seams, new_progress=new_progress)

    assert (shown[0].done, shown[0].total) == (2, 2)
