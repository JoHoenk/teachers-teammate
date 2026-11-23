"""Where a diagnostic goes: one rotating file in the data folder, and stderr when there is one.

The installed window has no console (``console=False``), so without a file a warning or the
traceback of an unexpected error reaches nobody.  :func:`start_logging` installs the one place
such a line goes, once per process: a rotating file ``<data root>/logs/teachers-teammate.log``
and, when the process has a console, the same line on stderr.

A line holds file names, stage names and exception text — never a document's text, a prompt or
an access key (``tests/unit/test_log_lines_carry_no_text.py`` guards that).  A sentence a
teacher reads is not logged either: that is the ``Reporter``'s, from ``texts/``.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from .storage_root import resolve_data_root

#: A line in the file: when it happened, how bad it was and where it came from.
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
#: A line on stderr, where a person reads it in a terminal right after the command.
STDERR_FORMAT = "%(levelname)s: %(message)s"
#: The folder of the data root the logs go into.
FOLDER = "logs"
LOG_NAME = "teachers-teammate.log"
#: How large the file may grow before it is rotated, and how many old files are kept.
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
#: What a teacher's log holds: a warning and everything above it, not a developer's details.
LEVEL = logging.WARNING


@dataclass
class _Installed:
    """What :func:`start_logging` did: the log file, or ``None``, and the handlers it added."""

    path: Path | None = None
    handlers: tuple[logging.Handler, ...] = ()
    started: bool = False


#: The state of this process; the first :func:`start_logging` fills it and nothing else does.
_installed = _Installed()


def log_folder(data_root: Path | None = None) -> Path:
    """Return the folder this computer's logs are written to."""
    root = resolve_data_root() if data_root is None else data_root
    return root / FOLDER


def log_file(data_root: Path | None = None) -> Path:
    """Return the file a warning or an error of the app is written to."""
    return log_folder(data_root) / LOG_NAME


def start_logging(data_root: Path | None = None) -> Path | None:
    """Send warnings and errors to the log file and to stderr, once per process.

    Returns the log file, or ``None`` when it cannot be opened — a read-only data folder, for
    example: then stderr only, and the app starts all the same.  A second call adds no handler
    and returns the same path.
    """
    if _installed.started:
        return _installed.path
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(_formatted(logging.StreamHandler(sys.stderr), STDERR_FORMAT))
    path = log_file(data_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            _formatted(
                RotatingFileHandler(
                    path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
                ),
                FILE_FORMAT,
            )
        )
    except OSError:
        path = None
    root = logging.getLogger()
    root.setLevel(LEVEL)
    for handler in handlers:
        root.addHandler(handler)
    _installed.path = path
    _installed.handlers = tuple(handlers)
    _installed.started = True
    return path


def _formatted(handler: logging.Handler, template: str) -> logging.Handler:
    """Return *handler* writing lines in *template*."""
    handler.setFormatter(logging.Formatter(template))
    return handler
