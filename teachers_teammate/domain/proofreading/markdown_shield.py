"""Strip the notation before a request and put it back afterwards.

A model that is asked to correct Markdown spends its attention on the marks: every failure of the
proofreading experiment on the Markdown fixture was a mark that moved, doubled or vanished.  So the
request carries the text as plain paragraphs — no marks, no list markers, crossed-out words removed
— and the notation is restored afterwards from the source document:

===============  ==========================================================
Equal            Characters keep their source style.
Replacement      New text takes the style of the span it replaces; a
                 replacement across style boundaries gets none (counted).
Insertion        Style only when both neighbours share it.
Deletion         Gone, with its style.
Crossed out      Put back exactly where it was, unchanged; inside a hunk
                 before its replacement.
Block boundary   Always the source's.  Line breaks the model adds or
                 removes are layout; a hunk across a boundary is cut there.
===============  ==========================================================

A table keeps its cells when the answer keeps them; a figure is passed through untouched.  Nothing
is ever lost: a region that cannot be restored keeps its words and loses only its style.

Pure: no I/O, no parser imports (the caller parses and renders).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..formatted_text import Block, FormattedDocument, Span, Style, merge_spans
from .word_diff import diff_opcodes, token_offsets, tokenize

#: Between two blocks of the request text.
_BLOCK_GAP = "\n\n"

#: Between two cells of a table row.
_CELL_GAP = " | "


@dataclass(frozen=True)
class CharRef:
    """Where one character of the request text comes from."""

    block: int
    cell: int
    span: int
    offset: int
    """Offset inside the span; ``-1`` for layout (a separator between blocks or cells)."""


@dataclass(frozen=True)
class Anchor:
    """A crossed-out run of the source, put back at one offset of the request text."""

    offset: int
    block: int
    cell: int
    span: int
    text: str
    style: Style


@dataclass(frozen=True)
class ShieldMap:
    """The request text of a document, and how to put the notation back."""

    text: str
    """The text the request carries: blocks as paragraphs, no marks, crossed-out words removed."""
    refs: tuple[CharRef, ...]
    """One entry per character of :attr:`text`."""
    styles: tuple[Style, ...]
    """The source style of every character of :attr:`text`."""
    anchors: tuple[Anchor, ...]
    """The crossed-out runs, in text order."""
    rows: tuple[tuple[tuple[int, ...], ...], ...]
    """Per block: the cell indices of every row (``((0,),)`` for a block with one cell)."""


@dataclass(frozen=True)
class Restored:
    """The document rebuilt from an answer, and what could not be restored."""

    document: FormattedDocument
    unstyled: int = 0
    """Regions that lost their style or their block structure (counted, never dropped)."""


def shield(doc: FormattedDocument) -> ShieldMap:
    """Return the request text of *doc* and the map that restores its notation."""
    pieces: list[str] = []
    refs: list[CharRef] = []
    styles: list[Style] = []
    anchors: list[Anchor] = []
    rows: list[tuple[tuple[int, ...], ...]] = []
    at = 0

    def write(text: str, block: int, cell: int, span: int, style: Style) -> None:
        nonlocal at
        pieces.append(text)
        refs.extend(CharRef(block, cell, span, offset) for offset in range(len(text)))
        styles.extend([style] * len(text))
        at += len(text)

    def layout(text: str, block: int) -> None:
        nonlocal at
        pieces.append(text)
        refs.extend(CharRef(block, -1, -1, -1) for _ in text)
        styles.extend([Style.NONE] * len(text))
        at += len(text)

    def write_spans(spans: Sequence[Span], block: int, cell: int) -> None:
        nonlocal at
        for span_index, span in enumerate(spans):
            if span.style & Style.STRIKE:
                anchors.append(Anchor(at, block, cell, span_index, span.text, span.style))
                continue
            write(span.text, block, cell, span_index, span.style)

    for block_index, block in enumerate(doc.blocks):
        if block.kind == "figure":
            rows.append(())
            continue
        if pieces:
            layout(_BLOCK_GAP, block_index)
        if block.kind != "table":
            write_spans(block.spans, block_index, 0)
            rows.append(((0,),))
            continue
        block_rows: list[tuple[int, ...]] = []
        cell = 0
        for row_index, row in enumerate(block.rows):
            if row_index:
                layout("\n", block_index)
            indices: list[int] = []
            for cell_index, spans in enumerate(row):
                if cell_index:
                    layout(_CELL_GAP, block_index)
                indices.append(cell)
                write_spans(spans, block_index, cell)
                cell += 1
            block_rows.append(tuple(indices))
        rows.append(tuple(block_rows))
    return ShieldMap(
        text="".join(pieces),
        refs=tuple(refs),
        styles=tuple(styles),
        anchors=tuple(anchors),
        rows=tuple(rows),
    )


def restore(doc: FormattedDocument, shield_map: ShieldMap, answer: str) -> Restored:
    """Return *doc* with *answer* in place of its text, notation restored from *shield_map*.

    *doc* must be the document :func:`shield` was given: the block structure of the result is the
    source's, and the map's rows are indexed by its blocks.
    """
    restorer = _Restorer(shield_map)
    restorer.walk(answer)
    blocks, lost = restorer.blocks(doc)
    return Restored(document=FormattedDocument(tuple(blocks)), unstyled=restorer.unstyled + lost)


class _Restorer:
    """Rebuilds the spans of every block from an answer, one region of the diff at a time."""

    def __init__(self, shield_map: ShieldMap) -> None:
        self._map = shield_map
        self._out: dict[tuple[int, int], list[Span]] = {
            (block, cell): []
            for block, block_rows in enumerate(shield_map.rows)
            for row in block_rows
            for cell in row
        }
        self._broken: set[int] = set()
        self._anchor_at = 0
        self.unstyled = 0
        """Regions that lost their style or their block structure."""

    def walk(self, answer: str) -> None:
        """Write every region of the diff between the request text and *answer*."""
        raw_tokens = tokenize(self._map.text)
        answer_tokens = tokenize(answer)
        raw_at = token_offsets(raw_tokens)
        answer_at = token_offsets(answer_tokens)
        cursor = 0
        spaced = False
        for tag, i1, i2, j1, j2 in diff_opcodes(raw_tokens, answer_tokens):
            start, end = raw_at[i1], raw_at[i2]
            if tag == "equal":
                while spaced and start < end and self._map.text[start].isspace():
                    start += 1
                self._emit_equal(start, end)
                spaced = False
                cursor = end
                continue
            replacement = answer[answer_at[j1] : answer_at[j2]]
            skip = self._skip_before(start, cursor, replacement)
            self._emit_equal(cursor, start - skip)
            self.unstyled += self._emit_replacement(start, end, replacement)
            spaced = replacement[-1:].isspace()
            cursor = end
        self._emit_equal(cursor, len(self._map.text))
        self._emit_anchors(len(self._map.text))

    def blocks(self, doc: FormattedDocument) -> tuple[list[Block], int]:
        """Return the rebuilt blocks of *doc* and how many lost their structure."""
        blocks: list[Block] = []
        lost = 0
        for block_index, block in enumerate(doc.blocks):
            if block.kind == "figure":
                blocks.append(block)
                continue
            block_rows = self._map.rows[block_index]
            if block.kind == "table" and block_index not in self._broken:
                blocks.append(
                    Block(
                        kind="table",
                        rows=tuple(
                            tuple(merge_spans(self._out[(block_index, cell)]) for cell in row)
                            for row in block_rows
                        ),
                    )
                )
                continue
            spans = merge_spans(
                [
                    span
                    for row in block_rows
                    for cell in row
                    for span in self._out[(block_index, cell)]
                ]
            )
            kind = "paragraph" if block.kind == "table" else block.kind
            if kind != block.kind:
                lost += 1
            blocks.append(
                Block(
                    kind=kind,
                    spans=spans,
                    level=block.level,
                    ordered=block.ordered,
                    number=block.number,
                )
            )
        return blocks, lost

    def _skip_before(self, start: int, cursor: int, replacement: str) -> int:
        """Return how much of the source's whitespace before a hunk the replacement replaces."""
        if not replacement[:1].isspace():
            return 0
        skip = 0
        while start - skip > cursor and self._map.text[start - skip - 1].isspace():
            skip += 1
        return skip

    def _emit_anchors(self, offset: int) -> None:
        """Write the crossed-out runs that belong at or before *offset*."""
        anchors = self._map.anchors
        while self._anchor_at < len(anchors) and anchors[self._anchor_at].offset <= offset:
            anchor = anchors[self._anchor_at]
            self._out[(anchor.block, anchor.cell)].append(Span(anchor.text, anchor.style))
            self._anchor_at += 1

    def _emit_equal(self, start: int, end: int) -> None:
        """Write the source characters of an unchanged region, with their styles."""
        for offset in range(start, end):
            self._emit_anchors(offset)
            ref = self._map.refs[offset]
            if ref.span < 0:
                continue
            self._out[(ref.block, ref.cell)].append(
                Span(self._map.text[offset], self._map.styles[offset])
            )

    def _emit_replacement(self, start: int, end: int, replacement: str) -> int:
        """Write the new words of a hunk; return how many regions lost their style."""
        if start == end:
            target = _neighbour(self._map, start)
            if target is None:
                return 0
            self._out[target].append(Span(replacement, _shared_style(self._map, start)))
            return 0
        # A crossed-out word inside the hunk goes before its replacement, set off by spaces.
        self._emit_anchors_in_hunk(end, replacement)
        refs = self._map.refs[start:end]
        first = next((ref for ref in refs if ref.span >= 0), None)
        if first is None:
            return self._emit_in_layout(start, replacement)
        styles = {self._map.styles[start + at] for at, ref in enumerate(refs) if ref.span >= 0}
        one_place = all(
            (ref.block, ref.cell) == (first.block, first.cell) for ref in refs if ref.span >= 0
        )
        style = next(iter(styles)) if len(styles) == 1 else Style.NONE
        self._out[(first.block, first.cell)].append(Span(replacement, style))
        lost = 0 if len(styles) == 1 and one_place else 1
        if any(ref.block != first.block for ref in refs):
            # The hunk is cut at the block boundary: the later blocks keep their own words.
            self._broken.add(first.block)
            lost += 1
            for offset in range(start, end):
                ref = self._map.refs[offset]
                if ref.block != first.block and ref.span >= 0:
                    self._out[(ref.block, ref.cell)].append(
                        Span(self._map.text[offset], self._map.styles[offset])
                    )
        return lost

    def _emit_in_layout(self, start: int, replacement: str) -> int:
        """Write what the answer put where the source has only layout; return the lost styles.

        The gap between two blocks (or two cells) is the source's, so whitespace there is dropped.
        Words and marks are not: they stay, unstyled, at the end of the block before the gap,
        and the region is counted.
        """
        core = replacement.strip()
        if not core:
            return 0
        target = _neighbour(self._map, start)
        if target is None:
            return 0
        lead = " " if replacement[:1].isspace() and core[:1].isalnum() else ""
        self._out[target].append(Span(lead + core, Style.NONE))
        return 1

    def _emit_anchors_in_hunk(self, end: int, replacement: str) -> None:
        """Write the crossed-out runs inside a hunk, before the replacement that follows them."""
        anchors = self._map.anchors
        first = self._anchor_at
        self._emit_anchors(end - 1)
        for anchor in anchors[first : self._anchor_at]:
            spans = self._out[(anchor.block, anchor.cell)]
            # ``_emit_anchors`` appended the run last; put the spaces around it.
            run = spans.pop()
            if spans and not spans[-1].text[-1:].isspace():
                spans.append(Span(" ", Style.NONE))
            spans.append(run)
        if self._anchor_at > first and replacement[:1].isalnum():
            last = anchors[self._anchor_at - 1]
            self._out[(last.block, last.cell)].append(Span(" ", Style.NONE))


def _neighbour(shield_map: ShieldMap, offset: int) -> tuple[int, int] | None:
    """Return the block and cell an insertion at *offset* belongs to, or ``None``."""
    for index in (offset, offset - 1):
        if 0 <= index < len(shield_map.refs):
            ref = shield_map.refs[index]
            if ref.span >= 0:
                return ref.block, ref.cell
    for index in range(offset, len(shield_map.refs)):
        ref = shield_map.refs[index]
        if ref.span >= 0:
            return ref.block, ref.cell
    return None


def _shared_style(shield_map: ShieldMap, offset: int) -> Style:
    """Return the style an insertion at *offset* takes: only when both neighbours share it."""
    if offset <= 0 or offset >= len(shield_map.refs):
        return Style.NONE
    before, after = shield_map.refs[offset - 1], shield_map.refs[offset]
    if before.span < 0 or after.span < 0:
        return Style.NONE
    if (before.block, before.cell, before.span) != (after.block, after.cell, after.span):
        return Style.NONE
    return shield_map.styles[offset - 1]
