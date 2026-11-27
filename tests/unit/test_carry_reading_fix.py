"""The carry rule: a reading fix that leaves the proofreading standing.

The offsets of a stored review are plain-text offsets into the transcription the proofreading was
made from.  These tests hold the rule that moves them — and the corrected text with them — over a
fix the teacher typed, and the rule that refuses the fix where it cannot be sure.
"""

from __future__ import annotations

from teachers_teammate.domain.proofreading.carry import (
    CarriedFix,
    WordFix,
    carry_reading_fix,
    retarget_words,
)
from teachers_teammate.domain.proofreading.edits import edits
from teachers_teammate.domain.proofreading.review import (
    CorrectionReview,
    UnclearNote,
    edit_note,
    fits,
)
from teachers_teammate.domain.proofreading.word_diff import word_diff

RAW = "Der bunte Hund bellt laut. Die Katze mauzt."
CORRECTED = "Der braune Hund bellt laut. Die Katze miaut."


def _review(raw: str = RAW, corrected: str = CORRECTED) -> CorrectionReview:
    """Return a review that explains every edit from *raw* to *corrected*."""
    return CorrectionReview(
        edits=tuple(
            edit_note(edit, category="word_choice", explanation="Wortwahl.")
            for edit in edits(raw, corrected)
        )
    )


def test_a_fix_away_from_every_edit_is_carried() -> None:
    """
    Given  a proofread text with one explanation per correction
    When   a word the corrections do not touch is fixed
    Then   the corrected text takes the fix too, and the review keeps its explanations
    """
    fixed = RAW.replace("laut", "leise")

    carried = carry_reading_fix(RAW, fixed, CORRECTED, _review())

    assert carried is not None
    assert carried.corrected == CORRECTED.replace("laut", "leise")
    assert fits(carried.review, fixed)
    assert [note.explanation for note in carried.review.edits] == ["Wortwahl.", "Wortwahl."]
    assert [note.raw_start for note in carried.review.edits] == [
        fixed.index("bunte"),
        fixed.index("mauzt"),
    ]


def test_a_fix_between_two_edits_moves_the_later_ones() -> None:
    """
    Given  two corrections, one before the other
    When   the words between them get longer, and then shorter
    Then   every offset after the fix moves by the length the fix added or took
    """
    longer = RAW.replace(" bellt laut.", " bellt sehr laut.")
    shorter = RAW.replace(" bellt laut.", " bellt.")

    grew = carry_reading_fix(RAW, longer, CORRECTED, _review())
    shrank = carry_reading_fix(RAW, shorter, CORRECTED, _review())

    assert grew is not None, "a word added between two corrections must be carried"
    assert fits(grew.review, longer)
    assert [note.raw_start for note in grew.review.edits] == [
        longer.index("bunte"),
        longer.index("mauzt"),
    ]
    assert shrank is not None, "a word removed between two corrections must be carried"
    assert fits(shrank.review, shorter)
    assert [note.raw_start for note in shrank.review.edits] == [
        shorter.index("bunte"),
        shorter.index("mauzt"),
    ]


def test_an_inserted_word_is_carried_with_its_space() -> None:
    """
    Given  a proofread text
    When   the teacher adds a word the corrections do not touch
    Then   the corrected text gets the word where it belongs, with the space it needs
    """
    fixed = RAW.replace("Katze mauzt", "graue Katze mauzt")

    carried = carry_reading_fix(RAW, fixed, CORRECTED, _review())

    assert carried is not None
    assert carried.corrected == CORRECTED.replace("Katze miaut", "graue Katze miaut")
    assert fits(carried.review, fixed)


def test_a_removed_word_takes_one_space_with_it() -> None:
    """
    Given  a proofread text
    When   the teacher removes a word the corrections do not touch
    Then   the corrected text loses the word and keeps one space between its neighbours
    """
    fixed = RAW.replace(" bellt laut", " bellt")

    carried = carry_reading_fix(RAW, fixed, CORRECTED, _review())

    assert carried is not None
    assert carried.corrected == CORRECTED.replace(" bellt laut", " bellt")
    assert fits(carried.review, fixed)


def test_a_fix_inside_an_edit_is_not_carried() -> None:
    """
    Given  a correction of one word
    When   the teacher's fix changes that same word
    Then   nothing is carried: its explanation is about words that are no longer there
    """
    fixed = RAW.replace("bunte", "buntee")

    assert carry_reading_fix(RAW, fixed, CORRECTED, _review()) is None


def test_a_fix_that_starts_where_an_edit_starts_is_not_carried() -> None:
    """
    Given  a correction of a word that stands right behind an opening quotation mark
    When   the teacher's fix inserts a word at the first letter of that word
    Then   nothing is carried: the insertion and the correction cannot be told apart there
    """
    raw = "Der „bunte“ Hund bellt laut."
    corrected = raw.replace("bunte", "braune")
    fixed = raw.replace("bunte", "sehr bunte")

    assert carry_reading_fix(raw, fixed, corrected, _review(raw, corrected)) is None


def test_a_fix_inside_an_unclear_passage_is_not_carried() -> None:
    """
    Given  a review that asks about the words ``bellt laut``
    When   the teacher's fix changes one of them
    Then   nothing is carried: the question is about the words the fix took away
    """
    span = RAW.index("bellt laut")
    review = CorrectionReview(
        unclear=(
            UnclearNote(
                raw_start=span,
                raw_end=span + len("bellt laut"),
                expression="bellt laut",
                why="Was heißt das?",
                recommendation="bellt laut?",
            ),
        )
    )
    fixed = RAW.replace("laut", "leise")

    assert carry_reading_fix(RAW, fixed, CORRECTED, review) is None


def test_one_unsafe_fix_carries_nothing() -> None:
    """
    Given  two fixes in one edit of the reading, one of them inside a correction
    When   the fix is carried
    Then   nothing is carried: half of it would leave the corrected text wrong
    """
    fixed = RAW.replace("bunte", "braune").replace("laut", "leise")

    assert carry_reading_fix(RAW, fixed, CORRECTED, _review()) is None


def test_a_change_the_model_made_without_explaining_it_is_not_carried() -> None:
    """
    Given  a corrected text that changed the words of the fix without saying so
    When   the teacher fixes those words
    Then   nothing is carried: the corrected text there is not the reading's own
    """
    unexplained = CORRECTED.replace(" bellt laut", " ruft")
    fixed = RAW.replace("laut", "leise")

    assert carry_reading_fix(RAW, fixed, unexplained, _review()) is None


def test_an_unchanged_text_keeps_the_proofreading_as_it_is() -> None:
    """
    Given  a proofread text
    When   the teacher's text is what was read already
    Then   the corrected text and the review come back unchanged
    """
    carried = carry_reading_fix(RAW, RAW, CORRECTED, _review())

    assert carried == CarriedFix(corrected=CORRECTED, review=_review())


def test_words_that_stand_once_in_the_teachers_text_take_the_fix() -> None:
    """
    Given  the teacher's own corrected text
    When   the fix's words stand in it exactly once
    Then   they are replaced there too, and the rest of that text is untouched
    """
    teacher = "Der braune Hund bellt laut. Die Katze schweigt."
    shift = len("braune") - len("bunte")
    start = RAW.index("laut") + shift

    carried = retarget_words(teacher, (WordFix(start, start + len("laut"), "laut", "leise"),))

    assert carried == "Der braune Hund bellt leise. Die Katze schweigt."


def test_words_that_stand_twice_in_the_teachers_text_take_no_fix() -> None:
    """
    Given  the teacher's own corrected text, with the fix's word in it twice
    When   the fix is placed by its words
    Then   it is not placed at all, and the caller drops the proofreading
    """
    teacher = "Der braune Hund bellt laut, und laut bellt er."

    assert retarget_words(teacher, (WordFix(21, 25, "laut", "leise"),)) is None


def test_inserted_words_have_no_place_in_the_teachers_text() -> None:
    """
    Given  a fix that only adds words
    When   it is placed in the teacher's own corrected text
    Then   nothing is placed: there are no words of the reading to find
    """
    assert retarget_words("Der braune Hund bellt.", (WordFix(4, 4, "", "sehr"),)) is None


def test_a_fix_before_every_correction_moves_them_all() -> None:
    """
    Given  a proofread text
    When   the teacher's fix lengthens the first word, before every correction
    Then   every offset of the review moves by the characters the fix added
    """
    fixed = RAW.replace("Der bunte", "Jener bunte")

    carried = carry_reading_fix(RAW, fixed, CORRECTED, _review())

    assert carried is not None
    assert carried.corrected == CORRECTED.replace("Der braune", "Jener braune")
    assert fits(carried.review, fixed)
    assert [note.raw_start for note in carried.review.edits] == [
        fixed.index("bunte"),
        fixed.index("mauzt"),
    ]


def test_a_review_without_notes_carries_every_fix() -> None:
    """
    Given  a text the proofreading left as it was read
    When   a word is fixed
    Then   the fix is carried and the empty review fits the new text
    """
    fixed = RAW.replace("Hund", "Kater")

    carried = carry_reading_fix(RAW, fixed, RAW, CorrectionReview())

    assert carried is not None
    assert carried.corrected == fixed
    assert fits(carried.review, fixed)


def _changes(old: str, new: str) -> list[tuple[str, str]]:
    """Return what the word diff from *old* to *new* changes, in order."""
    return [(hunk.original, hunk.corrected) for hunk in word_diff(old, new)]


def test_the_carried_text_differs_from_the_new_reading_by_what_the_model_changed() -> None:
    """
    Given  a proofreading and a fix that collapses a double space before the model's change
    When   the fix is carried
    Then   the corrected text is the model's text with the fix, and no word is glued or split
    """
    old = "Der Hnud bellt.  Er gehte heim."
    new = "Der Hund bellt. Er gehte heim."
    corrected = "Der Hnud bellt. Er ging heim."

    carried = carry_reading_fix(old, new, corrected, _review(old, corrected))

    assert carried is not None
    assert carried.corrected == "Der Hund bellt. Er ging heim."
    assert fits(carried.review, new)
    assert carried.review.edits[0].raw_start == new.index("gehte")


def test_a_fix_after_a_collapsed_double_space_is_placed_in_the_right_words() -> None:
    """
    Given  a corrected text in which the model collapsed a double space before the fixed word
    When   the fix is carried
    Then   the word is replaced where it stands, not a few characters off
    """
    old = "Er gehte heim.  Der Hnud bellt."
    new = "Er gehte heim.  Der Hund bellt."
    corrected = "Er ging heim. Der Hnud bellt."

    carried = carry_reading_fix(old, new, corrected, _review(old, corrected))

    assert carried is not None
    assert carried.corrected == "Er ging heim. Der Hund bellt."


def test_deleting_a_word_and_typing_the_new_one_carries_through_both_saves() -> None:
    """
    Given  a proofread text, and a typing that is stored twice: the word is deleted, then the
           new one is put in the gap
    When   each save is carried over the result of the one before
    Then   the corrected text ends as the model's text with the new word in it
    """
    old = "Er gehte heim. Der Hnud bellt."
    corrected = "Er ging heim. Der Hnud bellt."
    gap = "Er gehte heim. Der  bellt."
    done = "Er gehte heim. Der Hund bellt."

    first = carry_reading_fix(old, gap, corrected, _review(old, corrected))
    assert first is not None
    second = carry_reading_fix(gap, done, first.corrected, first.review)

    assert second is not None
    assert second.corrected == "Er ging heim. Der Hund bellt."
    assert fits(second.review, done)
    assert _changes(done, second.corrected) == _changes(old, corrected)


def test_a_change_of_layout_alone_is_carried() -> None:
    """
    Given  a proofread text
    When   the teacher only adds a space or a line break between words
    Then   the proofreading stays and its offsets move with the layout
    """
    fixed = RAW.replace("bellt laut.", "bellt laut.\n")

    carried = carry_reading_fix(RAW, fixed, CORRECTED, _review())

    assert carried is not None
    assert carried.corrected == CORRECTED
    assert carried.fixes == ()
    assert fits(carried.review, fixed)


def test_an_insertion_point_of_the_review_stays_between_the_same_words() -> None:
    """
    Given  a review with an inserted comma, and a fix that shortens a double space before it
    When   the fix is carried
    Then   the comma still stands between the same two words, and the question about
           ``Hunger`` still quotes it
    """
    old = "Der Hnud bellt sehr.  Er bellt laut weil er Hunger hat."
    corrected = "Der Hnud bellt sehr.  Er bellt laut, weil er Hunger hat."
    new = "Der Hund bellt sehr. Er bellt laut weil er Hunger hat."
    span = old.index("Hunger")
    review = CorrectionReview(
        edits=_review(old, corrected).edits,
        unclear=(
            UnclearNote(
                raw_start=span,
                raw_end=span + len("Hunger"),
                expression="Hunger",
                why="Was heißt das?",
                recommendation="Hunger?",
            ),
        ),
    )

    carried = carry_reading_fix(old, new, corrected, review)

    assert carried is not None
    comma = carried.review.edits[0]
    assert new[: comma.raw_start].endswith("laut")
    question = carried.review.unclear[0]
    assert new[question.raw_start : question.raw_end] == "Hunger"
    assert _changes(new, carried.corrected) == _changes(old, corrected)


def test_a_word_inside_a_longer_word_is_not_placed_in_the_teachers_text() -> None:
    """
    Given  the teacher's own corrected text, which holds the fix's word only inside a longer one
    When   the fix is placed by its words
    Then   it is not placed
    """
    assert retarget_words("Die Hausaufgabe war da.", (WordFix(0, 4, "Haus", "Hof"),)) is None


def test_a_word_that_stands_alone_is_replaced_even_next_to_longer_words() -> None:
    """
    Given  the teacher's own corrected text with the fix's word once on its own
    When   the fix is placed by its words
    Then   only that word is replaced
    """
    carried = retarget_words("Das Haus hat Hausaufgaben.", (WordFix(4, 8, "Haus", "Hof"),))

    assert carried == "Das Hof hat Hausaufgaben."


def test_a_removed_word_leaves_one_space_in_the_teachers_text() -> None:
    """
    Given  the teacher's own corrected text
    When   the fix removes a word from it
    Then   the words around it keep one space between them
    """
    carried = retarget_words("Der bunte Hund bellt.", (WordFix(4, 9, "bunte", ""),))

    assert carried == "Der Hund bellt."
