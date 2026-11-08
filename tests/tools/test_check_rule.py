"""Unit tests for the pass rule of the structured-output check."""

from __future__ import annotations

import pytest

from tools.model_eval.check_rule import CheckRule, CheckRun, Verdict


def _run(
    placed: int = 9,
    total: int = 10,
    explained: int = 5,
    needed: int = 5,
    verdict: Verdict = Verdict.OK,
) -> CheckRun:
    """Return a run with those numbers, a good one unless told otherwise."""
    return CheckRun(
        fixture="plain DE",
        temperature=0.2,
        verdict=verdict,
        placed=placed,
        total=total,
        explained=explained,
        needed=needed,
    )


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_run_that_places_and_explains_enough_passes() -> None:
    """
    Given  a run that places all sentences and explains every change
    When   the rule judges it
    Then   the verdict is OK
    """
    assert CheckRule().verdict(_run()) is Verdict.OK


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_too_few_placed_sentences_fail_a_run() -> None:
    """
    Given  a run whose answer places only 8 of 10 sentences
    When   the rule judges it
    Then   the verdict is TOO_FEW_PLACED
    """
    assert CheckRule().verdict(_run(placed=8)) is Verdict.TOO_FEW_PLACED


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_the_placement_threshold_is_inclusive() -> None:
    """
    Given  a run that places exactly 90 % of its sentences
    When   the rule judges it
    Then   the verdict is OK, because the threshold is a floor
    """
    assert CheckRule().verdict(_run(placed=9, total=10)) is Verdict.OK


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_too_few_explanations_fail_a_run() -> None:
    """
    Given  a run that explains 1 of 5 changes
    When   the rule judges it
    Then   the verdict is TOO_FEW_EXPLAINED
    """
    assert CheckRule().verdict(_run(explained=1)) is Verdict.TOO_FEW_EXPLAINED


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_run_without_changes_needs_no_explanations() -> None:
    """
    Given  a run whose text needed no explanation
    When   the rule judges it
    Then   the verdict is OK, so an empty list does not divide by zero
    """
    assert CheckRule().verdict(_run(explained=0, needed=0)) is Verdict.OK


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_failure_verdict_of_the_run_is_kept() -> None:
    """
    Given  a run that was cut off, with figures that would otherwise pass
    When   the rule judges it
    Then   the verdict stays TRUNCATED, so a cut-off answer is never re-judged
    """
    assert CheckRule().verdict(_run(verdict=Verdict.TRUNCATED)) is Verdict.TRUNCATED


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_one_stray_failed_run_is_allowed() -> None:
    """
    Given  twelve passes and one run that placed too few sentences
    When   the rule decides whether the model passes
    Then   it does, because a single unlucky run does not take the badge away
    """
    runs = [_run() for _ in range(12)] + [_run(placed=5)]
    assert CheckRule().passes(runs)


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_two_failed_runs_fail_the_model() -> None:
    """
    Given  eleven passes and two runs that placed too few sentences
    When   the rule decides whether the model passes
    Then   it does not, because that is a pattern rather than a stray miss
    """
    runs = [_run() for _ in range(11)] + [_run(placed=5), _run(placed=4)]
    assert not CheckRule().passes(runs)


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_any_hard_failure_fails_the_model() -> None:
    """
    Given  twelve passes and one cut-off answer
    When   the rule decides whether the model passes
    Then   it does not, however few hard failures there are
    """
    runs = [_run() for _ in range(12)] + [_run(verdict=Verdict.TRUNCATED)]
    assert not CheckRule().passes(runs)


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_the_explanation_ratio_over_all_runs_must_reach_the_overall_threshold() -> None:
    """
    Given  runs that all clear the per-run floor but explain only 6 of 10 changes in total
    When   the rule decides whether the model passes
    Then   it does not, because the overall ratio is below 0.75
    """
    runs = [_run(explained=3, needed=5) for _ in range(2)]
    assert not CheckRule().passes(runs)


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_no_runs_at_all_do_not_pass() -> None:
    """
    Given  no runs
    When   the rule decides whether the model passes
    Then   it does not, so a check that never ran cannot mark a model
    """
    assert not CheckRule().passes([])


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_the_hard_verdicts_are_the_three_that_break_the_answer() -> None:
    """
    Given  every verdict of the enum
    When   their hardness is read
    Then   only cut off, failed and too long are hard, the rest are stray misses
    """
    hard = {verdict for verdict in Verdict if verdict.is_hard}
    assert hard == {Verdict.TRUNCATED, Verdict.REQUEST_FAILED, Verdict.REQUEST_TOO_LONG}
