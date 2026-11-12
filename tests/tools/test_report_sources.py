"""Tests that the report is the same from a local cache and from a committed run.

``report`` prints the same table from the result rows of a run and from the
exported file of that run.  The two sources are different types (:class:`~.results.ResultRow` and
:class:`~.run_format.StoredItem`); both satisfy :class:`~.scoring.ScoredItem`, which is what lets
one renderer serve both.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.infrastructure import correction
from tests.tools import invented_corpus
from tests.tools.store_workspace import MODEL, one, workspace
from tools.model_eval import report, results, run_format, testset
from tools.model_eval.performance import LoadedModel, Performance, RequestStats
from tools.model_eval.scoring import ScoredItem


class _Measured:
    """Measures every text the same way, as :mod:`.measure` does in a real run."""

    def begin(self) -> None:
        """Note the state before the text; nothing to note here."""

    def end(self) -> Performance:
        """Return what one text cost: one request, a fixed memory peak, nothing shared."""
        return Performance(
            requests=(
                RequestStats(
                    model=MODEL,
                    prompt_tokens=400,
                    answer_tokens=120,
                    load_s=0.0,
                    prompt_eval_s=0.2,
                    generation_s=1.5,
                    total_s=1.7,
                    wall_s=1.8,
                ),
            ),
            loaded=(
                LoadedModel(name=MODEL, size_bytes=4_000_000_000, size_vram_bytes=3_000_000_000),
            ),
            vram_peak_mb=4200,
            vram_baseline_mb=700,
        )


def _space(tmp_path: Path):
    """Return a workspace whose run adds marks on one text, falls back on another, and is timed."""
    space = workspace(
        tmp_path,
        overrides={"de-01": lambda perfect: f"**{perfect}**"},
        instrumentation=_Measured(),
    )
    rows_path = space.cache / "run.jsonl"
    lines = rows_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first.update(
        fallback=True,
        has_review=False,
        warning=correction.FALLBACK_WARNING,
    )
    lines[0] = json.dumps(first, ensure_ascii=False)
    rows_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return space


def _local_rows(space) -> list[results.ResultRow]:
    """Return the local rows, re-scored as ``proofreading_store report --cache`` does."""
    rows = results.read_rows(space.cache / "run.jsonl")
    return [results.rescore(row, count_marks=False) for row in rows]


def _stored_items(space) -> list[run_format.StoredItem]:
    """Return the typed items of the workspace's exported run."""
    written = one(space)
    run = run_format.read_run(written.path)
    return list(run.items(testset.read_manifest(space.manifest)))


def _scored(item: ScoredItem) -> str:
    """Return what a report reads from one item; ``ty`` fails the lint if a type drifts."""
    return f"{item.model}/{item.language}/{item.item}/{item.kind}/{item.true_positives}"


@pytest.mark.use_case("Model_Eval_Report")
def test_the_table_is_the_same_from_a_local_cache_and_from_a_run_file(tmp_path: Path) -> None:
    """
    Given  a run whose texts add marks, fall back and are timed
    When   the results table is built from the local rows and from the exported run's items
    Then   the two tables are the same string, so the repository can be reported on
    """
    space = _space(tmp_path)

    local = report.table(report.summarise(_local_rows(space)))
    stored = report.table(report.summarise(_stored_items(space)))

    assert f"| `{MODEL}` |" in local, local
    assert local == stored


@pytest.mark.use_case("Model_Eval_Report")
def test_the_band_table_is_the_same_from_both_sources(tmp_path: Path) -> None:
    """
    Given  a run whose texts were drawn for bands
    When   the band table is built from the local rows and from the exported run's items
    Then   the two tables are the same string, bands and numbers included
    """
    bands = {"de-01": "short", "de-02": "page", "en-01": "pages"}
    pointers = invented_corpus.item_pointers()
    for pointer in pointers:
        if pointer["id"] in bands:
            pointer["band"] = bands[pointer["id"]]
    space = workspace(tmp_path, pointers=pointers)

    local = report.band_table(report.summarise(_local_rows(space)))
    stored = report.band_table(report.summarise(_stored_items(space)))

    assert "| short |" in local, local
    assert local == stored


@pytest.mark.use_case("Model_Eval_Report")
def test_the_performance_table_is_the_same_from_both_sources(tmp_path: Path) -> None:
    """
    Given  a run whose every text carries a performance record
    When   the performance table is built from the local rows and from the exported items
    Then   the two tables are the same string, so a speed figure means the same in both
    """
    space = _space(tmp_path)

    local = report.performance_table(report.summarise_performance(_local_rows(space)))
    stored = report.performance_table(report.summarise_performance(_stored_items(space)))

    assert f"| `{MODEL}` |" in local, local
    assert local == stored


@pytest.mark.use_case("Model_Eval_Report")
def test_an_item_of_a_language_without_a_name_is_reported_by_its_code() -> None:
    """
    Given  a result row in a language the tool has no name for
    When   the results table is built
    Then   the row is in it, printed by its code, instead of being left out without a word
    """
    row = results.ResultRow(
        id="xx/01",
        language="xx",
        kind="errors",
        origin="invented",
        model=MODEL,
        temperature=0.2,
        seconds=1.0,
        failed=False,
        fallback=False,
        has_review=False,
        source="Der Hund laufen schnell.",
        reference="Der Hund läuft schnell.",
        output="Der Hund läuft schnell.",
    )

    printed = report.table(report.summarise([results.rescore(row, count_marks=False)]))

    assert "| xx |" in printed, printed


@pytest.mark.use_case("Model_Eval_Report")
def test_both_sources_satisfy_the_protocol_a_report_needs(tmp_path: Path) -> None:
    """
    Given  a local row and a stored item of the same run
    When   they are handed to a function that takes a scored item
    Then   both are accepted, and both say they are one, so neither can drift unnoticed
    """
    space = _space(tmp_path)
    row = _local_rows(space)[0]
    item = _stored_items(space)[0]

    assert isinstance(row, ScoredItem)
    assert isinstance(item, ScoredItem)
    assert _scored(row) == _scored(item)
