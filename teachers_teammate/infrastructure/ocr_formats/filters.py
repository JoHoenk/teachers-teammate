"""Document filters applied after parsing OCR output."""

from __future__ import annotations

import re

from ...domain.formatted_text import Block, FormattedDocument
from ...interfaces import DocumentFilter

# A roman numeral up to 99, in one case only: "iv" and "XIV" are numerals, "Li" and "Mix" are
# names.  ``D`` and ``M`` are left out so words made of roman letters ("Didi", "Mimi") never match.
_ROMAN = r"(?=[ivxlc])(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
_ROMAN_PAGE = rf"(?-i:{_ROMAN}|{_ROMAN.upper()})"

# "3", "- 3 -", "Seite 3", "Page 3 of 10", "3/10", "iv": text that only numbers the page.
_PAGE_NUMBER_RE = re.compile(
    rf"""^\W*
        (?:(?:page|seite|s\.|p\.)\s*)?
        (?:\d{{1,4}}|{_ROMAN_PAGE})
        (?:\s*(?:/|of|von)\s*\d{{1,4}})?
        \W*$""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_page_number(block: Block) -> bool:
    return block.kind in ("page_header", "page_footer") and bool(
        _PAGE_NUMBER_RE.match(block.text.strip())
    )


class DropPageFurniture(DocumentFilter):
    """Remove page numbers from headers and footers.

    Other header/footer text (a student's name, a running title) is content the teacher
    wants to see, so it stays.  A page that holds nothing else keeps its number rather than
    turning into an empty page.
    """

    def apply(self, doc: FormattedDocument) -> FormattedDocument:
        kept = tuple(block for block in doc.blocks if not _is_page_number(block))
        if not any(block.text.strip() or block.rows or block.kind == "figure" for block in kept):
            return doc
        return FormattedDocument(kept)
