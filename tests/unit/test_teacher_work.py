"""Unit tests for what a teacher does to a corrected text (``domain/teacher_work.py``)."""

from __future__ import annotations

import pytest

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
from teachers_teammate.domain.proofreading.text_edits import TextEdit, apply_edits
from teachers_teammate.domain.proofreading.word_diff import Hunk, word_diff
from teachers_teammate.domain.teacher_work import (
    WORK_VERSION,
    CorrectionsWork,
    MachineTexts,
    TeacherWork,
    TranscriptionWork,
    WorkTooNewError,
    current_texts,
    kept_over,
    looked_at_progress,
    proofreading_id,
    put_back,
    replace_words,
    suggestion_is_wording,
    take_out,
    text_identity,
    use_suggestion,
    with_checked,
    with_corrected,
    with_looked_at,
    with_transcription,
    without_corrections,
    without_work_for_reading,
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


def test_replace_words_writes_the_typed_words_over_the_corrected_ones() -> None:
    """
    Given  a correction whose words stand in the corrected text
    When   other words are written in their place
    Then   they replace the corrected wording, and the rest of the text is untouched
    """
    raw, corrected = "Der bunte Hund bellt.", "Der braune Hund bellt."
    aligned = align(raw, corrected, _review_of(raw, corrected))

    edits_of_edit = replace_words(((4, 10),), (4, 9), aligned, corrected, "dunkelbraune")

    assert edits_of_edit == (TextEdit(4, 10, "dunkelbraune"),)
    assert apply_edits(corrected, edits_of_edit) == "Der dunkelbraune Hund bellt."


def test_replace_words_replaces_an_inserted_word_instead_of_adding_to_it() -> None:
    """
    Given  a correction that only added a word, so the student's span is empty
    When   other words are written in place of the added word
    Then   the added word is replaced, not left standing next to the new words
    """
    raw, corrected = "Ich gehe Schule.", "Ich gehe zur Schule."
    aligned = align(raw, corrected, _review_of(raw, corrected))
    hunk = aligned.changes[0].hunk

    edits_of_edit = replace_words(
        ((hunk.corrected_start, hunk.corrected_end),),
        (hunk.raw_start, hunk.raw_end),
        aligned,
        corrected,
        "in die",
    )

    assert apply_edits(corrected, edits_of_edit) == "Ich gehe in die Schule."


def test_replace_words_gives_nothing_for_words_outside_the_text() -> None:
    """
    Given  a span that lies beyond the corrected text
    When   other words are written in its place
    Then   there is no edit
    """
    raw = "Der Hund bellt."
    aligned = align(raw, raw, CorrectionReview())

    assert replace_words(((50, 60),), (50, 60), aligned, raw, "x") == ()


def test_replace_words_finds_the_students_words_where_the_model_left_them() -> None:
    """
    Given  a stretch the model left out, so the student's words still stand in the text
    When   other words are written in their place
    Then   they land on those words, not on the model's wording
    """
    raw = "Der bunte Hund bellt."
    aligned = align(raw, raw, CorrectionReview())

    edits_of_edit = replace_words((), (4, 9), aligned, raw, "braune")

    assert apply_edits(raw, edits_of_edit) == "Der braune Hund bellt."


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


# ── The teacher's work ────────────────────────────────────────────────────────

WORK_RAW = "Ich habe ein grosse Hund gesehen."
WORK_CORRECTED = "Ich habe einen großen Hund gesehen."
WORK_REVIEW = {"edits": [{"id": 1, "parts": []}]}


def _machine(
    *, corrected: str = WORK_CORRECTED, review: dict | None = None, source: str = ""
) -> MachineTexts:
    """Return the machine texts of the sample essay, with *corrected* as its proofreading."""
    return MachineTexts(
        raw_text=WORK_RAW,
        raw_text_format="plain",
        corrected_text=corrected,
        review=WORK_REVIEW if review is None else review,
        correction_source_hash=source,
    )


def _work(**kwargs) -> TeacherWork:
    """Return the work of the sample essay, with *kwargs* as its parts."""
    return TeacherWork(source_hash="hash", name="essay.png", **kwargs)


def test_current_texts_without_work_are_the_machines() -> None:
    """A document nobody worked on shows what the machine produced."""
    texts = current_texts(_machine(), None)

    assert texts.transcription == WORK_RAW
    assert texts.corrected == WORK_CORRECTED
    assert texts.machine_corrected == WORK_CORRECTED
    assert texts.proofreading_id
    assert texts.transcription_work is None
    assert texts.corrections_work is None


def test_current_texts_take_the_teachers_transcription() -> None:
    """The teacher's transcription wins over what the recognition read."""
    work = _work(
        transcription=TranscriptionWork(
            text="Ich habe einen großen Hund gesehen.", text_format="plain"
        )
    )

    texts = current_texts(_machine(), work)

    assert texts.transcription == "Ich habe einen großen Hund gesehen."
    assert texts.transcription_work is work.transcription


def test_current_texts_without_a_proofreading_have_no_id() -> None:
    """Without a proofreading there is no answer to bind anything to."""
    texts = current_texts(_machine(corrected="", review=None), None)

    assert texts.proofreading_id == ""
    assert texts.corrected == ""
    assert texts.machine_corrected == ""


def test_current_texts_apply_a_part_bound_to_the_stored_answer() -> None:
    """A corrections part written on this answer is the text the document has."""
    machine = _machine()
    answer = current_texts(machine, None).proofreading_id
    work = _work(
        corrections=CorrectionsWork(bound_to=answer, text="Ich habe einen großen Hund gesehen!")
    )

    texts = current_texts(machine, work)

    assert texts.corrected == "Ich habe einen großen Hund gesehen!"
    assert texts.machine_corrected == WORK_CORRECTED
    assert texts.corrections_work is work.corrections


def test_current_texts_ignore_a_part_bound_to_another_answer() -> None:
    """A part written on another answer does not apply: the machine's text stands."""
    work = _work(corrections=CorrectionsWork(bound_to="another answer", text="Etwas anderes"))

    texts = current_texts(_machine(), work)

    assert texts.corrected == WORK_CORRECTED
    assert texts.corrections_work is None


def test_current_texts_ignore_a_proofreading_made_from_another_transcription() -> None:
    """A proofreading made from a transcription that is gone is not current."""
    edited = "Ich habe einen großen Hund gesehen."
    machine = _machine(source=text_identity(edited))

    with_work = current_texts(
        machine, _work(transcription=TranscriptionWork(text=edited, text_format="plain"))
    )
    without_work = current_texts(machine, None)

    assert with_work.proofreading_id
    assert without_work.proofreading_id == ""
    # The text of a proofreading that is not current is not shown either: it would be diffed
    # against a transcription it was not made from.
    assert without_work.corrected == ""
    assert without_work.machine_corrected == ""


def test_current_texts_keep_the_machine_text_apart_from_the_teachers() -> None:
    """``machine_corrected`` is always the machine's own text, whatever the teacher wrote."""
    machine = _machine()
    answer = current_texts(machine, None).proofreading_id
    work = _work(corrections=CorrectionsWork(bound_to=answer, text="Anders"))

    texts = current_texts(machine, work)

    assert texts.machine_corrected == WORK_CORRECTED
    assert texts.corrected == "Anders"


def test_proofreading_id_changes_with_the_review() -> None:
    """Two answers with the same text but another review are two answers."""
    first = proofreading_id("source", WORK_CORRECTED, WORK_REVIEW)
    second = proofreading_id("source", WORK_CORRECTED, {"edits": []})

    assert first != second


def test_proofreading_id_ignores_the_key_order_of_the_review() -> None:
    """The review is hashed with sorted keys, so its order does not matter."""
    first = proofreading_id("source", WORK_CORRECTED, {"edits": [], "unclear": []})
    second = proofreading_id("source", WORK_CORRECTED, {"unclear": [], "edits": []})

    assert first == second


def test_proofreading_id_is_stable_for_the_same_answer() -> None:
    """The same source, text and review are the same answer."""
    assert proofreading_id("source", WORK_CORRECTED, WORK_REVIEW) == proofreading_id(
        "source", WORK_CORRECTED, WORK_REVIEW
    )


def test_with_transcription_drops_a_copy_of_the_recognition() -> None:
    """Typing the recognition's own text back is no work at all."""
    work = with_transcription(_work(), WORK_RAW, "plain", _machine())

    assert work.transcription is None
    assert work.empty


def test_with_transcription_keeps_a_checked_copy_of_the_recognition() -> None:
    """A checked transcription stays stored even when it is the recognition's text."""
    checked = _work(
        transcription=TranscriptionWork(text=WORK_RAW, text_format="plain", checked=True)
    )

    work = with_transcription(checked, WORK_RAW, "plain", _machine())

    assert work.transcription is not None
    assert work.transcription.checked


def test_with_transcription_drops_the_corrections_part_of_a_proofreading() -> None:
    """A new transcription means a new proofreading: the corrections written on the old one go."""
    work = _work(corrections=CorrectionsWork(bound_to="answer", text="Anders"))

    changed = with_transcription(work, "Ein anderer Text.", "plain", _machine())

    assert changed.transcription is not None
    assert changed.corrections is None


def test_with_transcription_keeps_the_corrections_part_without_a_proofreading() -> None:
    """Without a proofreading there is no answer to lose, so the part stays."""
    work = _work(corrections=CorrectionsWork(bound_to="answer", text="Anders"))

    changed = with_transcription(work, "Ein anderer Text.", "plain", _machine(corrected=""))

    assert changed.corrections is not None


def test_with_corrected_binds_to_the_current_answer() -> None:
    """An edit of the corrected text is stored against the answer it was made on."""
    machine = _machine()
    current = current_texts(machine, None)

    work = with_corrected(_work(), "Ein neuer Text.", current)

    assert work.corrections is not None
    assert work.corrections.bound_to == current.proofreading_id
    assert work.corrections.text == "Ein neuer Text."


def test_with_corrected_drops_the_copy_when_it_is_the_machines_text_again() -> None:
    """An edit that returns to the machine's text keeps the part but drops the copy."""
    machine = _machine()
    current = current_texts(machine, None)
    edited = with_corrected(_work(), "Ein neuer Text.", current)

    back = with_corrected(edited, WORK_CORRECTED, current)

    assert back.corrections is not None
    assert back.corrections.text is None


def test_with_corrected_replaces_a_part_bound_to_another_answer() -> None:
    """A part of another answer is replaced, and its items looked at with it."""
    machine = _machine()
    current = current_texts(machine, None)
    stale = _work(
        corrections=CorrectionsWork(
            bound_to="another answer", text="Alt", checked=True, looked_at=frozenset({"edit:1"})
        )
    )

    work = with_corrected(stale, "Ein neuer Text.", current)

    assert work.corrections is not None
    assert work.corrections.bound_to == current.proofreading_id
    assert work.corrections.looked_at == frozenset()
    assert not work.corrections.checked


def test_with_corrected_without_a_proofreading_changes_nothing() -> None:
    """There is no answer to bind an edit to, so nothing is stored."""
    current = current_texts(_machine(corrected="", review=None), None)

    work = with_corrected(_work(), "Ein neuer Text.", current)

    assert work.corrections is None


def test_with_checked_marks_the_corrections_of_a_proofreading() -> None:
    """With a proofreading, *Checked* marks the corrections."""
    machine = _machine()
    current = current_texts(machine, None)

    work = with_checked(_work(), True, current)

    assert work.corrections is not None
    assert work.corrections.checked
    assert work.transcription is None


def test_with_checked_stores_a_copy_of_the_transcription() -> None:
    """Without a proofreading, *Checked* stores the transcription it was given."""
    current = current_texts(_machine(corrected="", review=None), None)

    work = with_checked(_work(), True, current)

    assert work.transcription is not None
    assert work.transcription.text == WORK_RAW
    assert work.transcription.checked


def test_with_checked_drops_the_copy_it_created_when_unchecked() -> None:
    """Unchecking a copy of the recognition's own text leaves no work behind."""
    current = current_texts(_machine(corrected="", review=None), None)
    checked = with_checked(_work(), True, current)

    work = with_checked(checked, False, current)

    assert work.transcription is None
    assert work.empty


def test_with_checked_keeps_a_fixed_transcription_when_unchecked() -> None:
    """A transcription the teacher fixed stays stored when it is unchecked."""
    fixed = _work(transcription=TranscriptionWork(text="Ein anderer Text.", text_format="plain"))
    current = current_texts(_machine(corrected="", review=None), fixed)

    work = with_checked(fixed, True, current)
    unchecked = with_checked(work, False, current)

    assert unchecked.transcription is not None
    assert not unchecked.transcription.checked


def test_with_looked_at_adds_keys_to_the_current_answer() -> None:
    """Looking at an item is stored in the part of the answer it belongs to."""
    current = current_texts(_machine(), None)

    work = with_looked_at(_work(), ["edit:1", "unclear:2"], current)
    more = with_looked_at(work, ["minor"], current)

    assert more.corrections is not None
    assert more.corrections.looked_at == frozenset({"edit:1", "unclear:2", "minor"})


def test_with_looked_at_without_a_proofreading_changes_nothing() -> None:
    """There is no answer whose items could be looked at."""
    current = current_texts(_machine(corrected="", review=None), None)

    work = with_looked_at(_work(), ["edit:1"], current)

    assert work.corrections is None


def test_kept_over_records_the_settings_it_was_kept_for() -> None:
    """*Keep my changes* remembers the settings, so they never ask again."""
    machine = _machine()
    answer = current_texts(machine, None).proofreading_id
    work = _work(corrections=CorrectionsWork(bound_to=answer, text="Anders"))

    kept = kept_over(work, "correction-hash", current_texts(machine, work))

    assert kept.corrections is not None
    assert kept.corrections.kept_under == "correction-hash"


def test_kept_over_without_a_corrections_part_changes_nothing() -> None:
    """There is no answer to keep."""
    work = _work()

    kept = kept_over(work, "correction-hash", current_texts(_machine(), work))

    assert kept.corrections is None


def test_kept_over_leaves_a_part_of_another_answer_alone() -> None:
    """
    Given  corrections written on an answer that is not the stored one
    When   the teacher keeps their changes over new settings
    Then   that part is not stamped: it is not what they kept
    """
    machine = _machine()
    work = _work(corrections=CorrectionsWork(bound_to="another-answer", text="Anders"))

    kept = kept_over(work, "correction-hash", current_texts(machine, work))

    assert kept.corrections is not None
    assert kept.corrections.kept_under == ""


def test_without_corrections_keeps_the_transcription() -> None:
    """Dropping the corrections part leaves the transcription alone."""
    work = _work(
        transcription=TranscriptionWork(text="Ein anderer Text.", text_format="plain"),
        corrections=CorrectionsWork(bound_to="answer", text="Anders"),
    )

    dropped = without_corrections(work)

    assert dropped.corrections is None
    assert dropped.transcription is not None


def test_without_work_for_reading_drops_both_parts() -> None:
    """Reading the pages again makes both texts new."""
    work = _work(
        transcription=TranscriptionWork(text="Ein anderer Text.", text_format="plain"),
        corrections=CorrectionsWork(bound_to="answer", text="Anders"),
    )

    dropped = without_work_for_reading(work)

    assert dropped.empty


def test_looked_at_progress_counts_the_keys_the_list_has() -> None:
    """Only the items the list has now count, so a gone item is no work left over."""
    assert looked_at_progress(["edit:1", "edit:2", "minor"], {"edit:1", "taken:9"}) == (1, 3)
    assert looked_at_progress([], {"edit:1"}) == (0, 0)


def test_work_round_trips_through_its_stored_shape() -> None:
    """What is written is what is read back."""
    work = _work(
        transcription=TranscriptionWork(
            text="Ein anderer Text.", text_format="markdown", checked=True
        ),
        corrections=CorrectionsWork(
            bound_to="answer",
            text="Anders",
            checked=True,
            looked_at=frozenset({"edit:1"}),
            kept=frozenset({"edit:2"}),
            kept_under="correction-hash",
        ),
    )

    stored = work.to_dict()

    assert stored["version"] == WORK_VERSION
    assert TeacherWork.from_dict(stored) == work


def test_work_of_a_newer_version_is_refused() -> None:
    """A file of a newer version is never read: the app would lose what it does not know."""
    stored = _work().to_dict()
    stored["version"] = WORK_VERSION + 1

    with pytest.raises(WorkTooNewError):
        TeacherWork.from_dict(stored)


def test_a_file_that_is_not_work_is_refused() -> None:
    """A file without a version or a source hash is not a work file."""
    with pytest.raises(ValueError, match="version"):
        TeacherWork.from_dict({"source_hash": "hash"})
    with pytest.raises(ValueError, match="source hash"):
        TeacherWork.from_dict({"version": WORK_VERSION})


def test_empty_work_has_no_parts() -> None:
    """An empty work is what deletes its file."""
    assert _work().empty
    assert not _work(transcription=TranscriptionWork(text="x", text_format="plain")).empty
