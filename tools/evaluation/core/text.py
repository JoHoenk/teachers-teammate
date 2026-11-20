"""Counting the words of a text, the one way every layer counts them.

A word count decides a speed figure (seconds per 100 words), a text-loss ratio and the size of a
test set, so it is one function and not three.  The unit is the app's own token: a word or a mark,
whitespace not counted, so ``**bold**`` is three tokens and a comma is one.
"""

from __future__ import annotations

from teachers_teammate.domain.proofreading.word_diff import tokenize


def words(text: str) -> tuple[str, ...]:
    """Return the words and marks of *text* as the app's tokeniser sees them, without whitespace."""
    return tuple(token for token in tokenize(text) if token.strip())


def word_count(text: str) -> int:
    """Return how many words and marks *text* has (whitespace not counted)."""
    return len(words(text))


__all__ = ["word_count", "words"]
