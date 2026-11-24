"""The GUI's own settings store: window geometry, the last page of a window, remembered answers.

Pipeline settings live in ``ocr.toml`` (see :mod:`teachers_teammate.config`); this is the little
store Qt keeps for the windows.  One function, so the store a window writes to is decided in one
place: the location comes from ``gui_store_location()``, and a run of the app walkthrough or a test
sets ``TEACHERS_TEAMMATE_TMPDIR`` and its GUI state lands in that folder as an INI file instead of
the developer's own settings.  The credential store keeps its plain-text fallback in the same
store.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from ..config import gui_store_location


def app_settings() -> QSettings:
    """Return the GUI's settings: an INI file in the override folder, else the native store."""
    location = gui_store_location()
    if location.ini_file is not None:
        return QSettings(str(location.ini_file), QSettings.Format.IniFormat)
    return QSettings(location.organization, location.application)
