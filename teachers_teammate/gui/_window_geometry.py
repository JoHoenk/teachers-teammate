"""Restoring a saved window geometry without a small first frame.

Qt shows a window at its saved normal size and maximizes it afterwards, so the first frame is
small and the window jumps.  Setting the screen's rectangle first skips that frame.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtWidgets import QApplication, QWidget


def restore_geometry(window: QWidget, raw: object) -> bool:
    """Restore *raw* into *window*; a maximized window gets its final size at once.

    Returns whether a saved geometry was applied.  Without one the caller sizes the window
    itself.
    """
    if not isinstance(raw, QByteArray) or not window.restoreGeometry(raw):
        return False
    if window.isMaximized():
        screen = window.screen() or QApplication.primaryScreen()
        if screen is not None:
            state = window.windowState()
            window.setWindowState(state & ~Qt.WindowState.WindowMaximized)
            window.setGeometry(screen.availableGeometry())
            window.setWindowState(state)
    return True
