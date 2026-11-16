"""Tests of the sweep table: one row per temperature, the difference against a reference, the best.

A sweep is one model asked at several temperatures, and the question is which of them to keep.  The
tests move one number of one temperature and read the row, the difference and the choice back.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import grouping, sweep


def _texts(*, tp: int = 90, fp: int = 10, fn: int = 10) -> list[fixtures.Text]:
    """Return five page texts per deciding language with the given counts."""
    return [
        *fixtures.page_texts("de", tp=tp, fp=fp, fn=fn),
        *fixtures.page_texts("en", tp=tp, fp=fp, fn=fn),
    ]


def _group(
    temperature: float, *, tp: int = 90, fp: int = 10, fn: int = 10, **fields: Any
) -> grouping.RunGroup:
    """Return a group of one model at *temperature* over texts with the given counts."""
    return fixtures.group(*_texts(tp=tp, fp=fp, fn=fn), temperature=temperature, **fields)


def _row(rows: Sequence[sweep.SweepRow], temperature: float) -> sweep.SweepRow:
    """Return the row of one temperature."""
    return next(row for row in rows if row.temperature == temperature)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_row_is_the_mean_of_the_deciding_languages_over_the_repeats() -> None:
    """
    Given  one model at two temperatures, the same numbers at both
    When   the sweep is built
    Then   each row carries the mean F0.5 of German and English, and the same figures at both
    """
    rows = sweep.sweep_rows([_group(0.05), _group(0.7)])

    assert [row.temperature for row in rows] == [0.05, 0.7]
    assert rows[0].f_beta == pytest.approx(rows[1].f_beta)
    assert rows[0].de == pytest.approx(0.9)
    assert rows[0].en == pytest.approx(0.9)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_first_row_of_a_model_is_the_reference_when_none_is_named() -> None:
    """
    Given  one model at two temperatures, the second worse than the first
    When   the sweep is built without a reference
    Then   the first row has no difference and the second is measured against it
    """
    rows = sweep.sweep_rows([_group(0.05), _group(0.7, tp=60, fp=40)])

    assert rows[0].delta is None
    assert rows[1].delta is not None
    assert rows[1].delta.delta < 0


@pytest.mark.use_case("Model_Eval_Report")
def test_a_named_reference_measures_every_row_against_it() -> None:
    """
    Given  one model at three temperatures and the middle one named as the reference
    When   the sweep is built
    Then   the reference row has no difference and both others are measured against it
    """
    groups = [_group(0.05), _group(0.4), _group(0.7, tp=60, fp=40)]

    rows = sweep.sweep_rows(groups, groups[1])

    assert _row(rows, 0.4).delta is None
    assert _row(rows, 0.05).delta is not None
    assert _row(rows, 0.7).delta is not None
    assert _row(rows, 0.7).delta.delta < 0


@pytest.mark.use_case("Model_Eval_Report")
def test_the_best_row_is_the_highest_score_and_a_near_row_is_a_tie() -> None:
    """
    Given  one model at three temperatures, one clearly better and one a hair behind
    When   the best row and its ties are asked for
    Then   the best is the highest score, and the hair's breadth behind is a tie with it
    """
    rows = sweep.sweep_rows([_group(0.05, tp=80, fp=20), _group(0.4), _group(0.7, tp=89, fp=11)])

    best, ties = sweep.best_and_ties(rows)

    assert best is not None
    assert best.temperature == 0.4
    assert [row.temperature for row in ties] == [0.7]


@pytest.mark.use_case("Model_Eval_Report")
def test_a_row_that_is_clearly_worse_is_no_tie() -> None:
    """
    Given  one model at two temperatures, the second far behind
    When   the best row and its ties are asked for
    Then   the second is not a tie, because the difference is beyond the noise
    """
    rows = sweep.sweep_rows([_group(0.05), _group(0.7, tp=40, fp=60)])

    best, ties = sweep.best_and_ties(rows)

    assert best is not None
    assert best.temperature == 0.05
    assert ties == []


@pytest.mark.use_case("Model_Eval_Report")
def test_the_profile_leaves_out_the_temperature_it_has_a_column_for() -> None:
    """
    Given  a group whose request profile carries the temperature next to the other sampling values
    When   its row is built
    Then   the profile names the other values only, because the temperature is printed on its own
    """
    rows = sweep.sweep_rows([_group(0.05)])

    assert rows[0].profile == "compact_json False"


@pytest.mark.use_case("Model_Eval_Report")
def test_the_table_prints_one_table_per_model_with_the_per_page_figures_and_the_best() -> None:
    """
    Given  two models at two temperatures each
    When   the sweep is rendered
    Then   each model has its own heading, the per-page columns are filled, and the best row of
           each model is named below its table
    """
    rows = sweep.sweep_rows(
        [
            _group(0.05),
            _group(0.7, tp=60, fp=40),
            _group(0.05, model=fixtures.OTHER),
            _group(0.7, model=fixtures.OTHER),
        ]
    )

    text = sweep.sweep_table(rows, reference="gemma4:e4b@0.05")

    assert text.count("| Fixed/page |") == 2
    assert f"## `{fixtures.MODEL}`" in text
    assert f"## `{fixtures.OTHER}`" in text
    assert "Best: 0.05 (compact_json False) at F0.5 0.90." in text
    assert "The difference is measured against gemma4:e4b@0.05." in text


@pytest.mark.use_case("Model_Eval_Report")
def test_a_row_measured_on_another_machine_has_no_difference() -> None:
    """
    Given  two temperatures of one model, measured on two machines
    When   the sweep is built and printed
    Then   the second row has no interval and says it is not comparable, instead of a number
    """
    groups = [_group(0.05), _group(0.7, machine="another-machine")]

    rows = sweep.sweep_rows(groups)

    assert rows[1].delta is None
    assert rows[1].comparable is False
    assert "not comparable" in sweep.sweep_table(rows)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_reference_that_lacks_a_text_does_not_crash_the_sweep() -> None:
    """
    Given  a reference group that holds one text fewer than the other group
    When   the sweep is built
    Then   the row is not comparable, and no text is looked up in a group that lacks it
    """
    short = fixtures.group(*_texts()[1:], temperature=0.05)
    full = _group(0.7)

    rows = sweep.sweep_rows([short, full])

    assert rows[1].comparable is False


@pytest.mark.use_case("Model_Eval_Report")
def test_rows_in_two_answer_modes_are_not_compared_unless_corrections_are() -> None:
    """
    Given  one model at two temperatures, the first in exchange mode and the second correct-only
    When   the sweep is built without and with the corrections flag
    Then   without it the second row is not comparable; with it, it gets a difference
    """
    groups = [_group(0.05, mode="exchange"), _group(0.7, mode="correct-only")]

    assert sweep.sweep_rows(groups)[1].comparable is False
    assert sweep.sweep_rows(groups, corrections=True)[1].comparable is True
