"""Inline hint that an AI service's Python package is missing, with an install shortcut."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ._model_guidance import PROVIDER_PREFIX, OpenDownloads

PACKAGE_MISSING = "{name} support is not installed yet."
INSTALL_BUTTON = "Install…"
INSTALL_BUTTON_TOOLTIP = "Opens Add-ons, where this package can be installed with one click."
PIP_HINT = "Or run:  pip install {pip}"


class PackageHint(QWidget):
    """Says an AI service's package is missing and offers to install it from Add-ons.

    "Install…" switches to Settings → Add-ons with the package ticked; the owner looks
    again once the install finished (``on_packages_changed``).
    """

    def __init__(self, open_downloads: OpenDownloads | None, parent: QWidget | None = None) -> None:
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
        self._button = QPushButton(INSTALL_BUTTON)
        self._button.setToolTip(INSTALL_BUTTON_TOOLTIP)
        self._button.setVisible(open_downloads is not None)
        self._button.clicked.connect(self._on_install)
        layout.addWidget(self._button, alignment=Qt.AlignmentFlag.AlignTop)
        self.setVisible(False)

    def show_missing(self, provider: str, display_name: str, pip_name: str) -> None:
        """Show that *provider*'s package *pip_name* is missing."""
        self._provider = provider
        text = f"⚠ {PACKAGE_MISSING.format(name=display_name)}"
        if not getattr(sys, "frozen", False):
            text += f"\n{PIP_HINT.format(pip=pip_name)}"
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
        self._open_downloads([f"{PROVIDER_PREFIX}{self._provider}"], self.window())
