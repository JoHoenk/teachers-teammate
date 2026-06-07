"""Shared QApplication bootstrap for the GUI apps.

Both the main window (:func:`teachers_teammate.gui._main_window.main_gui`) and the
benchmark window (:func:`teachers_teammate.gui.benchmark.main_benchmark`) need the
same QApplication setup: application name/icon, the Linux desktop-file hint, and
the qdarkstyle theme.  Centralising it keeps the two entry points consistent.
"""

from __future__ import annotations

from collections.abc import Callable
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
import qdarkstyle


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
    app = QApplication.instance() or QApplication(sys.argv)
    assert isinstance(app, QApplication)
    app.setApplicationName("teachers-teammate")
    app.setApplicationDisplayName("Teacher's Teammate")
    if sys.platform.startswith("linux") and hasattr(app, "setDesktopFileName"):
        app.setDesktopFileName("teachers-teammate")
    if icon_factory is not None:
        icon = icon_factory()
        if icon is not None:
            app.setWindowIcon(icon)
    if apply_theme:
        app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api="pyside6"))
    return app
