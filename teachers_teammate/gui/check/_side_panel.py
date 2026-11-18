"""The side panel of the Check tab: the card of the chosen item above the list of all items.

The list numbers the same things the text marks — one row per item, in text order — and the card
says what the chosen one is and what the teacher can do with it.  Every action is a request: the
panel never edits a text itself, it says which item and which action, and the page turns that into
an edit of the final text.

The spelling and punctuation changes are folded into one row, which opens in place into its items;
a group of one is not folded, since a group row over a single correction is only noise.

All wording comes from :mod:`.._user_text`; the colours and icons from :mod:`._style`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
import html

from PySide6.QtCore import QSize, Qt, Signal, SignalInstance
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...application.check_document import CheckDocument
from ...application.check_items import CheckItem, ItemKind, group_minor
from .. import _user_text as txt
from . import _style
from ._masking import Masking

#: The key of the row that folds the spelling and punctuation changes; no item has it.
GROUP_KEY = "minor"

#: Item data role that carries the :class:`_Row` of a list entry.
_ROW_ROLE = Qt.ItemDataRole.UserRole + 1

_ROW_HEIGHT = 24


@dataclass(frozen=True)
class _Row:
    """One row of the list: an item, or the group of the spelling and punctuation changes."""

    key: str
    item: CheckItem | None = None
    members: tuple[CheckItem, ...] = ()

    @property
    def is_group(self) -> bool:
        """Whether this row folds several items."""
        return self.item is None


def rows_of(items: Sequence[CheckItem]) -> tuple[_Row, ...]:
    """Return the rows of *items*: one per item, with the minor ones folded into one row."""
    minor = group_minor(items)
    folded = len(minor) > 1
    rows: list[_Row] = []
    placed = False
    for item in items:
        if item.is_minor and folded:
            if not placed:
                rows.append(_Row(GROUP_KEY, members=minor))
                rows.extend(_Row(member.key, item=member) for member in minor)
                placed = True
            continue
        rows.append(_Row(item.key, item=item))
    return tuple(rows)


def _words_of(row: _Row) -> str:
    """Return the words a row shows: the student's, an arrow, and the corrected ones."""
    if row.item is None:
        return txt.minor_group_label(len(row.members))
    item = row.item
    if item.kind is ItemKind.UNCLEAR:
        return item.before
    if not item.before:
        return f"\u2192 {item.after}"
    if not item.after:
        return f"{item.before} \u2192"
    return f"{item.before} \u2192 {item.after}"


def _icon_of(row: _Row, *, expanded: bool, looked_at: frozenset[str]) -> str:
    """Return the character in front of a row: the group's arrow, else whether it was looked at."""
    if row.item is None:
        return "\u25be" if expanded else "\u25b8"
    return _style.icon(row.item.kind, looked_at=row.key in looked_at)


def _category_of(row: _Row) -> str:
    """Return the category a row shows on the right, or ``""``."""
    if row.item is None or not row.item.category:
        return ""
    return txt.category_label(row.item.category)


class _ItemDelegate(QStyledItemDelegate):
    """Draws one row: the icon of its kind, its words, and its category in a muted colour."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.expanded = False
        """Whether the group row shows its items, which decides its arrow."""
        self.looked_at: frozenset[str] = frozenset()
        """The keys the teacher has looked at, which decides the icon of every other row."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """Draw the row of *index*; a row without one falls back to the plain text."""
        row: _Row | None = index.data(_ROW_ROLE)
        if row is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = opt.rect.adjusted(6, 0, -6, 0)
        metrics = opt.fontMetrics
        text_colour = opt.palette.color(QPalette.ColorRole.Text)
        muted = QColor(text_colour)
        muted.setAlpha(150)
        painter.save()
        painter.setFont(QFont(opt.font))
        icon = _icon_of(row, expanded=self.expanded, looked_at=self.looked_at)
        painter.setPen(text_colour)
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, icon)
        words_rect = rect.adjusted(metrics.horizontalAdvance(icon) + 6, 0, 0, 0)
        category = _category_of(row)
        if category:
            width = metrics.horizontalAdvance(category) + 8
            painter.setPen(muted)
            painter.drawText(
                rect, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, category
            )
            words_rect = words_rect.adjusted(0, 0, -width, 0)
        painter.setPen(text_colour)
        painter.drawText(
            words_rect,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(_words_of(row), Qt.TextElideMode.ElideRight, words_rect.width()),
        )
        painter.restore()

    def sizeHint(self, _option: QStyleOptionViewItem, _index) -> QSize:
        """Every row is one line high."""
        return QSize(0, _ROW_HEIGHT)


def _title_html(row: _Row, masking: Masking) -> str:
    """Return the headline of the card: the words of the item, the model's words struck if gone."""
    if row.item is None:
        return html.escape(txt.minor_group_label(len(row.members)))
    item = row.item
    before = html.escape(masking.mask(item.before))
    after = html.escape(masking.mask(item.after))
    if item.kind is ItemKind.UNCLEAR:
        return before
    if item.kind is ItemKind.TAKEN_OUT:
        return f"{before} \u2192 <s>{after}</s>" if before else f"\u2192 <s>{after}</s>"
    if not before:
        return f"\u2192 {after}"
    if not after:
        return f"{before} \u2192"
    return f"{before} \u2192 {after}"


def _body_html(row: _Row, masking: Masking) -> str:
    """Return what the card says under the words: category, explanation and the notes."""
    if row.item is None:
        return f"<i>{html.escape(txt.MINOR_GROUP_NOTE)}</i>"
    item = row.item
    lines: list[str] = []
    if item.kind is ItemKind.UNCLEAR:
        if item.why:
            lines.append(f"{html.escape(txt.WHY_PREFIX)} {html.escape(masking.mask(item.why))}")
        if item.suggestion:
            caption = txt.recommendation_caption(item.suggestion_uncertain)
            lines.append(f"{html.escape(caption)} {html.escape(masking.mask(item.suggestion))}")
        note = txt.suggestion_note(item.suggestion_uncertain)
        if note:
            lines.append(f"<i>{html.escape(note)}</i>")
        return "<br>".join(lines)
    if item.category:
        lines.append(f"<i>{html.escape(txt.category_label(item.category))}</i>")
    if item.explanation:
        lines.append(html.escape(masking.mask(item.explanation)))
    if item.kind is ItemKind.UNEXPLAINED:
        lines.append(f"<i>{html.escape(txt.NO_EXPLANATION_MADE)}</i>")
    if item.kind in (ItemKind.CHANGED, ItemKind.OWN):
        lines.append(f"<i>{html.escape(txt.YOUR_CHANGE)}</i>")
    if item.kind is ItemKind.TAKEN_OUT:
        lines.append(f"<i>{html.escape(txt.TAKEN_OUT)}</i>")
    if item.optional:
        lines.append(f"<i>{html.escape(txt.OPTIONAL_CHANGE)}</i>")
    if item.needed is False:
        lines.append(f"<i>{html.escape(txt.AI_NOT_SURE)}</i>")
    return "<br>".join(lines)


class _Card(QFrame):
    """The chosen item: what it says, and the buttons that act on it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setTextFormat(Qt.TextFormat.RichText)
        title_font = self._title.font()
        title_font.setBold(True)
        self._title.setFont(title_font)
        layout.addWidget(self._title)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self._body)

        self.take_out = QPushButton(txt.TAKE_OUT)
        self.put_back = QPushButton(txt.PUT_BACK)
        self.use_suggestion = QPushButton(txt.USE_SUGGESTION)
        self.change = QPushButton(txt.CHANGE)
        self.take_out_all = QPushButton(txt.TAKE_OUT_ALL)
        self._buttons = (
            self.take_out,
            self.put_back,
            self.use_suggestion,
            self.change,
            self.take_out_all,
        )
        row = QHBoxLayout()
        row.setSpacing(4)
        for button in self._buttons:
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.set_row(None, Masking(), editable=False)

    def set_note(self, text: str) -> None:
        """Say *text* in place of the card's words, without any button."""
        for button in self._buttons:
            button.setVisible(False)
        self._title.clear()
        self._body.setText(html.escape(text))
        self.setVisible(bool(text))

    def set_row(
        self, row: _Row | None, masking: Masking, *, editable: bool, reason: str = ""
    ) -> None:
        """Show *row*, or nothing; *editable* says whether its buttons may be pressed."""
        for button in self._buttons:
            button.setVisible(False)
            button.setEnabled(editable)
            button.setToolTip(reason)
        if row is None:
            self._title.clear()
            self._body.clear()
            self.setVisible(False)
            return
        self.setVisible(True)
        self._title.setText(_title_html(row, masking))
        self._body.setText(_body_html(row, masking))
        for button, tooltip in self._buttons_of(row):
            button.setVisible(True)
            button.setToolTip(reason or tooltip)

    def _buttons_of(self, row: _Row) -> tuple[tuple[QPushButton, str], ...]:
        """Return the buttons *row* offers, each with its tooltip."""
        if row.item is None:
            return ((self.take_out_all, txt.TAKE_OUT_ALL_TOOLTIP),)
        kind = row.item.kind
        if kind is ItemKind.TAKEN_OUT:
            return ((self.put_back, txt.PUT_BACK_TOOLTIP),)
        if kind is ItemKind.UNCLEAR:
            return ((self.use_suggestion, txt.USE_SUGGESTION_TOOLTIP),)
        if kind is ItemKind.OWN:
            return ((self.take_out, txt.TAKE_OUT_TOOLTIP),)
        return (
            (self.take_out, txt.TAKE_OUT_TOOLTIP),
            (self.change, txt.CHANGE_TOOLTIP),
        )


class SidePanel(QWidget):
    """The card of the chosen item above the list of everything the text marks."""

    item_chosen = Signal(str)
    """A row was selected: the key of its item."""
    item_shown = Signal(str)
    """An item became the one the card shows: its key, or ``"minor"`` for the group row.

    Emitted for the first item of a document too, and again when a reload keeps the same row:
    the window decides whether it is a key it has not recorded yet.
    """
    take_out_requested = Signal(str)
    """The teacher asked for the student's words back for the item of this key."""
    put_back_requested = Signal(str)
    """The teacher asked for the correction of this key back in the text."""
    use_suggestion_requested = Signal(str)
    """The teacher asked for the suggested wording of this unclear passage."""
    take_out_all_requested = Signal()
    """The teacher asked for the student's words back for every minor correction."""
    change_requested = Signal(str)
    """The teacher asked to go to the words of this item in the final text."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[_Row, ...] = ()
        self._doc: CheckDocument | None = None
        self._masking = Masking()
        self._folded = False
        self._expanded = False
        self._choosing = False
        self._editable = True
        self._reason = ""
        self._note = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._card = _Card()
        layout.addWidget(self._card)

        self._list = QListWidget()
        self._delegate = _ItemDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.currentRowChanged.connect(self._on_row_changed)
        layout.addWidget(self._list, 1)

        self._counts = QLabel()
        self._counts.setStyleSheet("color: #888;")
        layout.addWidget(self._counts)

        layout.addWidget(self._build_content_section())

        self._card.take_out.clicked.connect(lambda: self._request(self.take_out_requested))
        self._card.put_back.clicked.connect(lambda: self._request(self.put_back_requested))
        self._card.use_suggestion.clicked.connect(
            lambda: self._request(self.use_suggestion_requested)
        )
        self._card.change.clicked.connect(lambda: self._request(self.change_requested))
        self._card.take_out_all.clicked.connect(self.take_out_all_requested)

    def _build_content_section(self) -> QWidget:
        """Return the collapsible section that shows the content review, read-only."""
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._content_toggle = QToolButton()
        self._content_toggle.setText(txt.CONTENT_REVIEW_HEADING)
        self._content_toggle.setCheckable(True)
        self._content_toggle.setAutoRaise(True)
        self._content_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._content_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._content_toggle.toggled.connect(self._on_content_toggled)
        layout.addWidget(self._content_toggle)
        self._content_view = QTextBrowser()
        self._content_view.setOpenExternalLinks(False)
        self._content_view.setVisible(False)
        layout.addWidget(self._content_view, 1)
        self._content_section = section
        section.setVisible(False)
        return section

    def _on_content_toggled(self, expanded: bool) -> None:
        """Open or close the content review."""
        self._content_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self._content_view.setVisible(expanded)

    # ── What the page pushes ──────────────────────────────────────────────

    def set_document(self, doc: CheckDocument, masking: Masking) -> None:
        """Show the items of *doc*, keeping the chosen one where it still exists."""
        chosen = self.chosen
        row = self._list.currentRow()
        self._doc = doc
        self._masking = masking
        self._rows = rows_of(doc.items)
        self._folded = len(group_minor(doc.items)) > 1
        self._list.blockSignals(True)
        self._list.clear()
        for entry in self._rows:
            item = QListWidgetItem()
            item.setData(_ROW_ROLE, entry)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._set_expanded(self._expanded)
        self._delegate.looked_at = doc.work.looked_at
        self._counts.setText(self._counts_text())
        self._set_content(doc.content_review)
        self._select(chosen, row)

    def set_chosen(self, key: str) -> None:
        """Select the row of *key*, if the list has it, without telling the page back."""
        for index, row in enumerate(self._rows):
            if row.item is not None and row.item.key == key:
                self._choosing = True
                try:
                    self._list.setCurrentRow(index)
                finally:
                    self._choosing = False
                return

    def set_note(self, text: str) -> None:
        """Say something in place of the card, e.g. that the marks could not be placed."""
        self._note = text
        self._card.set_row(
            self._current_row(), self._masking, editable=self._editable, reason=self._reason
        )
        if text:
            self._card.set_note(text)

    def set_editable(self, editable: bool, *, reason: str = "") -> None:
        """Allow or refuse the card's actions; *reason* is the tooltip that says why not."""
        self._editable = editable
        self._reason = reason
        self._card.set_row(self._current_row(), self._masking, editable=editable, reason=reason)
        if self._note:
            self._card.set_note(self._note)

    def set_looked_at(self, looked_at: frozenset[str], progress: tuple[int, int]) -> None:
        """Show which items were looked at, without rebuilding the list or moving the choice."""
        self._delegate.looked_at = looked_at
        self._list.viewport().update()
        if self._doc is not None:
            self._doc = replace(self._doc, looked_at_progress=progress)
        self._counts.setText(self._counts_text())

    def clear(self) -> None:
        """Empty the panel: no document is open."""
        self._rows = ()
        self._doc = None
        self._list.clear()
        self._counts.clear()
        self._set_content("")
        self._card.set_row(None, self._masking, editable=False)

    @property
    def chosen(self) -> str:
        """The key of the chosen item, or ``""`` for the group row and for no choice."""
        row = self._current_row()
        return row.item.key if row is not None and row.item is not None else ""

    @property
    def row_count(self) -> int:
        """How many rows the list has, the folded ones included."""
        return len(self._rows)

    # ── The list ──────────────────────────────────────────────────────────

    def _current_row(self) -> _Row | None:
        """Return the row the list has selected, or ``None``."""
        index = self._list.currentRow()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _on_row_changed(self, index: int) -> None:
        """Show the card of the selected row, and tell the page which item it is."""
        row = self._rows[index] if 0 <= index < len(self._rows) else None
        if row is not None and row.is_group:
            self._set_expanded(True)
        self._card.set_row(row, self._masking, editable=self._editable, reason=self._reason)
        if self._note:
            self._card.set_note(self._note)
        if row is None:
            return
        self.item_shown.emit(row.key)
        if row.item is not None and not self._choosing:
            self.item_chosen.emit(row.item.key)

    def _select(self, chosen: str, row: int) -> None:
        """Select *chosen*, or the item nearest to *row* when the item it was is gone.

        The list was rebuilt, not clicked: the page is not told, because it would scroll to the
        item and select its words in a text the teacher may be typing in.
        """
        if chosen:
            self.set_chosen(chosen)
            if self.chosen == chosen:
                return
        elif 0 <= row < len(self._rows) and self._rows[row].is_group:
            self._set_row_silently(row)  # the folded group stays the row that is open
            return
        index = self._nearest_item_row(max(row, 0))
        if index is None:
            self._card.set_row(None, self._masking, editable=False)
            return
        self._set_row_silently(index)

    def _set_row_silently(self, index: int) -> None:
        """Select the row at *index* without telling the page."""
        self._choosing = True
        try:
            self._list.setCurrentRow(index)
        finally:
            self._choosing = False

    def _nearest_item_row(self, row: int) -> int | None:
        """Return the first row of an item at or after *row*, else the last one before it."""
        indices = [index for index, entry in enumerate(self._rows) if entry.item is not None]
        if not indices:
            return None
        after = [index for index in indices if index >= row]
        return after[0] if after else indices[-1]

    def _set_expanded(self, expanded: bool) -> None:
        """Open or close the folded spelling and punctuation changes."""
        self._expanded = expanded
        self._delegate.expanded = expanded
        for index, row in enumerate(self._rows):
            if row.item is not None and row.item.is_minor and self._folded:
                self._list.item(index).setHidden(not expanded)
        self._list.viewport().update()

    def _request(self, signal: SignalInstance) -> None:
        """Ask the page for the action of the chosen item."""
        key = self.chosen
        if key:
            signal.emit(key)

    # ── The footer and the content review ─────────────────────────────────

    def _counts_text(self) -> str:
        """Return the footer line: the corrections, the unclear passages, and how far it is read."""
        doc = self._doc
        if doc is None:
            return ""
        unclear = sum(1 for item in doc.items if item.kind is ItemKind.UNCLEAR)
        looked_at, countable = doc.looked_at_progress
        parts = [
            txt.item_counts(len(doc.items) - unclear, unclear),
            txt.looked_at_progress_text(looked_at, countable),
        ]
        return " \u00b7 ".join(part for part in parts if part)

    def _set_content(self, content_review: str) -> None:
        """Show the content review of the document, or hide the section when there is none."""
        text = content_review.strip()
        self._content_section.setVisible(bool(text))
        if not text:
            self._content_view.clear()
            return
        self._content_view.setMarkdown(content_review)
