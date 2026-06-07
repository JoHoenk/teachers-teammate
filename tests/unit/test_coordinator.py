"""Unit tests for teachers_teammate.infrastructure.workflow.coordinator."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from teachers_teammate.infrastructure.workflow.coordinator import run_files
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
    succeeded, failed = run_files(
        [f1, f2],
        process_file=_success,
        cleanup_tmp=MagicMock(),
    )
    assert succeeded == ["a.png", "b.png"]
    assert failed == []


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

    succeeded, failed = run_files([f1, f2], process_file=process, cleanup_tmp=MagicMock())

    assert "ok.png" in succeeded
    assert "bad.png" in failed


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
    Then   on_file_done is called with 8 arguments for each file
    """
    f1 = tmp_path / "doc.png"
    on_done = MagicMock()

    run_files([f1], process_file=_success, cleanup_tmp=MagicMock(), on_file_done=on_done)

    assert on_done.call_count == 1
    args = on_done.call_args[0]
    assert len(args) == 8  # source_id, name, success, msg, preview, raw, corr, eval


# ── Stop event ─────────────────────────────────────────────────────────────


def test_run_files_stop_event_set_skips_all_files(tmp_path: Path) -> None:
    """
    Given  stop_event is already set before run_files begins
    When   run_files is called with two files
    Then   process_file is never called and ([], []) is returned
    """
    f1 = tmp_path / "a.png"
    f2 = tmp_path / "b.png"
    stop = threading.Event()
    stop.set()
    process_file = MagicMock()

    succeeded, failed = run_files(
        [f1, f2],
        process_file=process_file,
        cleanup_tmp=MagicMock(),
        stop_event=stop,
    )

    process_file.assert_not_called()
    assert succeeded == []
    assert failed == []


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
    Then   result is ([], []) and cleanup_tmp is called
    """
    cleanup = MagicMock()

    succeeded, failed = run_files([], process_file=MagicMock(), cleanup_tmp=cleanup)

    assert succeeded == []
    assert failed == []
    cleanup.assert_called_once()
