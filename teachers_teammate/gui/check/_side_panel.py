"""The side panel of the Check tab: the card of the chosen item above the list of all items.

The list numbers the same things the text marks — one row per item, in text order — and the card
says what the chosen one is and what the teacher can do with it.  Every action is a request: the
panel never edits a text itself, it says which item and which action, and the page turns that into
an edit of the final text.

*Next* on the card walks the rows the teacher has not looked at yet, in list order and from the top
at the end; a document opens at the first of them.  The walk keeps no state of its own: choosing a
row is what marks it looked at, so the icons and the footer say how far it got.  The boxes above
the list narrow both the list and the walk: nothing ticked shows every row, and *Not looked at*
narrows whatever else is ticked, while the counts keep covering the whole document.

The card says what the change is on one side and why it was made on the other, side by side once
the panel is wide enough for both.  Its height is the teacher's — a splitter against the list, so
a long explanation is read whole — and its text scrolls inside it, so neither its buttons nor
*Next* move while the teacher steps through a document.  The content review has a tab of its own
next to the list.

All wording comes from :mod:`...texts.review` and :mod:`...texts.check`; the colours and icons from
:mod:`._style`.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
import html

from PySide6.QtCore import QRect, QSize, Qt, Signal, SignalInstance
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...application.check_document import CheckDocument
from ...application.check_items import (
    MODEL_KINDS,
    CheckItem,
    ItemKind,
    counts_for_looked_at,
    first_unseen,
    is_minor_correction,
    minor_corrections,
    next_unseen,
)
from ...domain.proofreading.review import CHANGE_CATEGORIES
from ...texts import check as check_txt
from ...texts import review as txt
from .._handles import ITEM_LIST
from . import _style
from ._flow import FlowLayout
from ._masking import Masking

#: The filter that shows only the rows the teacher has not looked at yet.
FILTER_UNSEEN = "unseen"

#: The filters of the two kinds that have no category of their own.
FILTER_UNCLEAR = "unclear"
FILTER_LEFT_OUT = "left_out"

#: Item data role that carries the :class:`CheckItem` of a list entry.
_ROW_ROLE = Qt.ItemDataRole.UserRole + 1

_ROW_HEIGHT = 24

#: How wide the bar at the left of a row is: the accent of its kind.
_BAR_WIDTH = 3

#: How many rows of the list stay visible whatever the card's height is.
_LIST_MIN_ROWS = 3

#: How many lines of text the card shows before its text scrolls, until the teacher drags it.
_CARD_TEXT_LINES = 5

#: How many lines the card keeps at least, so its buttons never sit on a sliver of text.
_CARD_MIN_LINES = 2

#: The card's width from which *Change* and *Why* stand side by side instead of one above the other.
_SIDE_BY_SIDE_WIDTH = 560

#: The kinds whose words the card offers to replace with other words.
_REPLACEABLE = frozenset(
    {ItemKind.CORRECTION, ItemKind.CHANGED, ItemKind.UNEXPLAINED, ItemKind.LEFT_OUT}
)

#: The badge of an item that is no change of the model: what kind of thing it is.
_KIND_BADGES = {
    ItemKind.UNCLEAR: check_txt.BADGE_UNCLEAR,
    ItemKind.LEFT_OUT: check_txt.BADGE_LEFT_OUT,
    ItemKind.OWN: check_txt.BADGE_OWN,
    ItemKind.TAKEN_OUT: check_txt.BADGE_TAKEN_OUT,
}

#: The filter of every kind that has no category of its own.
_KIND_FILTERS = {
    ItemKind.UNCLEAR: FILTER_UNCLEAR,
    ItemKind.LEFT_OUT: FILTER_LEFT_OUT,
}

#: How many characters of the sentence around a change the card shows on either side.
_CONTEXT_CHARS = 60

#: How wide the card's replace field stays, so the buttons beside it keep their place.
_REPLACE_WIDTH = 90

#: The characters that end a sentence, for the context line.
_SENTENCE_ENDS = ".!?\n"


def _filter_key(item: CheckItem) -> str:
    """Return the filter *item* answers to: its category, or the kind it has no category for.

    A change of the model without a category, or with one the boxes do not know, is filed under
    ``"other"``; an item with neither — a change of the teacher's own — answers to no box at all.
    """
    if item.kind in _KIND_FILTERS:
        return _KIND_FILTERS[item.kind]
    if item.category or item.kind in MODEL_KINDS:
        return item.category if item.category in CHANGE_CATEGORIES else "other"
    return ""


def _columns_of(item: CheckItem) -> tuple[str, str]:
    """Return the two columns of a row: the student's words and the corrected ones.

    The second is empty where the item has no corrected wording of its own — an unclear
    expression, a stretch the model left out — and the first then takes the whole width.
    """
    if item.kind in (ItemKind.UNCLEAR, ItemKind.LEFT_OUT):
        return item.before, ""
    return item.before, item.after


def _icon_of(item: CheckItem, *, looked_at: frozenset[str]) -> str:
    """Return the bullet in front of a row: there while the teacher has not looked at it."""
    return _style.todo_mark(item, looked_at=item.key in looked_at)


def _category_of(item: CheckItem) -> str:
    """Return the badge a row shows on the right: what kind of thing it is, else its category.

    The kind comes first, so a correction the teacher took out says *taken out* rather than its
    category; the category of that correction stays on its card.
    """
    kind = _KIND_BADGES.get(item.kind)
    if kind is not None:
        return kind
    return txt.category_label(item.category)


def _words_in_text(item: CheckItem, corrected_plain: str) -> str:
    """Return the words of *item* as they stand in the corrected text.

    The card's replace field starts from what the text says now, not from the model's wording:
    a correction the teacher changed again shows their own words.
    """
    if not item.corrected_spans:
        return item.after
    start = min(span[0] for span in item.corrected_spans)
    end = max(span[1] for span in item.corrected_spans)
    if not 0 <= start <= end <= len(corrected_plain):
        return item.after
    return corrected_plain[start:end]


def _row_font(font: QFont, *, looked_at: bool) -> QFont:
    """Return the font a row is drawn in: bold until the teacher has looked at it."""
    drawn = QFont(font)
    drawn.setBold(not looked_at)
    return drawn


def _bar_rect(rect: QRect) -> QRect:
    """Return the bar at the left edge of a row: the accent of the item's kind."""
    return QRect(rect.left(), rect.top(), _BAR_WIDTH, rect.height())


def _bullet_rect(rect: QRect, metrics) -> QRect:
    """Return the column of the bullet, one bullet wide whether or not it holds one."""
    return QRect(rect.left(), rect.top(), metrics.horizontalAdvance(_style.TODO_DOT), rect.height())


def _column_rects(rect: QRect, arrow_width: int) -> tuple[QRect, QRect, QRect]:
    """Return the rects of the student's words, the arrow and the corrected words.

    The two word columns are the same width in every row, so the words line up down the list.
    """
    column = max((rect.width() - arrow_width) // 2, 0)
    before = QRect(rect.left(), rect.top(), column, rect.height())
    arrow = QRect(before.right() + 1, rect.top(), arrow_width, rect.height())
    after = QRect(
        arrow.right() + 1,
        rect.top(),
        max(rect.width() - column - arrow_width, 0),
        rect.height(),
    )
    return before, arrow, after


class _ItemDelegate(QStyledItemDelegate):
    """Draws one row: its kind's bar, its bullet, the two sorts of words, and the badge.

    The bar at the left edge is the accent of the item's kind, so a row says what happened to the
    text without a word.  The bullet column is one bullet wide whether or not it holds one, so the
    words of all rows start at the same place.  The two word columns are the same width in every
    row, so the student's words and the correction line up down the list.  A row the teacher has
    not looked at is bold, a looked-at one is drawn in the theme's secondary grey, and the chosen
    row in the theme's selection text, so all three stay readable.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.looked_at: frozenset[str] = frozenset()
        """The keys the teacher has looked at, which decides the bullet and the colour."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        """Draw the row of *index*; a row without one falls back to the plain text."""
        item: CheckItem | None = index.data(_ROW_ROLE)
        if item is None:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        looked_at = item.key in self.looked_at
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        font = _row_font(opt.font, looked_at=looked_at)
        metrics = QFontMetrics(font)
        painter.save()
        painter.setFont(font)
        painter.setPen(_style.row_text(opt.palette, looked_at=looked_at, selected=selected))
        painter.fillRect(_bar_rect(opt.rect), QColor(_style.MARK_ACCENTS[item.kind]))
        rect = opt.rect.adjusted(_BAR_WIDTH + 6, 0, -6, 0)
        bullet = _icon_of(item, looked_at=self.looked_at)
        painter.drawText(
            _bullet_rect(rect, metrics),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            bullet,
        )
        words = rect.adjusted(metrics.horizontalAdvance(_style.TODO_DOT) + 6, 0, 0, 0)
        words = self._draw_badge(painter, words, metrics, item)
        self._draw_words(painter, words, metrics, item)
        painter.restore()

    def _draw_badge(
        self,
        painter: QPainter,
        rect: QRect,
        metrics,
        item: CheckItem,
    ) -> QRect:
        """Draw the badge at the right of *rect*; return what is left for the words.

        The badge keeps its neutral grey, also on the chosen row: it is chosen to read together
        with its own text, and the row's colour would not read on it.
        """
        category = _category_of(item)
        if not category:
            return rect
        width = metrics.horizontalAdvance(category) + 10
        badge = QRect(rect.right() - width + 1, rect.top() + 3, width, rect.height() - 6)
        painter.fillRect(badge, QColor(_style.CHIP_BACKGROUND))
        painter.setPen(QColor(_style.CHIP_TEXT))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, category)
        return rect.adjusted(0, 0, -(width + 6), 0)

    def _draw_words(self, painter: QPainter, rect: QRect, metrics, item: CheckItem) -> None:
        """Draw the student's words and the corrected ones in two columns of the same width."""
        before, after = _columns_of(item)
        if not after:
            painter.drawText(
                rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(before, Qt.TextElideMode.ElideRight, rect.width()),
            )
            return
        arrow = " \u2192 "
        before_rect, arrow_rect, after_rect = _column_rects(rect, metrics.horizontalAdvance(arrow))
        for part, part_rect in ((before, before_rect), (arrow, arrow_rect), (after, after_rect)):
            painter.drawText(
                part_rect,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                metrics.elidedText(part, Qt.TextElideMode.ElideRight, part_rect.width()),
            )

    def sizeHint(self, _option: QStyleOptionViewItem, _index) -> QSize:
        """Every row is one line high."""
        return QSize(0, _ROW_HEIGHT)


def _wrapped_label() -> QLabel:
    """Return a label that wraps its rich text and says how tall it is at a given width.

    Without the height-for-width policy a wrapped label reports the height of one long line, and
    the scroll area around it would clip the text instead of scrolling it.
    """
    label = QLabel()
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    policy = label.sizePolicy()
    policy.setHeightForWidth(True)
    label.setSizePolicy(policy)
    return label


def _column(*labels: QLabel) -> QWidget:
    """Return *labels* stacked in one column of the card, held to the top of it."""
    column = QWidget()
    layout = QVBoxLayout(column)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    for label in labels:
        layout.addWidget(label)
    layout.addStretch()
    return column


def _chip(text: str) -> str:
    """Return *text* as a small neutral chip: a category, or a note about the change."""
    return (
        f'<span style="background-color:{_style.CHIP_BACKGROUND};'
        f' color:{_style.CHIP_TEXT};">'
        f"&nbsp;{html.escape(text)}&nbsp;</span>"
    )


def _title_html(item: CheckItem, masking: Masking) -> str:
    """Return the headline of the card: the words of the item, the model's words struck if gone."""
    before = html.escape(masking.mask(item.before))
    after = html.escape(masking.mask(item.after))
    if item.kind in (ItemKind.UNCLEAR, ItemKind.LEFT_OUT):
        return before
    if item.kind is ItemKind.TAKEN_OUT:
        return f"{before} \u2192 <s>{after}</s>" if before else f"\u2192 <s>{after}</s>"
    if not before:
        return f"\u2192 {after}"
    if not after:
        return f"{before} \u2192"
    return f"{before} \u2192 {after}"


def _context_html(item: CheckItem, raw_plain: str, masking: Masking) -> str:
    """Return the sentence around the item's words, with the student's words highlighted.

    The teacher sees what the change is about without looking for it in the text.  The sentence is
    cut a fixed number of characters around the words, so a long one does not push the explanation
    out of the card; the cut ends say so with an ellipsis.
    """
    start, end = item.raw_span
    if not raw_plain or not 0 <= start <= end <= len(raw_plain):
        return ""
    left, right = _context_window(raw_plain, start, end)
    text = raw_plain[left:right]
    masked = masking.mask(text)
    span = masking.carry((start - left, end - left), text)
    words = masked[span[0] : span[1]]
    if not words:
        return html.escape(masked)
    look = _style.look(item)
    highlight = (
        f'<span style="background-color:{look.background}; color:{look.text}; font-weight:bold;">'
        f"{html.escape(words)}</span>"
    )
    return f"{html.escape(masked[: span[0]])}{highlight}{html.escape(masked[span[1] :])}"


def _context_window(raw_plain: str, start: int, end: int) -> tuple[int, int]:
    """Return where the sentence around the words at *start*..*end* begins and ends."""
    left = _sentence_start(raw_plain, max(start - _CONTEXT_CHARS, 0), start)
    right = _sentence_end(raw_plain, min(end + _CONTEXT_CHARS, len(raw_plain)), end)
    while left < start and raw_plain[left].isspace():
        left += 1
    while right > end and raw_plain[right - 1].isspace():
        right -= 1
    return left, right


def _sentence_start(text: str, left: int, start: int) -> int:
    """Return where the sentence around *start* begins, at most *left* characters before it."""
    window = text[left:start]
    cut = max(window.rfind(char) for char in _SENTENCE_ENDS)
    return left if cut < 0 else left + cut + 1


def _sentence_end(text: str, right: int, end: int) -> int:
    """Return where the sentence around *end* ends, at most *right* characters after it."""
    window = text[end:right]
    cuts = [cut for cut in (window.find(char) for char in _SENTENCE_ENDS) if cut >= 0]
    return right if not cuts else end + min(cuts) + 1


def _tags_of(item: CheckItem) -> tuple[str, ...]:
    """Return the short notes about *item*: what kind of change it is, and the model's doubt."""
    tags: list[str] = []
    if item.kind in (ItemKind.CHANGED, ItemKind.OWN):
        tags.append(txt.YOUR_CHANGE)
    if item.kind is ItemKind.TAKEN_OUT:
        tags.append(txt.TAKEN_OUT)
    if item.optional:
        tags.append(txt.OPTIONAL_CHANGE)
    if item.needed is False:
        tags.append(txt.AI_NOT_SURE)
    return tuple(tags)


def _body_html(item: CheckItem, masking: Masking) -> str:
    """Return what the card says under the words: the category, the explanation and the notes."""
    if item.kind is ItemKind.LEFT_OUT:
        return html.escape(masking.mask(item.why))
    if item.kind is ItemKind.UNCLEAR:
        lines: list[str] = []
        if item.why:
            lines.append(f"{html.escape(txt.WHY_PREFIX)} {html.escape(masking.mask(item.why))}")
        if item.suggestion:
            caption = txt.recommendation_caption(item.suggestion_uncertain)
            lines.append(f"{html.escape(caption)} {html.escape(masking.mask(item.suggestion))}")
        note = txt.suggestion_note(item.suggestion_uncertain)
        if note:
            lines.append(f"<i>{html.escape(note)}</i>")
        return "<br>".join(lines)
    lines = []
    if item.category:
        lines.append(_chip(txt.category_label(item.category)))
    if item.explanation:
        lines.append(html.escape(masking.mask(item.explanation)))
    if item.kind is ItemKind.UNEXPLAINED:
        lines.append(f"<i>{html.escape(txt.NO_EXPLANATION_MADE)}</i>")
    tags = _tags_of(item)
    if tags:
        lines.append(" ".join(_chip(tag) for tag in tags))
    return "<br>".join(lines)


class _Card(QFrame):
    """The chosen item: what it changes, why, and the buttons that act on it.

    *Change* — the words and the sentence around them — stands on the left and *Why* — the
    category, the explanation and the notes — on the right, once the card is wide enough for both;
    below that width they stand one above the other.  The text scrolls inside the height the
    teacher gave the card, and the buttons sit on its bottom edge, so the card is as tall for a
    one-line correction as for a long explanation.
    """

    replace_requested = Signal(str, str)
    """The teacher asked for the words of this key to be replaced by the words they typed."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self._build_text(layout)
        self._build_buttons(layout)
        self._key = ""
        self._editable = False
        self.replace.textChanged.connect(self._refresh_replace_ok)
        self.replace.returnPressed.connect(self._on_replace)
        self.replace_ok.clicked.connect(self._on_replace)
        self.set_item(None, Masking(), editable=False)

    def _build_text(self, layout: QVBoxLayout) -> None:
        """Add the scrolling text of the card: what changed, and why."""
        self._title = _wrapped_label()
        title_font = self._title.font()
        title_font.setBold(True)
        self._title.setFont(title_font)

        self._context = _wrapped_label()

        self._body = _wrapped_label()

        self._change = _column(self._title, self._context)
        self._why = _column(self._body)

        text = QWidget()
        self._columns = QBoxLayout(QBoxLayout.Direction.TopToBottom, text)
        self._columns.setContentsMargins(0, 0, 0, 0)
        self._columns.setSpacing(6)
        self._columns.addWidget(self._change, 1)
        self._columns.addWidget(self._why, 1)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(text)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.viewport().setAutoFillBackground(False)
        self._scroll.setMinimumHeight(self.fontMetrics().lineSpacing() * _CARD_MIN_LINES + 6)
        layout.addWidget(self._scroll, 1)

    def _build_buttons(self, layout: QVBoxLayout) -> None:
        """Add the row of buttons and the field for the words to write instead."""
        self.take_out = QPushButton(txt.TAKE_OUT)
        self.put_back = QPushButton(txt.PUT_BACK)
        self.use_suggestion = QPushButton(txt.USE_SUGGESTION)
        self.take_out_all = QPushButton(txt.TAKE_OUT_ALL)
        # The row keeps the width of *Take out all* while it is not shown, so the card is as wide
        # for a correction as for a spelling change: the splitter never moves under the pointer.
        wide = self.take_out_all.sizePolicy()
        wide.setRetainSizeWhenHidden(True)
        self.take_out_all.setSizePolicy(wide)
        self._buttons = (
            self.take_out,
            self.put_back,
            self.use_suggestion,
            self.take_out_all,
        )
        # The words to write instead: the card's own way of changing a correction, so the
        # teacher never leaves the view they are reading.
        self.replace = QLineEdit()
        self.replace.setPlaceholderText(txt.REPLACE_WITH)
        self.replace.setMinimumWidth(_REPLACE_WIDTH)
        self.replace_ok = QPushButton(txt.REPLACE_OK)
        self._button_row = QWidget()
        row = QHBoxLayout(self._button_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for button in self._buttons:
            row.addWidget(button)
        row.addWidget(self.replace, 1)
        row.addWidget(self.replace_ok)
        row.addStretch()
        # The row keeps its height while no button is shown, so the card never shrinks.
        self._button_row.setFixedHeight(
            max(widget.sizeHint().height() for widget in (*self._buttons, self.replace_ok))
        )
        layout.addWidget(self._button_row)

    def sizeHint(self) -> QSize:
        """Ask for five lines of text and the buttons, so the splitter opens the card that tall."""
        hint = super().sizeHint()
        return QSize(hint.width(), self._default_height())

    def _default_height(self) -> int:
        """Return the height of a card nobody dragged: five lines of text and its buttons."""
        return self.fontMetrics().lineSpacing() * _CARD_TEXT_LINES + 6

    def resizeEvent(self, event) -> None:
        """Put *Change* and *Why* side by side once the card is wide enough for both."""
        super().resizeEvent(event)
        direction = (
            QBoxLayout.Direction.LeftToRight
            if event.size().width() >= _SIDE_BY_SIDE_WIDTH
            else QBoxLayout.Direction.TopToBottom
        )
        if self._columns.direction() != direction:
            self._columns.setDirection(direction)

    def set_note(self, text: str) -> None:
        """Say *text* in place of the card's words, without any button."""
        for button in self._buttons:
            button.setVisible(False)
        self._hide_replace()
        self._title.clear()
        self._context.clear()
        self._body.setText(html.escape(text))
        self.setVisible(bool(text))

    def set_item(
        self,
        item: CheckItem | None,
        masking: Masking,
        *,
        editable: bool,
        reason: str = "",
        raw_plain: str = "",
        corrected_plain: str = "",
        minor_count: int = 0,
    ) -> None:
        """Show *item*, or nothing; *editable* says whether its buttons may be pressed.

        *raw_plain* is the transcription the context line is taken from, *corrected_plain* the
        text the replace field is filled from, and *minor_count* how many spelling and punctuation
        changes the document has, which the *Take out all* button names.
        """
        for button in self._buttons:
            button.setVisible(False)
            button.setEnabled(editable)
            button.setToolTip(reason)
        self._editable = editable
        self._hide_replace()
        if item is None:
            self._key = ""
            self._title.clear()
            self._context.clear()
            self._body.clear()
            self.setVisible(False)
            return
        self.setVisible(True)
        self._key = item.key
        self._title.setText(_title_html(item, masking))
        self._context.setText(_context_html(item, raw_plain, masking))
        self._context.setVisible(bool(self._context.text()))
        self._body.setText(_body_html(item, masking))
        self.take_out_all.setText(txt.take_out_all_label(minor_count))
        for button, tooltip in self._buttons_of(item, minor_count):
            button.setVisible(True)
            button.setToolTip(reason or tooltip)
        if item.kind in _REPLACEABLE:
            self._show_replace(masking.mask(_words_in_text(item, corrected_plain)), reason)

    def _show_replace(self, words: str, reason: str) -> None:
        """Offer the field that writes *words* instead, with the words the item has now."""
        self.replace.setText(words)
        self.replace.setToolTip(reason or txt.REPLACE_TOOLTIP)
        self.replace.setEnabled(self._editable)
        self.replace_ok.setToolTip(reason or txt.REPLACE_TOOLTIP)
        self.replace.setVisible(True)
        self.replace_ok.setVisible(True)
        self._refresh_replace_ok()

    def _hide_replace(self) -> None:
        """Take the replace field away, and forget what was typed into it."""
        self.replace.setVisible(False)
        self.replace_ok.setVisible(False)
        self.replace.clear()

    def _refresh_replace_ok(self) -> None:
        """Let OK work only while the field says something and the card may be acted on."""
        self.replace_ok.setEnabled(self._editable and bool(self.replace.text().strip()))

    def _on_replace(self) -> None:
        """Ask for the words of the card's item to be replaced by what the field says."""
        words = self.replace.text().strip()
        if self._editable and self._key and words:
            self.replace_requested.emit(self._key, words)

    def _buttons_of(self, item: CheckItem, minor_count: int) -> tuple[tuple[QPushButton, str], ...]:
        """Return the buttons *item* offers, each with its tooltip.

        A spelling or punctuation change of the model offers *Take out all* beside its own
        *Take out*, since that is where the teacher meets the run of them.
        """
        if item.kind is ItemKind.TAKEN_OUT:
            return ((self.put_back, txt.PUT_BACK_TOOLTIP),)
        if item.kind is ItemKind.UNCLEAR:
            return ((self.use_suggestion, txt.USE_SUGGESTION_TOOLTIP),)
        if item.kind is ItemKind.LEFT_OUT:
            return ()  # only the replace field: there is no correction to take out
        if is_minor_correction(item):
            return (
                (self.take_out, txt.TAKE_OUT_TOOLTIP),
                (self.take_out_all, txt.take_out_all_tooltip(minor_count)),
            )
        return ((self.take_out, txt.TAKE_OUT_TOOLTIP),)


class SidePanel(QWidget):
    """The card of the chosen item above the list of everything the text marks."""

    item_chosen = Signal(str)
    """A row was selected: the key of its item."""
    item_shown = Signal(str)
    """An item became the one the card shows: its key.

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
    replace_requested = Signal(str, str)
    """The teacher asked for the words of this item to be replaced by the words they typed."""
    card_split_changed = Signal(object)
    """The teacher dragged the card's height: the heights of the card and the list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: tuple[CheckItem, ...] = ()
        self._doc: CheckDocument | None = None
        self._masking = Masking()
        self._looked_at: frozenset[str] = frozenset()
        self._choosing = False
        self._editable = True
        self._reason = ""
        self._note = ""
        self._ticked: set[str] = set()
        self._unseen = False
        self._card_split: tuple[int, ...] = ()
        self._filter_boxes: dict[str, QCheckBox] = {}
        self._filter_row = QWidget()
        self._filter_layout = FlowLayout(self._filter_row, spacing=2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._tabs = QTabWidget()
        self._tabs.setTabBarAutoHide(True)
        self._tabs.addTab(self._build_corrections_page(), check_txt.TAB_CORRECTIONS)
        self._tabs.addTab(self._build_content_page(), check_txt.TAB_CONTENT)
        layout.addWidget(self._tabs, 1)

        self._card.take_out.clicked.connect(lambda: self._request(self.take_out_requested))
        self._card.put_back.clicked.connect(lambda: self._request(self.put_back_requested))
        self._card.use_suggestion.clicked.connect(
            lambda: self._request(self.use_suggestion_requested)
        )
        self._card.replace_requested.connect(self.replace_requested)
        self._card.take_out_all.clicked.connect(self.take_out_all_requested)
        self._next_button.clicked.connect(self.choose_next)

    def _build_corrections_page(self) -> QWidget:
        """Return the tab of the card and the list, with the counts and *Next* above them."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Above the card, whose height only the teacher changes: Next stays where the pointer is.
        self._counts = QLabel()
        self._counts.setWordWrap(True)
        self._counts.setStyleSheet("color: #888;")
        self._next_button = QPushButton(txt.ALL_LOOKED_AT)
        self._next_button.setMinimumWidth(self._next_button.sizeHint().width())
        self._next_button.setText(txt.NEXT)
        self._next_button.setToolTip(txt.NEXT_TOOLTIP)
        top = QHBoxLayout()
        top.setSpacing(4)
        top.addWidget(self._counts, 1)
        top.addWidget(self._next_button)
        layout.addLayout(top)

        self._card = _Card()
        self._list = QListWidget()
        self._list.setObjectName(ITEM_LIST)
        self._delegate = _ItemDelegate(self._list)
        self._list.setItemDelegate(self._delegate)
        self._list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list.setUniformItemSizes(True)
        self._list.setMinimumHeight(_ROW_HEIGHT * _LIST_MIN_ROWS)
        self._list.currentRowChanged.connect(self._on_row_changed)

        below = QWidget()
        below_layout = QVBoxLayout(below)
        below_layout.setContentsMargins(0, 0, 0, 0)
        below_layout.setSpacing(4)
        below_layout.addWidget(self._build_filter_row())
        below_layout.addWidget(self._list, 1)

        # The card's height is the teacher's: a splitter, so a long explanation is read whole.
        self._split = QSplitter(Qt.Orientation.Vertical)
        self._split.addWidget(self._card)
        self._split.addWidget(below)
        self._split.setStretchFactor(0, 0)
        self._split.setStretchFactor(1, 1)
        self._split.splitterMoved.connect(self._on_card_split_moved)
        layout.addWidget(self._split, 1)
        return page

    def _build_filter_row(self) -> QWidget:
        """Return the boxes that narrow the list: what is left, each category, and each kind."""
        self._add_filter_box(
            FILTER_UNSEEN,
            check_txt.FILTER_NOT_LOOKED_AT,
            check_txt.FILTER_NOT_LOOKED_AT_TIP,
        )
        for category in CHANGE_CATEGORIES:
            self._add_filter_box(
                category,
                txt.category_label(category),
                check_txt.filter_category_tip(txt.category_label(category)),
            )
        self._add_filter_box(FILTER_UNCLEAR, check_txt.FILTER_UNCLEAR, check_txt.FILTER_UNCLEAR_TIP)
        self._add_filter_box(
            FILTER_LEFT_OUT, check_txt.FILTER_LEFT_OUT, check_txt.FILTER_LEFT_OUT_TIP
        )
        return self._filter_row

    def _add_filter_box(self, key: str, label: str, tip: str) -> None:
        """Add one box to the filter row; *key* is what it filters by."""
        box = QCheckBox(label)
        box.setToolTip(tip)
        box.toggled.connect(lambda checked, k=key: self.set_filter(k, checked))
        self._filter_boxes[key] = box
        self._filter_layout.addWidget(box)

    def _build_content_page(self) -> QWidget:
        """Return the tab that shows the content review, read-only."""
        self._content_view = QTextBrowser()
        self._content_view.setOpenExternalLinks(False)
        return self._content_view

    # ── The card's height ─────────────────────────────────────────────────

    def set_card_split(self, sizes: Sequence[int]) -> None:
        """Give the card and the list the heights the teacher dragged them to last time."""
        self._card_split = tuple(sizes)
        self._apply_card_split()

    @property
    def card_split(self) -> tuple[int, ...]:
        """The heights of the card and the list, as the splitter has them now."""
        return tuple(self._split.sizes())

    def _on_card_split_moved(self, _position: int, _index: int) -> None:
        """Remember the heights the teacher dragged the card and the list to."""
        sizes = self.card_split
        if all(size > 0 for size in sizes):
            self._card_split = sizes
            self.card_split_changed.emit(sizes)

    def _apply_card_split(self) -> None:
        """Give the card the height the teacher chose; without one it keeps its five lines."""
        if self._card_split and not self._card.isHidden():
            self._split.setSizes(list(self._card_split))

    # ── What the page pushes ──────────────────────────────────────────────

    def set_document(self, doc: CheckDocument, masking: Masking) -> None:
        """Show the items of *doc*: the first one not looked at, or the chosen one where it is.

        Another document opens at the first correction the teacher has not looked at yet, so
        coming back to an essay continues where they stopped, and with every row shown again.
        The same document keeps the chosen row and the filter: a stored edit must not move the
        teacher's place.
        """
        same = self._doc is not None and self._doc.source_id == doc.source_id
        chosen = self.chosen
        row = self._list.currentRow()
        self._doc = doc
        self._masking = masking
        self._looked_at = doc.work.looked_at
        self._rows = doc.items
        if not same or not self._filter_has_items():
            self._untick_filters()
        self._list.blockSignals(True)
        self._list.clear()
        for entry in self._rows:
            item = QListWidgetItem()
            item.setData(_ROW_ROLE, entry)
            item.setSizeHint(QSize(0, _ROW_HEIGHT))
            self._list.addItem(item)
        self._list.blockSignals(False)
        self._delegate.looked_at = doc.work.looked_at
        self._refresh_filters()
        self._counts.setText(self._counts_text())
        self._set_content(doc.content_review)
        if same:
            self._select(chosen, row)
        else:
            self._select(first_unseen(self._walk_keys(), self._looked_at), 0)
        self._refresh_next()

    def choose_next(self) -> None:
        """Choose the next row the teacher has not looked at yet, from the top at the end."""
        key = next_unseen(self._walk_keys(), self._looked_at, self.chosen)
        if key:
            self.set_chosen(key)

    def set_chosen(self, key: str) -> None:
        """Select the row of *key*, if the list has it, without telling the page back.

        A row the filters hide stays shown while it is the chosen one: the card and the list
        must name the same row.
        """
        index = self._row_index(key)
        if index is None:
            return
        self._set_row_silently(index)
        self._apply_row_visibility(keep_chosen=True)

    def set_filter(self, key: str, ticked: bool = True) -> None:
        """Tick or untick one filter; with nothing ticked the list shows every row.

        The walk of *Next* follows the filters, so it stays inside what the teacher is looking at;
        the counts above the list keep covering the whole document.
        """
        box = self._filter_boxes.get(key)
        if box is None:
            return
        box.blockSignals(True)
        box.setChecked(ticked)
        box.blockSignals(False)
        if key == FILTER_UNSEEN:
            self._unseen = ticked
        elif ticked:
            self._ticked.add(key)
        else:
            self._ticked.discard(key)
        self._choose_first_visible()
        self._apply_row_visibility()
        self._refresh_next()

    def show_content(self) -> None:
        """Put the content review in front, where the document has one."""
        index = self._tabs.indexOf(self._content_view)
        if self._tabs.isTabVisible(index):
            self._tabs.setCurrentIndex(index)

    def show_corrections(self) -> None:
        """Put the list of corrections in front."""
        self._tabs.setCurrentIndex(0)

    def set_note(self, text: str) -> None:
        """Say something in place of the card, e.g. that the marks could not be placed."""
        self._note = text
        self._show_item(self._current_item())

    def set_editable(self, editable: bool, *, reason: str = "") -> None:
        """Allow or refuse the card's actions; *reason* is the tooltip that says why not."""
        self._editable = editable
        self._reason = reason
        self._show_item(self._current_item())

    def _show_item(self, item: CheckItem | None) -> None:
        """Show *item* on the card, and give the card its height back when it comes back."""
        was_hidden = self._card.isHidden()
        self._card.set_item(
            item,
            self._masking,
            editable=self._editable,
            reason=self._reason,
            raw_plain=self._raw_plain(),
            corrected_plain=self._corrected_plain(),
            minor_count=len(minor_corrections(self._rows)),
        )
        if self._note:
            self._card.set_note(self._note)
        if was_hidden and not self._card.isHidden():
            self._apply_card_split()

    def set_looked_at(self, looked_at: frozenset[str], progress: tuple[int, int]) -> None:
        """Show which items were looked at, without rebuilding the list or moving the choice.

        A filter that hides what was looked at takes the rows away as the teacher works through
        them, but never the row they are on: that one must not vanish under their pointer.
        """
        self._looked_at = looked_at
        self._delegate.looked_at = looked_at
        self._list.viewport().update()
        if self._doc is not None:
            self._doc = replace(self._doc, looked_at_progress=progress)
        self._apply_row_visibility(keep_chosen=True)
        self._counts.setText(self._counts_text())
        self._refresh_filters()
        self._refresh_next()

    def clear(self) -> None:
        """Empty the panel: no document is open."""
        self._rows = ()
        self._doc = None
        self._looked_at = frozenset()
        self._list.clear()
        self._counts.clear()
        self._set_content("")
        self._refresh_filters()
        self._show_item(None)
        self._refresh_next()

    @property
    def chosen(self) -> str:
        """The key of the chosen item, or ``""`` for no choice."""
        item = self._current_item()
        return item.key if item is not None else ""

    @property
    def row_count(self) -> int:
        """How many rows the list has."""
        return len(self._rows)

    @property
    def ticked(self) -> frozenset[str]:
        """The filters the teacher ticked; with none ticked the list shows every row."""
        return frozenset(self._ticked)

    @property
    def unseen_only(self) -> bool:
        """Whether *Not looked at* is ticked, which narrows whatever else is ticked."""
        return self._unseen

    # ── The list ──────────────────────────────────────────────────────────

    def _raw_plain(self) -> str:
        """Return the plain transcription the card's context line is taken from."""
        return self._doc.raw_plain if self._doc is not None else ""

    def _corrected_plain(self) -> str:
        """Return the plain corrected text the card's replace field is filled from."""
        return self._doc.corrected_plain if self._doc is not None else ""

    def _current_item(self) -> CheckItem | None:
        """Return the item the list has selected, or ``None``."""
        index = self._list.currentRow()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _on_row_changed(self, index: int) -> None:
        """Show the card of the selected row, and tell the page which item it is."""
        item = self._rows[index] if 0 <= index < len(self._rows) else None
        self._show_item(item)
        self._refresh_next()
        if item is None:
            return
        self.item_shown.emit(item.key)
        if not self._choosing:
            self.item_chosen.emit(item.key)

    def _walk_keys(self) -> tuple[str, ...]:
        """Return the keys of the rows *Next* walks over, in list order.

        These are the rows the boxes show that count for looked-at: the progress of a document is
        measured in those, so *Next* never stops on a change of the teacher's own.
        """
        return tuple(
            item.key
            for item in self._rows
            if self._row_visible(item) and counts_for_looked_at(item)
        )

    def _refresh_next(self) -> None:
        """Let *Next* walk to the next row not looked at, and say when there is none left."""
        left = bool(next_unseen(self._walk_keys(), self._looked_at, self.chosen))
        self._next_button.setEnabled(left)
        self._next_button.setText(txt.NEXT if left else txt.ALL_LOOKED_AT)

    def _select(self, chosen: str, row: int) -> None:
        """Select *chosen*, or the item nearest to *row* when the item it was is gone.

        The list was rebuilt, not clicked: the page is not told, because it would scroll to the
        item and select its words in a text the teacher may be typing in.
        """
        if chosen and self._row_index(chosen) is not None:
            self.set_chosen(chosen)
            return
        index = self._nearest_shown(max(row, 0))
        if index is None:
            self._show_item(None)
            return
        self._set_row_silently(index)

    def _row_index(self, key: str) -> int | None:
        """Return the index of the row of *key*, or ``None`` when the list has no such row."""
        return next((index for index, item in enumerate(self._rows) if item.key == key), None)

    def _set_row_silently(self, index: int) -> None:
        """Select the row at *index* without telling the page."""
        self._choosing = True
        try:
            self._list.setCurrentRow(index)
        finally:
            self._choosing = False

    def _nearest_shown(self, row: int) -> int | None:
        """Return the first shown row at or after *row*, else the last one before it."""
        indices = [
            index for index in range(len(self._rows)) if not self._list.item(index).isHidden()
        ]
        if not indices:
            return None
        after = [index for index in indices if index >= row]
        return after[0] if after else indices[-1]

    def _request(self, signal: SignalInstance) -> None:
        """Ask the page for the action of the chosen item."""
        key = self.chosen
        if key:
            signal.emit(key)

    # ── The filter ────────────────────────────────────────────────────────

    def _row_visible(self, item: CheckItem) -> bool:
        """Whether the boxes show *item*: its category or kind is ticked, and it is not looked at.

        Nothing ticked shows every row.  *Not looked at* narrows whatever else is ticked, and a
        row that does not count for looked-at — a change of the teacher's own — is never one of
        those.
        """
        if self._ticked and _filter_key(item) not in self._ticked:
            return False
        if not self._unseen:
            return True
        return counts_for_looked_at(item) and item.key not in self._looked_at

    def _filter_has_items(self) -> bool:
        """Whether the ticked boxes still have something to show in this document."""
        if not self._ticked:
            return True
        return any(_filter_key(item) in self._ticked for item in self._doc_items())

    def _doc_items(self) -> tuple[CheckItem, ...]:
        """Return the items of the open document."""
        return self._doc.items if self._doc is not None else ()

    def _apply_row_visibility(self, *, keep_chosen: bool = False) -> None:
        """Show the rows the boxes let through, and hide the others."""
        chosen = self._list.currentRow()
        for index, item in enumerate(self._rows):
            hidden = not self._row_visible(item) and not (keep_chosen and index == chosen)
            self._list.item(index).setHidden(hidden)
        self._list.viewport().update()

    def _choose_first_visible(self) -> None:
        """Choose the first row the boxes show when the chosen one is hidden."""
        index = self._list.currentRow()
        if 0 <= index < len(self._rows) and self._row_visible(self._rows[index]):
            return
        first = next(
            (index for index, item in enumerate(self._rows) if self._row_visible(item)),
            None,
        )
        if first is None:
            self._show_item(None)
            return
        self._set_row_silently(first)

    def _refresh_filters(self) -> None:
        """Write the count on every box, and hide the ones this document has nothing for."""
        counts = Counter(_filter_key(item) for item in self._doc_items())
        left = sum(
            1
            for item in self._rows
            if counts_for_looked_at(item) and item.key not in self._looked_at
        )
        self._filter_boxes[FILTER_UNSEEN].setText(
            check_txt.filter_label(check_txt.FILTER_NOT_LOOKED_AT, left)
        )
        for category in CHANGE_CATEGORIES:
            self._set_filter_count(category, counts.get(category, 0), txt.category_label(category))
        self._set_filter_count(
            FILTER_UNCLEAR, counts.get(FILTER_UNCLEAR, 0), check_txt.FILTER_UNCLEAR
        )
        self._set_filter_count(
            FILTER_LEFT_OUT, counts.get(FILTER_LEFT_OUT, 0), check_txt.FILTER_LEFT_OUT
        )

    def _set_filter_count(self, key: str, count: int, label: str) -> None:
        """Write *count* on the box of *key*, and hide it when the document has none."""
        box = self._filter_boxes[key]
        box.setText(check_txt.filter_label(label, count))
        box.setVisible(bool(count))

    def _untick_filters(self) -> None:
        """Untick every box, so the list shows the whole document again."""
        self._ticked.clear()
        self._unseen = False
        for box in self._filter_boxes.values():
            box.blockSignals(True)
            box.setChecked(False)
            box.blockSignals(False)

    # ── The footer and the content review ─────────────────────────────────

    def _counts_text(self) -> str:
        """Return the footer line: the corrections, the unclear passages, and how far it is read."""
        doc = self._doc
        if doc is None:
            return ""
        unclear = sum(1 for item in doc.items if item.kind is ItemKind.UNCLEAR)
        left_out = sum(1 for item in doc.items if item.kind is ItemKind.LEFT_OUT)
        looked_at, countable = doc.looked_at_progress
        parts = [
            txt.item_counts(len(doc.items) - unclear - left_out, unclear, left_out),
            txt.looked_at_progress_text(looked_at, countable),
        ]
        return " \u00b7 ".join(part for part in parts if part)

    def _set_content(self, content_review: str) -> None:
        """Show the content review in its tab, or take the tab away when there is none."""
        text = content_review.strip()
        index = self._tabs.indexOf(self._content_view)
        self._tabs.setTabVisible(index, bool(text))
        if not text:
            self._content_view.clear()
            if self._tabs.currentIndex() == index:
                self._tabs.setCurrentIndex(0)
            return
        self._content_view.setMarkdown(content_review)
