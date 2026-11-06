"""Annotated word diff and notes pane for the proofreading review.

:class:`ReviewDiffView` is the local word diff of the raw against the corrected text with a
link on the words of every change and of every unclear expression.  Clicking such words asks
the notes pane to show their note; clicking a note in the pane scrolls the diff to its span and
highlights it.  The highlight is a Qt "extra selection", not part of the HTML, so choosing a
note neither re-renders the text nor loses the scroll position.

:class:`ReviewNotesView` lists the explanations and the unclear expressions, filtered to
*All*, *Changes* or *Unclear*.  Both views render the
:class:`~teachers_teammate.domain.correction_review.AlignedReview` the application layer
computed, so alignment is recomputed from the stored texts on every render and teacher edits
need no extra state.

Note that only the diff is annotated: the stored corrected text never carries a marker, the
links and the numbers exist in these views alone.  All wording comes from :mod:`._user_text`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial
import html

from PySide6.QtCore import QEvent, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QSizePolicy,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from ..domain.correction_review import (
    AlignedChange,
    AlignedReview,
    AlignedUnclear,
    ChangeNote,
    SentenceNote,
    UnclearNote,
    diff_opcodes,
    tokenize,
)
from . import _user_text as txt

FILTER_ALL = "all"
FILTER_CHANGES = "changes"
FILTER_UNCLEAR = "unclear"

_OLD_BACKGROUND = "#ffd7d7"
_NEW_BACKGROUND = "#d7ffd7"

# The chosen change or unclear expression: a strong colour on its words, drawn on top of the
# diff colours (see ReviewDiffView._apply_focus).
_FOCUS_CHANGE_BACKGROUND = "#ffe066"
_FOCUS_UNCLEAR_BACKGROUND = "#ffb347"
_FOCUS_TEXT = "#222222"

# Every highlight sets its text colour too: the app runs on a dark theme, whose light text is
# unreadable on these light backgrounds.
_UNCLEAR_BACKGROUND = "#ffe3b3"
_UNCLEAR_TEXT = "#5a3200"
_NOTE_FOCUS_STYLE = "background:#fff4c2;color:#222222;"

_STYLES = """
<style>
  .cat { color: #7f8c8d; font-style: italic; }
  .muted { color: #888; font-style: italic; }
  .heading { color: #555; font-weight: bold; }
  .note { margin-bottom: 8px; }
</style>
"""


def _marker(kind: str, number: int, label: str) -> str:
    """Return a superscript link to ``<kind>:<number>`` (for a span with no words to link)."""
    target = f"{kind}:{number}"
    return f"<sup><a name='{target}' href='{target}'>{html.escape(label)}</a></sup>"


def _change_tooltip(change: AlignedChange | None) -> str:
    """Return the hover text of a change marker: category and explanation, or why none."""
    if change is None:
        return ""
    if change.note is None:
        return txt.YOUR_CHANGE if change.by_teacher else txt.NO_EXPLANATION
    explanation = change.note.explanation or txt.NO_EXPLANATION
    return f"{txt.category_label(change.note.category)}: {explanation}"


def _unclear_tooltip(item: AlignedUnclear | None) -> str:
    """Return the hover text of an unclear marker: why it is unclear."""
    return item.note.why if item is not None else ""


def _token_offsets(tokens: Sequence[str]) -> list[int]:
    """Return the character offset of every token, plus the end of the last one."""
    offsets: list[int] = []
    position = 0
    for token in tokens:
        offsets.append(position)
        position += len(token)
    offsets.append(position)
    return offsets


def _annotation(raw: str, aligned: AlignedReview | None) -> list[int | None]:
    """Return the unclear-expression number of every character of *raw* (``None`` = clear)."""
    annotation: list[int | None] = [None] * len(raw)
    for item in aligned.unclear if aligned is not None else ():
        if item.start is None or item.end is None:
            continue
        for index in range(max(0, item.start), min(len(raw), item.end)):
            annotation[index] = item.number
    return annotation


def _span_ends(aligned: AlignedReview | None) -> dict[int, int]:
    """Return ``{offset: unclear number}`` for where each anchored unclear span ends."""
    if aligned is None:
        return {}
    return {
        item.end: item.number
        for item in aligned.unclear
        if item.start is not None and item.end is not None
    }


class _Links:
    """Builds the links of the diff; the first link to a note is also its scroll target.

    Qt keeps an ``<a>`` that carries both ``name`` and ``href`` as a link on its first
    character only, so the name goes on a one-character link and the rest is a second link
    to the same note.  The style sits on the ``<a>`` itself for the same reason: an ``<a>``
    around a styled ``<span>`` loses its link after the first character too.
    """

    def __init__(self) -> None:
        self._named: set[str] = set()

    def link(self, text: str, target: str, style: str, css_class: str = "") -> str:
        """Return *text* as a link to *target* drawn in *style*."""
        if not text:
            return ""
        attributes = f" href='{target}'"
        attributes += f" class='{css_class}'" if css_class else ""
        attributes += f" style='{style}'"
        if target in self._named:
            return f"<a{attributes}>{html.escape(text)}</a>"
        self._named.add(target)
        first = f"<a name='{target}'{attributes}>{html.escape(text[0])}</a>"
        rest = f"<a{attributes}>{html.escape(text[1:])}</a>" if len(text) > 1 else ""
        return first + rest

    def has_target(self, target: str) -> bool:
        """Whether a link to *target* has been written (and named) already."""
        return target in self._named


class _RawWriter:
    """Writes ranges of the raw text as HTML, linking unclear expressions to their notes."""

    def __init__(
        self,
        text: str,
        annotation: Sequence[int | None],
        end_at: dict[int, int],
        links: _Links,
    ) -> None:
        self._text = text
        self._annotation = annotation
        self._end_at = end_at
        self._links = links
        self._closed: set[int] = set()

    def emit(
        self,
        parts: list[str],
        start: int,
        end: int,
        wrap: Callable[[str], str] | None = None,
    ) -> None:
        """Append ``text[start:end]`` to *parts*, as HTML made by *wrap* (``None`` = plain).

        Unclear words outside a change become a link to their note.  Inside a change (*wrap*
        given) the change's own link and colours win, as two links cannot be nested.
        """
        position = start
        while position < end:
            self._close_span(parts, position)
            number = self._annotation[position]
            stop = position + 1
            while stop < end and self._annotation[stop] == number and stop not in self._end_at:
                stop += 1
            chunk = self._text[position:stop]
            if wrap is not None:
                parts.append(wrap(chunk))
            elif number is None:
                parts.append(html.escape(chunk))
            else:
                parts.append(self._unclear_link(number, chunk))
            position = stop
        self._close_span(parts, end)

    def _unclear_link(self, number: int, chunk: str) -> str:
        """Return *chunk* of unclear expression *number* as a highlighted link to its note."""
        style = f"background:{_UNCLEAR_BACKGROUND};color:{_UNCLEAR_TEXT};"
        return self._links.link(chunk, f"unclear:{number}", style, "unclear")

    def _close_span(self, parts: list[str], position: int) -> None:
        """Append a ``?`` link after an unclear span none of whose words could be linked.

        That is the case only when a change covers the whole expression.
        """
        number = self._end_at.get(position)
        if number is None or number in self._closed:
            return
        self._closed.add(number)
        if not self._links.has_target(f"unclear:{number}"):
            parts.append(_marker("unclear", number, "?"))


class ReviewDiffView(QTextBrowser):
    """Word diff of the raw against the corrected text, annotated with note numbers."""

    change_clicked = Signal(int)
    unclear_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # Linked words look like the rest of the diff; an inline style cannot drop the underline.
        self.document().setDefaultStyleSheet("a { text-decoration: none; }")
        self._font_pt = 9
        self._raw = ""
        self._corrected = ""
        self._aligned: AlignedReview | None = None
        self._focus_change: int | None = None
        self._focus_unclear: int | None = None
        self._last_key: tuple[object, ...] | None = None
        self._apply_font()
        self.anchorClicked.connect(self._on_anchor_clicked)

    # ── hover text ────────────────────────────────────────────────────────────

    def tooltip_for(self, target: str) -> str:
        """Return the hover text of the marker linking to *target* (``note:3``, ``unclear:1``).

        It is shown by :meth:`viewportEvent` rather than through an HTML ``title`` attribute,
        because Qt does not decode entities in attribute values: quotes in an explanation would
        show up as ``&quot;``.
        """
        kind, _, number = target.partition(":")
        if self._aligned is None or not number.isdigit():
            return ""
        index = int(number) - 1
        if kind == "note" and 0 <= index < len(self._aligned.changes):
            return _change_tooltip(self._aligned.changes[index])
        if kind == "unclear" and 0 <= index < len(self._aligned.unclear):
            return _unclear_tooltip(self._aligned.unclear[index])
        return ""

    def viewportEvent(self, event: QEvent) -> bool:
        """Show the explanation of the marker under the mouse as a tooltip."""
        if event.type() == QEvent.Type.ToolTip:
            tip = self.tooltip_for(self.anchorAt(event.pos()))  # ty: ignore[unresolved-attribute]  # QHelpEvent
            if tip:
                QToolTip.showText(event.globalPos(), f"<p>{html.escape(tip)}</p>", self)  # ty: ignore[unresolved-attribute]  # QHelpEvent
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)

    # ── content ───────────────────────────────────────────────────────────────

    def set_content(
        self,
        raw: str,
        corrected: str,
        aligned: AlignedReview | None,
        *,
        font_pt: int | None = None,
    ) -> None:
        """Show *raw* against *corrected*, annotated with *aligned* (``None`` = plain diff).

        A plain diff without numbers is what a file corrected in *Corrections only* mode or
        restored from an older cache record shows.
        """
        self._raw = raw
        self._corrected = corrected
        self._aligned = aligned
        self._focus_change = None
        self._focus_unclear = None
        self.setExtraSelections([])
        if font_pt is not None and font_pt != self._font_pt:
            self._font_pt = font_pt
            self._apply_font()
            self._last_key = None
        self._render()

    def clear_content(self) -> None:
        """Forget the shown texts and empty the view."""
        self._raw = ""
        self._corrected = ""
        self._aligned = None
        self._focus_change = None
        self._focus_unclear = None
        self._last_key = None
        self.clear()

    def set_font_point_size(self, point_size: int) -> None:
        """Apply the panel's text-zoom size and re-render at it."""
        if point_size == self._font_pt:
            return
        self._font_pt = point_size
        self._apply_font()
        self._last_key = None
        self._render()

    def focus_change(self, number: int) -> None:
        """Highlight the hunk of change *number* and scroll to it."""
        self._focus_change = number
        self._focus_unclear = None
        self._apply_focus()

    def focus_unclear(self, number: int) -> None:
        """Highlight unclear expression *number* and scroll to it."""
        self._focus_unclear = number
        self._focus_change = None
        self._apply_focus()

    def _apply_focus(self) -> None:
        """Mark the words of the focused note with an extra selection and bring them into view."""
        if self._focus_change is not None:
            target, background = f"note:{self._focus_change}", _FOCUS_CHANGE_BACKGROUND
        elif self._focus_unclear is not None:
            target, background = f"unclear:{self._focus_unclear}", _FOCUS_UNCLEAR_BACKGROUND
        else:
            self.setExtraSelections([])
            return
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(background))
        fmt.setForeground(QColor(_FOCUS_TEXT))
        fmt.setFontWeight(QFont.Weight.Bold)
        selections: list[QTextEdit.ExtraSelection] = []
        for start, length in self._link_ranges(target):
            cursor = QTextCursor(self.document())
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.MoveMode.KeepAnchor)
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.setExtraSelections(selections)
        if selections:
            self._reveal(selections[0].cursor)  # ty: ignore[unresolved-attribute]  # PySide6

    def _link_ranges(self, target: str) -> list[tuple[int, int]]:
        """Return ``(position, length)`` of every run of text that links to *target*."""
        ranges: list[tuple[int, int]] = []
        block = self.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().anchorHref() == target:
                    ranges.append((fragment.position(), fragment.length()))
                iterator += 1  # ty: ignore[unsupported-operator]  # QTextBlock.iterator
            block = block.next()
        return ranges

    def _reveal(self, cursor: QTextCursor) -> None:
        """Scroll so that the line of *cursor* is in the middle of the view if it is not visible."""
        rect = self.cursorRect(cursor)
        height = self.viewport().height()
        if 0 <= rect.top() and rect.bottom() <= height:
            return
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + rect.center().y() - height // 2)

    # ── rendering ─────────────────────────────────────────────────────────────

    def _apply_font(self) -> None:
        font = QFont()
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(self._font_pt)
        self.setFont(font)

    def _on_anchor_clicked(self, url: QUrl) -> None:
        kind, number = _split_target(url)
        if not number.isdigit():
            return
        if kind == "note":
            self.focus_change(int(number))  # the clicked words show that they were chosen
            self.change_clicked.emit(int(number))
        elif kind == "unclear":
            self.focus_unclear(int(number))
            self.unclear_clicked.emit(int(number))

    def _render(self) -> None:
        key = (self._raw, self._corrected, self._font_pt, id(self._aligned))
        if key == self._last_key:
            return
        self._last_key = key
        self.setHtml(self._html())
        self._apply_focus()  # setHtml drops the selections; a zoom keeps the chosen note

    def _html(self) -> str:
        if not self._raw:
            return _message_page(txt.NO_OCR_TEXT, self._font_pt)
        if not self._corrected:
            return _message_page(txt.NO_CORRECTION_TEXT, self._font_pt)
        body = "".join(self._body())
        return (
            f"<html><head>{_STYLES}</head><body>"
            f"<pre style='white-space: pre-wrap; font-family: monospace; "
            f"font-size: {self._font_pt}pt;'>{body}</pre></body></html>"
        )

    def _body(self) -> list[str]:
        raw_tokens = tokenize(self._raw)
        corrected_tokens = tokenize(self._corrected)
        links = _Links()
        writer = _RawWriter(
            self._raw,
            _annotation(self._raw, self._aligned),
            _span_ends(self._aligned),
            links,
        )
        offsets = _token_offsets(raw_tokens)
        parts: list[str] = []
        number = 0
        for tag, i1, i2, j1, j2 in diff_opcodes(raw_tokens, corrected_tokens):
            if tag == "equal":
                writer.emit(parts, offsets[i1], offsets[i2])
                continue
            number += 1
            old = partial(self._changed_words, links, number, deleted=True)
            writer.emit(parts, offsets[i1], offsets[i2], old)
            parts.append(self._changed_words(links, number, "".join(corrected_tokens[j1:j2])))
        return parts

    def _changed_words(
        self, links: _Links, number: int, text: str, *, deleted: bool = False
    ) -> str:
        """Return the old (*deleted*) or new *text* of change *number* as HTML.

        With a review the words themselves are the link to the change's note; without one
        (a plain diff) they are only coloured.
        """
        if not text:
            return ""
        if deleted:
            style = f"background:{_OLD_BACKGROUND};text-decoration:line-through;color:#c0392b;"
        else:
            style = f"background:{_NEW_BACKGROUND};color:#1e8449;"
        if self._aligned is None:
            return f"<span style='{style}'>{html.escape(text)}</span>"
        return links.link(text, f"note:{number}", style)


# ── notes pane ────────────────────────────────────────────────────────────────


def _split_target(url: QUrl) -> tuple[str, str]:
    """Split an ``anchorClicked`` URL such as ``note:3`` into kind and number."""
    kind, _, number = url.toString().partition(":")
    return kind, number


def _message_page(text: str, font_pt: int) -> str:
    """Return a one-paragraph page showing *text*."""
    return (
        f"<html><body><p style='font-size: {font_pt}pt; color: #888;'>"
        f"{html.escape(text)}</p></body></html>"
    )


def _change_entry(change: AlignedChange, shown: int | None = None) -> str:
    """Return the HTML block of one change: number, ``old → new``, category, explanation.

    *shown* is the running number in the list; the link keeps the number of the hunk in the
    diff, which is what the two views use to find each other.  A list without the minor
    changes therefore has no gaps in its numbers.
    """
    note = change.note
    if note is None:
        label = txt.YOUR_CHANGE if change.by_teacher else txt.NO_EXPLANATION
        detail = f"<span class='muted'>{html.escape(label)}</span>"
    else:
        category = html.escape(txt.category_label(note.category))
        explanation = html.escape(note.explanation) or html.escape(txt.NO_EXPLANATION)
        detail = f"<span class='cat'>{category}</span><br>{explanation}"
    return (
        f"<div class='note'>"
        f"<a name='note:{change.number}' href='note:{change.number}'>"
        f"{shown if shown is not None else change.number}</a> "
        f"<b>{html.escape(change.hunk.summary)}</b><br>{detail}</div>"
    )


def _unclear_entry(item: AlignedUnclear) -> str:
    """Return the HTML block of one unclear expression: why, then the recommendation."""
    note = item.note
    return (
        f"<div class='note'>"
        f"<a name='unclear:{item.number}' href='unclear:{item.number}'>?</a> "
        f"<b>{html.escape(note.expression)}</b><br>"
        f"{html.escape(txt.WHY_PREFIX)} {html.escape(note.why)}<br>"
        f"{html.escape(txt.recommendation_caption(note.is_suggestion))} "
        f"{html.escape(note.recommendation)}{_suggestion_note(note)}</div>"
    )


def _suggestion_note(note: UnclearNote) -> str:
    """Return the ``(meaning not certain)`` hint of a suggestion, or ``""``."""
    suffix = txt.suggestion_note(note.is_suggestion)
    return f" <span class='muted'>({html.escape(suffix)})</span>" if suffix else ""


def _orphan_entry(note: ChangeNote) -> str:
    """Return the greyed-out HTML block of a note whose edit is not in the text."""
    summary = f"{note.original} → {note.corrected}"
    category = txt.category_label(note.category)
    return (
        f"<div class='note'><span class='muted'>{html.escape(summary)} · "
        f"{html.escape(category)} · {html.escape(txt.NOT_IN_TEXT)}</span></div>"
    )


def _unplaced_entry(sentence: SentenceNote) -> str:
    """Return the greyed-out HTML block of a sentence that could not be placed."""
    summary = f"{sentence.original} → {sentence.corrected}"
    return f"<div class='note'><span class='muted'>{html.escape(summary)}</span></div>"


def _ordered_items(aligned: AlignedReview) -> list[AlignedChange | AlignedUnclear]:
    """Return the listed changes and unclear expressions in text order, unanchored items last."""
    last = max((change.position for change in aligned.changes), default=0)
    keyed: list[tuple[int, AlignedChange | AlignedUnclear]] = [
        (change.position, change) for change in aligned.listed_changes
    ]
    keyed.extend(
        (item.start if item.start is not None else last + 1, item) for item in aligned.unclear
    )
    keyed.sort(key=lambda entry: entry[0])
    return [item for _position, item in keyed]


def _unclear_table(items: Sequence[AlignedUnclear], focus: tuple[str, int] | None) -> str:
    """Return the three-column table of unclear expressions (the *Unclear* filter)."""
    header = "".join(
        f"<th align='left'>{html.escape(title)}</th>"
        for title in (txt.COLUMN_EXPRESSION, txt.COLUMN_WHY, txt.COLUMN_RECOMMENDED)
    )
    rows: list[str] = []
    for item in items:
        note = item.note
        focused = focus == ("unclear", item.number)
        background = f" style='{_NOTE_FOCUS_STYLE}'" if focused else ""
        rows.append(
            f"<tr><td{background}><a name='unclear:{item.number}' "
            f"href='unclear:{item.number}'>?</a> <b>{html.escape(note.expression)}</b></td>"
            f"<td{background}>{html.escape(note.why)}</td>"
            f"<td{background}>{html.escape(txt.recommendation_caption(note.is_suggestion))} "
            f"{html.escape(note.recommendation)}{_suggestion_note(note)}</td></tr>"
        )
    return (
        "<table border='1' cellspacing='0' cellpadding='4' width='100%'>"
        f"<tr>{header}</tr>{''.join(rows)}</table>"
    )


def _minor_entry(change: AlignedChange) -> str:
    """Return the short block of a spelling or punctuation change: ``old → new`` and category."""
    category = html.escape(txt.category_label(change.category))
    return (
        f"<div class='note'><a name='note:{change.number}' href='note:{change.number}'>"
        f"{html.escape(change.hunk.summary)}</a> <span class='cat'>{category}</span></div>"
    )


def _minor_section(aligned: AlignedReview, *, expanded: bool) -> str:
    """Return the collapsible section that holds the spelling and punctuation changes."""
    toggle = (
        f"<p class='heading'><a href='toggle:minor'>"
        f"{html.escape(txt.minor_toggle(aligned.minor_count, expanded=expanded))}</a></p>"
    )
    if not expanded:
        return toggle
    return toggle + "".join(_minor_entry(change) for change in aligned.minor_changes)


class ReviewNotesView(QWidget):
    """Segmented filter over a pane listing the change explanations and unclear expressions."""

    change_selected = Signal(int)
    unclear_selected = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._aligned: AlignedReview | None = None
        self._filter = FILTER_ALL
        self._font_pt = 9
        self._show_trailing = False
        self._show_minor = False
        self._last_key: tuple[object, ...] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        filter_bar = QWidget()
        filter_layout = QHBoxLayout(filter_bar)
        filter_layout.setContentsMargins(4, 2, 4, 2)
        filter_layout.setSpacing(4)
        self._buttons = QButtonGroup(self)
        self._buttons.setExclusive(True)
        self._all_btn = self._make_button(txt.FILTER_ALL, FILTER_ALL)
        self._changes_btn = self._make_button(txt.FILTER_CHANGES, FILTER_CHANGES)
        self._unclear_btn = self._make_button(txt.FILTER_UNCLEAR, FILTER_UNCLEAR)
        for button in (self._all_btn, self._changes_btn, self._unclear_btn):
            filter_layout.addWidget(button)
        filter_layout.addStretch()
        self._all_btn.setChecked(True)

        self._browser = QTextBrowser()
        self._browser.setOpenLinks(False)
        self._browser.setOpenExternalLinks(False)
        self._browser.setObjectName("review_notes_browser")
        # The numbers are jump markers, not web links: no underline.
        self._browser.document().setDefaultStyleSheet("a { text-decoration: none; }")
        self._browser.anchorClicked.connect(self._on_anchor_clicked)
        self._apply_font()

        layout.addWidget(filter_bar)
        layout.addWidget(self._browser, stretch=1)

    def _make_button(self, label: str, mode: str) -> QToolButton:
        """Return one filter button; clicking it switches to *mode*."""
        button = QToolButton()
        button.setText(label)
        button.setCheckable(True)
        button.clicked.connect(lambda _checked=False, m=mode: self.set_filter(m))
        self._buttons.addButton(button)
        return button

    # ── content ───────────────────────────────────────────────────────────────

    def set_content(
        self,
        aligned: AlignedReview | None,
        *,
        font_pt: int | None = None,
        focus: tuple[str, int] | None = None,
    ) -> None:
        """Show *aligned* through the current filter, optionally highlighting one note."""
        self._aligned = aligned
        self._show_trailing = False
        self._show_minor = False
        if font_pt is not None and font_pt != self._font_pt:
            self._font_pt = font_pt
            self._apply_font()
        self._update_filter_labels()
        self._render(focus)

    def clear_content(self) -> None:
        """Forget the shown review and empty the pane."""
        self._aligned = None
        self._last_key = None
        self._browser.clear()

    def set_font_point_size(self, point_size: int) -> None:
        """Apply the panel's text-zoom size and re-render at it."""
        if point_size == self._font_pt:
            return
        self._font_pt = point_size
        self._apply_font()
        self._last_key = None
        self._render(None)

    def set_filter(self, mode: str) -> None:
        """Show only *mode*: ``all``, ``changes`` or ``unclear``."""
        if mode == self._filter:
            return
        self._filter = mode
        self._button_for(mode).setChecked(True)
        self._last_key = None
        self._render(None)

    def current_filter(self) -> str:
        """Return the filter mode currently shown."""
        return self._filter

    def browser(self) -> QTextBrowser:
        """Return the text browser that shows the notes (used to install the zoom filter)."""
        return self._browser

    def focus_note(self, kind: str, number: int) -> None:
        """Highlight note *number* of *kind* and scroll to it."""
        self._show_filter_of(kind)
        if self._is_minor(kind, number):
            self._show_minor = True  # the entry lives in the collapsed section
        self._last_key = None
        self._render((kind, number))

    def _is_minor(self, kind: str, number: int) -> bool:
        """Whether change *number* is one of the spelling and punctuation changes."""
        aligned = self._aligned
        return (
            kind == "note"
            and aligned is not None
            and 0 < number <= len(aligned.changes)
            and aligned.changes[number - 1].is_minor
        )

    def _button_for(self, mode: str) -> QToolButton:
        return {
            FILTER_ALL: self._all_btn,
            FILTER_CHANGES: self._changes_btn,
            FILTER_UNCLEAR: self._unclear_btn,
        }[mode]

    def _show_filter_of(self, kind: str) -> None:
        """Switch to the filter that contains a note of *kind*, keeping *All* if it does."""
        if self._filter == FILTER_ALL:
            return
        wanted = FILTER_CHANGES if kind == "note" else FILTER_UNCLEAR
        if self._filter != wanted:
            self._filter = wanted
            self._button_for(wanted).setChecked(True)

    # ── rendering ─────────────────────────────────────────────────────────────

    def _apply_font(self) -> None:
        font = QFont()
        font.setPointSize(self._font_pt)
        self._browser.setFont(font)

    def _update_filter_labels(self) -> None:
        aligned = self._aligned
        changes = aligned.listed_count if aligned is not None else 0
        unclear = aligned.unclear_count if aligned is not None else 0
        self._changes_btn.setText(txt.filter_changes_label(changes))
        self._unclear_btn.setText(txt.filter_unclear_label(unclear))

    def _on_anchor_clicked(self, url: QUrl) -> None:
        kind, number = _split_target(url)
        if kind == "toggle":
            if number == "minor":
                self._show_minor = not self._show_minor
            else:
                self._show_trailing = not self._show_trailing
            self._last_key = None
            self._render(None)
            return
        if not number.isdigit() or kind not in ("note", "unclear"):
            return
        # The clicked entry shows that it was chosen, and the diff follows.
        self.focus_note(kind, int(number))
        if kind == "note":
            self.change_selected.emit(int(number))
        else:
            self.unclear_selected.emit(int(number))

    def _render(self, focus: tuple[str, int] | None) -> None:
        key = (
            id(self._aligned),
            self._filter,
            focus,
            self._font_pt,
            self._show_trailing,
            self._show_minor,
        )
        if key == self._last_key:
            return
        self._last_key = key
        self._browser.setHtml(self._html(focus))
        if focus is not None:
            self._browser.scrollToAnchor(f"{focus[0]}:{focus[1]}")

    def _html(self, focus: tuple[str, int] | None) -> str:
        aligned = self._aligned
        if aligned is None:
            return self._page([], focus)
        return self._page(self._blocks(aligned, focus), focus)

    def _blocks(self, aligned: AlignedReview, focus: tuple[str, int] | None) -> list[str]:
        """Return the note blocks of the current filter."""
        if self._filter == FILTER_UNCLEAR:
            return [_unclear_table(aligned.unclear, focus)] if aligned.unclear else []
        if self._filter == FILTER_CHANGES:
            blocks = [
                _change_entry(change, shown)
                for shown, change in enumerate(aligned.listed_changes, start=1)
            ]
        else:
            blocks = []
            shown = 0
            for item in _ordered_items(aligned):
                if isinstance(item, AlignedChange):
                    shown += 1
                    blocks.append(_change_entry(item, shown))
                else:
                    blocks.append(_unclear_entry(item))
        if aligned.minor_count:
            blocks.append(_minor_section(aligned, expanded=self._show_minor))
        if aligned.orphan_notes or aligned.unplaced:
            blocks.append(_trailing_section(aligned, expanded=self._show_trailing))
        return blocks

    def _page(self, blocks: Sequence[str], focus: tuple[str, int] | None) -> str:
        body = "".join(_focused(block, focus) for block in blocks)
        if not body:
            body = f"<p class='muted'>{html.escape(self._empty_text())}</p>"
        return (
            f"<html><head>{_STYLES}</head><body>"
            f"<div style='font-size: {self._font_pt}pt;'>{body}</div></body></html>"
        )

    def _empty_text(self) -> str:
        """Return the placeholder shown when the current filter has nothing to list."""
        if self._filter == FILTER_UNCLEAR:
            return txt.NO_UNCLEAR_NOTES
        if self._filter == FILTER_CHANGES:
            return txt.NO_CHANGE_NOTES
        return txt.NO_NOTES


def _focused(block: str, focus: tuple[str, int] | None) -> str:
    """Mark *block* as focused when it holds the note the teacher clicked in the diff."""
    if focus is None:
        return block
    kind, number = focus
    if f"name='{kind}:{number}'" not in block:
        return block
    return block.replace(
        "<div class='note'>", f"<div class='note focused' style='{_NOTE_FOCUS_STYLE}'>", 1
    )


def _trailing_section(aligned: AlignedReview, *, expanded: bool) -> str:
    """Return the collapsible section for notes whose edit is not in the text."""
    count = len(aligned.orphan_notes) + len(aligned.unplaced)
    toggle = (
        f"<p class='heading'><a href='toggle:trailing'>"
        f"{html.escape(txt.trailing_toggle(count, expanded=expanded))}</a></p>"
    )
    if not expanded:
        return toggle
    blocks: list[str] = [toggle]
    if aligned.orphan_notes:
        blocks.append(f"<p class='heading'>{html.escape(txt.ORPHAN_HEADING)}</p>")
        blocks.append(f"<p class='muted'>{html.escape(txt.ORPHAN_HINT)}</p>")
        blocks.extend(_orphan_entry(note) for note in aligned.orphan_notes)
    if aligned.unplaced:
        blocks.append(f"<p class='heading'>{html.escape(txt.UNPLACED_HEADING)}</p>")
        blocks.append(f"<p class='muted'>{html.escape(txt.UNPLACED_HINT)}</p>")
        blocks.extend(_unplaced_entry(sentence) for sentence in aligned.unplaced)
    return "".join(blocks)
