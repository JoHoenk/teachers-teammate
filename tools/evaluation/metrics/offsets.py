"""Token indices and character offsets, and the one place they are converted.

Every stored position — a label, a sentence span, a review part, a mark — is a **character** offset
into the stored text, end exclusive.  Token indices exist only inside the scorer, because the word
diff and the manifest count tokens of
:func:`~teachers_teammate.domain.proofreading.word_diff.tokenize`, whitespace tokens included, and a
tokenizer change would silently shift every stored position if one were stored.

The boundaries themselves, :func:`text_offsets` and :func:`char_to_token`, are the app's, in
:mod:`teachers_teammate.domain.proofreading.word_diff` and
:mod:`teachers_teammate.domain.proofreading.edit_agreement`: a stored review places its edits the
same way.  What stays here converts whole spans, which only the corpus tools do.
"""

from __future__ import annotations

from collections.abc import Sequence

from teachers_teammate.domain.proofreading.edit_agreement import char_to_token
from teachers_teammate.domain.proofreading.sentences import Span
from teachers_teammate.domain.proofreading.word_diff import text_offsets


def token_to_char(text: str, index: int) -> int:
    """Return the character offset where token *index* of *text* starts.

    Raises:
        IndexError: if *index* is not a token boundary of *text*.
    """
    offsets = text_offsets(text)
    if not 0 <= index < len(offsets):
        raise IndexError(f"token index {index} is not a boundary of a text of {len(offsets) - 1}")
    return offsets[index]


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
    "char_span_to_tokens",
    "char_spans_to_tokens",
    "text_under",
    "token_span_to_chars",
    "token_to_char",
]
