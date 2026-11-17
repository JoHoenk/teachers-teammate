"""Where the characters of a plain text sit in the Qt document that shows it.

A review's offsets are character offsets into the *plain* text of a stored document
(:meth:`~teachers_teammate.application.formatting.TextFormatter.plain_text`), while what a
teacher reads, clicks and edits is a :class:`QTextDocument` built by
:func:`~teachers_teammate.gui._formatted_text.render_html`.  The two are not the same text: the
document has no list markers, no `` | `` between table cells and no blank line between blocks,
and it may carry whitespace the plain text has trimmed.

:class:`PlainMap` walks the document the way
:func:`~teachers_teammate.gui._formatted_text.document_from_qt` does and records, per run of
real text, where that run starts in the document and in the plain text.  Everything the plain
text has and the document has not — a list marker, a table's separators, the blank line between
two blocks — is *virtual*: it carries a plain range but no Qt range, so an offset that falls in
one snaps to the next run.

The map is only used when it reproduces the plain text (:meth:`PlainMap.matches`).  A document
it cannot reproduce is not forced: the caller then shows the text without marks, since a mark on
the wrong words is worse than no mark at all.

Given the text it is expected to stand for, the map locates its runs in that text instead of
comparing the two character by character, so the layout a document never shows — the blank lines
of a stored plain text, the line break it ends with, the spaces a block is indented by — does not
cost the marks.

Pure Qt: no I/O, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtGui import (
    QTextBlock,
    QTextCursor,
    QTextDocument,
    QTextFrame,
    QTextList,
    QTextTable,
    QTextTableCell,
)

from ...domain.formatted_text import LIST_INDENT

#: Qt stores a soft line break as U+2028 and a non-breaking blank as U+00A0; the plain text has
#: ``"\\n"`` and ``" "`` there.  Both replacements keep the length, so a Qt position stays valid.
_LINE_SEPARATOR = chr(0x2028)
_NO_BREAK_SPACE = chr(0x00A0)

#: The list styles :func:`~teachers_teammate.gui._formatted_text.document_from_qt` counts as
#: numbered; every other style is a bullet.
_NUMBERED_STYLES = frozenset(
    {
        "ListDecimal",
        "ListLowerAlpha",
        "ListUpperAlpha",
        "ListLowerRoman",
        "ListUpperRoman",
    }
)


@dataclass(frozen=True)
class _Run:
    """One run of text that stands in both the document and the plain text."""

    qt_start: int
    qt_end: int
    plain_start: int

    @property
    def length(self) -> int:
        """Number of characters of the run."""
        return self.qt_end - self.qt_start


@dataclass(frozen=True)
class _Piece:
    """A run of the plain text, with the Qt position it starts at (``None``: virtual)."""

    text: str
    qt_start: int | None


def _fragments(block: QTextBlock) -> list[_Piece]:
    """Return the text runs of *block*, each with the Qt position it starts at."""
    pieces: list[_Piece] = []
    iterator = block.begin()
    while not iterator.atEnd():
        fragment = iterator.fragment()
        if fragment.isValid():
            text = fragment.text().replace(_LINE_SEPARATOR, "\n").replace(_NO_BREAK_SPACE, " ")
            if text:
                pieces.append(_Piece(text, fragment.position()))
        iterator += 1
    return pieces


def _strip(pieces: list[_Piece]) -> list[_Piece]:
    """Trim the outer whitespace of *pieces*, the way ``strip_spans`` trims a block's spans.

    The trimmed characters stay in the document but leave the plain text, so a real piece that
    loses leading whitespace starts that many Qt positions later.
    """
    items = list(pieces)
    while items and not items[0].text.strip():
        items.pop(0)
    while items and not items[-1].text.strip():
        items.pop()
    if not items:
        return []
    first = items[0]
    lead = len(first.text) - len(first.text.lstrip())
    if lead:
        items[0] = _Piece(
            first.text[lead:], None if first.qt_start is None else first.qt_start + lead
        )
    last = items[-1]
    trail = len(last.text) - len(last.text.rstrip())
    if trail:
        items[-1] = _Piece(last.text[: len(last.text) - trail], last.qt_start)
    return items


def _marker(block: QTextBlock, text_list: QTextList) -> str:
    """Return the list marker the plain text writes for *block* (``"- "`` or ``"3. "``)."""
    list_format = text_list.format()
    if list_format.style().name not in _NUMBERED_STYLES:
        return "- "
    return f"{list_format.start() + text_list.itemNumber(block)}. "


def _block_pieces(block: QTextBlock) -> tuple[list[_Piece], bool]:
    """Return the plain pieces of *block* and whether it is a list item."""
    text_list = block.textList()
    if text_list is None:
        return _strip(_fragments(block)), False
    level = max(text_list.format().indent() - 1, 0)
    marker = _Piece(LIST_INDENT * level + _marker(block, text_list), None)
    return [marker, *_strip(_fragments(block))], True


def _cell_pieces(cell: QTextTableCell) -> list[_Piece]:
    """Return the plain pieces of one table cell: its blocks, separated by a line break."""
    pieces: list[_Piece] = []
    iterator = cell.begin()
    while not iterator.atEnd():
        block = iterator.currentBlock()
        if block.isValid():
            if pieces:
                pieces.append(_Piece("\n", None))
            pieces.extend(_fragments(block))
        iterator += 1
    return _strip(pieces)


def _table_pieces(table: QTextTable) -> list[_Piece]:
    """Return the plain pieces of *table*: cells joined by ``" | "``, rows by a line break."""
    pieces: list[_Piece] = []
    for row in range(table.rows()):
        if row:
            pieces.append(_Piece("\n", None))
        for column in range(table.columns()):
            if column:
                pieces.append(_Piece(" | ", None))
            pieces.extend(_cell_pieces(table.cellAt(row, column)))
    return pieces


def _items(document: QTextDocument) -> list[tuple[bool, QTextBlock | QTextTable]]:
    """Return the blocks and tables of *document* in document order.

    The walk mirrors ``document_from_qt``: a table is one block of the plain text, and a frame
    that is not a table is entered.
    """
    found: list[tuple[bool, QTextBlock | QTextTable]] = []

    def walk(frame: QTextFrame) -> None:
        iterator = frame.begin()
        while not iterator.atEnd():
            child_frame = iterator.currentFrame()
            child_block = iterator.currentBlock()
            if isinstance(child_frame, QTextTable):
                found.append((True, child_frame))
            elif child_frame is not None:
                walk(child_frame)
            elif child_block.isValid():
                found.append((False, child_block))
            iterator += 1

    walk(document.rootFrame())
    return found


def _pieces(document: QTextDocument) -> list[_Piece]:
    """Return the plain pieces of *document*, in document order."""
    pieces: list[_Piece] = []
    previous_was_item = False
    emitted = False
    for is_table, item in _items(document):
        if is_table:
            assert isinstance(item, QTextTable)
            block_pieces, is_item = _table_pieces(item), False
        else:
            assert isinstance(item, QTextBlock)
            block_pieces, is_item = _block_pieces(item)
        if not "".join(piece.text for piece in block_pieces).strip():
            continue  # a blank block is in neither text
        if emitted:
            # A list item that follows another one continues the list on the next line.
            pieces.append(_Piece("\n" if is_item and previous_was_item else "\n\n", None))
        pieces.extend(block_pieces)
        previous_was_item = is_item
        emitted = True
    return pieces


def _runs(pieces: Sequence[_Piece]) -> tuple[_Run, ...]:
    """Return the runs of *pieces*, each with the plain offset it starts at."""
    runs: list[_Run] = []
    length = 0
    for piece in pieces:
        if piece.qt_start is not None and piece.text:
            runs.append(_Run(piece.qt_start, piece.qt_start + len(piece.text), length))
        length += len(piece.text)
    return tuple(runs)


def _locate(pieces: Sequence[_Piece], plain: str) -> tuple[_Run, ...] | None:
    """Find every piece of *pieces* in *plain*, in order, and return the runs' places.

    Whitespace the document does not show is skipped: the blank lines between two blocks, the
    line break a stored plain text ends with, the spaces a block is indented by.  A piece that
    is not where it should be means the document is not this text, and ``None`` is returned.
    """
    runs: list[_Run] = []
    cursor = 0
    for index, piece in enumerate(pieces):
        if not piece.text:
            continue
        if piece.qt_start is None and not piece.text.strip():
            # A separator: any amount of whitespace stands for it, but the indent of the piece
            # that follows belongs to that piece.
            following = pieces[index + 1].text if index + 1 < len(pieces) else ""
            cursor = _skip_whitespace(plain, cursor, keep=_indent(following))
            continue
        # A piece that starts with whitespace (a list marker's indent, a table's separator)
        # carries it, so it is looked for where the cursor stands.
        start = cursor if piece.text[:1].isspace() else _skip_whitespace(plain, cursor)
        if not plain.startswith(piece.text, start):
            return None
        if piece.qt_start is not None:
            runs.append(_Run(piece.qt_start, piece.qt_start + len(piece.text), start))
        cursor = start + len(piece.text)
    if plain[cursor:].strip():
        return None
    return tuple(runs)


def _indent(text: str) -> int:
    """Return how many whitespace characters *text* starts with."""
    return len(text) - len(text.lstrip())


def _skip_whitespace(text: str, start: int, *, keep: int = 0) -> int:
    """Return the first position at or after *start* that is not whitespace.

    *keep* whitespace characters are left in place, for a piece that starts with them.
    """
    position = start
    while position < len(text) and text[position].isspace():
        position += 1
    return max(start, position - keep)


class PlainMap:
    """The plain text a Qt document stands for, and where each of its runs sits.

    Build it after every fill of a view; :attr:`text` is the plain text the document
    reproduces, and :meth:`matches` says whether that is the text the caller expected.  Pass
    that text as *plain* to have the runs located in it, which tolerates the layout the
    document does not show.
    """

    def __init__(self, document: QTextDocument, plain: str | None = None) -> None:
        pieces = _pieces(document)
        self._text = "".join(piece.text for piece in pieces)
        self._plain = plain
        self._matched = True
        if plain is None:
            runs = _runs(pieces)
        else:
            located = _locate(pieces, plain)
            self._matched = located is not None
            runs = located or ()
        self._runs = runs
        self._plain_starts = tuple(run.plain_start for run in runs)
        self._qt_starts = tuple(run.qt_start for run in runs)

    @property
    def text(self) -> str:
        """The plain text the document stands for."""
        return self._text

    def matches(self, plain: str) -> bool:
        """Whether the document reproduces *plain* (the invariant of the map).

        Without an expected text at construction the two must be equal.  With one, the runs
        were located in it, so the layout the document does not show — a stored plain text's
        blank lines, the line break it ends with, the spaces a block is indented by — was
        skipped and the map is exact where it matters.
        """
        if self._plain is not None:
            return self._matched and self._plain == plain
        return self._text == plain

    def position(self, offset: int) -> int:
        """Return the Qt position of the plain *offset*.

        An offset inside virtual text (a list marker, a table separator, the blank line between
        two blocks) snaps forward to the next run, and one past the end to the end of the last.
        """
        if not self._runs:
            return 0
        index = bisect_right(self._plain_starts, offset) - 1
        if index < 0:
            return self._runs[0].qt_start
        run = self._runs[index]
        if offset < run.plain_start + run.length:
            return run.qt_start + (offset - run.plain_start)
        if index + 1 < len(self._runs):
            return self._runs[index + 1].qt_start
        return run.qt_end

    def offset(self, position: int) -> int:
        """Return the plain offset of the Qt *position*.

        A position in text the plain text does not have (trimmed whitespace, a block separator)
        snaps forward to the next run, and one past the end to the end of the plain text.
        """
        if not self._runs:
            return 0
        index = bisect_right(self._qt_starts, position) - 1
        if index < 0:
            return self._runs[0].plain_start
        run = self._runs[index]
        if position < run.qt_end:
            return run.plain_start + (position - run.qt_start)
        if index + 1 < len(self._runs):
            return self._runs[index + 1].plain_start
        return run.plain_start + run.length

    def span(self, start: int, end: int) -> tuple[int, int]:
        """Return the Qt positions of the plain *start*..*end*.

        The end stays inside the run it ends: an offset at the end of a run is followed by
        virtual text, and snapping it forward would take in the words of the next run.  An
        empty span is one place, found by :meth:`insertion`: its two ends taken apart would
        straddle the virtual text between two runs and select the paragraph break.
        """
        if start == end:
            place = self.insertion(start)
            return place, place
        return self.position(start), self._end_position(end)

    def insertion(self, offset: int) -> int:
        """Return the Qt position at which text belonging at the plain *offset* is inserted.

        Unlike :meth:`position` an offset in virtual text does not snap forward: words that
        belong behind the last run of a paragraph go there, not to the start of the next one.
        An offset at the start of a run is that run's start, so words that belong in front of
        a paragraph still go in front of it.
        """
        if not self._runs:
            return 0
        index = bisect_right(self._plain_starts, offset) - 1
        if index < 0:
            return self._runs[0].qt_start
        run = self._runs[index]
        return run.qt_start + min(offset - run.plain_start, run.length)

    def _end_position(self, offset: int) -> int:
        """Return the Qt position just behind the plain character before *offset*."""
        if not self._runs:
            return 0
        index = bisect_right(self._plain_starts, offset - 1) - 1
        if index < 0:
            return self._runs[0].qt_start
        run = self._runs[index]
        if offset - 1 < run.plain_start + run.length:
            return run.qt_start + (offset - run.plain_start)
        return run.qt_end

    def cursor(self, document: QTextDocument, start: int, end: int) -> QTextCursor:
        """Return a cursor over the words that stand at plain *start*..*end* of *document*."""
        first, last = self.span(start, end)
        cursor = QTextCursor(document)
        cursor.setPosition(first)
        cursor.setPosition(last, QTextCursor.MoveMode.KeepAnchor)
        return cursor
