"""Unit tests for the proofreading quality metrics (edit agreement, explanations)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionReview,
    SentenceNote,
)
from teachers_teammate.domain.proofreading_quality import (
    EditScore,
    ExplainedCount,
    count_explained,
    edits,
    score_edits,
    unchanged_sentences,
)

_SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."


@pytest.mark.use_case("Proofreading_Quality")
def test_an_unchanged_text_makes_no_edits() -> None:
    """
    Given  a text and the very same text
    When   the edits are computed
    Then   there are none, so nothing counts as a change
    """
    assert edits(_SOURCE, _SOURCE) == frozenset()


@pytest.mark.use_case("Proofreading_Quality")
def test_a_replaced_word_is_one_edit_with_the_replacement() -> None:
    """
    Given  a text and the same text with one word replaced
    When   the edits are computed
    Then   there is exactly one edit, carrying the new word
    """
    result = edits(_SOURCE, _SOURCE.replace("sein", "seiner"))

    assert len(result) == 1
    (edit,) = result
    assert edit[2] == ("seiner",)


@pytest.mark.use_case("Proofreading_Quality")
def test_a_whitespace_difference_is_not_an_edit() -> None:
    """
    Given  a text and the same text with a line break instead of a space
    When   the edits are computed
    Then   there is no edit, because a teacher cannot see the difference
    """
    assert edits("Ein Satz hier.", "Ein Satz\nhier.") == frozenset()


@pytest.mark.use_case("Proofreading_Quality")
def test_a_perfect_correction_scores_one_on_every_measure() -> None:
    """
    Given  a model text that equals the reference correction
    When   the score is computed
    Then   precision, recall and F0.5 are all one
    """
    reference = _SOURCE.replace("sein", "seiner")

    score = score_edits(_SOURCE, reference, reference)

    assert (score.true_positives, score.false_positives, score.false_negatives) == (1, 0, 0)
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.f_beta() == 1.0


@pytest.mark.use_case("Proofreading_Quality")
def test_an_untouched_text_finds_no_reference_change() -> None:
    """
    Given  a model text that changes nothing while the reference changes one word
    When   the score is computed
    Then   recall is zero and there is one false negative
    """
    score = score_edits(_SOURCE, _SOURCE, _SOURCE.replace("sein", "seiner"))

    assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 0, 1)
    assert score.recall == 0.0
    assert score.precision == 0.0
    assert score.f_beta() == 0.0


@pytest.mark.use_case("Proofreading_Quality")
def test_a_change_the_reference_does_not_make_is_a_false_positive() -> None:
    """
    Given  a model text that replaces one word the reference leaves alone
    When   the score is computed
    Then   the change counts as a false positive and the reference's own as a miss
    """
    score = score_edits(
        _SOURCE, _SOURCE.replace("Kino", "Theater"), _SOURCE.replace("sein", "seiner")
    )

    assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 1, 1)
    assert score.precision == 0.0
    assert score.recall == 0.0


@pytest.mark.use_case("Proofreading_Quality")
def test_the_right_words_with_the_wrong_replacement_count_once_each() -> None:
    """
    Given  a model text and a reference that change the same words differently
    When   the score is computed
    Then   the span is one false positive and one false negative, never a hit
    """
    score = score_edits(
        _SOURCE, _SOURCE.replace("sein", "ihrer"), _SOURCE.replace("sein", "seiner")
    )

    assert (score.true_positives, score.false_positives, score.false_negatives) == (0, 1, 1)


@pytest.mark.use_case("Proofreading_Quality")
@pytest.mark.parametrize(
    ("beta", "expected"),
    [(0.5, 0.625), (1.0, 0.571), (2.0, 0.526)],
)
def test_f_beta_weights_precision_and_recall(beta: float, expected: float) -> None:
    """
    Given  two hits, one invention and two misses
    When   F-beta is computed for three betas
    Then   a beta below one favours precision and a beta above one favours recall
    """
    score = EditScore(true_positives=2, false_positives=1, false_negatives=2)

    assert score.f_beta(beta) == pytest.approx(expected, abs=0.001)


@pytest.mark.use_case("Proofreading_Quality")
def test_a_change_that_explains_itself_needs_no_note() -> None:
    """
    Given  a review whose only change is a comma, a spelling category change
    When   the explanations are counted
    Then   nothing needs an explanation, so the count is complete rather than empty
    """
    source = "Er kam mit dem Rad und sie zu Fuss."
    corrected = "Er kam mit dem Rad, und sie zu Fuss."
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                original="Er kam mit dem Rad und sie zu Fuss.",
                corrected="Er kam mit dem Rad, und sie zu Fuss.",
                changes=(
                    ChangeNote(
                        original="Rad und",
                        corrected="Rad, und",
                        category="punctuation",
                        explanation="",
                    ),
                ),
            ),
        )
    )

    count = count_explained(source, corrected, review)

    assert (count.explained, count.needed, count.orphans) == (0, 0, 0)
    assert count.ratio == 1.0


@pytest.mark.use_case("Proofreading_Quality")
def test_a_word_change_without_an_explanation_counts_as_needed_but_not_explained() -> None:
    """
    Given  a review whose word change carries a blank explanation
    When   the explanations are counted
    Then   the change needs an explanation and the count reports it as unexplained
    """
    source = "Er hat sein Freundin mitgebracht."
    corrected = "Er hat seine Freundin mitgebracht."
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                original=source,
                corrected=corrected,
                changes=(
                    ChangeNote(
                        original="sein",
                        corrected="seine",
                        category="grammar",
                        explanation="   ",
                    ),
                ),
            ),
        )
    )

    count = count_explained(source, corrected, review)

    assert count.needed == 1
    assert count.explained == 0
    assert count.ratio == 0.0


@pytest.mark.use_case("Proofreading_Quality")
def test_a_note_that_explains_no_change_is_an_orphan() -> None:
    """
    Given  a review whose note names a change that is not in the text
    When   the explanations are counted
    Then   the note is reported as an orphan and nothing else counts
    """
    source = "Ein ganz normaler Satz."
    review = CorrectionReview(
        sentences=(
            SentenceNote(
                original="Ein ganz anderer Satz.",
                corrected="Ein ganz anderer Satz!",
                changes=(
                    ChangeNote(
                        original="Satz.",
                        corrected="Satz!",
                        category="punctuation",
                        explanation="Satzzeichen.",
                    ),
                ),
            ),
        )
    )

    count = count_explained(source, source, review)

    assert count == ExplainedCount(explained=0, needed=0, orphans=1)


@pytest.mark.use_case("Proofreading_Quality")
def test_no_review_counts_nothing_at_all() -> None:
    """
    Given  a corrections-only outcome without a review
    When   the explanations are counted
    Then   every number is zero and the ratio counts as complete
    """
    count = count_explained(_SOURCE, _SOURCE, None)

    assert count == ExplainedCount(explained=0, needed=0, orphans=0)
    assert count.ratio == 1.0


@pytest.mark.use_case("Proofreading_Quality")
def test_unchanged_sentences_counts_what_survived_word_for_word() -> None:
    """
    Given  two correct sentences, one of which the model rewrote
    When   the unchanged sentences are counted
    Then   only the one that still appears word for word is counted
    """
    sentences = ["Das Wetter war schön.", "Wir sind gewandert."]
    output = "Das Wetter war schön. Wir sind heute gewandert."

    assert unchanged_sentences(sentences, output) == 1
