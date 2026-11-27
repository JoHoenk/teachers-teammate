"""Where the cursor of a text stands on the page, and the mark the scan draws for it.

The cursor moves with every arrow key, so a view's own text is diffed against the transcription
once per pair of texts, and the pair is remembered: nothing is diffed while the teacher reads.
All the placing is the application's (:func:`~teachers_teammate.application.check_document.
mark_for_cursor`); this module only says which text a view shows and carries its offset over.
"""

from __future__ import annotations

from functools import lru_cache

from ...application.check_document import CheckDocument, mark_for_cursor
from ...domain.page_layout import Mark
from ...domain.proofreading.word_diff import OffsetMap
from ._masking import Masking

#: How many pairs of texts are remembered: the open document, and what a hiding made of it.
_CACHE_SIZE = 16


def mark_in_transcription(doc: CheckDocument, offset: int, text: str) -> Mark | None:
    """Return the mark for the plain *offset* of *text*, a view's copy of the transcription.

    *text* is what a view shows for the transcription: the text itself, or the string a request
    is made from, with the placeholders in it.  ``None`` where the place is not sure, which is
    where the scan shows the page.
    """
    if text == doc.raw_plain:
        return mark_for_cursor(doc, offset)
    return mark_for_cursor(doc, _to_transcription(text, doc.raw_plain).forward(offset))


def mark_in_corrected(doc: CheckDocument, offset: int, masking: Masking) -> Mark | None:
    """Return the mark for the plain *offset* of a view's corrected text, placeholders and all."""
    return mark_for_cursor(doc, masking.back(offset, doc.corrected_plain), corrected=True)


@lru_cache(maxsize=_CACHE_SIZE)
def _to_transcription(text: str, raw_plain: str) -> OffsetMap:
    """Map a view's copy of the transcription onto the transcription, once per pair."""
    return OffsetMap(text, raw_plain)
