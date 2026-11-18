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

    items = check_items(align(raw, corrected, review))

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

    items = check_items(align(RAW, CORRECTED, review))

    assert [item.key for item in items] == ["edit:1", "unclear:1", "edit:3", "edit:2"]
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

    items = check_items(align(RAW, RAW, review))

    assert len(items) == 1
    assert items[0].kind is ItemKind.TAKEN_OUT
    assert items[0].before == "laut"
    assert items[0].after == "leise"
    assert items[0].raw_span == (21, 25)
    assert items[0].corrected_spans == ()


def test_a_hunk_the_review_does_not_know_is_the_teachers_own() -> None:
    """
    Given  a text the teacher changed beyond the machine's answer
    When   the items are built
    Then   the change the machine did not make is the teacher's own
    """
    raw = "Der bunte Hund bellt laut."
    machine = "Der braune Hund bellt laut."
    teacher = "Der braune Hund bellt sehr laut."
    review = CorrectionReview(
        edits=(edit_note(edits(raw, machine)[0], category="word_choice", explanation="Farbe."),)
    )

    items = check_items(align(raw, teacher, review, machine_plain=machine))

    assert _kinds(items) == [ItemKind.CORRECTION, ItemKind.OWN]
    assert items[1].before == ""
    assert items[1].after == "sehr"


def test_a_change_of_the_machine_without_a_note_is_not_the_teachers() -> None:
    """
    Given  a text with the machine's changes and no review at all
    When   the items are built
    Then   every change is a correction without explanation, not the teacher's own
    """
    items = check_items(align(RAW, CORRECTED, CorrectionReview(), machine_plain=CORRECTED))

    assert _kinds(items) == [ItemKind.UNEXPLAINED, ItemKind.UNEXPLAINED]


def test_a_review_that_explains_one_of_two_changes_leaves_the_other_unexplained() -> None:
    """
    Given  a review that explains only one of the machine's two changes
    When   the items are built
    Then   the unexplained change is a correction without explanation
    """
    items = check_items(align(RAW, CORRECTED, _review("word_choice"), machine_plain=CORRECTED))

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

    items = check_items(align(raw, corrected, review, machine_plain="Der braune Hund"))

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

    items = check_items(align(RAW, CORRECTED, review))

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

    items = check_items(align(RAW, RAW, CorrectionReview(unclear=(note,))))

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
    items = check_items(align(RAW, CORRECTED, _review("spelling", "word_choice")))

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
    items = check_items(align("Der Hund bellt", "Der Hund, bellt", CorrectionReview()))

    assert len(items) == 1
    assert items[0].is_minor is True


def test_an_unexplained_change_keeps_its_key_when_an_earlier_one_is_taken_back() -> None:
    """
    Given  two changes of the model that the review does not explain
    When   the teacher takes the first one back
    Then   the second keeps the key it had, so what was looked at stays attached to it
    """
    raw = "aa bb cc dd"
    machine = "AA bb CC dd"
    taken_back = "aa bb CC dd"

    before = check_items(align(raw, machine, CorrectionReview(), machine_plain=machine))
    after = check_items(align(raw, taken_back, CorrectionReview(), machine_plain=machine))

    assert [item.before for item in before] == ["aa", "cc"]
    assert [item.before for item in after] == ["cc"]
    assert after[0].key == before[1].key
    assert before[0].key != before[1].key


def test_a_correction_keeps_its_key_when_the_teacher_takes_it_out() -> None:
    """
    Given  a correction the teacher has looked at
    When   the teacher takes it out of the text
    Then   the item is the same item: its key does not change, so it stays looked at
    """
    corrected = RAW.replace("laut", "leise")
    review = CorrectionReview(
        edits=(
            edit_note(_edit_of(1, 21, 25, "laut", "leise"), category="word_choice", explanation=""),
        )
    )

    inside = check_items(align(RAW, corrected, review))
    taken_out = check_items(align(RAW, RAW, review))

    assert [item.kind for item in inside] == [ItemKind.CORRECTION]
    assert [item.kind for item in taken_out] == [ItemKind.TAKEN_OUT]
    assert inside[0].key == taken_out[0].key == "edit:1"
