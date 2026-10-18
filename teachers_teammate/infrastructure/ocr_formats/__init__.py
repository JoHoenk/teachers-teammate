"""Format-aware post-processing of raw OCR output.

Engines answer in different notations: plain text (Tesseract, Paddle), Markdown (many
vision-language models) or layout HTML (Chandra).  :class:`OcrOutputPostprocessor` detects
the notation per page, parses it into a
:class:`~teachers_teammate.domain.formatted_text.FormattedDocument`, applies filters
(e.g. drop page numbers) and renders the canonical stored form:

* all pages plain  -> text unchanged, ``text_format == "plain"``
* anything richer  -> Markdown, ``text_format == "markdown"``

Adding a notation (hOCR, DocTags, ...) means adding one :class:`OcrOutputParser` module here
and listing it in :func:`default_parsers`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import logging

from ...domain.formatted_text import (
    FormattedDocument,
    TextFormat,
    join_labelled_pages,
    normalize_text_format,
)
from ...interfaces import DocumentFilter, OcrOutputParser, TextRenderer
from ..ocr_text_cleaner import clean_ocr_text
from .chandra_html import ChandraHtmlParser
from .filters import DropPageFurniture
from .markdown import MarkdownParser, unwrap_fenced_page
from .plain import PlainTextParser
from .renderers import MarkdownRenderer, PlainTextRenderer

_logger = logging.getLogger(__name__)

__all__ = [
    "ChandraHtmlParser",
    "DropPageFurniture",
    "MarkdownParser",
    "MarkdownRenderer",
    "OcrOutputPostprocessor",
    "PlainTextParser",
    "PlainTextRenderer",
    "ProcessedOcrText",
    "clean_llm_answer",
    "default_filters",
    "default_parsers",
    "looks_like_markup",
    "normalize_llm_answer",
    "parse_stored_text",
    "plain_text_of",
    "upgrade_legacy_text",
]


def default_parsers() -> tuple[OcrOutputParser, ...]:
    """Return the parsers tried in order; the always-matching plain parser comes last."""
    return (ChandraHtmlParser(), MarkdownParser(), PlainTextParser())


def default_filters() -> tuple[DocumentFilter, ...]:
    """Return the filters applied to every parsed page."""
    return (DropPageFurniture(),)


@dataclass(frozen=True)
class ProcessedOcrText:
    """Result of post-processing the OCR output of one document."""

    text: str
    """Canonical text to store and show (plain verbatim, or Markdown)."""
    text_format: TextFormat


class OcrOutputPostprocessor:
    """Detects, parses, filters and renders OCR output into the canonical stored form."""

    def __init__(
        self,
        parsers: Sequence[OcrOutputParser] | None = None,
        filters: Sequence[DocumentFilter] | None = None,
        renderer: TextRenderer | None = None,
    ) -> None:
        self._parsers = tuple(parsers) if parsers is not None else default_parsers()
        self._filters = tuple(filters) if filters is not None else default_filters()
        self._renderer = renderer or MarkdownRenderer()
        self._fallback = PlainTextParser()

    def parse_page(self, text: str) -> tuple[FormattedDocument, str]:
        """Return ``(document, parser_name)`` using the first parser that accepts *text*.

        A code fence around the whole answer (chat models wrap Markdown/HTML in one) is
        looked through.
        """
        _text, doc, name = self._detect(text)
        return doc, name

    def _detect(self, text: str) -> tuple[str, FormattedDocument, str]:
        """Return ``(page_text, document, parser_name)``; *page_text* has no wrapping fence.

        The fence is dropped even when what it wraps is plain text, so a plain answer is not
        stored with its backticks.
        """
        unwrapped = unwrap_fenced_page(text)
        if unwrapped != text:
            text = unwrapped.strip()
        parsed = self._parse_with_parsers(text)
        if parsed is not None:
            return text, *parsed
        return text, self._fallback.parse(text), self._fallback.name

    def _parse_with_parsers(self, text: str) -> tuple[FormattedDocument, str] | None:
        """Parse with the first non-fallback parser that accepts *text* (``None`` if none)."""
        for parser in self._parsers:
            if parser.name == self._fallback.name or not parser.can_parse(text):
                continue
            try:
                return parser.parse(text), parser.name
            except Exception:  # noqa: BLE001  # a malformed page must degrade to plain text, never fail OCR
                _logger.warning("OCR output parser %r failed; trying the next one", parser.name)
        return None

    def process_pages(self, pages: Sequence[str], *, label_pages: bool = True) -> ProcessedOcrText:
        """Post-process the cleaned model output of every page of one document.

        Args:
            pages: One cleaned model output per page.
            label_pages: Prefix multi-page text with ``Page N:`` lines (the pipeline format).
                When false, pages are separated by a blank line only.
        """
        parsed = [self._detect(page) for page in pages]
        if all(name == self._fallback.name for _text, _doc, name in parsed):
            texts = [text for text, _doc, _name in parsed]
            plain = self._join(texts, label_pages=label_pages, markdown=False)
            return ProcessedOcrText(plain, "plain")

        rendered: list[str] = []
        for _text, page_doc, _name in parsed:
            for doc_filter in self._filters:
                page_doc = doc_filter.apply(page_doc)  # noqa: PLW2901  # filters fold left over the page
            rendered.append(self._renderer.render(page_doc))
        text = self._join(rendered, label_pages=label_pages, markdown=True)
        return ProcessedOcrText(text, "markdown")

    @staticmethod
    def _join(pages: list[str], *, label_pages: bool, markdown: bool) -> str:
        if len(pages) == 1:
            return pages[0]
        if not label_pages:
            return "\n\n".join(pages)
        return join_labelled_pages(pages, markdown=markdown)


def parse_stored_text(text: str, text_format: str | None) -> FormattedDocument:
    """Parse stored *text* (as persisted with its format flag) into the document model."""
    if normalize_text_format(text_format) == "markdown":
        return MarkdownParser().parse(text)
    return PlainTextParser().parse(text)


def plain_text_of(text: str, text_format: str | None) -> str:
    """Return stored *text* without markup (crossed-out words are kept).

    Plain text is returned untouched; Markdown is parsed and re-rendered unstyled.
    """
    if normalize_text_format(text_format) != "markdown":
        return text
    return PlainTextRenderer().render(MarkdownParser().parse(text))


def looks_like_markup(text: str) -> bool:
    """``True`` if any rich-notation parser (Markdown, layout HTML) recognises *text*.

    A cheap signal check, for text stored before notations were tracked.
    """
    return any(
        parser.can_parse(text)
        for parser in default_parsers()
        if parser.name != PlainTextParser.name
    )


def upgrade_legacy_text(text: str) -> ProcessedOcrText:
    """Post-process *text* that was stored before notations were tracked, as one page."""
    return OcrOutputPostprocessor().process_pages([text], label_pages=False)


def clean_llm_answer(answer: str) -> str:
    """Strip reasoning blocks, control tokens and a code fence around a whole *answer*.

    What every chat answer (proofreading, review) needs before it is stored, whatever
    notation the text is in.
    """
    cleaned = clean_ocr_text(answer)
    if not cleaned:
        return cleaned
    return unwrap_fenced_page(cleaned).strip()


def normalize_llm_answer(answer: str, text_format: str | None, *, source: str = "") -> str:
    """Return a proofreading *answer* in the notation of the text it answers.

    On top of :func:`clean_llm_answer`:

    * Markdown text: the model was told its input is Markdown, so the answer is read as
      Markdown and written back canonically (HTML tags, odd list markers, ... included).
    * plain text: markup the model added although *source* had none is removed, so the
      answer does not show stray asterisks.
    """
    cleaned = clean_llm_answer(answer)
    if not cleaned:
        return cleaned
    if normalize_text_format(text_format) == "markdown":
        return MarkdownRenderer().render(MarkdownParser().parse(cleaned))
    if source and looks_like_markup(cleaned) and not looks_like_markup(source):
        return plain_text_of(cleaned, "markdown")
    return cleaned
