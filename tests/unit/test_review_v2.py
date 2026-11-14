"""Unit tests for the stored review and its alignment (``domain/proofreading/review.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import (
    REVIEW_VERSION,
    AlignedReview,
    CorrectionReview,
    EditNote,
    UnclearNote,
    align,
    edit_note,
    review_summary,
)

SOURCE = "Ich habe ein grosse Hund gesehen."
CORRECTED = "Ich habe einen großen Hund gesehen."


def _review() -> CorrectionReview:
    """Return a review of the two-word correction of :data:`SOURCE`."""
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(raw_start=9, raw_end=12, original="ein", corrected="einen"),
                    EditPart(raw_start=13, raw_end=19, original="grosse", corrected="großen"),
                ),
                sentences=(0,),
                category="grammar",
                explanation="The accusative needs the article and the ending.",
            ),
        )
    )


def test_a_review_survives_a_round_trip_through_its_record() -> None:
    """
    Given  a review with an edit and an unclear flag
    When   it is written to a record and read back
    Then   it is the same review
    """
    review = CorrectionReview(
        edits=_review().edits,
        unclear=(
            UnclearNote(
                raw_start=20,
                raw_end=26,
                expression="gesehen",
                why="Who saw it?",
                recommendation="Name the person.",
                is_suggestion=True,
            ),
        ),
    )

    assert CorrectionReview.from_dict(review.to_dict()) == review


def test_a_record_without_a_version_is_no_review() -> None:
    """
    Given  a stored record without a version field
    When   it is read as a review
    Then   there is no review
    """
    record = _review().to_dict()
    del record["version"]

    assert CorrectionReview.from_dict(record) is None


def test_a_record_of_another_version_is_no_review() -> None:
    """
    Given  a record of the previous review version
    When   it is read as a review
    Then   there is no review
    """
    record = _review().to_dict()
    record["version"] = REVIEW_VERSION - 1

    assert CorrectionReview.from_dict(record) is None


def test_a_record_that_is_not_a_mapping_is_no_review() -> None:
    """
    Given  a stored value that is not a mapping
    When   it is read as a review
    Then   there is no review
    """
    assert CorrectionReview.from_dict(["not", "a", "review"]) is None


def test_an_unchanged_edit_keeps_its_note() -> None:
    """
    Given  a review and the corrected text it was written for
    When   the review is aligned with the current diff
    Then   every hunk carries the note of its edit, unchanged
    """
    aligned = align(SOURCE, CORRECTED, _review())

    assert [change.edit_id for change in aligned.changes] == [1, 1]
    assert [change.changed_afterwards for change in aligned.changes] == [False, False]
    assert [change.by_teacher for change in aligned.changes] == [False, False]
    assert aligned.orphan_notes == ()


def test_an_edit_the_teacher_changed_keeps_its_note_marked() -> None:
    """
    Given  a review whose second part the teacher changed afterwards
    When   the review is aligned with the current diff
    Then   the note stays and the change is marked as changed afterwards
    """
    aligned = align(SOURCE, "Ich habe einen grossen Hund gesehen.", _review())

    assert [change.changed_afterwards for change in aligned.changes] == [False, True]
    assert aligned.changes[1].note is not None
    assert aligned.changes[1].explanation.startswith("The accusative")


def test_an_edit_the_teacher_undid_leaves_an_orphan_note() -> None:
    """
    Given  a review of a correction the teacher undid completely
    When   the review is aligned with the current diff
    Then   there is no change and the note is reported as an orphan
    """
    aligned = align(SOURCE, SOURCE, _review())

    assert aligned.changes == ()
    assert [note.id for note in aligned.orphan_notes] == [1]


def test_a_hunk_the_review_does_not_know_is_the_teachers_own_edit() -> None:
    """
    Given  a review without notes and a text the teacher changed
    When   the review is aligned with the current diff
    Then   the hunk is marked as the teacher's own edit
    """
    aligned = align("Ich gehe heim.", "Ich gehe Heim.", CorrectionReview())

    assert [change.by_teacher for change in aligned.changes] == [True]
    assert aligned.changes[0].note is None


def test_an_insertion_shifted_by_a_token_still_finds_its_note() -> None:
    """
    Given  a stored insertion one token away from where the current diff places it
    When   the review is aligned with the current diff
    Then   the note is attached and the edit counts as unchanged
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=9, raw_end=9, original="", corrected="sehr"),),
                sentences=(0,),
                category="word_choice",
                explanation="It was very much so.",
            ),
        )
    )

    aligned = align("Ich gehe  heim.", "Ich gehe sehr heim.", review)

    assert [change.edit_id for change in aligned.changes] == [1]
    assert aligned.changes[0].changed_afterwards is False


def test_map_text_maps_every_text_field() -> None:
    """
    Given  a review with an edit and an unclear flag
    When   a mapping is applied to its texts
    Then   every text field is mapped and the offsets stay as they were
    """
    review = CorrectionReview(
        edits=_review().edits,
        unclear=(
            UnclearNote(
                raw_start=20,
                raw_end=26,
                expression="gesehen",
                why="Who?",
                recommendation="Name them.",
            ),
        ),
    )

    mapped = review.map_text(str.upper)

    assert mapped.edits[0].parts[0].original == "EIN"
    assert mapped.edits[0].explanation.startswith("THE ACCUSATIVE")
    assert mapped.unclear[0].expression == "GESEHEN"
    assert mapped.edits[0].parts[0].raw_start == 9


def test_edit_note_takes_the_numbers_of_its_edit() -> None:
    """
    Given  an edit of the correction
    When   it is turned into a stored note
    Then   the note carries the edit's id, parts and sentences
    """
    from teachers_teammate.domain.proofreading.edits import edits  # noqa: PLC0415

    edit = edits(SOURCE, CORRECTED)[0]

    note = edit_note(edit, category="grammar", explanation="Case.")

    assert note.id == edit.id
    assert note.parts == edit.parts
    assert note.sentences == edit.sentences
    assert note.optional is False


def test_the_summary_counts_the_listed_hunks() -> None:
    """
    Given  an aligned review whose one edit is two hunks of the diff
    When   its summary is taken
    Then   it counts the hunks a teacher sees
    """
    aligned = align(SOURCE, CORRECTED, _review())

    assert review_summary(aligned) == "2 changes"


def test_the_summary_of_no_review_is_empty() -> None:
    """
    Given  no aligned review
    When   its summary is taken
    Then   it is empty
    """
    assert review_summary(None) == ""


def test_a_spelling_change_is_minor_and_not_listed() -> None:
    """
    Given  an aligned review whose only change is a spelling correction
    When   its listed changes are taken
    Then   the change is counted as minor and not listed
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=0, raw_end=4, original="Helo", corrected="Hello"),),
                sentences=(0,),
                category="spelling",
                explanation="",
            ),
        )
    )

    aligned = align("Helo world.", "Hello world.", review)

    assert aligned.minor_count == 1
    assert aligned.listed_count == 0
    assert isinstance(aligned, AlignedReview)
