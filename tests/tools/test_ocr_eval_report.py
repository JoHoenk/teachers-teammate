"""The report: one row per run and group, each compared page for page with a baseline."""

from __future__ import annotations

import pytest

from tools.ocr_eval import report, records, runs


def _page(
    page: str = "s01",
    *,
    cer: float = 0.1,
    group: str = "handwritten_scan",
    kept: float | None = 0.5,
    flags: tuple[str, ...] = (),
    characters: int = 50,
) -> records.PageNumbers:
    """Return one page read at *cer*."""
    return records.PageNumbers(
        page=page,
        group=group,
        split="tune",
        cer=cer,
        wer=cer,
        bag_wer=cer,
        kept=kept,
        words=10,
        characters=characters,
        writer="w1",
        flags=flags,
        seconds=1.0,
    )


def _stored(model: str, *pages: records.PageNumbers) -> runs.Stored:
    """Return a stored run of *pages*, one page at a tenth wrong when none is given."""
    record = records.RunRecord(
        header=records.Header(
            model=model,
            engine="ollama",
            prompt="app",
            preset="R",
            memory_mb=6144,
        ),
        pages=pages or (_page(),),
    )
    return runs.Stored(runs.run_name(record.header), record)


def _column(text: str, name: str) -> list[str]:
    """Return the cells of the column *name* of a report's table, one per row."""
    header, _rule, *rows = text.splitlines()
    index = [cell.strip() for cell in header.split("|")].index(name)
    return [row.split("|")[index].strip() for row in rows]


def test_a_run_that_reads_better_than_the_baseline_is_a_gain() -> None:
    """Given a baseline at a tenth wrong and a run at a twentieth
    When   the two are paired
    Then   the difference is positive, which is better for a character error rate
    """
    interval = report.paired({"s01": 0.05}, {"s01": 0.1}, {"s01": "w1"})

    assert interval is not None
    assert interval.delta == pytest.approx(0.05)
    assert interval.low >= 0


def test_a_kept_mistakes_gain_keeps_its_sign() -> None:
    """Given a run that keeps half the mistakes the baseline keeps
    When   the kept shares are paired, where higher is better
    Then   the difference is positive too, so both columns are read the same way
    """
    interval = report.paired({"s01": 0.9}, {"s01": 0.4}, {"s01": "w1"}, higher_is_better=True)

    assert interval is not None
    assert interval.delta == pytest.approx(0.5)


def test_an_interval_that_holds_zero_is_no_difference() -> None:
    """Given one page read the same by both runs
    When   the two are paired
    Then   the interval is zero on both ends
    """
    interval = report.paired({"s01": 0.1}, {"s01": 0.1}, {"s01": "w1"})

    assert interval is not None
    assert (interval.delta, interval.low, interval.high) == (0.0, 0.0, 0.0)


def test_runs_of_different_pages_are_not_compared() -> None:
    """Given two runs that share no page
    When   they are paired
    Then   there is no interval, because two pages are not the same pages
    """
    assert report.paired({"s01": 0.1}, {"s02": 0.2}, {"s01": "w1", "s02": "w1"}) is None


def test_the_draws_take_writers_and_not_pages() -> None:
    """Given two writers whose pages moved apart, one better and one worse
    When   the difference is drawn once over the writers and once over the pages
    Then   the writers give the wider interval: ten pages of one hand are one piece of evidence
    """
    better = tuple(f"w1s{index}" for index in range(1, 6))
    worse = tuple(f"w2s{index}" for index in range(1, 6))
    baseline = {**dict.fromkeys(better, 0.1), **dict.fromkeys(worse, 0.1)}
    run = {**dict.fromkeys(better, 0.9), **dict.fromkeys(worse, 0.2)}
    writers = {**dict.fromkeys(better, "w1"), **dict.fromkeys(worse, "w2")}
    per_page: dict[str, str] = {page: page for page in run}

    over_writers = report.paired(run, baseline, writers)
    over_pages = report.paired(run, baseline, per_page)

    assert over_writers is not None
    assert over_pages is not None
    assert over_writers.high - over_writers.low > over_pages.high - over_pages.low


def test_the_cer_difference_is_the_difference_of_the_cer_columns() -> None:
    """Given a short page that got much better and a long page that got a little worse
    When   the run is reported against the baseline
    Then   the difference is the two CER columns' difference: both are weighted by characters,
           so a page-by-page mean cannot make a loss look like a gain
    """
    run = _stored(
        "qwen3-vl:8b", _page("s01", cer=0.0, characters=10), _page("s02", cer=0.2, characters=990)
    )
    baseline = _stored(
        "chandra-ocr-2", _page("s01", cer=0.5, characters=10), _page("s02", cer=0.1, characters=990)
    )

    text = report.table([run, baseline], baseline=baseline)

    cer = _column(text, "CER")
    change = _column(text, "CER vs baseline")
    assert cer == ["19.8 %", "10.4 %"]
    assert change[0].startswith("-9.4 ")


def test_a_row_carries_its_group_its_kept_mistakes_and_the_baseline_column() -> None:
    """Given a run and a baseline with a baseline chosen
    When   the table is made
    Then   the row holds the group and the kept share, and the difference names its interval
    """
    run = _stored("qwen3-vl:8b", _page(cer=0.05))
    baseline = _stored("chandra-ocr-2", _page(cer=0.1))

    text = report.table([run, baseline], baseline=baseline)

    assert "handwritten_scan" in text
    assert "50.0 %" in text
    assert "+5.0" in text
    assert "qwen3-vl:8b" in text


def test_the_kept_column_is_a_dash_when_no_page_holds_a_misspelt_word() -> None:
    """Given a run and a baseline whose pages hold no braced word
    When   the table is made
    Then   the kept difference is a dash, because there is no share to compare
    """
    run = _stored("qwen3-vl:8b", _page(kept=None))
    baseline = _stored("chandra-ocr-2", _page(kept=None))

    assert _column(report.table([run], baseline=baseline), "Kept vs baseline") == ["—"]


def test_a_row_says_how_many_pages_failed() -> None:
    """Given a run whose page was flagged as cut off
    When   the table is made
    Then   the row counts it, so a good average with a broken page is visible
    """
    text = report.table([_stored("qwen3-vl:8b", _page(flags=("cut_off",)))])

    assert "1 (100%)" in text
