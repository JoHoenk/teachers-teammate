"""The three texts of the Check tab: what was read, with corrections, and the final text.

A view shows one stored text and marks what the review did to it.  The marks are overlays
(:class:`QTextEdit.ExtraSelection`), never document formatting, so the text a view stores is the
same with and without them and typing can never write a mark into a text.  Positions come from
:class:`~.plain_map.PlainMap`, never from arithmetic on the widget's text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QTextCharFormat, QTextCursor, QWheelEvent
from PySide6.QtWidgets import QTextEdit, QWidget

from ...application.check_document import CheckDocument
from ...application.check_items import ItemKind
from ...application.formatting import TextFormatter
from ...domain.formatted_text import FormattedDocument, TextFormat
from ...domain.proofreading.quotes import map_forward
from ...domain.teacher_work import TextEdit
from ...texts import check as check_text
from .._formatted_text import (
    STUDENT_STRIKE_COLOUR,
    FormattedTextEdit,
    document_from_qt,
    install_formatting_shortcuts,
    render_html,
)
from . import _style
from ._masking import Masking
from ._plain_map import PlainMap

#: Pause after the last keystroke before an edit is converted back and stored.
EDIT_COMMIT_DELAY_MS = 400

#: Text size of the views, in points, and the range Ctrl+wheel moves it in.
TEXT_POINT_SIZE = 12
MIN_POINT_SIZE = 8
MAX_POINT_SIZE = 24


@dataclass(frozen=True)
class Mark:
    """One stretch of the corrected text a mark is drawn on."""

    key: str
    """The item the mark belongs to."""
    start: int
    end: int
    """The span in the plain text of the corrected text (masked when names are hidden)."""
    kind: ItemKind
    minor: bool = False
    """``True`` for a spelling or punctuation change, which is drawn lighter."""


def marks_of(doc: CheckDocument, masking: Masking) -> tuple[Mark, ...]:
    """Return the marks of *doc*, in the text the views show.

    One mark per corrected span of an item.  A correction that was taken out has no span in the
    corrected text any more, and an unclear passage never had one, so their words are found where
    the span of the student's text landed.
    """
    hunks = tuple(change.hunk for change in doc.aligned.changes) if doc.aligned else ()
    marks: list[Mark] = []
    for item in doc.items:
        spans = item.corrected_spans
        if not spans and hunks and item.kind in (ItemKind.TAKEN_OUT, ItemKind.UNCLEAR):
            spans = (map_forward(item.raw_span, hunks),)
        for start, end in spans:
            carried = masking.carry((start, end), doc.corrected_plain)
            marks.append(Mark(item.key, carried[0], carried[1], item.kind, item.is_minor))
    return tuple(marks)


def _text_font(point_size: int) -> QFont:
    """Return the proportional font the views read in."""
    font = QFont()
    font.setPointSize(point_size)
    return font


def _struck_format() -> QTextCharFormat:
    """Return the format of the words the model took out: red and struck through."""
    fmt = QTextCharFormat()
    fmt.setForeground(QColor(_style.MODEL_STRIKE))
    fmt.setFontStrikeOut(True)
    return fmt


#: Qt answers ``characterAt`` with this non-character at a block or a table boundary.
_BOUNDARY = "\ufdd0"


def _is_separator(char: str) -> bool:
    """Whether *char* separates two words: whitespace, a block boundary, or nothing at all."""
    return not char or char == _BOUNDARY or char.isspace()


class _ZoomWheel(QObject):
    """Ctrl+wheel on a text view changes its text size instead of scrolling."""

    def __init__(self, view: CheckTextView) -> None:
        super().__init__(view)
        self._view = view

    def eventFilter(self, _obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Wheel:
            wheel = cast(QWheelEvent, event)
            if wheel.modifiers() & Qt.KeyboardModifier.ControlModifier:
                self._view.zoom_by(1 if wheel.angleDelta().y() > 0 else -1)
                return True
        return False


class CheckTextView(FormattedTextEdit):
    """One text of the Check tab: a proportional font, a plain map, marks as overlays."""

    item_clicked = Signal(str)
    """A mark was clicked: the key of the item it belongs to."""
    zoom_changed = Signal(int)
    """Ctrl+wheel changed the text size; the window stores it for the next document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFont(_text_font(TEXT_POINT_SIZE))
        self.setReadOnly(True)
        self._point_size = TEXT_POINT_SIZE
        self._map: PlainMap | None = None
        self._plain = ""
        self._format: TextFormat = "plain"
        self._mark_cursors: list[tuple[Mark, QTextCursor]] = []
        self._chosen = ""
        self._rendering = False
        self.installEventFilter(_ZoomWheel(self))

    # ── the text ──────────────────────────────────────────────────────────

    def set_text(self, text: str, text_format: TextFormat) -> None:
        """Fill the view from a stored text (already masked when names are hidden)."""
        self._format = text_format
        self._plain = TextFormatter.plain_text(text, text_format)
        self._rendering = True
        try:
            self.setHtml(
                render_html(
                    TextFormatter.document(text, text_format),
                    strike_colour=STUDENT_STRIKE_COLOUR,
                )
            )
        finally:
            self._rendering = False
        self._map = self._map_of(self._plain)
        self._mark_cursors = []
        self.setExtraSelections([])

    def _map_of(self, plain: str) -> PlainMap | None:
        """Return the map of the current document, or ``None`` when it is not *plain*."""
        plain_map = PlainMap(self.document(), plain)
        return plain_map if plain_map.matches(plain) else None

    @property
    def mapped(self) -> bool:
        """Whether the document reproduces the plain text, so marks can be placed."""
        return self._map is not None

    def set_editable(self, editable: bool) -> None:
        """Allow or refuse typing in the view."""
        self.setReadOnly(not editable)

    # ── the marks ─────────────────────────────────────────────────────────

    def set_marks(self, marks: Sequence[Mark], *, plain: str, chosen: str = "") -> None:
        """Mark the words at *marks*, which are spans of *plain*; *chosen* is drawn stronger."""
        self._plain = plain
        self._map = self._map_of(plain)
        self._chosen = chosen
        self._mark_cursors = []
        if self._map is None:
            self.setExtraSelections([])
            return
        self._mark_cursors = [
            (mark, self._map.cursor(self.document(), mark.start, mark.end))
            for mark in marks
            if mark.end > mark.start
        ]
        self._apply_marks()

    def _apply_marks(self) -> None:
        """Draw the marks of the stored cursors as overlays."""
        selections: list[QTextEdit.ExtraSelection] = []
        for mark, cursor in self._mark_cursors:
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = _style.mark_format(
                mark.kind, minor=mark.minor, chosen=mark.key == self._chosen
            )
            selections.append(selection)
        self.setExtraSelections(selections)

    def reveal(self, key: str) -> None:
        """Scroll to the mark of *key* and select its words."""
        for mark, cursor in self._mark_cursors:
            if mark.key == key:
                self.setTextCursor(cursor)
                self.ensureCursorVisible()
                return

    def _key_at(self, point: QPoint) -> str:
        """Return the key of the innermost mark at *point*, or ``""``."""
        position = self.cursorForPosition(point).position()
        best = ""
        best_length: int | None = None
        for mark, cursor in self._mark_cursors:
            start, end = cursor.selectionStart(), cursor.selectionEnd()
            if start <= position < end and (best_length is None or end - start < best_length):
                best, best_length = mark.key, end - start
        return best

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """A click on a mark tells the page which item it belongs to."""
        if event.button() == Qt.MouseButton.LeftButton:
            key = self._key_at(event.position().toPoint())
            if key:
                self.item_clicked.emit(key)
        super().mousePressEvent(event)

    # ── the text size ─────────────────────────────────────────────────────

    @property
    def point_size(self) -> int:
        """The text size of the view, in points."""
        return self._point_size

    def set_point_size(self, point_size: int) -> None:
        """Read the text at *point_size* points."""
        self._point_size = max(MIN_POINT_SIZE, min(MAX_POINT_SIZE, point_size))
        self.setFont(_text_font(self._point_size))

    def zoom_by(self, step: int) -> None:
        """Make the text one point larger or smaller, and tell the window."""
        before = self._point_size
        self.set_point_size(before + step)
        if self._point_size != before:
            self.zoom_changed.emit(self._point_size)


class _EditableView(CheckTextView):
    """A view the teacher can type in: an edit is stored after a pause in typing."""

    text_edited = Signal(str)
    """The stored text changed: the new text, in the notation of the document."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setReadOnly(False)
        self.setToolTip(check_text.FORMAT_TIP)
        install_formatting_shortcuts(self)
        self._stored = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(EDIT_COMMIT_DELAY_MS)
        self._timer.timeout.connect(self.flush)
        self.textChanged.connect(self._on_changed)
        self.focus_lost.connect(self.flush)

    def set_text(self, text: str, text_format: TextFormat) -> None:
        """Fill the view from a stored text and remember it as the text to compare against.

        The text to compare against is the canonical form of the stored text, not the stored
        text itself: a plain text keeps a line break at its end that the document does not
        show, and comparing against the stored text would make the first commit "edit" a text
        nobody touched.
        """
        super().set_text(text, text_format)
        self._stored = self._text_of(TextFormatter.document(text, text_format))

    def _text_of(self, doc: FormattedDocument) -> str:
        """Return *doc* in the notation of the document."""
        return (
            TextFormatter.markdown(doc) if self._format == "markdown" else TextFormatter.plain(doc)
        )

    def _on_changed(self) -> None:
        """Note a keystroke: the marks belong to the text that was there, so they go."""
        if self._rendering or self.isReadOnly():
            return
        self._mark_cursors = []
        self.setExtraSelections([])
        self._timer.start()

    def flush(self) -> None:
        """Store typed-but-uncommitted text right now."""
        self._timer.stop()
        if self._rendering or self.isReadOnly():
            return
        text = self._text_of(document_from_qt(self.document()))
        if text == self._stored:
            return  # nothing changed: a commit that stores nothing would only cost a reload
        self._stored = text
        self.text_edited.emit(text)


class ReadView(_EditableView):
    """*What was read*: the transcription, with reading fixes typed into it."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setToolTip(check_text.READ_TIP)


class FinalView(_EditableView):
    """*Final text*: the corrected text, which every card action edits."""

    def apply_plain_edits(self, edits: Sequence[TextEdit]) -> None:
        """Apply *edits* (plain offsets of the corrected text) as one undo step.

        The edits are applied last to first, so an earlier one never moves a later one, and the
        whole action is one edit block: one Ctrl+Z takes it back.
        """
        if self._map is None or not edits:
            return
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for edit in sorted(edits, key=lambda edit: edit.start, reverse=True):
            first, last = self._map.span(edit.start, edit.end)
            cursor.setPosition(first)
            cursor.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(edit.text)
        cursor.endEditBlock()
        self.flush()


class MarkedView(CheckTextView):
    """*With corrections*: the corrected text with the student's words struck through.

    Read-only, and the only view that carries formatting of its own: the student's words are
    inserted in front of the words that replaced them.  Nothing is ever converted back from it.
    """

    def show_document(self, doc: CheckDocument, masking: Masking, *, chosen: str = "") -> None:
        """Show *doc*: the corrected text, the student's words, and the marks of the review."""
        text = masking.mask(doc.corrected_text)
        self.set_text(text, doc.text_format)
        self._chosen = chosen
        if self._map is None:
            return
        marks = marks_of(doc, masking)
        cursors = [
            (mark, self._map.cursor(self.document(), mark.start, mark.end))
            for mark in marks
            if mark.end > mark.start
        ]
        self._insert_struck_words(doc, masking)
        self._mark_cursors = cursors
        self._apply_marks()

    def _insert_struck_words(self, doc: CheckDocument, masking: Masking) -> None:
        """Put the student's words in front of the words that replaced them, last hunk first."""
        if self._map is None or doc.aligned is None:
            return
        hunks = sorted(doc.aligned.changes, key=lambda change: change.hunk.corrected_start)
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        for change in reversed(hunks):
            hunk = change.hunk
            if not hunk.original:
                continue
            start, _ = masking.carry(
                (hunk.corrected_start, hunk.corrected_start), doc.corrected_plain
            )
            position = self._map.insertion(start)
            cursor.setPosition(position)
            words = masking.mask(hunk.original)
            cursor.insertText(
                self._space_before(position) + words + self._space_after(position),
                _struck_format(),
            )
        cursor.endEditBlock()

    def _space_before(self, position: int) -> str:
        """One space in front of the student's words unless a separator is already there."""
        if position <= 0:
            return ""
        return "" if _is_separator(self.document().characterAt(position - 1)) else " "

    def _space_after(self, position: int) -> str:
        """One space behind the student's words unless a separator is already there."""
        return "" if _is_separator(self.document().characterAt(position)) else " "
