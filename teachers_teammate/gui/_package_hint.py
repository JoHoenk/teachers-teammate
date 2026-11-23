"""Inline hint that an AI service's Python package is missing, with an install shortcut."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..domain.install_item import InstallItem, InstallKind
from ..texts import addons as addon_text
from ..texts.issues import package_missing_line
from ._model_guidance import OpenDownloads


class PackageHint(QWidget):
    """Says an AI service's package is missing and offers to install it from Add-ons.

    "Install…" switches to Settings → Add-ons with the package ticked; the owner looks
    again once the install finished (``on_packages_changed``).
    """

    def __init__(
        self,
        open_downloads: OpenDownloads | None,
        parent: QWidget | None = None,
        *,
        button_text: str = addon_text.PACKAGE_BUTTON,
        button_tooltip: str = addon_text.PACKAGE_BUTTON_TOOLTIP,
    ) -> None:
        """Build the hint.

        *button_text* and *button_tooltip* name the button: the Setup Assistant installs the
        package right there, so it says "Install now" instead of pointing at Add-ons.
        """
        super().__init__(parent)
        self._open_downloads = open_downloads
        self._provider = ""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel()
        self._label.setStyleSheet("color: #c0392b;")
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._label, stretch=1)
        self._button = QPushButton(button_text)
        self._button.setToolTip(button_tooltip)
        self._button.setVisible(open_downloads is not None)
        self._button.clicked.connect(self._on_install)
        layout.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignTop)
        self.setVisible(False)

    def show_missing(self, provider: str, display_name: str, pip_name: str) -> None:
        """Show that *provider*'s package *pip_name* is missing."""
        self._provider = provider
        text = f"⚠ {package_missing_line(display_name)}"
        if not getattr(sys, "frozen", False):
            text += "\n" + addon_text.package_pip_hint(pip_name)
        self._label.setText(text)
        self.setVisible(True)

    def clear(self) -> None:
        """Hide the hint (the package is installed, or the service needs none)."""
        self._provider = ""
        self._label.clear()
        self.setVisible(False)

    def message(self) -> str:
        """Return the shown text (empty while hidden)."""
        return self._label.text() if not self.isHidden() else ""

    def _on_install(self) -> None:
        if self._open_downloads is None or not self._provider:
            return
        self._open_downloads([InstallItem(InstallKind.PROVIDER, self._provider)], self.window())
