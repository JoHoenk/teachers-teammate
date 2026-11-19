"""One row: a label, the chosen path, and the buttons that change it.

The documents row and the results row are built from it, so the two steps look and work the
same.  The field is read-only: a path is chosen with the buttons, never typed.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

LABEL_WIDTH = 90


class PathRow(QWidget):
    """A label, a read-only path field and the buttons that choose the path."""

    def __init__(self, label: str, placeholder: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._label = QLabel(label)
        self._label.setMinimumWidth(LABEL_WIDTH)
        self._field = QLineEdit()
        self._field.setReadOnly(True)
        self._field.setPlaceholderText(placeholder)
        self._layout.addWidget(self._label)
        self._layout.addWidget(self._field, stretch=1)

    def add_button(self, text: str, tooltip: str, slot: Callable[[], None]) -> QPushButton:
        """Add a button on the right; ``slot`` runs when it is clicked."""
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: slot())
        self._layout.addWidget(button)
        return button

    def field(self) -> QLineEdit:
        """Return the read-only field, for a placeholder or a tooltip."""
        return self._field

    def text(self) -> str:
        """Return the path shown, or an empty string."""
        return self._field.text()

    def set_text(self, text: str) -> None:
        """Show ``text`` in the field."""
        self._field.setText(text)

    def set_tooltip(self, text: str) -> None:
        """Set the tooltip of the field."""
        self._field.setToolTip(text)
