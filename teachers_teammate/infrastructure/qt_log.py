"""Qt's own messages, in the log file instead of a stderr the installed window lacks.

:func:`install` runs before the first ``QApplication`` exists, so the messages of its start-up
(a platform plugin that does not load, say) are caught as well.  PySide6 is imported on first use:
the command line reaches this module through ``application.composition``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

_logger = logging.getLogger("qt")

#: What each kind of Qt message becomes in the log: the chatter of the libraries is a detail.
_LEVELS = {
    "QtDebugMsg": logging.DEBUG,
    "QtInfoMsg": logging.DEBUG,
    "QtWarningMsg": logging.WARNING,
    "QtCriticalMsg": logging.ERROR,
    "QtFatalMsg": logging.ERROR,
}
#: The category of a message nobody named: it says nothing.
_DEFAULT_CATEGORY = "default"
#: How many different warnings are written: Qt repeats one on every paint or every event.
_MAX_DISTINCT = 200


@dataclass
class _Seen:
    """The warnings written so far, by their text, and whether the cap was reached."""

    texts: set[str] = field(default_factory=set)
    full: bool = False


#: The state of this process.
_seen = _Seen()


def install() -> None:
    """Hand Qt's messages to :func:`log_message`."""
    from PySide6.QtCore import qInstallMessageHandler  # noqa: PLC0415

    qInstallMessageHandler(log_message)


def log_message(kind: Any, context: Any, message: str) -> None:
    """Write a message Qt itself reports into the log, where support reads it.

    A warning is written once per text; an error always, as a fatal one ends the process.
    """
    level = _LEVELS.get(getattr(kind, "name", ""), logging.DEBUG)
    category = getattr(context, "category", None)
    line = message if not category or category == _DEFAULT_CATEGORY else f"{category}: {message}"
    if level == logging.WARNING and not _is_new(line):
        return
    _logger.log(level, "%s", line)


def _is_new(line: str) -> bool:
    """Whether the warning *line* was not written before; the first one past the cap says so."""
    if line in _seen.texts:
        return False
    if len(_seen.texts) >= _MAX_DISTINCT:
        if not _seen.full:
            _seen.full = True
            _logger.warning("Further different Qt warnings are not written")
        return False
    _seen.texts.add(line)
    return True
