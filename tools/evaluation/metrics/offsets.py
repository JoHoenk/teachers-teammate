"""Token indices and character offsets, and the one place they are converted.

Every stored position — a label, a sentence span, a review part, a mark — is a **character** offset
into the stored text, end exclusive.  Token indices exist only inside the scorer, because the word
diff and the manifest count tokens of
:func:`~teachers_teammate.domain.proofreading.word_diff.tokenize`, whitespace tokens included, and a
tokenizer change would silently shift every stored position if one were stored.

The conversion is exact in both directions because the tokens concatenate back to the text: the
offset of token *i* is the length of the tokens before it.  A character position that is not a token
boundary has no token index, which is what :func:`char_to_token` reports as ``None``.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from functools import lru_cache

from teachers_teammate.domain.proofreading.word_diff import tokenize

Span = tuple[int, int]
"""A stretch ``(start, end)``, end exclusive; equal numbers are a point between two characters."""


@lru_cache(maxsize=4096)
def token_offsets(text: str) -> tuple[int, ...]:
    """Return the character offset of every token boundary of *text*.

    The result has one more entry than there are tokens: ``offsets[i]`` is where token *i* starts
    and ``offsets[-1]`` is the length of the text.

    The boundaries are cached by the text, because the scorer asks for the same source text once
    per label, per sentence span and per edit, and tokenizing it again each time is the hot path.
    The result is a tuple, so a caller cannot change what the cache holds.
    """
    offsets = [0]
    for token in tokenize(text):
        offsets.append(offsets[-1] + len(token))
    return tuple(offsets)


def token_to_char(text: str, index: int) -> int:
    """Return the character offset where token *index* of *text* starts.

    Raises:
        IndexError: if *index* is not a token boundary of *text*.
    """
    offsets = token_offsets(text)
    if not 0 <= index < len(offsets):
        raise IndexError(f"token index {index} is not a boundary of a text of {len(offsets) - 1}")
    return offsets[index]


def char_to_token(text: str, position: int) -> int | None:
    """Return the token index at character *position* of *text*, or ``None`` when none starts there."""
    offsets = token_offsets(text)
    index = bisect_left(offsets, position)
    if index < len(offsets) and offsets[index] == position:
        return index
    return None


def token_span_to_chars(text: str, span: Span) -> Span:
    """Return the character span of a token span of *text*.

    Raises:
        IndexError: if either end is not a token boundary of *text*.
    """
    start, end = span
    return token_to_char(text, start), token_to_char(text, end)


def char_span_to_tokens(text: str, span: Span) -> Span:
    """Return the token span of a character span of *text*.

    Raises:
        ValueError: if a span does not start and end on a token boundary.
    """
    start, end = span
    first = char_to_token(text, start)
    last = char_to_token(text, end)
    if first is None or last is None or last < first:
        raise ValueError(f"the span {start} to {end} does not lie on token boundaries")
    return first, last


def char_spans_to_tokens(text: str, spans: Sequence[Span]) -> tuple[Span, ...]:
    """Return the token span of every character span of *text*, in the same order.

    Raises:
        ValueError: if a span does not start and end on a token boundary.
    """
    return tuple(char_span_to_tokens(text, span) for span in spans)


def text_under(text: str, span: Span) -> str:
    """Return the characters of *text* under *span*, which is how a converted position is checked."""
    start, end = span
    return text[start:end]


__all__ = [
    "Span",
    "char_span_to_tokens",
    "char_spans_to_tokens",
    "char_to_token",
    "text_under",
    "token_offsets",
    "token_span_to_chars",
    "token_to_char",
]
