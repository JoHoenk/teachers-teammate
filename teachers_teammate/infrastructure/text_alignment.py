"""Where a page's lines stand in the text that was read from the same page.

The fuzzy step of the scan link happens once, while reading, between two readings of one page:
the lines and boxes the built-in reader finds, and the text the run stored — the built-in
reader's own, or an Ollama reader's.  Everything after it is between clean texts and uses the
word diff the app already has.

The alignment is made on characters, not on words: a misread word shares most of its letters
with the word that was written, so the stretches it sits in hold, while a word diff's long
replacements drift (measured on 8 handwritten pages: 89 % of the sentences placed against 72 %).
RapidFuzz does a whole document in under a millisecond; the standard library needs two orders of
magnitude longer for the same answer.

The places are stored next to the reading, not as a result: what they are made from is the text
and the pages, so they are made again when either moved on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
import logging

from ..domain.formatted_text import join_labelled_pages
from ..domain.page_layout import LineAt, Opcode, PageReading, ScanBox, place
from .ocr_formats import plain_text_of
from .ocr_text_cleaner import clean_ocr_text

_logger = logging.getLogger(__name__)


def opcodes(one: str, other: str) -> tuple[Opcode, ...]:
    """Return the character alignment of two texts: what matches, and what stands for what."""
    if not one or not other:
        return ()
    # Imported here: only a run that keeps the scan's places needs it.
    from rapidfuzz.distance import Levenshtein  # noqa: PLC0415

    found = Levenshtein.opcodes(_folded(one), _folded(other))
    # The stub types the items as a class; at run time they are the 5-tuples difflib gives too.
    return tuple(found.as_list())  # ty: ignore[invalid-return-type]


def _folded(text: str) -> str:
    """Return *text* lower-cased character by character, so every offset stays where it was.

    ``str.lower`` makes two characters of some (``İ``), which would move every match after it.
    """
    return "".join(low if len(low := char.lower()) == 1 else char for char in text)


def places(readings: Sequence[PageReading], reading: str, text_format: str) -> tuple[ScanBox, ...]:
    """Return the lines of *readings* with their range in *reading*.

    *reading* is the document's text as it was stored, in *text_format*; its plain text is what
    the lines are placed in.  The pages of *readings* are joined the way the run joined them, so
    the labels that number the pages anchor the matches.
    """
    if not readings or not reading.strip():
        return ()
    joined, pages = _joined(readings)
    plain = plain_text_of(reading, text_format)
    lines = [
        line
        for index, (base, text, page) in enumerate(pages)
        for line in _lines_at(page, index, base, text)
    ]
    return place(lines, opcodes(joined, plain), plain)


def _joined(
    readings: Sequence[PageReading],
) -> tuple[str, tuple[tuple[int, str, PageReading], ...]]:
    """Return the pages joined as the run joins them, and where each page begins in that text.

    The joining is :func:`~teachers_teammate.domain.formatted_text.join_labelled_pages`, so the
    ``Page N:`` labels sit where the stored text has them; the bases are the same arithmetic
    that function does, kept here so a line's place is its index in the joined text.
    """
    texts = [clean_ocr_text(reading.text) for reading in readings]
    joined = join_labelled_pages(texts)
    at = 0
    pages: list[tuple[int, str, PageReading]] = []
    for number, (text, reading) in enumerate(zip(texts, readings, strict=True), start=1):
        if len(texts) > 1:
            at += len(f"Page {number}:\n")
        pages.append((at, text, reading))
        at += len(text) + 1
    return joined, tuple(pages)


def _lines_at(page: PageReading, index: int, base: int, text: str) -> list[LineAt]:
    """Return the lines of page *index*, with where each stands in *text*, which begins at *base*.

    A line the cleaner changed is left out: its words no longer stand where the reader found
    them, so its place would point at another line's words.
    """
    lines: list[LineAt] = []
    at = 0
    for line in page.lines:
        found = text.find(line.text, at)
        if found < 0:
            _logger.debug("a line is not in its page's cleaned text, it stays unplaced")
            continue
        placed = replace(line, page=index)
        lines.append(LineAt(placed, base + found, base + found + len(line.text)))
        at = found + len(line.text)
    return lines
