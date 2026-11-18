"""The items the Check tab lists: one per thing the text marks.

The review is a list of hunks; a teacher reads a list of *things that happened to the text*.  The
two are not the same: an edit of several hunks (a moved word, a phrase reworded) is one thing, a
hunk the review does not know is the teacher's own change, and a note whose words are no longer
changed is a correction that was taken out.  This module folds the aligned review into those
items, in text order, so the list and the marks in the text number the same things.

Pure: no Qt, no I/O.  The offsets are those of the plain text ``align()`` saw.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum

from ..domain.proofreading.review import (
    MINOR_CATEGORIES,
    AlignedChange,
    AlignedReview,
    AlignedUnclear,
    EditNote,
)

#: Sort position of an expression the model quoted but the text does not contain: it goes last.
_NOT_FOUND = 1 << 30

#: The key of the row that folds the minor changes: one thing to look at, not one per comma.
MINOR_KEY = "minor"


class ItemKind(Enum):
    """What kind of thing an item of the list is."""

    CORRECTION = "correction"
    """A change the model made and explained."""
    UNEXPLAINED = "unexplained"
    """A change the model made without an explanation the app can trust."""
    OWN = "own"
    """A change the teacher made themselves."""
    CHANGED = "changed"
    """A change the model made that the teacher then changed again."""
    TAKEN_OUT = "taken_out"
    """A correction the teacher took out: the student's words stand there again."""
    UNCLEAR = "unclear"
    """Wording whose meaning a reader cannot tell."""


@dataclass(frozen=True)
class CheckItem:
    """One thing the text marks, as the list shows it."""

    key: str
    """Stable identity while the text is: ``"edit:3"``, ``"own:7"``, ``"unclear:2"``, ``"taken:5"``."""
    kind: ItemKind
    number: int
    """Position in text order, 1..n over all items."""
    before: str
    """The student's words; empty for an insertion."""
    after: str
    """The corrected words; empty for a deletion."""
    category: str
    """One of ``CHANGE_CATEGORIES``, or ``""`` where the item has none."""
    explanation: str
    """Why the change was made; only where the text still shows the model's own wording."""
    optional: bool
    """``True`` for a comma that is a matter of style, not a mistake."""
    needed: bool | None
    """The model's own doubt about the change; ``False`` means "not sure"."""
    raw_span: tuple[int, int]
    """Where the student's words are; zero width for an insertion."""
    corrected_spans: tuple[tuple[int, int], ...]
    """One span per hunk, in the corrected text; empty for a taken-out correction."""
    why: str = ""
    """Why the wording is unclear (unclear items only)."""
    suggestion: str = ""
    """The model's recommendation (unclear items only)."""
    suggestion_uncertain: bool = False
    """``True`` when the model could not tell the intended meaning and only suggests."""
    is_minor: bool = False
    """``True`` for a spelling or punctuation change, which the list folds into one row."""


def check_items(aligned: AlignedReview) -> tuple[CheckItem, ...]:
    """Return the items of *aligned*, in text order and numbered 1..n.

    One item per stored edit, however many hunks it has.  A hunk the review does not know is the
    teacher's own change or a correction without explanation, and ``align`` decides which: it
    compares the hunk with the machine's own answer, so the two are told apart from the texts
    rather than from whether the review is complete.
    """
    placed = [
        *_change_items(aligned.changes),
        *_taken_out_items(aligned.orphan_notes),
        *_unclear_items(aligned.unclear),
    ]
    placed.sort(key=lambda pair: pair[0])
    return tuple(
        CheckItem(
            key=item.key,
            kind=item.kind,
            number=number,
            before=item.before,
            after=item.after,
            category=item.category,
            explanation=item.explanation,
            optional=item.optional,
            needed=item.needed,
            raw_span=item.raw_span,
            corrected_spans=item.corrected_spans,
            why=item.why,
            suggestion=item.suggestion,
            suggestion_uncertain=item.suggestion_uncertain,
            is_minor=item.is_minor,
        )
        for number, (_position, item) in enumerate(placed, 1)
    )


def group_minor(items: Sequence[CheckItem]) -> tuple[CheckItem, ...]:
    """Return the items the minor group row folds: the spelling and punctuation changes."""
    return tuple(item for item in items if item.is_minor)


def counts_for_looked_at(item: CheckItem) -> bool:
    """Whether *item* is a row the teacher has to look at.

    Their own changes are not something to look at — they made them — and the spelling and
    punctuation changes count once, as the row that folds them, not one per comma.
    """
    return not item.is_minor and item.kind is not ItemKind.OWN


def countable_keys(items: Sequence[CheckItem]) -> tuple[str, ...]:
    """Return the keys the "looked at" count counts: one per row that counts, the group as one.

    The keys are those of the rows the list shows, so the count and the list never disagree: a
    single spelling change is a row of its own and is not folded, so it does not count either.
    """
    keys = [item.key for item in items if counts_for_looked_at(item)]
    if sum(1 for item in items if item.is_minor) > 1:
        keys.append(MINOR_KEY)
    return tuple(keys)


def next_unseen(rows: Sequence[str], looked_at: frozenset[str], after: str) -> str:
    """Return the first row after *after* that was not looked at, wrapping to the top.

    *rows* are the keys of the rows that count for looked-at, in list order.  *after* is the key
    of the chosen row; a key the rows do not have — the teacher's own change, which is not a
    correction to walk to — starts the walk at the top.  ``""`` when every row was looked at.
    """
    if not rows:
        return ""
    start = rows.index(after) + 1 if after in rows else 0
    for key in (*rows[start:], *rows[:start]):
        if key not in looked_at:
            return key
    return ""


def first_unseen(rows: Sequence[str], looked_at: frozenset[str]) -> str:
    """Return the first row that was not looked at, or the first row when all were.

    ``""`` when there is no row at all.
    """
    for key in rows:
        if key not in looked_at:
            return key
    return rows[0] if rows else ""


def _change_items(changes: Sequence[AlignedChange]) -> list[tuple[int, CheckItem]]:
    """Return one item per stored edit, and one per hunk the review does not know."""
    groups: dict[int, list[AlignedChange]] = {}
    placed: list[tuple[int, CheckItem]] = []
    for change in changes:
        if change.edit_id is None:
            placed.append((change.hunk.raw_start, _own_item(change)))
        else:
            groups.setdefault(change.edit_id, []).append(change)
    placed.extend((_raw_span(group)[0], _edit_item(group)) for group in groups.values())
    return placed


def _edit_item(group: Sequence[AlignedChange]) -> CheckItem:
    """Return the one item of a stored edit, however many hunks it has."""
    note = group[0].note
    changed = any(change.changed_afterwards for change in group)
    return CheckItem(
        key=f"edit:{group[0].edit_id}",
        kind=ItemKind.CHANGED if changed else ItemKind.CORRECTION,
        number=0,
        before=_words(change.hunk.original for change in group),
        after=_words(change.hunk.corrected for change in group),
        category=note.category if note is not None else "",
        explanation=note.explanation if group[0].explains_text and note is not None else "",
        optional=note.optional if note is not None else False,
        needed=note.needed if note is not None else None,
        raw_span=_raw_span(group),
        corrected_spans=tuple(
            (change.hunk.corrected_start, change.hunk.corrected_end) for change in group
        ),
        is_minor=group[0].is_minor,
    )


def _own_item(change: AlignedChange) -> CheckItem:
    """Return the item of a hunk the review does not know."""
    return CheckItem(
        # The words' place in the student's text, not their number among the changes: taking a
        # change back must not give the ones after it other keys.
        key=f"own:{change.hunk.raw_start}-{change.hunk.raw_end}",
        kind=ItemKind.OWN if change.by_teacher else ItemKind.UNEXPLAINED,
        number=0,
        before=change.hunk.original,
        after=change.hunk.corrected,
        category="",
        explanation="",
        optional=False,
        needed=None,
        raw_span=(change.hunk.raw_start, change.hunk.raw_end),
        corrected_spans=((change.hunk.corrected_start, change.hunk.corrected_end),),
        is_minor=change.is_minor,
    )


def _taken_out_items(notes: Sequence[EditNote]) -> list[tuple[int, CheckItem]]:
    """Return one item per note whose words are no longer changed: the teacher took it out."""
    return [
        (
            note.raw_start,
            CheckItem(
                # The note's own key: taking a correction out does not make it another item,
                # so that the teacher has looked at it stays true.
                key=f"edit:{note.id}",
                kind=ItemKind.TAKEN_OUT,
                number=0,
                before=note.original,
                after=note.corrected,
                category=note.category,
                explanation=note.explanation,
                optional=note.optional,
                needed=note.needed,
                raw_span=(note.raw_start, note.raw_end),
                corrected_spans=(),
                is_minor=note.category in MINOR_CATEGORIES,
            ),
        )
        for note in notes
    ]


def _unclear_items(unclear: Sequence[AlignedUnclear]) -> list[tuple[int, CheckItem]]:
    """Return one item per unclear expression; one that was not found in the text goes last."""
    return [
        (
            item.start if item.anchored and item.start is not None else _NOT_FOUND,
            CheckItem(
                key=f"unclear:{item.number}",
                kind=ItemKind.UNCLEAR,
                number=0,
                before=item.note.expression,
                after=item.note.recommendation,
                category="",
                explanation="",
                optional=False,
                needed=None,
                raw_span=_unclear_span(item),
                corrected_spans=(),
                why=item.note.why,
                suggestion=item.note.recommendation,
                suggestion_uncertain=item.note.is_suggestion,
            ),
        )
        for item in unclear
    ]


def _unclear_span(item: AlignedUnclear) -> tuple[int, int]:
    """Return where the unclear expression stands, or where the model said it stands."""
    if item.start is None or item.end is None:
        return (item.note.raw_start, item.note.raw_end)
    return (item.start, item.end)


def _raw_span(group: Sequence[AlignedChange]) -> tuple[int, int]:
    """Return the span of the student's words that the hunks of one edit cover."""
    return (
        min(change.hunk.raw_start for change in group),
        max(change.hunk.raw_end for change in group),
    )


def _words(parts: Iterable[str]) -> str:
    """Return the words of several hunks as one phrase, the way a note writes them."""
    return " ".join(part for part in parts if part)
