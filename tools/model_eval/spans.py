"""Character spans to token spans: the positions the word diff and the manifest use.

A text is cut into sentences by characters (a corpus adapter's sentence split, a fixture's
sentences), while ``edits``, ``untouched_spans`` and the manifest count tokens of
:func:`~teachers_teammate.domain.correction_review.tokenize`, whitespace tokens included.  This
module is the one place that converts one into the other, so a sentence span means the same
thing in the test set and in the structured-output check.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from teachers_teammate.domain.correction_review import tokenize

from .quality import Span


def token_spans(source: str, char_spans: Collection[Span]) -> tuple[Span, ...]:
    """Return the token span of every character span of *source*.

    The manifest records sentence spans in the positions of ``edits``, which are token indices
    into :func:`~teachers_teammate.domain.correction_review.tokenize`, whitespace tokens included.

    Args:
        source: The text the character spans are positions in.
        char_spans: ``[start, end)`` character offsets, in text order.

    Returns:
        One ``(start, end)`` token span per character span, in the same order.

    Raises:
        ValueError: if a span does not start and end on a token boundary.
    """
    offsets = [0]
    for token in tokenize(source):
        offsets.append(offsets[-1] + len(token))
    found: list[Span] = []
    for start, end in char_spans:
        first = _boundary(offsets, start)
        last = _boundary(offsets, end)
        if first is None or last is None or last < first:
            raise ValueError(f"the span {start} to {end} does not lie on token boundaries")
        found.append((first, last))
    return tuple(found)


def _boundary(offsets: Sequence[int], position: int) -> int | None:
    """Return the token index at character *position*, or ``None`` when no token starts there."""
    for index, offset in enumerate(offsets):
        if offset == position:
            return index
        if offset > position:
            return None
    return None


__all__ = ["token_spans"]
