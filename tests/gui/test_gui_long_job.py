"""Tests for :mod:`teachers_teammate.gui._long_job`: the one base every long job uses."""

from __future__ import annotations

import threading
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
def test_a_job_stopped_while_it_runs_ends_and_says_aborted(qtbot) -> None:
    """
    Given  a running job that waits between two chunks and gives up when it is stopped
    When   it is stopped
    Then   it ends without waiting out its wait and reports "aborted", not a success
    """
    started = threading.Event()

    def chunks(job: _Job) -> None:
        started.set()
        if job.stop_event.wait(10):
            raise ModelDownloadCancelledError("The download was cancelled.")

    job = _Job(chunks)
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    with qtbot.waitSignal(job.finished_err, timeout=5000):
        job.start()
        assert started.wait(5), "the job never started"
        job.stop()
    assert job.wait(5000)  # the signal comes from run(); the thread is still finishing

    assert job.stopped is True
    assert errors == [_long_job.ABORTED]
    assert results == []


@pytest.mark.gui
def test_a_job_that_finished_before_it_noticed_the_stop_is_a_success(qtbot) -> None:
    """
    Given  a job that has done all its work when the stop arrives
    When   it returns normally
    Then   it reports its result, so a finished download is not shown as a cancelled one
    """
    job = _Job(lambda self: (self.stop(), "all pages")[1])
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    job.run()

    assert results == ["all pages"]
    assert errors == []


class _PullService:
    """The one call of the Ollama service a pull makes."""

    def __init__(self, on_pull: Any) -> None:
        self.ollama = self
        self._on_pull = on_pull

    def pull_ollama_model(self, url, model, progress_cb, abort_event) -> None:
        self._on_pull(abort_event)


@pytest.mark.gui
def test_a_pull_that_ends_because_it_was_stopped_says_aborted(qtbot) -> None:
    """
    Given  an Ollama pull whose stream simply ends when it is stopped
    When   it is stopped and the stream ends
    Then   the job reports "aborted" and no model name, so no window shows a model as installed
    """
    from teachers_teammate.gui._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

    job = _OllamaPullThread(_PullService(lambda _abort: None), "http://x", "llama3:8b")
    results: list[Any] = []
    errors: list[str] = []
    job.finished_ok.connect(results.append)
    job.finished_err.connect(errors.append)

    job.stop()
    job.run()

    assert errors == [_long_job.ABORTED]
    assert results == []


@pytest.mark.gui
def test_a_pull_that_ran_to_its_end_carries_the_model_name(qtbot) -> None:
    """
    Given  an Ollama pull that is not stopped
    When   it ends
    Then   finished_ok carries the model name
    """
    from teachers_teammate.gui._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

    job = _OllamaPullThread(_PullService(lambda _abort: None), "http://x", "llama3:8b")
    results: list[Any] = []
    job.finished_ok.connect(results.append)

    job.run()

    assert results == ["llama3:8b"]


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
