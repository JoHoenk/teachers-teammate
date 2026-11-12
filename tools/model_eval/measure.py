"""Measuring a run: the requests it makes, the state of the server, the graphics memory.

Everything that touches a machine lives here, behind seams a test can replace: a recording client
(the app's own :class:`~teachers_teammate.infrastructure.ollama_utils.OllamaClient`, so the request
the app sends is the request that is measured), a probe of the server's HTTP interface, and a
sampler of the graphics card that runs a command on this machine or over SSH.

Protocol of one measured run (see ``evaluations/proofreading/FORMAT.md``):

1. optionally unload every model and check that the server is idle (``isolate``);
2. start the sampler and read the idle graphics memory;
3. one request that is not scored loads the model, so loading is not part of any text;
4. per text: note what is loaded, run the text, note what is loaded again, take the memory peak;
5. optionally unload the model again.

Timing is only comparable between runs on the same machine, and only for texts that were *not*
shared with another model.

The header of a run records the protocol it was made with (:mod:`.protocol`): the
``eval_version``, its ``fingerprint`` and the instructions hash of the run's languages and mode.
A run is resumed only into the same protocol — :mod:`.session` names what differs — and the export
refuses a run whose sessions disagree.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import threading
import time
from typing import TYPE_CHECKING, Any, Protocol
import urllib.request

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from teachers_teammate.domain.model_catalog import normalize_tag
from teachers_teammate.infrastructure.ollama_utils import OllamaClient

from . import protocol
from .answer_mode import AnswerMode
from .performance import LoadedModel, Performance, RequestStats
from .ports import ProbePort, RequestRecorder

if TYPE_CHECKING:
    from teachers_teammate.infrastructure.ollama_utils import ChatStats

Clock = Callable[[], float]
Fetch = Callable[[str, str, dict | None], dict]
"""``(method, path, json body) -> json answer``; replaced in tests."""

_SAMPLE_LEAD_S = 1.0
"""A sample is taken once a second and reports the second before it: look this far back."""

_STOP_WAIT_S = 5.0
"""How long a stopping sampler may take to end its current second before it is killed."""

_SAMPLE_LAG_S = 1.5
"""... and this far past the end of a text, for the sample that is still on its way."""


class ServerBusyError(RuntimeError):
    """The server will not become idle, so a speed run on it would measure something else."""


# ── the app's client, recording ───────────────────────────────────────────────


class RecordingClient(OllamaClient):
    """The app's Ollama client that keeps the statistics of every request it sends.

    Subclassing keeps the request path of the app untouched: the correctors call
    :meth:`chat_with_stats`, directly or through ``chat``, and this adds the seed to the options,
    times the call and notes the statistics.  A seed makes sampling repeatable on the same
    machine and software, which the app does not ask for (it sends none).

    An *identical* request sent again within one client gets the next seed, because the app's
    second try after a cut-off answer samples afresh: with the same seed the retry would repeat
    the runaway it is meant to escape.  The first send
    keeps the seed, so a run stays repeatable.
    """

    def __init__(self, url: str, *, seed: int | None = None, clock: Clock = time.monotonic) -> None:
        super().__init__(url)
        self._seed = seed
        self._clock = clock
        self._requests: list[RequestStats] = []
        self._sent: dict[str, int] = {}

    @property
    def seed(self) -> int | None:
        """Return the seed sent with every request, or ``None`` when none is sent."""
        return self._seed

    def chat_with_stats(  # noqa: PLR0913  # same keyword shape as the client it extends
        self,
        model: str,
        prompt: str,
        *,
        images: list[str] | None = None,
        options: dict | None = None,
        think: bool | None = False,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        system: str = "",
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Send the request as the app does, with the seed added, and record how it went."""
        if self._seed is not None:
            repeat = self._repeat_of(model, prompt, system, format, options)
            options = {**(options or {}), "seed": self._seed + repeat}
        started = self._clock()
        try:
            content, stats = super().chat_with_stats(
                model,
                prompt,
                images=images,
                options=options,
                think=think,
                timeout=timeout,
                format=format,
                system=system,
                total_timeout=total_timeout,
            )
        except Exception:
            self._requests.append(RequestStats.failure(model, self._clock() - started))
            raise
        self._requests.append(RequestStats.from_stats(model, stats, self._clock() - started))
        return content, stats

    def _repeat_of(
        self,
        model: str,
        prompt: str,
        system: str,
        answer_format: dict | str | None,
        options: dict | None,
    ) -> int:
        """Return how often this exact request was sent before, and count this send.

        "Exact" is the model, both messages, the answer schema and the options without the seed:
        the seed is what this varies.  The first send of a request returns 0, so it keeps the
        run's seed.
        """
        key = json.dumps(
            [model, system, prompt, answer_format, sorted((options or {}).items())],
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        seen = self._sent.get(key, 0)
        self._sent[key] = seen + 1
        return seen

    def drain(self) -> tuple[RequestStats, ...]:
        """Return the requests recorded since the last call and forget them."""
        taken, self._requests = tuple(self._requests), []
        return taken


# ── the server ────────────────────────────────────────────────────────────────


def _http_fetch(base_url: str, timeout: float) -> Fetch:
    """Return a :data:`Fetch` that talks to *base_url* with the standard library."""

    def fetch(method: str, path: str, body: dict | None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            base_url.rstrip("/") + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            return json.loads(answer.read().decode("utf-8") or "{}")

    return fetch


def same_model(first: str, second: str) -> bool:
    """Return whether two tags name the same model (``x`` and ``x:latest`` do)."""
    return normalize_tag(first) == normalize_tag(second)


class ServerProbe:
    """What the Ollama server says about itself: version, models, what is loaded."""

    def __init__(self, url: str, *, fetch: Fetch | None = None, timeout: float = 15.0) -> None:
        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            normalize_ollama_url,
        )

        self._fetch = fetch or _http_fetch(normalize_ollama_url(url), timeout)

    def version(self) -> str:
        """Return the server's version."""
        return str(self._fetch("GET", "/api/version", None).get("version", "unknown"))

    def model_info(self, tag: str) -> dict:
        """Return digest, quantisation, parameter size and family of an installed *tag*.

        The tag is a name that can be re-pointed; the digest pins the weights.  Returns ``{}``
        when the model is not installed.
        """
        for model in self._fetch("GET", "/api/tags", None).get("models", ()):
            if same_model(str(model.get("name", "")), tag):
                details = model.get("details", {})
                return {
                    "digest": str(model.get("digest", "")),
                    "quantization": str(details.get("quantization_level", "")),
                    "parameter_size": str(details.get("parameter_size", "")),
                    "family": str(details.get("family", "")),
                }
        return {}

    def loaded(self) -> tuple[LoadedModel, ...]:
        """Return the models the server holds in memory right now."""
        models = self._fetch("GET", "/api/ps", None).get("models", ())
        return tuple(
            LoadedModel(
                name=str(m.get("name", "")),
                size_bytes=int(m.get("size", 0)),
                size_vram_bytes=int(m.get("size_vram", 0)),
                context_length=int(m.get("context_length", 0)),
            )
            for m in models
        )

    def unload(self, name: str) -> None:
        """Ask the server to drop *name* from memory."""
        self._fetch("POST", "/api/generate", {"model": name, "keep_alive": 0})

    def unload_all(
        self,
        *,
        wait_s: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        """Unload every model and wait until the server reports none.

        Raises:
            ServerBusyError: something stays loaded, or keeps being loaded by someone else.
        """
        deadline = clock() + wait_s
        while True:
            loaded = self.loaded()
            if not loaded:
                return
            if clock() > deadline:
                names = ", ".join(m.name for m in loaded)
                raise ServerBusyError(f"the server still holds {names} after {wait_s:g} s")
            for model in loaded:
                self.unload(model.name)
            sleep(1.0)


# ── the graphics card ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SamplerCommand:
    """The command that prints the graphics memory in use, once a second, for one vendor."""

    command: str
    """The shell command to run on the machine with the card."""

    process: str
    """Part of the command line of the process it starts, to check before killing it."""


SAMPLER_COMMANDS: dict[str, SamplerCommand] = {
    "amd": SamplerCommand(
        # Python buffers a pipe in blocks; without this the samples arrive minutes late.
        "echo PID $$; exec env PYTHONUNBUFFERED=1 amd-smi monitor -v --csv -w 1 -W 14400",
        "amd-smi",
    ),
    "nvidia": SamplerCommand(
        "echo PID $$; exec nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -l 1",
        "nvidia-smi",
    ),
}
"""The AMD command was checked on a Radeon RX 9070 XT (``amd-smi`` 27.1.0) and the NVIDIA one on
a GeForce RTX 3070 Laptop; other cards and versions may print other columns."""


class SampleParser:
    """Reads the lines a sampler prints and picks out the graphics memory in MB.

    ``amd-smi monitor --csv`` repeats a header line before every sample, so the column is found
    by name each time; ``nvidia-smi`` prints one bare number per card.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self._column: int | None = None

    def feed(self, line: str) -> int | None:
        """Return the memory in use (MB) if *line* is a sample, else ``None``."""
        text = line.strip()
        if text.startswith("PID "):
            suffix = text[4:].strip()
            self.pid = int(suffix) if suffix.isdigit() else None
            return None
        if "," in text and "vram_used" in text:
            self._column = text.split(",").index("vram_used")
            return None
        if text.isdigit():
            return int(text)
        fields = text.split(",")
        if self._column is not None and len(fields) > self._column:
            value = fields[self._column].strip()
            if value.isdigit():
                return int(value)
        return None


@dataclass
class VramSeries:
    """Graphics memory samples with the time they arrived."""

    samples: list[tuple[float, int]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, when: float, megabytes: int) -> None:
        """Note a sample."""
        with self._lock:
            self.samples.append((when, megabytes))

    def peak(self, since: float, until: float) -> int | None:
        """Return the highest sample between *since* and *until*, or ``None`` if there is none."""
        with self._lock:
            inside = [mb for when, mb in self.samples if since <= when <= until]
        return max(inside) if inside else None


class GpuSampler:
    """Samples the graphics memory in use while a run goes on.

    Runs the vendor's command on this machine, or on another one through *ssh* (for example
    ``["ssh.exe", "user@host"]``; from WSL the Windows client is the one that holds the key).
    Samples are stamped with the time they arrive here, because the other machine's clock can be
    seconds off.  Stopping the local process does not stop the remote one, so the remote process is
    killed by the process id it printed first.
    """

    def __init__(
        self,
        vendor: str,
        ssh: Sequence[str] = (),
        *,
        clock: Clock = time.monotonic,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        run: Callable[..., object] = subprocess.run,
    ) -> None:
        if vendor not in SAMPLER_COMMANDS:
            known = ", ".join(sorted(SAMPLER_COMMANDS))
            raise ValueError(f"unknown graphics vendor {vendor!r}; use one of: {known}")
        self._spec = SAMPLER_COMMANDS[vendor]
        self._ssh = list(ssh)
        self._clock = clock
        self._popen = popen
        self._run = run
        self._parser = SampleParser()
        self.series = VramSeries()
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self.baseline_mb: int | None = None

    def _remote(self, command: str) -> list[str]:
        """Return the argument list that runs *command* where the card is."""
        return [*self._ssh, command] if self._ssh else ["sh", "-c", command]

    def start(self, *, wait_s: float = 15.0, sleep: Callable[[float], None] = time.sleep) -> None:
        """Start sampling and wait for the first sample, which is the idle baseline.

        Raises:
            RuntimeError: no sample arrived, so there is nothing to measure with.
        """
        self._process = self._popen(
            self._remote(self._spec.command),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
        )
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        deadline = self._clock() + wait_s
        while not self.series.samples:
            if self._clock() > deadline:
                self.stop()
                raise RuntimeError("the graphics memory sampler printed nothing")
            sleep(0.2)
        self.baseline_mb = self.series.samples[0][1]

    def _read(self) -> None:
        """Feed the sampler's output to the parser until the process ends."""
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            used = self._parser.feed(line)
            if used is not None:
                self.series.add(self._clock(), used)

    def peak_mb(self, since: float, until: float) -> int | None:
        """Return the peak memory in use between two times of this sampler's clock."""
        return self.series.peak(since - _SAMPLE_LEAD_S, until + _SAMPLE_LAG_S)

    def stop(self) -> None:
        """Stop the sampler here and, if it runs elsewhere, there."""
        process = self._process
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=_STOP_WAIT_S)
            except subprocess.TimeoutExpired:
                process.kill()
        if self._reader is not None:
            self._reader.join(timeout=_STOP_WAIT_S)
        pid = self._parser.pid
        if pid is not None:
            # Check the command line first: the id may belong to something else by now.
            guard = f"grep -q {self._spec.process} /proc/{pid}/cmdline && kill {pid}"
            self._run(self._remote(guard), capture_output=True, timeout=30, check=False)
        self._process = None


# ── the record of a run ───────────────────────────────────────────────────────


def tool_revision(run: Callable[..., Any] = subprocess.run) -> str:
    """Return the commit the tools are at, with ``+dirty`` for uncommitted changes.

    ``unknown`` when this is not a git checkout (a packaged copy).
    """
    here = str(Path(__file__).resolve().parent)

    def git(*args: str) -> str | None:
        try:
            done = run(
                ["git", *args], cwd=here, capture_output=True, text=True, timeout=15, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if getattr(done, "returncode", 1) == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if not commit:
        return "unknown"
    return commit + ("+dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


def run_identity(  # noqa: PLR0913  # a record of many independent facts
    *,
    stamp: protocol.Stamp,
    probe: ProbePort,
    model: str,
    mode: AnswerMode,
    languages: Sequence[str],
    temperature: float,
    request_profile: dict,
    num_ctx: int,
    timeout: float,
    seed: int | None,
    repeat: int,
    isolate: bool,
    sampler_vendor: str,
    machine: str,
    testset: str,
    testset_sha256: str,
    texts: int,
) -> dict:
    """Return the facts every session of one run shares: what it was run on and how.

    A result is only reproducible, and only comparable, together with these: the model's digest
    (the tag can be re-pointed), the server's version, the seed, window and timeout, the test
    set, and whether the server was idle.  ``request_profile`` is the profile the requests were
    built with (the compact-JSON rule and the temperature), so two runs of one tag that asked
    differently never pool.  They are exactly the keys a resumed run has to agree
    on (:data:`.session.RESUME_KEYS`) plus the model's details.

    The protocol is recorded as it was at run time, not as it is when the run is exported: the
    *stamp* (``eval_version`` and ``fingerprint``) is the one :func:`.protocol.current_stamp`
    checked, so what is recorded is what was compared, and ``instructions_sha256`` is the hash of
    the prompts of *languages* in *mode*, with the compact-JSON rule when the profile asks for it.
    A prompt changed after the run therefore stays visible instead of silently rewriting the
    record.
    """
    return {
        "eval_version": stamp.eval_version,
        "fingerprint": stamp.fingerprint,
        "instructions_sha256": protocol.instructions_sha256(
            languages, mode=mode, compact_json=bool(request_profile.get("compact_json"))
        ),
        "answer_format_version": ANSWER_FORMAT_VERSION,
        "machine": machine,
        "ollama_version": probe.version(),
        "model": model,
        "model_info": probe.model_info(model),
        "mode": mode,
        "temperature": temperature,
        "request_profile": request_profile,
        "num_ctx": num_ctx,
        "timeout": timeout,
        "seed": seed,
        "repeat": repeat,
        "isolated": isolate,
        "gpu_sampler": sampler_vendor or None,
        "testset": testset,
        "testset_sha256": testset_sha256,
        "texts": texts,
    }


def session_header(identity: dict, *, preparation: Preparation, revision: str, now: str) -> dict:
    """Return one entry of ``<name>.header.json``: the run's identity plus this session's facts.

    ``tool_revision`` is a pointer for a human reader; nothing checks it.  The facts of one
    session are named in :data:`.session.SESSION_KEYS`.
    """
    return {
        "date": now,
        "tool_revision": revision,
        **identity,
        "loaded_before": [m.name for m in preparation.loaded_before],
        "vram_baseline_mb": preparation.vram_baseline_mb,
    }


# ── one measured run ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Preparation:
    """What was true before the first scored text."""

    loaded_before: tuple[LoadedModel, ...]
    """What the server held when the run started (empty after an isolating start)."""

    vram_baseline_mb: int | None
    """Graphics memory in use before the model was loaded."""


class VramSampler(Protocol):
    """What :class:`MeasuredRun` needs from a graphics memory sampler."""

    baseline_mb: int | None

    def start(self) -> None:
        """Start sampling and read the idle memory."""

    def stop(self) -> None:
        """Stop sampling."""

    def peak_mb(self, since: float, until: float) -> int | None:
        """Return the highest memory in use between two times of the run's clock."""


class MeasuredRun:
    """Wraps the texts of one model, temperature and repeat in the measurement protocol.

    Implements the ``begin`` / ``end`` hook of :class:`~.comparison.WritingModelComparison`.
    """

    def __init__(
        self,
        *,
        client: RequestRecorder,
        probe: ProbePort,
        model: str,
        sampler: VramSampler | None = None,
        repeat: int = 1,
        clock: Clock = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._sleep = sleep
        self._client = client
        self._probe = probe
        self._model = model
        self._sampler = sampler
        self._repeat = repeat
        self._clock = clock
        self._baseline: int | None = None
        self._before: tuple[LoadedModel, ...] = ()
        self._started = 0.0

    def prepare(self, warm_up: Callable[[], object], *, isolate: bool) -> Preparation:
        """Get the server ready: idle if asked, sampler running, model loaded by a warm-up.

        Args:
            warm_up: Makes one request that is not scored (the model loads here).
            isolate: Unload everything first and refuse a server that will not become idle.
        """
        if isolate:
            self._probe.unload_all(sleep=self._sleep)
        loaded_before = self._probe.loaded()
        if self._sampler is not None:
            self._sampler.start()
            self._baseline = self._sampler.baseline_mb
        warm_up()
        self._client.drain()  # the warm-up is not part of any text
        return Preparation(loaded_before=loaded_before, vram_baseline_mb=self._baseline)

    def begin(self) -> None:
        """Note what is loaded before a text and when it started."""
        self._client.drain()
        self._before = self._probe.loaded()
        self._started = self._clock()

    def end(self) -> Performance:
        """Return what the text cost: its requests, what was loaded, the memory peak."""
        finished = self._clock()
        requests = self._client.drain()
        after = self._probe.loaded()
        others = [m for m in (*self._before, *after) if not same_model(m.name, self._model)]
        peak = self._sampler.peak_mb(self._started, finished) if self._sampler is not None else None
        return Performance(
            requests=requests,
            loaded=after,
            shared=bool(others),
            vram_peak_mb=peak,
            vram_baseline_mb=self._baseline,
            seed=self._client.seed,
            repeat=self._repeat,
        )

    def finish(self, *, unload: bool) -> None:
        """Stop the sampler and, if asked, drop the model from memory."""
        if self._sampler is not None:
            self._sampler.stop()
        if unload:
            self._probe.unload(self._model)
