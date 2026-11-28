"""The one scoring path: ``score(answer, record, run)``, pure and versioned.

A run file holds what the model, the server and the machine said and nothing else, so every
number a report shows is computed here, from the stored answer and the test set's key.  The run does
not score; the report loader calls this function, so the two cannot disagree.

Explanations are aligned by the app's own :func:`~teachers_teammate.domain.proofreading.review.align`,
so the tools and the teacher's window cannot disagree about which note explains which change: the
same ``is_minor``, the same ``changed_afterwards``, the same orphans.

``SCORER_VERSION`` is bumped by hand when a number changes — a tokenizer or differ change, a new
rule — and the golden numbers of the tests fail until it is.  A bump scores every stored answer
again and runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field, fields
from typing import Any

from teachers_teammate.domain.proofreading.edit_agreement import (
    EditScore,
    apply_alternatives,
    char_edits,
    char_to_token,
    count_explained,
    edits,
    match_edits,
    ordered_edits,
    score_sets,
)
from teachers_teammate.domain.proofreading.marks import added_marks, strip_added_marks
from teachers_teammate.domain.proofreading.review import (
    AlignedReview,
    CorrectionReview,
    align,
)
from teachers_teammate.domain.proofreading.word_diff import Edit, text_offsets, tokenize

from ..core.constants import TextKind
from ..core.formats import FormatError
from ..core.labels import ReferenceLabel
from ..core.modes import Step
from ..core.records import TASK_PROOFREAD, TASKS, Answer, RunHeader, TestRecord
from ..metrics import text_loss
from ..metrics.item_scores import item_scores
from ..metrics.offsets import char_span_to_tokens
from ..metrics.quality import unchanged_sentences

SCORER_VERSION = 5
"""Version of the numbers :func:`score` returns.  Bumped by hand, pinned by golden numbers."""


@dataclass(frozen=True)
class ScoreOptions:
    """What a report may ask the scorer to do differently; the run does not decide these."""

    count_marks: bool = False
    """Count the Markdown marks the model added as changes (``report --count-marks``)."""

    accepted: bool = True
    """Count the key's accepted alternatives (``report --no-accepted`` turns them off)."""


@dataclass(frozen=True)
class Scored:
    """Every number of one answer, as one value a report can carry.

    The counts are counts, not shares: a language's figures are pooled over its texts, and a share
    computed per text and then averaged would not be the same number.
    """

    true_positives: int = 0
    """Changes the answer made that the reference makes too."""

    false_positives: int = 0
    """Changes the answer made that neither the reference nor the key accepts."""

    found: int = 0
    """Reference changes the answer found: made itself, or overlapped by an accepted alternative."""

    false_negatives: int = 0
    """Reference changes the answer did not find."""

    explained: int = 0
    """Changes whose note explains them in non-blank words."""

    needed: int = 0
    """Changes that need an explanation at all (a comma does not)."""

    orphans: int = 0
    """Notes that explain no change in the text: the model claimed an edit it did not make."""

    category_checked: int = 0
    """Errors the answer fixed exactly with a note, whose corpus label maps to a category."""

    category_agreed: int = 0
    """Of those, how many notes carry the category the label maps to."""

    unchanged: int = 0
    """Already-correct sentences that came back unchanged (a ``correct`` text)."""

    unclear: int = 0
    """Unclear expressions the review listed."""

    marks_added: bool = False
    """Whether the answer carries Markdown marks the source does not have."""

    accepted: int = 0
    """Answer edits that match an accepted alternative of the key."""

    explanations_known: bool = True
    """Whether the answer's review could be read, so whether :attr:`explained` means anything.

    A review of a version this tool does not know is kept and written back, but its explanations
    cannot be counted: the number is unknown and not zero, and a report leaves the text out of the
    explanation figures instead of reporting a model that explained nothing.
    """

    scores: Mapping[str, int] = field(default_factory=dict)
    """The numbers of :func:`~..metrics.item_scores.item_scores`: GLEU, the recall halves, left
    alone, coverage, located, and the words of the answer against the reference's — plus the
    count of stretches the app put back, when there were any."""

    @property
    def precision(self) -> float:
        """Share of the answer's changes the key accepts (0 when it made none)."""
        made = self.true_positives + self.false_positives
        return self.true_positives / made if made else 0.0

    @property
    def recall(self) -> float:
        """Share of the reference's changes the answer found (0 when there are none)."""
        expected = self.found + self.false_negatives
        return self.found / expected if expected else 0.0

    def f_beta(self, beta: float = 0.5) -> float:
        """Return the weighted harmonic mean of precision and recall (0 when either is 0)."""
        squared = beta**2
        weighted = squared * self.precision + self.recall
        if not weighted:
            return 0.0
        return (1 + squared) * self.precision * self.recall / weighted

    @property
    def lost_text(self) -> bool | None:
        """Whether the answer lost text, or ``None`` when it was not measured."""
        return text_loss.is_lost(self.scores)

    @property
    def added_text(self) -> bool | None:
        """Whether the answer added text, or ``None`` when it was not measured."""
        return text_loss.is_added(self.scores)

    def to_dict(self) -> dict[str, Any]:
        """Return every number as one flat dict, the way a score cache line stores it.

        The fields are read from the class, so a new count is stored with no other edit.
        """
        record: dict[str, Any] = dict(self.scores)
        for item in fields(self):
            if item.name == "scores":
                continue
            value = getattr(self, item.name)
            stored = _STORED_NAMES.get(item.name, item.name)
            record[stored] = int(value) if isinstance(value, bool) else value
        return record

    @classmethod
    def from_dict(cls, scores: Mapping[str, Any]) -> Scored:
        """Return the numbers a flat score dict holds, as :meth:`to_dict` writes them.

        Whatever the dict holds beyond the counts is the item scores.

        Raises:
            KeyError: if the dict lacks a count.
            TypeError: if a count is not a number.
            ValueError: if a count is not a whole number.
        """
        values: dict[str, Any] = {}
        stored: set[str] = set()
        for item in fields(cls):
            if item.name == "scores":
                continue
            name = _STORED_NAMES.get(item.name, item.name)
            stored.add(name)
            raw = scores[name]
            values[item.name] = bool(int(raw)) if isinstance(item.default, bool) else int(raw)
        values["scores"] = {str(key): value for key, value in scores.items() if key not in stored}
        return cls(**values)


_STORED_NAMES: dict[str, str] = {
    "true_positives": "tp",
    "false_positives": "fp",
    "false_negatives": "fn",
}
"""The fields whose stored name is shorter than their attribute name."""


def scored_from_dict(scores: Mapping[str, Any]) -> Scored:
    """Return the numbers a flat score dict holds, as :meth:`Scored.to_dict` writes them."""
    return Scored.from_dict(scores)


def review_for_counting(
    run: RunHeader, review: CorrectionReview | None, source: str, output: str
) -> CorrectionReview | None:
    """Return the review the explanations are counted against.

    A run that asks for explanations and got none — a review that fell back to corrections only —
    gives the teacher the corrections but nothing that explains them, so its changes count as
    unexplained: it is counted against an empty review.  The rule is applied here and nowhere else,
    so a report of an older run says the same as the run did.
    """
    if review is None and Step.EXPLAIN in run.protocol.steps and output != source:
        return CorrectionReview()
    return review


@dataclass(frozen=True)
class CategoryAgreement:
    """How many found errors the model gave the category the corpus label stands for."""

    checked: int
    """Errors the model fixed exactly, with a note, and whose label maps to a category."""

    agreed: int
    """Of those, the notes whose category is the one the label maps to."""


def category_agreement(
    source: str,
    output: str,
    reference: str,
    review: CorrectionReview | None,
    labels: Sequence[ReferenceLabel],
    *,
    aligned: AlignedReview | None = None,
    made: AbstractSet[Edit] | None = None,
    expected: AbstractSet[Edit] | None = None,
) -> CategoryAgreement:
    """Return how often the model's category matches the corpus label of the errors it fixed.

    Only errors the model fixed *exactly* count (the same words and replacement), because only then
    it is certain which note belongs to which error, and only errors whose label maps to a category
    (not ``other``).  The corpus labels were not made for these six categories, so this is a rough
    diagnostic and not a verdict on a single note.

    Args:
        source: The text before proofreading.
        output: The text after proofreading, without the marks the model added.
        reference: The reference correction of the same text.
        review: The model's explanations, or ``None`` in corrections-only mode.
        labels: The corpus label of every reference change, in characters of *source*.
        aligned: The aligned review of *source* against *output*, when the caller has already
            computed it for another purpose.
        made: The changes of *source* to *output*, when the caller has already computed them.
        expected: The changes of *source* to *reference*, when the caller has already computed them.
    """
    if review is None or not labels:
        return CategoryAgreement(0, 0)
    # A label stores its replacement as text, an edit as the tokens it puts there, so the two are
    # compared as the text they spell: ``("ihrer", "Freundin")`` is ``"ihrer Freundin"``.
    exact = {
        (start, end, "".join(replacement))
        for start, end, replacement in (
            match.edit
            for match in match_edits(source, output, reference, made=made, expected=expected)
            if match.exact
        )
    }
    offsets = text_offsets(source)
    if aligned is None:
        aligned = align(source, output, review)
    noted = {
        change.position: change.note.category
        for change in aligned.changes
        if change.note is not None
    }
    checked = agreed = 0
    for label in labels:
        wanted = label.category
        if wanted in {"", "other"}:
            continue
        start = char_to_token(source, label.start)
        end = char_to_token(source, label.end)
        if start is None or end is None or (start, end, label.replacement) not in exact:
            continue
        given = noted.get(offsets[start])
        if given is None:
            continue
        checked += 1
        agreed += given == wanted
    return CategoryAgreement(checked, agreed)


def score(
    answer: Answer,
    record: TestRecord,
    run: RunHeader,
    options: ScoreOptions | None = None,
) -> Scored:
    """Return every number of *answer*, measured by the scorer of *run*'s task.

    The report loader calls this, so a task's numbers are computed in one place.

    Raises:
        FormatError: if the run's task is not one the tools know how to score.
    """
    scorer = SCORERS.get(run.task)
    if scorer is None:
        raise FormatError(
            f"the run's task {run.task!r} is not one of {', '.join(TASKS)}; "
            "there is no scorer for it"
        )
    return scorer(answer, record, run, options)


def score_row(
    answer: Answer,
    record: TestRecord,
    run: RunHeader,
    options: ScoreOptions | None = None,
) -> Scored:
    """Return every number of *answer*, measured against *record*'s key, for *run*'s protocol.

    This is the proofreading scorer: the report loader reaches it through :func:`score`, and the
    run does not.  It is pure and deterministic, so the same three inputs
    always give the same numbers.

    A failed text is scored as what the teacher got — its ``output``, which is the source — so the
    figures stay the ones the run reported.

    Args:
        answer: The stored answer: the output, the review and the outcome flags.
        record: The test record: the source, the reference, the labels and the sentence spans.
        run: The run's header, for the steps it asked for.
        options: What the report asks differently; the defaults are the run's own rules.

    Returns:
        The numbers, as one :class:`Scored`.
    """
    chosen = options or ScoreOptions()
    source = record.source
    output = answer.output if chosen.count_marks else strip_added_marks(source, answer.output)
    reference = record.reference
    marks = added_marks(source, answer.output)

    loss = text_loss.measure(source, output, reference)
    # The app puts back what the model left out, so the stored output is whole and the numbers
    # above see a whole text; the count keeps the row a lost text all the same.
    left_out = {text_loss.LEFT_OUT: answer.left_out} if answer.left_out else {}
    # The run counts the explanations and the category agreement of every text, a ``correct`` one
    # included: a model that edits a text it was told is correct still claimed an edit, and the
    # stored row says so.  Only the edit counts and the item scores are the ``errors`` texts' own.
    aligned = align(source, output, answer.review) if answer.review is not None else None
    review = review_for_counting(run, answer.review, source, output)
    explained = count_explained(source, output, review, aligned=aligned)
    made = frozenset(ordered_edits(source, output))
    expected = frozenset(ordered_edits(source, reference))
    agreement = category_agreement(
        source,
        output,
        reference,
        answer.review,
        record.labels,
        aligned=aligned,
        made=made,
        expected=expected,
    )
    known = not answer.review_unreadable
    if record.kind != TextKind.ERRORS:
        return Scored(
            explanations_known=known,
            explained=explained.explained,
            needed=explained.needed,
            orphans=explained.orphans,
            category_checked=agreement.checked,
            category_agreed=agreement.agreed,
            unchanged=unchanged_sentences(list(record.sentences), output),
            unclear=_unclear_count(answer),
            marks_added=marks,
            scores={**loss, **left_out},
        )

    edit_score, accepted = _edit_score(
        source, output, reference, record, chosen, made=made, expected=expected
    )
    return Scored(
        explanations_known=known,
        true_positives=edit_score.true_positives,
        false_positives=edit_score.false_positives,
        found=edit_score.found,
        false_negatives=edit_score.false_negatives,
        explained=explained.explained,
        needed=explained.needed,
        orphans=explained.orphans,
        category_checked=agreement.checked,
        category_agreed=agreement.agreed,
        unclear=_unclear_count(answer),
        marks_added=marks,
        accepted=accepted,
        scores={
            **item_scores(
                source,
                output,
                reference,
                sentence_spans=_token_spans(source, record.sentence_spans),
                unchanged_sentences=record.unchanged_sentences,
                made=made,
                expected=expected,
            ),
            **left_out,
        },
    )


SCORERS: dict[str, Callable[..., Scored]] = {TASK_PROOFREAD: score_row}
"""The scorer of each task; a task without one is refused, not guessed at."""


def _edit_score(
    source: str,
    output: str,
    reference: str,
    record: TestRecord,
    options: ScoreOptions,
    *,
    made: AbstractSet[Edit] | None = None,
    expected: AbstractSet[Edit] | None = None,
) -> tuple[EditScore, int]:
    """Return the edit counts of *output* against *reference*, and how many alternatives it made.

    A text without a reference has no counts.  The key's accepted alternatives count as further
    references unless the report turns them off, so an answer is not punished for fixing an error
    the way another annotator fixed it.

    Args:
        source: The text both corrections start from.
        output: What the model produced.
        reference: The reference correction.
        record: The test record, for the key's accepted alternatives.
        options: What the report asks differently.
        made: The changes of *source* to *output*, when the caller has already computed them.
        expected: The changes of *source* to *reference*, when the caller has already computed them.
    """
    if not reference.strip():
        return EditScore(true_positives=0, false_positives=0, found=0, false_negatives=0), 0
    made = edits(source, output) if made is None else made
    expected = edits(source, reference) if expected is None else expected
    if not options.accepted or not record.accepted:
        return score_sets(source, made=made, expected=expected), 0
    return apply_alternatives(
        source,
        made=made,
        expected=expected,
        alternatives=_alternatives(source, record),
    )


def _alternatives(source: str, record: TestRecord) -> frozenset[Edit]:
    """Return the key's accepted alternatives as token edits of *source*.

    Raises:
        ValueError: if a stored span does not lie on token boundaries, which is a defect of the key
            and not of the answer.
    """
    return frozenset(
        (start, end, tuple(tokenize(edit.replacement)))
        for edit in record.accepted
        for start, end in (char_span_to_tokens(source, edit.span),)
    )


def _token_spans(source: str, spans: Sequence[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """Return the token spans of the record's character spans, which is what the metrics count.

    Raises:
        ValueError: if a stored span does not lie on token boundaries, which is a defect of the
            test set and not of the answer.
    """
    if not spans:
        return ()
    found: list[tuple[int, int]] = []
    for start, end in spans:
        first = char_to_token(source, start)
        last = char_to_token(source, end)
        if first is None or last is None or last < first:
            raise ValueError(f"the sentence span {start} to {end} does not lie on token boundaries")
        found.append((first, last))
    return tuple(found)


def _unclear_count(answer: Answer) -> int:
    """Return how many unclear expressions the answer listed, from the review or the stored count."""
    if answer.review is not None:
        return answer.review.unclear_count
    return answer.unclear_count


def notes_of(
    source: str, output: str, review: CorrectionReview | None
) -> tuple[tuple[int | None, str, str, bool], ...]:
    """Return the review's notes as ``(edit index or None, category, explanation, orphan)``.

    A note that explains a change is linked to it by the index of that change in
    :func:`char_edits`; a note that matches no change keeps ``None`` and is flagged, so a reader
    can tell an unplaceable remark from an unlinked one.  The flagged notes are exactly
    the ones :func:`~teachers_teammate.domain.proofreading.edit_agreement.count_explained` counts as orphans.
    """
    if review is None:
        return ()
    aligned = align(source, output, review)
    index_of = {span[0]: position for position, (span, _) in enumerate(char_edits(source, output))}
    records: list[tuple[int | None, str, str, bool]] = []
    for change in aligned.changes:
        if change.note is None:
            continue
        index = index_of.get(change.position)
        records.append((index, change.note.category, change.note.explanation, index is None))
    for note in aligned.orphan_notes:
        records.append((None, note.category, note.explanation, True))
    return tuple(records)


__all__ = [
    "SCORERS",
    "SCORER_VERSION",
    "CategoryAgreement",
    "ScoreOptions",
    "Scored",
    "category_agreement",
    "notes_of",
    "review_for_counting",
    "score",
    "score_row",
    "scored_from_dict",
]
