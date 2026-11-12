"""Unit tests for the pass rule of the structured-output check."""

from __future__ import annotations

import pytest

from tools.model_eval.check_rule import CheckRule, CheckRun, Decision, Verdict


def _run(
    placed: int = 9,
    total: int = 10,
    explained: int = 5,
    needed: int = 5,
    verdict: Verdict = Verdict.OK,
    **overrides,
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
        **overrides,
    )


def _long(covered: int = 10, late_covered: int = 2, **overrides) -> CheckRun:
    """Return a run on a long fixture with 10 error sentences, 2 of them late."""
    return _run(covered=covered, cover_of=10, late_covered=late_covered, late_of=2, **overrides)


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


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_partial_coverage_fails_a_run() -> None:
    """
    Given  a long fixture with 10 error sentences, 7 and then 8 of them touched
    When   the rule judges the runs
    Then   7 of 10 is PARTIAL and 8 of 10 is OK, because the threshold is a floor
    """
    assert CheckRule().verdict(_long(covered=7)) is Verdict.PARTIAL
    assert CheckRule().verdict(_long(covered=8)) is Verdict.OK


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_no_late_error_sentence_covered_fails_a_run() -> None:
    """
    Given  a run that touches 9 of 10 error sentences but none of the two in the last third
    When   the rule judges it
    Then   the verdict is PARTIAL, because the answer stopped before the end of the text
    """
    assert CheckRule().verdict(_long(covered=9, late_covered=0)) is Verdict.PARTIAL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_short_fixture_has_no_coverage_verdict() -> None:
    """
    Given  a run on a short fixture, which has no error sentences to cover
    When   the rule judges it
    Then   the verdict is OK, so a short text is never judged on coverage
    """
    assert CheckRule().verdict(_run(cover_of=0)) is Verdict.OK


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_partial_comes_after_too_few_placed() -> None:
    """
    Given  a run that places too few sentences and also covers too little of the text
    When   the rule judges it
    Then   the verdict is TOO_FEW_PLACED, because the placement test comes first
    """
    assert CheckRule().verdict(_long(placed=5, covered=1)) is Verdict.TOO_FEW_PLACED


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_partial_is_soft() -> None:
    """
    Given  the PARTIAL verdict
    When   its hardness is read
    Then   it is soft, so one answer that stopped early does not take the badge away
    """
    assert not Verdict.PARTIAL.is_hard


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_one_soft_failure_in_a_round_passes() -> None:
    """
    Given  17 passes and one run that placed too few sentences
    When   the rule decides
    Then   the decision is PASS, because one stray miss is tolerated
    """
    runs = [_run() for _ in range(17)] + [_run(placed=5)]
    assert CheckRule().decision(runs, rounds=1, can_retry=True) is Decision.PASS


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_two_soft_failures_ask_for_a_second_round() -> None:
    """
    Given  16 passes and two runs that placed too few sentences, at a temperature above 0
    When   the rule decides
    Then   the decision is MORE_RUNS, so the model is not failed on two unlucky runs
    """
    runs = [_run() for _ in range(16)] + [_run(placed=5), _run(placed=4)]
    assert CheckRule().decision(runs, rounds=1, can_retry=True) is Decision.MORE_RUNS


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_three_soft_failures_in_the_first_round_fail() -> None:
    """
    Given  15 passes and three runs that placed too few sentences
    When   the rule decides
    Then   the decision is FAIL, because that is a pattern rather than a stray miss
    """
    runs = [_run() for _ in range(15)] + [_run(placed=5), _run(placed=4), _run(placed=3)]
    assert CheckRule().decision(runs, rounds=1, can_retry=True) is Decision.FAIL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_four_soft_failures_in_two_rounds_pass() -> None:
    """
    Given  32 passes and four runs that placed too few sentences over both rounds
    When   the rule decides over both rounds
    Then   the decision is PASS, because four in 36 is the allowance after the second round
    """
    first = [_run() for _ in range(16)] + [_run(placed=5) for _ in range(2)]
    second = [_run(round=2) for _ in range(16)] + [_run(placed=5, round=2) for _ in range(2)]
    assert CheckRule().decision(first + second, rounds=2, can_retry=True) is Decision.PASS


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_five_soft_failures_in_two_rounds_fail() -> None:
    """
    Given  31 passes and five runs that placed too few sentences over both rounds
    When   the rule decides over both rounds
    Then   the decision is FAIL
    """
    first = [_run() for _ in range(16)] + [_run(placed=5) for _ in range(2)]
    second = [_run(round=2) for _ in range(15)] + [_run(placed=5, round=2) for _ in range(3)]
    assert CheckRule().decision(first + second, rounds=2, can_retry=True) is Decision.FAIL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_second_round_does_not_rescue_a_first_round_with_three_failures() -> None:
    """
    Given  saved answers whose first round has three soft failures and whose second round is clean
           (a stricter rule judging a file made under an older one)
    When   the rule decides over both rounds
    Then   the decision is FAIL, as it would have been live
    """
    first = [_run() for _ in range(15)] + [_run(placed=5) for _ in range(3)]
    second = [_run(round=2) for _ in range(18)]
    assert CheckRule().decision(first + second, rounds=2, can_retry=False) is Decision.FAIL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_clean_first_round_passes_whatever_a_kept_second_round_holds() -> None:
    """
    Given  a first round with no failure and a kept second round with one
    When   the rule decides over both rounds
    Then   the decision is PASS: the first round alone already passed
    """
    first = [_run() for _ in range(18)]
    second = [_run(round=2) for _ in range(17)] + [_run(placed=5, round=2)]
    assert CheckRule().decision(first + second, rounds=2, can_retry=False) is Decision.PASS


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_a_hard_failure_in_the_second_round_fails() -> None:
    """
    Given  a second round whose runs are all good but one cut-off answer
    When   the rule decides over both rounds
    Then   the decision is FAIL, because a hard failure fails in either round
    """
    runs = [_run() for _ in range(35)] + [_run(verdict=Verdict.TRUNCATED, round=2)]
    assert CheckRule().decision(runs, rounds=2, can_retry=True) is Decision.FAIL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_no_second_round_at_temperature_zero() -> None:
    """
    Given  two soft failures and a temperature of 0, where a repeat is the same answer again
    When   the rule decides
    Then   the decision is FAIL, because no second round is possible
    """
    runs = [_run() for _ in range(4)] + [_run(placed=5), _run(placed=4)]
    assert CheckRule().decision(runs, rounds=1, can_retry=False) is Decision.FAIL


@pytest.mark.use_case("Model_Eval_Check_Rule")
def test_the_explanation_share_counts_both_rounds() -> None:
    """
    Given  a first round that explains 3 of 5 changes per run and a second round that explains
           all of them
    When   the rule decides over both rounds
    Then   the decision is PASS, because the share is taken over all 36 runs and reaches 0.75
    """
    first = [_run(explained=3, needed=5) for _ in range(18)]
    second = [_run(explained=5, needed=5, round=2) for _ in range(18)]
    assert CheckRule().decision(first + second, rounds=2, can_retry=True) is Decision.PASS
