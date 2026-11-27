"""The one place the window shows a failure, and the line it writes about it.

A failure reaches the teacher in one of two ways: the row of the file it happened to (the
results table says why, the end-of-run line counts it), or one notice about the action that
failed.  Every notice of a failure goes through here, so that it is written down as well: a
notice the teacher closes is gone, while the log file is what support has afterwards.

Who handled the failure logs it (``messages.log_failure``); a failure that a catch point
already wrote — a background task, the per-file net of a run — is not written twice
(``messages.log_once``).

An error nobody handled is a bug, and its kind and frames are in the file already.  The teacher
gets a generic notice with the folder of the log and nothing of the error's own words, since
those can quote the document that choked it.  One such notice at a time, and the same kind of
bug not again within :data:`REPEAT_WINDOW_S`: a bug in a slot that fires with every key press
must not bury the teacher in boxes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from ..application.composition import log_folder
from ..messages import log_once
from ..texts import dialogs as dlg
from ..texts._types import Notice
from . import _dialogs

_logger = logging.getLogger(__name__)

#: How long the same kind of bug says nothing after it was shown once (seconds).
REPEAT_WINDOW_S = 10.0


@dataclass
class _State:
    """What the notices of one process remember between them."""

    #: When each kind of bug was last shown.
    last_shown: dict[type[BaseException], float] = field(default_factory=dict)
    #: Whether a notice of a bug is up right now.
    showing: bool = False
    #: The bridge to the GUI thread, made by :func:`install_notices`.
    bridge: _UnexpectedBridge | None = None


_state = _State()


def show_notice(parent: QWidget | None, notice: Notice) -> None:
    """Tell the teacher *notice*, which reports no failure."""
    _dialogs.notify(parent, notice)


def show_failure(
    parent: QWidget | None,
    notice: Notice,
    error: BaseException | None = None,
) -> None:
    """Tell the teacher *notice* about something that failed, and write *error* down.

    *error* is the failure behind the notice, when the call site still has it; a failure a
    catch point already logged is not written a second time.  A notice worded from a value the
    call site already read (a reason, a list of documents) passes ``None``: it holds no error
    of its own.
    """
    if error is not None:
        log_once(_logger, notice.title, error)
    _dialogs.notify(parent, notice)


def show_unexpected(parent: QWidget | None, error: BaseException) -> None:
    """Say that the app ran into a bug, offering the folder of the log.

    The failure is in the log file already.  The notice names neither the type nor the words of
    the error; a repeat of the same kind within :data:`REPEAT_WINDOW_S` shows nothing, and so
    does anything while a notice of this kind is up.
    """
    if _state.showing or not _worth_showing(type(error)):
        return
    _state.showing = True
    try:
        answer = _dialogs.choose(parent, dlg.unexpected_error())
    finally:
        _state.showing = False
    if answer.choice is dlg.OPEN_LOG_FOLDER_CHOICE:
        open_log_folder()


def _worth_showing(kind: type[BaseException]) -> bool:
    """Return whether a bug of *kind* is shown, and remember that it was."""
    now = time.monotonic()
    last = _state.last_shown.get(kind)
    if last is not None and now - last < REPEAT_WINDOW_S:
        return False
    _state.last_shown[kind] = now
    return True


def open_log_folder() -> None:
    """Show the folder the app writes its log into."""
    folder = log_folder()
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass  # nothing to show: the data folder cannot be written at all
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class _UnexpectedBridge(QObject):
    """Carries a bug from the thread that raised it to the GUI thread.

    A hook fires on whatever thread the exception ended on, and a notice belongs to the GUI
    thread: the object lives there, so the connection is queued.
    """

    raised = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.raised.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def _show(self, error: object) -> None:
        if isinstance(error, BaseException):
            show_unexpected(None, error)


def install_notices() -> None:
    """Let a bug that reached no handler be shown in a window (once, on the GUI thread)."""
    if _state.bridge is None:
        _state.bridge = _UnexpectedBridge()


def show_unexpected_anywhere(error: BaseException) -> None:
    """Hand *error* to the window from whichever thread it ended on.

    This is what ``start_logging`` calls as ``on_unexpected``: the log line is written before
    the call, so a process without a window (a CLI run, a start that failed early) loses only
    the notice.
    """
    if _state.bridge is None:
        _logger.debug("An unexpected error arrived before a window could show it")
        return
    _state.bridge.raised.emit(error)
