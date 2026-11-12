"""Tests of the arithmetic the reports share: pooling, the F-beta and its mean."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from tools.model_eval.quality import EditScore
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.scoring import (
    by_configuration,
    f_beta,
    mean_f_beta,
    pooled,
    precision_recall,
)


@dataclass(frozen=True)
class _Counts:
    """One scored text, as :func:`pooled` sees it."""

    true_positives: int
    false_positives: int
    false_negatives: int


@pytest.mark.use_case("Model_Eval_Report")
def test_pooled_sums_the_counts_of_the_texts() -> None:
    """
    Given  two scored texts with different counts
    When   they are pooled
    Then   the result is the sum of each count, as the domain's score holds it
    """
    score = pooled([_Counts(2, 1, 0), _Counts(0, 3, 4)])

    assert score == EditScore(true_positives=2, false_positives=4, false_negatives=4)


@pytest.mark.use_case("Model_Eval_Report")
def test_nothing_scored_has_no_f_beta() -> None:
    """
    Given  a text whose reference has no edit and whose model made none
    When   its F-beta is asked for
    Then   it is None, so the language is left out of every mean instead of counting as 0.00
    """
    assert f_beta(EditScore(0, 0, 0)) is None


@pytest.mark.use_case("Model_Eval_Report")
def test_a_scored_text_gets_the_domains_f_beta() -> None:
    """
    Given  a text with changes the reference does not make and changes it misses
    When   its F-beta is asked for
    Then   it is the domain's own figure, 0.00 here, because nothing was found
    """
    score = EditScore(true_positives=0, false_positives=3, false_negatives=2)

    assert f_beta(score) == score.f_beta() == 0.0
    assert f_beta(EditScore(3, 0, 0)) == 1.0


@pytest.mark.use_case("Model_Eval_Report")
def test_a_share_without_a_denominator_is_none() -> None:
    """
    Given  a text that made no change and a reference that makes none
    When   precision and recall are asked for
    Then   both are None, because neither share has a denominator
    """
    assert precision_recall(EditScore(0, 0, 0)) == (None, None)
    assert precision_recall(EditScore(2, 2, 0)) == (0.5, 1.0)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_mean_skips_the_languages_that_scored_nothing() -> None:
    """
    Given  two languages, one of which scored nothing
    When   the mean F-beta is taken
    Then   only the scored language counts, so the unscored one does not drag the mean down
    """
    by_language = {"de": EditScore(3, 0, 0), "en": EditScore(0, 0, 0)}

    assert mean_f_beta(by_language) == 1.0


@pytest.mark.use_case("Model_Eval_Report")
def test_the_mean_of_no_scored_language_is_none() -> None:
    """
    Given  a run whose every language scored nothing
    When   the mean F-beta is taken
    Then   it is None, which the index and the report print as a dash
    """
    assert mean_f_beta({"de": EditScore(0, 0, 0)}) is None
    assert mean_f_beta({}) is None


@dataclass(frozen=True)
class _Item:
    """One scored text, as :func:`by_configuration` sees it."""

    model: str = "gemma4:e4b"
    temperature: float = 0.2
    mode: AnswerMode = AnswerMode.REVIEW


@pytest.mark.use_case("Model_Eval_Report")
def test_items_are_grouped_by_model_and_temperature() -> None:
    """
    Given  items of two models at two temperatures
    When   they are grouped
    Then   there is one group per model, temperature and mode, in a stable order
    """
    items = [
        _Item(model="qwen3:4b", temperature=0.2),
        _Item(model="gemma4:e4b", temperature=0.2),
        _Item(model="qwen3:4b", temperature=0.7),
    ]

    keys = [key for key, _ in by_configuration(items)]

    assert keys == [
        ("gemma4:e4b", 0.2, AnswerMode.REVIEW),
        ("qwen3:4b", 0.2, AnswerMode.REVIEW),
        ("qwen3:4b", 0.7, AnswerMode.REVIEW),
    ]


@pytest.mark.use_case("Model_Eval_Report")
def test_items_of_two_answer_modes_are_separate_groups() -> None:
    """
    Given  the items of one model at one temperature, asked in review mode and in text mode
    When   they are grouped
    Then   each mode is a group of its own, so the two are never averaged together
    """
    keys = [
        key
        for key, _ in by_configuration([_Item(mode=AnswerMode.TEXT), _Item(mode=AnswerMode.REVIEW)])
    ]

    assert keys == [
        ("gemma4:e4b", 0.2, AnswerMode.REVIEW),
        ("gemma4:e4b", 0.2, AnswerMode.TEXT),
    ]
