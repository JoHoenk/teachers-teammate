"""Tests of the test-set builder: the manifest it writes and the checks it runs on it."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

from tests.tools import invented_corpus
from tools import prepare_gec_testset
from tools.model_eval import measure, testset


def _args(manifest: Path, *, built: str = "2026-12-01") -> Namespace:
    """Return the arguments ``_write_manifest`` reads: where to write, which set, which day."""
    return Namespace(manifest=manifest, testset="test-2026-12", built=built)


def test_the_build_date_is_taken_as_given(tmp_path):
    """
    Given  a build with --built 2026-12-01
    When   the manifest is written
    Then   it records that day, so two builds of one day are byte-identical
    """
    corpora = invented_corpus.build_corpus(tmp_path)
    path = tmp_path / "manifest.json"

    code = prepare_gec_testset._write_manifest(
        _args(path), invented_corpus.item_pointers(), corpora, seed=20261107
    )

    assert code == 0
    assert testset.read_manifest(path)["built"] == "2026-12-01"


def test_a_build_with_a_changed_corpus_file_exits_1(tmp_path, capsys, monkeypatch):
    """
    Given  a corpus file that changes after the items were chosen and the manifest written
    When   the build checks its own manifest
    Then   it exits 1 and prints the file whose hash no longer matches
    """
    corpora = invented_corpus.build_corpus(tmp_path)
    written = testset.write_manifest

    def write_then_change(path, manifest):
        written(path, manifest)
        (corpora.root("falko-merlin") / "fm-test.m2").write_text(
            "S Etwas anderes .\n", encoding="utf-8"
        )

    monkeypatch.setattr(prepare_gec_testset.testset, "write_manifest", write_then_change)

    code = prepare_gec_testset._write_manifest(
        _args(tmp_path / "manifest.json"),
        invented_corpus.item_pointers(),
        corpora,
        seed=20261107,
    )

    captured = capsys.readouterr()
    assert code == 1
    assert "fm-test.m2" in captured.out


def test_a_build_from_a_dirty_tree_warns_but_writes(tmp_path, capsys, monkeypatch):
    """
    Given  a tree with uncommitted changes, which the revision records as +dirty
    When   the manifest is written
    Then   it is written, the warning goes to the error stream, and the exit code stays 0
    """
    corpora = invented_corpus.build_corpus(tmp_path)
    path = tmp_path / "manifest.json"
    monkeypatch.setattr(measure, "tool_revision", lambda: "abc1234+dirty")

    code = prepare_gec_testset._write_manifest(
        _args(path), invented_corpus.item_pointers(), corpora, seed=20261107
    )

    captured = capsys.readouterr()
    assert code == 0
    assert path.exists()
    assert testset.read_manifest(path)["builder_revision"] == "abc1234+dirty"
    assert "built from a dirty tree" in captured.err


def test_an_unknown_test_set_is_refused_with_the_known_ones(tmp_path, capsys, monkeypatch):
    """
    Given  a --testset no recipe exists for
    When   the builder runs
    Then   it exits with 2 and names the test sets that exist
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare_gec_testset", "--testset", "gec-1999-01", "--out", str(tmp_path / "set")],
    )

    assert prepare_gec_testset.main() == 2

    assert "no test set 'gec-1999-01'; known: gec-2026-12" in capsys.readouterr().err


def test_a_recipe_whose_corpus_has_no_path_is_refused_with_the_flag_to_add(
    tmp_path, capsys, monkeypatch
):
    """
    Given  a recipe whose corpora were not named on the command line
    When   the builder runs
    Then   it exits with 2 and names the --corpus options to add
    """
    monkeypatch.setattr(
        sys,
        "argv",
        ["prepare_gec_testset", "--testset", "gec-2026-12", "--out", str(tmp_path / "set")],
    )

    assert prepare_gec_testset.main() == 2

    err = capsys.readouterr().err
    assert "--corpus falko-merlin=<the extracted Falko-MERLIN data/ directory>" in err
    assert "--corpus wi=<the extracted wi+locness/ directory>" in err


def test_a_corpus_path_that_does_not_exist_is_refused_not_a_traceback(
    tmp_path, capsys, monkeypatch
):
    """
    Given  every corpus of the recipe named, but with paths that do not exist
    When   the builder runs
    Then   it exits with 2 and a "refused:" line
    """
    argv = ["prepare_gec_testset", "--testset", "gec-2026-12", "--out", str(tmp_path / "set")]
    for name in ("falko-merlin", "wi"):
        argv += ["--corpus", f"{name}={tmp_path / 'nowhere'}"]
    monkeypatch.setattr(sys, "argv", argv)

    assert prepare_gec_testset.main() == 2
    assert "refused:" in capsys.readouterr().err


def test_a_language_file_of_an_earlier_build_is_removed(tmp_path):
    """
    Given  an output directory holding fr.jsonl, notes.jsonl and de.jsonl from earlier
    When   a build that writes only de is cleaned up
    Then   fr.jsonl is removed; de.jsonl and notes.jsonl, which is no language, stay
    """
    for name in ("fr.jsonl", "notes.jsonl", "de.jsonl"):
        (tmp_path / name).write_text("")

    prepare_gec_testset._remove_stale(tmp_path, keep={"de"})

    assert sorted(path.name for path in tmp_path.iterdir()) == ["de.jsonl", "notes.jsonl"]
