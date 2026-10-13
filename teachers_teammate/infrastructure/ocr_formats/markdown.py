"""Markdown parser built on ``markdown-it-py``.

Understands CommonMark plus GFM strikethrough and tables, and the inline HTML tags the
canonical notation uses for styles Markdown lacks (``<sup>``, ``<sub>``, ``<u>``).
Many vision-language models emit Markdown natively, so this is also the parser for the
text the app stores itself.
"""

from __future__ import annotations

from collections import Counter
import dataclasses
from dataclasses import dataclass
import re
from typing import TYPE_CHECKING, ClassVar

from lxml import etree
from markdown_it import MarkdownIt

from ...domain.formatted_text import (
    INLINE_TAG_STYLE,
    Block,
    FormattedDocument,
    Span,
    Style,
    TableRow,
    compact_list_numbers,
    merge_spans,
    strip_spans,
)
from ...interfaces import OcrOutputParser
from .chandra_html import parse_html_blocks

if TYPE_CHECKING:
    from markdown_it.token import Token

# Constructs that mark text as Markdown.  Deliberately conservative -- a bare "- item"
# list or a stray "*" is common in plain OCR output and must not trigger parsing.
_SIGNAL_RE = re.compile(
    r"""
      \*\*\S(?:.*?\S)?\*\*                                  # **bold**
    | ~~\S(?:.*?\S)?~~                                      # ~~struck~~
    | ^\#{1,6}[ \t]+\S                                      # # heading
    | ^[ \t]*\|?[ \t]*:?-{3,}:?[ \t]*(?:\|[ \t]*:?-{3,}:?[ \t]*)+\|?[ \t]*$   # table rule
    | </?(?:sup|sub|u|b|i|em|strong|del|s)>                 # inline style tags
    | !\[[^\]]*\]\([^)]*\)                                  # image
    """,
    re.MULTILINE | re.VERBOSE | re.IGNORECASE,
)

_HTML_TAG_RE = re.compile(r"<\s*(/)?\s*([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>")

_TOKEN_STYLE: dict[str, Style] = {
    "strong": Style.BOLD,
    "em": Style.ITALIC,
    "s": Style.STRIKE,
}

_parser = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])

# Info strings of a fence that merely wraps the whole answer (chat models like to write
# ```markdown ... ```); any other language means the page really is a code block.
_WRAPPER_INFO = frozenset({"", "markdown", "md", "html"})


def unwrap_fenced_page(text: str) -> str:
    """Return the content of *text* if one code fence wraps all of it, else *text* unchanged."""
    tokens = _parser.parse(text)
    if len(tokens) != 1 or tokens[0].type != "fence":
        return text
    if tokens[0].info.strip().lower() not in _WRAPPER_INFO:
        return text
    return tokens[0].content


class _InlineState:
    """Accumulates spans while walking the children of one ``inline`` token."""

    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.figures: list[str] = []
        self._open: Counter[Style] = Counter()

    @property
    def style(self) -> Style:
        style = Style.NONE
        for flag, count in self._open.items():
            if count > 0:
                style |= flag
        return style

    def toggle(self, flag: Style, *, opening: bool) -> None:
        self._open[flag] = max(self._open[flag] + (1 if opening else -1), 0)

    def add(self, text: str) -> None:
        if text:
            self.spans.append(Span(text, self.style))


def _parse_inline(token: Token) -> _InlineState:
    state = _InlineState()
    for child in token.children or []:
        kind = child.type
        if kind in ("text", "text_special", "code_inline"):
            state.add(child.content)
        elif kind in ("softbreak", "hardbreak"):
            state.add("\n")
        elif kind.endswith(("_open", "_close")) and kind.rsplit("_", 1)[0] in _TOKEN_STYLE:
            flag = _TOKEN_STYLE[kind.rsplit("_", 1)[0]]
            state.toggle(flag, opening=kind.endswith("_open"))
        elif kind == "html_inline":
            match = _HTML_TAG_RE.match(child.content)
            if match is None:
                continue
            tag = match.group(2).lower()
            if tag == "br":
                state.add("\n")
            elif tag in INLINE_TAG_STYLE:
                state.toggle(INLINE_TAG_STYLE[tag], opening=match.group(1) is None)
        elif kind == "image":
            state.figures.append(child.content)
    return state


def _spans_of(token: Token) -> tuple[tuple[Span, ...], list[str]]:
    state = _parse_inline(token)
    return strip_spans(merge_spans(state.spans)), state.figures


@dataclass
class _ListState:
    """One open list while walking the token stream."""

    ordered: bool
    next_number: int = 1
    item_number: int | None = None


class _BlockBuilder:
    """Collects blocks while the token stream is walked, tracking open lists and items."""

    def __init__(self) -> None:
        self.blocks: list[Block] = []
        self._lists: list[_ListState] = []
        self._item_pending = False
        # Per open list item: index in *blocks* of the block holding its first paragraph.
        self._item_blocks: list[int | None] = []

    def open_list(self, *, ordered: bool, start: int) -> None:
        self._lists.append(_ListState(ordered, next_number=start))

    def close_list(self) -> None:
        if self._lists:
            self._lists.pop()

    def open_item(self) -> None:
        self._item_pending = True
        self._item_blocks.append(None)
        if self._lists:
            current = self._lists[-1]
            current.item_number = current.next_number if current.ordered else None
            current.next_number += 1

    def close_item(self) -> None:
        if self._item_blocks:
            self._item_blocks.pop()

    def paragraph(self, spans: tuple[Span, ...], figures: list[str]) -> None:
        """Add a paragraph token's content: a list item, an item continuation or a paragraph."""
        if self._continues_item(spans, figures):
            # A further paragraph of the same item stays in the item (as a line break), so
            # the stored Markdown does not move it out of the list.
            first = self.blocks[-1]
            self.blocks[-1] = dataclasses.replace(
                first, spans=merge_spans([*first.spans, Span("\n"), *spans])
            )
        else:
            self.blocks.extend(Block("figure", (Span(alt),)) for alt in figures)
            if spans and self._lists and self._item_pending:
                current = self._lists[-1]
                self.blocks.append(
                    Block(
                        "list_item",
                        spans,
                        level=len(self._lists) - 1,
                        ordered=current.ordered,
                        number=current.item_number,
                    )
                )
                if self._item_blocks:
                    self._item_blocks[-1] = len(self.blocks) - 1
            elif spans:
                self.blocks.append(Block("paragraph", spans))
        self._item_pending = False

    def _continues_item(self, spans: tuple[Span, ...], figures: list[str]) -> bool:
        """``True`` if a paragraph is a further paragraph of the list item emitted last.

        Only when nothing was emitted since the item's first paragraph (a nested list in
        between would be reordered otherwise).
        """
        return (
            bool(spans)
            and not figures
            and not self._item_pending
            and bool(self._item_blocks)
            and self._item_blocks[-1] is not None
            and self._item_blocks[-1] == len(self.blocks) - 1
        )


def _html_blocks(content: str) -> tuple[Block, ...]:
    """Parse a raw HTML block (e.g. a ``<table>`` a model embedded) into blocks."""
    if not content.strip():
        return ()
    try:
        return parse_html_blocks(content)
    except (etree.LxmlError, ValueError):
        return (Block("paragraph", (Span(content.strip()),)),)


class MarkdownParser(OcrOutputParser):
    """Parses Markdown (with inline ``<sup>``/``<sub>``/``<u>``) into the document model."""

    name: ClassVar[str] = "markdown"

    def can_parse(self, text: str) -> bool:
        return _SIGNAL_RE.search(text) is not None

    def parse(self, text: str) -> FormattedDocument:
        tokens = _parser.parse(text)
        builder = _BlockBuilder()
        blocks = builder.blocks
        index = 0
        while index < len(tokens):
            token = tokens[index]
            kind = token.type
            if kind in ("bullet_list_open", "ordered_list_open"):
                ordered = kind == "ordered_list_open"
                start = int(token.attrs.get("start", 1)) if ordered else 1
                builder.open_list(ordered=ordered, start=start)
            elif kind in ("bullet_list_close", "ordered_list_close"):
                builder.close_list()
            elif kind == "list_item_open":
                builder.open_item()
            elif kind == "list_item_close":
                builder.close_item()
            elif kind == "heading_open":
                spans, figures = _spans_of(tokens[index + 1])
                blocks.extend(Block("figure", (Span(alt),)) for alt in figures)
                blocks.append(Block("heading", spans, level=int(token.tag[1:])))
                index += 2
            elif kind == "paragraph_open":
                builder.paragraph(*_spans_of(tokens[index + 1]))
                index += 2
            elif kind == "table_open":
                block, index = self._parse_table(tokens, index)
                blocks.append(block)
            elif kind == "html_block":
                blocks.extend(_html_blocks(token.content))
            elif kind in ("fence", "code_block"):
                content = token.content.strip("\n")
                if content.strip():
                    blocks.append(Block("paragraph", (Span(content),)))
            index += 1
        return FormattedDocument(compact_list_numbers(tuple(blocks)))

    @staticmethod
    def _parse_table(tokens: list[Token], start: int) -> tuple[Block, int]:
        rows: list[TableRow] = []
        cells: list[tuple[Span, ...]] = []
        index = start + 1
        while index < len(tokens) and tokens[index].type != "table_close":
            kind = tokens[index].type
            if kind == "tr_open":
                cells = []
            elif kind == "tr_close":
                rows.append(tuple(cells))
            elif kind == "inline":
                spans, _figures = _spans_of(tokens[index])
                cells.append(spans)
            index += 1
        return Block("table", rows=tuple(rows)), index
