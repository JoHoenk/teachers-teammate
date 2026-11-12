"""Tests of ``INDEX.md``: one row per run, computed from what the run file holds."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.tools.store_workspace import (
    COMMITTED,
    MACHINE,
    MODEL,
    export,
    one,
    workspace,
)
from tools.model_eval import index, layout, protocol, run_format, testset


def test_the_index_lists_every_run_with_its_checksum(tmp_path):
    """
    Given  two exported runs
    When   the index is read
    Then   it lists each run with the SHA-256 of its file and is the one that is generated
    """
    space = workspace(tmp_path)
    first = one(space)
    second = one(space, label="second")
    text = (space.out / layout.INDEX_NAME).read_text(encoding="utf-8")
    for run in (first, second):
        assert run.run_id in text
        assert testset.file_sha256(run.path) in text
    assert text == index.index_markdown(space.out)


def test_the_index_reflects_a_run_added_without_it(tmp_path):
    """
    Given  an index and a run file added by hand afterwards
    When   the index is generated again
    Then   it differs and names the added run
    """
    space = workspace(tmp_path)
    export(space)
    text = (space.out / layout.INDEX_NAME).read_text(encoding="utf-8")
    first = layout.run_files(space.out)[0]
    run = run_format.read_run(first)
    spare = first.with_name("2026-11-10_added-by-hand.jsonl")
    run_format.write_run(
        spare,
        {**run.header, "run_id": spare.stem, "date": "2026-11-10T09:00:00"},
        run.records,
    )
    assert index.index_markdown(space.out) != text
    assert spare.stem in index.index_markdown(space.out)


def test_the_index_reports_the_mean_f_beta_and_the_seconds(tmp_path):
    """
    Given  an exported run
    When   it is summarised for the index
    Then   the summary has the mean F-beta, the texts, the model, the machine, the short digest and
           the file hash
    """
    written = one(workspace(tmp_path))
    summary = index.summarise_run(written.path)
    assert summary.mean_f_beta is not None
    assert summary.texts == 7
    assert summary.model == MODEL
    assert summary.machine == MACHINE
    assert summary.digest == "01234567"
    assert summary.sha256 == testset.file_sha256(written.path)


def test_a_run_that_scored_nothing_has_no_mean_f_beta(tmp_path):
    """
    Given  an exported run whose items all scored nothing, so every language has no F-beta
    When   it is summarised for the index
    Then   the mean is None, so the index shows a dash instead of a zero that would drag a mean
           down
    """
    space = workspace(tmp_path)
    written = one(space)
    run = run_format.read_run(written.path)
    items = [
        {**item, "scores": {**item["scores"], "tp": 0, "fp": 0, "fn": 0}} for item in run.records
    ]
    run_format.write_run(written.path, run.header, items)

    assert index.summarise_run(written.path).mean_f_beta is None


def test_the_index_shows_the_eval_version_instead_of_the_state_of_the_tree(tmp_path):
    """
    Given  an exported run
    When   the index is read
    Then   its column names the protocol version the run measured, and no column marks a dirty
           tree any more
    """
    written = one(workspace(tmp_path))
    text = (written.path.parents[2] / layout.INDEX_NAME).read_text(encoding="utf-8")
    assert "| Eval |" in text
    assert "Dirty" not in text
    version = protocol.current_stamp().eval_version
    assert f"| {version} |" in text
    assert index.summarise_run(written.path).eval_version == version


def test_the_committed_index_is_the_one_the_files_give():
    """
    Given  the committed index and the committed run files
    When   the index is generated again from the files
    Then   it is the same text, so a run added or changed behind the index fails here
    """
    if not COMMITTED.is_dir() or not layout.run_files(COMMITTED):
        pytest.skip("no committed run yet")
    path = COMMITTED / layout.INDEX_NAME
    assert path.exists(), "runs are committed but the index is missing"
    assert path.read_text(encoding="utf-8") == index.index_markdown(COMMITTED)


def test_a_run_whose_lines_the_manifest_does_not_match_shows_dashes(tmp_path):
    """
    Given  an exported run in which one line names an item the manifest does not have
    When   the index row of the run is built
    Then   the row shows a dash for the mean and the speed instead of stopping the index
    """
    space = workspace(tmp_path)
    written = one(space)
    run = run_format.read_run(written.path)
    broken = [{**run.records[0], "item": "xx-99"}, *run.records[1:]]
    run_format.write_run(written.path, run.header, broken)

    row = index.summarise_run(written.path)

    assert row.mean_f_beta is None
    assert row.seconds_per_100_words is None
    assert row.texts == len(broken)


def test_the_index_mean_leaves_french_out_like_the_report(tmp_path, monkeypatch):
    """
    Given  a run whose German and English texts score 1.0 and whose French text scores 0
    When   it is summarised for the index
    Then   the mean is 1.0, the one over the deciding languages, which French does not join
    """
    written = one(workspace(tmp_path))

    def item(language: str, hits: int) -> SimpleNamespace:
        return SimpleNamespace(
            kind="errors",
            language=language,
            true_positives=hits,
            false_positives=0,
            false_negatives=1 - hits,
            words=10,
            seconds=1.0,
        )

    monkeypatch.setattr(
        index, "_typed", lambda run, manifest: (item("de", 1), item("en", 1), item("fr", 0))
    )

    assert index.summarise_run(written.path).mean_f_beta == 1.0
