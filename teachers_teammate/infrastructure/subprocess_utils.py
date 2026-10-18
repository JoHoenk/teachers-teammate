"""Small helpers shared by every module that starts a child process."""

from __future__ import annotations

import sys
from typing import Any

_CREATE_NO_WINDOW = 0x08000000


def no_console_window_kwargs() -> dict[str, Any]:
    """Return ``subprocess`` keyword arguments that stop a console window flashing up.

    Windowed Windows builds show a console for every child process otherwise.  Empty
    on other platforms (``creationflags`` exists only on Windows).
    """
    if sys.platform == "win32":
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}
