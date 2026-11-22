"""The GUI's own settings store: window geometry, the last page of a window, remembered answers.

Pipeline settings live in ``ocr.toml`` (see :mod:`teachers_teammate.config`); this is the little
store Qt keeps for the windows.  One function, so the store a window writes to is decided in one
place: a run of the app walkthrough or a test sets ``TEACHERS_TEAMMATE_TMPDIR`` and its GUI state
lands in that folder as an INI file instead of the developer's own settings.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

from ..config import override_root

#: The organisation and application the native store files the settings under.
ORGANIZATION = "TeachersTeammate"
APPLICATION = "TeachersTeammate"

#: The file name under the override folder.
FILE_NAME = "gui-settings.ini"


def app_settings() -> QSettings:
    """Return the GUI's settings: an INI file in the override folder, else the native store.

    An INI file there, because the native store writes where the platform says (the registry on
    Windows), which no folder of the walkthrough could hold.
    """
    root = override_root()
    if root is not None:
        return QSettings(str(root / FILE_NAME), QSettings.Format.IniFormat)
    return QSettings(ORGANIZATION, APPLICATION)
