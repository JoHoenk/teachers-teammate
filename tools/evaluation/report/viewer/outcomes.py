"""What a model's edits and the reference's edits came to, and the sentences they sit in.

The scoring counts three numbers per text: changes the reference makes too, changes it does not,
and changes it makes that the model missed.  A reader of the report needs the same judgement per
edit, because "noticed the error and fixed it differently" and "did not notice it" are one miss in
the F-score and two very different things to a teacher:

=================  ============================================  ==============================
outcome of a model  meaning                                       outcome of a reference edit
edit
=================  ============================================  ==============================
``exact``           the same span with the same replacement      ``fixed``
``located``         touches a reference edit, other replacement  ``located``
``extra``           touches no reference edit                    ``missed``
=================  ============================================  ==============================

The rules are :func:`~teachers_teammate.domain.proofreading.edit_agreement.match_edits` and
:func:`~teachers_teammate.domain.proofreading.edit_agreement.spans_touch`,
unchanged, so on a text with errors the outcomes add up to the row's stored ``tp`` and ``fp``.  A
text without a reference error stores neither (it is scored by what is left alone), so there every
edit is ``extra``.

An edit belongs to the sentence its start lies in.  An insertion at a sentence boundary belongs to
the sentence that ends there, because a period added at the end of a sentence is that sentence's
change.  An edit that runs past the end of its sentence merges the sentences it covers into one
row, so every version of a text is shown in the same rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from teachers_teammate.domain.proofreading.edit_agreement import (
    char_to_token,
    edits,
    ordered_edits,
    spans_touch,
    word_positions,
)
from teachers_teammate.domain.proofreading.review import CorrectionReview
from teachers_teammate.domain.proofreading.sentences import (
    SentenceRow,
    Span,
    row_of,
)
from teachers_teammate.domain.proofreading.word_diff import Edit, tokenize

from ...core.labels import ReferenceLabel
from ..score import notes_of

EXACT = "exact"
LOCATED = "located"
EXTRA = "extra"
FIXED = "fixed"
MISSED = "missed"


@dataclass(frozen=True)
class ModelEdit:
    """One change a model made, with what explains it and what it came to."""

    start: int
    end: int
    text: str
    """The replacement, as text; empty for a deletion."""

    category: str
    explanation: str
    outcome: str
    """``exact``, ``located`` or ``extra``."""

    ref: int | None
    """The reference edit this one touches, as an index into :attr:`Outcomes.reference`."""

    row: int
    """The row of the text view this edit is shown in."""


@dataclass(frozen=True)
class ReferenceEdit:
    """One change the reference correction makes, with its corpus label and what came of it."""

    index: int
    start: int
    end: int
    text: str
    code: str
    """The corpus label (``R:DET:FORM``); empty when no annotated edit matched this change."""

    category: str
    """The app's category for the label (``grammar``, ``spelling``…); empty when unlabelled."""

    outcome: str
    """``fixed``, ``located`` or ``missed``."""

    row: int


@dataclass(frozen=True)
class Orphan:
    """A note that explains no change: the model claimed an edit it did not make."""

    category: str
    explanation: str


@dataclass(frozen=True)
class Outcomes:
    """One version of one text: its rows, its edits, the reference's edits and its orphans."""

    rows: tuple[SentenceRow, ...]
    edits: tuple[ModelEdit, ...]
    reference: tuple[ReferenceEdit, ...]
    orphans: tuple[Orphan, ...]

    @property
    def counts(self) -> tuple[int, int, int]:
        """Return ``(exact, located, extra)`` over the model's edits."""
        return (
            sum(1 for edit in self.edits if edit.outcome == EXACT),
            sum(1 for edit in self.edits if edit.outcome == LOCATED),
            sum(1 for edit in self.edits if edit.outcome == EXTRA),
        )

    @property
    def reference_counts(self) -> tuple[int, int, int]:
        """Return ``(fixed, located, missed)`` over the reference's edits."""
        return (
            sum(1 for edit in self.reference if edit.outcome == FIXED),
            sum(1 for edit in self.reference if edit.outcome == LOCATED),
            sum(1 for edit in self.reference if edit.outcome == MISSED),
        )


def edits_by_label(source: str, labels: Sequence[ReferenceLabel]) -> dict[Edit, ReferenceLabel]:
    """Return the reference's changes keyed by the token edit each label names.

    A label stores character offsets and its replacement as text, while the scoring's edits are
    token indices and replacement tokens, so the two are converted here, once.  A label whose span
    does not lie on token boundaries names no edit and is left out.
    """
    found: dict[Edit, ReferenceLabel] = {}
    for label in labels:
        start = char_to_token(source, label.start)
        end = char_to_token(source, label.end)
        if start is None or end is None:
            continue
        found[(start, end, tuple(tokenize(label.replacement)))] = label
    return found


def reference_edits(
    source: str, reference: str, labels: Sequence[ReferenceLabel]
) -> tuple[tuple[Edit, str, str], ...]:
    """Return the reference's changes in text order, each with its corpus code and app category.

    The changes are the word diff of source → reference, the same list the scoring compares
    against; a change no annotated edit matched keeps an empty code and category.  A text without
    a reference correction (a ``correct`` text) has no reference change at all.
    """
    if not reference.strip():
        return ()
    by_edit = edits_by_label(source, labels)
    found: list[tuple[Edit, str, str]] = []
    for edit in sorted(edits(source, reference)):
        label = by_edit.get(edit)
        found.append((edit, label.code if label else "", label.category if label else ""))
    return tuple(found)


def changes_of(
    source: str, output: str, review: CorrectionReview | None
) -> tuple[dict[str, Any], ...]:
    """Return the changes of one version, as :func:`outcomes` reads them.

    A change is the category and the explanation of one edit, or of a note that explains no edit
    (its ``edit`` is then ``None``).  The edit is named the way a stored row names it — its token
    span and its replacement as text — so a change read from a review and a change read from a
    stored run file are the same value.

    Args:
        source: The text before proofreading.
        output: The version's text, without the marks the model added.
        review: The model's explanations, or ``None`` in corrections-only mode.
    """
    made = ordered_edits(source, output)
    found: list[dict[str, Any]] = []
    for index, category, explanation, _orphan in notes_of(source, output, review):
        edit = None if index is None else [made[index][0], made[index][1], "".join(made[index][2])]
        found.append({"edit": edit, "category": category, "explanation": explanation})
    return tuple(found)


def outcomes(
    source: str,
    reference: str,
    labels: Sequence[ReferenceLabel],
    corrected: str,
    changes: Sequence[Mapping],
    rows: Sequence[SentenceRow],
) -> Outcomes:
    """Return what one version of one text came to.

    Args:
        source: The text before proofreading.
        reference: The reference correction of the same text.
        labels: The corpus labels of the reference's changes.
        corrected: The version's text, without the marks the model added.
        changes: The changes of the version, which carry the category and the explanation of each
            edit (:func:`changes_of`); a version of text mode has none, and its edits are shown
            without them.
        rows: The rows of the text view, shared by every version of the text
            (:func:`sentence_rows`).
    """
    positions = word_positions(tokenize(source))
    expected = reference_edits(source, reference, labels)
    expected_set = {edit for edit, _, _ in expected}
    expected_spans = [_words(positions, edit) for edit, _, _ in expected]
    made = ordered_edits(source, corrected)
    made_set = set(made)
    made_spans = [_words(positions, edit) for edit in made]
    notes, orphans = _notes(changes)
    edits: list[ModelEdit] = []
    for edit, span in zip(made, made_spans, strict=True):
        start, end, replacement = edit
        touched = next(
            (index for index, other in enumerate(expected_spans) if spans_touch(span, other)),
            None,
        )
        category, explanation = notes.get(edit, ("", ""))
        edits.append(
            ModelEdit(
                start=start,
                end=end,
                text="".join(replacement),
                category=category,
                explanation=explanation,
                outcome=(
                    EXACT if edit in expected_set else (LOCATED if touched is not None else EXTRA)
                ),
                ref=touched,
                row=row_of(rows, start, end),
            )
        )
    reference_outcomes = tuple(
        ReferenceEdit(
            index=index,
            start=edit[0],
            end=edit[1],
            text="".join(edit[2]),
            code=code,
            category=category,
            outcome=(
                FIXED
                if edit in made_set
                else (LOCATED if any(spans_touch(span, other) for other in made_spans) else MISSED)
            ),
            row=row_of(rows, edit[0], edit[1]),
        )
        for index, ((edit, code, category), span) in enumerate(
            zip(expected, expected_spans, strict=True)
        )
    )
    return Outcomes(
        rows=tuple(rows), edits=tuple(edits), reference=reference_outcomes, orphans=orphans
    )


def _words(positions: Sequence[int], edit: Edit) -> Span:
    """Return the word span of a token edit, so two edits compare by the words they touch."""
    return (positions[edit[0]], positions[edit[1]])


def _notes(changes: Sequence[Mapping]) -> tuple[dict[Edit, tuple[str, str]], tuple[Orphan, ...]]:
    """Return the category and explanation of each change, and the orphans.

    A change names its edit as ``[start, end, replacement]`` with the replacement as text; the
    scoring's edits carry the replacement as tokens, so the text is tokenised again, which gives
    back the tokens the diff produced.
    """
    notes: dict[Edit, tuple[str, str]] = {}
    orphans: list[Orphan] = []
    for change in changes:
        category = str(change.get("category", ""))
        explanation = str(change.get("explanation", ""))
        edit = change.get("edit")
        if edit is None:
            orphans.append(Orphan(category=category, explanation=explanation))
            continue
        start, end, replacement = edit
        notes[(int(start), int(end), _tokens(replacement))] = (category, explanation)
    return notes, tuple(orphans)


def _tokens(replacement: object) -> tuple[str, ...]:
    """Return a stored replacement as the tokens the diff produced."""
    if isinstance(replacement, str):
        text = replacement
    elif isinstance(replacement, (list, tuple)):
        text = "".join(str(part) for part in replacement)
    else:
        text = str(replacement)
    return tuple(tokenize(text))


__all__ = [
    "EXACT",
    "EXTRA",
    "FIXED",
    "LOCATED",
    "MISSED",
    "ModelEdit",
    "Orphan",
    "Outcomes",
    "ReferenceEdit",
    "changes_of",
    "edits_by_label",
    "outcomes",
    "reference_edits",
]
