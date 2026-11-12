"""The read-only "Suggestions are for …" line with its "change…" link.

The computer profile is edited on *This computer* and nowhere else.  The pages whose model
suggestions depend on it (AI models, Add-ons) show it as one line, and "change…" takes the
teacher to the place where it is edited.  Both pages use this widget so the line looks and
behaves the same.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ...config import DEFAULTS
from .._hardware_profile_selector import HardwareProfileSelector, _ProfileService

CHANGE_PROFILE_LINK = '<a href="change">change…</a>'


class ProfileLine(QWidget):
    """A read-only :class:`HardwareProfileSelector` plus a "change…" link."""

    #: "change…" was clicked.
    change_requested = Signal()

    def __init__(
        self,
        *,
        app_service: _ProfileService,
        setting: str = DEFAULTS["hardware_profile"],
        ollama_url: str = DEFAULTS["ollama_url"],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.selector = HardwareProfileSelector(
            app_service=app_service,
            setting=setting,
            ollama_url=ollama_url,
            read_only=True,
        )
        layout.addWidget(self.selector, stretch=1)
        self.change_link = QLabel(CHANGE_PROFILE_LINK)
        self.change_link.linkActivated.connect(lambda _link: self.change_requested.emit())
        layout.addWidget(self.change_link)

    def set_setting(self, setting: str) -> None:
        """Follow the computer profile chosen on *This computer*."""
        self.selector.set_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        """Follow the local AI server address chosen on *This computer*."""
        self.selector.set_ollama_url(url)
