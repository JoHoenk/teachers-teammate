"""A short right-aligned label next to each entry of a model combo box.

The model lists of the settings dialogs used to say what a model is good for only in a
tooltip and in the hint line below the box, so comparing models meant selecting them one at
a time.  This delegate draws the label next to the name, where it can be seen at a glance:

    gemma3:12b                  ★ Best  · 9 GB
    qwen3:8b                      Good  · 6 GB
    ────────────────────────────────────────────
    llama3.2:3b                  Works  · 3 GB

The item *text* stays the raw model ID, so ``currentText()`` and the settings file are
unaffected; the label and its colour live in item roles.  :func:`fill_grouped` adds the
entries and puts a separator between groups.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QModelIndex, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

#: Short label drawn at the right end of a row (``"★ Best"``).
ITEM_LABEL_ROLE = Qt.ItemDataRole.UserRole + 1
#: Colour of that label; without it the row's normal text colour is used.
ITEM_LABEL_COLOR_ROLE = Qt.ItemDataRole.UserRole + 2

#: Space between the model name and the label, and before the label at the row's right end.
_GAP = 12

#: Height of the row ``QComboBox.insertSeparator`` adds between two groups.
_SEPARATOR_HEIGHT = 9


@dataclass(frozen=True)
class ModelItem:
    """One entry of a grouped model list."""

    text: str  # the raw model ID: what the settings keep and the API is sent
    label: str = ""  # short right-aligned text, e.g. "★ Best"
    label_color: QColor | None = None
    tooltip: str = ""


class ModelItemDelegate(QStyledItemDelegate):
    """Draws a row's text on the left and its :data:`ITEM_LABEL_ROLE` label on the right."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        if _is_separator(index):
            self._paint_separator(painter, option)
            return
        label = _label(index)
        if not label:
            super().paint(painter, option, index)
            return

        style_option = QStyleOptionViewItem(option)
        self.initStyleOption(style_option, index)
        widget = style_option.widget
        style = widget.style() if widget is not None else QApplication.style()
        metrics = QFontMetrics(style_option.font)

        # Background, selection and focus frame over the whole row, without its text.
        background = QStyleOptionViewItem(style_option)
        background.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, background, painter, widget)

        margin = style.pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, widget) + 1
        label_rect = QRect(
            style_option.rect.right() - _GAP - metrics.horizontalAdvance(label),
            style_option.rect.top(),
            metrics.horizontalAdvance(label),
            style_option.rect.height(),
        )
        text_left = style_option.rect.left() + margin
        text_width = max(0, label_rect.left() - _GAP - text_left)
        text_option = QStyleOptionViewItem(style_option)
        text_option.rect = QRect(
            text_left, style_option.rect.top(), text_width, style_option.rect.height()
        )
        text_option.text = metrics.elidedText(
            style_option.text, Qt.TextElideMode.ElideRight, text_width
        )
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, text_option, painter, widget)

        painter.save()
        painter.setPen(_label_pen(style_option, index))
        painter.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            label,
        )
        painter.restore()

    @staticmethod
    def _paint_separator(painter: QPainter, option: QStyleOptionViewItem) -> None:
        """Draw the thin divider line of a group separator.

        Replacing a combo's own delegate also drops its separator drawing, so it is done here:
        without it the separator would be an empty row as high as an entry.
        """
        rect = option.rect
        y = rect.center().y()
        painter.save()
        painter.setPen(option.palette.color(QPalette.ColorRole.Mid))
        painter.drawLine(rect.left() + _GAP // 2, y, rect.right() - _GAP // 2, y)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        if _is_separator(index):
            return QSize(0, _SEPARATOR_HEIGHT)
        size = super().sizeHint(option, index)
        label = _label(index)
        if label:
            extra = QFontMetrics(option.font).horizontalAdvance(label) + _GAP
            size.setWidth(size.width() + extra)
        return size


def install(combo: QComboBox) -> ModelItemDelegate:
    """Give *combo* the label delegate (once) and return it."""
    delegate = combo.itemDelegate()
    if isinstance(delegate, ModelItemDelegate):
        return delegate
    delegate = ModelItemDelegate(combo)
    combo.setItemDelegate(delegate)
    return delegate


def fill_grouped(combo: QComboBox, groups: Sequence[Sequence[ModelItem]]) -> None:
    """Replace the entries of *combo* with *groups*, separated by a line between them.

    The caller blocks the combo's signals when it wants to decide the selection itself.
    """
    combo.clear()
    first = True
    for group in groups:
        if not group:
            continue
        if not first:
            combo.insertSeparator(combo.count())
        first = False
        for item in group:
            combo.addItem(item.text)
            row = combo.count() - 1
            if item.label:
                combo.setItemData(row, item.label, ITEM_LABEL_ROLE)
            if item.label_color is not None:
                combo.setItemData(row, item.label_color, ITEM_LABEL_COLOR_ROLE)
            if item.tooltip:
                combo.setItemData(row, item.tooltip, Qt.ItemDataRole.ToolTipRole)
    widen(combo)


def widen(combo: QComboBox) -> None:
    """Make the popup at least as wide as the longest name plus its label."""
    view = combo.view()
    metrics = QFontMetrics(view.font())
    margin = view.style().pixelMetric(QStyle.PixelMetric.PM_FocusFrameHMargin, None, view)
    widest = 0
    for row in range(combo.count()):
        width = metrics.horizontalAdvance(combo.itemText(row))
        label = combo.itemData(row, ITEM_LABEL_ROLE)
        if label:
            width += metrics.horizontalAdvance(str(label)) + _GAP
        widest = max(widest, width)
    if widest:
        view.setMinimumWidth(widest + 2 * (margin + _GAP))


def _is_separator(index: QModelIndex) -> bool:
    """Return whether *index* is a separator added by ``QComboBox.insertSeparator``."""
    return index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == "separator"


def _label(index: QModelIndex) -> str:
    """Return the label of *index* (empty for a separator or an unlabelled row)."""
    value = index.data(ITEM_LABEL_ROLE)
    return str(value) if value else ""


def _label_pen(option: QStyleOptionViewItem, index: QModelIndex) -> QColor:
    """Return the colour of the label: the item's own, the normal text or the selected text."""
    if option.state & QStyle.StateFlag.State_Selected:
        # A semantic colour such as grey or orange is unreadable on the highlight.
        return option.palette.color(QPalette.ColorRole.HighlightedText)
    stored = index.data(ITEM_LABEL_COLOR_ROLE)
    if isinstance(stored, QColor):
        return stored
    return option.palette.color(QPalette.ColorRole.Text)


__all__ = [
    "ITEM_LABEL_COLOR_ROLE",
    "ITEM_LABEL_ROLE",
    "ModelItem",
    "ModelItemDelegate",
    "fill_grouped",
    "install",
    "widen",
]
