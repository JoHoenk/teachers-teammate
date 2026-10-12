"""Run work off the GUI thread without owning a ``QThread`` per task.

Every ``QThread`` subclass in the GUI has to be kept alive until it finished —
destroying a running one aborts the whole process.  Tasks started through this
module run on a shared :class:`QThreadPool` instead: there is no thread object
to destroy, a result is delivered to the GUI thread through a queued signal, and
a result for an owner that has been closed in the meantime is dropped silently.

* :func:`run_in_background` — fire-and-forget; ``on_done(result, error)`` runs on
  the GUI thread.
* :class:`LatestTask` — "latest wins": starting a new task makes the results of
  all earlier ones stale, so a slow, outdated answer can never overwrite a newer
  one.
* :func:`wait_for_background_tasks` — called when the application closes.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
import functools

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

#: Upper bound of simultaneous background tasks.  The tasks are I/O bound
#: (subprocesses, HTTP), so a small fixed number is plenty.
_MAX_THREADS = 4
#: How long :func:`wait_for_background_tasks` waits by default.
_DEFAULT_WAIT_MS = 3000

# Deliveries stay referenced until their result arrived on the GUI thread; an
# unreferenced QObject would be destroyed together with its pending signal.
_active: set[_Delivery] = set()


@functools.cache
def _get_pool() -> QThreadPool:
    """Return the process-wide pool, created on first use (from the GUI thread)."""
    pool = QThreadPool()
    pool.setMaxThreadCount(_MAX_THREADS)
    return pool


def _owner_alive(owner: QObject | None) -> bool:
    """Return whether *owner* still has its C++ object (``None`` counts as alive)."""
    if owner is None:
        return True
    try:
        owner.objectName()
    except RuntimeError:
        return False
    return True


class _Delivery(QObject):
    """Carries a task result from a pool thread to the GUI thread."""

    arrived = Signal(object, object)  # result, error

    def __init__(
        self,
        on_done: Callable[[object, BaseException | None], None],
        owner: QObject | None,
    ) -> None:
        super().__init__()  # no parent: lives until the result was delivered
        self._on_done = on_done
        self._owner = owner
        # Created on the GUI thread, emitted from a pool thread → queued.
        self.arrived.connect(self._deliver)

    @Slot(object, object)
    def _deliver(self, result: object, error: object) -> None:
        _active.discard(self)
        if not _owner_alive(self._owner):
            return
        self._on_done(result, error if isinstance(error, BaseException) else None)


class _Task(QRunnable):
    """Runs one callable and hands its outcome to a :class:`_Delivery`."""

    def __init__(self, fn: Callable[[], object], delivery: _Delivery) -> None:
        super().__init__()
        self._fn = fn
        self._delivery = delivery

    def run(self) -> None:
        result: object = None
        error: BaseException | None = None
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001  # any failure is reported to on_done, never raised in the pool
            error = exc
        self._delivery.arrived.emit(result, error)


def run_in_background(
    fn: Callable[[], object],
    on_done: Callable[[object, BaseException | None], None],
    *,
    owner: QObject | None = None,
) -> None:
    """Run *fn* in a pool thread and call ``on_done(result, error)`` on the GUI thread.

    Args:
        fn: Zero-argument callable; must not touch widgets.
        on_done: Receives ``(result, None)`` or ``(None, exception)``.
        owner: Optional widget the result belongs to.  When it has been destroyed
            by the time the task finishes, *on_done* is not called.
    """
    delivery = _Delivery(on_done, owner)
    _active.add(delivery)
    _get_pool().start(_Task(fn, delivery))


class LatestTask:
    """Run background tasks where only the most recently started one counts."""

    def __init__(self, owner: QObject | None = None) -> None:
        self._owner = owner
        self._sequence = 0

    def submit(
        self,
        fn: Callable[[], object],
        on_done: Callable[[object, BaseException | None], None],
    ) -> int:
        """Start *fn*; earlier, still running submissions become stale.

        Returns:
            The ticket number of this submission (useful for logging/tests).
        """
        self._sequence += 1
        ticket = self._sequence

        def deliver(result: object, error: BaseException | None) -> None:
            if ticket == self._sequence:
                on_done(result, error)

        run_in_background(fn, deliver, owner=self._owner)
        return ticket

    def cancel(self) -> None:
        """Make every pending result stale (e.g. when the owner closes)."""
        self._sequence += 1


def wait_for_background_tasks(timeout_ms: int = _DEFAULT_WAIT_MS) -> bool:
    """Wait up to *timeout_ms* for running tasks; return whether all finished."""
    return _get_pool().waitForDone(timeout_ms)


atexit.register(wait_for_background_tasks, 2000)
