"""Tests of the numbers of one item: GLEU, recall by half, left alone and coverage."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from teachers_teammate.domain.correction_review import tokenize
from tools.model_eval.quality import score_edits
from tools.model_eval.gleu import SOURCE_PREFIX
from tools.model_eval.item_scores import item_scores

FIRST = "Der Hund laufen schnell ."
SECOND = "Die Kinder spielt gern im Garten ."
THIRD = "Am Abend gehen wir nach Hause ."
SOURCE = f"{FIRST} {SECOND} {THIRD}"
REFERENCE = f"Der Hund läuft schnell . {SECOND} Am Abend gehen wir nach Hause ."
"""The reference fixes the first sentence and leaves the second and the third alone."""

FIXED_FIRST = "Der Hund läuft schnell ."
FIXED_THIRD = "Am Abend gingen wir nach Hause ."
OTHER_THIRD = "Am Abend gehen wir heim ."
"""A third sentence the model changes differently from the reference."""
BOTH_HALVES = f"{FIXED_FIRST} {SECOND} {FIXED_THIRD}"
"""A reference that changes the first and the third sentence, one in each half of the text."""


def _spans(source: str, sentences: Sequence[str]) -> tuple[tuple[int, int], ...]:
    """Return the token span of every sentence of *source*, whitespace tokens included."""
    tokens = tokenize(source)
    spans = []
    index = 0
    for sentence in sentences:
        wanted = tokenize(sentence)
        while tokens[index : index + len(wanted)] != wanted:
            index += 1
        spans.append((index, index + len(wanted)))
        index += len(wanted)
    return tuple(spans)


SPANS = _spans(SOURCE, (FIRST, SECOND, THIRD))


@pytest.mark.use_case("Model_Eval_Report")
def test_a_sentence_the_reference_leaves_alone_and_the_model_changes_is_not_left_alone() -> None:
    """
    Given  a reference that leaves the second sentence alone
    When   the model changes that sentence, and in another run leaves it alone
    Then   left alone is 0 of 1 in the first case and 1 of 1 in the second
    """
    changed = item_scores(
        SOURCE,
        f"{FIRST} Die Kinder spielen gern im Garten . {THIRD}",
        REFERENCE,
        sentence_spans=SPANS,
        unchanged_sentences=(1,),
    )
    kept = item_scores(
        SOURCE,
        f"{FIXED_FIRST} {SECOND} {THIRD}",
        REFERENCE,
        sentence_spans=SPANS,
        unchanged_sentences=(1,),
    )

    assert (changed["alone"], changed["alone_of"]) == (0, 1)
    assert (kept["alone"], kept["alone_of"]) == (1, 1)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_word_inserted_between_two_sentences_counts_against_both() -> None:
    """
    Given  a reference that leaves the second sentence alone
    When   the model inserts a word at the edge between the first and the second sentence
    Then   the second sentence is not left alone, because the word may belong to either of them
    """
    scores = item_scores(
        SOURCE,
        f"{FIRST} Heute {SECOND} {THIRD}",
        REFERENCE,
        sentence_spans=SPANS,
        unchanged_sentences=(1,),
    )

    assert (scores["alone"], scores["alone_of"]) == (0, 1)


@pytest.mark.use_case("Model_Eval_Report")
def test_coverage_counts_a_sentence_the_model_looked_at_even_with_another_replacement() -> None:
    """
    Given  a reference that changes the first and the third sentence
    When   the model leaves the first sentence alone and fixes the third differently
    Then   coverage is 1 of 2, because coverage is "looked at", not "fixed"
    """
    scores = item_scores(
        SOURCE,
        f"{FIRST} {SECOND} {OTHER_THIRD}",
        BOTH_HALVES,
        sentence_spans=SPANS,
    )
    untouched = item_scores(SOURCE, SOURCE, BOTH_HALVES, sentence_spans=SPANS)

    assert (scores["cover"], scores["cover_of"]) == (1, 2)
    assert (untouched["cover"], untouched["cover_of"]) == (0, 2)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_halves_split_the_reference_changes_and_add_up_to_the_score() -> None:
    """
    Given  a reference with one change in the first half of the text and one in the second
    When   the model fixes only the first
    Then   the first half has one match and no miss, the second one miss and no match, and the
           halves add up to the counts of the whole text
    """
    scores = item_scores(SOURCE, f"{FIXED_FIRST} {SECOND} {THIRD}", BOTH_HALVES)
    score = score_edits(SOURCE, f"{FIXED_FIRST} {SECOND} {THIRD}", BOTH_HALVES)

    assert (scores["tp_h1"], scores["fn_h1"]) == (1, 0)
    assert (scores["tp_h2"], scores["fn_h2"]) == (0, 1)
    assert scores["tp_h1"] + scores["tp_h2"] == score.true_positives
    assert scores["fn_h1"] + scores["fn_h2"] == score.false_negatives


@pytest.mark.use_case("Model_Eval_Report")
def test_a_text_without_spans_has_no_left_alone_and_no_coverage() -> None:
    """
    Given  a text whose set kept no sentence spans
    When   its numbers are computed
    Then   the GLEU and half keys are there and the left-alone and coverage keys are not
    """
    scores = item_scores(SOURCE, BOTH_HALVES, BOTH_HALVES)

    assert "alone" not in scores and "cover" not in scores
    assert {"gleu_c", "gleu_r", "gleu_m1", "gleu_p4", "tp_h1", "fn_h2"} <= set(scores)


@pytest.mark.use_case("Model_Eval_Report")
def test_an_output_equal_to_the_source_has_the_sources_gleu_counts() -> None:
    """
    Given  a model that changed nothing
    When   its numbers are computed
    Then   its GLEU counts are the source's, and the source's counts carry no reference length
    """
    scores = item_scores(SOURCE, SOURCE, BOTH_HALVES)

    model = {key: value for key, value in scores.items() if key.startswith("gleu_")}
    source = {
        key.removeprefix(f"{SOURCE_PREFIX}_"): value
        for key, value in scores.items()
        if key.startswith(f"{SOURCE_PREFIX}_")
    }

    assert model["gleu_c"] == source["c"]
    assert model["gleu_m1"] == source["m1"]
    assert f"{SOURCE_PREFIX}_r" not in scores


@pytest.mark.use_case("Model_Eval_Report")
def test_a_correct_text_has_no_numbers() -> None:
    """
    Given  a text without a reference correction, as a ``correct`` item has
    When   its numbers are computed
    Then   there are none, because nothing was scored
    """
    assert item_scores("Ein guter Satz .", "Ein guter Satz .", "") == {}
