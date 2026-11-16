"""Tests of the figures a report is made of: per page, found the spot, and the mean over texts.

The three are the numbers a teacher reads: how many mistakes a model fixes, gets wrong and misses
on a page of 300 words; how often it at least found the spot, next to how often it fixed it the
way the reference did; and the same score with every text weighted alike instead of every word.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import report, summary


def _one(texts: Sequence[fixtures.Text]) -> tuple[summary.ModelSummary, ...]:
    """Return the summaries of one repeat over *texts*."""
    return fixtures.group_of([list(texts)]).summaries()[0]


def _row(rows: Sequence[summary.ModelSummary], language: str = "de") -> summary.ModelSummary:
    """Return the row over all texts of *language*."""
    return next(row for row in rows if not row.band and row.language == language)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_per_page_figures_are_the_pooled_counts_of_one_page() -> None:
    """
    Given  one text of 300 words with 30 fixes, 10 wrong changes and 20 misses
    When   its summary is built
    Then   the per-page figures are those counts, because the text is exactly one page long
    """
    row = _row(_one([fixtures.Text(id="de-01", words=300, tp=30, fp=10, fn=20)]))

    assert row.fixed_per_page == pytest.approx(30.0)
    assert row.wrong_per_page == pytest.approx(10.0)
    assert row.missed_per_page == pytest.approx(20.0)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_per_page_figures_scale_with_the_length_of_the_texts() -> None:
    """
    Given  one text of 600 words with the same counts as a page of 300
    When   its summary is built
    Then   every per-page figure is half of the count, because the text is two pages long
    """
    row = _row(_one([fixtures.Text(id="de-01", words=600, tp=30, fp=10, fn=20)]))

    assert row.fixed_per_page == pytest.approx(15.0)
    assert row.wrong_per_page == pytest.approx(5.0)
    assert row.missed_per_page == pytest.approx(10.0)


@pytest.mark.use_case("Model_Eval_Report")
def test_found_the_spot_is_the_second_recall_and_the_gap_is_what_was_fixed_differently() -> None:
    """
    Given  a text whose reference makes 10 changes, of which the model made 6 exactly and
           touched 8
    When   its summary is built
    Then   found the spot is 0.80, the exact recall 0.60, and the gap between them 0.20
    """
    row = _row(_one([fixtures.Text(id="de-01", tp=6, fp=0, fn=4, located=8, ref_edits=10)]))

    assert row.recall == pytest.approx(0.6)
    assert row.recall_located == pytest.approx(0.8)
    assert row.noticed_gap == pytest.approx(0.2)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_run_written_before_the_located_count_was_stored_shows_no_second_recall() -> None:
    """
    Given  a text whose stored numbers carry no located count
    When   its summary is built
    Then   found the spot is a dash, because the run was not measured for it
    """
    row = _row(_one([fixtures.Text(id="de-01", located=0, ref_edits=0)]))

    assert row.recall_located is None
    assert row.noticed_gap is None


@pytest.mark.use_case("Model_Eval_Report")
def test_the_mean_over_texts_weights_a_short_text_as_much_as_a_long_one() -> None:
    """
    Given  a long text the model nearly gets right and a short one it gets wrong
    When   its summary is built
    Then   the pooled F0.5 is high, because the long text carries it, and the mean over texts is
           much lower, because the short text counts as much as the long one
    """
    row = _row(
        _one(
            [
                fixtures.Text(id="de-01", words=1000, tp=90, fp=10, fn=10),
                fixtures.Text(id="de-02", words=100, tp=1, fp=9, fn=9),
            ]
        )
    )

    assert row.f_beta == pytest.approx(0.827, abs=0.001)
    assert row.mean_text_f_beta == pytest.approx(0.5, abs=0.001)
    assert row.mean_text_precision == pytest.approx(0.5, abs=0.001)
    assert row.mean_text_recall == pytest.approx(0.5, abs=0.001)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_text_the_model_left_untouched_is_skipped_by_the_mean_precision() -> None:
    """
    Given  one text the model changed and one it left alone
    When   its summary is built
    Then   the mean precision is the changed text's own, because a text without a change has no
           precision to average
    """
    row = _row(
        _one(
            [
                fixtures.Text(id="de-01", tp=9, fp=1, fn=1),
                fixtures.Text(id="de-02", tp=0, fp=0, fn=0),
            ]
        )
    )

    assert row.mean_text_precision == pytest.approx(0.9)
    assert row.mean_text_recall == pytest.approx(0.9)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_table_shows_the_per_page_figures_and_found_the_spot() -> None:
    """
    Given  one text of 300 words with 30 fixes, 10 wrong changes, 20 misses and 8 of 10 changes
           located
    When   the results table is rendered
    Then   the row carries the three per-page figures and the second recall with its gap
    """
    rows = _one(
        [fixtures.Text(id="de-01", words=300, tp=30, fp=10, fn=20, located=38, ref_edits=50)]
    )

    line = next(line for line in report.table(rows).splitlines() if line.startswith("| `"))

    assert "| 0.60 | 0.76 (+0.16) |" in line
    assert "| 30.0 | 10.0 | 20.0 |" in line


@pytest.mark.use_case("Model_Eval_Report")
def test_the_gap_never_goes_negative_when_older_rows_are_pooled_with_newer_ones() -> None:
    """
    Given  a language of two texts: one with the located count, one written before it existed
    When   its summary is built
    Then   the gap counts the exact fixes of the first text only, so it is 0.20 and not negative
    """
    newer = fixtures.Text(id="de-01", tp=6, fp=0, fn=4, located=8, ref_edits=10)
    older = fixtures.Text(id="de-02", tp=8, fn=2, stores_located=False)

    row = _row(_one([newer, older]))

    assert row.noticed_gap == pytest.approx(0.2)
