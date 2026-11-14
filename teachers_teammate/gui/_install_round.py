"""One round of installs: pip packages, then spaCy models, the model files, then Ollama pulls.

The Add-ons page and the Setup Assistant both install through this object, so the order, the
log lines and the rule about closing the window are the same in both places.

pip and spaCy run here and must finish before their window closes (a new package has to be
importable before anything uses it); the pulls are handed to the main window's
``OllamaDownloadManager`` and go on after the window has closed.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
import threading
from typing import Any, Protocol

from PySide6.QtCore import QObject, QThread, Signal

from ._model_guidance import SPACY_PREFIX

#: How an item is named in :attr:`InstallRound.item_started` and ``item_done``.
PACKAGE_PREFIX = "package:"
#: The built-in reader's model files (``reader:ppocr``); also the Add-ons "preselect" entry.
READER_PREFIX = "reader:"
READER_ITEM = f"{READER_PREFIX}ppocr"
#: The status line the round sends with the reader's download progress.
READER_PROGRESS_STATUS = "Built-in reader"
#: The page models of the automatic image preparation (``page_models:all``); also the Add-ons
#: "preselect" entry.
PAGE_MODELS_PREFIX = "page_models:"
PAGE_MODELS_ITEM = f"{PAGE_MODELS_PREFIX}all"
PAGE_MODELS_PROGRESS_STATUS = "Page models"


class _InstallService(Protocol):
    """What the round asks the application service; the facade methods are the only ones."""

    def install_packages(self, packages: list[str], progress_cb: Callable[[str], None]) -> None: ...

    def addon_packages_dir(self) -> Path: ...

    def inject_addon_packages_dir(self) -> None: ...

    def on_ollama_models_changed(self, ollama_url: str) -> None: ...

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None: ...


class _PullManager(Protocol):
    """What the round asks of the main window's download manager."""

    progress: Any
    finished: Any
    error: Any
    is_active: bool
    queued: list[str]
    current_model: str

    def start(self, url: str, model: str, queue: Sequence[str] = ()) -> None: ...

    def abort(self) -> None: ...


class _PipInstallThread(QThread):
    """Installs pip packages in a background thread."""

    log_line = Signal(str)
    finished_ok = Signal(list)  # installed packages
    finished_err = Signal(str)

    def __init__(self, app_service: _InstallService, packages: list[str]) -> None:
        super().__init__()
        self._svc = app_service
        self._packages = packages

    def run(self) -> None:
        try:
            self._svc.install_packages(self._packages, self.log_line.emit)
            self.finished_ok.emit(self._packages)
        except Exception as exc:  # noqa: BLE001  # surface any pip/subprocess failure to the UI via the error signal
            self.finished_err.emit(str(exc))


class InstallRound(QObject):
    """One round of installs: pip packages, spaCy models, the reader and page models, Ollama pulls.

    pip and spaCy run here and must finish before their window closes (a new package has to be
    importable before anything uses it); the pulls are handed to the main window's
    OllamaDownloadManager and go on after the window has closed.
    """

    log_line = Signal(str)
    item_started = Signal(str)  # "package:<pip>", "spacy:<model>", "reader:ppocr", ..., "<tag>"
    item_done = Signal(str)
    pull_progress = Signal(str, int, int)  # status, bytes done, bytes total
    finished = Signal(bool)  # True when anything was installed or pulled
    failed = Signal(str, str)  # item, message

    def __init__(
        self,
        app_service: _InstallService,
        pull_manager: _PullManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._svc = app_service
        self._pull_manager = pull_manager  # owned by MainWindow; outlives this round
        self._pull_connected = False  # subscribed to the pull manager's signals
        self._thread: QThread | None = None
        # "pip", "spacy", "reader", "page_models" or "pull" — what the running thread does
        self._phase = ""
        self._reader = False
        self._page_models = False
        self._packages: list[str] = []
        self._spacy_models: list[str] = []
        self._ollama_models: list[str] = []
        self._ollama_url = ""
        self._current = ""  # the item that is running now
        self._installed_any = False
        self._running = False

    # ── what the caller drives ───────────────────────────────────────────────────

    def start(
        self,
        *,
        packages: Sequence[str] = (),
        spacy_models: Sequence[str] = (),
        reader: bool = False,
        page_models: bool = False,
        ollama_models: Sequence[str] = (),
        ollama_url: str = "",
    ) -> bool:
        """Begin the round; return False when there is nothing to do or one is running."""
        if self._running:
            return False
        self._packages = list(packages)
        self._spacy_models = list(spacy_models)
        self._reader = reader
        self._page_models = page_models
        self._ollama_models = list(ollama_models)
        self._ollama_url = ollama_url
        if not (
            self._packages
            or self._spacy_models
            or self._reader
            or self._page_models
            or self._ollama_models
        ):
            return False
        self._running = True
        self._installed_any = False
        self._current = ""
        if self._packages:
            self._start_pip()
        elif self._spacy_models:
            self._start_spacy()
        else:
            self._after_spacy()
        return True

    def busy(self) -> bool:
        """True while pip, spaCy or a model download runs: their window must not close yet."""
        return (
            self._phase in ("pip", "spacy", "reader", "page_models")
            and self._thread is not None
            and self._thread.isRunning()
        )

    def running(self) -> bool:
        """True from :meth:`start` until the round has finished or failed."""
        return self._running

    def abort(self) -> None:
        """Stop the downloads; a pip install or spaCy download cannot be stopped once it runs."""
        from ._addon_installer_dialog import (  # noqa: PLC0415
            _OllamaPullThread,
            _ReaderDownloadThread,
        )

        if isinstance(self._thread, _ReaderDownloadThread):
            self._thread.cancel()
            return
        if self._pull_manager is not None:
            self._pull_manager.abort()
        elif isinstance(self._thread, _OllamaPullThread):
            self._thread.request_abort()

    def detach(self) -> None:
        """Stop following the pull manager; a running pull goes on in the main window."""
        self._disconnect_pull_manager()

    def follow_running_pull(self) -> str:
        """Follow a pull the main window already runs; return its model, or ""."""
        if self._pull_manager is None or not self._pull_manager.is_active:
            return ""
        if self._pull_connected:
            return ""
        self._connect_pull_manager()
        self._current = self._pull_manager.current_model
        return self._current

    # ── pip ──────────────────────────────────────────────────────────────────────

    def _start_pip(self) -> None:
        self.log_line.emit(f"pip install {' '.join(self._packages)}")
        # pip reports one error for the whole command, so a failure names the first package.
        self._current = f"{PACKAGE_PREFIX}{self._packages[0]}"
        for package in self._packages:
            self.item_started.emit(f"{PACKAGE_PREFIX}{package}")
        thread = _PipInstallThread(self._svc, self._packages)
        thread.log_line.connect(self.log_line)
        thread.finished_ok.connect(self._on_pip_done)
        thread.finished_err.connect(self._on_error)
        # Set before start(): a thread that finishes at once must not be overwritten.
        self._thread = thread
        self._phase = "pip"
        thread.start()

    def _on_pip_done(self, packages: list[str]) -> None:
        self.log_line.emit("✓ pip install complete.")
        for package in packages:
            self.item_done.emit(f"{PACKAGE_PREFIX}{package}")
        self._installed_any = True
        self._svc.inject_addon_packages_dir()
        if self._spacy_models:
            self._start_spacy()
        else:
            self._after_spacy()

    # ── spaCy ────────────────────────────────────────────────────────────────────

    def _start_spacy(self) -> None:
        from ._addon_installer_dialog import _ModelDownloadThread  # noqa: PLC0415

        model = self._spacy_models[0]
        self.log_line.emit(f"Downloading spaCy model: {model}")
        self._begin(f"{SPACY_PREFIX}{model}")
        thread = _ModelDownloadThread(model, self._svc.addon_packages_dir())
        thread.log_line.connect(self.log_line)
        thread.finished_ok.connect(self._on_spacy_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        self._phase = "spacy"
        thread.start()

    def _on_spacy_done(self, _model: str) -> None:
        self.log_line.emit("✓ spaCy model downloaded.")
        self.item_done.emit(self._current)
        self._installed_any = True
        self._svc.inject_addon_packages_dir()
        self._spacy_models = self._spacy_models[1:]
        if self._spacy_models:
            self._start_spacy()
        else:
            self._after_spacy()

    def _after_spacy(self) -> None:
        """Go on with the reader, the page models, the pulls, or end the round."""
        if self._reader:
            self._start_reader()
        elif self._page_models:
            self._start_page_models()
        elif self._ollama_models:
            self._start_pulls()
        else:
            self._finish()

    # ── the built-in reader ──────────────────────────────────────────────────────

    def _start_reader(self) -> None:
        from ._addon_installer_dialog import _ReaderDownloadThread  # noqa: PLC0415

        self.log_line.emit("Downloading the built-in reader…")
        self._begin(READER_ITEM)
        thread = _ReaderDownloadThread(self._svc)
        thread.progress.connect(self.pull_progress_from_reader)
        thread.finished_ok.connect(self._on_reader_done)
        thread.finished_err.connect(self._on_reader_error)
        self._thread = thread
        self._phase = "reader"
        thread.start()

    def pull_progress_from_reader(self, done: int, total: int) -> None:
        """Forward the reader's download progress as a pull progress line."""
        self.pull_progress.emit(READER_PROGRESS_STATUS, done, total)

    def _on_reader_done(self) -> None:
        self.log_line.emit("✓ Built-in reader downloaded.")
        self.item_done.emit(READER_ITEM)
        self._installed_any = True
        self._reader = False
        # The finished thread must not stay the one abort() cancels: the Ollama pulls that
        # follow are stopped through the pull manager.
        self._thread = None
        self._after_spacy()

    def _on_reader_error(self, message: str) -> None:
        self._on_error(message)

    # ── the page models ──────────────────────────────────────────────────────────

    def _start_page_models(self) -> None:
        from ._addon_installer_dialog import _ReaderDownloadThread  # noqa: PLC0415

        self.log_line.emit("Downloading the page models…")
        self._begin(PAGE_MODELS_ITEM)
        thread = _ReaderDownloadThread(self._svc, "download_page_models")
        thread.progress.connect(
            lambda done, total: self.pull_progress.emit(PAGE_MODELS_PROGRESS_STATUS, done, total)
        )
        thread.finished_ok.connect(self._on_page_models_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        self._phase = "page_models"
        thread.start()

    def _on_page_models_done(self) -> None:
        self.log_line.emit("✓ Page models downloaded.")
        self.item_done.emit(PAGE_MODELS_ITEM)
        self._installed_any = True
        self._page_models = False
        self._thread = None
        self._after_spacy()

    # ── Ollama pulls ─────────────────────────────────────────────────────────────

    def _start_pulls(self) -> None:
        model = self._ollama_models[0]
        queued_behind = self._pull_manager is not None and self._pull_manager.is_active
        self.log_line.emit(
            f"Queued behind the running download: {', '.join(self._ollama_models)}"
            if queued_behind
            else f"Pulling Ollama model: {model}"
        )
        self._begin(model)
        self._phase = "pull"
        if self._pull_manager is not None:
            # Delegate to the main-window-owned manager: it pulls the whole queue, also
            # after this round's window was closed.
            self._connect_pull_manager()
            self._pull_manager.start(self._ollama_url, model, self._ollama_models[1:])
            return
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        thread = _OllamaPullThread(self._ollama_url, model)
        # The thread reports bytes as floats; the round's own signal carries whole bytes, so
        # the pages that show them need no second handler.
        thread.progress.connect(
            lambda status, done, total: self.pull_progress.emit(status, int(done), int(total))
        )
        thread.finished_ok.connect(self._on_pull_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def _on_pull_done(self, _model: str) -> None:
        self._pull_arrived(self._current)
        self._ollama_models = self._ollama_models[1:]
        if self._ollama_models:
            self._start_pulls()
        else:
            self._finish()

    def _on_manager_pull_done(self, model: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._pull_arrived(model)
        queued = self._pull_manager.queued
        if queued:
            # The manager goes on with the next model by itself.
            self.log_line.emit(f"Pulling Ollama model: {queued[0]}")
            self._begin(queued[0])
            return
        self._disconnect_pull_manager()
        self._finish()

    def _on_manager_pull_error(self, message: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._disconnect_pull_manager()
        self._on_error(message)

    def _pull_arrived(self, model: str) -> None:
        self.log_line.emit("✓ Ollama model pulled.")
        self.item_done.emit(model)
        self._installed_any = True
        # The installed-model list and the classifications are stale from now on.
        self._svc.on_ollama_models_changed(self._ollama_url)

    # ── the end of the round ─────────────────────────────────────────────────────

    def _begin(self, item: str) -> None:
        self._current = item
        self.item_started.emit(item)

    def _on_error(self, message: str) -> None:
        self._disconnect_pull_manager()
        self.failed.emit(self._current, message)
        self._finish()

    def _finish(self) -> None:
        self._running = False
        self._current = ""
        self.finished.emit(self._installed_any)

    # ── pull manager subscription ────────────────────────────────────────────────

    def _connect_pull_manager(self) -> None:
        if self._pull_manager is None or self._pull_connected:
            return
        self._pull_manager.progress.connect(self.pull_progress)
        self._pull_manager.finished.connect(self._on_manager_pull_done)
        self._pull_manager.error.connect(self._on_manager_pull_error)
        self._pull_connected = True

    def _disconnect_pull_manager(self) -> None:
        if self._pull_manager is None or not self._pull_connected:
            return
        for signal, slot in (
            (self._pull_manager.progress, self.pull_progress),
            (self._pull_manager.finished, self._on_manager_pull_done),
            (self._pull_manager.error, self._on_manager_pull_error),
        ):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass  # already disconnected
        self._pull_connected = False
