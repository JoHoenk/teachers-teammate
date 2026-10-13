"""Notation-aware text queries for adapters (GUI, CLI).

Stored OCR/correction text is either plain text or Markdown (see
:mod:`teachers_teammate.domain.formatted_text`).  Adapters must not import the parsers in
``infrastructure/ocr_formats`` themselves; they ask :class:`TextFormatter` for the view they
need: the structured document (to display), the markup-free text (to diff or measure) or the
canonical Markdown (to store an edit).

Parser imports are lazy so importing this module does not require ``markdown-it-py``/``lxml``.
"""

from __future__ import annotations

from functools import lru_cache

from ..domain.formatted_text import FormattedDocument, TextFormat, normalize_text_format

# Parsing is the expensive step and the same stored text is asked for repeatedly (to show,
# to diff, to count words); results are immutable, so they are memoised by text and format.
_CACHE_SIZE = 128


@lru_cache(maxsize=_CACHE_SIZE)
def _document(text: str, text_format: TextFormat) -> FormattedDocument:
    from ..infrastructure.ocr_formats import parse_stored_text  # noqa: PLC0415

    return parse_stored_text(text, text_format)


@lru_cache(maxsize=_CACHE_SIZE)
def _markdown_plain_text(text: str) -> str:
    from ..infrastructure.ocr_formats import plain_text_of  # noqa: PLC0415

    return plain_text_of(text, "markdown")


class TextFormatter:
    """Parses and renders stored text according to its notation flag."""

    @staticmethod
    def normalize(text_format: str | None) -> TextFormat:
        """Return *text_format* as a valid :data:`TextFormat` (unknown -> ``"plain"``)."""
        return normalize_text_format(text_format)

    @staticmethod
    def document(text: str, text_format: str | None) -> FormattedDocument:
        """Return the structured document for stored *text*."""
        return _document(text, normalize_text_format(text_format))

    @staticmethod
    def plain_text(text: str, text_format: str | None) -> str:
        """Return *text* without markup; crossed-out words are kept (diffs, metrics)."""
        if normalize_text_format(text_format) != "markdown":
            return text
        return _markdown_plain_text(text)

    @staticmethod
    def markdown(doc: FormattedDocument) -> str:
        """Return *doc* as canonical Markdown (what edits are stored as)."""
        from ..infrastructure.ocr_formats import MarkdownRenderer  # noqa: PLC0415

        return MarkdownRenderer().render(doc)
