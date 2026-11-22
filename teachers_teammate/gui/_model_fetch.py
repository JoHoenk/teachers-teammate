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

from collections.abc import Callable
import html

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QComboBox, QProgressBar, QWidget

from ..domain.model_listing import ModelGroup, ModelListing
from ..texts.models import models_from_text
from ._background import LatestTask

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
        self._task = LatestTask(owner, network=True)

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
