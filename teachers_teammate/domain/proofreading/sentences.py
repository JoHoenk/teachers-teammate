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
from itertools import pairwise
import re

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
