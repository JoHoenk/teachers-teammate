"""A layout that puts its widgets side by side and wraps them onto the next line.

The filter row above the list has one chip per category, and how many there are depends on the
document; a plain row would run out of the panel's width.  Qt has no wrapping box layout, so this
is the small one it needs: left to right, top to bottom, hidden widgets skipped.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Lays its widgets out left to right, wrapping to the next line when the width runs out."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 2) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self.setContentsMargins(QMargins(0, 0, 0, 0))
        self.setSpacing(spacing)

    def addItem(self, item: QLayoutItem) -> None:
        """Add *item* to the end of the flow."""
        self._items.append(item)

    def count(self) -> int:
        """How many items the layout holds."""
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        """Return the item at *index*, or ``None``."""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        """Remove and return the item at *index*."""
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def hasHeightForWidth(self) -> bool:
        """The height depends on the width: more chips fit on a line in a wider panel."""
        return True

    def heightForWidth(self, width: int) -> int:
        """Return the height the layout needs at *width*."""
        return self._do_layout(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        """Place the widgets inside *rect*."""
        super().setGeometry(rect)
        self._do_layout(rect, apply=True)

    def sizeHint(self) -> QSize:
        """Return the size the layout would like: one line of its widest widget."""
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """Return the size of the widest widget, one line high."""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, *, apply: bool) -> int:
        """Place the widgets in *rect*, or only measure them; return the height used."""
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._items:
            widget = item.widget()
            if widget is not None and widget.isHidden():
                continue
            hint = item.sizeHint()
            if line_height and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + self.spacing()
                line_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.spacing()
            line_height = max(line_height, hint.height())
        return y + line_height - rect.y() + margins.bottom()
