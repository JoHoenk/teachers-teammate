"""How good a proofreading answer is, measured against a reference correction.

The developer tools that compare writing models need three numbers, and this module computes
them without I/O, without a provider and without Qt, so they can be unit-tested and reused:

* **Edit agreement** — the word-diff changes of source → model text against source → reference
  text.  A change is one hunk of :func:`~.correction_review.diff_opcodes`: its span in the
  source and the words that replace it.  A change counts as found when both agree, so a note
  that touched the right words but replaced them with different ones counts as one miss and
  one invention, not as a hit.
* **Explanations** — how many of the changes that need one carry a non-blank explanation, how
  many changes need one at all, and how many notes explain no change (orphans).
* **Left alone** — how many already-correct sentences came back unchanged, which catches a
  model that rewrites text nobody asked it to touch.

Edit agreement is reported as precision, recall and the F-beta score.  F0.5 (``beta=0.5``)
weights precision twice as much as recall: a wrong correction in a teacher's text costs more
than a missed one, because the teacher has to notice and undo it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .correction_review import CorrectionReview, align, diff_opcodes, tokenize

Edit = tuple[int, int, tuple[str, ...]]
"""One word-diff change: ``(start, end, replacement)`` token indices into the source."""

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
    source_tokens = tokenize(source)
    other_tokens = tokenize(other)
    return frozenset(
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


def count_explained(source: str, corrected: str, review: CorrectionReview | None) -> ExplainedCount:
    """Return how many changes the review explains, against the changes in the text.

    A change that explains itself — spelling or punctuation touching no word — does not need a
    note.  A note whose explanation is blank tells the teacher nothing, and ``align`` cannot
    tell the difference on its own because it matches notes by their anchor.

    Args:
        source: The text before proofreading, in plain text.
        corrected: The text after proofreading, in plain text.
        review: The model's explanations, or ``None`` in corrections-only mode.

    Returns:
        The counts; all zero when there is no review to measure.
    """
    if review is None:
        return ExplainedCount(explained=0, needed=0, orphans=0)
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
