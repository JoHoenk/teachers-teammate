"""The one log file: where a warning or an error of the app goes, once per process.

The installed window has no console, so the log file is the only place a traceback reaches.
These tests cover the file being there, being one, being rotated, and the app starting all
the same when it cannot be written.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from teachers_teammate.infrastructure import logging_setup
from teachers_teammate.infrastructure.workflow.coordinator import run_files
from teachers_teammate.infrastructure.workflow.file_processor import ProcessingResult
from teachers_teammate.interfaces import Reporter

_logger = logging.getLogger("tests.logging_setup")


class _QuietReporter(Reporter):
    """A reporter that says nothing: the test reads the log file, not the run's lines."""

    def status(self, line: str) -> None:
        return None

    def warn(self, line: str) -> None:
        return None

    def error(self, line: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _logging_back_to_its_previous_state():
    """Leave the root logger as it was: this module's handlers go, and the state is reset."""
    root = logging.getLogger()
    handlers_before = list(root.handlers)
    level_before = root.level
    logging_setup._installed = logging_setup._Installed()
    yield
    for handler in list(root.handlers):
        if handler not in handlers_before:
            handler.close()
            root.removeHandler(handler)
    root.setLevel(level_before)
    logging_setup._installed = logging_setup._Installed()


def _file_handlers() -> list[logging.Handler]:
    """Return the file handlers start_logging() installed (pytest has one of its own)."""
    installed = logging_setup._installed
    return [handler for handler in installed.handlers if hasattr(handler, "baseFilename")]


def test_the_log_is_one_file_in_the_logs_folder_of_the_data_root(tmp_path: Path) -> None:
    """
    Given  a data root
    When   start_logging() runs twice
    Then   both calls name the same file, and the root logger carries one file handler
    """
    first = logging_setup.start_logging(tmp_path)
    second = logging_setup.start_logging(tmp_path)

    assert first == tmp_path / "logs" / "teachers-teammate.log"
    assert second == first
    assert len(_file_handlers()) == 1


def test_a_warning_reaches_the_file_when_the_process_has_no_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a windowed process, where ``sys.stderr`` is ``None``
    When   a warning is logged after start_logging()
    Then   the line is in the log file
    """
    monkeypatch.setattr("sys.stderr", None)
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    _logger.warning("the reader found no text on page 2")
    logging.shutdown()

    assert "the reader found no text on page 2" in path.read_text(encoding="utf-8")


def test_a_folder_that_cannot_be_written_leaves_the_app_running(tmp_path: Path) -> None:
    """
    Given  a data root that is a file, so the logs folder cannot be made
    When   start_logging() runs
    Then   it reports no log file instead of raising, and installs no file handler
    """
    blocked = tmp_path / "data"
    blocked.write_text("not a folder", encoding="utf-8")

    assert logging_setup.start_logging(blocked) is None
    assert _file_handlers() == []
    assert not (blocked / "logs").exists()


def test_the_file_rotates_when_it_reaches_its_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a log file of a small maximum size
    When   more lines are logged than fit into it
    Then   the lines of the first file are kept in a rotated copy
    """
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 200)
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    for index in range(6):
        _logger.warning("line %d with some words to fill the file up", index)
    logging.shutdown()

    assert path.with_name(path.name + ".1").is_file()


def test_an_unexpected_error_leaves_its_kind_and_frames_but_not_its_message(
    tmp_path: Path,
) -> None:
    """
    Given  a run whose stage raises an error whose message quotes a student's text
    When   the file is processed
    Then   the log names the kind of error and the frames, and not the message
    """
    path = logging_setup.start_logging(tmp_path / "data")
    assert path is not None

    secret = "my secret sentence"

    def process(_path: Path) -> ProcessingResult:
        raise ValueError("the student wrote: " + secret)

    run_files(
        [tmp_path / "anna.png"],
        process_file=process,
        cleanup_tmp=lambda: None,
        reporter=_QuietReporter(),
    )
    logging.shutdown()

    text = path.read_text(encoding="utf-8")
    assert "Unexpected error while processing anna.png: ValueError" in text
    assert "process" in text
    assert secret not in text
