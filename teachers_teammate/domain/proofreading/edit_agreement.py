"""How good a proofreading answer is, measured against another answer or a reference.

The app compares writing models on one text, and the evaluation tools compare them on a corpus;
both count the same things, so they count them here:

* **Edits** — the word-diff changes of source → other text.  A change is one hunk of
  :func:`~teachers_teammate.domain.proofreading.word_diff.text_opcodes`: its span in the source
  and the words that replace it.  A change counts as found when both sides agree, so a note that
  touched the right words but replaced them with different ones counts as one miss and one
  invention, not as a hit.
* **Explanations** — how many of the changes that need one carry a non-blank explanation, how
  many changes need one at all, and how many notes explain no change (orphans).
* **Positions** — the token indices of the diff turned into character offsets, the form a stored
  position takes.

Two changes are the same change where they stand in words and in the words and marks they put
there (:func:`change_key`), never in the whitespace around them: a comma inserted before a space
and the same comma inserted after it are one change, and so are ``" of"`` and ``"of "``.

Agreement is reported as precision, recall and the F-beta score.  F0.5 (``beta=0.5``) weights
precision twice as much as recall: a wrong correction in a teacher's text costs more than a missed
one, because the teacher has to notice and undo it.

The conversion is exact in both directions because the tokens concatenate back to the text.  A
character position that is not a token boundary has no token index, which is what
:func:`char_to_token` reports as ``None``.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from functools import lru_cache

from .review import AlignedReview, CorrectionReview, align
from .sentences import Span
from .word_diff import Edit, text_offsets, text_opcodes, tokenize

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


@lru_cache(maxsize=16384)
def ordered_edits(source: str, other: str) -> tuple[Edit, ...]:
    """Return the word-diff changes that turn *source* into *other*, in text order.

    The result is cached for many pairs: a report reads every row of every run of a test set, and
    the same text's reference is diffed again for each of them, and each version's own diff is
    asked for by the scoring, the outcomes and the rows of the report.

    The same changes as :func:`edits`, which is a set because the scoring only needs membership;
    a caller that wants to point at one change (a note that explains it) needs the order, which
    is the order of the hunks :func:`~teachers_teammate.domain.proofreading.review.align` numbers.
    """
    other_tokens = tokenize(other)
    return tuple(
        (start, end, tuple(other_tokens[from_:to]))
        for tag, start, end, from_, to in text_opcodes(source, other)
        if tag != "equal"
    )


ChangeKey = tuple[int, int, tuple[str, ...]]
"""A change as the agreement compares it: its span in words and the words it puts there."""


def change_key(edit: Edit, positions: Sequence[int]) -> ChangeKey:
    """Return the form in which the agreement compares *edit*.

    The span is read through :func:`word_positions`, so an insertion before a space and the same
    insertion after it are one place, and the replacement leaves the whitespace tokens out, so
    ``" of"`` and ``"of "`` are one wording.  Two texts that fixed one place in the same words
    therefore agree however they laid out the spaces between them.

    Args:
        edit: The change: token indices into the source and the tokens that replace them.
        positions: The word position of every token boundary of the source, from
            :func:`word_positions`.
    """
    start, end, replacement = edit
    return (
        positions[start],
        positions[end],
        tuple(token for token in replacement if token.strip()),
    )


def char_edits(source: str, output: str) -> list[tuple[tuple[int, int], str]]:
    """Return the changes that turn *source* into *output* as ``(character span, replacement)``.

    This is the form a stored position takes: a mark, a label and a review part all name a stretch
    of the text in characters, so the token changes of :func:`ordered_edits` are converted here and
    nowhere else.
    """
    offsets = text_offsets(source)
    return [
        ((offsets[start], offsets[end]), "".join(replacement))
        for start, end, replacement in ordered_edits(source, output)
    ]


def char_to_token(text: str, position: int) -> int | None:
    """Return the token index at character *position* of *text*, or ``None`` when none starts there."""
    offsets = text_offsets(text)
    index = bisect_left(offsets, position)
    if index < len(offsets) and offsets[index] == position:
        return index
    return None


def token_spans(source: str, spans: Sequence[Span]) -> tuple[Span, ...]:
    """Return the token span of every character span of a sentence segmentation.

    A span that does not lie on token boundaries names no row and is left out, so the sentence
    merges with its neighbour instead of the text losing a row.
    """
    found: list[Span] = []
    for start, end in spans:
        first = char_to_token(source, start)
        last = char_to_token(source, end)
        if first is not None and last is not None and last >= first:
            found.append((first, last))
    return tuple(found)


@dataclass(frozen=True)
class EditScore:
    """How well one text's changes agree with a reference correction.

    The two sides are counted separately, because an accepted alternative makes them differ: a
    true positive is an *answer* change the key accepts, and ``found`` is a *reference* change the
    answer got.  One alternative can be one true positive and find two reference changes, so
    precision and recall do not share a numerator.
    """

    true_positives: int
    """Changes the text made that the reference makes too, or that the key accepts."""

    false_positives: int
    """Changes the text made that neither the reference nor the key accepts."""

    found: int
    """Reference changes the text found: made itself, or overlapped by an accepted alternative."""

    false_negatives: int
    """Reference changes the text did not find."""

    @property
    def precision(self) -> float:
        """Share of this text's changes the key accepts (0 when it made none)."""
        made = self.true_positives + self.false_positives
        return self.true_positives / made if made else 0.0

    @property
    def recall(self) -> float:
        """Share of the reference's changes this text found (0 when there are none)."""
        expected = self.found + self.false_negatives
        return self.found / expected if expected else 0.0

    def f_beta(self, beta: float = DEFAULT_BETA) -> float:
        """Return the weighted harmonic mean of precision and recall (0 when either is 0)."""
        squared = beta**2
        weighted = squared * self.precision + self.recall
        if not weighted:
            return 0.0
        return (1 + squared) * self.precision * self.recall / weighted


def score_sets(source: str, *, made: AbstractSet[Edit], expected: AbstractSet[Edit]) -> EditScore:
    """Return the agreement of two sets of changes of *source*, compared as :func:`change_key`.

    Args:
        source: The text both sets of changes are changes of.
        made: The changes of one text.
        expected: The changes of the other.

    Returns:
        The :class:`EditScore` of *made* against *expected*.
    """
    positions = word_positions(tokenize(source))
    mine = {change_key(edit, positions) for edit in made}
    theirs = {change_key(edit, positions) for edit in expected}
    hits = len(mine & theirs)
    return EditScore(
        true_positives=hits,
        false_positives=len(mine) - hits,
        found=hits,
        false_negatives=len(theirs) - hits,
    )


def score_edits(source: str, model_text: str, reference: str) -> EditScore:
    """Return the edit agreement of *model_text* with *reference*, both derived from *source*.

    Args:
        source: The text both corrections start from.
        model_text: What the writing model produced.
        reference: The reference correction of the same text.

    Returns:
        The :class:`EditScore` of the model's changes against the reference's.
    """
    return score_sets(source, made=edits(source, model_text), expected=edits(source, reference))


def apply_alternatives(
    source: str,
    *,
    made: AbstractSet[Edit],
    expected: AbstractSet[Edit],
    alternatives: AbstractSet[Edit],
) -> tuple[EditScore, int]:
    """Return the edit score of an answer whose key accepts other annotators' fixes, and how many.

    An accepted alternative is a second annotator's correction, so it counts as a reference of its
    own.  The counts are made on the answer's side: an answer change is a true positive when it is
    a reference change or an accepted alternative, and a false positive otherwise.  A reference
    change is found when the answer made it or made an alternative that overlaps it, and a false
    negative otherwise.  An alternative that overlaps no reference change is optional — a true
    positive for an answer that makes it, nothing for one that leaves it out — so recall stays at
    most 1 and no answer is punished for leaving it out.

    Args:
        source: The text every edit is a change of.
        made: The answer's changes, as :func:`edits` returns them.
        expected: The reference's changes.
        alternatives: The key's accepted alternatives, as token edits of *source*.

    Returns:
        The score, and how many of the alternatives the answer made that the reference does not.
    """
    positions = word_positions(tokenize(source))
    mine = {change_key(edit, positions) for edit in made}
    theirs = {change_key(edit, positions) for edit in expected}
    accepted = {change_key(edit, positions) for edit in alternatives}
    taken = mine & accepted
    true_positives = len(mine & (theirs | accepted))
    found = sum(
        1
        for key in theirs
        if key in mine
        or any(spans_touch((key[0], key[1]), (other[0], other[1])) for other in taken)
    )
    return (
        EditScore(
            true_positives=true_positives,
            false_positives=len(mine) - true_positives,
            found=found,
            false_negatives=len(theirs) - found,
        ),
        len(taken - theirs),
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


@dataclass(frozen=True)
class EditMatch:
    """What a model made of one change that the reference correction makes."""

    edit: Edit
    """The reference change: tokens ``start`` to ``end`` of the source and their replacement."""

    exact: bool
    """The model made this very change: the same place in words and the same words."""

    located: bool
    """The model changed at least some of the same words, with the same or another replacement."""


def match_edits(
    source: str,
    model_text: str,
    reference: str,
    *,
    made: AbstractSet[Edit] | None = None,
    expected: AbstractSet[Edit] | None = None,
) -> list[EditMatch]:
    """Return, for every change of the reference, whether the model made or at least located it.

    ``exact`` is what the F-score counts.  ``located`` separates "did not notice the error" from
    "noticed it and fixed it differently", which the F-score scores alike (both are a miss).

    Args:
        source: The text both corrections start from.
        model_text: What the writing model produced.
        reference: The reference correction of the same text.
        made: The changes of *source* to *model_text*, when the caller has already computed them.
        expected: The changes of *source* to *reference*, when the caller has already computed them.

    Returns:
        One entry per reference change, in text order.
    """
    positions = word_positions(tokenize(source))
    made = edits(source, model_text) if made is None else made
    expected = edits(source, reference) if expected is None else expected
    made_keys = {change_key(edit, positions) for edit in made}
    made_spans = [(positions[start], positions[end]) for start, end, _ in made]
    matches = []
    for edit in sorted(expected):
        key = change_key(edit, positions)
        exact = key in made_keys
        matches.append(
            EditMatch(
                edit=edit,
                exact=exact,
                located=exact or any(spans_touch((key[0], key[1]), other) for other in made_spans),
            )
        )
    return matches
