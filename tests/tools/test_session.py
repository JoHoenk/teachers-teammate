"""Tests of the sessions of one run: the header file, the resume check and the layering."""

from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from tools import model_eval
from tools.model_eval import session

_IDENTITY = {
    "eval_version": 1,
    "fingerprint": "a" * 64,
    "instructions_sha256": "c" * 64,
    "answer_format_version": 3,
    "machine": "rx9070xt",
    "ollama_version": "0.40.15",
    "model": "granite4.1:3b",
    "model_info": {"digest": "6fd349357287abc"},
    "mode": "review",
    "temperature": 0.2,
    "num_ctx": 8192,
    "timeout": 600.0,
    "seed": 20261109,
    "repeat": 1,
    "isolated": False,
    "gpu_sampler": None,
    "testset": "set-labelled",
    "testset_sha256": "b" * 64,
    "texts": 27,
}


def _header(**overrides) -> dict:
    """Return one session header: the identity, the facts of the session, and *overrides*."""
    return {
        "date": "2026-11-09T19:00:00+00:00",
        "tool_revision": "abc1234",
        "loaded_before": [],
        "vram_baseline_mb": None,
        **_IDENTITY,
        **overrides,
    }


@pytest.mark.use_case("Model_Eval_Measure")
def test_two_sessions_that_differ_only_in_their_own_facts_do_not_conflict() -> None:
    """
    Given  two sessions of one run that differ in date, revision, loaded models and idle memory
    When   they are compared
    Then   nothing conflicts, so the run may be resumed
    """
    first = _header()
    second = _header(
        tool_revision="def5678+dirty",
        date="2026-11-10T08:00:00+00:00",
        loaded_before=["m:1b"],
        vram_baseline_mb=700,
    )

    assert session.resume_conflicts(first, second) == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_digest_seed_version_or_fingerprint_is_named() -> None:
    """
    Given  a session and a second one that changed the model's digest, the seed, the eval version
           and the fingerprint
    When   they are compared
    Then   each of them is named, so a run is never resumed into another protocol
    """
    first = _header()
    second = _header(
        seed=1, model_info={"digest": "ffffffffffff"}, eval_version=2, fingerprint="0" * 64
    )

    conflicts = session.resume_conflicts(first, second)

    for key in ("model_info.digest:", "seed:", "eval_version:", "fingerprint:"):
        assert any(line.startswith(key) for line in conflicts), key


@pytest.mark.use_case("Model_Eval_Measure")
def test_other_texts_or_another_timeout_are_named() -> None:
    """
    Given  a session and a second one over a rebuilt test set of the same name, with another
           count of texts and another timeout
    When   they are compared
    Then   the hash, the count and the timeout are named, because they decide what the rows mean
    """
    conflicts = session.resume_conflicts(
        _header(), _header(testset_sha256="d" * 64, texts=28, timeout=60.0)
    )

    for key in ("testset_sha256:", "texts:", "timeout:"):
        assert any(line.startswith(key) for line in conflicts), key


def test_the_header_file_is_read_and_appended_in_order(tmp_path: Path) -> None:
    """
    Given  a header file that does not exist yet
    When   two sessions are appended
    Then   both are read back, oldest first
    """
    path = tmp_path / "run.header.json"

    session.append_session(path, _header(date="first"))
    session.append_session(path, _header(date="second"))

    assert [entry["date"] for entry in session.read_sessions(path)] == ["first", "second"]


@pytest.mark.parametrize("content", ["[]", "{}", "[1]", "not json"])
def test_a_header_file_that_is_not_a_list_of_objects_is_refused(tmp_path: Path, content) -> None:
    """
    Given  a header file that is empty, an object, a list of non-objects, or not JSON
    When   it is read
    Then   a ValueError names the file
    """
    path = tmp_path / "run.header.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError, match=r"run\.header\.json"):
        session.read_sessions(path)


def test_the_conflicts_of_a_run_name_the_pair_of_sessions() -> None:
    """
    Given  three sessions of which the third changed the seed
    When   the conflicts of the run are asked for
    Then   the line names session 3, and the first pair, which agrees, is silent
    """
    found = session.session_conflicts([_header(), _header(), _header(seed=1)])

    assert len(found) == 1
    assert found[0].startswith("session 3: seed:")


# ── may this run be resumed? ──────────────────────────────────────────────────


def test_rows_without_a_header_cannot_be_resumed_into(tmp_path: Path) -> None:
    """
    Given  a results file with results and no header file
    When   a session asks to resume into it
    Then   it is refused, because nobody can say which session made the rows, and the message
           names the file to delete
    """
    rows, header = tmp_path / "run.jsonl", tmp_path / "run.header.json"

    problem = session.resume_problem(rows, header, _IDENTITY, done={"de/01"})

    assert problem is not None
    assert "1 results but no run.header.json" in problem
    assert f"delete {rows}" in problem


def test_a_header_without_rows_and_a_fresh_run_may_start(tmp_path: Path) -> None:
    """
    Given  no results at all, with and without a header left by a session that stopped early
    When   a session asks to start
    Then   it may
    """
    rows, header = tmp_path / "run.jsonl", tmp_path / "run.header.json"

    assert session.resume_problem(rows, header, _IDENTITY, done=set()) is None

    session.append_session(header, _header())
    assert session.resume_problem(rows, header, _IDENTITY, done=set()) is None


def test_a_matching_session_may_be_resumed_into(tmp_path: Path) -> None:
    """
    Given  a header file whose last session agrees with the new one
    When   a session asks to resume
    Then   it may
    """
    rows, header = tmp_path / "run.jsonl", tmp_path / "run.header.json"
    session.append_session(header, _header())

    assert session.resume_problem(rows, header, _IDENTITY, done={"de/01"}) is None


def test_a_conflicting_session_is_named_with_the_files_to_delete(tmp_path: Path) -> None:
    """
    Given  a header file whose last session used another model digest
    When   a session asks to resume
    Then   the conflict is named and so are the two files to delete
    """
    rows, header = tmp_path / "run.jsonl", tmp_path / "run.header.json"
    session.append_session(header, _header(model_info={"digest": "other"}))

    problem = session.resume_problem(rows, header, _IDENTITY, done={"de/01"})

    assert problem is not None
    assert "model_info.digest" in problem
    assert f"delete {rows} and {header}" in problem


def test_a_header_file_that_cannot_be_read_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    """
    Given  a header file that is not JSON
    When   a session asks to resume
    Then   a message comes back that names the file and says to delete both
    """
    rows, header = tmp_path / "run.jsonl", tmp_path / "run.header.json"
    header.write_text("{", encoding="utf-8")

    problem = session.resume_problem(rows, header, _IDENTITY, done={"de/01"})

    assert problem is not None
    assert "run.header.json" in problem
    assert "start this run over" in problem


# ── layering ──────────────────────────────────────────────────────────────────


def _imported_modules(module) -> set[str]:
    """Return the names a module imports, relative imports as written (``.measure``)."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            found.add(base)
            found.update(f"{base}.{alias.name}".replace("..", ".") for alias in node.names)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


# The renderer is a leaf that only the command lines import, the run-time module belongs to the
# runner, and no module of the package imports a command line.
_RESTRICTED = {
    "report": "the renderer is a leaf that only the command lines import",
    "measure": "the run-time module is imported by the runner and the comparison command line",
    "runner": "the run loop is imported by the comparison command line",
    "compare_writing_models": "no module of model_eval imports a command line",
    "proofreading_store": "no module of model_eval imports a command line",
}
_ALLOWED = {"measure": {"runner", "gate_one", "model_fit"}}
"""The exceptions: the runner, gate 1 and the size measurement talk to a server."""


def _module_names() -> list[str]:
    """Return the names of the modules of ``tools.model_eval``, the packages themselves excluded.

    The subpackages are listed too: a corpus adapter and a recipe are modules of this package, so
    the layering rules apply to them as well.
    """
    directory = Path(model_eval.__file__).parent
    names = [path.stem for path in directory.glob("*.py") if path.stem != "__init__"]
    for package in ("corpora", "recipes"):
        names += [
            f"{package}.{path.stem}"
            for path in (directory / package).glob("*.py")
            if path.stem != "__init__"
        ]
    return sorted(names)


@pytest.mark.parametrize("name", _module_names())
def test_the_offline_modules_do_not_reach_the_run_time_module_or_the_command_line(name) -> None:
    """
    Given  every module of ``tools.model_eval``
    When   its imports are read
    Then   it imports neither the renderer, nor the run-time module, nor the run loop, nor a
           command line, except the runner, which drives the run-time module
    """
    found = _imported_modules(importlib.import_module(f"tools.model_eval.{name}"))

    for restricted, why in _RESTRICTED.items():
        if name in _ALLOWED.get(restricted, set()):
            continue
        assert not any(imported.split(".")[-1] == restricted for imported in found), (
            name,
            restricted,
            why,
        )


# A corpus adapter sits below the manifest: it knows its corpus, not the set that draws on it.
_CORPORA_RESTRICTED = {
    "testset": "testset imports corpora, so a corpus adapter must not import it back",
    "export": "a corpus adapter knows nothing about a run",
    "validation": "a corpus adapter knows nothing about a run",
    "recipes": "which texts a set chooses is a recipe, not a corpus",
    "compare_writing_models": "no module of model_eval imports a command line",
    "proofreading_store": "no module of model_eval imports a command line",
}


@pytest.mark.parametrize("name", [name for name in _module_names() if name.startswith("corpora.")])
def test_a_corpus_adapter_does_not_reach_the_manifest_or_a_recipe(name) -> None:
    """
    Given  every module of ``tools.model_eval.corpora``
    When   its imports are read
    Then   it imports neither the manifest, nor a run, nor a recipe, nor a command line, so the
           package stays below all of them and no cycle can form
    """
    found = _imported_modules(importlib.import_module(f"tools.model_eval.{name}"))

    for restricted, why in _CORPORA_RESTRICTED.items():
        assert not any(imported.split(".")[-1] == restricted for imported in found), (
            name,
            restricted,
            why,
        )
