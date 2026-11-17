"""Unit tests for what a teacher does to a corrected text (``domain/teacher_work.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.edits import EditPart, edits
from teachers_teammate.domain.proofreading.review import (
    AlignedChange,
    AlignedReview,
    AlignedUnclear,
    CorrectionReview,
    UnclearNote,
    align,
    edit_note,
)
from teachers_teammate.domain.proofreading.word_diff import Hunk, word_diff
from teachers_teammate.domain.teacher_work import (
    TextEdit,
    apply_edits,
    put_back,
    suggestion_is_wording,
    take_out,
    use_suggestion,
)

SOURCE = "Ich habe ein grosse Hund gesehen. Er lauft schnell."
CORRECTED = "Ich habe einen großen Hund gesehen. Er läuft schnell."


def _review_of(raw: str, corrected: str) -> CorrectionReview:
    """Return the review the model would have written for *corrected* against *raw*."""
    return CorrectionReview(
        edits=tuple(edit_note(edit, category="grammar") for edit in edits(raw, corrected))
    )


def _changes(raw: str, corrected: str) -> tuple[AlignedChange, ...]:
    """Return the current hunks of *raw* against *corrected*, as the view sees them."""
    return tuple(
        AlignedChange(number=number, hunk=hunk, note=None)
        for number, hunk in enumerate(word_diff(raw, corrected), 1)
    )


def _group(aligned: AlignedReview, edit_id: int) -> tuple[AlignedChange, ...]:
    """Return the changes of *aligned* that belong to the stored edit *edit_id*."""
    return tuple(change for change in aligned.changes if change.edit_id == edit_id)


def test_take_out_puts_the_students_words_back_for_a_replacement() -> None:
    """
    Given  a corrected text whose article and adjective were replaced
    When   the edit is taken out
    Then   the student's words stand there again, and nothing else changed
    """
    aligned = align(SOURCE, CORRECTED, _review_of(SOURCE, CORRECTED))

    edits_of_edit = take_out(_group(aligned, 1), SOURCE, CORRECTED)

    assert edits_of_edit == (TextEdit(9, 14, "ein"), TextEdit(15, 21, "grosse"))
    assert apply_edits(CORRECTED, edits_of_edit) == (
        "Ich habe ein grosse Hund gesehen. Er läuft schnell."
    )


def test_take_out_puts_a_deleted_word_back_with_one_space() -> None:
    """
    Given  a corrected text from which the model removed a word
    When   the edit is taken out
    Then   the word is back between its neighbours, separated by one space
    """
    raw, corrected = "Der bunte Hund", "Der Hund"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(4, 4, "bunte "),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_removes_an_inserted_word_with_one_space() -> None:
    """
    Given  a corrected text into which the model inserted a word
    When   the edit is taken out
    Then   the word and one of the spaces around it are gone
    """
    raw, corrected = "Der Hund", "Der bunte Hund"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(4, 10, ""),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_at_the_start_of_the_text() -> None:
    """
    Given  a corrected text whose first word the model removed
    When   the edit is taken out
    Then   the word comes back at the start, followed by one space
    """
    raw, corrected = "Ein Hund", "Hund"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(0, 0, "Ein "),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_at_the_end_of_the_text() -> None:
    """
    Given  a corrected text whose last word the model removed
    When   the edit is taken out
    Then   the word comes back at the end, preceded by one space
    """
    raw, corrected = "Der Hund bellt", "Der Hund"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(8, 8, " bellt"),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_next_to_punctuation_takes_the_space_before() -> None:
    """
    Given  a corrected text where the model added a word before a full stop
    When   the edit is taken out
    Then   the word and the space before it are gone, and the full stop stays put
    """
    raw, corrected = "Der Hund bellt.", "Der Hund bellt laut."

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(14, 19, ""),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_of_a_punctuation_mark_takes_no_space() -> None:
    """
    Given  a corrected text where the model added an exclamation mark
    When   the edit is taken out
    Then   only the mark is removed
    """
    raw, corrected = "Der Hund bellt", "Der Hund bellt!"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(14, 15, ""),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_never_takes_a_line_break() -> None:
    """
    Given  a corrected text whose inserted word stands before a line break
    When   the edit is taken out
    Then   the space before the word goes with it and the line break stays
    """
    raw, corrected = "Der Hund\n\nbellt", "Der Hund laut\n\nbellt"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(8, 13, ""),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_adds_no_space_next_to_a_line_break() -> None:
    """
    Given  a corrected text whose removed word stood at the start of a second block
    When   the edit is taken out
    Then   the word comes back after the line break, with no space in front of it
    """
    raw, corrected = "Der Hund\n\nlaut bellt", "Der Hund\n\nbellt"

    edits_of_edit = take_out(_changes(raw, corrected), raw, corrected)

    assert edits_of_edit == (TextEdit(10, 10, "laut "),)
    assert apply_edits(corrected, edits_of_edit) == raw


def test_take_out_leaves_the_other_edits_of_the_text_alone() -> None:
    """
    Given  a review of two edits, one of them over two hunks
    When   the first edit is taken out and the text aligned again
    Then   the second edit is still a change with its note, and the first is an orphan
    """
    review = _review_of(SOURCE, CORRECTED)
    aligned = align(SOURCE, CORRECTED, review)
    taken_out = apply_edits(CORRECTED, take_out(_group(aligned, 1), SOURCE, CORRECTED))

    again = align(SOURCE, taken_out, review)

    assert [change.hunk.original for change in again.changes] == ["lauft"]
    assert [note.id for note in again.orphan_notes] == [1]


def test_put_back_restores_the_corrected_text() -> None:
    """
    Given  a corrected text from which an edit was taken out
    When   the edit is put back
    Then   the corrected text is there again, character for character
    """
    review = _review_of(SOURCE, CORRECTED)
    aligned = align(SOURCE, CORRECTED, review)
    taken_out = apply_edits(CORRECTED, take_out(_group(aligned, 1), SOURCE, CORRECTED))
    again = align(SOURCE, taken_out, review)

    edits_of_edit = put_back(review.edits[0], again, SOURCE, taken_out)

    assert apply_edits(taken_out, edits_of_edit) == CORRECTED


def test_put_back_round_trips_over_every_edit_of_the_review() -> None:
    """
    Given  a review of two edits
    When   each edit is taken out and put back in turn
    Then   the corrected text comes back every time
    """
    review = _review_of(SOURCE, CORRECTED)
    for note in review.edits:
        aligned = align(SOURCE, CORRECTED, review)
        taken_out = apply_edits(CORRECTED, take_out(_group(aligned, note.id), SOURCE, CORRECTED))
        again = align(SOURCE, taken_out, review)

        edits_of_edit = put_back(note, again, SOURCE, taken_out)

        assert apply_edits(taken_out, edits_of_edit) == CORRECTED


def test_put_back_refuses_a_part_inside_the_teachers_own_change() -> None:
    """
    Given  a text whose words the teacher replaced themselves
    When   the model's edit of those words is put back
    Then   nothing is put back: the teacher's words stand there now
    """
    hunk = Hunk(
        raw_start=4,
        raw_end=9,
        corrected_start=4,
        corrected_end=12,
        original="bunte",
        corrected="schwarze",
        kind="replace",
    )
    aligned = AlignedReview(
        changes=(AlignedChange(number=1, hunk=hunk, note=None, position=4, by_teacher=True),)
    )
    note = edit_note(edits("Der bunte Hund", "Der braune Hund")[0], category="word_choice")

    assert put_back(note, aligned, "Der bunte Hund", "Der schwarze Hund") == ()


def test_put_back_refuses_a_note_whose_words_are_no_longer_there() -> None:
    """
    Given  a note that quotes words the student's text does not have
    When   it is put back
    Then   nothing is put back, so no words land in the wrong place
    """
    note = edit_note(edits("Der bunte Hund", "Der braune Hund")[0], category="word_choice")

    assert put_back(note, AlignedReview(), "Der schwarze Hund", "Der schwarze Hund") == ()


def test_use_suggestion_replaces_an_expression_in_an_equal_region() -> None:
    """
    Given  an unclear expression the model did not change
    When   its suggestion is used
    Then   the suggested wording stands where the expression stood
    """
    raw, corrected = "Der Hund bellt laut.", "Der Hund bellt laut!"
    item = AlignedUnclear(
        number=1,
        note=UnclearNote(
            raw_start=4,
            raw_end=8,
            expression="Hund",
            why="Welcher Hund?",
            recommendation="Köter",
        ),
        start=4,
        end=8,
    )

    edits_of_edit = use_suggestion(item, align(raw, corrected, CorrectionReview()), raw, corrected)

    assert edits_of_edit == (TextEdit(4, 8, "Köter"),)
    assert apply_edits(corrected, edits_of_edit) == "Der Köter bellt laut!"


def test_use_suggestion_replaces_an_expression_that_reaches_into_a_hunk() -> None:
    """
    Given  an unclear expression whose last word the model also corrected
    When   its suggestion is used
    Then   the suggestion replaces the corrected wording, not the student's
    """
    raw, corrected = "Der bunte Hund bellt.", "Der braune Hund bellt."
    aligned = align(raw, corrected, _review_of(raw, corrected))
    item = AlignedUnclear(
        number=1,
        note=UnclearNote(
            raw_start=0,
            raw_end=9,
            expression="Der bunte",
            why="Welcher Hund?",
            recommendation="Ein schwarzer",
        ),
        start=0,
        end=9,
    )

    edits_of_edit = use_suggestion(item, aligned, raw, corrected)

    assert edits_of_edit == (TextEdit(0, 10, "Ein schwarzer"),)
    assert apply_edits(corrected, edits_of_edit) == "Ein schwarzer Hund bellt."


def test_use_suggestion_of_an_unanchored_expression_does_nothing() -> None:
    """
    Given  an unclear expression that was not found in the text
    When   its suggestion is used
    Then   nothing is edited: there is no place to put it
    """
    item = AlignedUnclear(
        number=1,
        note=UnclearNote(
            raw_start=0, raw_end=0, expression="Hund", why="Welcher?", recommendation="Köter"
        ),
    )

    assert use_suggestion(item, AlignedReview(), "Der Hund bellt.", "Der Hund bellt.") == ()


def test_suggestion_is_wording_only_for_a_wording() -> None:
    """
    Given  three recommendations: a wording, a question and nothing
    When   each is asked whether it is a wording
    Then   only the first one is
    """

    def note(recommendation: str) -> UnclearNote:
        return UnclearNote(
            raw_start=0,
            raw_end=4,
            expression="Hund",
            why="Welcher?",
            recommendation=recommendation,
        )

    assert suggestion_is_wording(note("Der Köter bellt."))
    assert not suggestion_is_wording(note("Welcher Hund ist gemeint?"))
    assert not suggestion_is_wording(note("   "))


def test_apply_edits_applies_them_last_to_first() -> None:
    """
    Given  two edits of one text, given in text order
    When   they are applied
    Then   both are in the result, and the offsets of the first stayed valid
    """
    text = "Der bunte Hund bellt laut."

    result = apply_edits(text, (TextEdit(4, 9, "braune"), TextEdit(21, 25, "leise")))

    assert result == "Der braune Hund bellt leise."
