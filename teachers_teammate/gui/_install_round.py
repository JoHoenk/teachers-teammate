"""One round of installs: pip packages, then spaCy models, the model files, then Ollama pulls.

The Add-ons page and the Setup Assistant both install through this object, so the order, the
log lines and the rule about closing the window are the same in both places.

pip and spaCy run here and must finish before their window closes (a new package has to be
importable before anything uses it); the pulls are handed to the main window's
``PullManager`` and go on after the window has closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, cast

from PySide6.QtCore import QObject, Signal

from ..application.composition import AppServices, inject_addon_packages_dir
from ..domain.install_item import (
    PAGE_MODELS_ITEM,
    READER_ITEM,
    UPDATE_ADDONS_ITEM,
    InstallItem,
    InstallKind,
)
from ..texts import addons as txt
from ._long_job import LongJobThread

#: The phases :meth:`InstallRound.abort` can stop: the downloads.  pip and the spaCy model
#: download run to their end once started.
_ABORTABLE_PHASES = (InstallKind.READER, InstallKind.PAGE_MODELS, InstallKind.OLLAMA)


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


class _PipInstallThread(LongJobThread):
    """Installs pip packages; the result is the packages that were installed."""

    def __init__(self, app_service: AppServices, packages: list[str]) -> None:
        super().__init__()
        self._svc = app_service
        self._packages = packages

    def work(self) -> list[str]:
        self._svc.addons.install_packages(self._packages, self.log_line.emit)
        return self._packages

    def error_text(self, exc: BaseException) -> str:
        return txt.addon_error_text(exc)


class _AddonUpdateThread(LongJobThread):
    """Installs the add-ons again after an app update."""

    def __init__(self, app_service: AppServices) -> None:
        super().__init__()
        self._svc = app_service

    def work(self) -> None:
        self._svc.addons.update_addons(self.log_line.emit)

    def error_text(self, exc: BaseException) -> str:
        return txt.addon_error_text(exc)


class InstallRound(QObject):
    """One round of installs: pip packages, spaCy models, the reader and page models, Ollama pulls.

    pip and spaCy run here and must finish before their window closes (a new package has to be
    importable before anything uses it); the pulls are handed to the main window's
    PullManager and go on after the window has closed.
    """

    log_line = Signal(str)
    item_started = Signal(object)  # InstallItem: the step that just began
    item_done = Signal(object)  # InstallItem: the step that finished
    pull_progress = Signal(str, float, float)  # status, bytes done, bytes total
    finished = Signal(bool)  # True when anything was installed or pulled
    failed = Signal(object, str)  # InstallItem, message

    def __init__(
        self,
        app_service: AppServices,
        pull_manager: _PullManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._svc = app_service
        self._pull_manager = pull_manager  # owned by MainWindow; outlives this round
        self._pull_connected = False  # subscribed to the pull manager's signals
        # The thread itself is held until it ends (``LongJobThread``): this reference is
        # dropped — set to ``None`` or replaced by the next step's thread — from the slot of
        # its own "done" signal, while it is still in its last lines.
        self._thread: LongJobThread | None = None
        # What the running thread does, and so what Abort could stop.
        self._phase: InstallKind | None = None
        self._reader = False
        self._page_models = False
        self._update_addons = False
        self._packages: list[str] = []
        self._spacy_models: list[str] = []
        self._ollama_models: list[str] = []
        self._ollama_url = ""
        self._current: InstallItem | None = None  # the item that is running now
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
        update_addons: bool = False,
        ollama_models: Sequence[str] = (),
        ollama_url: str = "",
    ) -> bool:
        """Begin the round; return False when there is nothing to do or one is running.

        ``update_addons`` installs the add-ons in the packages folder again after an app update;
        it runs before everything else in the round.
        """
        if self._running:
            return False
        self._packages = list(packages)
        self._spacy_models = list(spacy_models)
        self._reader = reader
        self._page_models = page_models
        self._update_addons = update_addons
        self._ollama_models = list(ollama_models)
        self._ollama_url = ollama_url
        if not (
            self._update_addons
            or self._packages
            or self._spacy_models
            or self._reader
            or self._page_models
            or self._ollama_models
        ):
            return False
        self._running = True
        self._installed_any = False
        self._current = None
        if self._update_addons:
            self._start_update()
        else:
            self._after_update()
        return True

    def _after_update(self) -> None:
        """Go on with the pip packages, or whatever follows them."""
        if self._packages:
            self._start_pip()
        elif self._spacy_models:
            self._start_spacy()
        else:
            self._after_spacy()

    def busy(self) -> bool:
        """True while pip, spaCy or a model download runs: their window must not close yet."""
        return (
            self._phase
            in (
                InstallKind.UPDATE,
                InstallKind.PACKAGE,
                InstallKind.SPACY,
                InstallKind.READER,
                InstallKind.PAGE_MODELS,
            )
            and self._thread is not None
            and self._thread.isRunning()
        )

    def running(self) -> bool:
        """True from :meth:`start` until the round has finished or failed."""
        return self._running

    def can_abort(self) -> bool:
        """True while Abort would stop something: a download, or a pull this page follows.

        pip and the spaCy model download run to their end once started; the page shows Abort
        only where it does something.
        """
        if self._running:
            return self._phase in _ABORTABLE_PHASES
        # A pull the main window started before this page was opened: the page follows it, and
        # Abort stops it through the manager.
        if self._pull_manager is None:
            return False
        return self._pull_connected and self._pull_manager.is_active

    def abort(self) -> None:
        """Stop the downloads; a pip install or spaCy download cannot be stopped once it runs."""
        if not self.can_abort():
            return
        if (
            self._phase in (InstallKind.READER, InstallKind.PAGE_MODELS)
            and self._thread is not None
        ):
            self._thread.stop()
        elif self._pull_manager is not None:
            self._pull_manager.abort()
        elif self._thread is not None:
            self._thread.stop()

    def detach(self) -> None:
        """Stop following the pull manager; a running pull goes on in the main window."""
        self._disconnect_pull_manager()

    def follow_running_pull(self) -> InstallItem | None:
        """Follow a pull the main window already runs; return its item, or ``None``."""
        if self._pull_manager is None or not self._pull_manager.is_active:
            return None
        if self._pull_connected:
            return None
        self._connect_pull_manager()
        self._current = InstallItem(InstallKind.OLLAMA, self._pull_manager.current_model)
        return self._current

    # ── the add-ons after an app update ──────────────────────────────────────────

    def _start_update(self) -> None:
        self.log_line.emit(txt.LOG_UPDATING)
        # The phase is set before the item is announced: the page reads it to decide whether
        # Abort can do anything.
        self._phase = InstallKind.UPDATE
        self._begin(UPDATE_ADDONS_ITEM)
        thread = _AddonUpdateThread(self._svc)
        thread.log_line.connect(self.log_line)
        thread.finished_ok.connect(self._on_update_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def _on_update_done(self) -> None:
        self.log_line.emit(txt.LOG_UPDATED)
        self.item_done.emit(UPDATE_ADDONS_ITEM)
        self._installed_any = True
        self._update_addons = False
        self._thread = None
        self._after_update()

    # ── pip ──────────────────────────────────────────────────────────────────────

    def _start_pip(self) -> None:
        self.log_line.emit(txt.log_pip_install(self._packages))
        # pip reports one error for the whole command, so a failure names the first package.
        self._current = InstallItem(InstallKind.PACKAGE, self._packages[0])
        self._phase = InstallKind.PACKAGE
        for package in self._packages:
            self.item_started.emit(InstallItem(InstallKind.PACKAGE, package))
        thread = _PipInstallThread(self._svc, self._packages)
        thread.log_line.connect(self.log_line)
        thread.finished_ok.connect(self._on_pip_done)
        thread.finished_err.connect(self._on_error)
        # Set before start(): a thread that finishes at once must not be overwritten.
        self._thread = thread
        thread.start()

    def _on_pip_done(self, packages: list[str]) -> None:
        self.log_line.emit(txt.LOG_PIP_DONE)
        for package in packages:
            self.item_done.emit(InstallItem(InstallKind.PACKAGE, package))
        self._installed_any = True
        inject_addon_packages_dir()
        if self._spacy_models:
            self._start_spacy()
        else:
            self._after_spacy()

    # ── spaCy ────────────────────────────────────────────────────────────────────

    def _start_spacy(self) -> None:
        from ._addon_installer_dialog import _ModelDownloadThread  # noqa: PLC0415

        model = self._spacy_models[0]
        self.log_line.emit(txt.log_spacy_start(model))
        self._phase = InstallKind.SPACY
        self._begin(InstallItem(InstallKind.SPACY, model))
        thread = _ModelDownloadThread(self._svc, model, self._svc.addons.addon_packages_dir())
        thread.log_line.connect(self.log_line)
        thread.finished_ok.connect(self._on_spacy_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def _on_spacy_done(self) -> None:
        self.log_line.emit(txt.LOG_SPACY_DONE)
        self.item_done.emit(self._current)
        self._installed_any = True
        inject_addon_packages_dir()
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

        self.log_line.emit(txt.LOG_READER_START)
        self._phase = InstallKind.READER
        self._begin(READER_ITEM)
        thread = _ReaderDownloadThread(self._svc)
        thread.progress.connect(self.pull_progress_from_reader)
        thread.finished_ok.connect(self._on_reader_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def pull_progress_from_reader(self, payload: object) -> None:
        """Forward the reader's download progress as a pull progress line."""
        done, total = cast("tuple[int, int]", payload)
        self.pull_progress.emit(txt.READER_PROGRESS_STATUS, done, total)

    def _on_reader_done(self) -> None:
        self.log_line.emit(txt.LOG_READER_DONE)
        self.item_done.emit(READER_ITEM)
        self._installed_any = True
        self._reader = False
        # The finished thread must not stay the one abort() cancels: the Ollama pulls that
        # follow are stopped through the pull manager.
        self._thread = None
        self._after_spacy()

    # ── the page models ──────────────────────────────────────────────────────────

    def _start_page_models(self) -> None:
        from ._addon_installer_dialog import _ReaderDownloadThread  # noqa: PLC0415

        self.log_line.emit(txt.LOG_PAGE_MODELS_START)
        self._phase = InstallKind.PAGE_MODELS
        self._begin(PAGE_MODELS_ITEM)
        thread = _ReaderDownloadThread(self._svc, "download_page_models")
        thread.progress.connect(self.page_models_progress)
        thread.finished_ok.connect(self._on_page_models_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def page_models_progress(self, payload: object) -> None:
        """Forward the page models' download progress as a pull progress line."""
        done, total = cast("tuple[int, int]", payload)
        self.pull_progress.emit(txt.PAGE_MODELS_PROGRESS_STATUS, done, total)

    def _on_page_models_done(self) -> None:
        self.log_line.emit(txt.LOG_PAGE_MODELS_DONE)
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
        self._phase = InstallKind.OLLAMA
        self._begin(InstallItem(InstallKind.OLLAMA, model))
        if self._pull_manager is not None:
            # Delegate to the main-window-owned manager: it pulls the whole queue, also
            # after this round's window was closed.
            self._connect_pull_manager()
            self._pull_manager.start(self._ollama_url, model, self._ollama_models[1:])
            return
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        thread = _OllamaPullThread(self._svc, self._ollama_url, model)
        thread.progress.connect(self.pull_stream_progress)
        thread.finished_ok.connect(self._on_pull_done)
        thread.finished_err.connect(self._on_error)
        self._thread = thread
        thread.start()

    def pull_stream_progress(self, payload: object) -> None:
        """Re-send one progress line of a pull this round runs itself."""
        status, done, total = cast("tuple[str, float, float]", payload)
        self.pull_progress.emit(status, done, total)

    def _on_pull_done(self, _model: str) -> None:
        self._pull_arrived(InstallItem(InstallKind.OLLAMA, _model))
        self._ollama_models = self._ollama_models[1:]
        if self._ollama_models:
            self._start_pulls()
        else:
            self._finish()

    def _on_manager_pull_done(self, model: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._pull_arrived(InstallItem(InstallKind.OLLAMA, model))
        queued = self._pull_manager.queued
        if queued:
            # The manager goes on with the next model by itself.
            self.log_line.emit(txt.log_ollama_start(queued[0]))
            self._begin(InstallItem(InstallKind.OLLAMA, queued[0]))
            return
        self._disconnect_pull_manager()
        self._finish()

    def _on_manager_pull_error(self, message: str) -> None:
        # Only connected while a manager pull is active, so _pull_manager is set.
        if self._pull_manager is None:
            return
        self._disconnect_pull_manager()
        self._on_error(message)

    def _pull_arrived(self, item: InstallItem) -> None:
        self.log_line.emit(txt.LOG_OLLAMA_DONE)
        self.item_done.emit(item)
        self._installed_any = True
        # The installed-model list and the classifications are stale from now on.
        self._svc.recommendations.on_ollama_models_changed(self._ollama_url)

    # ── the end of the round ─────────────────────────────────────────────────────

    def _begin(self, item: InstallItem) -> None:
        self._current = item
        self.item_started.emit(item)

    def _on_error(self, message: str) -> None:
        self._disconnect_pull_manager()
        self.failed.emit(self._current, message)
        self._finish()

    def _finish(self) -> None:
        self._running = False
        self._current = None
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
            except RuntimeError, TypeError:
                pass  # already disconnected
        self._pull_connected = False
