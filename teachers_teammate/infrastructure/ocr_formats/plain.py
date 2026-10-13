"""Fallback parser: verbatim text split into paragraphs at blank lines."""

from __future__ import annotations

import re
from typing import ClassVar

from ...domain.formatted_text import Block, FormattedDocument, Span
from ...interfaces import OcrOutputParser

_BLANK_LINE_RE = re.compile(r"\n(?:[ \t]*\n)+")


class PlainTextParser(OcrOutputParser):
    """Accepts any text; every blank-line-separated chunk becomes one paragraph."""

    name: ClassVar[str] = "plain"

    def can_parse(self, text: str) -> bool:  # noqa: ARG002  # interface parameter; always accepts
        return True

    def parse(self, text: str) -> FormattedDocument:
        blocks = [
            Block("paragraph", (Span(chunk.rstrip()),))
            for chunk in _BLANK_LINE_RE.split(text.strip("\n"))
            if chunk.strip()
        ]
        return FormattedDocument(tuple(blocks))
