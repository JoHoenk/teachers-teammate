"""Narrow a long run of blank lines before the request, and put it back after the answer.

A model reads a text whose paragraphs stand several blank lines apart as two texts and answers
the second one: on the held-out set, four blank lines between two essays and the answer held the
second essay alone, four tries of four, while one blank line was answered whole.  A gap that wide
is the student's layout, not something a correction changes, so the request carries it as one
paragraph break (:func:`collapse`) and the answer gets it back (:func:`restore_gaps`).

The gaps are put back by counting paragraph breaks, not by looking for words: the answer's own
breaks are the model's, so a gap goes back only where the model kept the break, and a model that
merged or split a paragraph while correcting it keeps the breaks it wrote.  A text without a long
gap is sent as it is, and no gap is held.

The app stores line breaks as ``"\\n"``, so a gap of another line ending is not found here.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
import re

BREAK = "\n\n"
"""One paragraph break in a request: one blank line between the two paragraphs."""

LONG_RUN = 3
"""How many line breaks in a row make a gap long: two blank lines or more."""

_GAP = re.compile(r"(?<=\S)(?:[ \t]*\n){2,}[ \t]*(?=\S)")
"""A paragraph break between two words, with any spaces a reader left on the blank lines."""


def collapse(raw: str) -> tuple[str, tuple[str, ...]]:
    """Return *raw* with every long gap narrowed, and the gaps to put back, in text order.

    A run of :data:`LONG_RUN` line breaks or more becomes :data:`BREAK`; one blank line stays as
    it is.  The gaps are the text of every paragraph break of *raw*, so the i-th of them is the
    one the i-th break of the answer stands for.  A text whose breaks are all one blank line comes
    back unchanged, with no gaps.
    """
    pieces: list[str] = []
    gaps: list[str] = []
    cursor = 0
    for found in _GAP.finditer(raw):
        pieces.append(raw[cursor : found.start()])
        pieces.append(BREAK)
        gaps.append(found.group())
        cursor = found.end()
    if not any(gap.count("\n") >= LONG_RUN for gap in gaps):
        return raw, ()
    pieces.append(raw[cursor:])
    return "".join(pieces), tuple(gaps)


def restore_gaps(answer: str, gaps: Sequence[str]) -> str:
    """Return *answer* with *gaps* put back at the paragraph breaks it kept, in order.

    The breaks are counted, not found: a gap goes back only when the answer has exactly as many
    paragraph breaks as the text had, and then the i-th gap widens the i-th break.  An answer that
    merged or split a paragraph keeps the breaks it wrote, because there is no telling which of
    them stands where the student's gap stood.
    """
    if not gaps:
        return answer
    breaks = list(_GAP.finditer(answer))
    if len(breaks) != len(gaps):
        return answer
    pieces: list[str] = []
    cursor = 0
    for found, gap in zip(breaks, gaps, strict=True):
        pieces.append(answer[cursor : found.start()])
        pieces.append(gap)
        cursor = found.end()
    pieces.append(answer[cursor:])
    return "".join(pieces)
