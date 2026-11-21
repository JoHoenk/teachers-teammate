"""The line an app update leaves behind: the add-ons on disk are older than the app.

It is empty most of the time; after an update it offers one button that installs the
add-ons again with the versions the app was built with.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ... import _user_text as txt


class UpdateBanner(QWidget):
    """The hint that the add-ons are outdated, and the button that installs them again."""

    #: The teacher clicked *Update add-ons*.
    update_requested = Signal()

    def __init__(self, outdated: Callable[[], bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._outdated = outdated
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel(f"<b>{txt.ADDONS_OUTDATED_LINE}.</b> {txt.ADDONS_OUTDATED_NOTE}")
        note.setWordWrap(True)
        self.button = QPushButton(txt.UPDATE_ADDONS)
        self.button.clicked.connect(self.update_requested.emit)
        layout.addWidget(note, stretch=1)
        layout.addWidget(self.button)
        self.refresh()

    def refresh(self) -> None:
        """Show the banner while the packages folder waits for an update."""
        self.setVisible(self._outdated())

    def set_installing(self, installing: bool) -> None:
        """Say whether the update round runs: the button waits while it does."""
        self.button.setEnabled(not installing)
