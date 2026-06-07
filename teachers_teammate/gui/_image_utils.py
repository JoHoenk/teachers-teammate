"""GUI image loading utilities.

:func:`load_pixmap` and :func:`load_icon` are the single safe way to load an
image file into a ``QPixmap`` / ``QIcon``.  Reading the bytes via Python's own
I/O avoids the Qt file-system layer, which can silently fail on Windows when the
path contains spaces, umlauts, or other non-ASCII characters.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap


def load_pixmap(path: str | Path | None) -> QPixmap:
    """Load *path* into a QPixmap, returning an empty QPixmap on any failure.

    Uses Python file I/O instead of passing the path string to Qt directly so
    that paths containing spaces or non-ASCII characters (e.g. umlauts in a
    Windows user directory) are handled correctly on all platforms.

    Args:
        path: File-system path to the image file, or ``None`` / empty string.

    Returns:
        A populated ``QPixmap`` on success, or an empty ``QPixmap()`` when
        *path* is empty, the file does not exist, or loading fails.
    """
    if not path:
        return QPixmap()
    try:
        data = Path(path).read_bytes()
    except OSError:
        return QPixmap()
    pix = QPixmap()
    pix.loadFromData(data)
    return pix


def load_icon(path: str | Path | None) -> QIcon:
    """Load *path* into a QIcon, returning an empty QIcon on any failure.

    Like :func:`load_pixmap`, this reads the file via Python I/O instead of
    passing the path string to Qt directly, so icon files under paths
    containing spaces or non-ASCII characters (e.g. an app installed below a
    Windows user directory with an umlaut) load correctly on all platforms.

    Args:
        path: File-system path to the icon file, or ``None`` / empty string.

    Returns:
        A populated ``QIcon`` on success, or an empty ``QIcon()`` when *path*
        is empty, the file does not exist, or loading fails.
    """
    pix = load_pixmap(path)
    if pix.isNull():
        return QIcon()
    return QIcon(pix)
