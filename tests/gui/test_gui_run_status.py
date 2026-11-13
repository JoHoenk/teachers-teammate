"""Tests for the wording of a finished run (teachers_teammate.gui._run_status)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.performance_estimate import TimeRange
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.gui._run_status import (
    RunPace,
    RunTone,
    describe_run,
    time_left_note,
    time_left_tooltip,
)


def test_done_fills_the_bar_and_counts_the_processed_files() -> None:
    """
    Given  a run in which every file succeeded and one was skipped
    When   it is described
    Then   the bar shows Done, the notification counts files and skipped, and the bar is full
    """
    text = describe_run(RunSummary(succeeded=("a", "b"), skipped=("c",)))

    assert text.progress == "Done ✓"
    assert text.tray == "Done — 2 file(s) processed, 1 skipped"
    assert text.complete is True
    assert text.tone is RunTone.INFO


def test_stopped_names_what_is_done_unfinished_and_not_started() -> None:
    """
    Given  3 of 10 documents done, one stopped, six not started
    When   it is described
    Then   the text counts all three and the bar is not full
    """
    summary = RunSummary(succeeded=("a", "b", "c"), stopped=("d",), not_started=tuple("efghij"))

    text = describe_run(summary)

    assert text.progress == "Stopped — 3 of 10 done, 1 unfinished, 6 not started"
    assert text.tray == text.progress
    assert text.complete is False
    assert text.tone is RunTone.INFO


def test_stopped_counts_skipped_documents_in_the_total() -> None:
    """
    Given  2 documents done, 1 skipped (a scan while recognition is off), 2 not started
    When   the stopped run is described
    Then   the total is 5 and the skipped document is named, so the numbers add up
    """
    summary = RunSummary(succeeded=("a", "b"), skipped=("c",), not_started=("d", "e"))

    text = describe_run(summary)

    assert text.progress == "Stopped — 2 of 5 done, 1 skipped, 2 not started"


def test_a_failure_beats_a_stop() -> None:
    """
    Given  a run that failed one file and was also stopped
    When   it is described
    Then   it says it finished with errors, as a warning
    """
    text = describe_run(RunSummary(failed=("a",), not_started=("b",)))

    assert text.progress == "Finished with errors — 1 failed"
    assert text.tone is RunTone.WARNING
    assert text.complete is False


def test_a_run_that_could_not_start_finished_with_errors() -> None:
    """
    Given  a run that could not start (an error, no failed file)
    When   it is described
    Then   it says it finished with errors
    """
    assert describe_run(RunSummary(error="no server")).progress == "Finished with errors"


# ── the time left while a run is going ─────────────────────────────────────


def test_the_estimate_answers_before_the_first_file() -> None:
    """
    Given  a run of 10 files with 20 pages and an estimate of 10 to 20 seconds per page
    When   the time left is asked for before any file is done
    Then   it is the estimate for the pages that are left
    """
    pace = RunPace(estimate=TimeRange(10.0, 20.0))
    pace.set_files(10)
    pace.set_pages(20)

    left = pace.time_left()

    assert left is not None
    assert left.low_s == pytest.approx(200.0)
    assert left.high_s == pytest.approx(400.0)


def test_the_run_own_pace_answers_after_the_first_file() -> None:
    """
    Given  a run of 3 files with 6 pages, one file of 2 pages done in 100 seconds, no estimate
    When   the time left is asked for
    Then   it is the run's own pace for the 4 pages that are left
    """
    pace = RunPace()
    pace.set_files(3)
    pace.set_pages(6)
    pace.file_done(100.0, 2)

    left = pace.time_left()

    assert left is not None
    assert left.low_s == pytest.approx(200.0)
    assert left.high_s == pytest.approx(200.0)


def test_the_first_files_blend_the_run_pace_with_the_estimate() -> None:
    """
    Given  an estimate of 10 to 20 seconds per page and one file of 2 pages done in 100 seconds
    When   the time left is asked for with 4 pages left
    Then   the figure is a third of the way from the estimate to the run's own pace
    """
    pace = RunPace(estimate=TimeRange(10.0, 20.0))
    pace.set_files(3)
    pace.set_pages(6)
    pace.file_done(100.0, 2)

    left = pace.time_left()

    assert left is not None
    assert left.low_s == pytest.approx((50.0 / 3 + 2 * 10.0 / 3) * 4)
    assert left.high_s == pytest.approx((50.0 / 3 + 2 * 20.0 / 3) * 4)


def test_three_files_of_its_own_pace_replace_the_estimate() -> None:
    """
    Given  an estimate of 10 to 20 seconds per page and three files done at 50 seconds a page
    When   the time left is asked for
    Then   the estimate no longer counts, because the run has shown what it does
    """
    pace = RunPace(estimate=TimeRange(10.0, 20.0))
    pace.set_files(4)
    pace.set_pages(8)
    for _ in range(3):
        pace.file_done(100.0, 2)

    left = pace.time_left()

    assert left is not None
    assert left.low_s == pytest.approx(100.0)
    assert left.high_s == pytest.approx(100.0)


def test_pages_left_come_from_the_page_counts_when_they_are_known() -> None:
    """
    Given  a run of 3 files with 6 pages, one file of 2 pages done
    When   the pages left are asked for
    Then   they are the pages of the whole run minus the pages done
    """
    pace = RunPace()
    pace.set_files(3)
    pace.set_pages(6)
    pace.file_done(10.0, 2)

    assert pace.pages_left() == 4


def test_pages_left_come_from_the_average_of_the_files_done() -> None:
    """
    Given  a run of 4 files whose page counts are unknown, two files of 2 and 4 pages done
    When   the pages left are asked for
    Then   the two files left are counted at the average of the two done
    """
    pace = RunPace()
    pace.set_files(4)
    pace.file_done(10.0, 2)
    pace.file_done(10.0, 4)

    assert pace.pages_left() == 6


def test_nothing_known_gives_no_figure() -> None:
    """
    Given  a run with no estimate and no file done
    When   the time left is asked for
    Then   there is none, because a guess the teacher would plan around is worse than nothing
    """
    pace = RunPace()
    pace.set_files(3)

    assert pace.time_left() is None


def test_a_run_with_no_pages_left_gives_no_figure() -> None:
    """
    Given  a run of one file of 2 pages that is done
    When   the time left is asked for
    Then   there is none, because nothing is left to do
    """
    pace = RunPace(estimate=TimeRange(10.0, 20.0))
    pace.set_files(1)
    pace.set_pages(2)
    pace.file_done(10.0, 2)

    assert pace.time_left() is None


def test_the_note_says_how_long_is_left() -> None:
    """
    Given  420 seconds left
    When   the note is written
    Then   it reads as minutes, because a teacher plans in minutes
    """
    assert time_left_note(420.0) == "about 7 min left"


def test_the_tooltip_gives_the_range_behind_the_note() -> None:
    """
    Given  a range of 7 to 12 minutes left
    When   the tooltip is written
    Then   it names both ends, so the figure can be judged
    """
    assert time_left_tooltip(420.0, 720.0) == (
        "Between 7 min and 12 min left, at the pace of this run."
    )
