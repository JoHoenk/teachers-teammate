"""The Settings window: one window with a page per topic (AI models, Scan quality, …).

See :mod:`._window` for how pages are built and wired, and :mod:`._page` for the protocol
every page follows.
"""

from ._page import (
    PAGE_TITLES,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
    SettingsResult,
)
from ._window import SettingsWindow, last_page

__all__ = [
    "PAGE_TITLES",
    "SettingsContext",
    "SettingsPage",
    "SettingsPageBase",
    "SettingsResult",
    "SettingsWindow",
    "last_page",
]
