"""How much of a text an answer kept, without a reference to compare it with.

A model can silently drop a sentence or a whole paragraph: the answer is well formed, the
exchange sees no remark around it, and the piece would be stored as if the teacher had received
the whole text.  The evaluation tools catch that against the reference correction; the app has
no reference, so it compares the answer with the text it sent.

Two rules, because one alone misses a case:

* **The length ratio** catches an answer that came back much shorter or much longer than the
  text.  It is the rule that catches most of the drops.
* **The long run** catches a dropped sentence in a text that was otherwise corrected heavily,
  where the ratio stays near 1.

The run threshold is much higher than the one the evaluation tools use, because the app cannot
tell a dropped sentence from a text whose own correction cuts one: on the saved runs of the
official test set, 50 words fires on the four models that drop text and on no text where most
models agree, while 20 words fires on a text that every model cuts.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .word_diff import Hunk, tokenize, word_diff

LOST_RATIO = 0.80
"""Below this share of the text's words an answer has lost text."""

ADDED_RATIO = 1.25
"""Above this share of the text's words an answer has added text."""

RUN_WORDS = 50
"""How many words a deleted run needs to count on its own, ratio or not."""

MIN_RATIO_WORDS = 10
"""A text with fewer words is not judged by the ratio.

Correcting a short text changes its length by a large share: "ich bin müde" becomes
"Ich bin müde." and gains a mark, a third of its length.  The run rule never fires on such a text.
"""


def words(text: str) -> int:
    """Return how many words *text* has, as the app's tokeniser splits them; marks do not count."""
    return sum(1 for token in tokenize(text) if any(char.isalnum() for char in token))


@dataclass(frozen=True)
class TextLoss:
    """What one answer kept of its text: the words on both sides and the longest cut."""

    words_in: int
    """The words of the text that was sent."""
    words_out: int
    """The words of the answer."""
    cut_run: int
    """The longest stretch of words the answer deleted without writing as many in their place."""

    @property
    def lost(self) -> bool:
        """Whether the answer kept too little of the text."""
        short = self.words_in < MIN_RATIO_WORDS
        return (not short and self.words_out < LOST_RATIO * self.words_in) or (
            self.cut_run >= RUN_WORDS
        )

    @property
    def added(self) -> bool:
        """Whether the answer wrote much more than the text had."""
        return self.words_in >= MIN_RATIO_WORDS and self.words_out > ADDED_RATIO * self.words_in

    @property
    def problem(self) -> bool:
        """Whether the answer is not the text: it lost or added too much of it."""
        return self.lost or self.added


def measure(source: str, corrected: str, hunks: Sequence[Hunk] | None = None) -> TextLoss:
    """Return what *corrected* kept of *source*.

    Both texts must be plain (see ``TextFormatter.plain_text``): the diff is the app's own word
    diff, so the run is the one a teacher would see as a deleted stretch of text.  A caller that
    has the diff already passes it as *hunks*.
    """
    cut_run = 0
    for hunk in word_diff(source, corrected) if hunks is None else hunks:
        cut_run = max(cut_run, words(hunk.original) - words(hunk.corrected))
    return TextLoss(words_in=words(source), words_out=words(corrected), cut_run=cut_run)


def dropped_text(
    source: str, corrected: str, hunks: Sequence[Hunk] | None = None
) -> TextLoss | None:
    """Return the measurement of an answer that lost or added text, ``None`` for a usable one."""
    measured = measure(source, corrected, hunks)
    return measured if measured.problem else None


__all__ = [
    "ADDED_RATIO",
    "LOST_RATIO",
    "MIN_RATIO_WORDS",
    "RUN_WORDS",
    "TextLoss",
    "dropped_text",
    "measure",
    "words",
]
