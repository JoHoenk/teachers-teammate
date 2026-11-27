"""Sentence boundaries of a plain text.

The edits of a review are grouped per sentence and the explain step shows a sentence before and
after the correction, so both need to know where a sentence begins and ends.  The rule is the one
the chunker uses (:data:`teachers_teammate.domain.text_chunks._SENTENCE_BREAK`): a sentence ends
after ``.``, ``!``, ``?`` or ``…`` (with closing quotes or brackets) when whitespace follows, and a
blank line ends one too, because a heading or a list item carries no full stop.

A single line break is *not* a boundary: OCR output breaks a sentence across lines, and splitting
there would cut a sentence in two.  A better splitter (abbreviations such as "z. B.", lists) is
needed only by the review exchange, which places edits by sentence number.

Pure: no I/O, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import pairwise
import re

from .word_diff import Edit

Span = tuple[int, int]
"""A stretch of words ``(start, end)``; equal numbers are a point between two words."""

#: A sentence end: the mark, any closing quotes or brackets, then whitespace.
_SENTENCE_END = re.compile(r"[.!?\u2026][\"'\u201c\u201d\u2018\u2019\u00bb)\]]*(?=\s)")

#: A paragraph break: a blank line, possibly with trailing spaces or more blank lines.
_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n[ \t\n]*")


def sentence_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return the ``(start, end)`` of every sentence of *text*, whitespace trimmed off.

    The spans are half-open and in text order; the whitespace between two sentences belongs to
    neither.  An empty or whitespace-only text has no sentences.
    """
    cuts = {0, len(text)}
    for match in _SENTENCE_END.finditer(text):
        cuts.add(match.end())
    for match in _PARAGRAPH_BREAK.finditer(text):
        cuts.update((match.start(), match.end()))
    ordered = sorted(cuts)
    spans: list[tuple[int, int]] = []
    for start, end in pairwise(ordered):
        piece = text[start:end]
        stripped = piece.strip()
        if stripped:
            lead = len(piece) - len(piece.lstrip())
            spans.append((start + lead, start + lead + len(stripped)))
    return tuple(spans)


def sentence_index(spans: tuple[tuple[int, int], ...], offset: int) -> int:
    """Return the index of the sentence *offset* belongs to (the last one that starts before).

    An offset in the whitespace between two sentences belongs to the sentence before it; an
    offset before the first sentence belongs to the first one.  ``-1`` when there is no sentence.
    """
    if not spans:
        return -1
    return max(0, bisect_right([start for start, _ in spans], offset) - 1)


def sentence_texts(text: str, spans: tuple[tuple[int, int], ...]) -> tuple[str, ...]:
    """Return the text of every sentence of *spans*, in order."""
    return tuple(text[start:end] for start, end in spans)


@dataclass(frozen=True)
class SentenceRow:
    """One row of a side-by-side view: a sentence, or several when an edit crosses a boundary."""

    start: int
    """First token of the row, an index into ``tokenize(source)``."""

    end: int
    """One past the last token; the rows of a text are contiguous and cover it."""


def sentence_rows(
    spans: Sequence[Span], edits: Iterable[Edit], total: int
) -> tuple[SentenceRow, ...]:
    """Return the rows of a text's view, from its sentence spans and every version's edits.

    Args:
        spans: The token span of every sentence of the corpus's segmentation, in text order.
        edits: The ``(start, end, replacement)`` of every version, so that a row is wide enough
            for the widest edit any version makes.
        total: How many tokens the text has, so the last row reaches the end.

    Returns:
        The rows, contiguous and covering the text.  A text without sentence spans is one row.
    """
    if not spans:
        return (SentenceRow(0, total),)
    owner = [_owner(spans, start, end) for start, end, _ in edits]
    ends = [end for _, end in spans]
    for index, (_, end, _) in enumerate(edits):
        sentence = owner[index]
        if sentence is not None:
            ends[sentence] = max(ends[sentence], end)
    merged: list[SentenceRow] = []
    index = 0
    while index < len(spans):
        start, end = spans[index][0], ends[index]
        following = index + 1
        while following < len(spans) and spans[following][0] < end:
            end = max(end, ends[following])
            following += 1
        merged.append(SentenceRow(start, end))
        index = following
    # The whitespace between two sentences belongs to the row before it, so the rows cover the
    # text and a version's rows join to the version's whole text.
    rows: list[SentenceRow] = []
    for number, row in enumerate(merged):
        following = merged[number + 1].start if number + 1 < len(merged) else total
        rows.append(SentenceRow(row.start, max(row.end, following)))
    return tuple(rows)


def _owner(spans: Sequence[Span], start: int, end: int) -> int | None:
    """Return the sentence an edit starting at *start* belongs to, or ``None``."""
    if start == end:
        for index, (_, sentence_end) in enumerate(spans):
            if sentence_end == start:
                return index
    for index, (sentence_start, sentence_end) in enumerate(spans):
        if sentence_start <= start < sentence_end:
            return index
    for index in range(len(spans) - 1, -1, -1):
        if spans[index][0] <= start:
            return index
    return None


def row_of(rows: Sequence[SentenceRow], start: int, end: int) -> int:
    """Return the row an edit starting at *start* is shown in."""
    for index, row in enumerate(rows):
        if row.start <= start < row.end:
            return index
    if start == end:
        for index, row in enumerate(rows):
            if row.end == start:
                return index
    return len(rows) - 1
