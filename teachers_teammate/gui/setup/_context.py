"""What the main window hands to the assistant, and what the assistant hands back.

Kept apart from the package's ``__init__`` so the pages and the wizard can import these types
without importing each other.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...application.composition import AppServices


@dataclass(frozen=True)
class SetupContext:
    """Everything the main window hands to the assistant when it opens.

    ``settings`` is a copy of the panel's settings dict; the assistant never writes into it.
    ``first_start`` says the app has no settings file yet, so Cancel has to leave the teacher
    with a working setup instead of nothing.
    """

    settings: Mapping[str, Any]
    app_service: AppServices
    pull_manager: Any = None
    #: The document language to start on (the system language on a first start).
    language: str = ""
    privacy_available: bool = True
    first_start: bool = False
    #: The window shows the texts with placeholders for the names (the 👁 setting).
    display_anonymized: bool = True


@dataclass(frozen=True)
class SetupResult:
    """What the assistant hands back when the teacher presses Set up.

    ``settings`` are the Settings-window keys, ``panel`` the main-window ones.  Both are
    applied by the main window, which saves them at once.  ``display_anonymized`` is ``True``
    when the teacher asked to see the names hidden in the texts, and ``None`` when the
    assistant leaves that view setting alone (it never turns anything off).
    """

    settings: dict[str, Any]
    panel: dict[str, Any]
    display_anonymized: bool | None = None
