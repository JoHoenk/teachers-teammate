"""Tests of GLEU: the counts of one text, their sum, and the score they give.

The cross-check runs the official script of Napoles' ``gec-ranking``
(``https://github.com/cnap/gec-ranking/blob/master/gleu.py``, header "Updated 6 9 2017"), ported
to Python 3 outside the repository, on the space-joined app tokens of one invented German text:

    python3 official.py hyp.txt src.txt ref.txt

with one line each in ``src.txt``, ``ref.txt`` and ``hyp.txt``.  It printed ``0.845279`` for the
hypothesis, ``0.525322`` for the unchanged source and ``1.000000`` for the reference itself.
"""

from __future__ import annotations

import math

import pytest

from tools.model_eval.gleu import (
    SOURCE_PREFIX,
    GleuCounts,
    add,
    from_scores,
    gleu,
    gleu_counts,
    to_scores,
    tokens,
)

SOURCE = (
    "Der Hund laufen schnell über die Wiese . Die Kinder spielt gern im Garten . "
    "Ich habe ein Hund der sehr laut bellt . Am Abend gehen wir nach Hause . Morgen regnet es ."
)
REFERENCE = (
    "Der Hund läuft schnell über die Wiese . Die Kinder spielen gern im Garten . "
    "Ich habe einen Hund der sehr laut bellt . Am Abend gehen wir nach Hause . Morgen regnet es ."
)
HYPOTHESIS = (
    "Der Hund läuft schnell über die Wiese . Die Kinder spielt gern im Garten . "
    "Ich habe einen Hund der sehr laut bellt . Am Abend gehen wir nach Hause . Morgen regnet es ."
)


def _precision_mean(counts: GleuCounts) -> float:
    """Return the geometric mean of the four precisions, without any brevity penalty."""
    return math.exp(
        sum(math.log(m / p) for m, p in zip(counts.matched, counts.possible, strict=True)) / 4
    )


@pytest.mark.use_case("Model_Eval_Report")
def test_the_score_matches_the_official_script_to_four_decimals() -> None:
    """
    Given  one invented German text, its reference and a hypothesis that fixes two of three errors
    When   the GLEU of the hypothesis, of the unchanged source and of the reference is computed
    Then   each matches the official script's output to four decimal places
    """
    assert " ".join(tokens(SOURCE)) == SOURCE, "the script splits on spaces, as the app tokenizes"

    assert gleu(gleu_counts(SOURCE, HYPOTHESIS, REFERENCE)) == pytest.approx(0.845279, abs=1e-4)
    assert gleu(gleu_counts(SOURCE, SOURCE, REFERENCE)) == pytest.approx(0.525322, abs=1e-4)
    assert gleu(gleu_counts(SOURCE, REFERENCE, REFERENCE)) == pytest.approx(1.0, abs=1e-4)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_hypothesis_equal_to_the_reference_scores_one() -> None:
    """
    Given  a reference without a repeated n-gram
    When   the hypothesis is the reference itself
    Then   every precision is 1 and the score is 1.0
    """
    counts = gleu_counts(SOURCE, REFERENCE, REFERENCE)

    assert counts.matched == counts.possible
    assert gleu(counts) == pytest.approx(1.0)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_unchanged_source_scores_below_one_and_the_reference_changes_above_it() -> None:
    """
    Given  a reference that changes the source
    When   the unchanged source and a hypothesis that makes the reference's changes are scored
    Then   the source scores below 1 and the hypothesis above the source
    """
    unchanged = gleu(gleu_counts(SOURCE, SOURCE, REFERENCE))
    corrected = gleu(gleu_counts(SOURCE, REFERENCE, REFERENCE))

    assert unchanged is not None and unchanged < 1.0
    assert corrected is not None and corrected > unchanged


@pytest.mark.use_case("Model_Eval_Report")
def test_a_word_the_reference_removed_lowers_the_numerator_when_the_hypothesis_keeps_it() -> None:
    """
    Given  a source with "ein" where the reference has "einen"
    When   one hypothesis keeps "ein" and another replaces it
    Then   the kept word lowers the unigram numerator, and a hypothesis of only that word floors
           it at 0 instead of going negative
    """
    source = "Ich habe ein Hund ."
    reference = "Ich habe einen Hund ."

    kept = gleu_counts(source, source, reference)
    fixed = gleu_counts(source, reference, reference)
    only_removed = gleu_counts(source, "ein ein ein ein ein", reference)

    assert kept.matched[0] == 3, "four unigrams the reference has, one of them removed by it"
    assert fixed.matched[0] == 5
    assert only_removed.matched[0] == 0


@pytest.mark.use_case("Model_Eval_Report")
def test_added_counts_are_the_sum_of_every_field_and_score_as_one_text() -> None:
    """
    Given  the counts of two texts, one of them corrected perfectly and one not at all
    When   they are added and scored
    Then   every field is the sum of the two, the score is not the mean of the two scores, and an
           empty sum has no score
    """
    other = "Ich habe einen Hund ."
    first = gleu_counts(SOURCE, HYPOTHESIS, REFERENCE)
    second = gleu_counts(other, other, other)

    total = add([first, second])

    assert total.hyp_len == first.hyp_len + second.hyp_len
    assert total.ref_len == first.ref_len + second.ref_len
    assert total.matched == tuple(a + b for a, b in zip(first.matched, second.matched, strict=True))
    assert total.possible == tuple(
        a + b for a, b in zip(first.possible, second.possible, strict=True)
    )
    assert gleu(total) != pytest.approx((gleu(first) + gleu(second)) / 2)
    assert gleu(add([])) is None


@pytest.mark.use_case("Model_Eval_Report")
def test_a_shorter_hypothesis_gets_the_brevity_penalty_and_a_longer_one_does_not() -> None:
    """
    Given  a hypothesis shorter than the reference and one longer than it
    When   both are scored
    Then   only the shorter one scores below the geometric mean of its precisions
    """
    shorter = gleu_counts(SOURCE, "Der Hund läuft schnell .", REFERENCE)
    longer = gleu_counts(SOURCE, REFERENCE + " . . .", REFERENCE)

    assert gleu(shorter) < _precision_mean(shorter)
    assert gleu(longer) == pytest.approx(_precision_mean(longer))


@pytest.mark.use_case("Model_Eval_Report")
def test_the_stored_keys_read_back_as_the_counts_they_came_from() -> None:
    """
    Given  the counts of a text and of its source
    When   they are written as stored numbers and read back
    Then   the counts are the same, the source's reference length coming from the model's
    """
    counts = gleu_counts(SOURCE, HYPOTHESIS, REFERENCE)
    source_counts = gleu_counts(SOURCE, SOURCE, REFERENCE)

    scores = to_scores(counts) | to_scores(source_counts, SOURCE_PREFIX, reference_length=False)

    assert scores["gleu_c"] == counts.hyp_len
    assert scores["gleu_r"] == counts.ref_len
    assert f"{SOURCE_PREFIX}_r" not in scores
    assert from_scores(scores) == counts
    assert from_scores(scores, SOURCE_PREFIX, ref_len=scores["gleu_r"]) == source_counts
