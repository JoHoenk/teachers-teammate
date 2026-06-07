"""Teacher's Teammate — GUI sub-package.

Entry point::

    python -m teachers_teammate.gui

Public API re-exported for convenience:

- :class:`MainWindow`
- :func:`main_gui`
"""

try:
    from ._main_window import MainWindow, main_gui
except ImportError as _exc:
    raise ImportError(
        "GUI requires extra dependencies. Install with: pip install teachers-teammate[gui]"
    ) from _exc

__all__ = ["MainWindow", "main_gui"]
