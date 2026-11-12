"""The run loop of ``compare_writing_models run``, driven without a server.

The loop lives outside the command line: the planning, the resume check, the seed per repeat, the
warm-up, the header and the sampler.  Every seam is replaced here, so what these tests check is
the loop itself — the order of the calls, what is written where, and what stops a run.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
import time

import pytest

from teachers_teammate.domain.correction_review import CorrectionOutcome
from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.interfaces import Corrector
from tools.model_eval import measure, protocol, results, runner, session as sessions
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.smoke import Smoke

MODEL = "gemma4:e2b"
OTHER_MODEL = "granite4.1:3b"
MACHINE = "rx9070xt-server"
DIGEST = "sha256:0123456789abcdef0123456789abcdef"
OTHER_DIGEST = "sha256:fedcba9876543210fedcba9876543210"
STAMP = protocol.Stamp(eval_version=2, fingerprint="a" * 64)
SOURCE = "Der Hund laufen schnell."


# ── the invented run ──────────────────────────────────────────────────────────


def _records() -> list[results.TestRecord]:
    """Return the two invented texts every run here is asked about."""
    return [
        results.TestRecord(
            id=name,
            language="de",
            kind="errors",
            origin="invented",
            source=SOURCE,
            reference="Der Hund läuft schnell.",
        )
        for name in ("de-01", "de-02")
    ]


def _row(identifier: str) -> results.ResultRow:
    """Return one stored row of the invented text."""
    return results.ResultRow(
        id=identifier,
        language="de",
        kind="errors",
        origin="invented",
        model=MODEL,
        temperature=0.2,
        seconds=1.0,
        failed=False,
        fallback=False,
        has_review=False,
        source=SOURCE,
        reference="Der Hund läuft schnell.",
        output="Der Hund läuft schnell.",
    )


def _matrix(tmp_path: Path, **overrides) -> runner.Matrix:
    """Return the matrix of a run over the invented texts, with *overrides* applied."""
    values = {
        "out": tmp_path / "out",
        "testset": tmp_path / "set",
        "languages": ("de",),
        "models": (MODEL,),
        "temperatures": (0.2,),
        "modes": (AnswerMode.REVIEW,),
        "repeats": 1,
        "base_seed": 20261109,
        "timeout": 5.0,
        "isolate": False,
        "gpu_sampler": None,
        "machine": MACHINE,
    }
    values.update(overrides)
    return runner.Matrix(**values)


def _session(matrix: runner.Matrix, **overrides) -> runner.Session:
    """Return the session of one model, temperature, mode and repeat of *matrix*."""
    values = {
        "mode": AnswerMode.REVIEW,
        "model": MODEL,
        "temperature": 0.2,
        "repeat": 1,
        "done": frozenset(),
    }
    values.update(overrides)
    path = matrix.out / results.result_file_name(
        values["model"],
        values["temperature"],
        values["mode"],
        values["repeat"] if matrix.repeats > 1 else None,
    )
    return runner.Session(
        mode=values["mode"],
        model=values["model"],
        temperature=values["temperature"],
        repeat=values["repeat"],
        seed=matrix.seed_of(values["repeat"]),
        rows_path=path,
        header_path=path.with_suffix(".header.json"),
        done=values["done"],
    )


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

    def loaded(self) -> tuple[measure.LoadedModel, ...]:
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

    def __init__(self, seed: int | None) -> None:
        self.seed = seed
        self.requests: list[measure.RequestStats] = []

    def drain(self) -> tuple[measure.RequestStats, ...]:
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
        self._client.requests.append(measure.RequestStats.failure(MODEL, 0.1))
        if raw_text == self._fail_on:
            raise RuntimeError("the server went away")
        return CorrectionOutcome(text=raw_text)


class _Sampler:
    """A graphics memory sampler that records when it was started and stopped."""

    def __init__(self, log: list[str]) -> None:
        self._log = log
        self.baseline_mb = 100

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

    def new_client(self, seed: int | None) -> _Client:
        """Return a recording client for *seed*."""
        client = _Client(seed)
        self.clients.append(client)
        return client

    def new_factory(
        self, client: _Client, mode: AnswerMode, profile: RequestProfile
    ) -> Callable[[str, float], Corrector]:
        """Return a factory whose correctors answer with the text they were given."""
        self.profiles.append(profile)

        def build(model: str, temperature: float) -> Corrector:
            corrector = _Scripted(client, self.log, fail_on=self._fail_on)
            self.correctors.append(corrector)
            return corrector

        return build

    def new_sampler(self) -> _Sampler:
        """Return a sampler that writes into this run's log."""
        return _Sampler(self.log)

    def seams(self) -> runner.Seams:
        """Return the seams of this run."""
        return runner.Seams(
            probe=self.probe,
            new_client=self.new_client,
            new_factory=self.new_factory,
            new_sampler=self.new_sampler,
            now=lambda: "2026-11-09T18:00:00+00:00",
            revision="cfdc0ea",
        )


def _write_session(matrix: runner.Matrix, item: runner.Session, probe: _Probe, **overrides) -> None:
    """Store one earlier session next to the results file of *item*."""
    identity = runner.identity(matrix, item, STAMP, probe, _records())
    identity.update(overrides)
    sessions.append_session(
        item.header_path,
        measure.session_header(
            identity,
            preparation=measure.Preparation(loaded_before=(), vram_baseline_mb=None),
            revision="cfdc0ea",
            now="2026-11-09T18:00:00+00:00",
        ),
    )


# ── the smoke check ───────────────────────────────────────────────────────────


def _smoke(passed: bool = True) -> Callable[[str, AnswerMode, float], Smoke]:
    """Return a smoke seam that records the setups it was asked about."""
    return lambda _model, _mode, _temperature: Smoke(
        passed, "no sign of a systematic fault; gate 1 decides"
    )


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_smoke_runs_once_per_setup_and_a_temperature_arm_is_its_own(tmp_path: Path) -> None:
    """
    Given  a run of one model at two temperatures, each repeated twice
    When   it runs with a smoke seam
    Then   the smoke check is asked once per setup — model, mode and temperature, not per repeat
           — and every session of a setup it passed runs
    """
    harness = _Harness()
    asked: list[tuple[str, AnswerMode, float]] = []

    def smoke(model: str, mode: AnswerMode, temperature: float) -> Smoke:
        asked.append((model, mode, temperature))
        return _smoke()(model, mode, temperature)

    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, replace(harness.seams(), smoke=smoke))

    assert asked == [(MODEL, AnswerMode.REVIEW, 0.2), (MODEL, AnswerMode.REVIEW, 0.7)]
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

    def smoke(_model: str, _mode: AnswerMode, temperature: float) -> Smoke:
        return _smoke(passed=temperature == 0.2)(_model, _mode, temperature)

    matrix = _matrix(tmp_path, temperatures=(0.2, 0.7), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, replace(harness.seams(), smoke=smoke))

    assert (result.sessions, result.rows) == (2, 4)
    assert sorted(path.name for path in matrix.out.glob("*.jsonl")) == sorted(
        results.result_file_name(MODEL, 0.2, repeat=repeat) for repeat in (1, 2)
    )


# ── what a run writes ─────────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_fresh_matrix_writes_one_header_per_session(tmp_path: Path) -> None:
    """
    Given  a run of two models, each repeated twice
    When   it runs
    Then   four results files are written, each with one header, named after model, temperature
           and repeat
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, models=(MODEL, OTHER_MODEL), repeats=2)

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused == ()
    assert (result.sessions, result.rows) == (4, 8)
    assert sorted(path.name for path in matrix.out.glob("*.jsonl")) == sorted(
        results.result_file_name(model, 0.2, repeat=repeat)
        for model in (MODEL, OTHER_MODEL)
        for repeat in (1, 2)
    )
    for path in matrix.out.glob("*.jsonl"):
        assert len(sessions.read_sessions(path.with_suffix(".header.json"))) == 1


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_seeds_follow_the_repeat(tmp_path: Path) -> None:
    """
    Given  a run of two repeats with a base seed, and one that sends no seed at all
    When   each runs
    Then   the headers and the clients carry base, base+1 — and ``None`` everywhere without a seed
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, repeats=2, base_seed=100)

    runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert [client.seed for client in harness.clients] == [100, 101]
    assert [
        entry["seed"]
        for path in sorted(matrix.out.glob("*.header.json"))
        for entry in sessions.read_sessions(path)
    ] == [100, 101]

    unseeded = _Harness()
    other = _matrix(tmp_path / "unseeded", repeats=2, base_seed=None)

    runner.run_matrix(other, _records(), STAMP, unseeded.seams())

    assert [client.seed for client in unseeded.clients] == [None, None]
    assert all(
        entry["seed"] is None
        for path in other.out.glob("*.header.json")
        for entry in sessions.read_sessions(path)
    )


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_resumed_session_sends_only_the_missing_texts(tmp_path: Path) -> None:
    """
    Given  a results file that already holds the first of two texts, with a header of the same
           settings
    When   the run is started again
    Then   only the second text is sent and a second header is appended
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    item = _session(matrix)
    matrix.out.mkdir(parents=True)
    results.write_rows(item.rows_path, [_row("de/de-01")])
    _write_session(matrix, item, harness.probe)

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused == ()
    assert [text for corrector in harness.correctors for text in corrector.seen] == [
        runner.WARM_UP_TEXT,
        SOURCE,
    ]
    assert len(sessions.read_sessions(item.header_path)) == 2
    assert [row.id for row in results.read_rows(item.rows_path)] == ["de/de-01", "de/de-02"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_complete_results_file_is_skipped_and_reported(tmp_path: Path) -> None:
    """
    Given  a results file that already holds every text
    When   the run is planned and started
    Then   the file is reported as complete, no session is planned for it and nothing is sent
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)
    path = matrix.out / results.result_file_name(MODEL, 0.2)
    matrix.out.mkdir(parents=True)
    results.write_rows(path, [_row("de/de-01"), _row("de/de-02")])

    decided = runner.plan(matrix, _records(), STAMP, harness.probe)
    reported: list[Path] = []
    result = runner.run_matrix(
        matrix, _records(), STAMP, harness.seams(), on_complete=reported.append
    )

    assert decided.complete == (path,)
    assert decided.sessions == ()
    assert decided.refused == ()
    assert reported == [path]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []


# ── the measurement protocol ──────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_warm_up_is_not_scored(tmp_path: Path) -> None:
    """
    Given  a run of two texts
    When   it runs
    Then   the first corrector call is the warm-up text, no row is written for it, and the first
           row's performance holds no warm-up request
    """
    harness = _Harness()
    matrix = _matrix(tmp_path)

    runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert harness.correctors[0].seen[0] == runner.WARM_UP_TEXT
    rows = results.read_rows(matrix.out / results.result_file_name(MODEL, 0.2))
    assert [row.id for row in rows] == ["de/de-01", "de/de-02"]
    assert rows[0].performance is not None
    assert len(rows[0].performance.requests) == 1


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
    Given  two models, of which the second already has a session made with another seed
    When   the run is started
    Then   it refuses before the first model sends anything, naming the second model's file,
           because every combination is checked before the first one runs
    """
    harness = _Harness()
    matrix = _matrix(tmp_path, models=(MODEL, OTHER_MODEL))
    second = _session(matrix, model=OTHER_MODEL)
    matrix.out.mkdir(parents=True)
    results.write_rows(second.rows_path, [_row("de/de-01")])
    _write_session(matrix, second, harness.probe, seed=1)

    result = runner.run_matrix(matrix, _records(), STAMP, harness.seams())

    assert result.refused
    assert second.rows_path.name in result.refused[0]
    assert (result.sessions, result.rows) == (0, 0)
    assert harness.correctors == []
    assert not (matrix.out / results.result_file_name(MODEL, 0.2)).exists()


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_model_pulled_again_between_plan_and_session_stops_before_that_session(
    tmp_path: Path,
) -> None:
    """
    Given  a results file with a header of the planned model, and a server whose digest changes
           after the run was planned
    When   the run starts its session
    Then   it refuses before the first request, because the model is not the one planned
    """
    harness = _Harness(probe=_ChangingProbe())
    matrix = _matrix(tmp_path)
    item = _session(matrix)
    matrix.out.mkdir(parents=True)
    results.write_rows(item.rows_path, [_row("de/de-01")])
    _write_session(matrix, item, _Probe())

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


@pytest.mark.parametrize(("outage", "rows"), [(True, 0), (False, 2)])
@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failed_text_is_the_models_only_while_the_server_still_answers(
    tmp_path: Path, outage: bool, rows: int
) -> None:
    """
    Given  a text that comes back failed, once with the server gone afterwards and once with the
           server still answering
    When   the run is made
    Then   after an outage the run stops without writing that row, so a resume sends the text
           again; while the server answers, the failure is the model's and is written
    """
    probe = _GoingProbe()
    harness = _Harness(probe=probe)

    def factory(client, mode, profile):
        return lambda model, temperature: _FailingOn(SOURCE, probe, outage=outage)

    seams = replace(harness.seams(), new_factory=factory)
    records = [replace(record, source=SOURCE) for record in _records()]
    matrix = _matrix(tmp_path)

    result = runner.run_matrix(matrix, records, STAMP, seams)

    written = results.read_all(matrix.out)
    assert len(written) == rows
    if outage:
        assert result.refused and "cannot be reached" in result.refused[0]
        assert all(not row.failed for row in written)
    else:
        assert not result.refused
        assert all(row.failed for row in written)


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

    def factory(client, mode, profile):
        return lambda model, temperature: _FailingOn(SOURCE, probe, outage=True)

    seams = replace(harness.seams(), new_factory=factory)
    records = [replace(record, source=SOURCE) for record in _records()]
    matrix = _matrix(tmp_path, isolate=True)

    result = runner.run_matrix(matrix, records, STAMP, seams)

    assert result.refused and "cannot be reached" in result.refused[0]
    assert probe.unloaded == []
    assert "sampler.stop" in harness.log
