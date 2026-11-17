"""Unit tests for the items the Check tab lists (``application/check_items.py``)."""

from __future__ import annotations

from teachers_teammate.application.check_items import CheckItem, ItemKind, check_items, group_minor
from teachers_teammate.domain.proofreading.edits import Edit, EditPart, edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    align,
    edit_note,
)

RAW = "Der bunte Hund bellt laut. Er lauft schnell."
CORRECTED = "Der braune Hund bellt laut. Er läuft schnell."


def _edit_of(edit_id: int, start: int, end: int, original: str, corrected: str) -> Edit:
    """Return a one-part edit over ``raw[start:end]``."""
    return Edit(
        id=edit_id,
        parts=(EditPart(raw_start=start, raw_end=end, original=original, corrected=corrected),),
        sentences=(0,),
    )


def _review(*categories: str) -> CorrectionReview:
    """Return the review of :data:`RAW` against :data:`CORRECTED`, one note per edit."""
    return CorrectionReview(
        edits=tuple(
            edit_note(edit, category=category, explanation=f"weil {category}")
            for edit, category in zip(edits(RAW, CORRECTED), categories, strict=False)
        )
    )


def _unclear(start: int, end: int, expression: str = "Hund") -> UnclearNote:
    """Return an unclear note over ``raw[start:end]``."""
    return UnclearNote(
        raw_start=start,
        raw_end=end,
        expression=expression,
        why="Welcher Hund?",
        recommendation="Köter",
    )


def _kinds(items: tuple[CheckItem, ...]) -> list[ItemKind]:
    """Return the kinds of *items*, in order."""
    return [item.kind for item in items]


def test_one_item_per_edit_however_many_hunks_it_has() -> None:
    """
    Given  a review whose first edit changes two words
    When   the items are built
    Then   that edit is one item, with both of its spans
    """
    raw = "Ich habe ein grosse Hund gesehen."
    corrected = "Ich habe einen großen Hund gesehen."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, corrected)[0], category="grammar", explanation="Akkusativ."),)
    )

    items = check_items(align(raw, corrected, review), review_complete=True)

    assert len(items) == 1
    assert items[0].kind is ItemKind.CORRECTION
    assert items[0].before == "ein grosse"
    assert items[0].after == "einen großen"
    assert items[0].corrected_spans == ((9, 14), (15, 21))
    assert items[0].explanation == "Akkusativ."


def test_items_are_numbered_in_text_order() -> None:
    """
    Given  a review with two edits, an unclear expression and a note whose words are gone
    When   the items are built
    Then   they are numbered in the order the text marks them
    """
    review = CorrectionReview(
        edits=(
            *_review("word_choice", "spelling").edits,
            edit_note(_edit_of(3, 21, 25, "laut", "laut"), category="other", explanation="Weg."),
        ),
        unclear=(_unclear(10, 14),),
    )

    items = check_items(align(RAW, CORRECTED, review), review_complete=True)

    assert [item.key for item in items] == ["edit:1", "unclear:1", "taken:3", "edit:2"]
    assert [item.number for item in items] == [1, 2, 3, 4]
    assert _kinds(items) == [
        ItemKind.CORRECTION,
        ItemKind.UNCLEAR,
        ItemKind.TAKEN_OUT,
        ItemKind.CORRECTION,
    ]


def test_a_taken_out_item_has_the_notes_words_and_no_corrected_span() -> None:
    """
    Given  a note whose words are no longer changed in the text
    When   the items are built
    Then   it is a taken-out item with the note's words and no span in the corrected text
    """
    review = CorrectionReview(
        edits=(
            edit_note(_edit_of(1, 21, 25, "laut", "leise"), category="word_choice", explanation=""),
        )
    )

    items = check_items(align(RAW, RAW, review), review_complete=True)

    assert len(items) == 1
    assert items[0].kind is ItemKind.TAKEN_OUT
    assert items[0].before == "laut"
    assert items[0].after == "leise"
    assert items[0].raw_span == (21, 25)
    assert items[0].corrected_spans == ()


def test_a_hunk_the_review_does_not_know_is_the_teachers_own() -> None:
    """
    Given  a complete review that does not explain one change of the text
    When   the items are built
    Then   that change is the teacher's own
    """
    items = check_items(align(RAW, CORRECTED, _review("word_choice")), review_complete=True)

    assert _kinds(items) == [ItemKind.CORRECTION, ItemKind.OWN]
    assert items[1].before == "lauft"
    assert items[1].after == "läuft"


def test_without_a_review_a_change_is_a_correction_without_explanation() -> None:
    """
    Given  a text with changes and no review at all
    When   the items are built
    Then   every change is a correction without explanation, not the teacher's own
    """
    items = check_items(align(RAW, CORRECTED, CorrectionReview()), review_complete=False)

    assert _kinds(items) == [ItemKind.UNEXPLAINED, ItemKind.UNEXPLAINED]


def test_an_incomplete_review_makes_unknown_hunks_unexplained() -> None:
    """
    Given  a review that explains only one of two changes, reported as incomplete
    When   the items are built
    Then   the unexplained change is a correction without explanation
    """
    items = check_items(align(RAW, CORRECTED, _review("word_choice")), review_complete=False)

    assert _kinds(items) == [ItemKind.CORRECTION, ItemKind.UNEXPLAINED]


def test_a_change_the_teacher_changed_again_is_marked_as_changed() -> None:
    """
    Given  a note whose words the teacher replaced by other words
    When   the items are built
    Then   the item is a changed one, with the words now there and no explanation
    """
    raw, corrected = "Der bunte Hund", "Der schwarze Hund"
    review = CorrectionReview(
        edits=(
            edit_note(
                _edit_of(1, 4, 9, "bunte", "braune"), category="word_choice", explanation="Farbe."
            ),
        )
    )

    items = check_items(align(raw, corrected, review), review_complete=True)

    assert len(items) == 1
    assert items[0].kind is ItemKind.CHANGED
    assert items[0].before == "bunte"
    assert items[0].after == "schwarze"
    assert items[0].explanation == ""


def test_an_unclear_expression_that_was_not_found_goes_last() -> None:
    """
    Given  an unclear expression the text does not contain
    When   the items are built
    Then   it is the last item, after everything the text marks
    """
    review = CorrectionReview(unclear=(_unclear(0, 0, "Nirgends"),))

    items = check_items(align(RAW, CORRECTED, review), review_complete=False)

    assert items[-1].kind is ItemKind.UNCLEAR
    assert items[-1].before == "Nirgends"


def test_an_unclear_item_carries_its_why_and_suggestion() -> None:
    """
    Given  an unclear expression with a suggestion the model is unsure about
    When   the items are built
    Then   the item carries the reason, the suggestion and the doubt
    """
    note = UnclearNote(
        raw_start=10,
        raw_end=14,
        expression="Hund",
        why="Welcher Hund?",
        recommendation="Köter",
        is_suggestion=True,
    )

    items = check_items(align(RAW, RAW, CorrectionReview(unclear=(note,))), review_complete=False)

    assert len(items) == 1
    assert items[0].why == "Welcher Hund?"
    assert items[0].suggestion == "Köter"
    assert items[0].suggestion_uncertain is True


def test_minor_changes_are_folded_into_one_group() -> None:
    """
    Given  a review with a spelling change and a word choice
    When   the minor items are asked for
    Then   only the spelling change is in the group, and it is marked minor
    """
    items = check_items(
        align(RAW, CORRECTED, _review("spelling", "word_choice")), review_complete=True
    )

    minor = group_minor(items)

    assert [item.key for item in minor] == ["edit:1"]
    assert minor[0].is_minor is True
    assert [item.is_minor for item in items] == [True, False]


def test_an_edit_without_a_note_is_minor_when_it_touches_no_word() -> None:
    """
    Given  a text whose only change is an added comma, with no review
    When   the items are built
    Then   the change is minor, so the list folds it away
    """
    items = check_items(
        align("Der Hund bellt", "Der Hund, bellt", CorrectionReview()), review_complete=False
    )

    assert len(items) == 1
    assert items[0].is_minor is True
