"""Unit tests for teachers_teammate.infrastructure.workflow.coordinator."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import logging
import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    EditNote,
    UnclearNote,
)
from teachers_teammate.domain.run_summary import RunStatus, RunSummary
from teachers_teammate.domain.run_summary import FileOutcome, FileStatus
from teachers_teammate.infrastructure.workflow.coordinator import (
    UNEXPECTED_ERROR_MESSAGE,
    run_files,
)
from teachers_teammate.infrastructure.workflow.file_processor import ProcessingResult


# ── Helpers ────────────────────────────────────────────────────────────────


def _success(path: Path) -> ProcessingResult:
    return ProcessingResult(True, path.stem + ".docx", "preview.png", "raw", "corrected", "eval")


def _failure(path: Path) -> ProcessingResult:
    return ProcessingResult(False, "OCR model timeout", "", "", "", "")


# ── All succeed ────────────────────────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_run_files_all_succeed(tmp_path: Path) -> None:
    """
    Given  two files and a process_file function that succeeds for both
    When   run_files is called
    Then   both file names are in succeeded and failed is empty
    """
    f1 = tmp_path / "a.png"
    f2 = tmp_path / "b.png"
    summary = run_files(
        [f1, f2],
        process_file=_success,
        cleanup_tmp=MagicMock(),
    )
    assert summary.succeeded == ("a.png", "b.png")
    assert summary.failed == ()
    assert summary.skipped == ()
    assert summary.status is RunStatus.DONE


def test_run_files_one_failure(tmp_path: Path) -> None:
    """
    Given  two files and process_file fails on the second
    When   run_files is called
    Then   first file is in succeeded, second is in failed
    """
    f1 = tmp_path / "ok.png"
    f2 = tmp_path / "bad.png"

    def process(path: Path) -> ProcessingResult:
        if path == f2:
            return _failure(path)
        return _success(path)

    summary = run_files([f1, f2], process_file=process, cleanup_tmp=MagicMock())

    assert "ok.png" in summary.succeeded
    assert "bad.png" in summary.failed
    assert summary.exit_code == 1


def test_run_files_an_unexpected_error_fails_only_its_file(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """
    Given  three files and process_file raising a ValueError for the second
    When   run_files is called
    Then   files one and three are saved, the second is failed, and the traceback is logged
    """
    files = [tmp_path / f"{name}.png" for name in ("one", "two", "three")]

    def process(path: Path) -> ProcessingResult:
        if path == files[1]:
            raise ValueError("the stage broke")
        return _success(path)

    done: list[FileOutcome] = []
    with caplog.at_level(logging.ERROR):
        summary = run_files(
            files, process_file=process, cleanup_tmp=MagicMock(), on_file_done=done.append
        )

    assert summary.succeeded == ("one.png", "three.png")
    assert summary.failed == ("two.png",)
    assert [event.status for event in done] == [
        FileStatus.SAVED,
        FileStatus.FAILED,
        FileStatus.SAVED,
    ]
    assert done[1].error == UNEXPECTED_ERROR_MESSAGE
    assert any("the stage broke" in line for line in caplog.text.splitlines())


@pytest.mark.parametrize("control", [KeyboardInterrupt, SystemExit])
def test_run_files_leaves_a_control_exception_to_the_caller(
    tmp_path: Path, control: type[BaseException]
) -> None:
    """
    Given  a process_file function that raises an interrupt or a SystemExit
    When   run_files is called
    Then   the exception reaches the caller: it is not one file's failure
    """

    def process(_path: Path) -> ProcessingResult:
        raise control

    with pytest.raises(control):
        run_files([tmp_path / "a.png"], process_file=process, cleanup_tmp=MagicMock())


def test_run_files_a_skipped_file_is_neither_succeeded_nor_failed(tmp_path: Path) -> None:
    """
    Given  two files and process_file reports the second as skipped
    When   run_files is called
    Then   the second is listed under skipped only, and on_file_done carries the status
    """
    f1 = tmp_path / "text.txt"
    f2 = tmp_path / "scan.png"

    def process(path: Path) -> ProcessingResult:
        if path == f2:
            return ProcessingResult(False, "Needs text recognition", "", "", "", "", skipped=True)
        return _success(path)

    done: list = []

    summary = run_files(
        [f1, f2], process_file=process, cleanup_tmp=MagicMock(), on_file_done=done.append
    )

    assert (summary.succeeded, summary.failed, summary.skipped) == (
        ("text.txt",),
        (),
        ("scan.png",),
    )
    assert [event.status for event in done] == [FileStatus.SAVED, FileStatus.SKIPPED]


# ── Callbacks ─────────────────────────────────────────────────────────────


@pytest.mark.use_case("Batch_OCR_Processing")
def test_run_files_on_file_started_called_per_file(tmp_path: Path) -> None:
    """
    Given  two files and an on_file_started callback
    When   run_files is called
    Then   on_file_started is called with (source_id, name, idx, total) for each file
    """
    f1 = tmp_path / "x.png"
    f2 = tmp_path / "y.png"
    on_started = MagicMock()

    run_files([f1, f2], process_file=_success, cleanup_tmp=MagicMock(), on_file_started=on_started)

    assert on_started.call_count == 2
    first_call_args = on_started.call_args_list[0][0]
    source_id, name, idx, total = first_call_args
    assert name == "x.png"
    assert idx == 1
    assert total == 2


@pytest.mark.use_case("Batch_OCR_Processing")
def test_run_files_on_file_done_called_per_file(tmp_path: Path) -> None:
    """
    Given  two files and an on_file_done callback
    When   run_files is called
    Then   on_file_done is called once per file with a FileOutcome
    """
    f1 = tmp_path / "doc.png"
    on_done = MagicMock()

    run_files([f1], process_file=_success, cleanup_tmp=MagicMock(), on_file_done=on_done)

    assert on_done.call_count == 1
    result = on_done.call_args[0][0]
    assert isinstance(result, FileOutcome)
    assert result.name == "doc.png"
    assert result.status is FileStatus.SAVED


# ── Stop event ─────────────────────────────────────────────────────────────


def test_run_files_stop_event_set_skips_all_files(tmp_path: Path) -> None:
    """
    Given  stop_event is already set before run_files begins
    When   run_files is called with two files
    Then   process_file is never called and both files are reported as not started
    """
    f1 = tmp_path / "a.png"
    f2 = tmp_path / "b.png"
    stop = threading.Event()
    stop.set()
    process_file = MagicMock()

    summary = run_files(
        [f1, f2],
        process_file=process_file,
        cleanup_tmp=MagicMock(),
        stop_event=stop,
    )

    process_file.assert_not_called()
    assert summary.succeeded == ()
    assert summary.failed == ()
    assert summary.not_started == ("a.png", "b.png")
    assert summary.status is RunStatus.STOPPED
    assert summary.exit_code == 0


def test_run_files_stop_after_file_lets_the_current_file_finish(tmp_path: Path) -> None:
    """
    Given  three files and "Stop after this file" is pressed while the first one is processed
    When   run_files is called
    Then   the first file succeeds completely, the other two are not started, and the run is
           stopped, not failed
    """
    files = [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
    stop_after = threading.Event()
    reporter = MagicMock()

    def process(path: Path) -> ProcessingResult:
        stop_after.set()
        return _success(path)

    summary = run_files(
        files,
        process_file=process,
        cleanup_tmp=MagicMock(),
        stop_after_file_event=stop_after,
        reporter=reporter,
    )

    assert summary.succeeded == ("a.png",)
    assert summary.not_started == ("b.png", "c.png")
    assert summary.status is RunStatus.STOPPED
    reporter.status.assert_any_call("\nStopped by user: 2 file(s) not started.")


def test_run_files_stop_on_the_last_file_after_it_finished_is_done(tmp_path: Path) -> None:
    """
    Given  one file and "Stop after this file" is pressed while it is processed
    When   run_files is called
    Then   nothing was left undone, so the run is done (not stopped)
    """
    stop_after = threading.Event()

    def process(path: Path) -> ProcessingResult:
        stop_after.set()
        return _success(path)

    summary = run_files(
        [tmp_path / "a.png"],
        process_file=process,
        cleanup_tmp=MagicMock(),
        stop_after_file_event=stop_after,
    )

    assert summary.status is RunStatus.DONE


def test_run_files_a_stopped_file_is_neither_succeeded_nor_failed(tmp_path: Path) -> None:
    """
    Given  two files and process_file reports the first as stopped by "Stop now"
    When   run_files is called
    Then   it is listed under stopped only, no error is logged, on_file_done carries the status,
           and the second file is not started
    """
    f1 = tmp_path / "a.png"
    f2 = tmp_path / "b.png"
    stop = threading.Event()
    done: list = []

    def process(path: Path) -> ProcessingResult:
        stop.set()
        return ProcessingResult(
            False,
            "Stopped before proofreading.",
            "p.png",
            "raw",
            "",
            "",
            stopped=True,
            stopped_stage="proofreading",
        )

    summary = run_files(
        [f1, f2],
        process_file=process,
        cleanup_tmp=MagicMock(),
        stop_event=stop,
        on_file_done=done.append,
    )

    assert summary.stopped == ("a.png",)
    assert summary.failed == ()
    assert summary.not_started == ("b.png",)
    assert summary.status is RunStatus.STOPPED
    assert [event.status for event in done] == [FileStatus.STOPPED]
    assert done[0].stopped_stage == "proofreading"


# ── cleanup_tmp always called ──────────────────────────────────────────────


def test_run_files_cleanup_called_after_success(tmp_path: Path) -> None:
    """
    Given  a normal run
    When   run_files completes
    Then   cleanup_tmp is called exactly once
    """
    f = tmp_path / "doc.png"
    cleanup = MagicMock()

    run_files([f], process_file=_success, cleanup_tmp=cleanup)

    cleanup.assert_called_once()


def test_run_files_cleanup_called_even_when_stopped(tmp_path: Path) -> None:
    """
    Given  stop_event is set so no files are processed
    When   run_files completes
    Then   cleanup_tmp is still called (via finally)
    """
    stop = threading.Event()
    stop.set()
    cleanup = MagicMock()

    run_files([], process_file=MagicMock(), cleanup_tmp=cleanup, stop_event=stop)

    cleanup.assert_called_once()


def test_run_files_empty_list_returns_empty_and_cleans_up(tmp_path: Path) -> None:
    """
    Given  an empty file list
    When   run_files is called
    Then   nothing is reported and cleanup_tmp is called
    """
    cleanup = MagicMock()

    summary = run_files([], process_file=MagicMock(), cleanup_tmp=cleanup)

    assert summary == RunSummary()
    cleanup.assert_called_once()


def test_run_files_reports_the_outcome_without_the_texts(tmp_path: Path) -> None:
    """
    Given  a file whose result carries Markdown text and an on_file_done callback
    When   run_files is called
    Then   the FileOutcome it receives names the file and its status, and carries no text
    """
    f1 = tmp_path / "doc.png"
    on_done = MagicMock()

    def _markdown(_file: Path) -> ProcessingResult:
        return ProcessingResult(True, "doc.docx", "", "**x**", "", "", text_format="markdown")

    run_files([f1], process_file=_markdown, cleanup_tmp=MagicMock(), on_file_done=on_done)

    on_done.assert_called_once()
    result = on_done.call_args[0][0]
    assert isinstance(result, FileOutcome)
    assert result.source_id == str(f1.resolve())
    assert result.status is FileStatus.SAVED
    assert not hasattr(result, "raw_text")


def test_run_files_reports_the_counts_of_the_diff_not_of_the_notes(tmp_path: Path) -> None:
    """
    Given  a result whose single note explains an edit that the word diff splits in two hunks,
           plus one unclear expression
    When   run_files() reports the file
    Then   the summary line counts the two hunks and the unclear expression
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=8, raw_end=18, original="his friend", corrected="her friends"
                    ),
                ),
                sentences=(0,),
                category="word_choice",
                explanation="Agreement.",
            ),
        ),
        unclear=(
            UnclearNote(
                raw_start=0, raw_end=3, expression="She", why="Who?", recommendation="Anna"
            ),
        ),
    )
    result = ProcessingResult(
        True,
        "doc.docx",
        "preview.png",
        "She met his friend.",
        "She met her friends.",
        "",
        correction_review=review,
    )
    reporter = MagicMock()

    run_files(
        [tmp_path / "doc.png"],
        process_file=lambda _p: result,
        cleanup_tmp=lambda: None,
        reporter=reporter,
    )

    reporter.status.assert_any_call("       \u2192 2 changes, 1 unclear")
