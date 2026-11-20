"""Restoring a saved window geometry without a small first frame.

Qt shows a window at its saved normal size and maximizes it afterwards, so the first frame is
small and the window jumps.  Setting the screen's rectangle first skips that frame.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRect, QSize, Qt
from PySide6.QtWidgets import QApplication, QWidget

#: How much of the screen's available area a window may take when it opens.
SCREEN_SHARE = 0.9


def restore_geometry(window: QWidget, raw: object) -> bool:
    """Restore *raw* into *window*; a maximized window gets its final size at once.

    Returns whether a saved geometry was applied.  Without one the caller sizes the window
    itself.
    """
    if not isinstance(raw, QByteArray) or not window.restoreGeometry(raw):
        return False
    if window.isMaximized():
        _fill_screen(window)
    return True


def maximize_without_saved_geometry(window: QWidget) -> None:
    """Make *window* open maximized: the first start has no saved geometry to restore."""
    # The size set before stays as the window's normal size, for when it is restored.
    window.setWindowState(window.windowState() | Qt.WindowState.WindowMaximized)


def _fill_screen(window: QWidget) -> None:
    """Give a maximized *window* the screen's rectangle first, so its first frame is full size."""
    screen = window.screen() or QApplication.primaryScreen()
    if screen is None:
        return
    state = window.windowState()
    window.setWindowState(state & ~Qt.WindowState.WindowMaximized)
    window.setGeometry(screen.availableGeometry())
    window.setWindowState(state)


def fit_to_screen(
    window: QWidget,
    preferred: QSize | None = None,
    *,
    available: QRect | None = None,
    resize: bool = True,
) -> None:
    """Make *window* fit the screen: clamp its start size and lower minimums that are too big.

    The start size is *preferred* (the window's own size when not given), at most
    :data:`SCREEN_SHARE` of the screen's available area in each direction.  A minimum size the
    window or its layout asks for that is larger than that share is lowered to it, so a small
    screen never gets a window it cannot show whole.  A page that then has less room scrolls; it
    is not squeezed, as long as its content sits in a scroll area.

    *available* is the area to fit into; it defaults to the available area of the window's
    screen.  With *resize* off only the minimum is lowered: a window that was restored maximized
    keeps the screen's whole area.
    """
    if available is None:
        screen = window.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
    max_width = int(available.width() * SCREEN_SHARE)
    max_height = int(available.height() * SCREEN_SHARE)
    # The layout's own minimum counts too: Qt takes it for the window unless one was set.
    hint = window.minimumSizeHint()
    window.setMinimumSize(
        min(max(window.minimumWidth(), hint.width()), max_width),
        min(max(window.minimumHeight(), hint.height()), max_height),
    )
    if not resize:
        return
    size = preferred if preferred is not None else window.size()
    window.resize(min(size.width(), max_width), min(size.height(), max_height))
