"""Where an API key is kept: the system's secret store, or the app's settings file."""

from __future__ import annotations

from enum import StrEnum


class KeyLocation(StrEnum):
    """Where new keys are kept."""

    KEYCHAIN = "keychain"
    SETTINGS_FILE = "settings_file"
