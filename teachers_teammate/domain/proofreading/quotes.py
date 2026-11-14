"""Finding a quoted expression in a text, and mapping its span back through the diff.

Step ③ of the exchange quotes the *corrected* text; R quotes the student's text.  Either way the
quote has to become a character span in the text the review is anchored on, and a quote that the
correction changed is not a separate observation: the edit already says what is wrong there.

Two functions:

* :func:`find_spans` locates every quote in a text.  A quote is searched exactly first, then with
  collapsed whitespace, then fuzzily over whole sentence-like pieces, because a model re-quotes
  what it read with its own spacing and occasionally with a word changed.
* :func:`map_back` turns a span of the corrected text into the span of the student's text, and
  returns ``None`` when the span touches a hunk — there the two texts differ, so there is no
  one-to-one offset.

Pure: no I/O, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
import re

from .word_diff import Hunk, Similarity

_FUZZY_RATIO = 0.9
"""Two strings count as the same quote above this similarity."""

_SEGMENT_RE = re.compile(
    r"[^\n]+?(?:[.!?\u2026]+[\x22'\u201c\u201d\u2018\u2019\u00bb)\]]*(?=\s|$)|$)", re.M
)
"""One sentence-like piece of text: up to a sentence end followed by a space, or a line end."""

_MAX_FUZZY_SEGMENTS = 4
"""A fuzzy quote may span this many consecutive sentence-like pieces (a model may merge two)."""


def find_spans(
    text: str, quotes: Sequence[str], *, ordered: bool = False
) -> list[tuple[int, int] | None]:
    """Locate every quote in *text*; ``None`` where it cannot be found.

    With *ordered* the quotes are expected in text order (each search starts where the last match
    ended).  Without it every quote is searched from the start, and two quotes never share a
    match: a model lists the expressions it found in whatever order it noticed them.
    """
    normalised_text, text_map = _normalised_with_map(text)
    cursor = 0
    taken: set[int] = set()
    spans: list[tuple[int, int] | None] = []
    for quote in quotes:
        span = None
        if quote.strip():
            span = _find_anchor(text, normalised_text, text_map, cursor if ordered else 0, quote)
            while span is not None and not ordered and span[0] in taken:
                span = _find_anchor(text, normalised_text, text_map, span[1], quote)
        if span is not None:
            if ordered:
                cursor = span[1]
            else:
                taken.add(span[0])
        spans.append(span)
    return spans


def map_back(span: tuple[int, int], hunks: Sequence[Hunk]) -> tuple[int, int] | None:
    """Return *span* of the corrected text as a span of the student's text.

    ``None`` when the span touches a hunk: inside a hunk the two texts differ, so the offsets do
    not correspond.  A span that lies in an equal region maps one to one, shifted by the length
    difference up to the last hunk before it.
    """
    start, end = span
    shift = 0
    for hunk in hunks:
        if end <= hunk.corrected_start:
            break
        if start >= hunk.corrected_end:
            shift = hunk.offset_after
            continue
        return None
    return start - shift, end - shift


def _normalised_with_map(text: str) -> tuple[str, tuple[int, ...]]:
    """Return *text* with whitespace runs collapsed to one space, plus per-char source offsets."""
    chars: list[str] = []
    positions: list[int] = []
    pending_space = False
    for position, char in enumerate(text):
        if char.isspace():
            pending_space = bool(chars)
            continue
        if pending_space:
            chars.append(" ")
            positions.append(position)
            pending_space = False
        chars.append(char)
        positions.append(position)
    return "".join(chars), tuple(positions)


def _normalise(text: str) -> str:
    """Return *text* with every whitespace run collapsed to a single space and trimmed."""
    return " ".join(text.split())


def _find_whole(text: str, needle: str, start: int) -> int:
    """Return the first index of *needle* in *text* from *start* that is not inside a word.

    An expression such as ``"it"`` or ``"he"`` is also a part of ``"without"`` and ``"then"``;
    where *needle* begins (ends) with a letter or digit, the character before (after) the match
    must not be one.  ``-1`` when there is no such match.
    """
    check_before = needle[:1].isalnum()
    check_after = needle[-1:].isalnum()
    index = text.find(needle, start)
    while index >= 0:
        end = index + len(needle)
        inside_before = check_before and index > 0 and text[index - 1].isalnum()
        inside_after = check_after and end < len(text) and text[end].isalnum()
        if not (inside_before or inside_after):
            return index
        index = text.find(needle, index + 1)
    return -1


def _find_anchor(
    text: str,
    normalised_text: str,
    text_map: tuple[int, ...],
    cursor: int,
    quote: str,
) -> tuple[int, int] | None:
    """Return the ``(start, end)`` of *quote* in *text* at or after *cursor*, or ``None``."""
    if not quote.strip():
        return None
    exact = _find_whole(text, quote, cursor)
    if exact >= 0:
        return exact, exact + len(quote)

    needle = _normalise(quote)
    if needle:
        from_index = bisect_left(text_map, cursor)
        found = _find_whole(normalised_text, needle, from_index)
        if found >= 0:
            last = found + len(needle) - 1
            return text_map[found], text_map[last] + 1

    return _fuzzy_anchor(text, cursor, quote)


def _fuzzy_anchor(text: str, cursor: int, quote: str) -> tuple[int, int] | None:
    """Return the span of the sentence-like piece(s) of *text* most similar to *quote*.

    Candidates are whole runs of up to :data:`_MAX_FUZZY_SEGMENTS` consecutive sentence-like
    pieces after *cursor*, so the returned span is always a complete piece of the text.  The best
    candidate with a ``SequenceMatcher`` ratio of at least :data:`_FUZZY_RATIO` wins; the earliest
    one on a tie.
    """
    target = quote.strip()
    if len(target) < 4:
        return None
    spans: list[tuple[int, int]] = []
    for match in _SEGMENT_RE.finditer(text, cursor):
        piece = match.group()
        stripped = piece.strip()
        if stripped:
            start = match.start() + len(piece) - len(piece.lstrip())
            spans.append((start, start + len(stripped)))
    matcher = Similarity(target)
    longest = len(target) * (2 / _FUZZY_RATIO - 1)
    best: tuple[int, int] | None = None
    best_ratio = _FUZZY_RATIO
    for first in range(len(spans)):
        for last in range(first, min(first + _MAX_FUZZY_SEGMENTS, len(spans))):
            start, end = spans[first][0], spans[last][1]
            if end - start > longest:
                break
            ratio = matcher.ratio(text[start:end], at_least=best_ratio)
            if ratio >= best_ratio and (best is None or ratio > best_ratio):
                best, best_ratio = (start, end), ratio
    return best
