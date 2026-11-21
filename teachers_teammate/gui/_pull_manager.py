"""One active Ollama model pull, outliving any dialog that started it.

The main window owns one of these; Settings → Add-ons subscribes while it is open and
unsubscribes when the window closes, so pulls continue in the background.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QObject, QThread, Signal

from ..application.composition import AppServices
from ._model_fetch import _start_thread


class PullManager(QObject):
    """Manages a single active Ollama model pull thread, outliving any dialog."""

    progress = Signal(str, float, float)  # (status, completed_bytes, total_bytes)
    started = Signal(str)  # model_name
    finished = Signal(str)  # model_name (success)
    error = Signal(str)  # error message

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        service: AppServices,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._thread: QThread | None = None
        self._current_model: str = ""
        self._current_url: str = ""
        #: Models waiting for the running pull; pulled one after the other, also after the
        #: Settings window that asked for them was closed.
        self._queue: list[str] = []

    @property
    def is_active(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def current_model(self) -> str:
        return self._current_model

    @property
    def current_url(self) -> str:
        """Ollama server of the latest (or running) pull."""
        return self._current_url

    @property
    def queued(self) -> list[str]:
        """Return the models still waiting for the running pull (a copy)."""
        return list(self._queue)

    def start(self, url: str, model_name: str, queue: Sequence[str] = ()) -> None:
        """Pull *model_name* from *url*, then each model of *queue*.

        While a pull is running (also one that outlived the Settings window) the models are
        added to its queue instead, so a request is never dropped silently; a model that is
        already pulling or waiting is not added twice.  A failed or aborted pull drops the
        queue.
        """
        if self.is_active:
            for model in (model_name, *queue):
                if model != self._current_model and model not in self._queue:
                    self._queue.append(model)
            return
        self._queue = list(queue)
        self._begin(url, model_name)

    def _begin(self, url: str, model_name: str) -> None:
        from ._addon_installer_dialog import _OllamaPullThread  # noqa: PLC0415

        self._current_model = model_name
        self._current_url = url
        thread = _OllamaPullThread(self._service, url, model_name)
        thread.progress.connect(self.progress)
        thread.finished_ok.connect(self._on_finished_ok)
        thread.finished_err.connect(self._on_finished_err)
        # Kept alive until it ends: the thread reports from run(), so the reference dropped
        # in _on_finished_ok may be the last one while the thread is still running.
        _start_thread(thread)
        self._thread = thread
        self.started.emit(model_name)

    def abort(self) -> None:
        """Request the active pull to stop after the current chunk (and drop the queue)."""
        self._queue.clear()
        if self._thread is not None:
            self._thread.request_abort()  # ty: ignore[unresolved-attribute]  # _thread holds an abortable _OllamaPullThread; base QThread lacks request_abort

    def _on_finished_ok(self, model_name: str) -> None:
        self._thread = None
        # Subscribers see the queue as it was when this pull ended: a non-empty one means
        # the round goes on.
        self.finished.emit(model_name)
        if self._queue:
            self._begin(self._current_url, self._queue.pop(0))

    def _on_finished_err(self, msg: str) -> None:
        self._thread = None
        self._queue.clear()
        self.error.emit(msg)
