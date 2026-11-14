"""Shared plumbing for widgets that show a live list of AI models.

The Text Recognition picker and the Proofreading / Content Review sections all ask a
service which models it offers, show a busy bar and a status line meanwhile, and must not
leave a running ``QThread`` behind when their window closes.  That machinery lives here
once: the fetch thread, the "retire an orphaned thread" bookkeeping, the status-line
renderers and the small combo helpers.

The widgets themselves (``gui._ocr_config_selector``, ``gui.settings``) contain
presentation only; every query is delegated to the injected service, described by the
:class:`SettingsAppService` protocol below.
"""

from __future__ import annotations

import atexit
from collections.abc import Callable, Sequence
import html
from pathlib import Path
import threading
from typing import Protocol

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QComboBox, QProgressBar, QWidget

from ..domain.data_location import StageLocation
from ..domain.formatted_text import TextFormat
from ..domain.model_catalog import (
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
)
from ..domain.model_listing import ModelGroup, ModelListing
from ..domain.performance_estimate import TimeRange
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


class _SettingsAppService(Protocol):
    def list_providers(self) -> list[str]: ...

    def list_ocr_engines(self) -> list[str]: ...

    def reader_installed(self) -> bool: ...

    def models_dir(self) -> Path: ...

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...

    def page_models_installed(self) -> bool: ...

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...

    def get_provider_info(self, provider: str) -> dict: ...

    def provider_key_missing(self, provider: str) -> bool: ...

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]: ...

    def list_ocr_models(self, base_url: str = "") -> list[str]: ...

    def list_provider_models_checked(
        self, provider: str, *, base_url: str = ""
    ) -> ModelListing: ...

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None: ...

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None: ...

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None: ...

    def is_module_importable(self, module: str) -> bool: ...

    def missing_provider_package(self, provider: str) -> str | None: ...

    def list_correction_prompt_presets(self) -> dict[str, str]: ...

    def default_correction_prompt(self) -> str: ...

    def default_answer_formats(self) -> tuple[str, str]: ...

    def correction_notation_hint(self) -> str: ...

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str: ...

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection: ...

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection: ...

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]: ...

    def rate_ollama_models(
        self,
        tags: list[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
    ) -> list[ModelRating]: ...

    def detect_gpus(self, *, refresh: bool = False) -> list: ...

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation: ...

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str: ...

    def describe_llm_location(
        self, provider: str, model: str, ollama_url: str
    ) -> StageLocation: ...

    def default_model_for_task(self, provider: str, task: str) -> str: ...

    def speed_page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
    ) -> dict[str, TimeRange]: ...


# ── background threads ────────────────────────────────────────────────────────


class _ModelFetchThread(QThread):
    """Fetches model names in a background thread via a caller-supplied callable.

    The callable returns the names, or a :class:`ModelListing` that also says whether they
    are live.  It is handed a ``report(text)`` callback for saying what it is doing, since a
    slow fetch (one HTTP request per model) otherwise looks like a hung window.  *tag* (the
    provider the fetch is for) comes back with the answer and with each progress text, so a
    late answer for a provider the user has already left can be told apart and dropped.
    """

    models_ready = Signal(list)
    listing_ready = Signal(str, object)  # (tag, ModelListing)
    progress = Signal(str, str)  # (tag, text)
    error = Signal(str)

    def __init__(
        self,
        fetch_fn: Callable[[Callable[[str], None]], list[str] | ModelListing],
        tag: str = "",
    ) -> None:
        super().__init__()
        self._fetch_fn = fetch_fn
        self._tag = tag

    def _report(self, text: str) -> None:
        """Say what the fetch is doing (called from the worker thread)."""
        self.progress.emit(self._tag, text)

    def run(self) -> None:
        try:
            result = self._fetch_fn(self._report)
        except Exception as exc:  # noqa: BLE001  # provider model fetch may raise any network/auth error; report via signals
            self.error.emit(str(exc))
            self.models_ready.emit([])
            self.listing_ready.emit(self._tag, ModelListing([], "failed", str(exc)))
            return
        listing = result if isinstance(result, ModelListing) else ModelListing(list(result))
        self.models_ready.emit(listing.models)
        self.listing_ready.emit(self._tag, listing)


class _GpuDetectThread(QThread):
    """Runs GPU detection (nvidia-smi / rocm-smi) in a background thread."""

    detect_done = Signal(list)  # list[GpuInfo]

    def __init__(self, app_service: _SettingsAppService) -> None:
        super().__init__()
        self._app_service = app_service

    def run(self) -> None:
        # "Detect GPU" is a request to look again (a driver may have been installed
        # since the first probe); the fresh result also feeds "My computer".
        self.detect_done.emit(self._app_service.detect_gpus(refresh=True))


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


#: Threads that outlived their dialog.  A running ``QThread`` must not be destroyed
#: (that aborts the whole process) and these run a plain function with no event
#: loop, so they cannot be interrupted either: they are parked here until they end.
_RETIRED_THREADS: set[QThread] = set()
_THREAD_RESULT_SIGNALS = (
    "models_ready",
    "listing_ready",
    "progress",
    "error",
    "detect_done",
    "found",
    "search_done",
    "finished",
)
_RETIRED_WAIT_MS = 3000


def _purge_retired_threads() -> None:
    """Drop retired threads that have finished."""
    for thread in [t for t in _RETIRED_THREADS if t.isFinished()]:
        _RETIRED_THREADS.discard(thread)


def _retire_thread(thread: QThread | None) -> None:
    """Detach *thread* from its dialog without blocking the GUI thread.

    Its results are disconnected — a stale answer can no longer touch a widget —
    and a thread that is still running is kept alive until it ends on its own.
    """
    _purge_retired_threads()
    if thread is None or not thread.isRunning():
        return
    for name in _THREAD_RESULT_SIGNALS:
        signal = getattr(thread, name, None)
        if signal is None:
            continue
        try:
            signal.disconnect()
        except (RuntimeError, TypeError):
            pass  # nothing was connected
    _RETIRED_THREADS.add(thread)


def _wait_for_retired_threads() -> None:
    """Give retired threads a moment to finish before the interpreter shuts down."""
    for thread in list(_RETIRED_THREADS):
        thread.wait(_RETIRED_WAIT_MS)


atexit.register(_wait_for_retired_threads)
