"""Where a diagnostic goes: one rotating file in the data folder, and stderr when there is one.

The installed window has no console (``console=False``), so without a file a warning or the
traceback of an unexpected error reaches nobody.  :func:`start_logging` installs the one place
such a line goes, once per process: a rotating file ``<data root>/logs/teachers-teammate.log``
and, when the process has a console, the same line on stderr.

It also installs the nets of the process: an exception that reaches no handler — in a thread, in
a Qt slot, in a destructor — writes a line and is handed to the entry point; a console still
gets the interpreter's own report of it.  A native crash (a segfault in a reader or in Qt)
leaves its stack in ``logs/crash.log``, below the header line each start writes.

How much the file holds is a :class:`~teachers_teammate.settings_schema.LogDetail`: what a run
did and every warning of the app by default, only what went wrong when asked for, or everything
the libraries say as well.  A flag or the environment decides it for a start that has to be read
afterwards, and the teacher's setting decides otherwise.  The file starts with a line naming
this session, and a path under the home folder is written as ``~``, so a log can be sent on.

A line holds file names, stage names and exception text — never a document's text, a prompt or
an access key (``tests/unit/test_log_lines_carry_no_text.py`` guards that).  A sentence a
teacher reads is not logged either: that is the ``Reporter``'s, from ``texts/``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import faulthandler
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import platform
import sys
import threading
from types import TracebackType
from typing import IO

from .. import __version__
from ..messages import log_failure
from ..settings_schema import LogDetail
from .storage_root import resolve_data_root

#: A line in the file: when it happened, how bad it was and where it came from.
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
#: A line on stderr, where a person reads it in a terminal right after the command.
STDERR_FORMAT = "%(levelname)s: %(message)s"
#: The folder of the data root the logs go into.
FOLDER = "logs"
LOG_NAME = "teachers-teammate.log"
#: The file a native crash leaves its stack in.
CRASH_NAME = "crash.log"
#: How large the file may grow before it is rotated, and how many old files are kept.
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
#: What a detail lets through: the app's own lines, and other libraries' lines.
_LEVELS: dict[LogDetail, tuple[int, int]] = {
    LogDetail.ERRORS: (logging.WARNING, logging.WARNING),
    LogDetail.NORMAL: (logging.INFO, logging.WARNING),
    LogDetail.DETAILED: (logging.DEBUG, logging.INFO),
}
#: The package of the app: the one whose own lines a detail says separately from the rest.
PACKAGE = "teachers_teammate"
#: The switch and the environment variable that decide the detail instead of the setting.
FLAG = "--log-level"
ENVIRONMENT_VARIABLE = "TEACHERS_TEAMMATE_LOG_LEVEL"
#: The switch that sends the lines to a console this process has, besides the file.
CONSOLE_FLAGS = ("-v", "--verbose")
#: What a bug's line says it was, in the words of the log rather than of the window.
WHAT_UNEXPECTED = "An unexpected error reached no handler"
#: What the line of an exception Python could not raise anywhere says: a destructor's, say.
WHAT_IGNORED = "An exception Python could not raise was ignored"

_logger = logging.getLogger(__name__)
#: The logger of the nets.  The interpreter's own hook writes the same error to a console in full,
#: so stderr leaves these lines to the file.
_nets_logger = logging.getLogger(f"{__name__}.nets")


@dataclass
class _Installed:
    """What :func:`start_logging` did: the log file, or ``None``, and the handlers it added."""

    path: Path | None = None
    handlers: tuple[logging.Handler, ...] = ()
    started: bool = False
    #: The detail the file is written with now.
    detail: LogDetail = LogDetail.NORMAL
    #: The detail the command line or the environment asked for, which a setting cannot change.
    override: LogDetail | None = None
    #: What the entry point shows a bug with, or ``None`` when it shows nothing yet.
    on_unexpected: Callable[[BaseException], None] | None = None
    #: The open ``crash.log``: ``faulthandler`` writes to the handle, so it stays open.
    crash_file: IO[str] | None = None


#: The state of this process; the first :func:`start_logging` fills it and nothing else does.
_installed = _Installed()


def log_folder(data_root: Path | None = None) -> Path:
    """Return the folder this computer's logs are written to."""
    root = resolve_data_root() if data_root is None else data_root
    return root / FOLDER


def start_logging(
    data_root: Path | None = None,
    *,
    on_unexpected: Callable[[BaseException], None] | None = None,
    log_detail: LogDetail | None = None,
    console: bool = True,
    argv: Sequence[str] | None = None,
) -> Path | None:
    """Send the app's lines to the log file, and to stderr when there is one, once per process.

    Returns the log file, or ``None`` when it cannot be opened — a read-only data folder, for
    example: then stderr only, and the app starts all the same.  A second call adds no handler
    and returns the same path.

    *on_unexpected* is what the entry point shows a bug with; the log line is written with or
    without it, and a later call may still hand it in.  *log_detail* is the detail to write with;
    without one, a flag or the environment decides (:func:`detail_from`) and the setting decides
    later.  *console* says whether this process has a console that wants the lines too, and
    *argv* is the command line the flag is read from.
    """
    if _installed.started:
        if on_unexpected is not None:
            _installed.on_unexpected = on_unexpected
        return _installed.path
    root_data = resolve_data_root() if data_root is None else data_root
    asked = detail_from(argv)
    handlers: list[logging.Handler] = []
    if console and sys.stderr is not None:
        console_handler = _formatted(logging.StreamHandler(sys.stderr), STDERR_FORMAT)
        console_handler.addFilter(_leave_nets_to_file)
        handlers.append(console_handler)
    folder = log_folder(root_data)
    path: Path | None = folder / LOG_NAME
    try:
        folder.mkdir(parents=True, exist_ok=True)
        handlers.append(
            _formatted(
                RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
                ),
                FILE_FORMAT,
                home=True,
            )
        )
    except OSError:
        path = None
    root = logging.getLogger()
    for handler in handlers:
        root.addHandler(handler)
    _installed.path = path
    _installed.handlers = tuple(handlers)
    _installed.on_unexpected = on_unexpected
    _installed.override = asked
    _installed.started = True
    detail = set_log_detail(log_detail or asked or LogDetail.NORMAL)
    _write_session_header(detail, root_data)
    _install_process_hooks()
    _enable_crash_log(folder)
    return path


def set_log_detail(detail: LogDetail, *, from_setting: bool = False) -> LogDetail:
    """Make the log hold *detail*; return the detail that is in effect now.

    The window hands its setting in with *from_setting*: a detail the command line or the
    environment asked for wins over it, so ``--log-level`` holds for the whole session.
    """
    if from_setting and _installed.override is not None:
        return _installed.detail
    ours, others = _LEVELS[detail]
    logging.getLogger().setLevel(others)
    logging.getLogger(PACKAGE).setLevel(ours)
    _installed.detail = detail
    return detail


def detail_from(
    argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None
) -> LogDetail | None:
    """Return the detail a start was asked for, or ``None`` when nothing asked.

    The order is the flag, then the environment, then ``--diagnose`` (which means everything).
    ``None`` leaves it to the teacher's setting.
    """
    args = sys.argv if argv is None else argv
    flagged = _flag_detail(args)
    if flagged is not None:
        return flagged
    environment = os.environ if environ is None else environ
    typed = _detail_or_none(environment.get(ENVIRONMENT_VARIABLE, ""))
    if typed is not None:
        return typed
    from . import diagnostics  # noqa: PLC0415  # it reads the log folder from this module

    return LogDetail.DETAILED if diagnostics.requested(args, environ) else None


def _flag_detail(argv: Sequence[str]) -> LogDetail | None:
    """Return the detail ``--log-level`` names, or ``None`` when the flag is not there."""
    for index, argument in enumerate(argv):
        if argument == FLAG and index + 1 < len(argv):
            found = _detail_or_none(argv[index + 1])
        elif argument.startswith(f"{FLAG}="):
            found = _detail_or_none(argument[len(FLAG) + 1 :])
        else:
            continue
        if found is not None:
            return found
    return None


def _detail_or_none(text: str) -> LogDetail | None:
    """Return *text* as a detail, or ``None`` when it names none."""
    for member in LogDetail:
        if member.value == text.strip().lower():
            return member
    return None  # not a level of the app: the next source decides


def console_wanted(argv: Sequence[str] | None = None) -> bool:
    """Return whether the console gets the lines too: the command line says ``-v``."""
    args = sys.argv if argv is None else argv
    return any(flag in args for flag in CONSOLE_FLAGS)


def _formatted(handler: logging.Handler, template: str, *, home: bool = False) -> logging.Handler:
    """Return *handler* writing lines in *template*, the home folder as ``~`` when *home*."""
    handler.setFormatter(_HomeShort(template) if home else logging.Formatter(template))
    return handler


class _HomeShort(logging.Formatter):
    """Write a path under the teacher's home folder as ``~``: one place, no call site remembers."""

    def __init__(self, template: str) -> None:
        super().__init__(template)
        try:
            self._home = str(Path.home())
        except RuntimeError:  # a process without a home folder: nothing to shorten
            self._home = ""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        return line.replace(self._home, "~") if self._home else line


def _write_session_header(detail: LogDetail, data_root: Path) -> None:
    """Write what a later reader needs to place the lines below: version, system, roots."""
    _logger.info(
        "Session: version %s, %s %s, frozen %s, data root %s, log detail %s",
        __version__,
        platform.system(),
        platform.release(),
        "yes" if getattr(sys, "frozen", False) else "no",
        data_root,
        detail.value,
    )


def _leave_nets_to_file(record: logging.LogRecord) -> bool:
    """Keep the lines of the nets off stderr: the interpreter's hook prints the error there."""
    return record.name != _nets_logger.name


def _install_process_hooks() -> None:
    """Send every exception that reached no handler through :func:`_report`."""
    sys.excepthook = _excepthook
    threading.excepthook = _threading_excepthook
    sys.unraisablehook = _unraisablehook


def _excepthook(
    kind: type[BaseException], error: BaseException, traceback_of: TracebackType | None
) -> None:
    """Log the exception that ended the main thread or a Qt slot: ``sys.excepthook``.

    Ctrl+C is the teacher's own doing, not a bug: it only gets the interpreter's report.
    """
    if not issubclass(kind, KeyboardInterrupt):
        _report(WHAT_UNEXPECTED, error, traceback_of, show=True)
    sys.__excepthook__(kind, error, traceback_of)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """Log the exception that ended a thread: ``threading.excepthook``.

    A ``SystemExit`` in a thread ends it as the default hook lets it end: silently.
    """
    if isinstance(args.exc_value, SystemExit):
        return
    name = args.thread.name if args.thread is not None else "unknown"
    _report(f"{WHAT_UNEXPECTED} in thread {name}", args.exc_value, args.exc_traceback, show=True)
    threading.__excepthook__(args)


def _unraisablehook(args: sys.UnraisableHookArgs) -> None:
    """Log the exception Python could not raise anywhere: ``sys.unraisablehook``.

    A destructor that fails does no harm to what the teacher is doing, so only the file hears.
    """
    owner = args.object
    where = getattr(owner, "__qualname__", type(owner).__name__) if owner is not None else None
    what = WHAT_IGNORED if where is None else f"{WHAT_IGNORED} in {where}"
    _report(what, args.exc_value, args.exc_traceback, show=False)
    sys.__unraisablehook__(args)


def _report(
    what: str, error: BaseException | None, traceback_of: TracebackType | None, *, show: bool
) -> None:
    """Write the bug to the log file; when *show*, let the entry point say it, on any thread."""
    if error is None:
        return
    if error.__traceback__ is None:
        error = error.with_traceback(traceback_of)
    log_failure(_nets_logger, what, error)
    on_unexpected = _installed.on_unexpected
    if not show or on_unexpected is None:
        return
    try:
        on_unexpected(error)
    except Exception as exc:  # noqa: BLE001  # a hook says nothing to the stderr a window lacks
        log_failure(_logger, "The unexpected error could not be shown", exc)


def _enable_crash_log(folder: Path) -> None:
    """Write the stack of a native crash to ``crash.log``; ``faulthandler`` keeps it open.

    Every start writes a header line, so a stack belongs to the session above it.  The file starts
    over once it is larger than the log file may grow; nothing rotates it.
    """
    path = folder / CRASH_NAME
    crash_file: IO[str] | None = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        over = path.exists() and path.stat().st_size > MAX_BYTES
        crash_file = path.open("w" if over else "a", encoding="utf-8")
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        crash_file.write(f"--- {started} version {__version__} pid {os.getpid()}\n")
        crash_file.flush()
    except OSError as exc:
        # A read-only data folder: no crash log, and the app starts all the same.
        if crash_file is not None:
            crash_file.close()
        log_failure(_logger, "The crash log could not be opened", exc)
        return
    faulthandler.enable(file=crash_file, all_threads=True)
    _installed.crash_file = crash_file
