"""Tests for the wording of a finished run (teachers_teammate.gui._run_status)."""

from __future__ import annotations

from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.gui._run_status import RunTone, describe_run


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
