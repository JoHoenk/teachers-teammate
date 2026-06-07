"""Collapsible section widget with an animated QToolButton header."""

from __future__ import annotations

from PySide6.QtCore import QPropertyAnimation, Qt
from PySide6.QtWidgets import QSizePolicy, QToolButton, QVBoxLayout, QWidget

_MAX_HEIGHT = 16777215  # Qt QWIDGETSIZE_MAX


class CollapsibleSection(QWidget):
    """A titled group that can be expanded or collapsed by clicking its header.

    Usage::

        section = CollapsibleSection("OCR Parameters")
        form = QFormLayout(section.content_widget)
        form.addRow("Engine:", some_combo)

    The *collapsed* keyword sets the initial state.
    """

    def __init__(
        self,
        title: str,
        *,
        collapsed: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._toggle = QToolButton()
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(not collapsed)
        self._toggle.setArrowType(
            Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow
        )
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; text-align: left; padding: 4px 2px; }"
        )
        self._toggle.toggled.connect(self._on_toggled)
        outer.addWidget(self._toggle)

        self._content = QWidget()
        self._content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        outer.addWidget(self._content)

        self._anim = QPropertyAnimation(self._content, b"maximumHeight")
        self._anim.setDuration(150)

        if collapsed:
            self._content.setMaximumHeight(0)

    @property
    def content_widget(self) -> QWidget:
        """The inner widget; attach a layout here to add rows."""
        return self._content

    def _on_toggled(self, checked: bool) -> None:
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        if checked:
            self._content.setMaximumHeight(_MAX_HEIGHT)
            target = max(self._content.sizeHint().height(), 10)
            self._anim.setStartValue(0)
            self._anim.setEndValue(target)
        else:
            self._anim.setStartValue(self._content.height())
            self._anim.setEndValue(0)
        self._anim.start()
