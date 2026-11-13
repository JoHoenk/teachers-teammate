"""The protocol every page of the Settings window follows, and the names of the pages.

A page is a plain widget that knows how to show a set of settings (:meth:`load`) and how to
hand back the keys it owns (:meth:`values`).  It never talks to another page: when one page's
change matters to another (the computer profile, the local AI server address, a finished
install), the page emits a signal and :class:`~._window.SettingsWindow` passes it on.

Pages are built the first time they are shown, so a page that is never opened starts no
background work and changes nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QWidget

from ...config import DEFAULTS
from .. import _user_text as txt


class SettingsPage(StrEnum):
    """The pages of the Settings window, in sidebar order (the value is the QSettings key)."""

    GENERAL = "general"
    AI_MODELS = "ai_models"
    AI_SETUP = "ai_setup"
    ONLINE_SERVICES = "online_services"
    SCAN_QUALITY = "scan_quality"
    PRIVACY = "privacy"
    ADDONS = "addons"


# ── Focus values: deep links into a page ─────────────────────────────────────

FOCUS_OCR = "ocr"
FOCUS_PROOFREADING = "proofreading"
FOCUS_REVIEW = "review"
#: The key row under the service that proofreads / reviews (the cursor goes into the field).
FOCUS_PROOFREADING_KEY = "proofreading_key"
FOCUS_REVIEW_KEY = "review_key"
FOCUS_API_KEYS = "api_keys"
#: The local AI server address field on *This computer* (the cursor goes into it).
FOCUS_SERVER = "server"
#: Same page, and the "Find the AI server" dialog opens at once.
FOCUS_FIND_SERVER = "find_server"
#: The speed test on *This computer*.
FOCUS_SPEED = "speed"

FOCUS_SENDING = "sending"
FOCUS_DETECTION = "detection"
FOCUS_PATTERNS = "patterns"

FOCUS_LOCAL_MODELS = "local_models"
FOCUS_OCR_ENGINES = "ocr_engines"
FOCUS_ONLINE_SERVICES = "online_services"
FOCUS_NAME_DETECTION = "name_detection"
FOCUS_GPU = "gpu"


@dataclass(frozen=True)
class SettingsContext:
    """Everything the main window hands to the Settings window when it opens.

    ``settings`` is a copy of the panel's settings dict; the window never writes into it.
    """

    settings: Mapping[str, Any]
    app_service: Any
    language: str = ""
    anonymizer_config: object | None = None
    acknowledgements: Any = None
    display_anonymized: bool = True
    privacy_available: bool = True
    pull_manager: Any = None
    #: The API keys as edited in the open window, shared by every page that shows a key
    #: (``ApiKeyDrafts``).  A page built without a window makes its own.
    key_drafts: Any = None


@dataclass(frozen=True)
class SettingsResult:
    """What the Settings window hands back.

    ``settings`` holds only the keys of the pages that were opened; ``anonymizer_config`` and
    ``display_anonymized`` are ``None`` when the Privacy page was never opened.
    ``restore_all`` asks the main window to reset every setting (the window already asked).
    ``turn_on_anonymization`` asks it to tick "Remove personal info": the teacher chose an
    online service while names would be sent as written, and the AI models page said it
    would be turned on.
    """

    settings: dict[str, Any]
    anonymizer_config: object | None = None
    display_anonymized: bool | None = None
    restore_all: bool = False
    turn_on_anonymization: bool = False


#: Opens another page from inside a page: ``(page, focus, preselect)``.
Navigate = Callable[[SettingsPage, str, Sequence[str]], None]


class SettingsPageBase(QWidget):
    """Common protocol of the Settings pages; see the module docstring."""

    page: ClassVar[SettingsPage]
    title: ClassVar[str]
    description: ClassVar[str]
    #: True for Add-ons: what happens there is not undone by Cancel.
    applies_immediately: ClassVar[bool] = False

    #: Asks the window to show another page: ``(SettingsPage, focus, preselect list)``.
    navigate_requested = Signal(object, str, list)
    #: Whether the page's settings can be saved now (OK is disabled while one cannot).
    validity_changed = Signal(bool)

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context

    # ── settings ──────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        """Show *values* (only the keys this page owns are read)."""
        _ = values

    def values(self) -> dict[str, Any]:
        """Return the keys this page owns, as currently shown."""
        return {}

    def owned_keys(self) -> frozenset[str]:
        """Return the settings keys this page owns (used by "Restore defaults → This page")."""
        return frozenset()

    def restore_defaults(self) -> None:
        """Show the built-in defaults of this page's keys; applied with OK like any change."""
        self.load({key: DEFAULTS[key] for key in self.owned_keys() if key in DEFAULTS})

    def apply(self) -> None:
        """Save what the page keeps outside the settings dict (API keys, remembered answers).

        Called by the window's OK, before it closes.
        """

    def is_valid(self) -> bool:
        """Return whether the page's settings can be saved now."""
        return True

    # ── navigation and life cycle ─────────────────────────────────────────────

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        """Bring the part named *focus* into view (and preselect entries where it applies)."""
        _ = focus, preselect

    def on_shown(self) -> None:
        """The page became visible: refresh what may have changed meanwhile."""

    def on_packages_changed(self) -> None:
        """An add-on, AI model or language model was installed: look again."""

    def set_hardware_setting(self, setting: str) -> None:
        """Follow the computer profile chosen on *This computer* (read-only elsewhere)."""
        _ = setting

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        _ = url

    def close_blocker(self) -> str:
        """Return why the window cannot close right now (``""`` = it can)."""
        return ""

    def shutdown(self) -> None:
        """Stop or detach background work before the page is destroyed."""

    # ── helpers for subclasses ────────────────────────────────────────────────

    def _navigate(self, page: SettingsPage, focus: str = "", preselect: Sequence[str] = ()) -> None:
        """Ask the window to show *page* (focused on *focus*)."""
        self.navigate_requested.emit(page, focus, list(preselect))

    def _scroll_to(self, widget: QWidget) -> None:
        """Scroll the window's scroll area around this page so *widget* is in view.

        Deferred: right after a page was built its layout has no geometry yet.
        """

        def scroll() -> None:
            parent = self.parentWidget()
            while parent is not None:
                ensure = getattr(parent, "ensureWidgetVisible", None)
                if callable(ensure):
                    ensure(widget, 0, 0)
                    return
                parent = parent.parentWidget()

        QTimer.singleShot(0, self, scroll)

    def _open_addons_callback(self, focus: str = FOCUS_LOCAL_MODELS):
        """Return an ``OpenDownloads`` callback that switches to Add-ons inside the window.

        There is no modal round trip any more: the callback returns ``None`` straight away,
        and the page follows a profile change through the window's shared-state wiring.
        """

        def open_addons(preselect: list[str], _parent: QWidget | None) -> None:
            self._navigate(SettingsPage.ADDONS, focus, preselect)

        return open_addons


#: Sidebar titles, also used by the menu entries.
PAGE_TITLES: dict[SettingsPage, str] = {
    SettingsPage.GENERAL: txt.PAGE_GENERAL,
    SettingsPage.AI_MODELS: txt.PAGE_AI_MODELS,
    SettingsPage.AI_SETUP: txt.PAGE_AI_SETUP,
    SettingsPage.ONLINE_SERVICES: txt.PAGE_ONLINE_SERVICES,
    SettingsPage.SCAN_QUALITY: txt.PAGE_SCAN_QUALITY,
    SettingsPage.PRIVACY: txt.PAGE_PRIVACY,
    SettingsPage.ADDONS: txt.PAGE_ADDONS,
}
