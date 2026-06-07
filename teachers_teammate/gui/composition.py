"""GUI composition helpers for assembling main-window dependencies."""

from __future__ import annotations

from collections.abc import Callable

from ..application.service import ProcessingApplicationService
from ._main_window import MainWindow
from ._worker import OCRWorker


def build_main_window(
    *,
    app_service: ProcessingApplicationService | None = None,
    worker_factory: Callable[..., OCRWorker] | None = None,
) -> MainWindow:
    """Create the main window with optional injected application dependencies."""
    return MainWindow(app_service=app_service, worker_factory=worker_factory)
