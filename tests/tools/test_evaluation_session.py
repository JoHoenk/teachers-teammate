"""Tests of the sessions of one run: whether a run may be resumed into its file, and what differs.

A run file is append-only: its first line is the header, and every start appends one session line.
The header is what a resume has to agree with, so the tests here change one fact of it at a time
and assert that the refusal names that fact and nothing else.
"""

from __future__ import annotations

import ast
from dataclasses import replace
import importlib
from pathlib import Path
from typing import Any

import pytest

from tools.evaluation import run
from tools.evaluation.core import protocol_table
from tools.evaluation.core.modes import Exchange, Step
from tools.evaluation.core.records import (
    Answer,
    Environment,
    Input,
    Protocol,
    RunFile,
    RunHeader,
    RunSettings,
)
from tools.evaluation.run import session
from tools.evaluation.store import reader, writer


def _header(**overrides) -> RunHeader:
    """Return the header of a run of one German text in review mode, with *overrides*."""
    values: dict[str, Any] = {
        "input": Input(
            testset="gec-2026-12", question_sha256="b" * 64, texts=27, languages=("de",)
        ),
        "protocol": Protocol(
            model="granite4.1:3b",
            model_digest="6fd349357287abc",
            exchange=Exchange.ONE_REQUEST_REVIEW,
            steps=(),
            temperature=0.2,
            num_ctx=8192,
            instructions=(("de", "c" * 64),),
            fingerprint="a" * 64,
        ),
        "run": RunSettings(seed=20261109, repeat=1, timeout=600.0),
        "model_info": {"digest": "6fd349357287abc"},
        "environment": Environment(machine="rx9070xt", ollama_version="0.40.15"),
    }
    values.update(overrides)
    return RunHeader(**values)


def _answer(text: str = "de/de-01") -> Answer:
    """Return one answer line of a run."""
    return Answer(text=text, text_sha256="e" * 64, output="Ein Text.")


def _one_version_of_two_fingerprints(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the protocol table one version that accepts the fingerprints ``a…`` and ``b…``."""
    table = {"versions": [{"version": 4, "fingerprints": ["a" * 64, "b" * 64]}]}
    monkeypatch.setattr(protocol_table, "read_table", lambda path=None: table)


@pytest.mark.use_case("Model_Eval_Measure")
def test_two_sessions_of_one_run_do_not_conflict() -> None:
    """
    Given  the header of a run, twice
    When   they are compared
    Then   nothing conflicts, so the run may be resumed; the facts that may differ between two
           starts are in the session line, which is never compared
    """
    assert session.resume_conflicts(_header(), _header()) == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_digest_seed_or_fingerprint_is_named() -> None:
    """
    Given  a header and a second one that changed the model's digest, the seed and the fingerprint
    When   they are compared
    Then   each of them is named, so a run is never resumed into another protocol
    """
    second = _header(
        protocol=Protocol(
            model="granite4.1:3b",
            model_digest="ffffffffffff",
            exchange=Exchange.ONE_REQUEST_REVIEW,
            steps=(),
            temperature=0.2,
            num_ctx=8192,
            instructions=(("de", "c" * 64),),
            fingerprint="0" * 64,
        ),
        run=RunSettings(seed=1, repeat=1, timeout=600.0),
        model_info={"digest": "ffffffffffff"},
    )

    conflicts = session.resume_conflicts(_header(), second)

    for key in ("model_info.digest:", "run.seed:", "protocol.fingerprint:"):
        assert any(line.startswith(key) for line in conflicts), key


@pytest.mark.use_case("Model_Eval_Measure")
def test_two_fingerprints_of_one_version_do_not_conflict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a protocol table whose version 4 accepts two fingerprints, and two headers that differ
           only in the fingerprint
    When   they are compared
    Then   nothing conflicts, because an additive change of the request adds a fingerprint without
           changing what the texts mean, so a long run in progress is not blocked by it
    """
    _one_version_of_two_fingerprints(monkeypatch)
    second = _header(protocol=replace(_header().protocol, fingerprint="b" * 64))

    assert session.resume_conflicts(_header(), second) == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_two_fingerprints_of_two_versions_conflict() -> None:
    """
    Given  a header of the protocol of one version and a second one of the next version
    When   they are compared
    Then   the fingerprint is named and nothing else, because a request-side change means the
           answers were made under another protocol
    """
    first = _header(
        protocol=replace(_header().protocol, fingerprint=protocol_table.fingerprints_of(1)[0])
    )
    second = _header(
        protocol=replace(_header().protocol, fingerprint=protocol_table.fingerprints_of(2)[0])
    )

    conflicts = session.resume_conflicts(first, second)

    assert len(conflicts) == 1
    assert conflicts[0].startswith("protocol.fingerprint:")


@pytest.mark.use_case("Model_Eval_Measure")
def test_other_instructions_conflict_within_one_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a protocol table whose version 4 accepts two fingerprints, and two headers of that
           version that sent another hash for German
    When   they are compared
    Then   the instructions are named: the fingerprint may be additive, what was sent may not
    """
    _one_version_of_two_fingerprints(monkeypatch)
    second = _header(
        protocol=replace(_header().protocol, fingerprint="b" * 64, instructions=(("de", "f" * 64),))
    )

    conflicts = session.resume_conflicts(_header(), second)

    assert len(conflicts) == 1
    assert conflicts[0].startswith("protocol.instructions:")


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_fingerprint_the_table_does_not_record_is_compared_as_it_is() -> None:
    """
    Given  a header of a recorded protocol and a second one of a fingerprint no version accepts
    When   they are compared
    Then   the fingerprint is named, because nobody can say which protocol the second one is
    """
    first = _header(
        protocol=replace(_header().protocol, fingerprint=protocol_table.fingerprints_of(1)[0])
    )

    conflicts = session.resume_conflicts(first, _header())

    assert len(conflicts) == 1
    assert conflicts[0].startswith("protocol.fingerprint:")


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_provider_is_named() -> None:
    """
    Given  a header and a second one of another provider
    When   they are compared
    Then   the provider is named, so a run is never resumed into another service
    """
    second = _header(
        protocol=Protocol(
            model="granite4.1:3b",
            model_digest="6fd349357287abc",
            exchange=Exchange.ONE_REQUEST_REVIEW,
            steps=(),
            temperature=0.2,
            num_ctx=8192,
            instructions=(("de", "c" * 64),),
            fingerprint="a" * 64,
            provider="openai",
        )
    )

    conflicts = session.resume_conflicts(_header(), second)

    assert any(line.startswith("protocol.provider:") for line in conflicts)


@pytest.mark.use_case("Model_Eval_Measure")
def test_other_texts_or_another_timeout_are_named() -> None:
    """
    Given  a header and a second one over a rebuilt test set of the same name, with another
           count of texts and another timeout
    When   they are compared
    Then   the hash, the count and the timeout are named, because they decide what an answer means
    """
    second = _header(
        input=Input(testset="gec-2026-12", question_sha256="d" * 64, texts=28, languages=("de",)),
        run=RunSettings(seed=20261109, repeat=1, timeout=60.0),
    )

    conflicts = session.resume_conflicts(_header(), second)

    for key in ("input.question_sha256:", "input.texts:", "run.timeout:"):
        assert any(line.startswith(key) for line in conflicts), key


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_changed_exchange_step_or_instructions_hash_is_named() -> None:
    """
    Given  a header and a second one that asked another exchange, with another step and another
           hash of what was sent
    When   they are compared
    Then   each of them is named, because they decide what the answers mean
    """
    second = _header(
        protocol=Protocol(
            model="granite4.1:3b",
            model_digest="6fd349357287abc",
            exchange=Exchange.TEXT_FIRST,
            steps=(Step.CORRECT,),
            temperature=0.2,
            num_ctx=8192,
            instructions=(("de", "f" * 64),),
            fingerprint="a" * 64,
        )
    )

    conflicts = session.resume_conflicts(_header(), second)

    for key in ("protocol.exchange:", "protocol.steps:", "protocol.instructions:"):
        assert any(line.startswith(key) for line in conflicts), key


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_conflict_names_the_block_and_both_values() -> None:
    """
    Given  a header and a second one with another seed
    When   they are compared
    Then   the line names the block and the field, and shows the stored value before the new one,
           so a reader can see which way the run moved
    """
    conflicts = session.resume_conflicts(
        _header(), _header(run=RunSettings(seed=1, repeat=1, timeout=600.0))
    )

    assert conflicts == ["run.seed: 20261109 → 1"]


# ── may this run be resumed? ──────────────────────────────────────────────────


def test_a_fresh_run_may_start(tmp_path: Path) -> None:
    """
    Given  a run file that does not exist
    When   a session asks to start
    Then   it may
    """
    assert session.resume_problem(tmp_path / "run.run.jsonl", _header(), done=set()).problem is None


def test_a_header_without_answers_may_be_resumed_into(tmp_path: Path) -> None:
    """
    Given  a run file that holds its header and no answer, left by a session that stopped early
    When   a session asks to resume
    Then   it may, because the header is what says which run the file is
    """
    path = tmp_path / "run.run.jsonl"
    writer.start_run(path, RunFile(header=_header()))

    assert session.resume_problem(path, _header(), done=set()).problem is None


def test_a_matching_run_may_be_resumed_into(tmp_path: Path) -> None:
    """
    Given  a run file whose header agrees with the new session and which holds one answer
    When   a session asks to resume
    Then   it may
    """
    path = tmp_path / "run.run.jsonl"
    writer.start_run(path, RunFile(header=_header()))
    writer.append_answer(path, _answer())

    assert session.resume_problem(path, _header(), done={"de/de-01"}).problem is None


def test_a_conflicting_run_is_named_with_the_file_to_delete(tmp_path: Path) -> None:
    """
    Given  a run file whose header used another model digest
    When   a session asks to resume
    Then   the conflict is named and so is the file to delete
    """
    path = tmp_path / "run.run.jsonl"
    writer.start_run(path, RunFile(header=_header(model_info={"digest": "other"})))

    problem = session.resume_problem(path, _header(), done={"de/de-01"}).problem

    assert problem is not None
    assert "model_info.digest" in problem
    assert f"delete {path}" in problem


def test_answers_without_a_header_cannot_be_resumed_into(tmp_path: Path) -> None:
    """
    Given  a run file whose first line is an answer and not a header
    When   a session asks to resume into it
    Then   it is refused, because nobody can say which session made the answers, and the message
           names the file to delete
    """
    path = tmp_path / "run.run.jsonl"
    writer.append_answer(path, _answer())

    problem = session.resume_problem(path, _header(), done={"de/de-01"}).problem

    assert problem is not None
    assert "run.run.jsonl" in problem
    assert f"delete {path}" in problem


@pytest.mark.parametrize("content", ["", "{", "[]"])
def test_a_run_file_that_cannot_be_read_is_a_problem_not_a_crash(
    tmp_path: Path, content: str
) -> None:
    """
    Given  a run file that is empty, not JSON, or a JSON array
    When   a session asks to resume
    Then   a message comes back that names the file and says to delete it
    """
    path = tmp_path / "run.run.jsonl"
    path.write_text(content, encoding="utf-8")

    problem = session.resume_problem(path, _header(), done=set()).problem

    assert problem is not None
    assert "run.run.jsonl" in problem
    assert "start this run over" in problem


# ── a torn last line ─────────────────────────────────────────────────────────


def _torn_run(path: Path) -> None:
    """Write a run file of one answer whose last line was cut off mid-write."""
    writer.start_run(path, RunFile(header=_header()))
    writer.append_answer(path, _answer())
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type": "answer", "text": "de/de-02", "text_sha')


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_run_torn_mid_answer_resumes_and_drops_the_half_line(tmp_path: Path) -> None:
    """
    Given  a run file whose last line was cut off mid-write, and a session that resumes into it
    When   the session asks to resume
    Then   it may, the half line is gone from the file, and the message says it is sent again
    """
    path = tmp_path / "run.run.jsonl"
    _torn_run(path)

    resume = session.resume_problem(path, _header(), done={"de/de-01"})

    assert resume.problem is None
    assert resume.dropped is not None
    assert "it is sent again" in resume.dropped
    assert path.read_text(encoding="utf-8").endswith("}\n")
    assert [answer.text for answer in reader.read_run(path).answers] == ["de/de-01"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_run_torn_in_its_header_is_still_refused(tmp_path: Path) -> None:
    """
    Given  a run file whose first line was cut off mid-write
    When   a session asks to resume into it
    Then   it is refused, because nobody can say which session made the answers below it
    """
    path = tmp_path / "run.run.jsonl"
    path.write_text('{"format": 3, "type": "run", "task": "proof', encoding="utf-8")

    resume = session.resume_problem(path, _header(), done=set())

    assert resume.problem is not None
    assert "start this run over" in resume.problem
    assert resume.dropped is None


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


def _run_modules() -> list[str]:
    """Return the names of the modules of ``tools.evaluation.run``, the packages excluded."""
    directory = Path(run.__file__).parent
    names = [path.stem for path in directory.glob("*.py") if path.stem != "__init__"]
    names += [
        f"one_request.{path.stem}"
        for path in (directory / "one_request").glob("*.py")
        if path.stem != "__init__"
    ]
    return sorted(names)


@pytest.mark.parametrize("name", _run_modules())
def test_no_module_of_the_run_layer_reaches_a_layer_above_it(name) -> None:
    """
    Given  every module of ``tools.evaluation.run``
    When   its imports are read
    Then   it imports neither the report, nor a check, nor a command line, so the layer graph
           holds and no cycle can form
    """
    found = _imported_modules(importlib.import_module(f"tools.evaluation.run.{name}"))

    for restricted in ("report", "checks", "evaluate", "report_cli"):
        assert not any(imported.split(".")[-1] == restricted for imported in found), (
            name,
            restricted,
        )
