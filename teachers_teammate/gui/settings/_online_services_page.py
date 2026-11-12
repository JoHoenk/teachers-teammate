"""Settings → Online services: the access keys of the services that run the AI online.

Not a choice of its own (which service proofreads is picked on *AI models*, where its key can
also be typed), so the sidebar shows it indented under AI models next to *This computer*.  It
is the one place that lists every saved key, lets one be removed and lets one be added for a
service that is not chosen yet.  The keys are the window's shared
:class:`~._credentials.ApiKeyDrafts`: a key is used right away and stored when OK is pressed.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .. import _user_text as txt
from ._credentials import ApiKeyDrafts, CredentialsGroup
from ._page import FOCUS_API_KEYS, SettingsContext, SettingsPage, SettingsPageBase

TITLE = txt.PAGE_ONLINE_SERVICES
DESCRIPTION = (
    f"Access keys for the online AI services you can choose on {txt.PAGE_AI_MODELS}. Not "
    "needed while everything runs on this computer."
)


class OnlineServicesPage(SettingsPageBase):
    """The saved API keys, one row each, plus a picker to add a key for another service."""

    page = SettingsPage.ONLINE_SERVICES
    title = TITLE
    description = DESCRIPTION

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        drafts = context.key_drafts or ApiKeyDrafts(context.app_service, parent=self)
        self._credentials = CredentialsGroup(context.app_service, drafts=drafts)
        layout.addWidget(self._credentials)
        layout.addStretch(1)

    def apply(self) -> None:
        """Hand the key fields to the drafts; the window stores them (it owns the keys).

        A field changed a moment ago has not had its pause yet, and OK must not lose it.
        """
        self._credentials.commit()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        _ = preselect
        if focus == FOCUS_API_KEYS:
            self._scroll_to(self._credentials)

    def shutdown(self) -> None:
        self._credentials.stop()
