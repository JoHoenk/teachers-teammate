"""Format-neutral document model for OCR output.

OCR engines emit text in many notations (plain text, Markdown, layout-aware HTML).
Parsers in :mod:`teachers_teammate.infrastructure.ocr_formats` turn any of them into a
:class:`FormattedDocument`; renderers turn the model back into Markdown, plain text,
Qt HTML or DOCX runs.  This module is pure data: no I/O, no third-party imports.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
import dataclasses
from dataclasses import dataclass, field
import enum
from typing import Literal

TextFormat = Literal["plain", "markdown"]
"""Notation of a stored text: ``"plain"`` is verbatim, ``"markdown"`` is the canonical
rich form (CommonMark + GFM strikethrough/tables + inline ``<sup>``/``<sub>``/``<u>``)."""

PLAIN: TextFormat = "plain"
MARKDOWN: TextFormat = "markdown"


def normalize_text_format(value: str | None) -> TextFormat:
    """Return *value* as a :data:`TextFormat`, falling back to ``"plain"`` when unknown."""
    return MARKDOWN if value == MARKDOWN else PLAIN


def join_labelled_pages(pages: Sequence[str], *, markdown: bool = False) -> str:
    """Join per-page text with ``Page N:`` labels; a single page is returned unlabelled.

    In Markdown the label is followed by a blank line and pages without text are dropped
    (numbering is kept).  Every source of multi-page text (OCR, embedded PDF text) uses
    this so the stored ``raw_text`` has one shape.
    """
    if len(pages) == 1:
        return pages[0]
    if markdown:
        return "\n\n".join(
            f"Page {i + 1}:\n\n{text}" for i, text in enumerate(pages) if text.strip()
        )
    return "\n".join(f"Page {i + 1}:\n{text}" for i, text in enumerate(pages))


class Style(enum.Flag):
    """Inline character styles; combine with ``|``."""

    NONE = 0
    BOLD = enum.auto()
    ITALIC = enum.auto()
    UNDERLINE = enum.auto()
    STRIKE = enum.auto()
    SUPERSCRIPT = enum.auto()
    SUBSCRIPT = enum.auto()


@dataclass(frozen=True)
class Span:
    """A run of text sharing one :class:`Style`.  ``"\\n"`` inside *text* is a line break."""

    text: str
    style: Style = Style.NONE


BlockKind = Literal[
    "paragraph",
    "heading",
    "list_item",
    "table",
    "page_header",
    "page_footer",
    "figure",
    "caption",
]

TableRow = tuple[tuple[Span, ...], ...]
"""One table row: one tuple of spans per cell."""


@dataclass(frozen=True)
class Block:
    """One block-level element of a :class:`FormattedDocument`.

    ``spans`` carries the text of paragraph-like blocks.  For ``"figure"`` blocks the
    spans hold the alt text; for ``"table"`` blocks ``rows`` holds the cells and the first
    row is the header.  ``level`` is the heading level (1-6) or the list nesting depth
    (0 = top level).  ``number`` is the explicit number of an ordered list item (so a list
    that starts at 3 keeps its numbers); ``None`` continues counting from the previous item.
    """

    kind: BlockKind
    spans: tuple[Span, ...] = ()
    level: int = 0
    ordered: bool = False
    number: int | None = None
    rows: tuple[TableRow, ...] = ()

    @property
    def text(self) -> str:
        """Unstyled text of the block's spans."""
        return "".join(span.text for span in self.spans)


@dataclass(frozen=True)
class FormattedDocument:
    """An ordered sequence of :class:`Block` values."""

    blocks: tuple[Block, ...] = field(default_factory=tuple)


def merge_spans(spans: list[Span]) -> tuple[Span, ...]:
    """Drop empty spans and join neighbours that share a style."""
    merged: list[Span] = []
    for span in spans:
        if not span.text:
            continue
        if merged and merged[-1].style == span.style:
            merged[-1] = Span(merged[-1].text + span.text, span.style)
        else:
            merged.append(span)
    return tuple(merged)


def strip_spans(spans: tuple[Span, ...]) -> tuple[Span, ...]:
    """Strip whitespace from the outer edges of a span sequence."""
    items = list(spans)
    while items and not items[0].text.strip():
        items.pop(0)
    while items and not items[-1].text.strip():
        items.pop()
    if not items:
        return ()
    items[0] = Span(items[0].text.lstrip(), items[0].style)
    items[-1] = Span(items[-1].text.rstrip(), items[-1].style)
    return tuple(items)


class _ListNumbering:
    """Tracks the ordered-list counters while blocks are walked in document order."""

    def __init__(self) -> None:
        self._counters: dict[int, int] = {}
        self._last_ordered: dict[int, bool] = {}

    def reset(self) -> None:
        """Forget all lists (a non-list block ends them)."""
        self._counters.clear()
        self._last_ordered.clear()

    def implicit_number(self, block: Block) -> int:
        """Return the number *block* would get if it carried no explicit ``number``."""
        if self._last_ordered.get(block.level) is not True:
            return 1
        return self._counters.get(block.level, 0) + 1

    def advance(self, block: Block) -> int | None:
        """Consume the list item *block*; return its number (``None`` for a bullet)."""
        level = block.level
        for deeper in [lvl for lvl in self._counters if lvl > level]:
            del self._counters[deeper]
            self._last_ordered.pop(deeper, None)
        number: int | None = None
        if block.ordered:
            number = block.number if block.number is not None else self.implicit_number(block)
            self._counters[level] = number
        self._last_ordered[level] = block.ordered
        return number


def list_markers(blocks: tuple[Block, ...]) -> Iterator[tuple[Block, str]]:
    """Yield ``(block, marker)``; *marker* is ``"- "`` / ``"3. "`` for list items, else ``""``."""
    numbering = _ListNumbering()
    for block in blocks:
        if block.kind != "list_item":
            numbering.reset()
            yield block, ""
            continue
        number = numbering.advance(block)
        yield block, "- " if number is None else f"{number}. "


def compact_list_numbers(blocks: tuple[Block, ...]) -> tuple[Block, ...]:
    """Drop explicit item numbers that :func:`list_markers` would count to anyway.

    Parsers record every item's number; keeping only the ones that differ from plain
    counting (a list starting at 3, a list resumed after an interrupting paragraph) leaves
    ordinary lists identical to models built without numbers.
    """
    numbering = _ListNumbering()
    out: list[Block] = []
    for block in blocks:
        if block.kind != "list_item":
            numbering.reset()
        else:
            if block.ordered and block.number == numbering.implicit_number(block):
                block = dataclasses.replace(block, number=None)  # noqa: PLW2901  # normalised copy
            numbering.advance(block)
        out.append(block)
    return tuple(out)


INLINE_TAG_STYLE: dict[str, Style] = {
    "b": Style.BOLD,
    "strong": Style.BOLD,
    "i": Style.ITALIC,
    "em": Style.ITALIC,
    "u": Style.UNDERLINE,
    "ins": Style.UNDERLINE,
    "s": Style.STRIKE,
    "del": Style.STRIKE,
    "strike": Style.STRIKE,
    "sup": Style.SUPERSCRIPT,
    "sub": Style.SUBSCRIPT,
}
"""HTML inline tag name -> the :class:`Style` it switches on (shared by all parsers)."""

LIST_INDENT = "    "
"""Indentation of one list nesting level in flattened and Markdown text."""


def flatten_block(block: Block, marker: str = "") -> tuple[Span, ...]:
    """Return *block* as plain-looking spans: tables, figures and list markers become text."""
    if block.kind == "table":
        spans: list[Span] = []
        for row in (row for row in block.rows if row):
            if spans:
                spans.append(Span("\n"))
            for cell_index, cell in enumerate(row):
                if cell_index:
                    spans.append(Span(" | "))
                spans.extend(strip_spans(cell))
        return tuple(spans)
    if block.kind == "figure":
        return (Span(f"[{block.text.strip() or 'image'}]"),)
    spans = strip_spans(block.spans)
    if block.kind == "list_item" and spans:
        return (Span(LIST_INDENT * block.level + marker), *spans)
    return spans


def flat_chunks(doc: FormattedDocument) -> Iterator[tuple[tuple[Span, ...], bool]]:
    """Yield ``(spans, tight)`` for every non-empty block of *doc*, flattened.

    *tight* is ``True`` for a list item that directly follows another list item: such
    chunks belong on the next line, every other chunk is separated by a blank line.
    """
    previous_was_item = False
    for block, marker in list_markers(doc.blocks):
        spans = flatten_block(block, marker)
        if "".join(span.text for span in spans).strip():
            is_item = block.kind == "list_item"
            yield spans, is_item and previous_was_item
            previous_was_item = is_item


def join_chunks(chunks: Iterable[tuple[str, bool]]) -> str:
    """Join rendered ``(text, tight)`` chunks: a tight chunk follows on the next line."""
    out: list[str] = []
    for index, (text, tight) in enumerate(chunks):
        if index:
            out.append("\n" if tight else "\n\n")
        out.append(text)
    return "".join(out)
