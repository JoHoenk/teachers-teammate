"""The command line of ``verify_model_catalog structured-output`` and its ``recheck``.

The check itself is tested in ``test_structured_check.py``; what is left here is what belongs to
the command: the header that says what a verdict is valid for, the default window, the saved
answers and the re-run that judges them again, mixed ``--tag`` lists, and the margin warning.

Everything runs offline: the server is a scripted chat port and a scripted probe, and the answers
of ``recheck`` come from a file the live run wrote.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import re

import pytest

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.exceptions import OCRError
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from tools import verify_model_catalog
from tools.model_eval import defaults, gate1_answers, gate_one, measure, protocol
from tools.model_eval.agent_client import AgentCard, AgentError, AgentInfo, WindowStats
from tools.model_eval.model_fit import (
    CardReading,
    FitRecord,
    FitStore,
    OllamaReading,
    spilled,
)

CATALOG_TAG = "gemma4:e4b"
"""A writing model of the catalog, so a ``--tag`` list can mix it with a candidate."""

CANDIDATE = "invented:1b"
"""A tag the catalog does not know."""

DIGEST = "sha256:0123456789abcdef0123456789abcdef"

Answer = Callable[[str, str], tuple[str, ChatStats]]


def _stats(done_reason: str = "stop", prompt: int = 100, answer: int = 100) -> ChatStats:
    """Return the statistics of a finished answer."""
    return ChatStats(done_reason=done_reason, prompt_eval_count=prompt, eval_count=answer)


def _answer_json(*sentences: tuple[str, str]) -> str:
    """Return a valid review answer that corrects each ``(original, corrected)`` pair."""
    return json.dumps(
        {
            "sentences": [
                {
                    "original": original,
                    "corrected": corrected,
                    "changes": [
                        {
                            "original": original,
                            "corrected": corrected,
                            "category": "grammar",
                            "explanation": "Die Form ist hier nicht richtig.",
                        }
                    ],
                }
                for original, corrected in sentences
            ],
            "unclear": [],
        },
        ensure_ascii=False,
    )


def _corrected_answer(prompt: str) -> str:
    """Return a valid answer that changes every sentence of the text in *prompt*."""
    text = prompt.split("Text:\n\n", 1)[1]
    sentences = [piece for piece in re.split(r"(?<=[.!?])\s+", text) if piece.strip()]
    return _answer_json(*[(sentence, sentence + "!") for sentence in sentences])


class _ScriptedChat:
    """A chat port that answers from a function instead of a server."""

    def __init__(self, answer: Answer, *, num_ctx: int = BASE_NUM_CTX) -> None:
        self._answer = answer
        self.num_ctx = num_ctx
        self.requests: list[str] = []

    def chat_with_stats(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        options: dict | None = None,
        think: bool | str = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Return the scripted answer for this request."""
        self.requests.append(model)
        return self._answer(system, prompt)


class _Probe:
    """A server that answers without a server."""

    def __init__(self, url: str) -> None:
        self.url = url

    def version(self) -> str:
        """Return the version of the invented server."""
        return "0.40.13"

    def model_info(self, tag: str) -> dict:
        """Return the digest and details of the invented model."""
        return {
            "digest": DIGEST,
            "quantization": "Q4_K_M",
            "parameter_size": "4B",
            "family": "qwen3",
        }

    def loaded(self) -> tuple:
        """Return nothing loaded: the smoke check's memory split is not what this test is about."""
        return ()


def _install(monkeypatch: pytest.MonkeyPatch, answer: Answer) -> _ScriptedChat:
    """Make the command talk to a scripted port and probe, and return the port."""
    chat = _ScriptedChat(answer)
    for module in (verify_model_catalog, gate_one):
        monkeypatch.setattr(module, "OllamaChatPort", lambda url, num_ctx: chat)
        monkeypatch.setattr(module, "ServerProbe", _Probe)
    return chat


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    """Run the command with *argv* and return its exit code and what it printed."""
    code = verify_model_catalog.main(list(argv))
    return code, capsys.readouterr().out


def _structured(*tags: str, answers: Path | None = None) -> list[str]:
    """Return the arguments of a structured-output run over *tags*."""
    argv = [
        "structured-output",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--temperature",
        "0",
        "--fit-limit",
        "off",
    ]
    for tag in tags:
        argv += ["--tag", tag]
    if answers is not None:
        argv += ["--answers", str(answers)]
    return argv


def test_the_default_window_is_8192() -> None:
    """
    Given  no ``--num-ctx``
    When   the window of the check is read
    Then   it is the base window the app uses without a measurement, not the largest one
    """
    assert BASE_NUM_CTX == 8192
    assert verify_model_catalog._default_window(None) == BASE_NUM_CTX
    assert verify_model_catalog._default_window(16384) == 16384


def test_the_header_carries_the_protocol_stamp(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a scripted server and the protocol the tools are at
    When   the check runs
    Then   the header names the eval version, the fingerprint, the answer format version, the
           window and the temperature, so the verdicts below are valid for exactly those
    """
    _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))
    stamp = protocol.current_stamp()

    code, out = _run(capsys, *_structured(CANDIDATE))

    assert code == 0
    assert f"eval version {stamp.eval_version}, fingerprint {stamp.fingerprint}" in out
    assert f"answer format version {ANSWER_FORMAT_VERSION}" in out
    assert f"num_ctx {BASE_NUM_CTX}, temperature 0" in out
    assert "ollama: 0.40.13" in out
    assert f"digest {DIGEST[:12]}" in out


def test_a_stale_fingerprint_stops_the_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a protocol file that does not record the request the code builds
    When   the check is started
    Then   it exits with code 2 and its message, and sends no request
    """
    chat = _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))

    def refuse() -> protocol.Stamp:
        raise protocol.ProtocolError("the prompts changed since version 3")

    monkeypatch.setattr(protocol, "current_stamp", refuse)

    code, out = _run(capsys, *_structured(CANDIDATE))

    assert code == 2
    assert "the prompts changed since version 3" in out
    assert chat.requests == []


def test_answers_are_saved_and_recheck_gives_the_same_verdicts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a live run that saved its answers
    When   ``recheck`` judges the file without a server
    Then   it prints the same report lines and the same decision
    """
    _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))

    code, live = _run(capsys, *_structured(CANDIDATE, answers=tmp_path))

    assert code == 0
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    header, answers = gate1_answers.read_answers(files[0])
    assert header.num_ctx == BASE_NUM_CTX
    assert len(answers) == 6

    code, again = _run(capsys, "recheck", str(files[0]))

    assert code == 0
    assert _report_lines(again) == _report_lines(live)
    assert "→ PASS" in again


def test_answers_survive_a_run_that_dies(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a run whose server stops answering with an exception the check does not catch
    When   the command is interrupted halfway through its fixtures
    Then   the answers received so far are already in the file, with their header
    """
    calls = {"n": 0}

    def answer(system: str, prompt: str) -> tuple[str, ChatStats]:
        calls["n"] += 1
        if calls["n"] > 3:
            raise KeyboardInterrupt
        return _corrected_answer(prompt), _stats()

    _install(monkeypatch, answer)

    with pytest.raises(KeyboardInterrupt):
        verify_model_catalog.main(_structured(CANDIDATE, answers=tmp_path))
    capsys.readouterr()

    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    header, answers = gate1_answers.read_answers(files[0])
    assert header.num_ctx == BASE_NUM_CTX
    assert len(answers) == 3


def _report_lines(out: str) -> list[str]:
    """Return the per-run report lines of *out*, without the run counter of a live run."""
    return [
        re.sub(r" \(run \d+/\d+\)$", "", line)
        for line in out.splitlines()
        if line.startswith("  ") and " placed " in line
    ]


def test_recheck_refuses_a_changed_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a saved answer whose text no longer hashes to the fixture's text
    When   ``recheck`` judges the file
    Then   it refuses the model and says the check has to run again
    """
    _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))
    _run(capsys, *_structured(CANDIDATE, answers=tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[1])
    first["text_sha256"] = "0" * 64
    path.write_text("\n".join([lines[0], json.dumps(first), *lines[2:]]) + "\n", encoding="utf-8")

    code, out = _run(capsys, "recheck", str(path))

    assert code == 1
    assert "changed since the answers were saved" in out


def test_recheck_refuses_another_fingerprint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a saved answer file made with a different request
    When   ``recheck`` judges it
    Then   it refuses the file, because a different request makes the answers stale
    """
    _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))
    _run(capsys, *_structured(CANDIDATE, answers=tmp_path))
    path = next(tmp_path.glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    header = json.loads(lines[0])
    header["fingerprint"] = "0" * 64
    path.write_text("\n".join([json.dumps(header), *lines[1:]]) + "\n", encoding="utf-8")

    code, out = _run(capsys, "recheck", str(path))

    assert code == 1
    assert "the answers are stale" in out


def test_mixed_tags_check_catalog_models_and_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a ``--tag`` list that names a catalog model and a candidate
    When   the check runs
    Then   both are checked, the catalog model is counted in the tally and the candidate gets
           a pass or fail, so neither is silently skipped
    """
    chat = _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))

    code, out = _run(capsys, *_structured(CATALOG_TAG, CANDIDATE))

    assert code == 0
    assert set(chat.requests) == {CATALOG_TAG, CANDIDATE}
    assert "1/1 writing models verified" in out
    assert "1/1 candidates pass the rule" in out
    assert f"  {CANDIDATE}: PASS" in out


def test_a_small_margin_is_warned_about(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a model whose answer nearly filled the window
    When   the check runs
    Then   the summary warns that less than a tenth of the window was left
    """
    _install(
        monkeypatch,
        lambda system, prompt: (_corrected_answer(prompt), _stats(prompt=4000, answer=4000)),
    )

    code, out = _run(capsys, *_structured(CANDIDATE))

    assert code == 0
    assert "smallest margin on page DE at budget: 192 tokens" in out
    assert "WARNING: less than 10 % of the window left" in out


def test_recheck_judges_both_attempts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a saved run whose first answer was cut off and whose second try answered
    When   ``recheck`` judges the file
    Then   it judges the second answer and calls the run cut off once, as the live run did
    """
    calls = {"n": 0}

    def answer(system: str, prompt: str) -> tuple[str, ChatStats]:
        calls["n"] += 1
        if calls["n"] == 1:
            return '{"sentences": [', _stats(done_reason="length")
        return _corrected_answer(prompt), _stats()

    _install(monkeypatch, answer)

    code, live = _run(capsys, *_structured(CANDIDATE, answers=tmp_path), "--temperature", "0.2")

    assert code == 0
    path = next(tmp_path.glob("*.jsonl"))
    _, answers = gate1_answers.read_answers(path)
    assert [saved.attempt for saved in answers[:2]] == [1, 2]
    assert answers[0].done_reason == "length"

    code, again = _run(capsys, "recheck", str(path))

    assert code == 0
    assert _report_lines(again) == _report_lines(live)
    assert "CUT OFF, THE SECOND TRY ANSWERED" in again


def test_the_profile_is_recorded_in_header_and_answers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a run with ``--compact-json off`` and a temperature
    When   its answers are saved
    Then   the header says which setting the invocation used and every answer says which
           profile its model was asked with, so a run is never read as another request
    """
    _install(monkeypatch, lambda system, prompt: (_corrected_answer(prompt), _stats()))

    code, _ = _run(capsys, *_structured(CANDIDATE, answers=tmp_path), "--compact-json", "off")

    assert code == 0
    header, answers = gate1_answers.read_answers(next(tmp_path.glob("*.jsonl")))
    assert header.compact_json == "off"
    assert header.temperature == 0.0
    assert {saved.request_profile["compact_json"] for saved in answers} == {False}
    assert {saved.request_profile["temperature"] for saved in answers} == {0.0}


def test_the_gate_tests_use_the_temperature_the_vendor_recommends() -> None:
    """
    Given  a catalogued model whose vendor recommends a temperature, and one whose vendor does not
    When   the gate tests resolve the temperature they send
    Then   the vendor's value is used — the card's non-thinking row for Qwen, "below 0.1" for
           Ministral — and the app's 0.2 for a model without a recommendation
    """
    assert defaults.temperature_for("gemma4:e4b") == 1.0
    assert defaults.temperature_for("qwen3:4b") == 0.7
    assert defaults.temperature_for("qwen3.5:9b") == 0.7
    assert defaults.temperature_for("ministral-3:8b") == 0.05
    assert defaults.temperature_for("mistral-nemo:12b") == 0.3
    assert defaults.temperature_for("lfm2.5:8b") == 0.2
    assert defaults.temperature_for("lfm2.5:24b") == 0.1
    assert defaults.temperature_for("gpt-oss:20b") == 0.2
    assert defaults.temperature_for(CANDIDATE) == 0.2


def test_the_request_carries_the_recommended_temperature() -> None:
    """
    Given  a model whose vendor recommends a temperature, and a tag written with ``:latest``
    When   the profile of the request is built without ``--temperature``
    Then   the request carries the recommended value, and an explicit temperature still wins
    """
    assert defaults.request_profile_for("gemma4:e4b").temperature == 1.0
    assert defaults.request_profile_for("gemma4:e4b:latest").temperature == 1.0
    assert defaults.request_profile_for("gemma4:e4b", temperature=0.2).temperature == 0.2
    assert defaults.request_profile_for(CANDIDATE).temperature == 0.2
    assert defaults.temperature_for("gemma4:e4b", 0.4) == 0.4


# ── fit: measure once, decide later ──────────────────────────────────────────

GB = 1e9
CARD_TOTAL = 17095983104


class _FitProbe(_Probe):
    """A server that reports the model as loaded at the window it was asked for."""

    def __init__(self, url: str, *, context_length: int | None = None) -> None:
        super().__init__(url)
        self.context_length = context_length
        self.unload_all_calls = 0
        self.asked: list[int] = []
        self.tag = CANDIDATE

    def unload_all(self, **kwargs) -> None:
        """Count the isolating start."""
        self.unload_all_calls += 1

    def loaded(self) -> tuple:
        """Report the model on the card at the window the last request asked for."""
        window = self.asked[-1] if self.asked else BASE_NUM_CTX
        loaded_at = window if self.context_length is None else self.context_length
        return (
            measure.LoadedModel(
                name=self.tag,
                size_bytes=int(8.0 * GB),
                size_vram_bytes=int(8.0 * GB),
                context_length=loaded_at,
            ),
        )


class _FitClient:
    """A client whose chat fails for the tags a test names, and answers otherwise."""

    def __init__(self, url: str, failing: set[str], probe: _FitProbe) -> None:
        self.url = url
        self.failing = failing
        self.probe = probe
        self.asked: list[str] = []
        self.options: list[dict] = []

    def chat_with_stats(self, model: str, prompt: str, *, options=None, **kwargs):
        """Record the request, then fail or answer with the statistics of a load."""
        self.asked.append(model)
        self.options.append(dict(options or {}))
        self.probe.tag = model
        self.probe.asked.append(int((options or {}).get("num_ctx", BASE_NUM_CTX)))
        if model in self.failing:
            raise OCRError("model not pulled")
        return "Der Hund läuft schnell nach Hause.", ChatStats(
            done_reason="stop",
            load_duration=4_200_000_000,
            eval_count=170,
            eval_duration=2_000_000_000,
        )


class _FakeAgent:
    """A memory agent whose card rises by a figure a test names."""

    def __init__(self, *, increase_gb: float = 8.2, fail_at: int | None = None) -> None:
        self.increase_gb = increase_gb
        self.fail_at = fail_at
        self.settles = 0
        self.windows = 0
        self.closed: list[int] = []

    def info(self):
        """Return what the agent is."""
        return AgentInfo(
            agent=1,
            agent_sha256="deadbeef",
            platform="linux",
            vendor="amd",
            source="sysfs",
            interval_s=0.2,
            cards=(AgentCard(index=0, name="AMD Radeon RX 9070 XT", total_bytes=CARD_TOTAL),),
            card_total_bytes=CARD_TOTAL,
            ram_total_bytes=33285996544,
            card_used_bytes=int(0.7 * GB),
            ram_available_bytes=int(30 * GB),
        )

    def settle(self, **kwargs) -> int:
        """Return the idle figure, counting the wait."""
        self.settles += 1
        return int(0.7 * GB)

    def open_window(self) -> int:
        """Open a window and return its number."""
        self.windows += 1
        return self.windows

    def close_window(self, window: int):
        """Return the window's statistics, or stop answering when the test says so."""
        if self.fail_at is not None and len(self.closed) + 1 >= self.fail_at:
            raise AgentError("the memory agent at http://127.0.0.1:1 is not reachable")
        self.closed.append(window)
        idle = int(0.7 * GB)
        peak = idle + int(self.increase_gb * GB)
        return WindowStats(
            window=window,
            card_start_bytes=idle,
            card_peak_bytes=peak,
            card_mean_bytes=peak,
            card_min_bytes=idle,
            card_end_bytes=peak,
            ram_start_available_bytes=int(30 * GB),
            ram_min_available_bytes=int(28.5 * GB),
            ram_end_available_bytes=int(28.5 * GB),
            samples=40,
            duration_s=8.6,
        )


def _fit(
    monkeypatch: pytest.MonkeyPatch,
    *,
    failing: set[str] | None = None,
    context_length: int | None = None,
    agent: _FakeAgent | None = None,
) -> tuple[_FitClient, _FitProbe]:
    """Make the command talk to a scripted client, probe and agent."""
    probe = _FitProbe("http://127.0.0.1:1", context_length=context_length)
    client = _FitClient("http://127.0.0.1:1", failing or set(), probe)
    monkeypatch.setattr(verify_model_catalog, "OllamaClient", lambda url: client)
    monkeypatch.setattr(verify_model_catalog, "ServerProbe", lambda url: probe)
    if agent is not None:
        monkeypatch.setattr(verify_model_catalog, "AgentClient", lambda url, token=None: agent)
    return client, probe


def _fit_args(store: Path, *extra: str) -> list[str]:
    """Return the arguments of a measuring run into *store*."""
    return [
        "fit",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--server",
        "rx9070xt",
        "--store",
        str(store),
        "--gpu-agent",
        "http://127.0.0.1:1",
        *extra,
    ]


def test_measuring_a_model_stores_one_record_per_window(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a scripted server and a scripted agent
    When   ``fit`` measures one model
    Then   every window is stored with the server, the digest, Ollama's figures and the agent's
           window, in raw bytes, and the card is settled before each load
    """
    agent = _FakeAgent()
    _fit(monkeypatch, agent=agent)
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE))

    assert code == 0
    records = FitStore(store).records()
    assert [record.num_ctx for record in records] == [8192, 16384, 32768]
    assert {record.server for record in records} == {"rx9070xt"}
    assert {record.digest for record in records} == {DIGEST}
    assert records[0].ollama.size_bytes == int(8.0 * GB)
    assert records[0].card.peak_bytes == int(0.7 * GB) + int(8.2 * GB)
    assert records[0].card.idle_bytes == int(0.7 * GB)
    assert records[0].card.agent_sha256 == "deadbeef"
    assert records[0].ram.min_available_bytes == int(28.5 * GB)
    assert records[0].ram.total_bytes == 33285996544
    assert (records[0].answer_tokens, records[0].generation_seconds) == (170, 2.0)
    assert agent.settles == 3
    assert "needs" in out
    assert "85.0 tokens/s" in out


def test_a_cpu_only_run_asks_for_no_gpu_at_one_window(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a scripted server and agent
    When   ``fit --cpu-only`` measures one model under its own label
    Then   one request is sent, with ``num_gpu`` 0, at 8,192 tokens only, the record keeps that
           option, and the output says CPU only and names the speed
    """
    client, _probe = _fit(monkeypatch, agent=_FakeAgent(increase_gb=0.0))
    store = tmp_path / "sizes.jsonl"

    code, out = _run(
        capsys, *_fit_args(store, "--cpu-only", "--server", "rx9070xt-cpu", "--tag", CANDIDATE)
    )

    assert code == 0
    records = FitStore(store).records()
    assert [record.num_ctx for record in records] == [BASE_NUM_CTX]
    assert records[0].options["num_gpu"] == 0
    assert client.options[0]["num_gpu"] == 0
    assert "CPU only" in out
    assert "85.0 tokens/s" in out


def test_a_cpu_only_label_is_never_mixed_with_size_measurements(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a store whose label already holds a normal size measurement
    When   ``fit --cpu-only`` is asked to measure under the same label
    Then   it refuses before any request and names a label to use instead
    """
    client, _probe = _fit(monkeypatch, agent=_FakeAgent())
    store = tmp_path / "sizes.jsonl"
    FitStore(store).append(_fit_record())

    code, out = _run(capsys, *_fit_args(store, "--cpu-only", "--tag", CANDIDATE))

    assert code == 2
    assert "rx9070xt-cpu" in out
    assert client.asked == []


def test_a_failed_request_still_closes_the_agents_window(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a model whose request fails after the agent opened a window
    When   ``fit`` measures it
    Then   the window is closed anyway, so the agent keeps no window open
    """
    agent = _FakeAgent()
    _fit(monkeypatch, failing={CANDIDATE}, agent=agent)

    code, _out = _run(capsys, *_fit_args(tmp_path / "sizes.jsonl", "--tag", CANDIDATE))

    assert code == 1
    assert agent.windows == 1
    assert agent.closed == [1]


def test_a_report_after_measuring_holds_only_the_model_and_server_just_measured(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a store that already holds another model and another machine
    When   ``fit`` measures a catalog model
    Then   the report under its name shows its own windows only, not the others' figures
    """
    from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG  # noqa: PLC0415

    tag = OLLAMA_CATALOG[0].tag
    store = tmp_path / "sizes.jsonl"
    _stored(
        store,
        _fit_record(tag="huge:70b", size_gb=41.0, card_gb=41.0),
        _fit_record(tag=tag, server="rtx3070", size_gb=40.0, card_gb=40.0),
    )
    _fit(monkeypatch, agent=_FakeAgent())

    code, out = _run(capsys, *_fit_args(store, "--tag", tag))

    assert code == 0
    assert "41.00 GB" not in out
    assert "40.00 GB" not in out


def test_an_agent_that_does_not_answer_stops_before_the_first_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an agent that cannot be reached
    When   ``fit`` starts
    Then   it exits with 2 and sends no request, so no record is made without its card reading
    """
    client, _probe = _fit(monkeypatch)

    def refuse(url: str, token: str | None = None):
        raise AgentError("the memory agent at http://127.0.0.1:1 is not reachable")

    monkeypatch.setattr(verify_model_catalog, "AgentClient", refuse)

    code, out = _run(capsys, *_fit_args(tmp_path / "sizes.jsonl", "--tag", CANDIDATE))

    assert code == 2
    assert "cannot be used" in out
    assert client.asked == []


def test_an_agent_that_stops_answering_ends_the_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an agent that stops answering at the second window
    When   ``fit`` measures a model
    Then   the first window is stored, the second is not, and the exit code is 2
    """
    _fit(monkeypatch, agent=_FakeAgent(fail_at=2))
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE))

    assert code == 2
    assert "stopped answering" in out
    assert [record.num_ctx for record in FitStore(store).records()] == [8192]


def test_without_an_agent_the_records_carry_ollamas_figures_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  ``--no-agent``
    When   ``fit`` measures a model
    Then   the records have no card reading, and the report says so
    """
    _fit(monkeypatch)
    store = tmp_path / "sizes.jsonl"

    code, out = _run(
        capsys,
        "fit",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--server",
        "rx9070xt",
        "--store",
        str(store),
        "--no-agent",
        "--tag",
        CANDIDATE,
    )

    assert code == 0
    assert all(record.card is None for record in FitStore(store).records())
    assert "no card reading" in out


def test_a_second_run_measures_nothing_and_again_measures_again(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a store that already holds every window of a model
    When   ``fit`` runs again, and then with ``--again``
    Then   the second run sends no request and the third measures every window again
    """
    _fit(monkeypatch, agent=_FakeAgent())
    store = tmp_path / "sizes.jsonl"
    _run(capsys, *_fit_args(store, "--tag", CANDIDATE))
    client, _probe = _fit(monkeypatch, agent=_FakeAgent())

    code, out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE))

    assert code == 0
    assert client.asked == []
    assert "already measured" in out

    code, _out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE, "--again"))

    assert code == 0
    assert len(client.asked) == 3
    assert len(FitStore(store).records()) == 6


def test_a_window_the_model_cannot_hold_ends_the_ladder(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a model whose own maximum is 8192 tokens
    When   ``fit`` asks for 16384
    Then   that record is stored and the ladder stops, because a larger step would measure the
           same window
    """
    _fit(monkeypatch, context_length=8192, agent=_FakeAgent())
    store = tmp_path / "sizes.jsonl"

    code, _out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE))

    assert code == 0
    assert [record.num_ctx for record in FitStore(store).records()] == [8192, 16384]


def test_a_step_that_does_not_fit_the_card_is_stored_and_flagged(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a card whose use rises to its total
    When   ``fit`` measures a model
    Then   the step is stored, because it is a real figure, and marked as spilled
    """
    _fit(monkeypatch, agent=_FakeAgent(increase_gb=CARD_TOTAL / GB - 0.7))
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE))

    assert code == 0
    assert "SPILLED" in out
    assert all(spilled(record) for record in FitStore(store).records())


def test_a_model_that_fails_does_not_end_the_fit_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  two tags, and a client whose chat fails with OCRError for the first
    When   ``fit`` runs
    Then   the second tag is measured and the exit code is 1, so one bad tag does not cost the
           whole evening
    """
    client, _probe = _fit(monkeypatch, failing={CANDIDATE}, agent=_FakeAgent())
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_fit_args(store, "--tag", CANDIDATE, "--tag", "invented:2b"))

    assert code == 1
    assert f"{CANDIDATE}: " in out
    assert "invented:2b" in client.asked
    assert {record.tag for record in FitStore(store).records()} == {"invented:2b"}


def test_an_unreachable_server_ends_the_fit_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a probe whose unload_all raises URLError, as a dead server does
    When   ``fit`` runs
    Then   it exits with code 2 and says the server cannot be reached, without a traceback
    """
    from urllib.error import URLError  # noqa: PLC0415

    _fit(monkeypatch, agent=_FakeAgent())
    monkeypatch.setattr(
        verify_model_catalog,
        "ServerProbe",
        lambda url: _DeadProbe(url, URLError("connection refused")),
    )

    code, out = _run(capsys, *_fit_args(tmp_path / "sizes.jsonl", "--tag", CANDIDATE))

    assert code == 2
    assert "cannot be reached" in out


class _DeadProbe(_FitProbe):
    """A server that is gone: every isolating start fails."""

    def __init__(self, url: str, error: BaseException) -> None:
        super().__init__(url)
        self.error = error

    def unload_all(self, **kwargs) -> None:
        """Fail the way a dead server does."""
        raise self.error


def _stored(store: Path, *records) -> None:
    """Write *records* into *store*."""
    fit_store = FitStore(store)
    for record in records:
        fit_store.append(record)


def _fit_record(
    *,
    num_ctx: int = BASE_NUM_CTX,
    size_gb: float = 8.0,
    card_gb: float | None = 8.2,
    server: str = "rx9070xt",
    tag: str = CANDIDATE,
) -> FitRecord:
    """Return a record as a measurement would store it."""
    card = None
    if card_gb is not None:
        card = CardReading(
            agent_sha256="deadbeef",
            source="sysfs",
            total_bytes=CARD_TOTAL,
            idle_bytes=int(0.7 * GB),
            peak_bytes=int((0.7 + card_gb) * GB),
            end_bytes=int((0.7 + card_gb) * GB),
        )
    return FitRecord(
        measured_at="2026-11-13T19:04:11Z",
        server=server,
        ollama_version="0.40.15",
        tool_commit="abc1234",
        tag=tag,
        digest=DIGEST,
        num_ctx=num_ctx,
        answer_tokens=170,
        generation_seconds=2.0,
        options={"temperature": 1.0, "num_ctx": num_ctx},
        load_seconds=4.2,
        ollama=OllamaReading(
            context_length=num_ctx,
            size_bytes=int(size_gb * GB),
            size_vram_bytes=int(size_gb * GB),
        ),
        card=card,
    )


def test_the_report_names_the_tier_window_of_a_limit(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a store with a model measured at every window
    When   ``fit --report --fit-limit 14.5`` runs
    Then   the largest window within 14.5 GB is printed, without a server
    """
    store = tmp_path / "sizes.jsonl"
    _stored(
        store,
        _fit_record(num_ctx=8192, size_gb=6.0, card_gb=6.0),
        _fit_record(num_ctx=16384, size_gb=10.0, card_gb=10.0),
        _fit_record(num_ctx=32768, size_gb=16.0, card_gb=16.0),
    )

    code, out = _run(capsys, "fit", "--report", "--store", str(store), "--fit-limit", "14.5")

    assert code == 0
    assert "limit 14.5 GB: up to 16,384 tokens" in out


def test_the_report_marks_two_servers_that_disagree(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  the same build measured on two machines, 12 % apart
    When   ``fit --report --compare`` runs
    Then   the figure is marked as disagreeing
    """
    store = tmp_path / "sizes.jsonl"
    _stored(
        store,
        _fit_record(server="rx9070xt", size_gb=8.0, card_gb=8.0),
        _fit_record(server="rtx3070", size_gb=8.96, card_gb=8.96),
    )

    code, out = _run(
        capsys, "fit", "--report", "--store", str(store), "--compare", "rx9070xt", "rtx3070"
    )

    assert code == 0
    assert "DISAGREE" in out


def test_the_catalog_figures_take_the_larger_of_two_servers(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  the same build measured on two machines, one needing more
    When   ``fit --report --catalog`` runs
    Then   the larger need is the figure to enter, because two backends that disagree give the
           cautious answer
    """
    store = tmp_path / "sizes.jsonl"
    _stored(
        store,
        _fit_record(server="rx9070xt", size_gb=8.0, card_gb=8.0),
        _fit_record(server="rtx3070", size_gb=9.0, card_gb=9.4),
    )

    code, out = _run(capsys, "fit", "--report", "--store", str(store), "--catalog")

    assert code == 0
    assert "memory_gb: 9.40" in out


def test_the_catalog_figures_of_a_model_come_from_its_own_records(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a store holding a large and a small model
    When   ``fit --report --catalog`` runs
    Then   each model gets its own memory and growth, not the other model's
    """
    store = tmp_path / "sizes.jsonl"
    _stored(
        store,
        _fit_record(tag="big:27b", num_ctx=8192, size_gb=8.0, card_gb=8.0),
        _fit_record(tag="big:27b", num_ctx=16384, size_gb=12.0, card_gb=12.0),
        _fit_record(tag="small:4b", num_ctx=8192, size_gb=3.0, card_gb=3.0),
        _fit_record(tag="small:4b", num_ctx=16384, size_gb=3.5, card_gb=3.5),
    )

    code, out = _run(capsys, "fit", "--report", "--store", str(store), "--catalog")

    assert code == 0
    big = out[out.index("big:27b:\n  memory_gb") :]
    small = out[out.index("small:4b:\n  memory_gb") :]
    assert "memory_gb: 8.00" in big.split("small:4b")[0]
    assert "context_gb_per_8k: 4.00" in big.split("small:4b")[0]
    assert "memory_gb: 3.00" in small
    assert "context_gb_per_8k: 0.50" in small


def test_an_empty_store_says_so(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """
    Given  a store that was never written
    When   ``fit --report`` runs
    Then   it says there is nothing to report and exits with 1
    """
    code, out = _run(capsys, "fit", "--report", "--store", str(tmp_path / "absent.jsonl"))

    assert code == 1
    assert "no measurement" in out


def test_measuring_needs_a_server_label(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a measuring run without ``--server``
    When   ``fit`` starts
    Then   it refuses, because a record without a server is worthless for the cross-check
    """
    _fit(monkeypatch, agent=_FakeAgent())

    code, out = _run(
        capsys,
        "fit",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--store",
        str(tmp_path / "sizes.jsonl"),
        "--gpu-agent",
        "http://127.0.0.1:1",
        "--tag",
        CANDIDATE,
    )

    assert code == 2
    assert "--server LABEL is required" in out


def test_measuring_needs_an_agent_or_a_decision_against_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a measuring run with neither ``--gpu-agent`` nor ``--no-agent``
    When   ``fit`` starts
    Then   it refuses instead of silently storing the weaker figure
    """
    _fit(monkeypatch)

    code, out = _run(
        capsys,
        "fit",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--server",
        "rx9070xt",
        "--store",
        str(tmp_path / "sizes.jsonl"),
        "--tag",
        CANDIDATE,
    )

    assert code == 2
    assert "no --gpu-agent" in out


def test_the_old_flag_still_reaches_the_fit_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  the older ``--context-growth`` flag
    When   the command line is normalised
    Then   it becomes ``fit``, so an old invocation keeps working
    """
    assert verify_model_catalog._normalise(["--context-growth", "--tag", "x"]) == [
        "fit",
        "--tag",
        "x",
    ]


# ── the size limit the screen applies ────────────────────────────────────────


class _ScreenProbe(_Probe):
    """A server that can be emptied, and that reports the model a measurement loaded."""

    def __init__(self, url: str, *, loaded: tuple = (), busy: bool = False) -> None:
        super().__init__(url)
        self.loaded_models = loaded
        self.busy = busy
        self.unloads = 0

    def unload_all(self, **kwargs) -> None:
        """Count the isolating start, or refuse the way a busy server does."""
        self.unloads += 1
        if self.busy:
            raise measure.ServerBusyError("the server still holds invented:1b after 60 s")

    def loaded(self) -> tuple:
        """Return the models the server holds."""
        return self.loaded_models


def _answers_everything(system: str, prompt: str) -> tuple[str, ChatStats]:
    """Answer the fit's short request and every smoke fixture with a valid answer."""
    if "Text:\n\n" not in prompt:
        return "Der Hund läuft schnell nach Hause.", _stats()
    return _corrected_answer(prompt), _stats()


def _install_screen(
    monkeypatch: pytest.MonkeyPatch,
    *,
    loaded: tuple = (),
    busy: bool = False,
    agent: _FakeAgent | None = None,
) -> tuple[_ScriptedChat, _ScreenProbe]:
    """Make the screen talk to a scripted port, probe and agent."""
    chat = _ScriptedChat(_answers_everything)
    probe = _ScreenProbe("http://127.0.0.1:1", loaded=loaded, busy=busy)
    for module in (verify_model_catalog, gate_one):
        monkeypatch.setattr(module, "OllamaChatPort", lambda url, num_ctx: chat)
        monkeypatch.setattr(module, "ServerProbe", lambda url: probe)
    if agent is not None:
        monkeypatch.setattr(verify_model_catalog, "AgentClient", lambda url, token=None: agent)
    return chat, probe


def _screen_args(store: Path, *extra: str) -> list[str]:
    """Return the arguments of a smoke run that applies the size limit."""
    return [
        "smoke",
        "--ollama-url",
        "http://127.0.0.1:1",
        "--answers",
        str(store.parent / "answers"),
        "--fit-store",
        str(store),
        "--progress",
        "off",
        *extra,
        "--tag",
        CANDIDATE,
    ]


def test_a_model_that_does_not_fit_the_limit_fails_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a stored record whose card increase is 15.8 GB, and the default 16 GB limit
    When   the smoke check starts
    Then   the setup fails at once with the figure, its source and the limit, no fixture is sent,
           and the exit code is 1
    """
    chat, probe = _install_screen(monkeypatch)
    store = tmp_path / "sizes.jsonl"
    _stored(store, _fit_record(size_gb=14.0, card_gb=15.8))

    code, out = _run(capsys, *_screen_args(store))

    assert code == 1
    assert "needs 15.80 GB at 8,192 tokens (rx9070xt, 2026-11-13, card reading)" in out
    assert "limit 15.11 GB (16gb)" in out
    assert chat.requests == []
    assert probe.unloads == 0


def test_a_larger_limit_lets_the_same_model_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  the same record, and ``--fit-limit 16.5``
    When   the smoke check starts
    Then   the fixtures run, and the setup's block says what the model needs and where the figure
           comes from
    """
    chat, _probe = _install_screen(monkeypatch)
    store = tmp_path / "sizes.jsonl"
    _stored(store, _fit_record(size_gb=14.0, card_gb=15.8))

    code, out = _run(capsys, *_screen_args(store, "--fit-limit", "16.5"))

    assert code == 0
    assert chat.requests
    assert (
        "fit: needs 15.80 GB at 8,192 tokens (rx9070xt; Ollama says 14.00), limit 16.50 GB" in out
    )


def test_a_model_with_no_record_is_measured_before_the_first_fixture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an empty store and a memory agent
    When   the smoke check starts
    Then   one record is measured at the checked window with the agent's window, the server is
           emptied once, and the fixtures run
    """
    loaded = (
        measure.LoadedModel(
            name=CANDIDATE,
            size_bytes=int(8.0 * GB),
            size_vram_bytes=int(8.0 * GB),
            context_length=BASE_NUM_CTX,
        ),
    )
    chat, probe = _install_screen(monkeypatch, loaded=loaded, agent=_FakeAgent())
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_screen_args(store, "--gpu-agent", "http://127.0.0.1:1"))

    assert code == 0
    records = FitStore(store).records()
    assert [record.num_ctx for record in records] == [BASE_NUM_CTX]
    assert records[0].card is not None
    assert records[0].server == "127.0.0.1"
    assert probe.unloads == 1
    assert chat.requests
    assert "fit: needs" in out


def test_without_an_agent_the_screen_stores_ollamas_figure_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  an empty store and no ``--gpu-agent``
    When   the smoke check starts
    Then   the record has no card reading, the setup's block warns about it, and the fixtures run
    """
    loaded = (
        measure.LoadedModel(
            name=CANDIDATE,
            size_bytes=int(8.0 * GB),
            size_vram_bytes=int(8.0 * GB),
            context_length=BASE_NUM_CTX,
        ),
    )
    chat, _probe = _install_screen(monkeypatch, loaded=loaded)
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_screen_args(store))

    assert code == 0
    assert FitStore(store).records()[0].card is None
    assert "no card reading: Ollama's size only" in out
    assert chat.requests


def test_a_limit_of_off_looks_nothing_up_and_measures_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  ``--fit-limit off``
    When   the smoke check starts
    Then   the store is not read, the server is not emptied, and the header says the check is off
    """
    chat, probe = _install_screen(monkeypatch)
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_screen_args(store, "--fit-limit", "off"))

    assert code == 0
    assert probe.unloads == 0
    assert not store.exists()
    assert "fit limit: off" in out
    assert chat.requests


def test_a_busy_server_is_a_warning_and_the_fixtures_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a server that will not empty, so the fit cannot be measured
    When   the smoke check starts
    Then   the block warns that the fit could not be measured, and the fixtures run anyway
    """
    chat, _probe = _install_screen(monkeypatch, busy=True)
    store = tmp_path / "sizes.jsonl"

    code, out = _run(capsys, *_screen_args(store))

    assert code == 0
    assert "could not measure the fit: the server is busy" in out
    assert not store.exists()
    assert chat.requests


def test_the_header_names_the_limit_the_setups_were_screened_under(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  ``--fit-limit 8gb``
    When   the smoke check runs
    Then   the header names the limit and the size it allows, so a record says what was checked
    """
    _install_screen(monkeypatch)
    store = tmp_path / "sizes.jsonl"
    _stored(store, _fit_record(size_gb=4.0, card_gb=4.2))

    code, out = _run(capsys, *_screen_args(store, "--fit-limit", "8gb"))

    assert code == 0
    assert "fit limit: 8gb (7.56 GB)" in out


def test_the_answers_file_records_the_limit_the_setups_were_screened_under(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """
    Given  a smoke run with ``--fit-limit 8gb``
    When   its answers file is read back
    Then   the header names the limit, so a saved run says what was checked
    """
    _install_screen(monkeypatch)
    store = tmp_path / "sizes.jsonl"
    _stored(store, _fit_record(size_gb=4.0, card_gb=4.2))
    answers = tmp_path / "answers"

    code, _out = _run(capsys, *_screen_args(store, "--fit-limit", "8gb"))

    assert code == 0
    header, _saved = gate1_answers.read_answers(next(answers.glob("*.jsonl")))
    assert header.fit_limit == "8gb"
