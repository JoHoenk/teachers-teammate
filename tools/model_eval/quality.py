"""How good a proofreading answer is, measured against a reference correction.

The developer tools that compare writing models need three numbers, and this module computes
them without I/O, without a provider and without Qt, so they can be unit-tested and reused:

* **Edit agreement** — the word-diff changes of source → model text against source → reference
  text.  A change is one hunk of :func:`~teachers_teammate.domain.correction_review.diff_opcodes`: its span in the
  source and the words that replace it.  A change counts as found when both agree, so a note
  that touched the right words but replaced them with different ones counts as one miss and
  one invention, not as a hit.
* **Explanations** — how many of the changes that need one carry a non-blank explanation, how
  many changes need one at all, and how many notes explain no change (orphans).
* **Left alone** — how many already-correct sentences came back unchanged, which catches a
  model that rewrites text nobody asked it to touch.

On top of that, :func:`match_edits` tells for every reference change whether the model made
exactly that change and whether it at least touched the same words, which lets a report ask
"which kinds of error does a model find?" once the reference changes carry a label.

Edit agreement is reported as precision, recall and the F-beta score.  F0.5 (``beta=0.5``)
weights precision twice as much as recall: a wrong correction in a teacher's text costs more
than a missed one, because the teacher has to notice and undo it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from teachers_teammate.domain.correction_review import (
    AlignedReview,
    CorrectionReview,
    align,
    diff_opcodes,
    tokenize,
)

Edit = tuple[int, int, tuple[str, ...]]
"""One word-diff change: ``(start, end, replacement)`` token indices into the source."""

Span = tuple[int, int]
"""A stretch of words ``(start, end)``; equal numbers are a point between two words."""

DEFAULT_BETA = 0.5
"""F-beta weight of the summary score: precision counts twice as much as recall."""


def edits(source: str, other: str) -> frozenset[Edit]:
    """Return the word-diff changes that turn *source* into *other*.

    Args:
        source: The text the comparison starts from.
        other: The text to reach.

    Returns:
        The changes as ``(start, end, replacement)`` over the source tokens.  Whitespace-only
        differences are not changes, exactly as the GUI's diff does not show them.
    """
    return frozenset(ordered_edits(source, other))


@lru_cache(maxsize=32)
def ordered_edits(source: str, other: str) -> tuple[Edit, ...]:
    """Return the word-diff changes that turn *source* into *other*, in text order.

    The same changes as :func:`edits`, which is a set because the scoring only needs membership;
    a caller that wants to point at one change (a note that explains it) needs the order, which
    is the order of the hunks :func:`~teachers_teammate.domain.correction_review.align` numbers.
    """
    source_tokens = tokenize(source)
    other_tokens = tokenize(other)
    return tuple(
        (start, end, tuple(other_tokens[from_:to]))
        for tag, start, end, from_, to in diff_opcodes(source_tokens, other_tokens)
        if tag != "equal"
    )


@dataclass(frozen=True)
class EditScore:
    """How well one text's changes agree with a reference correction."""

    true_positives: int
    """Changes the text made that the reference makes too."""

    false_positives: int
    """Changes the text made that the reference does not make."""

    false_negatives: int
    """Changes the reference makes that the text does not make."""

    @property
    def precision(self) -> float:
        """Share of this text's changes that the reference agrees with (0 when it made none)."""
        made = self.true_positives + self.false_positives
        return self.true_positives / made if made else 0.0

    @property
    def recall(self) -> float:
        """Share of the reference's changes this text found (0 when there are none)."""
        expected = self.true_positives + self.false_negatives
        return self.true_positives / expected if expected else 0.0

    def f_beta(self, beta: float = DEFAULT_BETA) -> float:
        """Return the weighted harmonic mean of precision and recall (0 when either is 0)."""
        squared = beta**2
        weighted = squared * self.precision + self.recall
        if not weighted:
            return 0.0
        return (1 + squared) * self.precision * self.recall / weighted


def score_edits(source: str, model_text: str, reference: str) -> EditScore:
    """Return the edit agreement of *model_text* with *reference*, both derived from *source*.

    Args:
        source: The text both corrections start from.
        model_text: What the writing model produced.
        reference: The reference correction of the same text.

    Returns:
        The :class:`EditScore` of the model's changes against the reference's.
    """
    made = edits(source, model_text)
    expected = edits(source, reference)
    hits = len(made & expected)
    return EditScore(
        true_positives=hits,
        false_positives=len(made) - hits,
        false_negatives=len(expected) - hits,
    )


@dataclass(frozen=True)
class ExplainedCount:
    """How many changes carry an explanation, and how many need one."""

    explained: int
    """Changes whose note explains them in non-blank words."""

    needed: int
    """Changes that need an explanation at all (a comma does not)."""

    orphans: int
    """Notes that explain no change in the text: the model claimed an edit it did not make."""

    @property
    def ratio(self) -> float:
        """Share of the changes that need an explanation and carry one (1 when none needed)."""
        return self.explained / self.needed if self.needed else 1.0


def _says_why(change) -> bool:
    """Whether *change* carries a note that actually explains it."""
    return change.note is not None and change.note.explanation.strip() != ""


def count_explained(
    source: str,
    corrected: str,
    review: CorrectionReview | None,
    *,
    aligned: AlignedReview | None = None,
) -> ExplainedCount:
    """Return how many changes the review explains, against the changes in the text.

    A change that explains itself — spelling or punctuation touching no word — does not need a
    note.  A note whose explanation is blank tells the teacher nothing, and ``align`` cannot
    tell the difference on its own because it matches notes by their anchor.

    Args:
        source: The text before proofreading, in plain text.
        corrected: The text after proofreading, in plain text.
        review: The model's explanations, or ``None`` in corrections-only mode.
        aligned: The aligned review of *source* against *corrected*, when the caller has already
            computed it for another purpose; it is aligned here otherwise.

    Returns:
        The counts; all zero when there is no review to measure.
    """
    if review is None:
        return ExplainedCount(explained=0, needed=0, orphans=0)
    if aligned is None:
        aligned = align(source, corrected, review)
    needed = [change for change in aligned.changes if _says_why(change) or not change.is_minor]
    return ExplainedCount(
        explained=sum(1 for change in needed if _says_why(change)),
        needed=len(needed),
        orphans=len(aligned.orphan_notes),
    )


def unchanged_sentences(sentences: tuple[str, ...] | list[str], output: str) -> int:
    """Return how many of the already-correct *sentences* came back unchanged in *output*.

    Args:
        sentences: The sentences that were correct before proofreading.
        output: The text the model produced.

    Returns:
        How many of them still appear in *output*, word for word.
    """
    return sum(1 for sentence in sentences if sentence in output)


def word_positions(tokens: Sequence[str]) -> list[int]:
    """Return, for every token boundary, how many words and marks lie before it.

    Whitespace tokens are not counted, so ``positions[i]`` is the same number on either side of
    a space: a comma inserted before the space and one inserted after it are the same place.
    The list has one more entry than there are tokens.
    """
    positions = [0]
    for token in tokens:
        positions.append(positions[-1] + (1 if token.strip() else 0))
    return positions


def spans_touch(first: Span, second: Span) -> bool:
    """Return whether two spans of words share words, or one is a point inside the other.

    Two points touch when they are the same place.  A point touches a stretch only strictly
    inside it, so an insertion right next to a replaced word is not that word's change.
    """
    (first_start, first_end), (second_start, second_end) = first, second
    if first_start == first_end and second_start == second_end:
        return first_start == second_start
    if first_start == first_end:
        return second_start < first_start < second_end
    if second_start == second_end:
        return first_start < second_start < first_end
    return max(first_start, second_start) < min(first_end, second_end)


def untouched_spans(source: str, other: str, spans: Sequence[Span]) -> tuple[bool, ...]:
    """Return, for every token span of *source*, whether the word diff to *other* leaves it alone.

    The rule is deliberately conservative, because it decides which sentences count as "the
    reference left this one alone": a sentence only counts when nothing could have changed it.  A
    replacement or a deletion touches a span when they share a word.  An insertion touches a span
    when it lies inside it **or at either edge** — the opposite of :func:`spans_touch`, because a
    word inserted between two sentences may belong to either of them.

    Args:
        source: The text the spans are positions in.
        other: The text to compare with, a reference correction or a model's answer.
        spans: ``[start, end)`` token indices into ``tokenize(source)``, whitespace tokens
            included, the positions :func:`ordered_edits` and the manifest's edits use.

    Returns:
        One flag per span, in the order given: ``True`` when no change of the diff touches it.
    """
    positions = word_positions(tokenize(source))
    changes = [(positions[start], positions[end]) for start, end, _ in ordered_edits(source, other)]
    return tuple(
        not any(
            _touches_or_borders((positions[start], positions[end]), change) for change in changes
        )
        for start, end in spans
    )


def _touches_or_borders(span: Span, change: Span) -> bool:
    """Return whether *change* shares words with *span*, or is an insertion at its edge."""
    start, end = span
    change_start, change_end = change
    if change_start == change_end:
        return start <= change_start <= end
    return max(start, change_start) < min(end, change_end)


@dataclass(frozen=True)
class EditMatch:
    """What a model made of one change that the reference correction makes."""

    edit: Edit
    """The reference change: tokens ``start`` to ``end`` of the source and their replacement."""

    exact: bool
    """The model made this very change (same words, same replacement)."""

    located: bool
    """The model changed at least some of the same words, with the same or another replacement."""


def match_edits(source: str, model_text: str, reference: str) -> list[EditMatch]:
    """Return, for every change of the reference, whether the model made or at least located it.

    ``exact`` is what the F-score counts.  ``located`` separates "did not notice the error" from
    "noticed it and fixed it differently", which the F-score scores alike (both are a miss).

    Args:
        source: The text both corrections start from.
        model_text: What the writing model produced.
        reference: The reference correction of the same text.

    Returns:
        One entry per reference change, in text order.
    """
    positions = word_positions(tokenize(source))
    made = edits(source, model_text)
    made_spans = [(positions[start], positions[end]) for start, end, _ in made]
    matches = []
    for edit in sorted(edits(source, reference)):
        span = (positions[edit[0]], positions[edit[1]])
        exact = edit in made
        matches.append(
            EditMatch(
                edit=edit,
                exact=exact,
                located=exact or any(spans_touch(span, other) for other in made_spans),
            )
        )
    return matches
