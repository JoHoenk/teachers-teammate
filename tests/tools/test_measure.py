"""Unit tests for measuring a run: recording client, server probe, memory sampler, protocol."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from teachers_teammate.infrastructure.ollama_utils import ChatStats, OllamaClient
from tools.model_eval import protocol, session
from tools.model_eval.measure import (
    GpuSampler,
    MeasuredRun,
    Preparation,
    RecordingClient,
    SampleParser,
    ServerBusyError,
    ServerProbe,
    VramSeries,
    run_identity,
    same_model,
    session_header,
    tool_revision,
)
from tools.model_eval.performance import LoadedModel, Performance, RequestStats

_STATS = ChatStats(
    done_reason="stop",
    prompt_eval_count=500,
    eval_count=300,
    total_duration=6_000_000_000,
    load_duration=1_000_000_000,
    prompt_eval_duration=500_000_000,
    eval_duration=3_000_000_000,
)


def _recorded(client: RecordingClient, **kwargs) -> list[dict | None]:
    """Send one request through *client* with the base class replaced; return the options sent."""
    sent: list[dict | None] = []

    def fake(self, model, prompt, *, options=None, **_):
        sent.append(options)
        return "answer", _STATS

    with patch.object(OllamaClient, "chat_with_stats", fake):
        client.chat_with_stats("m", "hi", **kwargs)
    return sent


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_client_adds_its_seed_to_the_options_of_the_app() -> None:
    """
    Given  a recording client with a seed and a request that carries the app's options
    When   the request is sent
    Then   the options keep the temperature and window and gain the seed
    """
    client = RecordingClient("http://x:11434", seed=42)

    options = _recorded(client, options={"temperature": 0.2, "num_ctx": 8192})[0]

    assert options == {"temperature": 0.2, "num_ctx": 8192, "seed": 42}


@pytest.mark.use_case("Model_Eval_Measure")
def test_an_identical_repeat_gets_the_next_seed() -> None:
    """
    Given  a recording client with a seed
    When   the same request is sent twice, and another request once
    Then   the first send keeps the seed and the repeat gets the next one, because the app's
           second try after a cut-off answer samples afresh; the other request keeps the seed
    """
    client = RecordingClient("http://x:11434", seed=42)

    first = _recorded(client, options={"temperature": 0.2})[0]
    again = _recorded(client, options={"temperature": 0.2})[0]
    other = _recorded(client, options={"temperature": 0.2}, system="another prompt")[0]

    assert first == {"temperature": 0.2, "seed": 42}
    assert again == {"temperature": 0.2, "seed": 43}
    assert other == {"temperature": 0.2, "seed": 42}


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_client_without_a_seed_sends_the_options_unchanged() -> None:
    """
    Given  a recording client without a seed (as the app is)
    When   a request is sent
    Then   the options are exactly the app's
    """
    client = RecordingClient("http://x:11434")

    options = _recorded(client, options={"temperature": 0.2})[0]

    assert options == {"temperature": 0.2}


@pytest.mark.use_case("Model_Eval_Measure")
def test_every_request_is_recorded_with_the_durations_the_server_reported() -> None:
    """
    Given  a request that Ollama answered with token counts and durations
    When   the recorded requests are drained
    Then   the record has the tokens, the durations in seconds and the tool's own waiting time
    """
    ticks = iter([10.0, 14.5])
    client = RecordingClient("http://x:11434", clock=lambda: next(ticks))
    _recorded(client)

    request = client.drain()[0]

    assert (request.prompt_tokens, request.answer_tokens) == (500, 300)
    assert (request.load_s, request.prompt_eval_s, request.generation_s) == (1.0, 0.5, 3.0)
    assert request.wall_s == 4.5
    assert request.done_reason == "stop"


@pytest.mark.use_case("Model_Eval_Measure")
def test_draining_forgets_what_it_returned() -> None:
    """
    Given  a client that recorded one request
    When   it is drained twice
    Then   the second call returns nothing, so one text never sees another's requests
    """
    client = RecordingClient("http://x:11434")
    _recorded(client)

    assert len(client.drain()) == 1
    assert client.drain() == ()


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_failed_request_is_recorded_and_raised_again() -> None:
    """
    Given  a request that times out
    When   it is sent through the recording client
    Then   the error still reaches the corrector, and the waiting time is on record as a failure
    """
    client = RecordingClient("http://x:11434")

    def boom(self, *args, **kwargs):
        raise TimeoutError("too slow")

    with patch.object(OllamaClient, "chat_with_stats", boom), pytest.raises(TimeoutError):
        client.chat_with_stats("m", "hi")

    request = client.drain()[0]
    assert request.failed
    assert request.answer_tokens == 0


# ── the server ────────────────────────────────────────────────────────────────


class _FakeServer:
    """A server that answers the probe's requests from a state it keeps."""

    def __init__(self, loaded: list[dict] | None = None, *, stubborn: bool = False) -> None:
        self.loaded = list(loaded or [])
        self.stubborn = stubborn
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method: str, path: str, body: dict | None) -> dict:
        self.calls.append((method, path, body))
        if path == "/api/version":
            return {"version": "0.40.15"}
        if path == "/api/tags":
            return {
                "models": [
                    {
                        "name": "granite4.1:3b",
                        "digest": "6fd349357287abc",
                        "details": {
                            "quantization_level": "Q4_K_M",
                            "parameter_size": "3.4B",
                            "family": "granite",
                        },
                    }
                ]
            }
        if path == "/api/ps":
            return {"models": list(self.loaded)}
        if path == "/api/generate" and body and body.get("keep_alive") == 0:
            if not self.stubborn:
                self.loaded = [m for m in self.loaded if m["name"] != body["model"]]
        return {}


def _ps(name: str, size: int = 4_000_000_000, vram: int = 4_000_000_000) -> dict:
    return {"name": name, "size": size, "size_vram": vram, "context_length": 8192}


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_probe_reads_version_digest_and_loaded_models() -> None:
    """
    Given  a server with one installed model and one loaded model, 75 % of it on the card
    When   the probe is asked
    Then   it returns the version, the digest of the tag and the share on the card
    """
    server = _FakeServer([_ps("granite4.1:3b", size=4_000_000_000, vram=3_000_000_000)])
    probe = ServerProbe("http://x:11434", fetch=server)

    info = probe.model_info("granite4.1:3b")
    loaded = probe.loaded()[0]

    assert probe.version() == "0.40.15"
    assert info["digest"] == "6fd349357287abc"
    assert info["quantization"] == "Q4_K_M"
    assert loaded.on_card_share == 0.75
    assert probe.model_info("not-installed:1b") == {}


@pytest.mark.use_case("Model_Eval_Measure")
def test_unloading_everything_asks_for_each_model_and_waits_until_none_is_left() -> None:
    """
    Given  a server with two models loaded
    When   it is asked to unload everything
    Then   each model gets a request with keep_alive 0 and the call returns once none is loaded
    """
    server = _FakeServer([_ps("a:1b"), _ps("b:2b")])
    probe = ServerProbe("http://x:11434", fetch=server)

    probe.unload_all(sleep=lambda _: None)

    asked = [body["model"] for _, path, body in server.calls if path == "/api/generate" and body]
    assert sorted(asked) == ["a:1b", "b:2b"]
    assert server.loaded == []


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_server_that_will_not_become_idle_is_refused() -> None:
    """
    Given  a server that keeps a model loaded however often it is asked to drop it
    When   it is asked to unload everything
    Then   it raises ServerBusyError naming the model, instead of measuring a busy server
    """
    server = _FakeServer([_ps("a:1b")], stubborn=True)
    probe = ServerProbe("http://x:11434", fetch=server)
    ticks = iter(range(0, 1000, 20))

    with pytest.raises(ServerBusyError, match="a:1b"):
        probe.unload_all(wait_s=30.0, sleep=lambda _: None, clock=lambda: float(next(ticks)))


@pytest.mark.use_case("Model_Eval_Measure")
@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("qwen3.5:4b", "qwen3.5:4b", True),
        ("granite4.1", "granite4.1:latest", True),
        ("gemma4:e2b", "gemma4:e4b", False),
    ],
)
def test_two_tags_name_the_same_model_when_they_differ_only_by_latest(
    first: str, second: str, same: bool
) -> None:
    """
    Given  two model tags
    When   they are compared
    Then   "x" and "x:latest" are one model and different tags are not
    """
    assert same_model(first, second) is same


# ── the graphics card ─────────────────────────────────────────────────────────

_AMD_OUTPUT = """PID 4242
'CTRL' + 'C' to stop watching output:
timestamp,gpu,xcp,vram_used,vram_free,vram_total,vram_percent
1794248980,0,0,682,15622,16304,4.18

timestamp,gpu,xcp,vram_used,vram_free,vram_total,vram_percent
1794248981,0,0,5100,11204,16304,31.28
"""


@pytest.mark.use_case("Model_Eval_Measure")
def test_amd_output_is_read_by_column_name_and_the_pid_is_kept() -> None:
    """
    Given  the lines amd-smi prints, with a header before every sample
    When   they are fed to the parser
    Then   the samples come out in MB, the headers and notes yield nothing, and the pid is kept
    """
    parser = SampleParser()

    found = [parser.feed(line) for line in _AMD_OUTPUT.splitlines()]

    assert [value for value in found if value is not None] == [682, 5100]
    assert parser.pid == 4242


@pytest.mark.use_case("Model_Eval_Measure")
def test_nvidia_output_is_one_number_per_line() -> None:
    """
    Given  the lines nvidia-smi prints (one bare number of MB per card and second)
    When   they are fed to the parser
    Then   each number is a sample
    """
    parser = SampleParser()

    assert [parser.feed(line) for line in ["PID 7", "0", "6900", ""]] == [None, 0, 6900, None]


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_peak_is_the_highest_sample_inside_the_window() -> None:
    """
    Given  samples before, inside and after a time window
    When   the peak of the window is asked for
    Then   only the samples inside count, and an empty window has no peak
    """
    series = VramSeries()
    for when, mb in [(1.0, 9000), (5.0, 4000), (6.0, 6500), (9.0, 8000)]:
        series.add(when, mb)

    assert series.peak(4.0, 7.0) == 6500
    assert series.peak(20.0, 30.0) is None


class _FakeProcess:
    """A process that prints fixed lines and notes that it was terminated."""

    def __init__(self, lines: str) -> None:
        self.stdout = iter(lines.splitlines(keepends=True))
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        """Report that the process has ended."""
        return 0

    def kill(self) -> None:
        self.killed = True


def _sampler(
    lines: str, ssh: tuple[str, ...] = ()
) -> tuple[GpuSampler, list[list[str]], list[list[str]], _FakeProcess]:
    """Return a sampler on a fake process, with the commands it started and ran."""
    started: list[list[str]] = []
    ran: list[list[str]] = []
    process = _FakeProcess(lines)

    def popen(args, **_):
        started.append(args)
        return process

    def run(args, **_):
        ran.append(args)
        return SimpleNamespace(returncode=0)

    return GpuSampler("amd", ssh, popen=popen, run=run), started, ran, process


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_sampler_reports_the_idle_memory_as_its_baseline() -> None:
    """
    Given  a sampler whose command prints an idle sample first
    When   it is started
    Then   the first sample is the baseline
    """
    sampler, _, _, _ = _sampler(_AMD_OUTPUT)

    sampler.start(wait_s=2.0)

    assert sampler.baseline_mb == 682


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_remote_sampler_runs_through_the_ssh_command_and_is_killed_by_pid() -> None:
    """
    Given  a sampler for a machine reached through an SSH command
    When   it is started and stopped
    Then   the command runs through SSH, the local process is terminated and the remote one is
           killed by the pid it printed, after a check of its command line
    """
    sampler, started, ran, process = _sampler(_AMD_OUTPUT, ssh=("ssh.exe", "user@host"))

    sampler.start(wait_s=2.0)
    sampler.stop()

    assert started[0][:2] == ["ssh.exe", "user@host"]
    assert "amd-smi monitor" in started[0][2]
    assert process.terminated
    assert ran[0][:2] == ["ssh.exe", "user@host"]
    assert "grep -q amd-smi /proc/4242/cmdline && kill 4242" in ran[0][2]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_local_sampler_runs_through_a_shell() -> None:
    """
    Given  a sampler without an SSH command
    When   it is started
    Then   the command runs on this machine through sh -c
    """
    sampler, started, _, _ = _sampler(_AMD_OUTPUT)

    sampler.start(wait_s=2.0)

    assert started[0][:2] == ["sh", "-c"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_sampler_that_prints_nothing_is_an_error() -> None:
    """
    Given  a command that prints no sample (amd-smi missing, a wrong host)
    When   the sampler is started
    Then   it raises instead of letting a run go on without memory figures
    """
    sampler, _, _, _ = _sampler("")

    with pytest.raises(RuntimeError, match="printed nothing"):
        sampler.start(wait_s=0.3)


@pytest.mark.use_case("Model_Eval_Measure")
def test_an_unknown_graphics_vendor_is_refused() -> None:
    """
    Given  a vendor the tool has no command for
    When   a sampler is created
    Then   a ValueError names the vendors it knows
    """
    with pytest.raises(ValueError, match="amd, nvidia"):
        GpuSampler("intel")


# ── one measured run ──────────────────────────────────────────────────────────


class _FakeRecorder:
    """A recorder that hands out the requests it was given, once."""

    seed = 7

    def __init__(self, requests: tuple[RequestStats, ...] = ()) -> None:
        self.pending = requests

    def drain(self) -> tuple[RequestStats, ...]:
        taken, self.pending = self.pending, ()
        return taken


class _FakeSampler:
    baseline_mb = 700

    def __init__(self, peak: int | None) -> None:
        self._peak = peak
        self.started = self.stopped = False
        self.window: tuple[float, float] | None = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def peak_mb(self, since: float, until: float) -> int | None:
        self.window = (since, until)
        return self._peak


def _request(answer: int = 100) -> RequestStats:
    return RequestStats("m:1b", 200, answer, 0.0, 0.1, 1.0, 1.2, 1.3, "stop")


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_text_that_ran_alone_is_comparable_and_carries_its_requests_and_memory() -> None:
    """
    Given  a server with only the model under test loaded, and a sampler that saw 6.2 GB
    When   a text is begun and ended
    Then   the text is not shared, carries its requests, seed and memory peak and baseline
    """
    server = _FakeServer([_ps("m:1b")])
    recorder = _FakeRecorder((_request(),))
    sampler = _FakeSampler(6200)
    run = MeasuredRun(
        client=recorder,
        probe=ServerProbe("http://x", fetch=server),
        model="m:1b",
        sampler=sampler,
        repeat=2,
        clock=iter([10.0, 20.0]).__next__,
    )
    run.prepare(lambda: None, isolate=False)
    run.begin()
    recorder.pending = (_request(),)

    performance = run.end()

    assert not performance.shared
    assert performance.request_count == 1
    assert (performance.seed, performance.repeat) == (7, 2)
    assert (performance.vram_peak_mb, performance.vram_baseline_mb) == (6200, 700)
    assert performance.vram_used_mb == 5500
    assert sampler.window == (10.0, 20.0)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_text_is_shared_when_another_model_was_loaded() -> None:
    """
    Given  a server that also holds another model while the text runs
    When   a text is begun and ended
    Then   the text is marked shared, because its timing is not comparable
    """
    server = _FakeServer([_ps("m:1b"), _ps("other:7b")])
    run = MeasuredRun(
        client=_FakeRecorder(), probe=ServerProbe("http://x", fetch=server), model="m:1b"
    )
    run.begin()

    assert run.end().shared


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_tag_and_its_latest_form_are_the_same_model_for_sharing() -> None:
    """
    Given  a model run as "granite4.1" that the server lists as "granite4.1:latest"
    When   a text is begun and ended
    Then   the text is not shared
    """
    server = _FakeServer([_ps("granite4.1:latest")])
    run = MeasuredRun(
        client=_FakeRecorder(), probe=ServerProbe("http://x", fetch=server), model="granite4.1"
    )
    run.begin()

    assert not run.end().shared


@pytest.mark.use_case("Model_Eval_Measure")
def test_without_a_sampler_there_is_no_memory_figure() -> None:
    """
    Given  a run without a sampler
    When   a text is ended
    Then   the memory fields are empty and the used memory is unknown
    """
    run = MeasuredRun(
        client=_FakeRecorder(),
        probe=ServerProbe("http://x", fetch=_FakeServer([_ps("m:1b")])),
        model="m:1b",
    )
    run.begin()

    performance = run.end()

    assert performance.vram_peak_mb is None
    assert performance.vram_used_mb is None


@pytest.mark.use_case("Model_Eval_Measure")
def test_preparing_with_isolation_unloads_first_then_warms_up_and_forgets_the_warm_up() -> None:
    """
    Given  a server holding another model, and a warm-up that makes one request
    When   the run is prepared with isolation
    Then   the other model is unloaded before anything runs, the sampler is started, the warm-up
           ran, and its request is not left for the first text
    """
    server = _FakeServer([_ps("other:7b")])
    recorder = _FakeRecorder()
    sampler = _FakeSampler(None)
    order: list[str] = []

    def warm_up() -> None:
        order.append("warm-up")
        recorder.pending = (_request(),)

    run = MeasuredRun(
        client=recorder,
        probe=ServerProbe("http://x", fetch=server),
        model="m:1b",
        sampler=sampler,
        sleep=lambda _: None,
    )
    preparation = run.prepare(warm_up, isolate=True)

    assert order == ["warm-up"]
    assert sampler.started
    assert server.loaded == []
    assert recorder.drain() == ()
    assert preparation.vram_baseline_mb == 700
    assert preparation.loaded_before == ()


@pytest.mark.use_case("Model_Eval_Measure")
def test_finishing_stops_the_sampler_and_unloads_the_model_on_request() -> None:
    """
    Given  a prepared run with a sampler
    When   it is finished with unloading asked for
    Then   the sampler is stopped and the model gets a keep_alive 0 request
    """
    server = _FakeServer([_ps("m:1b")])
    sampler = _FakeSampler(None)
    run = MeasuredRun(
        client=_FakeRecorder(),
        probe=ServerProbe("http://x", fetch=server),
        model="m:1b",
        sampler=sampler,
    )

    run.finish(unload=True)

    assert sampler.stopped
    assert server.loaded == []


# ── the record of a run ───────────────────────────────────────────────────────


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_revision_names_the_commit_and_marks_uncommitted_changes() -> None:
    """
    Given  a git that reports a commit and a dirty working tree
    When   the revision is asked for
    Then   it is the short commit with +dirty
    """

    def run(args, **_):
        out = {"rev-parse": "abc1234\n", "status": " M tools/x.py\n"}[args[1]]
        return SimpleNamespace(returncode=0, stdout=out)

    assert tool_revision(run) == "abc1234+dirty"


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_revision_is_unknown_outside_a_git_checkout() -> None:
    """
    Given  a git that fails (a packaged copy of the tools)
    When   the revision is asked for
    Then   it is "unknown" instead of an error
    """

    def run(args, **_):
        return SimpleNamespace(returncode=128, stdout="")

    assert tool_revision(run) == "unknown"


_STAMP = protocol.Stamp(eval_version=7, fingerprint="a" * 64)


def _identity(**overrides) -> dict:
    """Return the identity of a run of the three languages in review mode, with *overrides*."""
    values = {
        "stamp": _STAMP,
        "probe": ServerProbe("http://x", fetch=_FakeServer()),
        "model": "granite4.1:3b",
        "mode": "review",
        "languages": ["de", "en", "fr"],
        "temperature": 0.2,
        "request_profile": {"compact_json": False, "temperature": 0.2},
        "num_ctx": 8192,
        "timeout": 600.0,
        "seed": 20261109,
        "repeat": 1,
        "isolate": False,
        "sampler_vendor": "",
        "machine": "rx9070xt",
        "testset": "set-labelled",
        "testset_sha256": "b" * 64,
        "texts": 27,
    }
    values.update(overrides)
    return run_identity(**values)


def _session(identity: dict | None = None, **overrides) -> dict:
    """Return one session header of *identity*, with *overrides*."""
    values = {
        "preparation": Preparation(loaded_before=(), vram_baseline_mb=None),
        "revision": "abc1234",
        "now": "2026-11-09T19:00:00+00:00",
    }
    values.update(overrides)
    return session_header(_identity() if identity is None else identity, **values)


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_header_records_what_a_result_needs_to_be_reproduced(tmp_path: Path) -> None:
    """
    Given  a probe of a server and the settings of a run
    When   the header is built and stored twice (a run that was resumed)
    Then   it holds the digest, version, seed, window, timeout and idle state, and the file lists
           both
    """
    header = _session(
        _identity(mode="text", isolate=True, sampler_vendor="amd"),
        preparation=Preparation(loaded_before=(), vram_baseline_mb=682),
    )
    path = tmp_path / "run.header.json"

    session.append_session(path, header)
    session.append_session(path, header)

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert len(stored) == 2
    assert stored[0]["model_info"]["digest"] == "6fd349357287abc"
    assert stored[0]["ollama_version"] == "0.40.15"
    assert (stored[0]["seed"], stored[0]["num_ctx"], stored[0]["isolated"]) == (
        20261109,
        8192,
        True,
    )
    assert (stored[0]["timeout"], stored[0]["testset_sha256"]) == (600.0, "b" * 64)
    assert stored[0]["vram_baseline_mb"] == 682
    assert session.read_sessions(path)[-1] == stored[-1]


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_header_records_the_stamp_it_was_given_and_the_languages_hash() -> None:
    """
    Given  a stamp that the protocol check returned, and a run of three languages in review mode
    When   its identity is built
    Then   the header carries that very stamp (not a recomputed one), the instructions hash of
           those languages in that mode, and the answer format version
    """
    header = _identity()

    assert (header["eval_version"], header["fingerprint"]) == (7, "a" * 64)
    assert header["instructions_sha256"] == protocol.instructions_sha256(
        ["de", "en", "fr"], mode="review"
    )
    assert header["answer_format_version"] >= 1


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_header_names_the_provider_and_what_only_an_ollama_run_has() -> None:
    """
    Given  the identity of a run on Ollama, and one of an online provider on no machine of ours
    When   both are built
    Then   the first names Ollama, its machine and its server version; the second names its
           provider and has neither a machine nor a server version
    """
    local = _identity()
    online = _identity(provider="openai", machine=None)

    assert (local["provider"], local["machine"], local["ollama_version"]) == (
        "ollama",
        "rx9070xt",
        "0.40.15",
    )
    assert (online["provider"], online["machine"], online["ollama_version"]) == (
        "openai",
        None,
        None,
    )


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_text_mode_header_has_another_instructions_hash_than_a_review_header() -> None:
    """
    Given  the same languages asked in review mode and in text mode
    When   both identities are built
    Then   their instructions hashes differ, because the answer format is part of the request
    """
    assert _identity()["instructions_sha256"] != _identity(mode="text")["instructions_sha256"]


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_identity_and_the_session_facts_cover_the_resume_keys_exactly() -> None:
    """
    Given  the keys a resumed run has to agree on and the keys that may differ between sessions
    When   a session header is built
    Then   the identity holds every resume key (and the model's details), and the session adds
           exactly the keys that may differ, so a field cannot be added to one list and forgotten
           in the other
    """
    identity = _identity()
    header = _session(identity)

    assert set(identity) == set(session.RESUME_KEYS) | {"model_info"}
    assert set(header) == set(identity) | set(session.SESSION_KEYS)


@pytest.mark.use_case("Model_Eval_Measure")
def test_performance_survives_a_round_trip_through_json() -> None:
    """
    Given  a performance record with requests and a loaded model
    When   it is turned into a dict and back
    Then   it is equal to the original
    """
    performance = Performance(
        requests=(_request(),),
        loaded=(LoadedModel("m:1b", 4_000_000_000, 3_000_000_000, 8192),),
        shared=True,
        vram_peak_mb=6200,
        vram_baseline_mb=700,
        seed=3,
        repeat=2,
    )

    assert Performance.from_dict(json.loads(json.dumps(performance.to_dict()))) == performance


class _StubbornProcess(_FakeProcess):
    """A process that ignores terminate: wait times out until it is killed."""

    def wait(self, timeout: float | None = None) -> int:
        import subprocess  # noqa: PLC0415

        raise subprocess.TimeoutExpired("sampler", timeout or 0)


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_sampler_that_ignores_terminate_is_killed() -> None:
    """
    Given  a sampler process that does not end when asked to
    When   the sampler is stopped
    Then   it is killed after the wait, so nothing keeps sampling into the next run
    """
    process = _StubbornProcess(_AMD_OUTPUT)
    sampler = GpuSampler("amd", popen=lambda *_, **__: process, run=lambda *_, **__: None)
    sampler.start(wait_s=2.0)

    sampler.stop()

    assert process.terminated
    assert process.killed
