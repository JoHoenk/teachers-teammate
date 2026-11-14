"""Unit tests for hunks to edits (``domain/proofreading/edits.py``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.proofreading.edits import (
    Batch,
    Edit,
    EditPart,
    NoteAnswer,
    batches,
    drop_overlapping,
    edits,
    merge_notes,
    optional_comma,
    framing_problem,
    group_after,
    sentence_groups,
)
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff


def _edit(number: int, sentence: int) -> Edit:
    """Return a one-part edit in *sentence*, for the batching tests."""
    return Edit(
        id=number,
        parts=(
            EditPart(raw_start=number * 10, raw_end=number * 10 + 1, original="a", corrected="b"),
        ),
        sentences=(sentence,),
    )


def test_a_verb_bracket_is_one_edit() -> None:
    """
    Given  a sentence whose auxiliary moved to the end
    When   its edits are taken
    Then   the deletion and the insertion are one edit
    """
    source = "weil ich habe gestern gewartet"
    corrected = "weil ich gestern gewartet habe"

    found = edits(source, corrected)

    assert len(found) == 1
    assert [(part.original, part.corrected) for part in found[0].parts] == [
        ("habe", ""),
        ("", "habe"),
    ]


def test_a_long_verb_move_is_one_edit() -> None:
    """
    Given  a moved auxiliary with seven words between its old and its new place
    When   its edits are taken
    Then   the move rule joins them, though the gap rule alone would not
    """
    source = "weil ich habe gestern mit meiner Schwester im Park gewartet"
    corrected = "weil ich gestern mit meiner Schwester im Park gewartet habe"

    assert len(edits(source, corrected)) == 1


def test_two_mistakes_side_by_side_are_one_edit() -> None:
    """
    Given  two neighbouring words corrected in one sentence
    When   its edits are taken
    Then   the gap rule makes them one edit
    """
    source = "Ich habe ein grosse Hund gesehen."
    corrected = "Ich habe einen großen Hund gesehen."

    found = edits(source, corrected)

    assert len(found) == 1
    assert len(found[0].parts) == 2


def test_two_mistakes_in_different_sentences_are_two_edits() -> None:
    """
    Given  one mistake in each of two sentences
    When   the edits are taken
    Then   each mistake is its own edit
    """
    source = "Ich gehe heim. Du bleibst hier."
    corrected = "Ich gehe Heim. Du bleibst Hier."

    found = edits(source, corrected)

    assert [edit.sentences for edit in found] == [(0,), (1,)]


def test_a_joined_sentence_is_one_edit_and_one_group() -> None:
    """
    Given  a correction that joins two sentences with a comma
    When   the edits and the sentence groups are taken
    Then   the change is one edit and the two sentences are one group
    """
    source = "Wir blieben im Haus. Weil es regnete."
    corrected = "Wir blieben im Haus, weil es regnete."

    found = edits(source, corrected)

    assert len(found) == 1
    assert found[0].sentences == (0, 1)
    assert sentence_groups(sentence_spans(source), found) == ((0, 1),)


def test_a_split_sentence_is_one_edit() -> None:
    """
    Given  a correction that splits one sentence in two
    When   its edits are taken
    Then   the change is one edit inside the first sentence
    """
    source = "Ich ging heim und dann habe ich gegessen."
    corrected = "Ich ging heim. Dann habe ich gegessen."

    found = edits(source, corrected)

    assert len(found) == 1
    assert found[0].sentences == (0,)


def test_edits_are_numbered_in_text_order() -> None:
    """
    Given  a text with three separate mistakes
    When   its edits are taken
    Then   they are numbered 1, 2, 3 from the start of the text
    """
    source = "Ich habe ein grosse Hund gesehen. Er war laut und grosse. Sie hat ein grosse Katze."
    corrected = (
        "Ich habe einen großen Hund gesehen. Er war laut und groß. Sie hat eine große Katze."
    )

    found = edits(source, corrected)

    assert [edit.id for edit in found] == [1, 2, 3]
    assert [edit.raw_start for edit in found] == sorted(edit.raw_start for edit in found)


def test_a_text_without_changes_has_no_edits() -> None:
    """
    Given  a text the model left unchanged
    When   its edits are taken
    Then   there is no edit
    """
    assert edits("Ich gehe heim.", "Ich gehe heim.") == ()


def test_sentence_groups_keep_every_sentence() -> None:
    """
    Given  a text of three sentences and one edit in the middle one
    When   the sentence groups are taken
    Then   every sentence is in a group, in text order
    """
    source = "Ich gehe heim. Du bleibst hier. Er kommt mit."
    corrected = "Ich gehe heim. Du bleibst Hier. Er kommt mit."

    groups = sentence_groups(sentence_spans(source), edits(source, corrected))

    assert groups == ((0,), (1,), (2,))


def test_batches_hold_whole_sentence_groups() -> None:
    """
    Given  three edits in three sentences and a batch of two edits
    When   the explain batches are taken
    Then   the first two sentences share a batch and the third has its own
    """
    found = [_edit(1, 0), _edit(2, 1), _edit(3, 2)]
    groups = ((0,), (1,), (2,))

    result = batches(found, groups, 2)

    assert [batch.groups for batch in result] == [((0,), (1,)), ((2,),)]
    assert [batch.whole_group for batch in result] == [True, True]


def test_a_group_larger_than_a_batch_is_cut_and_marked() -> None:
    """
    Given  one sentence with three edits and a batch of two edits
    When   the explain batches are taken
    Then   the group is cut at edit boundaries and every part repeats the group
    """
    found = [_edit(1, 0), _edit(2, 0), _edit(3, 0)]

    result = batches(found, ((0,),), 2)

    assert [batch.groups for batch in result] == [((0,),), ((0,),)]
    assert [[edit.id for edit in batch.edits] for batch in result] == [[1, 2], [3]]
    assert [batch.whole_group for batch in result] == [False, False]


def test_batches_of_no_edits_are_none() -> None:
    """
    Given  a text without an edit
    When   the explain batches are taken
    Then   there is no batch, so the step is not sent
    """
    assert batches([], ((0,),), 20) == ()


def test_a_batch_holds_at_least_one_edit() -> None:
    """
    Given  a batch size of zero
    When   the explain batches are taken
    Then   the size is refused
    """
    with pytest.raises(ValueError, match="at least one edit"):
        batches([_edit(1, 0)], ((0,),), 0)


def test_merge_notes_merges_duplicates_drops_unknown_and_reports_missing() -> None:
    """
    Given  an answer with a repeated id, an id nobody asked for and a missing id
    When   its notes are merged
    Then   the duplicate is merged, the unknown one dropped and the missing one reported
    """
    answers = [
        NoteAnswer(id=1, category="grammar", explanation="Wrong case."),
        NoteAnswer(id=1, category="other", explanation="Use the dative."),
        NoteAnswer(id=9, category="grammar", explanation="Not asked for."),
    ]

    merged = merge_notes(answers, [1, 2])

    assert merged.notes == (
        NoteAnswer(id=1, category="grammar", explanation="Wrong case. Use the dative."),
    )
    assert merged.unknown == 1
    assert merged.duplicates == 1
    assert merged.missing == (2,)


def test_drop_overlapping_drops_flags_that_touch_an_edit() -> None:
    """
    Given  an unclear flag on words the correction changed
    When   the overlap filter runs
    Then   the flag is dropped and counted
    """
    source = "Ich habe ein grosse Hund gesehen."
    corrected = "Ich habe einen großen Hund gesehen."
    hunks = word_diff(source, corrected)
    flags = [(source.index("grosse"), source.index("grosse") + len("grosse"))]

    kept, dropped = drop_overlapping(flags, lambda span: span, hunks, side="raw")

    assert kept == ()
    assert dropped == 1


def test_drop_overlapping_keeps_flags_between_edits() -> None:
    """
    Given  an unclear flag on words the correction left alone
    When   the overlap filter runs
    Then   the flag is kept
    """
    source = "Ich habe ein grosse Hund gesehen."
    corrected = "Ich habe einen großen Hund gesehen."
    hunks = word_diff(source, corrected)
    flags = [(source.index("gesehen"), source.index("gesehen") + len("gesehen"))]

    kept, dropped = drop_overlapping(flags, lambda span: span, hunks, side="raw")

    assert kept == tuple(flags)
    assert dropped == 0


def test_an_inserted_comma_before_und_between_main_clauses_is_optional() -> None:
    """
    Given  a correction that only inserts a comma before "und"
    When   the optional-comma rule runs with a clause check that accepts both sides
    Then   the edit is optional
    """
    source = "Ich gehe heim und du bleibst hier."
    corrected = "Ich gehe heim, und du bleibst hier."
    edit = edits(source, corrected)[0]

    assert optional_comma(edit, source, lambda left, right: True)


def test_the_optional_comma_rule_is_off_without_a_clause_check() -> None:
    """
    Given  the same comma insertion and no clause check (no spaCy model installed)
    When   the optional-comma rule runs
    Then   the comma is a normal edit
    """
    source = "Ich gehe heim und du bleibst hier."
    corrected = "Ich gehe heim, und du bleibst hier."
    edit = edits(source, corrected)[0]

    assert not optional_comma(edit, source, None)


def test_a_comma_before_a_subordinate_clause_is_not_optional() -> None:
    """
    Given  a comma insertion before "und" where the clause check says the sides are not equal
    When   the optional-comma rule runs
    Then   the comma is a normal edit
    """
    source = "Ich gehe heim und du bleibst hier."
    corrected = "Ich gehe heim, und du bleibst hier."
    edit = edits(source, corrected)[0]

    assert not optional_comma(edit, source, lambda left, right: False)


def test_a_comma_before_another_word_is_not_optional() -> None:
    """
    Given  a comma inserted before "weil"
    When   the optional-comma rule runs
    Then   the comma is a normal edit
    """
    source = "Ich gehe heim weil es regnet."
    corrected = "Ich gehe heim, weil es regnet."
    edit = edits(source, corrected)[0]

    assert not optional_comma(edit, source, lambda left, right: True)


def test_group_after_shows_the_sentences_as_corrected() -> None:
    """
    Given  a correction of the first of two sentences
    When   each sentence is asked for as the answer leaves it
    Then   the first carries the correction and the second is untouched
    """
    source = "Ich habe ein grosse Hund gesehen. Er war laut."
    corrected = "Ich habe einen großen Hund gesehen. Er war laut."
    spans = sentence_spans(source)

    hunks = word_diff(source, corrected)

    assert group_after(corrected, spans, (0,), hunks) == "Ich habe einen großen Hund gesehen."
    assert group_after(corrected, spans, (1,), hunks) == "Er war laut."


@pytest.mark.parametrize(
    ("source", "corrected", "after"),
    [
        (
            "Ich gehe heute. Ja ja. Nach Hause.",
            "Ich gehe heute nach Hause.",
            "Ich gehe heute nach Hause.",
        ),
        ("Er sagt gut. Das ist es.", "Er sagt gut, das ist es.", "Er sagt gut, das ist es."),
        (
            "weil ich habe lange gewartet.",
            "weil ich lange gewartet habe.",
            "weil ich lange gewartet habe.",
        ),
    ],
)
def test_group_after_is_the_corrected_text_without_a_word_twice(
    source: str, corrected: str, after: str
) -> None:
    """
    Given  a correction that joins sentences or moves a word
    When   every sentence group is shown as the answer leaves it
    Then   the groups together are the corrected text: no word twice, no space lost or doubled
    """
    spans = sentence_spans(source)
    hunks = word_diff(source, corrected)
    found = edits(source, corrected)

    shown = [
        group_after(corrected, spans, group, hunks).strip()
        for group in sentence_groups(spans, found)
    ]

    assert " ".join(shown) == after


def test_group_after_marks_every_edit_in_front_of_its_replacement() -> None:
    """
    Given  a word moved to the end of a clause
    When   the group is shown with its edits marked
    Then   the marks sit where the deletion and the insertion are, with the model's spacing
    """
    source = "weil ich habe lange gewartet."
    corrected = "weil ich lange gewartet habe."
    spans = sentence_spans(source)
    hunks = word_diff(source, corrected)

    shown = group_after(corrected, spans, (0,), hunks, lambda hunk: "⟦1⟧")

    assert shown == "weil ich ⟦1⟧lange gewartet ⟦1⟧habe."


def test_a_sentence_between_the_two_of_an_edit_joins_their_group() -> None:
    """
    Given  one deletion that runs across a whole short sentence
    When   the sentences are grouped
    Then   all three are one group, so no sentence is shown twice
    """
    source = "Ich gehe heute. Ja ja. Nach Hause."
    corrected = "Ich gehe heute nach Hause."
    found = edits(source, corrected)

    assert sentence_groups(sentence_spans(source), found) == ((0, 1, 2),)


def test_a_fix_between_the_halves_of_a_move_is_its_own_edit() -> None:
    """
    Given  a verb moved to the end and a spelling fix far from both halves
    When   the hunks are grouped
    Then   the move is one edit with its two parts in text order and the fix is another
    """
    source = "Er sagt, dass ich habe es dem Lehrer leider gesten nicht richtig gemacht."
    corrected = "Er sagt, dass ich es dem Lehrer leider gestern nicht richtig gemacht habe."

    found = edits(source, corrected)

    assert len(found) == 2
    move, fix = found
    assert [part.original for part in move.parts] == ["habe", ""]
    assert [part.raw_start for part in move.parts] == sorted(part.raw_start for part in move.parts)
    assert fix.original == "gesten"
    assert fix.corrected == "gestern"


def test_a_replacement_is_not_half_of_a_move() -> None:
    """
    Given  a deleted word and, five words later, a replacement that writes the same word
    When   the hunks are grouped
    Then   they stay two edits
    """
    source = "Ich war gestern dort mit dem Hund, aber heute nicht."
    corrected = "Ich war dort mit dem Hund, aber gestern nicht."

    found = edits(source, corrected)

    assert len(found) == 2


def test_a_colon_the_model_adds_to_the_first_line_is_not_a_lead_in() -> None:
    """
    Given  an answer that adds a missing colon to the text's own first line
    When   the answer is checked for framing
    Then   it passes, while a real lead-in line still fails
    """
    source = "Ich brauche folgende Dinge\nBrot\nMilch"

    assert framing_problem(source, "Ich brauche folgende Dinge:\nBrot\nMilch") is None
    assert framing_problem(source, "Hier ist der Text:\n" + source) is not None


def test_a_batch_names_the_sentence_groups_it_shows() -> None:
    """
    Given  a batch of one group
    When   it is built
    Then   it carries that group as its context
    """
    batch = Batch(groups=((3, 4),), edits=(_edit(1, 3),))

    assert batch.groups == ((3, 4),)
    assert batch.whole_group is True
