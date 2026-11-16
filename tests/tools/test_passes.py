"""Tests of the passes table: what a vote over the repeats would keep, and what it would cost.

The repeats of one configuration are the passes, and an edit's votes are how many of them made it.
The tests use three hand-made passes over one text, so every count below is arithmetic a reader can
follow: two edits the reference makes, one of them in every pass, and one change only one pass made.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import answer_mode, grouping, passes, performance, quality, results

SOURCE = "a b c d e f"
REFERENCE = "a B c D e f"
"""The reference makes two changes: ``b`` becomes ``B`` and ``d`` becomes ``D``."""

AGREED = "a B c D e f"
"""A pass that makes both of the reference's changes."""

HALF = "a B c X e f"
"""A pass that makes the first change and a wrong one in place of the second."""


def _row(
    *,
    item: str = "de-01",
    language: str = "de",
    output: str = AGREED,
    seconds: float = 1.0,
) -> results.ResultRow:
    """Return one result row of an error text, corrected to *output*."""
    return results.ResultRow(
        id=f"{language}/{item}",
        model="gemma4:e4b",
        temperature=0.05,
        language=language,
        kind="errors",
        origin="Falko-MERLIN test",
        seconds=seconds,
        failed=False,
        fallback=False,
        has_review=False,
        source=SOURCE,
        output=output,
        reference=REFERENCE,
        mode=answer_mode.AnswerMode.CORRECT_ONLY,
    )


def _group(per_repeat: Sequence[Sequence[results.ResultRow]]) -> grouping.RunGroup:
    """Return a group whose repeats hold the rows of *per_repeat*, in repeat order."""
    key = grouping.GroupKey(
        testset="test-2026-11",
        manifest_sha256="a" * 64,
        eval_version=1,
        provider="ollama",
        machine="rx9070xt-server",
        model="gemma4:e4b",
        digest="sha256:0123456789abcdef0123456789abcdef",
        request_profile=(("compact_json", False), ("temperature", 0.05)),
        mode=answer_mode.AnswerMode.CORRECT_ONLY,
        explainer=None,
        temperature=0.05,
        num_ctx=8192,
    )
    return grouping.RunGroup(key=key, runs=(), items=tuple(tuple(rows) for rows in per_repeat))


def _three_passes() -> grouping.RunGroup:
    """Return three passes over one text: two agree, the third makes one wrong change."""
    return _group([[_row(output=AGREED)], [_row(output=AGREED)], [_row(output=HALF)]])


@pytest.mark.use_case("Model_Eval_Report")
def test_the_single_pass_is_the_mean_of_the_repeats() -> None:
    """
    Given  three passes, two of which make both of the reference's changes and one of which makes
           one of them and a wrong change instead of the other
    When   the passes are built
    Then   the single pass is the mean of the three, so two fixes and no wrong change
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.repeats == 3
    assert row.single.true_positives == 2
    assert row.single.false_positives == 0
    assert row.single.false_negatives == 0


@pytest.mark.use_case("Model_Eval_Report")
def test_the_vote_keeps_the_changes_at_least_two_of_three_made() -> None:
    """
    Given  the same three passes
    When   the passes are built
    Then   the vote keeps both of the reference's changes, because two passes made each of them
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.vote_two.true_positives == 2
    assert row.vote_two.false_positives == 0
    assert row.vote_two.false_negatives == 0


@pytest.mark.use_case("Model_Eval_Report")
def test_keeping_only_what_every_pass_made_drops_the_change_one_pass_missed() -> None:
    """
    Given  the same three passes
    When   the passes are built
    Then   only the change every pass made survives, so one fix is kept and one is missed
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.all_repeats.true_positives == 1
    assert row.all_repeats.false_positives == 0
    assert row.all_repeats.false_negatives == 1


@pytest.mark.use_case("Model_Eval_Report")
def test_the_flag_covers_half_the_changes_and_all_of_the_false_alarms() -> None:
    """
    Given  the same three passes: six changes in all, of which three were not made by every pass
    When   the passes are built
    Then   the flag covers half of the changes, all of the false alarms and two of the five fixes
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.flagged_share == pytest.approx(0.5)
    assert row.flagged_false_alarms == pytest.approx(1.0)
    assert row.flagged_true_fixes == pytest.approx(0.4)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_precision_of_a_change_rises_with_the_number_of_passes_that_made_it() -> None:
    """
    Given  the same three passes: one change made by one pass, one by two, one by all three
    When   the passes are built
    Then   the change only one pass made is a false alarm, and the other two are right
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.precision_by_votes == (
        pytest.approx(0.0),
        pytest.approx(1.0),
        pytest.approx(1.0),
    )


@pytest.mark.use_case("Model_Eval_Report")
def test_a_group_of_one_repeat_has_no_vote_to_take_and_is_left_out() -> None:
    """
    Given  a group of one repeat
    When   the passes are built
    Then   there is no row, because a vote needs a second pass
    """
    assert passes.pass_rows([_group([[_row()]])]) == []


@pytest.mark.use_case("Model_Eval_Report")
def test_a_committed_run_is_refused_because_it_keeps_no_texts() -> None:
    """
    Given  a group whose items are committed items rather than result rows
    When   the passes are built
    Then   it is refused, because the changes cannot be read from a run that keeps no texts
    """
    group = _group([[_row()], [_row()]])
    head = fixtures.header()
    committed = grouping.RunGroup(
        key=group.key,
        runs=(),
        items=tuple(
            tuple(fixtures.Text(id="de-01").item(head) for _ in items) for items in group.items
        ),
    )

    with pytest.raises(ValueError, match="keeps no texts"):
        passes.pass_rows([committed])


@pytest.mark.use_case("Model_Eval_Report")
def test_the_time_of_the_passes_is_one_pass_plus_the_repeated_first_step() -> None:
    """
    Given  a row of three passes whose first step took half of the exchange's waiting time
    When   the time of the passes is asked for
    Then   it is twice one pass, because the second and third pass repeat only that half
    """
    row = passes.PassRow(
        model="gemma4:e4b",
        temperature=0.05,
        mode="correct-only",
        repeats=3,
        single=quality.EditScore(1, 0, 0),
        vote_two=quality.EditScore(1, 0, 0),
        all_repeats=quality.EditScore(1, 0, 0),
        flagged_share=None,
        flagged_false_alarms=None,
        flagged_true_fixes=None,
        precision_by_votes=(),
        step_one_share=0.5,
    )

    assert row.passes_time == pytest.approx(2.0)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_run_without_step_tags_shows_no_time_for_the_passes() -> None:
    """
    Given  a group whose rows carry no performance figures
    When   the passes are built
    Then   the time of the passes is a dash, because the share of the first step is unknown
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.step_one_share is None
    assert row.passes_time is None


@pytest.mark.use_case("Model_Eval_Report")
def test_the_table_prints_one_row_per_configuration_with_the_votes_and_the_time() -> None:
    """
    Given  the same three passes
    When   the passes are rendered
    Then   the row carries the single pass, the vote, the flag and the precision by votes
    """
    text = passes.passes_table(passes.pass_rows([_three_passes()]))

    assert "| `gemma4:e4b` | 0.05 | correct-only | 1.00 (1.00/1.00) | 1.00 " in text
    assert "| 50% | 100% | 40% | 0.00 · 1.00 · 1.00 | — |" in text


@pytest.mark.use_case("Model_Eval_Report")
def test_a_repeat_that_lacks_a_text_is_refused_instead_of_misaligned() -> None:
    """
    Given  three repeats, the second of which stopped before the second text
    When   the passes are built
    Then   the build is refused with a reason, not an IndexError and not a shifted vote
    """
    both = [_row(item="de-01"), _row(item="de-02")]
    group = _group([both, [_row(item="de-01")], both])

    with pytest.raises(ValueError, match="repeat 2"):
        passes.pass_rows([group])


def _exchange_group(*, correct_s: float, other_s: float) -> grouping.RunGroup:
    """Return three exchange passes whose requests took *correct_s* in step ① and *other_s* after."""

    def timed(output: str) -> results.ResultRow:
        requests = (
            performance.RequestStats(
                "gemma4:e4b", 1, 1, 0, 0, 0, 0, correct_s, "stop", step="correct"
            ),
            performance.RequestStats(
                "gemma4:e4b", 1, 1, 0, 0, 0, 0, other_s, "stop", step="explain"
            ),
        )
        row = _row(output=output)
        return results.ResultRow(
            **{
                **row.__dict__,
                "mode": answer_mode.AnswerMode.EXCHANGE,
                "performance": performance.Performance(requests=requests),
            }
        )

    group = _three_passes()
    key = grouping.GroupKey(**{**group.key.__dict__, "mode": answer_mode.AnswerMode.EXCHANGE})
    items = tuple((timed(row.output),) for row in (items[0] for items in group.items))
    return grouping.RunGroup(key=key, runs=(), items=items)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_correct_only_row_is_priced_with_its_models_exchange_share() -> None:
    """
    Given  correct-only passes of a model and its exchange passes, step ① taking a quarter of them
    When   the passes are built from both
    Then   the correct-only row costs 1 + 2 x 0.25 = 1.5 against one pass, not the 3x of step ① alone
    """
    rows = passes.pass_rows([_three_passes(), _exchange_group(correct_s=1.0, other_s=3.0)])

    correct_only = next(row for row in rows if row.mode == "correct-only")
    assert correct_only.step_one_share == pytest.approx(0.25)
    assert correct_only.passes_time == pytest.approx(1.5)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_correct_only_row_without_exchange_rows_shows_no_cost() -> None:
    """
    Given  correct-only passes read without any exchange folder
    When   the passes are built
    Then   the cost is unknown, because step ① is the whole of a correct-only run and says nothing
    """
    row = passes.pass_rows([_three_passes()])[0]

    assert row.passes_time is None
