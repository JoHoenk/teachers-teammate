"""The run loop of ``evaluate.py run``, driven without a server.

The loop lives outside the command line: the planning, the resume check, the seed per repeat, the
warm-up, the header and the sampler.  Every seam is replaced here, so what these tests check is
the loop itself — the order of the calls, what is written where, and what stops a run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
import time
from typing import Any, cast

import pytest

from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.exceptions import StageError, StageErrorKind
from teachers_teammate.interfaces import Corrector
from tools.evaluation.core.modes import (
    ALL_STEPS,
    AnswerMode,
    Exchange,
    Step,
    exchange_and_steps,
)
from tools.evaluation.core.performance import LoadedModel, RequestStats
from tools.evaluation.core.records import Answer, RunFile, RunHeader, Session, TestRecord
from tools.evaluation.run import protocol, runner
from tools.evaluation.run.measure import VramSampler
from tools.evaluation.run.ports import RecordingPort
from tools.evaluation.store import reader, writer

MODEL = "gemma4:e2b"
OTHER_MODEL = "granite4.1:3b"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"
OTHER_DIGEST = "sha256:fedcba9876543210fedcba9876543210"
STAMP = protocol.Stamp(eval_version=2, fingerprint="a" * 64)
DATE = "2026-11-09"
SOURCE = "Der Hund laufen schnell."


# ── the invented run ──────────────────────────────────────────────────────────


def _records() -> list[TestRecord]:
    """Return the two invented texts every run here is asked about."""
    return [
        TestRecord(
            id=name,
            language="de",
            kind="errors",
            origin="invented",
            source=SOURCE,
            reference="Der Hund läuft schnell.",
        )
        for name in ("de-01", "de-02")
    ]


def _answer(identifier: str) -> Answer:
    """Return one stored answer of the invented text."""
    return Answer(
        text=identifier,
        text_sha256="e" * 64,
        output="Der Hund läuft schnell.",
        requests=(RequestStats.failure(MODEL, 0.1),),
    )


def _matrix(root: Path, **overrides) -> runner.Matrix:
    """Return the matrix of a run over the invented texts, with *overrides* applied."""
    values: dict[str, Any] = {
        "root": root,
        "testset": "gec-2026-12",
        "languages": ("de",),
        "models": (MODEL,),
        "temperatures": (0.2,),
        "setups": ((Exchange.ONE_REQUEST_REVIEW, ALL_STEPS),),
        "repeats": 1,
        "base_seed": 20261109,
        "timeout": 5.0,
        "isolate": False,
        "gpu_sampler": None,
        "machine": MACHINE,
    }
    values.update(overrides)
    return runner.Matrix(**values)


def _runs(root: Path) -> list[Path]:
    """Return the run files of the store at *root*, in path order."""
    return sorted((root / "runs").glob("*.run.jsonl"))


def _planned(matrix: runner.Matrix, probe: _Probe | None = None) -> runner.Plan:
    """Return what the command line works out for *matrix* before the first request."""
    return runner.plan(matrix, _records(), STAMP, probe or _Probe(), date=DATE)


def _header(
    matrix: runner.Matrix, item: runner.PlannedRun, probe: _Probe | None = None
) -> RunHeader:
    """Return the header *item* would write, built from *matrix* and *probe*."""
    return runner.header_of(
        matrix,
        exchange=item.exchange,
        steps=item.steps,
        model=item.model,
        temperature=item.temperature,
        repeat=item.repeat,
        seed=item.seed,
        stamp=STAMP,
        probe=probe or _Probe(),
        records=_records(),
    )


def _write_run(path: Path, header: RunHeader, answers: tuple[Answer, ...] = ()) -> None:
    """Store a run file as an earlier session left it: its header, one session, then its answers."""
    session = Session(date=f"{DATE}T18:00:00+00:00", tool_revision="cfdc0ea")
    writer.start_run(path, RunFile(header=header, sessions=(session,)))
    for answer in answers:
        writer.append_answer(path, answer)


# ── the fakes ─────────────────────────────────────────────────────────────────


class _Probe:
    """A server that answers without a server, and remembers what it was asked to unload."""

    def __init__(self, digests: dict[str, str] | None = None) -> None:
        self.digests = dict(digests or {})
        self.unloaded: list[str] = []
        self.unload_all_calls = 0

    def version(self) -> str:
        """Return the version of the invented server."""
        return "0.40.13"

    def model_info(self, tag: str) -> dict:
        """Return the digest and details of the invented model."""
        return {
            "digest": self.digests.get(tag, DIGEST),
            "quantization": "Q4_K_M",
            "parameter_size": "2B",
            "family": "gemma3",
        }

    def loaded(self) -> tuple[LoadedModel, ...]:
        """Return nothing loaded: the invented server is always idle."""
        return ()

    def unload(self, name: str) -> None:
        """Remember that the model was dropped from memory."""
        self.unloaded.append(name)

    def unload_all(
        self, *, wait_s: float = 60.0, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        """Count the isolating start; the invented server is idle already."""
        self.unload_all_calls += 1


class _ChangingProbe(_Probe):
    """A server whose model is pulled again after the run was planned."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def model_info(self, tag: str) -> dict:
        """Return the planned digest once, then the digest of the model pulled again."""
        self.calls += 1
        info = super().model_info(tag)
        return info if self.calls == 1 else {**info, "digest": OTHER_DIGEST}


class _Client:
    """A recording client without a server: it keeps the requests the corrector sends."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.requests: list[RequestStats] = []

    def drain(self) -> tuple[RequestStats, ...]:
        """Return the requests recorded since the last call and forget them."""
        taken, self.requests = tuple(self.requests), []
        return taken


class _Scripted(Corrector):
    """A corrector that answers with the source text and notes the call on the client."""

    def __init__(self, client: _Client, log: list[str], *, fail_on: str | None = None) -> None:
        self._client = client
        self._log = log
        self._fail_on = fail_on
        self.seen: list[str] = []

    def correct(self, raw_text, language, *, text_format="plain") -> CorrectionOutcome:
        """Note the request, then answer with the text it was given."""
        self.seen.append(raw_text)
        self._log.append(f"correct {raw_text}")
        self._client.requests.append(RequestStats.failure(MODEL, 0.1))
        if raw_text == self._fail_on:
            raise RuntimeError("the server went away")
        return CorrectionOutcome(text=raw_text)


class _Sampler:
    """A graphics memory sampler that records when it was started and stopped."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.baseline_mb: int | None = 100

    def start(self) -> None:
        """Note the start."""
        self._log.append("sampler.start")

    def stop(self) -> None:
        """Note the stop."""
        self._log.append("sampler.stop")

    def peak_mb(self, since: float, until: float) -> int | None:
        """Return an invented peak."""
        return 900


class _Harness:
    """The seams of one run, with every call recorded."""

    def __init__(self, *, probe: _Probe | None = None, fail_on: str | None = None) -> None:
        self.log: list[str] = []
        self.probe = probe or _Probe()
        self.clients: list[_Client] = []
        self.correctors: list[_Scripted] = []
        self.profiles: list[RequestProfile] = []
        self._fail_on = fail_on

    def new_client(self, seed: int) -> _Client:
        """Return a recording client for *seed*."""
        client = _Client(seed)
        self.clients.append(client)
        return client

    def new_factory(
        self,
        client: RecordingPort,
        exchange: Exchange,
        steps: tuple[Step, ...],
        profile: RequestProfile,
    ) -> Callable[[str, float], Corrector]:
        """Return a factory whose correctors answer with the text they were given."""
        self.profiles.append(profile)

        def build(model: str, temperature: float) -> Corrector:
            corrector = _Scripted(cast(_Client, client), self.log, fail_on=self._fail_on)
            self.correctors.append(corrector)
            return corrector

        return build

    def new_sampler(self) -> VramSampler:
        """Return a sampler that writes into this run's log."""
        return _Sampler(self.log)

    def seams(self) -> runner.Seams:
        """Return the seams of this run."""
        return runner.Seams(
            probe=self.probe,
            new_client=self.new_client,
            new_factory=self.new_factory,
            new_sampler=self.new_sampler,
            now=lambda: f"{DATE}T18:00:00+00:00",
            revision="cfdc0ea",
        )


# ── the smoke check ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Verdict:
    """What a smoke check answers; ``checks.smoke.Smoke`` has the same two fields."""

    passed: bool
    reason: str


def _smoke(passed: bool = True) -> Callable[[str, Exchange, tuple[Step, ...], float], _Verdict]:
    """Return a smoke seam that records the setups it was asked about."""
    return lambda _model, _exchange, _steps, _temperature: _Verdict(
        passed, "no sign of a systematic fault; gate 1 decides"
    )


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_smoke_runs_once_per_setup_and_a_temperature_arm_is_its_own(tmp_path: Path) -> None:
    """
    Given  a run of one model at two temperatures, each repeated twice
    When   it runs with a smoke seam
    Then   the smoke check is asked once per setup — model, exchange, steps and temperature, not
           per repeat — and every session of a setup it passed runs
    """
    harness = _Harness()
    asked: list[tuple[str, Exchange, tuple[Step, ...], float]] = []

    def smoke(
        model: str, exchange: Exchange, steps: tuple[Step, ...], temperature: float
    ) -> _Verdict:
        asked.append((model, exchange, steps, temperature))
        return _smoke()(model, exchange, steps, temperature)

    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, replace(harness.seams(), smoke=smoke))

    assert asked == [
        (MODEL, Exchange.ONE_REQUEST_REVIEW, ALL_STEPS, 0.2),
        (MODEL, Exchange.ONE_REQUEST_REVIEW, ALL_STEPS, 0.7),
    ]
    assert (result.sessions, result.rows) == (4, 8)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failed_smoke_skips_only_its_own_setup(tmp_path: Path) -> None:
    """
    Given  a run of one model at two temperatures, where the arm at 0.7 fails the smoke check
    When   it runs
    Then   the sessions of that arm are skipped and the other arm runs, so one broken setup does
           not cost the run
    """
    harness = _Harness()

    def smoke(
        _model: str, _exchange: Exchange, _steps: tuple[Step, ...], temperature: float
    ) -> _Verdict:
        return _smoke(passed=temperature == 0.2)(_model, _exchange, _steps, temperature)

    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, replace(harness.seams(), smoke=smoke))

    assert (result.sessions, result.rows) == (2, 4)
    assert len(_runs(tmp_path)) == 2
    assert all(reader.read_run(path).header.protocol.temperature == 0.2 for path in _runs(tmp_path))


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failed_smoke_lists_the_sessions_that_did_not_run(tmp_path: Path) -> None:
    """
    Given  a run of one model at two temperatures, where the arm at 0.7 fails the smoke check
    When   it runs
    Then   the two sessions of that arm are listed with the reason, and the planned count says
           how many there were, so a night that lost a setup says so at the end
    """
    harness = _Harness()

    def smoke(
        _model: str, _exchange: Exchange, _steps: tuple[Step, ...], temperature: float
    ) -> _Verdict:
        return _Verdict(temperature == 0.2, "the answer was cut off twice")

    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, replace(harness.seams(), smoke=smoke))

    assert result.planned == 4
    assert result.sessions == 2
    assert result.skipped == (
        f"{MODEL} @ 0.7 (one-request-review), repeat 1: the answer was cut off twice",
        f"{MODEL} @ 0.7 (one-request-review), repeat 2: the answer was cut off twice",
    )


# ── what a run writes ─────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_fresh_matrix_writes_one_run_file_per_session(tmp_path: Path) -> None:
    """
    Given  a run of two models, each repeated twice
    When   it runs
    Then   four run files are written, each with one header, one session and its two answers,
           named after the model, the temperature and the repeat
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, models=(MODEL, OTHER_MODEL), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused == ()
    assert (result.sessions, result.rows) == (4, 8)
    assert len(_runs(tmp_path)) == 4
    for path in _runs(tmp_path):
        stored = reader.read_run(path)
        assert len(stored.sessions) == 1
        assert [answer.text for answer in stored.answers] == ["de/de-01", "de/de-02"]
        assert stored.header.run.repeat in (1, 2)
        assert stored.header.protocol.model in (MODEL, OTHER_MODEL)


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_seeds_follow_the_repeat(tmp_path: Path) -> None:
    """
    Given  a run of two repeats with a base seed
    When   it runs
    Then   the clients and the headers carry base and base+1, so every repeat is reproducible
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, repeats=2, base_seed=100)

    runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert [client.seed for client in harness.clients] == [100, 101]
    assert sorted(reader.read_run(path).header.run.seed for path in _runs(tmp_path)) == [100, 101]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_resumed_session_sends_only_the_missing_texts(tmp_path: Path) -> None:
    """
    Given  a run file that already holds the first of two texts, with a header of the same
           settings
    When   the run is started again
    Then   only the second text is sent and a second session line is appended
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"),))

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused == ()
    assert [text for corrector in harness.correctors for text in corrector.seen] == [
        runner.WARM_UP_TEXT,
        SOURCE,
    ]
    stored = reader.read_run(item.path)
    assert len(stored.sessions) == 2
    assert [answer.text for answer in stored.answers] == ["de/de-01", "de/de-02"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_complete_run_file_is_skipped_and_reported(tmp_path: Path) -> None:
    """
    Given  a run file that already holds every text
    When   the run is planned and started
    Then   the file is reported as complete, no session is planned for it and nothing is sent
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"), _answer("de/de-02")))

    decided = _planned(matrix)
    reported: list[Path] = []
    result = runner.run_matrix(
        matrix, _records(), STAMP, harness.seams(), on_complete=reported.append
    )

    assert decided.complete == (item.path,)
    assert decided.sessions == ()
    assert decided.refused == ()
    assert reported == [item.path]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_run_resumed_on_another_day_writes_into_the_same_file(tmp_path: Path) -> None:
    """
    Given  a run file written on one day
    When   the same matrix is planned on the next day
    Then   the stored file is found by its compare key and reported complete, so a run that
           crosses midnight resumes instead of starting a second file
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"), _answer("de/de-02")))

    later = runner.plan(matrix, _records(), STAMP, harness.probe, date="2026-11-10")

    assert later.complete == (item.path,)
    assert later.sessions == ()
    assert len(_runs(tmp_path)) == 1


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_second_repeat_gets_a_file_of_its_own(tmp_path: Path) -> None:
    """
    Given  a run of two repeats whose first repeat is complete
    When   the matrix is planned
    Then   the first repeat is reported complete and the second is planned into another file,
           so a repeat is not taken for the run it repeats
    """
    matrix = _matrix(tmp_path, repeats=2)
    first, second = _planned(matrix).sessions
    _write_run(first.path, _header(matrix, first), (_answer("de/de-01"), _answer("de/de-02")))

    decided = _planned(matrix)

    assert decided.complete == (first.path,)
    assert [item.path for item in decided.sessions] == [second.path]
    assert first.path != second.path


@pytest.mark.use_case("Model_Eval_Measure")
def test_an_interrupted_second_repeat_resumes_its_own_file(tmp_path: Path) -> None:
    """
    Given  a second repeat that stopped after its first text
    When   the matrix is planned again
    Then   the second repeat resumes its own file and sends only the missing text
    """
    matrix = _matrix(tmp_path, repeats=2)
    first, second = _planned(matrix).sessions
    _write_run(first.path, _header(matrix, first), (_answer("de/de-01"), _answer("de/de-02")))
    _write_run(second.path, _header(matrix, second), (_answer("de/de-01"),))

    decided = _planned(matrix)

    assert decided.complete == (first.path,)
    assert [(item.path, item.done) for item in decided.sessions] == [
        (second.path, frozenset({"de/de-01"}))
    ]


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_same_setup_on_another_machine_gets_its_own_file(tmp_path: Path) -> None:
    """
    Given  a complete run of one setup on one machine
    When   the same setup is planned on another machine
    Then   it is planned into its own file instead of being refused as a duplicate
    """
    here = _matrix(tmp_path)
    item = _planned(here).sessions[0]
    _write_run(item.path, _header(here, item), (_answer("de/de-01"), _answer("de/de-02")))

    decided = _planned(_matrix(tmp_path, machine="other-machine"))

    assert decided.complete == ()
    assert decided.refused == ()
    other = decided.sessions[0].path
    assert other != item.path
    assert other.name.startswith(f"{DATE}_other-machine_")


@pytest.mark.use_case("Model_Eval_Measure")
def test_planning_reads_each_run_file_once(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a store that holds one run file and a matrix of four sessions
    When   the matrix is planned
    Then   the file's header is read once, not once per session
    """
    matrix = _matrix(tmp_path, models=(MODEL, OTHER_MODEL), repeats=2)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"),))
    read: list[Path] = []
    original = reader.read_header

    def counting(path: Path) -> RunHeader:
        read.append(path)
        return original(path)

    monkeypatch.setattr(reader, "read_header", counting)

    _planned(matrix)

    assert read == [item.path]


@pytest.mark.use_case("Model_Eval_Measure")
def test_two_files_of_one_run_key_are_refused(tmp_path: Path) -> None:
    """
    Given  a run file and a copy of it under another name
    When   the matrix is planned
    Then   planning is refused, naming both files, so a copy is not silently ignored
    """
    matrix = _matrix(tmp_path)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"),))
    copy = item.path.with_name("a-copy.run.jsonl")
    copy.write_bytes(item.path.read_bytes())

    decided = _planned(matrix)

    assert decided.sessions == ()
    assert len(decided.refused) == 1
    assert str(item.path) in decided.refused[0]
    assert str(copy) in decided.refused[0]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_copied_run_file_stops_the_run_before_it_sends(tmp_path: Path) -> None:
    """
    Given  a run file and a copy of it under another name
    When   the run is started
    Then   it refuses before the first request, naming both files, because one of them is a copy
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _planned(matrix).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"),))
    copy = item.path.with_name("a-copy.run.jsonl")
    copy.write_bytes(item.path.read_bytes())

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused
    assert str(item.path) in result.refused[0]
    assert str(copy) in result.refused[0]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_subset_run_records_the_whole_sets_question_hash(tmp_path: Path) -> None:
    """
    Given  a run of one language of a set of two
    When   its header is built
    Then   the header names the whole set's question hash and the number of texts it sends,
           so a subset run is not taken for another set
    """
    matrix = _matrix(tmp_path, question_sha256="f" * 64)

    header = _header(matrix, _planned(matrix).sessions[0])

    assert header.input.question_sha256 == "f" * 64
    assert header.input.texts == 2
    assert header.input.languages == ("de",)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_step_one_run_records_what_the_converter_writes_for_correct_only(
    tmp_path: Path,
) -> None:
    """
    Given  a run of the exchange asked for step ① alone
    When   its header is built
    Then   it records that one step, and the exchange and steps are the pair the converter writes
           for an old correct-only run, so the two vocabularies name one run
    """
    matrix = _matrix(tmp_path, setups=((Exchange.TEXT_FIRST, (Step.CORRECT,)),))

    header = _header(matrix, _planned(matrix).sessions[0])

    assert header.protocol.steps == (Step.CORRECT,)
    assert (header.protocol.exchange, header.protocol.steps) == exchange_and_steps(
        AnswerMode.CORRECT_ONLY
    )
    assert header.protocol.to_dict()["steps"] == ["correct"]


# ── the measurement protocol ──────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_warm_up_is_not_scored(tmp_path: Path) -> None:
    """
    Given  a run of two texts
    When   it runs
    Then   the first corrector call is the warm-up text, no answer is written for it, and the
           first answer's performance holds no warm-up request
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)

    runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert harness.correctors[0].seen[0] == runner.WARM_UP_TEXT
    answers = reader.read_run(_runs(tmp_path)[0]).answers
    assert [answer.text for answer in answers] == ["de/de-01", "de/de-02"]
    assert len(answers[0].requests) == 1


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_warm_up_that_fails_does_not_stop_the_run() -> None:
    """
    Given  a corrector whose first request never reaches the model
    When   the warm-up is sent
    Then   nothing is raised: the first scored text sends the same request, and its row is where
           the failure is recorded
    """

    class _Unreachable(Corrector):
        def correct(self, raw_text, language, *, text_format="plain") -> CorrectionOutcome:
            raise StageError("no server", kind=StageErrorKind.PROVIDER, stage="correction")

    runner._warm_up(_Unreachable())


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_sampler_runs_from_before_the_warm_up_to_after_the_last_text(tmp_path: Path) -> None:
    """
    Given  a run with a graphics memory sampler
    When   it runs
    Then   the sampler starts before the warm-up request and stops after the last text
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)

    runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert harness.log == [
        "sampler.start",
        f"correct {runner.WARM_UP_TEXT}",
        f"correct {SOURCE}",
        f"correct {SOURCE}",
        "sampler.stop",
    ]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failing_text_still_stops_the_sampler_and_unloads_the_model(tmp_path: Path) -> None:
    """
    Given  an isolating run whose text raises
    When   it runs
    Then   the sampler is stopped and the model unloaded, and the error is not swallowed
    """
    harness = _Harness(fail_on=SOURCE)
    matrix = _matrix(tmp_path, isolate=True)

    with pytest.raises(RuntimeError, match="the server went away"):
        runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert harness.log[-1] == "sampler.stop"
    assert harness.probe.unloaded == [MODEL]


# ── what stops a run ──────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_conflict_in_a_later_session_stops_the_run_before_the_first_sends(tmp_path: Path) -> None:
    """
    Given  two models, of which the second already has a run file made with another seed
    When   the run is started
    Then   it refuses before the first model sends anything, naming the second model's file,
           because every combination is checked before the first one runs
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, models=(MODEL, OTHER_MODEL))
    second = next(item for item in _planned(matrix).sessions if item.model == OTHER_MODEL)
    header = _header(matrix, second)
    _write_run(
        second.path, replace(header, run=replace(header.run, seed=1)), (_answer("de/de-01"),)
    )

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused
    assert second.path.name in result.refused[0]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []
    assert len(_runs(tmp_path)) == 1


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_model_pulled_again_between_plan_and_session_stops_before_that_session(
    tmp_path: Path,
) -> None:
    """
    Given  a run file with a header of the planned model, and a server whose digest changes
           after the run was planned
    When   the run starts its session
    Then   it refuses before the first request, because the model is not the one planned
    """
    harness = _Harness(probe=_ChangingProbe())
    matrix = _matrix(tmp_path)
    item = _planned(matrix, _Probe()).sessions[0]
    _write_run(item.path, _header(matrix, item), (_answer("de/de-01"),))

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused
    assert "model_info.digest" in result.refused[0]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []


# ── an outage is not the model's failure ──────────────────────────────────────


class _GoingProbe(_Probe):
    """A server that stops answering once ``down`` is set."""

    def __init__(self) -> None:
        super().__init__()
        self.down = False

    def version(self) -> str:
        """Answer, or refuse the connection when the server is down."""
        if self.down:
            raise OSError("connection refused")
        return super().version()


class _FailingOn(Corrector):
    """A corrector whose text *fail_on* comes back failed, and takes the server down if asked."""

    def __init__(self, fail_on: str, probe: _GoingProbe, *, outage: bool) -> None:
        self._fail_on = fail_on
        self._probe = probe
        self._outage = outage

    def correct(self, raw_text, language, *, text_format="plain") -> CorrectionOutcome:
        """Answer with the text, or fail the one text the test names."""
        if raw_text != self._fail_on:
            return CorrectionOutcome(text=raw_text)
        self._probe.down = self._outage
        return CorrectionOutcome(text=raw_text, warning="Correction failed", failed=True)


@pytest.mark.parametrize(("outage", "written"), [(True, 0), (False, 2)])
@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failed_text_is_the_models_only_while_the_server_still_answers(
    tmp_path: Path, outage: bool, written: int
) -> None:
    """
    Given  a text that comes back failed, once with the server gone afterwards and once with the
           server still answering
    When   the run is made
    Then   after an outage the run stops without writing that answer, so a resume sends the text
           again; while the server answers, the failure is the model's and is written
    """
    probe = _GoingProbe()
    harness = _Harness(probe=probe)

    def factory(client, exchange, steps, profile):
        return lambda model, temperature: _FailingOn(SOURCE, probe, outage=outage)

    seams = replace(harness.seams(), new_factory=factory)
    records = [replace(record, source=SOURCE) for record in _records()]
    matrix = _matrix(tmp_path)

    result = runner.run_matrix(matrix, records, STAMP, seams)

    answers = [answer for stored in reader.read_runs(tmp_path) for answer in stored.run.answers]
    assert len(answers) == written
    if outage:
        assert result.refused and "cannot be reached" in result.refused[0]
        assert all(not answer.failed for answer in answers)
    else:
        assert not result.refused
        assert all(answer.failed for answer in answers)


class _DeadProbe(_GoingProbe):
    """A server that is gone: it does not answer, and unloading it raises as well."""

    def unload(self, name: str) -> None:
        """Fail the way a urllib POST to a dead server does."""
        from urllib.error import URLError  # noqa: PLC0415

        raise URLError("connection refused")


@pytest.mark.use_case("Model_Eval_Measure")
def test_an_outage_keeps_its_message_when_the_unload_would_fail(tmp_path: Path) -> None:
    """
    Given  an isolating run whose first text fails and a server that is gone, so that unloading
           it raises too
    When   the run is made
    Then   the run ends with the "cannot be reached … run again" message instead of a traceback,
           and the unload was not attempted
    """
    probe = _DeadProbe()
    harness = _Harness(probe=probe)

    def factory(client, exchange, steps, profile):
        return lambda model, temperature: _FailingOn(SOURCE, probe, outage=True)

    seams = replace(harness.seams(), new_factory=factory)
    records = [replace(record, source=SOURCE) for record in _records()]
    matrix = _matrix(tmp_path, isolate=True)

    result = runner.run_matrix(matrix, records, STAMP, seams)

    assert result.refused and "cannot be reached" in result.refused[0]
    assert probe.unloaded == []
    assert "sampler.stop" in harness.log


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_file_of_a_run_is_named_by_the_prompt_its_requests_carry(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a model that has an adopted step-one prompt variant
    When   the run is planned without --prompt, with --prompt standard and with another variant
    Then   the first writes the adopted variant's file, the second the standard one — which is the
           axis default, so it carries no tag — and the third its own, so a file never holds
           answers of two prompts
    """
    from teachers_teammate.domain import model_catalog  # noqa: PLC0415

    monkeypatch.setitem(model_catalog.STEP_ONE_PROMPTS, MODEL, "keep")
    harness = _Harness()

    def planned(prompt: str) -> str:
        matrix = _matrix(tmp_path, setups=((Exchange.TEXT_FIRST, (Step.CORRECT,)),), prompt=prompt)
        return _planned(matrix, harness.probe).sessions[0].path.name

    assert "step_one_prompt-keep" in planned("")
    assert "step_one_prompt" not in planned("standard")
    assert "step_one_prompt-find" in planned("find")
    standard = _matrix(tmp_path, prompt="standard").profile_of(MODEL, 0.2)
    assert standard.correct.prompt == ""
