"""Shared QApplication bootstrap for the GUI apps.

Both the main window (:func:`teachers_teammate.gui._main_window.main_gui`) and the
benchmark window (:func:`teachers_teammate.gui.benchmark.main_benchmark`) need the
same QApplication setup: application name/icon, the Linux desktop-file hint, and
the qdarkstyle theme.  Centralising it keeps the two entry points consistent.

The opt-in diagnostics are started through :mod:`teachers_teammate.application.composition`.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
import sys
from typing import cast

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
import qdarkstyle

from ..texts import app as app_text


def create_app(
    *,
    icon_factory: Callable[[], QIcon | None] | None = None,
    apply_theme: bool = True,
) -> QApplication:
    """Return the singleton :class:`QApplication`, configured with name/icon/theme.

    Reuses an existing instance when present so multiple windows share one app.
    ``icon_factory`` is invoked *after* the QApplication exists — building a
    :class:`QIcon`/``QPixmap`` before that aborts with "Must construct a
    QGuiApplication before a QPixmap".
    """
    existing = QApplication.instance()
    # ``instance()`` is typed as the base class; the app is a QApplication by construction.
    app = cast("QApplication", existing) if existing is not None else QApplication(sys.argv)
    app.setApplicationName("teachers-teammate")
    app.setApplicationDisplayName(app_text.NAME)
    if sys.platform.startswith("linux") and hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("teachers-teammate")
    if icon_factory is not None:
        icon = icon_factory()
        if icon is not None:
            app.setWindowIcon(icon)
    if sys.platform == "win32":
        # The native Windows style gives every QWizard its Aero look, with a back arrow in the
        # title bar; Fusion has only the modern one.
        app.setStyle("Fusion")
    if apply_theme:
        app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    return app


def finish_process(
    code: int,
    *,
    exit_fn: Callable[[int], object] | None = None,
    hard_exit: Callable[[int], object] | None = None,
    shutdown_logging: Callable[[], object] = logging.shutdown,
) -> None:
    """End the process with *code* after the event loop returned.

    A worker that was still running when its window closed (it could not be stopped within the
    grace period, for example an online service that cannot be interrupted) cannot be destroyed:
    that aborts the process with a crash dialog.  Then the process ends without cleaning up,
    after the logs were flushed.  Otherwise it is the normal ``sys.exit``.
    """
    from ._worker import detached_workers_running  # noqa: PLC0415  # keeps start-up light

    if detached_workers_running():
        shutdown_logging()
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except OSError, ValueError:
                pass  # a stream that cannot be flushed has nowhere left to report it
        (hard_exit or os._exit)(code)
        return
    (exit_fn or sys.exit)(code)
