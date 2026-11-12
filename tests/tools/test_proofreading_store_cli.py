"""Tests of the command line of the proofreading store (``tools/proofreading_store.py``)."""

from __future__ import annotations

from argparse import Namespace
from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest

from teachers_teammate.domain.correction_review import ChangeNote
from tests.tools import gate_fixtures as fixtures
from tests.tools import invented_corpus
from tests.tools.store_workspace import (
    MACHINE,
    MODEL,
    TESTSET,
    export,
    one,
    review,
    rewrite,
    workspace,
)
from tools import proofreading_store
from tools.model_eval import export as export_module
from tools.model_eval import layout, protocol, report, results, selection, spot_check, testset
from tools.model_eval.cli_paths import workspace_path


def _corpus_options(corpora) -> list[str]:
    """Return the ``--corpus`` options that name the corpora of *corpora*."""
    return [f"{name}={corpora.root(name)}" for name in corpora.names]


def _args(corpora, out: Path, runs: list[str]) -> Namespace:
    """Return the arguments ``validate`` gets when the corpora are named on the command line."""
    return Namespace(out=str(out), runs=runs, corpus=_corpus_options(corpora), corpora=None)


def test_a_relative_path_is_relative_to_the_repository_under_bazel(tmp_path, monkeypatch):
    """
    Given  the tool started by `bazel run`, which sets BUILD_WORKSPACE_DIRECTORY
    When   a relative path such as the default --out is resolved
    Then   it points into the repository, not into the runfiles directory the tool runs in
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path))

    assert workspace_path("evaluations/proofreading") == (tmp_path / "evaluations" / "proofreading")


def test_an_absolute_path_is_left_as_it_is_under_bazel(tmp_path, monkeypatch):
    """
    Given  the tool started by `bazel run`
    When   an absolute path is resolved
    Then   it is not moved below the repository
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path / "repository"))

    assert workspace_path(str(tmp_path / "cache")) == tmp_path / "cache"


def test_a_relative_path_stays_relative_outside_bazel(monkeypatch):
    """
    Given  the tool started directly, without BUILD_WORKSPACE_DIRECTORY
    When   a relative path is resolved
    Then   it stays relative to the current directory
    """
    monkeypatch.delenv("BUILD_WORKSPACE_DIRECTORY", raising=False)

    assert workspace_path("evaluations/proofreading") == Path("evaluations/proofreading")


def test_strict_validation_reports_an_unreadable_run_and_does_not_crash(tmp_path, capsys):
    """
    Given  a run file with no content and the corpora named on the command line
    When   `validate` runs in strict mode
    Then   the file is refused with a summary and exit code 1, not with a traceback
    """
    corpora, _ = invented_corpus.write_corpus(tmp_path)
    out = tmp_path / "evaluations"
    empty = out / layout.RUNS_DIR / "test-2026-11" / "empty.jsonl"
    empty.parent.mkdir(parents=True)
    empty.write_text("", encoding="utf-8")

    code = proofreading_store._validate(_args(corpora, out, [str(empty)]))

    assert code == 1
    assert "refused empty.jsonl" in capsys.readouterr().err


def test_validate_without_run_files_exits_with_one(tmp_path, capsys):
    """
    Given  a repository directory without run files
    When   `validate` runs
    Then   it says so and exits with 1
    """
    code = proofreading_store._validate(
        Namespace(out=str(tmp_path), runs=[], corpus=[], corpora=None)
    )

    assert code == 1
    assert "no run files" in capsys.readouterr().err


def _manifest_args(corpora, path: Path) -> Namespace:
    """Return the arguments the `manifest` command gets when the corpora are named."""
    return Namespace(manifest=str(path), corpus=_corpus_options(corpora), corpora=None)


def test_the_manifest_of_a_test_set_is_readable_by_the_manifest_command(tmp_path, capsys):
    """
    Given  a clean manifest written for the invented corpus
    When   the `manifest` command prints what it pins and checks it
    Then   it names the test set and the builder revision, and exits with 0
    """
    corpora, pinned = invented_corpus.write_corpus(tmp_path)
    path = tmp_path / "manifest.json"
    testset.write_manifest(path, pinned)

    code = proofreading_store._manifest(_manifest_args(corpora, path))

    captured = capsys.readouterr()
    assert code == 0
    assert "builder abc1234" in captured.out
    assert "the corpora are the ones the manifest pinned" in captured.out


def test_the_manifest_command_prints_the_bands(tmp_path, capsys):
    """
    Given  a manifest whose German item carries a band
    When   the `manifest` command prints what it pins
    Then   it prints one line per language with the item count per band
    """
    corpora, pinned = invented_corpus.write_corpus(tmp_path)
    pinned["items"][0]["band"] = "short"
    path = tmp_path / "manifest.json"
    testset.write_manifest(path, pinned)

    code = proofreading_store._manifest(_manifest_args(corpora, path))

    captured = capsys.readouterr()
    assert code == 0
    assert "  de: short 1" in captured.out


def test_the_manifest_command_reports_a_dirty_build(tmp_path, capsys):
    """
    Given  a manifest whose builder revision ends in +dirty
    When   the `manifest` command checks it
    Then   the dirty tree is a problem and the exit code is 1
    """
    corpora, pinned = invented_corpus.write_corpus(tmp_path)
    pinned["builder_revision"] = "abc1234+dirty"
    path = tmp_path / "manifest.json"
    testset.write_manifest(path, pinned)

    code = proofreading_store._manifest(_manifest_args(corpora, path))

    captured = capsys.readouterr()
    assert code == 1
    assert "built from a dirty tree" in captured.err


def test_the_manifest_command_reports_a_changed_item(tmp_path, capsys):
    """
    Given  a manifest whose item no longer rebuilds to the hash it recorded
    When   the `manifest` command checks it against the corpora
    Then   the item is named as a problem and the exit code is 1
    """
    corpora, pinned = invented_corpus.write_corpus(tmp_path)
    pinned["items"][0]["source"]["sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    testset.write_manifest(path, pinned)

    code = proofreading_store._manifest(_manifest_args(corpora, path))

    captured = capsys.readouterr()
    assert code == 1
    assert f"{pinned['items'][0]['id']}: source has hash" in captured.err


def test_the_report_command_prints_the_table_of_the_committed_runs(tmp_path, capsys):
    """
    Given  an exported run in a repository directory
    When   `report` runs over that directory
    Then   it prints the results table of the run, the one the local report prints, and exits 0
    """
    space = workspace(tmp_path)
    written = one(space)
    rows = [
        results.rescore(row, count_marks=False)
        for row in results.read_rows(space.cache / "run.jsonl")
    ]

    code = proofreading_store._report(Namespace(out=str(space.out), runs=[], performance=False))

    summaries = report.summarise(rows)

    assert code == 0
    bands = report.band_table(summaries)
    expected = f"{report.table(summaries)}\n\n{bands}" if bands else report.table(summaries)
    assert capsys.readouterr().out.strip() == expected
    assert written.path.exists()


def test_the_report_command_prints_the_same_table_from_a_local_cache(tmp_path, capsys):
    """
    Given  an exported run and the local cache it was exported from
    When   `report --cache` runs over the cache and `report` over the repository directory
    Then   both print the same table, so one command answers for either source
    """
    space = workspace(tmp_path)
    one(space)
    committed = proofreading_store._report(
        Namespace(out=str(space.out), runs=[], performance=False)
    )
    from_runs = capsys.readouterr().out

    cached = proofreading_store._report(
        Namespace(
            cache=str(space.cache),
            testset=None,
            by_error=None,
            count_marks=False,
            performance=False,
        )
    )
    from_cache = capsys.readouterr().out

    assert (committed, cached) == (0, 0)
    assert from_cache == from_runs


def test_the_report_by_error_needs_the_test_set(tmp_path, capsys):
    """
    Given  a local cache and no test set
    When   `report --cache --by-error` runs
    Then   it says the labels live in the test set and exits 2
    """
    space = workspace(tmp_path)
    one(space)

    code = proofreading_store._report(
        Namespace(
            cache=str(space.cache),
            testset=None,
            by_error="category",
            count_marks=False,
            performance=False,
        )
    )

    assert code == 2
    assert "--by-error needs --testset" in capsys.readouterr().err


def test_the_report_command_names_a_run_it_refuses(tmp_path, capsys):
    """
    Given  a run file that fails format mode
    When   `report` runs over it
    Then   the file is named on the error stream and the exit code is 1
    """
    space = workspace(tmp_path)
    written = one(space)
    written.path.write_text("", encoding="utf-8")

    code = proofreading_store._report(
        Namespace(out=str(space.out), runs=[str(written.path)], performance=False)
    )

    assert code == 1
    assert f"refused {written.path.name}" in capsys.readouterr().err


def test_the_report_command_refuses_runs_of_different_eval_versions(tmp_path, capsys, monkeypatch):
    """
    Given  two runs of one model in a repository directory, one measured as another version
    When   `report` runs over that directory
    Then   it prints no table, names both runs with their versions on the error stream, and exits 1,
           because scores of different versions are never compared
    """
    space = workspace(tmp_path)
    first, second = export(space)[0], export(space, label="second")[0]
    other = protocol.current_stamp().eval_version + 1
    monkeypatch.setattr(protocol, "is_recorded", lambda version, fingerprint: True)
    rewrite(second.path, lambda header, items: header.update(eval_version=other))

    code = proofreading_store._report(Namespace(out=str(space.out), runs=[], performance=False))

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert f"{first.path.name}: eval v{protocol.current_stamp().eval_version}" in captured.err
    assert f"{second.path.name}: eval v{other}" in captured.err


def test_the_manifest_command_notes_a_different_licence_text_and_exits_0(tmp_path, capsys):
    """
    Given  a manifest whose licence text is an older wording than the adapter's
    When   the `manifest` command prints what it pins
    Then   the difference is a note on stdout and the exit code stays 0, because manifests are
           immutable
    """
    corpora, pinned = invented_corpus.write_corpus(tmp_path)
    pinned["corpora"]["wi"]["licence"] = "an older wording"
    path = tmp_path / "manifest.json"
    testset.write_manifest(path, pinned)

    code = proofreading_store._manifest(_manifest_args(corpora, path))

    captured = capsys.readouterr()
    assert code == 0
    assert "wi: the licence text differs from the adapter's" in captured.out


def test_export_names_the_flag_of_a_corpus_the_manifest_needs(tmp_path, capsys):
    """
    Given  a manifest that names four corpora and a command that names only the German one
    When   the cache is exported
    Then   it is refused with the --corpus option of the corpus that is missing
    """
    space = workspace(tmp_path)

    code = proofreading_store._export(
        Namespace(
            cache=str(space.cache),
            testset=TESTSET,
            manifest=str(space.manifest),
            machine=MACHINE,
            out=str(space.out),
            label=None,
            corpus=[f"falko-merlin={space.corpora.root('falko-merlin')}"],
            corpora=None,
        )
    )

    assert code == 1
    assert "--corpus wi=" in capsys.readouterr().err


def test_a_german_only_manifest_needs_only_the_german_corpus(tmp_path, capsys):
    """
    Given  a run over a manifest that names only the German corpus
    When   `validate` runs in strict mode with only that corpus named
    Then   it passes, because a command needs the corpora its manifest names and no others
    """
    pointers = [item for item in invented_corpus.item_pointers() if item["language"] == "de"]
    space = workspace(tmp_path, pointers=pointers)
    written = one(space)

    code = proofreading_store._validate(
        Namespace(
            out=str(space.out),
            runs=[str(written.path)],
            corpus=[f"falko-merlin={space.corpora.root('falko-merlin')}"],
            corpora=None,
        )
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "strict mode: 1 of 1 run files pass" in captured.out


def test_a_relative_corpus_option_is_relative_to_the_repository_under_bazel(tmp_path, monkeypatch):
    """
    Given  the tool started by `bazel run` and --corpus with a relative path
    When   the corpora are read from the arguments
    Then   the path points into the repository, as the old corpus flags did
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path))
    args = Namespace(corpus=["wi=corpora/bea"], corpora=None)

    found = proofreading_store._corpora(args)

    assert found is not None
    assert found.adapter("wi").root == tmp_path / "corpora" / "bea"


# ── gate 2: the floors, the comparison and the marks ─────────────────────────


OTHER_SET = "test-2026-10"
"""A second test set of the same corpus, for a test that needs two evaluations."""


def _other_testset(space, name: str = OTHER_SET) -> Path:
    """Pin a second test set of the workspace's corpus; return its manifest."""
    path = space.root / f"manifest-{name}.json"
    testset.write_manifest(
        path, invented_corpus.manifest(space.root, space.corpora, testset_id=name)
    )
    return path


def _export(  # noqa: PLR0913  # one keyword per part of a header a test may vary
    space,
    name: str,
    *,
    model: str | None = None,
    digest: str | None = None,
    repeat: int = 1,
    compact_json: bool | None = None,
    label: str | None = None,
    testset_id: str = TESTSET,
    temperature: float | None = None,
    date: str | None = None,
):
    """Export one more run of the workspace's cache, with the header a test describes."""
    cache = space.root / f"results-{name}"
    shutil.copytree(space.cache, cache)
    path = cache / "run.header.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    for entry in entries:
        entry["repeat"] = repeat
        if model is not None:
            entry["model"] = model
        if digest is not None:
            entry["model_info"] = {**entry["model_info"], "digest": digest}
        if compact_json is not None:
            entry["request_profile"] = {**entry["request_profile"], "compact_json": compact_json}
        if temperature is not None:
            entry["temperature"] = temperature
            entry["request_profile"] = {**entry["request_profile"], "temperature": temperature}
        if date is not None:
            entry["date"] = date
    path.write_text(json.dumps(entries), encoding="utf-8")
    return export_module.export(
        cache,
        testset=testset_id,
        manifest=space.manifest if testset_id == TESTSET else _other_testset(space, testset_id),
        corpora=space.corpora,
        machine=MACHINE,
        out=space.out,
        label=label,
    )


def _repeats(space, count: int = 3) -> list:
    """Export *count* repeats of the workspace's cache, one run per repeat."""
    return [_export(space, f"r{repeat}", repeat=repeat)[0] for repeat in range(1, count + 1)]


def _reviews() -> dict:
    """Return a review for every error text of the invented corpus, so every change is explained."""
    return {
        "de-01": review(
            ChangeNote(
                original="laufen",
                corrected="läuft",
                category="grammar",
                explanation="Das Verb muss im Singular stehen.",
            ),
            original="Der Hund laufen schnell.",
            corrected="Der Hund läuft schnell.",
        ),
        "de-02": review(
            ChangeNote(
                original="ein",
                corrected="einen",
                category="grammar",
                explanation="``Hund`` ist männlich, also ``einen``.",
            ),
            original="Ich habe ein Hund.",
            corrected="Ich habe einen Hund.",
        ),
        "en-01": review(
            ChangeNote(
                original="has",
                corrected="have",
                category="grammar",
                explanation="``I`` takes ``have``.",
            ),
            original="I has a dog.",
            corrected="I have a dog.",
        ),
    }


def _gate_args(space, **changes) -> Namespace:
    """Return the arguments ``gate`` gets for the workspace's runs."""
    values = {
        "model": MODEL,
        "mode": "review",
        "temperature": 0.7,
        "machine": None,
        "testset": None,
        "digest": None,
        "compact_json": None,
        "out": str(space.out),
    }
    values.update(changes)
    return Namespace(**values)


def test_the_gate_command_prints_every_check_and_exits_0(tmp_path, capsys):
    """
    Given  three repeats of one model that meets every floor
    When   `gate` runs over the repository directory
    Then   it prints one line per check and the outcome, and exits 0
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)

    code = proofreading_store._gate(_gate_args(space))

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "✓ failed texts, de: 0" in captured.out
    assert "✓ coverage, de:" in captured.out
    assert "· seconds per 100 words:" in captured.out
    assert "→ pass" in captured.out


def test_the_gate_command_exits_1_for_a_model_that_fails(tmp_path, capsys):
    """
    Given  a run in which one text could not be proofread at all
    When   `gate` runs over it
    Then   the failed text is named and the exit code is 1
    """
    space = workspace(tmp_path, reviews=_reviews())
    first = _repeats(space)[0]
    rewrite(first.path, lambda header, items: items[0]["outcome"].update(failed=True))

    code = proofreading_store._gate(_gate_args(space))

    captured = capsys.readouterr()
    assert code == 1, captured.err
    assert "✗ failed texts, de: 1" in captured.out
    assert "→ fail" in captured.out


def test_the_gate_command_exits_2_for_two_repeats(tmp_path, capsys):
    """
    Given  two repeats of one model
    When   `gate` runs over them
    Then   nothing is judged and the exit code is 2, because the spread of two says nothing
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space, count=2)

    code = proofreading_store._gate(_gate_args(space))

    captured = capsys.readouterr()
    assert code == 2, captured.err
    assert "→ incomplete" in captured.out


def test_the_compare_command_prints_every_check_and_uses_its_exit_code(tmp_path, capsys):
    """
    Given  two models measured the same way, one of them worse
    When   `compare` runs over them
    Then   it prints the four tests and the side-by-side figures, and exits 0 for the better one
           and 1 for the tie of two equal models
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)
    _repeats_other = [
        _export(space, f"other-r{repeat}", model="qwen3:4b", repeat=repeat) for repeat in (1, 2, 3)
    ]
    for written in _repeats_other:
        rewrite(written[0].path, _weaker)

    better = proofreading_store._compare(_gate_args(space, against="qwen3:4b"))
    tie = proofreading_store._compare(_gate_args(space, against=MODEL))

    captured = capsys.readouterr()
    assert better == 0, captured.err
    assert "✓ F0.5, repeats:" in captured.out
    assert "Side by side, never decisive:" in captured.out
    assert "→ better" in captured.out
    assert tie == 1
    assert "→ within the spread" in captured.out


def _weaker(header, items) -> None:
    """Turn every item of a run into one that found nothing, so the model is clearly worse."""
    for item in items:
        scores = item["scores"]
        if "fn_h1" not in scores:
            continue
        scores["fn"] = scores["fn"] + scores["tp"]
        scores["fn_h1"] = scores["fn_h1"] + scores["tp_h1"]
        scores["fn_h2"] = scores["fn_h2"] + scores["tp_h2"]
        scores["tp"] = scores["tp_h1"] = scores["tp_h2"] = 0


def test_the_compare_command_exits_2_when_a_run_is_missing(tmp_path, capsys):
    """
    Given  runs of one model at one temperature only
    When   `compare` asks for another temperature
    Then   it refuses and the exit code is 2, because there is nothing to compare
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)
    for repeat in (1, 2, 3):
        _export(space, f"other-r{repeat}", model="qwen3:4b", repeat=repeat)

    code = proofreading_store._compare(_gate_args(space, against="qwen3:4b", temperature=0.2))

    captured = capsys.readouterr()
    assert code == 2
    assert "no run of" in captured.err


def test_against_uses_the_machine_and_testset_of_the_model(tmp_path, capsys):
    """
    Given  a model whose newest run is on another test set at another temperature, and a model to
           compare against whose newest run is on that other test set too
    When   `compare` runs for the first model
    Then   both groups are read on the first model's test set, so the comparison is made at all
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)
    _export(
        space,
        "model-elsewhere",
        testset_id=OTHER_SET,
        temperature=0.2,
        date="2026-11-10T18:00:00",
    )
    for repeat in (1, 2, 3):
        _export(space, f"other-r{repeat}", model="qwen3:4b", repeat=repeat)
        _export(
            space,
            f"other-elsewhere-r{repeat}",
            model="qwen3:4b",
            repeat=repeat,
            testset_id=OTHER_SET,
            date="2026-11-10T18:00:00",
        )

    code = proofreading_store._compare(_gate_args(space, against="qwen3:4b"))

    captured = capsys.readouterr()
    assert code == 1, captured.err
    assert f"`{MODEL}`" in captured.out
    assert "`qwen3:4b`" in captured.out
    assert TESTSET in captured.out
    assert OTHER_SET not in captured.out
    assert "→ within the spread" in captured.out


def test_each_model_is_read_at_its_own_recommended_temperature(tmp_path, capsys):
    """
    Given  gemma4:e2b run at its recommended 1.0 and qwen3:4b run at its recommended 0.7
    When   `compare` runs without --temperature
    Then   each model is read at its own starting point and the comparison is made, with both
           temperatures in its figures; 0.2 is not looked for
    """
    space = workspace(tmp_path, reviews=_reviews())
    for repeat in (1, 2, 3):
        _export(space, f"first-r{repeat}", repeat=repeat, temperature=1.0)
        _export(space, f"other-r{repeat}", model="qwen3:4b", repeat=repeat, temperature=0.7)

    code = proofreading_store._compare(_gate_args(space, against="qwen3:4b", temperature=None))

    captured = capsys.readouterr()
    assert code in (0, 1), captured.err
    assert "temperature 1," in captured.out
    assert "temperature 0.7," in captured.out
    assert "→ within the spread" in captured.out or "→ better" in captured.out


def test_two_digests_of_one_tag_ask_for_a_choice(tmp_path, capsys):
    """
    Given  two digests of one model tag committed under the same conditions
    When   `gate` runs without --digest
    Then   it refuses and names the digests, and with --digest it judges the one asked for
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)
    for repeat in (1, 2, 3):
        _export(space, f"new-r{repeat}", repeat=repeat, digest="sha256:" + "f" * 32)

    refused = proofreading_store._gate(_gate_args(space))
    chosen = proofreading_store._gate(_gate_args(space, digest="f" * 8))

    captured = capsys.readouterr()
    assert refused == 2
    assert "give --digest" in captured.err
    assert chosen == 0, captured.err


def test_two_profiles_of_one_tag_ask_for_a_choice(tmp_path, capsys):
    """
    Given  two request profiles of one model committed under the same conditions
    When   `gate` runs without --compact-json
    Then   it refuses and asks for the choice, and with --compact-json it judges that profile
    """
    space = workspace(tmp_path, reviews=_reviews())
    _repeats(space)
    for repeat in (1, 2, 3):
        _export(space, f"compact-r{repeat}", repeat=repeat, compact_json=True, label=f"c{repeat}")

    refused = proofreading_store._gate(_gate_args(space))
    chosen = proofreading_store._gate(_gate_args(space, compact_json="on"))

    captured = capsys.readouterr()
    assert refused == 2
    assert "give --compact-json" in captured.err
    assert chosen == 0, captured.err


def test_the_marks_command_writes_the_marks_of_a_filled_in_sheet(tmp_path, capsys):
    """
    Given  a committed run with a page text whose note explains an edit, and a filled-in sheet
    When   `marks` runs over the sheet
    Then   it writes ``marks/<run-id>.json`` with the mark and the hash of the run's explanation
    """
    space = workspace(
        tmp_path,
        pointers=[dict(pointer, band="page") for pointer in invented_corpus.item_pointers()],
        reviews={
            "de-01": review(
                ChangeNote(
                    original="laufen",
                    corrected="läuft",
                    category="grammar",
                    explanation="Das Verb muss im Singular stehen.",
                ),
                original="Der Hund laufen schnell.",
                corrected="Der Hund läuft schnell.",
            )
        },
    )
    written = one(space)
    sheet = tmp_path / "sheet.md"
    sheet.write_text(
        f"Run: `{written.run_id}`\n\n"
        "| 1 | `de-01` | 0 | Der Hund laufen schnell. | laufen → läuft | grammar | x | correct |\n",
        encoding="utf-8",
    )

    code = proofreading_store._marks(Namespace(sheet=str(sheet), out=str(space.out), marker="joho"))

    captured = capsys.readouterr()
    assert code == 0, captured.err
    marks = spot_check.read_marks(spot_check.marks_path(space.out, written.run_id))
    assert marks.marker == "joho"
    assert [(mark.item, mark.note, mark.mark) for mark in marks.marks] == [("de-01", 0, "correct")]
    assert "wrote" in captured.out


def _page_workspace(tmp_path):
    """Return a workspace whose explained notes sit in page texts, so a sample holds them."""
    return workspace(
        tmp_path,
        pointers=[dict(pointer, band="page") for pointer in invented_corpus.item_pointers()],
        reviews=_reviews(),
    )


def _mark_every_note(space) -> Path:
    """Mark every note of the first repeat's sample ``correct``; return the marks file."""
    groups = selection.select(space.out, selection.Selection(model=MODEL, temperature=0.7))
    run, items = groups[0].first()
    drawn = spot_check.sample(run, items)
    path = spot_check.marks_path(space.out, run.run_id)
    spot_check.write_marks(
        path,
        spot_check.Marks(
            run_id=run.run_id,
            date=spot_check.today(),
            marker="joho",
            sample_seed=drawn.seed,
            marks=tuple(
                spot_check.Mark(note.item, note.note, note.explanation_sha256, "correct")
                for note in drawn.notes
            ),
        ),
    )
    return path


def test_gate_treats_a_refused_marks_file_as_unmarked(tmp_path, capsys):
    """
    Given  a run whose marks file names an explanation the run does not hold
    When   `gate` runs over it
    Then   the marks count as unmarked — the outcome is pending, not a pass — and the problems are
           printed, while the same file with the run's hashes gives a pass
    """
    space = _page_workspace(tmp_path)
    _repeats(space)
    path = _mark_every_note(space)
    marked = proofreading_store._gate(_gate_args(space))
    before = capsys.readouterr()

    data = json.loads(path.read_text(encoding="utf-8"))
    data["marks"][0]["explanation_sha256"] = "0" * 64
    path.write_text(json.dumps(data), encoding="utf-8")
    refused = proofreading_store._gate(_gate_args(space))
    after = capsys.readouterr()

    assert marked == 0, before.err
    assert "→ pass\n" in before.out
    assert refused == 0, after.err
    assert "→ pass, explanations not yet marked" in after.out
    assert f"note: {path.name} is refused" in after.err
    assert "explanation" in after.err


def test_marks_needs_a_marker(tmp_path):
    """
    Given  a filled-in sheet and no --marker
    When   the `marks` command parses its arguments
    Then   it refuses, because the name that goes into the repository is the teacher's choice
    """
    parser = proofreading_store.build_parser()

    with pytest.raises(SystemExit) as refused:
        parser.parse_args(["marks", "sheet.md"])

    assert refused.value.code == 2


def test_the_marks_command_refuses_a_note_that_is_not_in_the_sample(tmp_path, capsys):
    """
    Given  a sheet that marks a note the run's sample does not hold
    When   `marks` runs over it
    Then   it refuses and writes nothing, because the sheet and the run disagree
    """
    space = workspace(tmp_path)
    written = one(space)
    sheet = tmp_path / "sheet.md"
    sheet.write_text(
        f"Run: `{written.run_id}`\n\n| 1 | `de-01` | 7 | x | y | grammar | z | correct |\n",
        encoding="utf-8",
    )

    code = proofreading_store._marks(Namespace(sheet=str(sheet), out=str(space.out), marker="joho"))

    captured = capsys.readouterr()
    assert code == 1
    assert "is not in the sample" in captured.err
    assert not spot_check.marks_path(space.out, written.run_id).exists()


# ── the sheet ─────────────────────────────────────────────────────────────────

SHEET_SOURCE = "Der Hund laufen schnell ."
SHEET_OUTPUT = "Ein Hund läuft schnell ."


def _sheet_row(
    text: fixtures.Text, *, output: str = SHEET_OUTPUT, model: str = fixtures.MODEL
) -> results.ResultRow:
    """Return the cache row of one page text, with the changes its notes explain."""
    return results.ResultRow(
        id=f"de/{text.id}",
        model=model,
        temperature=0.2,
        language="de",
        kind="errors",
        origin="falko",
        seconds=1.0,
        failed=False,
        fallback=False,
        has_review=True,
        source=SHEET_SOURCE,
        output=output,
        changes=tuple(
            {"edit": list(edit), "category": "grammar", "explanation": fixtures.EXPLANATION}
            for edit in text.edits
        ),
    )


def test_the_sheet_command_reads_a_cache_of_several_runs(tmp_path, monkeypatch, capsys):
    """
    Given  a committed run of repeat 1 and a cache that holds the rows of three repeats and of
           another model under the same item ids
    When   `sheet` runs over that cache
    Then   it prints the sheet of the run's own rows instead of refusing on another run's output
    """
    monkeypatch.delenv("BUILD_WORKSPACE_DIRECTORY", raising=False)
    texts = fixtures.page_texts("de", count=2)
    rows = [_sheet_row(text) for text in texts]
    digest = export_module.output_sha256(rows[0])
    texts = [replace(text, output_sha256=digest) for text in texts]
    out = fixtures.repository(tmp_path, *texts, run_id="gemma4-e4b-r1")
    cache = tmp_path / "cache"
    cache.mkdir()
    results.write_rows(cache / results.result_file_name(fixtures.MODEL, 0.2, repeat=1), rows)
    for repeat in (2, 3):
        results.write_rows(
            cache / results.result_file_name(fixtures.MODEL, 0.2, repeat=repeat),
            [replace(row, output=f"Ein Hund läuft schnell{'.' * repeat}") for row in rows],
        )
    results.write_rows(
        cache / results.result_file_name(fixtures.OTHER, 0.2),
        [replace(row, model=fixtures.OTHER) for row in rows],
    )

    code = proofreading_store._sheet(Namespace(out=out, run="gemma4-e4b-r1", cache=cache))

    printed = capsys.readouterr()
    assert code == 0
    assert printed.err == ""
    assert "Run: `gemma4-e4b-r1`" in printed.out
    assert "| 1 | `de-01` | 0 |" in printed.out
    assert fixtures.EXPLANATION in printed.out
