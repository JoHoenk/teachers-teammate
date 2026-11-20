"""Unit tests for the items the Check tab lists (``application/check_items.py``)."""

from __future__ import annotations

import pytest

from teachers_teammate.application.check_items import (
    CheckItem,
    ItemKind,
    check_items,
    countable_keys,
    counts_for_looked_at,
    first_unseen,
    group_minor,
    next_unseen,
)
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


def test_counts_for_looked_at_skips_own_changes_and_group_members() -> None:
    """
    Given  a correction, a spelling change, an unexplained change and the teacher's own change
    When   each item is asked whether it counts for looked-at
    Then   the correction and the unexplained change count, the other two do not
    """
    items = check_items(align(RAW, CORRECTED, _review("word_choice", "spelling")))
    unexplained = check_items(align("aa bb", "aa BB", CorrectionReview(), machine_plain="aa BB"))
    own = check_items(align("aa bb", "aa BB", CorrectionReview(), machine_plain="aa bb"))

    assert [counts_for_looked_at(item) for item in items] == [True, False]
    assert [counts_for_looked_at(item) for item in unexplained] == [True]
    assert [counts_for_looked_at(item) for item in own] == [False]


def test_the_countable_keys_are_the_rows_the_list_counts() -> None:
    """
    Given  a document with one spelling change, and one with two
    When   the keys the looked-at count counts are asked for
    Then   the single change is not folded and does not count, the two count once as the group
    """
    single = check_items(align(RAW, CORRECTED, _review("word_choice", "spelling")))
    folded = check_items(align(RAW, CORRECTED, _review("spelling", "spelling")))

    assert countable_keys(single) == ("edit:1",)
    assert countable_keys(folded) == ("minor",)


@pytest.mark.parametrize(
    ("rows", "looked_at", "after", "expected"),
    [
        (("a", "b", "c"), frozenset(), "a", "b"),
        (("a", "b", "c"), frozenset(), "c", "a"),
        (("a", "b", "c"), frozenset({"b"}), "a", "c"),
        (("a", "b", "c"), frozenset({"a", "b", "c"}), "a", ""),
        (("a", "b"), frozenset(), "own:1-4", "a"),
        ((), frozenset(), "a", ""),
    ],
)
def test_next_unseen_walks_the_rows_that_were_not_looked_at(
    rows: tuple[str, ...], looked_at: frozenset[str], after: str, expected: str
) -> None:
    """
    Given  the rows that count, the ones already looked at, and the chosen row
    When   the next row to walk to is asked for
    Then   it is the next one not looked at, from the top when the chosen one is the last, and
           nothing when every row was looked at
    """
    assert next_unseen(rows, looked_at, after) == expected


@pytest.mark.parametrize(
    ("rows", "looked_at", "expected"),
    [
        (("a", "b", "c"), frozenset(), "a"),
        (("a", "b", "c"), frozenset({"a"}), "b"),
        (("a", "b", "c"), frozenset({"a", "b", "c"}), "a"),
        ((), frozenset(), ""),
    ],
)
def test_first_unseen_is_the_first_row_not_looked_at(
    rows: tuple[str, ...], looked_at: frozenset[str], expected: str
) -> None:
    """
    Given  the rows that count and the ones already looked at
    When   the row a document opens on is asked for
    Then   it is the first one not looked at, the first row when all were, and nothing without rows
    """
    assert first_unseen(rows, looked_at) == expected


def test_a_stretch_the_model_left_out_is_its_own_kind_of_item() -> None:
    """
    Given  a review with a note for a stretch the model left out and one unclear expression
    When   the items are built
    Then   the first is a left-out item carrying its explanation and no suggestion, the second
           stays an unclear one, and both count for what the teacher has to look at
    """
    left_out = UnclearNote(
        raw_start=0,
        raw_end=27,
        expression="Der bunte Hund bellt laut.",
        why="Der Satz fehlte in der Antwort.",
        recommendation="",
        left_out=True,
    )

    items = check_items(align(RAW, RAW, CorrectionReview(unclear=(left_out, _unclear(28, 31)))))

    assert _kinds(items) == [ItemKind.LEFT_OUT, ItemKind.UNCLEAR]
    assert items[0].before == "Der bunte Hund bellt laut."
    assert items[0].why == "Der Satz fehlte in der Antwort."
    assert items[0].suggestion == ""
    assert all(counts_for_looked_at(item) for item in items)
