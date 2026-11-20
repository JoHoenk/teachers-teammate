"""Unit tests for the stored review and its alignment (``domain/proofreading/review.py``)."""

from __future__ import annotations

from teachers_teammate.domain.anonymization import restore_with_offsets
from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.edits import edits as find_edits
from teachers_teammate.domain.proofreading.review import (
    REVIEW_VERSION,
    AlignedReview,
    CorrectionReview,
    EditNote,
    UnclearNote,
    align,
    edit_note,
    explained_sentences,
    fits,
    merge_reviews,
    note_sentence,
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


def _spelling_review(text: str, original: str, corrected: str) -> CorrectionReview:
    """Return a review of one spelling edit of *text*."""
    start = text.index(original)
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=start,
                        raw_end=start + len(original),
                        original=original,
                        corrected=corrected,
                    ),
                ),
                sentences=(0,),
                category="spelling",
                explanation="Tippfehler.",
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


def test_a_left_out_stretch_survives_the_record_shift_and_text_mapping() -> None:
    """
    Given  a review with a note for a stretch the model left out
    When   it is written and read back, shifted into a longer text and mapped through a text
           function
    Then   the flag is kept every time, and a record without the key reads as not left out
    """
    note = UnclearNote(
        raw_start=5,
        raw_end=15,
        expression="left over",
        why="The AI left this out.",
        recommendation="",
        left_out=True,
    )
    review = CorrectionReview(unclear=(note,))

    assert CorrectionReview.from_dict(review.to_dict()) == review
    assert review.shifted(100).unclear[0].left_out is True
    assert review.shifted(100).unclear[0].raw_start == 105
    assert review.map_text(str.upper).unclear[0].left_out is True
    record = review.to_dict()
    del record["unclear"][0]["left_out"]
    restored = CorrectionReview.from_dict(record)
    assert restored is not None
    assert restored.unclear[0].left_out is False


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


def test_a_hunk_of_the_machines_answer_is_not_the_teachers() -> None:
    """
    Given  the machine's own text and a review that does not explain one of its changes
    When   the review is aligned with the machine's text as the comparison
    Then   the unexplained hunk is the model's, not the teacher's
    """
    machine = "Ich gehe Heim."

    aligned = align("Ich gehe heim.", machine, CorrectionReview(), machine_plain=machine)

    assert [change.by_teacher for change in aligned.changes] == [False]


def test_a_hunk_beyond_the_machines_answer_is_the_teachers() -> None:
    """
    Given  a text the teacher changed beyond the machine's answer
    When   the review is aligned with the machine's text as the comparison
    Then   only the change the machine did not make is the teacher's
    """
    machine = "Ich gehe Heim."

    aligned = align(
        "Ich gehe heim.", "Ich gehe Heim. Und du?", CorrectionReview(), machine_plain=machine
    )

    assert [change.by_teacher for change in aligned.changes] == [False, True]


def test_without_the_machines_text_every_unexplained_hunk_is_the_teachers() -> None:
    """
    Given  a review without notes and no machine text to compare with
    When   the review is aligned with the current diff
    Then   the hunk counts as the teacher's, the rule the Word builder uses
    """
    aligned = align("Ich gehe heim.", "Ich gehe Heim.", CorrectionReview())

    assert [change.by_teacher for change in aligned.changes] == [True]


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
    edit = find_edits(SOURCE, CORRECTED)[0]

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


# ── Offsets: pieces, names, and what the Word file lists ─────────────────────


def test_a_review_shifted_into_a_whole_text_points_at_the_same_words() -> None:
    """
    Given  a review of a piece that starts ten characters into the whole text
    When   the review is shifted there
    Then   every part and every unclear span moved, and an insertion stayed zero-width
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(raw_start=0, raw_end=0, original="", corrected="sehr "),
                    EditPart(raw_start=4, raw_end=9, original="grose", corrected="große"),
                ),
                sentences=(0,),
                category="spelling",
                explanation="Tippfehler.",
            ),
        ),
        unclear=(
            UnclearNote(raw_start=2, raw_end=6, expression="Hund", why="?", recommendation="!"),
        ),
    )

    shifted = review.shifted(10)

    assert [(part.raw_start, part.raw_end) for part in shifted.edits[0].parts] == [
        (10, 10),
        (14, 19),
    ]
    assert (shifted.unclear[0].raw_start, shifted.unclear[0].raw_end) == (12, 16)


def test_a_review_remapped_through_a_restored_name_points_at_the_same_words() -> None:
    """
    Given  a review of "[PERSON_1] go home" and the text with the name put back
    When   the review is carried over through the offset map of the restore
    Then   every part points at the same words and the review fits the restored text
    """
    sent = "[PERSON_1] go home"
    restored, offsets = restore_with_offsets(sent, {"[PERSON_1]": "Anna Lena"})
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=14, raw_end=18, original="home", corrected="home."),),
                sentences=(0,),
                category="punctuation",
                explanation="Ein Satz endet mit einem Punkt.",
            ),
        ),
        unclear=(
            UnclearNote(
                raw_start=0, raw_end=10, expression="[PERSON_1]", why="?", recommendation="!"
            ),
        ),
    )

    remapped = review.remapped(offsets)

    assert restored == "Anna Lena go home"
    assert (
        restored[remapped.edits[0].parts[0].raw_start : remapped.edits[0].parts[0].raw_end]
        == "home"
    )
    assert restored[remapped.unclear[0].raw_start : remapped.unclear[0].raw_end] == "Anna Lena"
    assert fits(remapped, restored) is True


def test_a_part_inside_a_placeholder_does_not_fit_the_restored_text() -> None:
    """
    Given  a review whose part points into the middle of a placeholder
    When   it is carried over through the offset map of the restore
    Then   the review no longer fits the restored text
    """
    sent = "[PERSON_1] go home"
    restored, offsets = restore_with_offsets(sent, {"[PERSON_1]": "Anna Lena"})
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=3, raw_end=8, original="ERSON", corrected="erson"),),
                sentences=(0,),
                category="spelling",
                explanation="Tippfehler.",
            ),
        )
    )

    remapped = review.remapped(offsets)

    assert restored == "Anna Lena go home"
    assert fits(remapped, restored) is False


def test_merged_pieces_are_numbered_in_text_order_and_fit_the_whole_text() -> None:
    """
    Given  three pieces of one text, each with its own review
    When   the reviews are merged over the whole text
    Then   the ids run 1..k in text order, the sentences are the whole text's, and every part
           points at the words it quotes
    """
    whole = "Eins fehlerr. Zwei fehlerr. Drei fehlerr."
    pieces = [
        (0, _spelling_review(whole[0:13], "fehlerr", "Fehler")),
        (14, _spelling_review(whole[14:27], "fehlerr", "Fehler")),
        (28, _spelling_review(whole[28:], "fehlerr", "Fehler")),
    ]

    merged = merge_reviews(pieces, whole)

    assert [note.id for note in merged.edits] == [1, 2, 3]
    assert [note.sentences for note in merged.edits] == [(0,), (1,), (2,)]
    assert fits(merged, whole) is True
    assert [whole[note.raw_start : note.raw_end] for note in merged.edits] == ["fehlerr"] * 3


def test_merged_pieces_agree_with_a_review_made_on_the_whole_text() -> None:
    """
    Given  a text whose three sentences each carry one edit
    When   the reviews of its three pieces are merged
    Then   the sentences of the merged edits are the ones a review of the whole text names
    """
    whole = "Eins fehlerr. Zwei fehlerr. Drei fehlerr."
    pieces = [
        (0, _spelling_review(whole[0:13], "fehlerr", "Fehler")),
        (14, _spelling_review(whole[14:27], "fehlerr", "Fehler")),
        (28, _spelling_review(whole[28:], "fehlerr", "Fehler")),
    ]

    merged = merge_reviews(pieces, whole)

    assert [note.sentences for note in merged.edits] == [
        (0,),
        (1,),
        (2,),
    ]


def test_a_review_that_does_not_fit_is_reported() -> None:
    """
    Given  a review whose part quotes words that are not at its offsets
    When   it is checked against the text
    Then   it does not fit
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=0, raw_end=4, original="Eins", corrected="Eins,"),),
                sentences=(0,),
                category="punctuation",
                explanation="Ein Komma fehlt.",
            ),
        )
    )

    assert fits(review, "Eins zwei") is True
    assert fits(review, "Zwei eins") is False


def test_an_insertion_fits_anywhere_inside_the_text() -> None:
    """
    Given  a review whose only part is an insertion
    When   it is checked against a text of the right length
    Then   it fits, because an insertion has no words to compare
    """
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=4, raw_end=4, original="", corrected=" sehr"),),
                sentences=(0,),
                category="word_choice",
                explanation="Das Wort fehlt.",
            ),
        )
    )

    assert fits(review, "Eins zwei") is True
    assert fits(review, "Eins zwei drei") is True
    assert fits(review, "Ein") is False


def test_an_explanation_of_a_change_in_the_text_is_listed_once_per_sentence() -> None:
    """
    Given  a review whose one edit is two hunks of the same sentence
    When   the explained sentences are collected
    Then   the sentence is listed once, with the edit's note, as the corrected text reads it
    """
    raw = "weil ich habe auf dich gewartet."
    corrected = "weil ich auf dich gewartet habe."
    found = find_edits(raw, corrected)
    assert len(found) == 1 and len(found[0].parts) == 2
    review = CorrectionReview(
        edits=(edit_note(found[0], category="word_order", explanation="Das Verb steht am Ende."),)
    )

    listed = explained_sentences(align(raw, corrected, review), raw, corrected)

    assert [entry.text for entry in listed] == [corrected]
    assert [note.id for note in listed[0].notes] == [1]


def test_minor_and_changed_edits_are_not_listed() -> None:
    """
    Given  a review with a spelling edit, a grammar edit and a grammar edit the teacher changed
    When   the explained sentences are collected
    Then   only the grammar edit that is still in the text is listed
    """
    raw = "Er hat ein Hund. Sie hat ein Katz."
    corrected = "Er hat einen Hund. Sie hat ein Katze."
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=7, raw_end=10, original="ein", corrected="einen"),),
                sentences=(0,),
                category="grammar",
                explanation="Der Akkusativ.",
            ),
            EditNote(
                id=2,
                parts=(EditPart(raw_start=24, raw_end=28, original="Katz", corrected="Katze"),),
                sentences=(1,),
                category="spelling",
                explanation="Tippfehler.",
            ),
        )
    )

    listed = explained_sentences(align(raw, corrected, review), raw, corrected)

    assert [entry.text for entry in listed] == ["Er hat einen Hund."]
    assert [note.id for note in listed[0].notes] == [1]


def test_a_note_on_an_edit_the_teacher_changed_is_not_listed() -> None:
    """
    Given  a review whose edit the teacher changed afterwards
    When   the explained sentences are collected
    Then   nothing is listed, because the note explains words that are no longer there
    """
    raw = "Ich habe ein grosse Hund gesehen."
    corrected = "Ich habe einen grossen Hund gesehen."
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=13, raw_end=19, original="grosse", corrected="großen"),),
                sentences=(0,),
                category="grammar",
                explanation="Das Adjektiv braucht die richtige Endung.",
            ),
        )
    )

    listed = explained_sentences(align(raw, corrected, review), raw, corrected)

    assert listed == ()


def test_the_listed_sentence_follows_the_teachers_edit_of_the_corrected_text() -> None:
    """
    Given  a review of a correction the teacher then edited further
    When   the explained sentences are collected
    Then   the sentence is quoted as the corrected text reads it now
    """
    raw = "Er hat ein Hund und ist nett."
    corrected = "Er hat einen Hund und ist sehr nett."
    review = CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(EditPart(raw_start=7, raw_end=10, original="ein", corrected="einen"),),
                sentences=(0,),
                category="grammar",
                explanation="Der Akkusativ.",
            ),
        )
    )

    listed = explained_sentences(align(raw, corrected, review), raw, corrected)

    assert [entry.text for entry in listed] == ["Er hat einen Hund und ist sehr nett."]


def test_a_remapped_review_forgets_the_sentence_numbers_of_the_other_text() -> None:
    """
    Given  a review whose edits carry sentence numbers counted on the anonymized text
    When   it is carried over through the offsets of the restore
    Then   the edits no longer claim a sentence, so the restored text's own split decides
    """
    sent = "[PERSON_1] wohnt in der Str. 5. Er gehen heim."
    _restored, offsets = restore_with_offsets(sent, {"[PERSON_1]": "Anna"})
    review = _spelling_review(sent, "gehen", "geht")

    assert review.edits[0].sentences == (0,)
    assert review.remapped(offsets).edits[0].sentences == ()


def test_an_explained_sentence_survives_a_sentence_number_the_text_does_not_have() -> None:
    """
    Given  a review whose note names sentence 5 of a text that has two sentences
    When   the explained sentences are listed
    Then   the note is placed by its offset instead of raising
    """
    raw = "Das ist gut. Er gehen heim."
    corrected = "Das ist gut. Er geht heim."
    start = raw.index("gehen")
    note = EditNote(
        id=1,
        parts=(EditPart(raw_start=start, raw_end=start + 5, original="gehen", corrected="geht"),),
        sentences=(5,),
        category="grammar",
        explanation="Der Verbform passt nicht zum Subjekt.",
    )
    aligned = align(raw, corrected, CorrectionReview(edits=(note,)))

    sentences = explained_sentences(aligned, raw, corrected)

    assert [sentence.text for sentence in sentences] == ["Er geht heim."]
    assert note_sentence(aligned, raw, corrected, note) == "Er geht heim."


def test_the_sentence_of_a_spelling_note_is_still_found() -> None:
    """
    Given  a review whose only note is a spelling edit
    When   the sentence of that note is asked for
    Then   it is the corrected sentence, although the explained sentences leave spelling out
    """
    raw = "Er geht hiem. Sie lacht."
    corrected = "Er geht heim. Sie lacht."
    review = _spelling_review(raw, "hiem", "heim")
    aligned = align(raw, corrected, review)

    assert explained_sentences(aligned, raw, corrected) == ()
    assert note_sentence(aligned, raw, corrected, review.edits[0]) == "Er geht heim."


def test_a_stretch_left_out_is_counted_apart_from_the_unclear_expressions() -> None:
    """
    Given  a review with one unclear expression and one stretch the model left out
    When   the review and its aligned form are counted
    Then   the unclear count is one and the left-out count is one, so the queue's badge and the
           exports do not call the stretch unclear
    """
    unclear = UnclearNote(0, 3, "abc", "Vague.", "Say more.")
    left_out = UnclearNote(4, 9, "defgh", "Left out.", "", left_out=True)
    review = CorrectionReview(unclear=(unclear, left_out))

    assert (review.unclear_count, review.left_out_count) == (1, 1)
    assert review.unclear_notes == (unclear,)
    assert align("abc defgh", "abc defgh", review).unclear_count == 1
