"""The one way a long job runs in the GUI.

A pip install, a model download and an Ollama pull are subprocesses or streams: they report
while they run, a window must not close before they ended, and one can be stopped only
between two chunks.  That whole shape lives here once; a subclass implements :meth:`work`
only.

A one-shot query or fetch belongs on the shared pool instead (``gui/_background.py``).
"""

from __future__ import annotations

import atexit
import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

from ..exceptions import ModelDownloadCancelledError
from ..messages import log_failure

_logger = logging.getLogger(__name__)

#: What ``finished_err`` says about a job the teacher stopped.
ABORTED = "aborted"

#: How long the application waits for the running jobs when it closes.
_WAIT_MS = 3000

#: The jobs that are running.  A running ``QThread`` must not be destroyed — Qt aborts the
#: whole process — and a window may be closed while its job runs, so a job is held here from
#: its start until it has ended, whatever happened to the window that started it.
_LIVE: set[LongJobThread] = set()


class LongJobThread(QThread):
    """A job that runs off the GUI thread and reports while it runs.

    A subclass implements :meth:`work` and watches :attr:`stop_event` where it waits, so
    :meth:`stop` ends it at the next chunk; :meth:`work` then raises
    :class:`~teachers_teammate.exceptions.ModelDownloadCancelledError` to say it ended
    unfinished.  A job that finished anyway reports success.  Whatever :meth:`work` returns
    is :attr:`result` and travels with ``finished_ok``.
    """

    #: One line for the window's log.
    log_line = Signal(str)
    #: The job's own progress payload: ``(done, total)`` bytes for a download,
    #: ``(status, done, total)`` for a pull.
    progress = Signal(object)
    #: The job ended; carries :attr:`result`.
    finished_ok = Signal(object)
    #: The job failed, or was stopped (:data:`ABORTED`).
    finished_err = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        #: What :meth:`work` returned; it is set when ``finished_ok`` arrives.
        self.result: Any = None
        #: Set by :meth:`stop`, watched by :meth:`work`.
        self.stop_event = threading.Event()
        # The hold on the job is dropped on the GUI thread: dropping it from run() could be
        # the last reference to an object whose thread is still running.
        self.finished.connect(self._release, Qt.ConnectionType.QueuedConnection)

    # ── what a subclass provides ─────────────────────────────────────────────────

    def work(self) -> Any:
        """Do the job; return what ``finished_ok`` should carry.

        Raise ``ModelDownloadCancelledError`` when :attr:`stop_event` ended it unfinished.
        """
        raise NotImplementedError

    def error_text(self, exc: BaseException) -> str:
        """Return the sentence a window shows for *exc*."""
        return str(exc)

    # ── what a window drives ─────────────────────────────────────────────────────

    def run(self) -> None:
        try:
            self.result = self.work()
        except ModelDownloadCancelledError:
            self.finished_err.emit(ABORTED)
        except Exception as exc:  # noqa: BLE001  # any failure reaches the window as a sentence
            log_failure(_logger, "A long job ended with an error", exc)
            self.finished_err.emit(self.error_text(exc))
        else:
            self.finished_ok.emit(self.result)

    def start(self) -> None:
        """Start the job, which is then held until it has ended."""
        for job in [job for job in _LIVE if job.isFinished()]:
            _LIVE.discard(job)
        _LIVE.add(self)
        super().start()

    def stop(self) -> None:
        """Ask the job to end at the next chunk; nothing half done is kept."""
        self.stop_event.set()

    @property
    def stopped(self) -> bool:
        """True once :meth:`stop` was called."""
        return self.stop_event.is_set()

    @Slot()
    def _release(self) -> None:
        _LIVE.discard(self)


def wait_for_long_jobs() -> None:
    """Give the running jobs a moment to end before the interpreter shuts down."""
    for job in list(_LIVE):
        job.wait(_WAIT_MS)


atexit.register(wait_for_long_jobs)
