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

An answer that left text out is repaired instead of failing the file: :func:`repair` writes the
student's words back where the answer left out a stretch of at least :data:`RESTORE_MIN_WORDS`
words, so the corrections of the rest are not lost with it.

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


RESTORE_MIN_WORDS = 8
"""How many words a stretch must be cut by for :func:`repair` to put it back.

Shorter cuts are what correction does all the time (a doubled word, a filler); a stretch of this
many words that the answer left out is a sentence the student wrote.
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
    def ratio_lost(self) -> bool:
        """Whether the length ratio fired: the answer has far fewer words than the text."""
        return self.words_in >= MIN_RATIO_WORDS and self.words_out < LOST_RATIO * self.words_in

    @property
    def run_lost(self) -> bool:
        """Whether the long-run rule fired: the answer cut one long stretch of the text."""
        return self.cut_run >= RUN_WORDS

    @property
    def lost(self) -> bool:
        """Whether the answer kept too little of the text."""
        return self.ratio_lost or self.run_lost

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


@dataclass(frozen=True)
class LeftOut:
    """One stretch of the text that the answer left out, as it stood in the text that was sent."""

    text: str
    """The student's words, as they were sent."""
    start: int
    end: int
    """Half-open span in the text that was sent, with its outer whitespace removed."""
    words: int
    """How many words of it the answer did not write in their place."""


@dataclass(frozen=True)
class Repaired:
    """An answer that left text out, with every stretch it left out put back."""

    text: str
    """The answer, with each left-out stretch written back where it stood."""
    left_out: tuple[LeftOut, ...]
    """The stretches that were put back, in text order."""

    @property
    def words(self) -> int:
        """How many words were put back."""
        return sum(part.words for part in self.left_out)


def repair(source: str, corrected: str, hunks: Sequence[Hunk] | None = None) -> Repaired | None:
    """Return *corrected* with the stretches it left out put back, or ``None`` when it left none.

    Every hunk of the word diff that cuts at least :data:`RESTORE_MIN_WORDS` words is a stretch
    the answer left out: the student's words stand there again, and the answer is kept everywhere
    else.  The repaired text is measured again by the caller: an answer that is still not the
    text (it added text of its own, or cut many short stretches) is not repaired by this.

    Both texts must be plain, as for :func:`measure`; the spans are in *source* with its outer
    whitespace removed.  A caller that has the diff of the two stripped texts passes it as *hunks*.
    """
    source, corrected = source.strip(), corrected.strip()
    found = word_diff(source, corrected) if hunks is None else hunks
    cut = [
        hunk for hunk in found if words(hunk.original) - words(hunk.corrected) >= RESTORE_MIN_WORDS
    ]
    if not cut:
        return None
    text = corrected
    for hunk in reversed(cut):
        before, after = text[: hunk.corrected_start], text[hunk.corrected_end :]
        text = before + _joined(before, hunk.original, after, source, hunk) + after
    return Repaired(
        text=text,
        left_out=tuple(
            LeftOut(
                text=hunk.original,
                start=hunk.raw_start,
                end=hunk.raw_end,
                words=words(hunk.original) - words(hunk.corrected),
            )
            for hunk in cut
        ),
    )


_OPENING = "([{\"'„“‚‘»«"


def _wordlike(char: str) -> bool:
    """Return whether *char* starts or ends a word: a letter or digit, or an opening mark."""
    return char.isalnum() or char in _OPENING


def _joined(before: str, stretch: str, after: str, source: str, hunk: Hunk) -> str:
    """Return *stretch* with the whitespace it needs to stand between *before* and *after*.

    The whitespace is the one that stood around the stretch in *source*, so a paragraph that was
    left out comes back with its paragraph breaks; where the source had none, a stretch that
    starts or ends with a word takes one space.  The diff may cut a stretch at a full stop (the
    one of the last sentence is the one it matches with the answer's), so a stretch that starts
    or ends with a mark takes no space on that side.
    """
    kept_before = source[: hunk.raw_start]
    kept_after = source[hunk.raw_end :]
    gap_before = kept_before[len(kept_before.rstrip()) :]
    gap_after = kept_after[: len(kept_after) - len(kept_after.lstrip())]
    lead = ""
    if before and not before[-1].isspace():
        if gap_before:
            lead = gap_before
        elif before[-1] not in _OPENING and _wordlike(stretch[:1] or " "):
            lead = " "
    follow = ""
    if after and not after[0].isspace() and not stretch[-1:].isspace():
        if gap_after:
            follow = gap_after
        elif _wordlike(after[0]) and stretch[-1:] not in _OPENING:
            follow = " "
    return lead + stretch + follow


__all__ = [
    "ADDED_RATIO",
    "LOST_RATIO",
    "MIN_RATIO_WORDS",
    "RESTORE_MIN_WORDS",
    "RUN_WORDS",
    "LeftOut",
    "Repaired",
    "TextLoss",
    "dropped_text",
    "measure",
    "repair",
    "words",
]
