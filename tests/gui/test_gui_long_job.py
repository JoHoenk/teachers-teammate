"""Tests for :mod:`teachers_teammate.gui._long_job`: the one base every long job uses."""

from __future__ import annotations

from typing import Any

import pytest

from teachers_teammate.exceptions import ModelDownloadCancelledError
from teachers_teammate.gui import _long_job


class _Job(_long_job.LongJobThread):
    """A job whose work is a callable the test hands it."""

    def __init__(self, work: Any) -> None:
        super().__init__()
        self._work = work

    def work(self) -> Any:
        return self._work(self)

    def error_text(self, exc: BaseException) -> str:
        return f"the job failed: {exc}"


@pytest.fixture(autouse=True)
def _restore_live_jobs() -> Any:
    """Leave the registry of running jobs as it was, so one test cannot hide another's job."""
    before = set(_long_job._LIVE)
    yield
    _long_job._LIVE.intersection_update(before)


@pytest.mark.gui
def test_a_finished_job_carries_its_result(qtbot) -> None:
    """
    Given  a job that works something out
    When   it runs to its end
    Then   finished_ok carries the result, which is also kept on the job, and nothing failed
    """
    job = _Job(lambda _self: "page one")
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    job.run()

    assert results == ["page one"]
    assert job.result == "page one"
    assert errors == []


@pytest.mark.gui
def test_a_failed_job_reports_the_sentence_of_its_error_text(qtbot) -> None:
    """
    Given  a job whose work raises
    When   it runs
    Then   finished_err carries the sentence the job makes of the failure, and nothing
           reported success
    """

    def boom(_self: _Job) -> None:
        raise RuntimeError("no such model")

    job = _Job(boom)
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    job.run()

    assert errors == ["the job failed: no such model"]
    assert results == []


@pytest.mark.gui
def test_a_cancelled_download_is_reported_as_aborted(qtbot) -> None:
    """
    Given  a job whose work reports that its download was cancelled
    When   it runs
    Then   finished_err says "aborted", which every window shows as a stop and not as a
           failure
    """

    def cancelled(_self: _Job) -> None:
        raise ModelDownloadCancelledError("The download was cancelled.")

    job = _Job(cancelled)
    errors: list[str] = []
    job.finished_err.connect(errors.append)

    job.run()

    assert errors == [_long_job.ABORTED]


@pytest.mark.gui
def test_a_stopped_job_ends_between_two_chunks_and_says_aborted(qtbot) -> None:
    """
    Given  a job that waits until it is stopped and then returns normally
    When   it is stopped while it runs
    Then   it reports "aborted" instead of a success, so a window shows no finished model
    """
    job = _Job(lambda self: self.stop_event.wait(5))
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    job.stop()
    job.run()

    assert job.stopped is True
    assert errors == [_long_job.ABORTED]
    assert results == []


@pytest.mark.gui
def test_a_started_job_is_held_until_it_has_ended(qtbot) -> None:
    """
    Given  a job that is still running
    When   it has been started, and again once it has ended and the next one starts
    Then   it is held from its start — a window may let go of it while it runs, and Qt
           aborts the whole process when a running QThread is destroyed — and dropped once
           it has finished
    """
    job = _Job(lambda self: self.stop_event.wait(5))

    job.start()
    try:
        assert job in _long_job._LIVE
    finally:
        job.stop()
        job.wait(5000)

    other = _Job(lambda _self: None)
    other.start()
    other.wait(5000)

    assert job not in _long_job._LIVE
