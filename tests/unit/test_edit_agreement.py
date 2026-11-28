"""Unit tests for ``domain/proofreading/edit_agreement.py``: edits, positions and agreement."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.proofreading.edit_agreement import (
    EditScore,
    apply_alternatives,
    change_key,
    char_edits,
    char_to_token,
    edits,
    match_edits,
    ordered_edits,
    score_edits,
    spans_touch,
    word_positions,
)
from teachers_teammate.domain.proofreading.word_diff import text_offsets, tokenize

SOURCE = "Gestern hat sie ins Kino gegangen."
CORRECTED = "Gestern ist sie ins Kino gegangen."


def test_a_character_span_round_trips_through_the_tokenizer() -> None:
    """
    Given  a text and every token boundary of it
    When   every boundary is mapped to a token and back
    Then   the character offset is the one it started from
    """
    text = "Gestern hat sie ins Kino gegangen."
    boundaries = text_offsets(text)

    for index, position in enumerate(boundaries):
        assert char_to_token(text, position) == index


def test_the_token_boundaries_of_a_text_are_read_once() -> None:
    """Given  a text whose token boundaries were asked for
    When   they are asked for again
    Then   the same tuple comes back, because the boundaries are cached by the text
    """
    text = "Gestern hat sie ins Kino gegangen."
    first = text_offsets(text)

    second = text_offsets(text)

    assert first is second


def test_a_position_inside_a_word_has_no_token_index() -> None:
    """
    Given  a character position in the middle of a word
    When   it is mapped to a token
    Then   there is none
    """
    text = "Gestern hat sie"

    assert char_to_token(text, 3) is None


def test_the_changes_are_the_ones_the_diff_made_in_text_order() -> None:
    """
    Given  a source and a correction that replaces one word
    When   its changes are read in order and as a set
    Then   one change names the word's tokens and its replacement
    """
    start, end, replacement = ordered_edits(SOURCE, CORRECTED)[0]

    assert (start, end) == (2, 3)
    assert replacement == ("ist",)
    assert edits(SOURCE, CORRECTED) == {ordered_edits(SOURCE, CORRECTED)[0]}


def test_a_change_takes_a_character_span_and_its_replacement_as_text() -> None:
    """
    Given  a source and a correction that replaces one word
    When   the change is converted to characters
    Then   the span covers the word and the replacement is the text that replaced it
    """
    ((start, end), replacement) = char_edits(SOURCE, CORRECTED)[0]

    assert SOURCE[start:end] == "hat"
    assert replacement == "ist"


def test_two_changes_that_share_a_word_touch() -> None:
    """
    Given  two spans of words that overlap
    When   they are compared
    Then   they touch
    """
    assert spans_touch((2, 4), (3, 5)) is True


def test_an_insertion_beside_a_replaced_word_does_not_touch_it() -> None:
    """
    Given  a replaced word and an insertion right next to it
    When   the two spans are compared
    Then   they do not touch, because the insertion is a point, not a share of the word
    """
    assert spans_touch((2, 3), (3, 3)) is False


def test_two_insertions_touch_only_at_the_same_place() -> None:
    """
    Given  two insertions at different places and at the same place
    When   each pair is compared
    Then   only the pair at the same place touches
    """
    assert spans_touch((3, 3), (4, 4)) is False
    assert spans_touch((3, 3), (3, 3)) is True


def test_the_same_change_on_both_sides_scores_one_hit() -> None:
    """
    Given  a correction and a reference that make the same change
    When   their agreement is scored
    Then   it is one true positive, one found, and no miss
    """
    score = score_edits(SOURCE, CORRECTED, CORRECTED)

    assert score == EditScore(true_positives=1, false_positives=0, found=1, false_negatives=0)
    assert score.precision == 1.0
    assert score.recall == 1.0


def test_a_fix_of_the_right_words_elsewhere_counts_as_a_miss_and_an_invention() -> None:
    """
    Given  a correction that fixes the same words differently from the reference
    When   their agreement is scored
    Then   the reference change is missed and the correction's own change is a false positive
    """
    score = score_edits(SOURCE, "Gestern war sie ins Kino gegangen.", CORRECTED)

    assert (score.true_positives, score.false_positives) == (0, 1)
    assert (score.found, score.false_negatives) == (0, 1)
    assert score.f_beta() == 0.0


def test_an_accepted_alternative_is_a_true_positive_that_finds_the_reference() -> None:
    """
    Given  a key whose answer fixes the reference's words differently, and accepts that fix
    When   the alternatives are applied
    Then   the answer's change is a hit, and the reference change it overlaps is found
    """
    answer = "Gestern war sie ins Kino gegangen."
    score, accepted = apply_alternatives(
        SOURCE,
        made=edits(SOURCE, answer),
        expected=edits(SOURCE, CORRECTED),
        alternatives=edits(SOURCE, answer),
    )

    assert (score.true_positives, score.false_positives) == (1, 0)
    assert (score.found, score.false_negatives) == (1, 0)
    assert accepted == 1


def test_an_alternative_that_overlaps_no_reference_change_is_optional() -> None:
    """
    Given  an answer that leaves the reference change alone and makes an optional one elsewhere
    When   the alternatives are applied
    Then   the optional change costs nothing, because leaving it out is not punished either
    """
    optional = "Gestern hat sie ins Kino gegangen!"
    score, accepted = apply_alternatives(
        SOURCE,
        made=frozenset(),
        expected=edits(SOURCE, CORRECTED),
        alternatives=edits(SOURCE, optional),
    )

    assert (score.true_positives, score.false_positives) == (0, 0)
    assert (score.found, score.false_negatives) == (0, 1)
    assert accepted == 0


def test_a_change_of_the_same_words_is_exact_and_another_one_is_only_located() -> None:
    """
    Given  a reference change and two answers, one making it and one fixing the same words
    When   every reference change is matched
    Then   the first is exact and located, the second located but not exact
    """
    exact = match_edits(SOURCE, CORRECTED, CORRECTED)[0]
    located = match_edits(SOURCE, "Gestern war sie ins Kino gegangen.", CORRECTED)[0]

    assert (exact.exact, exact.located) == (True, True)
    assert (located.exact, located.located) == (False, True)


def test_an_answer_that_leaves_the_words_alone_locates_nothing() -> None:
    """
    Given  an answer that changes another word than the reference does
    When   every reference change is matched
    Then   it is neither exact nor located
    """
    match = match_edits(SOURCE, "Gestern hat sie ins Theater gegangen.", CORRECTED)[0]

    assert (match.exact, match.located) == (False, False)


def test_a_change_is_compared_by_its_place_in_words_and_its_words() -> None:
    """
    Given  a change that inserts a word at the end of the source, before its own space
    When   it is read in the form the agreement compares
    Then   its span counts words and its replacement holds no whitespace
    """
    positions = word_positions(tokenize("Das ist gut"))

    start, end, words = change_key((5, 5, ("sehr", " ")), positions)

    assert (start, end) == (3, 3)
    assert words == ("sehr",)


def test_an_insertion_either_side_of_a_space_is_the_same_change() -> None:
    """
    Given  a reference that inserts a comma before a space and an answer that inserts it after
    When   their agreement is scored
    Then   it is one hit and no miss, because they are one place in the words
    """
    score = score_edits("Ja das ist gut", "Ja , das ist gut", "Ja, das ist gut")

    assert score == EditScore(true_positives=1, false_positives=0, found=1, false_negatives=0)


def test_a_replacement_that_differs_only_in_whitespace_is_the_same_change() -> None:
    """
    Given  a reference that writes its insertion with one space and an answer with two
    When   their agreement is scored
    Then   the whitespace is not counted, so the answer made the change
    """
    score = score_edits("Das ist gut", "Das ist  sehr gut", "Das ist sehr gut")

    assert (score.true_positives, score.false_positives) == (1, 0)
    assert (score.found, score.false_negatives) == (1, 0)


def test_a_change_is_exact_however_the_spaces_fell() -> None:
    """
    Given  an answer that fixes the reference's place with its own spacing
    When   the reference's changes are matched
    Then   the change is exact, not merely located
    """
    match = match_edits("Ja das ist gut", "Ja , das ist gut", "Ja, das ist gut")[0]

    assert (match.exact, match.located) == (True, True)


def test_a_whitespace_token_does_not_move_a_word_position() -> None:
    """
    Given  the tokens of a text
    When   the word position of every token boundary is read
    Then   a space does not advance it, so a mark either side of a space is the same place
    """
    tokens = tokenize(SOURCE)
    positions = word_positions(tokens)

    space = tokens.index(" ")
    assert positions[space] == positions[space + 1]
    assert len(positions) == len(tokens) + 1


@pytest.mark.parametrize(
    ("beta", "expected"),
    [(0.5, 5 / 6), (1.0, 2 / 3), (2.0, 5 / 9)],
)
def test_precision_and_recall_weigh_the_score(beta: float, expected: float) -> None:
    """
    Given  a score with perfect precision and half the recall
    When   the F-beta score is taken with three weights
    Then   the lower the weight, the less the miss counts
    """
    score = EditScore(true_positives=1, false_positives=0, found=1, false_negatives=1)

    assert score.f_beta(beta) == pytest.approx(expected)
