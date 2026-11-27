"""The one log file: where a warning or an error of the app goes, once per process.

The installed window has no console, so the log file is the only place a traceback reaches.
These tests cover the file being there, being one, being rotated, the app starting all the same
when it cannot be written, and the nets that catch what reaches no handler at all.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys
import threading

import pytest

from teachers_teammate import __version__
from teachers_teammate.infrastructure import logging_setup
from teachers_teammate.infrastructure.workflow.coordinator import run_files
from teachers_teammate.infrastructure.workflow.file_processor import ProcessingResult
from teachers_teammate.interfaces import Reporter

_logger = logging.getLogger("tests.logging_setup")

pytestmark = pytest.mark.usefixtures("process_logging_untouched")


class _QuietReporter(Reporter):
    """A reporter that says nothing: the test reads the log file, not the run's lines."""

    def status(self, line: str) -> None:
        return None

    def warn(self, line: str) -> None:
        return None

    def error(self, line: str) -> None:
        return None


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


def _lines_holding(path: Path, what: str) -> list[str]:
    """Return the lines of *path* that hold *what*."""
    return [line for line in path.read_text(encoding="utf-8").splitlines() if what in line]


def test_a_thread_that_ends_with_an_error_is_logged(tmp_path: Path) -> None:
    """
    Given  a process whose logging was started
    When   a thread ends with an exception nobody caught
    Then   the file names the type and the frames, and not the message
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    quoted = "the student wrote forty-two apples"

    def read() -> None:
        raise ValueError(quoted)

    thread = threading.Thread(target=read, name="reader-7")
    thread.start()
    thread.join()
    logging.shutdown()

    lines = _lines_holding(path, logging_setup.WHAT_UNEXPECTED)
    assert len(lines) == 1
    assert "ValueError" in lines[0]
    assert "reader-7" in lines[0]
    written = path.read_text(encoding="utf-8")
    assert "in read" in written
    assert "forty-two apples" not in written


def test_an_exception_of_the_main_thread_is_logged(tmp_path: Path) -> None:
    """
    Given  a process whose logging was started
    When   an exception reaches sys.excepthook, as Qt hands it one from a slot
    Then   the file holds one such line, with the type and the frames
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    def read() -> None:
        raise ValueError("secret")

    try:
        read()
    except ValueError as error:
        sys.excepthook(ValueError, error, error.__traceback__)
    logging.shutdown()

    lines = _lines_holding(path, logging_setup.WHAT_UNEXPECTED)
    assert len(lines) == 1
    assert "ValueError" in lines[0]
    assert "in read" in path.read_text(encoding="utf-8")


def test_an_exception_python_could_not_raise_is_logged_and_not_shown(tmp_path: Path) -> None:
    """
    Given  a process whose logging was started with what the window shows a bug with
    When   a destructor raises while its object is collected
    Then   the file names the destructor and its frames, and not the message, and the entry
           point is not told: nothing the teacher did failed
    """
    reported: list[BaseException] = []
    path = logging_setup.start_logging(tmp_path, on_unexpected=reported.append)
    assert path is not None

    quoted = "the destructor choked on forty-two apples"

    class _Collects:
        def __del__(self) -> None:
            raise ValueError(quoted)

    _Collects()
    logging.shutdown()

    lines = _lines_holding(path, logging_setup.WHAT_IGNORED)
    assert len(lines) == 1
    written = path.read_text(encoding="utf-8")
    assert "in __del__" in written
    assert "forty-two apples" not in written
    assert reported == []


def test_the_entry_point_is_told_about_what_reached_no_handler(tmp_path: Path) -> None:
    """
    Given  a process whose logging was started with what the window shows a bug with
    When   an exception reaches a hook
    Then   the file holds its line and the entry point received the exception
    """
    reported: list[BaseException] = []
    path = logging_setup.start_logging(tmp_path, on_unexpected=reported.append)
    assert path is not None

    def read() -> None:
        raise ValueError("secret")

    try:
        read()
    except ValueError as error:
        sys.excepthook(ValueError, error, error.__traceback__)
    logging.shutdown()

    assert [type(one).__name__ for one in reported] == ["ValueError"]
    assert len(_lines_holding(path, logging_setup.WHAT_UNEXPECTED)) == 1


def test_a_native_crash_leaves_its_stack_in_its_own_file(
    tmp_path: Path, process_logging_untouched
) -> None:
    """
    Given  a data root
    When   start_logging() runs, twice, in two sessions
    Then   logs/crash.log is open for the stacks faulthandler reports, its handle kept, and each
           session wrote a header line with its time, version and process
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None
    crash_log = tmp_path / "logs" / logging_setup.CRASH_NAME
    handle = logging_setup._installed.crash_file
    assert handle is not None
    process_logging_untouched.assert_called_once_with(file=handle, all_threads=True)
    handle.close()
    logging_setup._installed = logging_setup._Installed()
    logging_setup._enable_crash_log(tmp_path / "logs")

    headers = crash_log.read_text(encoding="utf-8").splitlines()
    assert len(headers) == 2
    assert all(f"version {__version__} pid {os.getpid()}" in line for line in headers)


def test_a_crash_log_that_has_grown_too_large_starts_over(tmp_path: Path) -> None:
    """
    Given  a crash.log larger than the log file may grow
    When   the crash log is opened
    Then   it holds only the header of this session
    """
    folder = tmp_path / "logs"
    folder.mkdir()
    (folder / logging_setup.CRASH_NAME).write_text("x" * (logging_setup.MAX_BYTES + 1))

    logging_setup._enable_crash_log(folder)

    assert (folder / logging_setup.CRASH_NAME).read_text(encoding="utf-8").startswith("--- ")
    assert (folder / logging_setup.CRASH_NAME).stat().st_size < 200


def test_a_crash_log_that_cannot_be_opened_is_said_in_the_log(tmp_path: Path) -> None:
    """
    Given  a data folder where crash.log cannot be opened (a folder is in its place)
    When   start_logging() runs
    Then   the app starts, the log names the crash log's failure, and no handle is kept
    """
    (tmp_path / "logs" / logging_setup.CRASH_NAME).mkdir(parents=True)

    path = logging_setup.start_logging(tmp_path)
    assert path is not None
    logging.shutdown()

    assert logging_setup._installed.crash_file is None
    assert _lines_holding(path, "The crash log could not be opened: IsADirectoryError (errno 21")


def test_ctrl_c_is_not_logged_as_a_bug(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """
    Given  a process whose logging was started with what the window shows a bug with
    When   a KeyboardInterrupt reaches sys.excepthook
    Then   the file holds the header line and nothing else, the entry point is not told, and the
           console still gets the interpreter's report
    """
    reported: list[BaseException] = []
    path = logging_setup.start_logging(tmp_path, on_unexpected=reported.append)
    assert path is not None

    try:
        raise KeyboardInterrupt
    except KeyboardInterrupt as error:
        sys.excepthook(KeyboardInterrupt, error, error.__traceback__)
    logging.shutdown()

    written = path.read_text(encoding="utf-8").splitlines()
    assert len(written) == 1
    assert "Session:" in written[0]
    assert reported == []
    assert "KeyboardInterrupt" in capsys.readouterr().err


def test_a_console_gets_the_interpreters_report_and_the_line_only_the_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a process with a console whose logging was started
    When   an exception reaches sys.excepthook
    Then   the console shows it once, in full, as the interpreter does, and the file holds the
           redacted line
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    quoted = "seen on the console"
    try:
        raise ValueError(quoted)
    except ValueError as error:
        sys.excepthook(ValueError, error, error.__traceback__)
    logging.shutdown()

    shown = capsys.readouterr().err
    assert shown.count(quoted) == 1
    assert logging_setup.WHAT_UNEXPECTED not in shown
    written = path.read_text(encoding="utf-8")
    assert logging_setup.WHAT_UNEXPECTED in written
    assert quoted not in written


def test_a_hook_that_fails_to_show_the_error_is_logged(tmp_path: Path) -> None:
    """
    Given  an entry point whose on_unexpected raises
    When   an exception reaches a hook
    Then   the file holds the bug's line and the failure to show it
    """

    def refuse(error: BaseException) -> None:
        raise RuntimeError("no window")

    path = logging_setup.start_logging(tmp_path, on_unexpected=refuse)
    assert path is not None

    try:
        raise ValueError("secret")
    except ValueError as error:
        sys.excepthook(ValueError, error, error.__traceback__)
    logging.shutdown()

    assert len(_lines_holding(path, logging_setup.WHAT_UNEXPECTED)) == 1
    assert _lines_holding(path, "The unexpected error could not be shown: RuntimeError")


def test_the_file_starts_with_a_line_naming_this_session(tmp_path: Path) -> None:
    """
    Given  a data root
    When   start_logging() runs
    Then   the file's first line names the version, the system, whether it is frozen, the data
           root and the log detail, so a later reader can place what follows
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None
    logging.shutdown()

    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert "Session:" in first
    assert f"version {__version__}" in first
    assert "frozen no" in first
    assert str(tmp_path) in first
    assert "log detail normal" in first


def test_normal_holds_what_the_app_did_and_what_the_libraries_warned(
    tmp_path: Path,
) -> None:
    """
    Given  a log file written at the default detail
    When   the app says something at INFO and a library the app uses does the same
    Then   the app's line is in the file and the library's is not: only its warnings are
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    logging.getLogger("teachers_teammate.run").info("reading anna.png")
    logging.getLogger("httpx").info("HTTP Request: GET http://localhost:11434/")
    logging.shutdown()

    written = path.read_text(encoding="utf-8")
    assert "INFO teachers_teammate.run: reading anna.png" in written
    assert "httpx" not in written


def test_detailed_holds_what_the_libraries_say_as_well(tmp_path: Path) -> None:
    """
    Given  a log file written at the most detailed level
    When   the app and a library the app uses each say something at INFO
    Then   both lines are in the file, and a debug detail of the app's as well
    """
    path = logging_setup.start_logging(tmp_path, log_detail=logging_setup.LogDetail.DETAILED)
    assert path is not None

    logging.getLogger("teachers_teammate.run").info("reading anna.png")
    logging.getLogger("teachers_teammate.run").debug("the watch on the pages folder fired")
    logging.getLogger("httpx").info("HTTP Request: GET http://localhost:11434/")
    logging.shutdown()

    written = path.read_text(encoding="utf-8")
    assert "INFO teachers_teammate.run: reading anna.png" in written
    assert "DEBUG teachers_teammate.run: the watch on the pages folder fired" in written
    assert "INFO httpx: HTTP Request" in written


def test_errors_only_keeps_the_apps_progress_out_of_the_file(tmp_path: Path) -> None:
    """
    Given  a log file written at the level a flag asks for when something has to be read later
    When   the app reports what a run did
    Then   the file holds no line about it
    """
    path = logging_setup.start_logging(tmp_path, log_detail=logging_setup.LogDetail.ERRORS)
    assert path is not None

    logging.getLogger("teachers_teammate.run").info("reading anna.png")
    logging.shutdown()

    assert "reading anna.png" not in path.read_text(encoding="utf-8")


def test_the_flag_beats_the_setting(tmp_path: Path) -> None:
    """
    Given  a start whose command line asked for the most detailed level
    When   the window hands in its own, less detailed, setting afterwards
    Then   the flag holds: the reported detail is still the flag's, and nothing of the app's
           INFO lines is lost
    """
    path = logging_setup.start_logging(tmp_path, argv=["--log-level", "detailed"])
    assert path is not None

    assert logging_setup.set_log_detail(logging_setup.LogDetail.ERRORS, from_setting=True) is (
        logging_setup.LogDetail.DETAILED
    )
    logging.getLogger("teachers_teammate.run").info("reading anna.png")
    logging.shutdown()

    assert "reading anna.png" in path.read_text(encoding="utf-8")


def test_the_setting_decides_when_no_flag_did(tmp_path: Path) -> None:
    """
    Given  a start without a flag or an environment variable
    When   the window hands in a less detailed setting
    Then   the file follows it from that point on
    """
    path = logging_setup.start_logging(tmp_path)
    assert path is not None

    logging_setup.set_log_detail(logging_setup.LogDetail.ERRORS, from_setting=True)
    logging.getLogger("teachers_teammate.run").info("reading anna.png")
    logging.shutdown()

    assert "reading anna.png" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("argv", "environ", "detail"),
    [
        (["--log-level", "detailed"], {}, logging_setup.LogDetail.DETAILED),
        (["--log-level=errors"], {}, logging_setup.LogDetail.ERRORS),
        ([], {"TEACHERS_TEAMMATE_LOG_LEVEL": "normal"}, logging_setup.LogDetail.NORMAL),
        (["--diagnose"], {}, logging_setup.LogDetail.DETAILED),
        (["--log-level", "normal", "--diagnose"], {}, logging_setup.LogDetail.NORMAL),
        (["--log-level", "many"], {}, None),
        ([], {}, None),
    ],
)
def test_the_detail_a_start_was_asked_for(
    argv: list[str], environ: dict[str, str], detail: logging_setup.LogDetail | None
) -> None:
    """
    Given  a command line and an environment
    When   the detail a start was asked for is read
    Then   it is the flag's, else the environment's, else what --diagnose means, else nothing
    """
    assert logging_setup.detail_from(argv, environ) is detail


@pytest.mark.parametrize(
    ("argv", "wanted"), [(["-v"], True), (["--verbose"], True), (["-i", "pages"], False)]
)
def test_the_console_gets_the_lines_only_when_the_command_line_asks(
    argv: list[str], wanted: bool
) -> None:
    """
    Given  a command line
    When   it is asked whether the console wants the log lines too
    Then   only -v or --verbose says yes
    """
    assert logging_setup.console_wanted(argv) is wanted


def test_a_path_under_the_home_folder_is_written_as_a_tilde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a log file that is meant to be sent to whoever helps
    When   a line names a file in the teacher's home folder
    Then   the home folder is written as ~, so the log names no teacher
    """
    monkeypatch.setattr(logging_setup.Path, "home", staticmethod(lambda: tmp_path / "home"))
    path = logging_setup.start_logging(tmp_path / "data")
    assert path is not None

    logging.getLogger("teachers_teammate.run").warning(
        "cannot write %s", tmp_path / "home" / "Documents" / "anna.docx"
    )
    logging.shutdown()

    written = path.read_text(encoding="utf-8")
    assert "cannot write ~/Documents/anna.docx" in written
    assert str(tmp_path / "home") not in written
