"""Shared plumbing for widgets that show a live list of AI models.

The Text Recognition picker and the Proofreading / Content Review sections all ask a
service which models it offers, show a busy bar and a status line meanwhile, and must not
leave anything running when their window closes.  That machinery lives here once: the
fetch (on the shared background pool), the status-line renderers and the small combo
helpers.

The widgets themselves (``gui._ocr_config_selector``, ``gui.settings``) contain
presentation only; every query is delegated to the injected service.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable
import html

from PySide6.QtCore import QObject, QThread
from PySide6.QtWidgets import QComboBox, QProgressBar, QWidget

from ..domain.model_listing import ModelGroup, ModelListing
from ._background import LatestTask
from ._model_wording import models_from_text

# ── busy bar ──────────────────────────────────────────────────────────────────


class _BusyIndicator(QProgressBar):
    """Small indeterminate bar shown next to a model combo while its list is being fetched."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setRange(0, 0)
        self.setFixedWidth(64)
        self.setTextVisible(False)
        self.setVisible(False)


# ── the service the widgets query ─────────────────────────────────────────────


# ── background fetches ────────────────────────────────────────────────────────


class ModelFetcher:
    """Fetches one service's model list on the background pool.

    The callable returns the names, or a :class:`ModelListing` that also says whether they
    are live.  It is handed a ``report(text)`` callback for saying what it is doing, since a
    slow fetch (one HTTP request per model) otherwise looks like a hung window.  *tag* (the
    service the fetch is for) comes back with the answer and with each progress text, so a
    late answer for a service the user has already left can be told apart and dropped.

    Only the newest fetch counts: starting another one, or :meth:`cancel` (the widget closed),
    drops everything the earlier ones still report.
    """

    def __init__(self, owner: QObject | None = None) -> None:
        self._task = LatestTask(owner)

    def start(
        self,
        fetch_fn: Callable[[Callable[[str], None]], list[str] | ModelListing],
        on_listing: Callable[[str, ModelListing], None],
        on_progress: Callable[[str, str], None] | None = None,
        *,
        tag: str = "",
    ) -> None:
        """Ask *fetch_fn* off the GUI thread; *on_listing* gets ``(tag, listing)``."""

        def fetch(emit: Callable[[object], None]) -> list[str] | ModelListing:
            return fetch_fn(emit)

        def report(payload: object) -> None:
            if on_progress is not None:
                on_progress(tag, str(payload))

        def deliver(result: object, error: BaseException | None) -> None:
            if error is not None:
                listing = ModelListing([], "failed", str(error))
            elif isinstance(result, ModelListing):
                listing = result
            elif isinstance(result, list):
                listing = ModelListing([str(model) for model in result])
            else:
                listing = ModelListing([])
            on_listing(tag, listing)

        self._task.submit_streaming(fetch, report, deliver)

    def cancel(self) -> None:
        """Stop showing answers: whatever a running fetch still finds is dropped."""
        self._task.cancel()


# ── status label helpers ──────────────────────────────────────────────────────


_STATUS_LOADING = '<span style="color:#888;">● loading…</span>'
_STATUS_KEY_MISSING = '<span style="color:#e67e22;">● key not set</span>'
_STATUS_ERROR = '<span style="color:#c0392b;">● -</span>'


def _status_ok(n: int, service: str = "") -> str:
    """Return the finished status line: a green "✓ N models" (from *service* when given)."""
    what = models_from_text(n, service) if service else f"{n} model{'' if n == 1 else 's'}"
    return f'<span style="color:#27ae60;">✓ {html.escape(what)}</span>'


def _status_busy(text: str) -> str:
    """Return the grey line shown while the model list is still on its way."""
    return f'<span style="color:#888;">● {html.escape(text)}</span>'


def _status_note(text: str) -> str:
    """Return an orange note to append to a status line."""
    return f'<span style="color:#e67e22;">{html.escape(text)}</span>'


#: Status-link prefix that carries the model to take instead of the chosen one.
_USE_PREFIX = "use:"

#: The groups of a curated model list, in the order they are shown.
_MODEL_GROUPS: tuple[ModelGroup, ...] = ("current", "pinned", "own", "other")


def _listing_status(listing: ModelListing, service: str = "") -> str:
    """Return the status line for *listing*: live, built-in by design, or built-in after a failure.

    A failed query must not look like a successful one, so it says why and offers to retry.
    """
    count = len(listing.models)
    noun = "model" if count == 1 else "models"
    if listing.source == "failed":
        what = f"built-in list of {count} {noun}" if count else "no models to show"
        return (
            f'<span style="color:#e67e22;">● {what} — '
            f"{html.escape(listing.problem or 'the service could not be asked')}</span> "
            '<a href="retry">Try again</a>'
        )
    if listing.source == "builtin":
        return f'<span style="color:#888;">● {count} {noun} · built-in list</span>'
    return _status_ok(count, service)


# ── combo helpers ─────────────────────────────────────────────────────────────


def _set_combo(combo: QComboBox, value: str) -> None:
    idx = combo.findText(value)
    if idx >= 0:
        combo.setCurrentIndex(idx)


# ── retired threads ───────────────────────────────────────────────────────────


#: Long jobs that are running and may outlive the widget that started them.  A running
#: ``QThread`` must not be destroyed (that aborts the whole process) and these run a plain
#: function with no event loop, so they cannot be interrupted either: ``_start_thread``
#: parks every thread here from its start, ``_retire_thread`` adds a thread whose widget
#: lets go of it, and ``_purge_retired_threads`` drops the finished ones.
_RETIRED_THREADS: set[QThread] = set()
_RETIRED_WAIT_MS = 3000


def _purge_retired_threads() -> None:
    """Drop retired threads that have finished."""
    for thread in [t for t in _RETIRED_THREADS if t.isFinished()]:
        _RETIRED_THREADS.discard(thread)


def _start_thread(thread: QThread) -> None:
    """Start *thread* and keep it alive until it ends, whatever happens to its widget.

    A widget that is closed, deleted or garbage-collected without calling its ``shutdown()``
    would otherwise drop the last reference to a thread that is still running, and Qt
    aborts the process ("QThread: Destroyed while thread is still running").
    """
    _purge_retired_threads()
    _RETIRED_THREADS.add(thread)
    thread.start()


def _retire_thread(thread: QThread | None) -> None:
    """Detach *thread* from its dialog without blocking the GUI thread.

    Its results are disconnected — a stale answer can no longer touch a widget —
    and a thread that is still running is kept alive until it ends on its own.
    """
    _purge_retired_threads()
    if thread is None or not thread.isRunning():
        return
    for name in ("log_line", "progress", "finished_ok", "finished_err", "finished"):
        signal = getattr(thread, name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except RuntimeError, TypeError:
            pass  # nothing was connected
    _RETIRED_THREADS.add(thread)


def _wait_for_retired_threads() -> None:
    """Give retired threads a moment to finish before the interpreter shuts down."""
    for thread in list(_RETIRED_THREADS):
        thread.wait(_RETIRED_WAIT_MS)


atexit.register(_wait_for_retired_threads)
