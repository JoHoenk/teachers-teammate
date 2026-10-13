"""Parser for Chandra OCR's layout HTML.

Chandra (Datalab) emits one ``<div data-bbox="x0 y0 x1 y1" data-label="Text">`` per layout
block, with ordinary inline HTML (``<b>``, ``<sup>``, ``<del>``, ``<img alt>`` ...) inside.
The attributes are a Datalab convention, not an OCR standard; this parser maps ``data-label``
onto the format-neutral document model (the bounding boxes are not kept).

:func:`parse_html_blocks` exposes the generic HTML handling (paragraphs, headings, lists,
tables, inline styles) for HTML embedded in other notations, e.g. Markdown HTML blocks.
"""

from __future__ import annotations

import re
from typing import ClassVar

from lxml import etree
import lxml.html

from ...domain.formatted_text import (
    INLINE_TAG_STYLE,
    Block,
    BlockKind,
    FormattedDocument,
    Span,
    Style,
    TableRow,
    compact_list_numbers,
    merge_spans,
    strip_spans,
)
from ...interfaces import OcrOutputParser

_SIGNAL_RE = re.compile(r"<[a-zA-Z][^>]*\sdata-(?:bbox|label)\s*=", re.IGNORECASE)

# data-label -> block kind.  Unknown labels fall back to a plain paragraph.
_LABEL_KIND: dict[str, BlockKind] = {
    "text": "paragraph",
    "section-header": "heading",
    "title": "heading",
    "page-header": "page_header",
    "page-footer": "page_footer",
    "caption": "caption",
    "picture": "figure",
    "figure": "figure",
    "image": "figure",
    "diagram": "figure",
    "table": "table",
    "list-group": "list_item",
}

_BLOCK_TAGS = frozenset(
    {"p", "div", "ul", "ol", "table", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote"}
)
# Elements that start a new line when they are nested inside inline content (a table cell
# or list item holding several paragraphs).
_LINE_TAGS = _BLOCK_TAGS | {"li"}
_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_DEFAULT_HEADING_LEVEL = 2
_WHITESPACE_RE = re.compile(r"\s+")
_AROUND_BREAK_RE = re.compile(r" *\n *")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text)


def _tag(element: etree._Element) -> str | None:
    return element.tag.lower() if isinstance(element.tag, str) else None


def _list_start(element: etree._Element) -> int:
    """Return the number of an ``<ol>``'s first item (its ``start`` attribute, default 1)."""
    try:
        return int(element.get("start", "1"))
    except ValueError:
        return 1


class _Inline:
    """Spans and image alts gathered from one element's inline content."""

    def __init__(self) -> None:
        self.spans: list[Span] = []
        self.figures: list[str] = []

    def _line_break(self) -> None:
        """End the current line (once), dropping blanks before it."""
        while self.spans and not self.spans[-1].text.strip(" "):
            self.spans.pop()
        if self.spans and not self.spans[-1].text.endswith("\n"):
            last = self.spans[-1]
            self.spans[-1] = Span(last.text.rstrip(" "), last.style)
            self.spans.append(Span("\n"))

    def collect(self, element: etree._Element, style: Style, *, skip_lists: bool = False) -> None:
        if element.text:
            self.spans.append(Span(_collapse(element.text), style))
        for child in element:
            tag = _tag(child)
            if tag == "br":
                self.spans.append(Span("\n", style))
            elif tag == "img":
                self.figures.append(child.get("alt", ""))
            elif tag is not None and not (skip_lists and tag in ("ul", "ol")):
                own_line = tag in _LINE_TAGS
                if own_line:
                    self._line_break()
                self.collect(
                    child, style | INLINE_TAG_STYLE.get(tag, Style.NONE), skip_lists=skip_lists
                )
                if own_line:
                    self._line_break()
            if child.tail:
                self.spans.append(Span(_collapse(child.tail), style))

    def result(self) -> tuple[Span, ...]:
        """Return the spans with whitespace tidied around line breaks and block edges."""
        tidy: list[Span] = []
        previous = "\n"  # the start of a block counts as a break
        for span in merge_spans(self.spans):
            text = _AROUND_BREAK_RE.sub("\n", span.text)
            if previous in " \n":
                text = text.lstrip(" ")
            if not text:
                continue
            if text.startswith("\n") and tidy and tidy[-1].text.endswith(" "):
                tidy[-1] = Span(tidy[-1].text.rstrip(" "), tidy[-1].style)
            tidy.append(Span(text, span.style))
            previous = text[-1]
        return strip_spans(merge_spans(tidy))


class ChandraHtmlParser(OcrOutputParser):
    """Parses Chandra's ``data-bbox`` / ``data-label`` HTML into the document model."""

    name: ClassVar[str] = "chandra_html"

    def can_parse(self, text: str) -> bool:
        return _SIGNAL_RE.search(text) is not None

    def parse(self, text: str) -> FormattedDocument:
        return FormattedDocument(self.parse_blocks(text))

    def parse_blocks(self, text: str) -> tuple[Block, ...]:
        """Parse an HTML fragment to blocks (the ``data-*`` attributes are optional)."""
        root = lxml.html.fragment_fromstring(text, create_parent="div")
        blocks: list[Block] = []
        self._container(root, kind=None, blocks=blocks)
        return compact_list_numbers(tuple(blocks))

    # ── containers ─────────────────────────────────────────────────────────

    def _container(
        self, element: etree._Element, *, kind: BlockKind | None, blocks: list[Block]
    ) -> None:
        """Emit blocks for *element*'s content, flushing inline runs at block children."""
        pending = _Inline()
        if element.text:
            pending.spans.append(Span(_collapse(element.text)))

        def flush() -> None:
            self._emit_inline(pending, kind, blocks)
            pending.spans.clear()
            pending.figures.clear()

        for child in element:
            tag = _tag(child)
            if tag in _BLOCK_TAGS:
                flush()
                self._block(child, kind=kind, blocks=blocks)
            elif tag == "img":
                pending.figures.append(child.get("alt", ""))
            elif tag == "br":
                pending.spans.append(Span("\n"))
            elif tag is not None:
                pending.collect(child, INLINE_TAG_STYLE.get(tag, Style.NONE))
            if child.tail:
                pending.spans.append(Span(_collapse(child.tail)))
        flush()

    def _block(
        self, element: etree._Element, *, kind: BlockKind | None, blocks: list[Block]
    ) -> None:
        tag = _tag(element)
        label = (element.get("data-label") or "").lower()
        own_kind = _LABEL_KIND.get(label, "paragraph") if label else kind
        if tag in ("div", "blockquote"):
            self._container(element, kind=own_kind, blocks=blocks)
        elif tag in ("ul", "ol"):
            self._list(element, level=0, blocks=blocks)
        elif tag == "table":
            blocks.append(Block("table", rows=self._rows(element)))
        elif tag in _HEADING_TAGS:
            inline = _Inline()
            inline.collect(element, Style.NONE)
            self._emit_figures(inline, blocks)
            spans = inline.result()
            if spans:
                blocks.append(Block("heading", spans, level=int(tag[1])))
        else:  # p, pre
            inline = _Inline()
            inline.collect(element, Style.NONE)
            self._emit_inline(inline, own_kind, blocks)

    @staticmethod
    def _emit_figures(inline: _Inline, blocks: list[Block]) -> None:
        blocks.extend(Block("figure", (Span(alt),)) for alt in inline.figures)

    def _emit_inline(self, inline: _Inline, kind: BlockKind | None, blocks: list[Block]) -> None:
        self._emit_figures(inline, blocks)
        spans = inline.result()
        if not spans:
            return
        if kind == "heading":
            blocks.append(Block("heading", spans, level=_DEFAULT_HEADING_LEVEL))
        elif kind in ("page_header", "page_footer", "caption", "figure"):
            blocks.append(Block(kind, spans))
        else:
            blocks.append(Block("paragraph", spans))

    # ── lists and tables ───────────────────────────────────────────────────

    def _list(self, element: etree._Element, *, level: int, blocks: list[Block]) -> None:
        ordered = _tag(element) == "ol"
        number = _list_start(element) if ordered else None
        for item in element:
            if _tag(item) != "li":
                continue
            inline = _Inline()
            inline.collect(item, Style.NONE, skip_lists=True)
            spans = inline.result()
            if spans:
                blocks.append(
                    Block("list_item", spans, level=level, ordered=ordered, number=number)
                )
            if number is not None:
                number += 1
            for nested in item:
                if _tag(nested) in ("ul", "ol"):
                    self._list(nested, level=level + 1, blocks=blocks)

    @staticmethod
    def _rows(table: etree._Element) -> tuple[TableRow, ...]:
        rows: list[TableRow] = []
        for row in table.iter("tr"):
            cells: list[tuple[Span, ...]] = []
            for cell in row:
                if _tag(cell) in ("td", "th"):
                    inline = _Inline()
                    inline.collect(cell, Style.NONE)
                    cells.append(inline.result())
            if cells:
                rows.append(tuple(cells))
        return tuple(rows)


def parse_html_blocks(text: str) -> tuple[Block, ...]:
    """Parse an HTML fragment (paragraphs, headings, lists, tables, inline styles) to blocks."""
    return ChandraHtmlParser().parse_blocks(text)
