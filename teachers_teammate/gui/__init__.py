"""Teacher's Teammate — GUI sub-package.

Entry point::

    python -m teachers_teammate.gui

Public API re-exported for convenience:

- :class:`MainWindow`
- :func:`main_gui`
"""

from ..texts import app as app_text

try:
    from ._main_window import MainWindow, main_gui
except ImportError as _exc:
    raise ImportError(app_text.MISSING_PYSIDE) from _exc

__all__ = ["MainWindow", "main_gui"]
