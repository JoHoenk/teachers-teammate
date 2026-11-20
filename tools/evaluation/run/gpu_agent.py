"""Report the graphics card's and the main memory's use over HTTP.

Ollama's ``/api/ps`` is Ollama's own bookkeeping, not a reading of the card: on the 8 GB laptop
it reported about 1 GB for ``gemma4:12b`` while ``nvidia-smi`` showed 6.9 GB.  The fit check
therefore has to read the card itself, and the machine with the card is not the machine that runs
the tool.  This program runs on that machine and answers what it reads, the way Ollama already
answers there.

It is **one file, standard library only, no import from this repository**, so it can be copied
alone to any machine with Python 3.9 or later::

    python3 gpu_agent.py [--vendor auto|amd|nvidia] [--source auto|sysfs|tool]
                         [--bind 127.0.0.1] [--port 11436]
                         [--token SECRET | --token-file PATH]
                         [--interval 0.2] [--keep-minutes 10]

It runs no command a client names, reads no path a client names, serves no file, and rejects
bodies over 1 KB.  It tells memory figures and nothing else.  It listens on the loopback address
unless another one is given, and a token is compared with :func:`hmac.compare_digest`.

All sizes are bytes.  Windows are cut on the agent's own clock, because the tool's and the
server's clocks were two seconds apart.
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Callable, Sequence
import ctypes
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import NamedTuple
import urllib.parse

#: The version of the answer format, so a client can refuse an agent it does not understand.
AGENT_VERSION = 1

#: A request body larger than this is refused: the agent reads no body it needs.
MAX_BODY_BYTES = 1024

#: How many windows may be open at once; opening one more drops the oldest.
MAX_WINDOWS = 8

#: A window older than this is discarded, so a forgotten one cannot hold memory for ever.
WINDOW_MAX_AGE_S = 3600.0

#: Without a sample this recent the agent says it has nothing to report.
STALE_AFTER_S = 2.0

MIB = 1024 * 1024

#: The PCI vendor id of AMD, as sysfs spells it.
AMD_VENDOR_ID = "0x1002"

#: Where the kernel lists the graphics cards.
SYSFS_DRM_ROOT = Path("/sys/class/drm")

#: How many lines of a tool's output are kept for the parser to look at.
TOOL_LINES_KEPT = 64

#: The longest pause between two starts of a tool that keeps ending.
TOOL_RESTART_MAX_S = 30.0

#: How long the agent waits for a tool's first reading before it gives up on the card.
TOOL_FIRST_READING_S = 15.0


class AgentSetupError(RuntimeError):
    """This machine has no graphics card the agent can read."""


class CardReading(NamedTuple):
    """One graphics card as one sample found it."""

    index: int
    name: str
    used_bytes: int
    total_bytes: int


class Sample(NamedTuple):
    """What one tick read: the card's use and the main memory still available."""

    at_s: float
    card_used_bytes: int
    ram_available_bytes: int | None


# ── readers: pure functions over what the machine prints ──────────────────────


def _as_int(text: str) -> int | None:
    """Return *text* as an integer, or ``None`` when it is not one."""
    stripped = text.strip()
    return int(stripped) if stripped.isdigit() else None


def read_sysfs_amd(root: Path = SYSFS_DRM_ROOT) -> list[CardReading]:
    """Return the AMD cards under *root*, read from sysfs.

    The kernel lists a connector entry per output (``card0-DP-1``) next to the card itself
    (``card0``), so only the plain names count.  A card whose files cannot be read is skipped:
    a card that is asleep is not a reason to stop reporting the others.
    """
    cards: list[CardReading] = []
    for entry in sorted(root.glob("card[0-9]*")):
        if re.fullmatch(r"card\d+", entry.name) is None:
            continue
        device = entry / "device"
        try:
            vendor = (device / "vendor").read_text().strip()
            used = int((device / "mem_info_vram_used").read_text().strip())
            total = int((device / "mem_info_vram_total").read_text().strip())
        except (OSError, ValueError):
            continue
        if vendor.lower() != AMD_VENDOR_ID:
            continue
        cards.append(CardReading(len(cards), entry.name, used, total))
    return cards


def parse_amd_smi(text: str) -> list[CardReading]:
    """Return the cards of the last sample in ``amd-smi monitor -v --csv`` output.

    The tool repeats a header line before every sample, so the columns are found by name each
    time.  Its ``MB`` is a mebibyte: a 16 GB card reads ``vram_total`` 16304, which is
    17095983104 bytes.
    """
    columns: dict[str, int] = {}
    rows: list[list[str]] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.strip().split(",")]
        if "vram_used" in fields:
            columns = {name: index for index, name in enumerate(fields)}
            rows = []
            continue
        if not columns or len(fields) <= columns["vram_used"]:
            continue
        rows.append(fields)
    cards: list[CardReading] = []
    for fields in rows:
        used = _as_int(fields[columns["vram_used"]])
        if used is None:
            continue
        total = _as_int(fields[columns["vram_total"]]) if "vram_total" in columns else None
        gpu = fields[columns["gpu"]] if "gpu" in columns else str(len(cards))
        cards.append(CardReading(len(cards), f"AMD GPU {gpu}", used * MIB, (total or 0) * MIB))
    return cards


def parse_nvidia_smi(text: str) -> list[CardReading]:
    """Return the cards of the last tick of ``nvidia-smi --query-gpu=...`` output.

    One line per card per tick, in MiB.  A tick starts at card 0, so the last tick is the last
    group of lines.
    """
    groups: list[list[CardReading]] = []
    for line in text.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 4:
            continue
        index = _as_int(fields[0])
        used = _as_int(fields[2])
        total = _as_int(fields[3])
        if index is None or used is None or total is None:
            continue
        if index == 0 or not groups:
            groups.append([])
        groups[-1].append(CardReading(index, fields[1], used * MIB, total * MIB))
    return groups[-1] if groups else []


def parse_meminfo(text: str) -> tuple[int, int] | None:
    """Return ``(available_bytes, total_bytes)`` from ``/proc/meminfo`` text.

    ``MemAvailable`` counts reclaimable page cache as available, and Ollama maps the model file,
    so the drop of available memory is a hint, not an exact figure.
    """
    values: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        fields = rest.split()
        if not fields:
            continue
        value = _as_int(fields[0])
        if value is not None:
            values[name.strip()] = value * 1024
    available = values.get("MemAvailable")
    total = values.get("MemTotal")
    if available is None or total is None:
        return None
    return available, total


class MemoryStatusEx(ctypes.Structure):
    """The ``MEMORYSTATUSEX`` structure ``GlobalMemoryStatusEx`` fills."""

    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _windows_memory_status() -> MemoryStatusEx | None:
    """Fill a :class:`MemoryStatusEx` from the Windows API, or ``None`` elsewhere."""
    if platform.system() != "Windows":
        return None
    status = MemoryStatusEx()
    # The API is told how large the structure it fills is.
    status.dwLength = ctypes.sizeof(MemoryStatusEx)  # pylint: disable=W0201
    kernel32 = ctypes.windll.kernel32  # ty: ignore[unresolved-attribute]  # Windows only
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return status


def windows_memory(
    fill: Callable[[], MemoryStatusEx | None] | None = None,
) -> tuple[int, int] | None:
    """Return ``(available_bytes, total_bytes)`` of the main memory, or ``None``.

    *fill* is the seam a test replaces, so the Windows reader is checked on any machine.
    """
    status = (fill or _windows_memory_status)()
    if status is None:
        return None
    return int(status.ullAvailPhys), int(status.ullTotalPhys)


def read_ram() -> tuple[int, int] | None:
    """Return ``(available_bytes, total_bytes)`` of this machine's main memory, or ``None``."""
    if platform.system() == "Windows":
        return windows_memory()
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_meminfo(text)


# ── the ring buffer and the windows ───────────────────────────────────────────


class SampleBuffer:
    """The samples of the last *keep_s* seconds, oldest first."""

    def __init__(self, keep_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._keep_s = keep_s
        self._clock = clock
        self._lock = threading.Lock()
        self._samples: list[Sample] = []

    def add(self, sample: Sample) -> None:
        """Note *sample* and drop what is older than the buffer keeps."""
        with self._lock:
            self._samples.append(sample)
            oldest = sample.at_s - self._keep_s
            while self._samples and self._samples[0].at_s < oldest:
                self._samples.pop(0)

    def latest(self) -> Sample | None:
        """Return the newest sample, or ``None`` when there is none."""
        with self._lock:
            return self._samples[-1] if self._samples else None

    def between(self, start_s: float, end_s: float) -> list[Sample]:
        """Return the samples taken between *start_s* and *end_s*, both included."""
        with self._lock:
            return [sample for sample in self._samples if start_s <= sample.at_s <= end_s]


class Window:
    """One open measurement window: the sample it was opened with."""

    def __init__(self, number: int, start: Sample) -> None:
        self.number = number
        self.start = start


class Agent:
    """What the HTTP interface reports: the cards, the samples and the open windows."""

    def __init__(  # noqa: PLR0913  # what the agent is, all keyword-only
        self,
        *,
        vendor: str,
        source: str,
        interval_s: float,
        keep_s: float,
        cards: Sequence[CardReading] = (),
        ram_total_bytes: int | None,
        read_sample: Callable[[], tuple[int, int | None]],
        clock: Callable[[], float] = time.monotonic,
        agent_sha256: str = "",
        read_cards: Callable[[], Sequence[CardReading]] | None = None,
    ) -> None:
        self.vendor = vendor
        self.source = source
        self.interval_s = interval_s
        # A tool delivers its cards at its own pace, so *read_cards* is asked each time;
        # without it the cards are the fixed ones.
        self._fixed_cards = tuple(cards)
        self._read_cards = read_cards
        self.ram_total_bytes = ram_total_bytes
        self._read_sample = read_sample
        self._clock = clock
        self._sha256 = agent_sha256
        self._buffer = SampleBuffer(keep_s, clock)
        self._windows: dict[int, Window] = {}
        self._next_window = 1
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def cards(self) -> list[CardReading]:
        """The cards as the source knows them now."""
        if self._read_cards is not None:
            return list(self._read_cards())
        return list(self._fixed_cards)

    # ── sampling ─────────────────────────────────────────────────────────────

    def take_sample(self) -> Sample:
        """Read the machine once and keep the reading."""
        card_used, ram_available = self._read_sample()
        sample = Sample(self._clock(), card_used, ram_available)
        self._buffer.add(sample)
        return sample

    def start(self) -> None:
        """Take the first sample and keep sampling in the background."""
        self.take_sample()
        self._thread = threading.Thread(
            target=self._sample_forever, name="gpu-agent-sampler", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop sampling."""
        self._stop.set()

    def _sample_forever(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.take_sample()
            except Exception:  # noqa: BLE001  # one bad reading must not end the agent
                continue

    def stale(self) -> bool:
        """Return whether no sample arrived recently enough to report."""
        latest = self._buffer.latest()
        return latest is None or self._clock() - latest.at_s > STALE_AFTER_S

    # ── the API ──────────────────────────────────────────────────────────────

    def info(self) -> dict:
        """Return what the agent is and what it last read."""
        latest = self._buffer.latest()
        cards = self.cards
        return {
            "agent": AGENT_VERSION,
            "agent_sha256": self._sha256,
            "platform": platform.system().lower(),
            "vendor": self.vendor,
            "source": self.source,
            "interval_s": self.interval_s,
            "cards": [
                {"index": card.index, "name": card.name, "total_bytes": card.total_bytes}
                for card in cards
            ],
            "card_total_bytes": sum(card.total_bytes for card in cards),
            "ram_total_bytes": self.ram_total_bytes,
            "now": None
            if latest is None
            else {
                "card_used_bytes": latest.card_used_bytes,
                "ram_available_bytes": latest.ram_available_bytes,
            },
        }

    def open_window(self) -> dict:
        """Open a window, take a sample at once, and return the window and that sample."""
        sample = self.take_sample()
        with self._lock:
            self._drop_stale(sample.at_s)
            number = self._next_window
            self._next_window += 1
            self._windows[number] = Window(number, sample)
            while len(self._windows) > MAX_WINDOWS:
                del self._windows[min(self._windows)]
        return {
            "window": number,
            "start": {
                "card_used_bytes": sample.card_used_bytes,
                "ram_available_bytes": sample.ram_available_bytes,
            },
        }

    def close_window(self, number: int) -> dict | None:
        """Close window *number* and return its statistics, or ``None`` when it is not open."""
        with self._lock:
            window = self._windows.pop(number, None)
        if window is None:
            return None
        end = self.take_sample()
        samples = self._buffer.between(window.start.at_s, end.at_s)
        card_values = [sample.card_used_bytes for sample in samples]
        ram_values = [
            sample.ram_available_bytes
            for sample in samples
            if sample.ram_available_bytes is not None
        ]
        return {
            "window": number,
            "card": {
                "start_bytes": window.start.card_used_bytes,
                "peak_bytes": max(card_values),
                "mean_bytes": int(sum(card_values) / len(card_values)),
                "min_bytes": min(card_values),
                "end_bytes": end.card_used_bytes,
            },
            "ram": {
                "start_available_bytes": window.start.ram_available_bytes,
                "min_available_bytes": min(ram_values) if ram_values else None,
                "end_available_bytes": end.ram_available_bytes,
            },
            "samples": len(samples),
            "duration_s": round(end.at_s - window.start.at_s, 3),
        }

    def _drop_stale(self, now: float) -> None:
        """Forget the windows that were never closed and are older than an hour."""
        for number, window in list(self._windows.items()):
            if now - window.start.at_s > WINDOW_MAX_AGE_S:
                del self._windows[number]


# ── the HTTP interface ────────────────────────────────────────────────────────


class AgentServer(ThreadingHTTPServer):
    """The agent's HTTP server: it carries the state every request reports."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], agent: Agent, token: str | None) -> None:
        super().__init__(address, _Handler)
        self.agent = agent
        self.token = token


class _Handler(BaseHTTPRequestHandler):
    """Answers the four requests of the API, and nothing else."""

    server_version = "gpu-agent"
    sys_version = ""

    @property
    def _agent(self) -> Agent:
        server = self.server
        assert isinstance(server, AgentServer)
        return server.agent

    @property
    def _token(self) -> str | None:
        server = self.server
        assert isinstance(server, AgentServer)
        return server.token

    def do_GET(self) -> None:  # the name is the base class's protocol
        self._handle("GET")

    def do_POST(self) -> None:  # the name is the base class's protocol
        self._handle("POST")

    def do_PUT(self) -> None:  # the name is the base class's protocol
        self._handle("PUT")

    do_DELETE = do_PUT
    do_PATCH = do_PUT
    do_HEAD = do_PUT
    do_OPTIONS = do_PUT

    def log_message(self, format: str, *args: object) -> None:  # pylint: disable=redefined-builtin
        """Stay quiet: the agent is a background service, not a conversation."""

    def _handle(self, method: str) -> None:
        if not self._token_ok():
            self._send(401, {"error": "wrong token"})
            return
        path = urllib.parse.urlsplit(self.path).path
        if path != "/" and path.endswith("/"):
            path = path.rstrip("/")
        closing = re.fullmatch(r"/window/(\d+)/close", path)
        if path == "/info":
            if method != "GET":
                self._method_not_allowed()
                return
            self._send(200, self._agent.info())
            return
        if path == "/window" or closing is not None:
            if method != "POST":
                self._method_not_allowed()
                return
            if not self._body_ok():
                return
            if self._agent.stale():
                self._send(503, {"error": "no samples"})
                return
            if closing is None:
                self._send(200, self._agent.open_window())
                return
            stats = self._agent.close_window(int(closing.group(1)))
            if stats is None:
                self._send(404, {"error": "no such window"})
            else:
                self._send(200, stats)
            return
        self._send(404, {"error": "no such path"})

    def _method_not_allowed(self) -> None:
        self._send(405, {"error": "method not allowed"})

    def _token_ok(self) -> bool:
        expected = self._token
        if expected is None:
            return True
        given = self.headers.get("X-Token") or ""
        return hmac.compare_digest(given, expected)

    def _body_ok(self) -> bool:
        """Read and discard the body, refusing one that is larger than the agent accepts."""
        length = _as_int(self.headers.get("Content-Length") or "0") or 0
        if length > MAX_BODY_BYTES:
            self._send(413, {"error": "body too large"})
            return False
        if length:
            self.rfile.read(length)
        return True

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":  # an answer to HEAD has the headers only
            self.wfile.write(body)


# ── the sample sources ───────────────────────────────────────────────────────


class ToolStream:
    """Runs a command that prints samples, and keeps the latest reading.

    The tool delivers at its own pace, so the sampler reads whatever arrived last.  A tool that
    ends is started again after 1 s, then 2, 4 … up to :data:`TOOL_RESTART_MAX_S`.
    """

    def __init__(
        self,
        command: Sequence[str],
        parse: Callable[[str], list[CardReading]],
        *,
        lines_kept: int = TOOL_LINES_KEPT,
        restart_max_s: float = TOOL_RESTART_MAX_S,
    ) -> None:
        self._command = list(command)
        self._parse = parse
        self._lines: deque[str] = deque(maxlen=lines_kept)
        self._restart_max_s = restart_max_s
        self._lock = threading.Lock()
        self._cards: list[CardReading] = []
        self._first_reading = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start reading the tool in the background."""
        self._thread = threading.Thread(target=self._run, name="gpu-agent-tool", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop reading, and let the tool end."""
        self._stop.set()

    def cards(self) -> list[CardReading]:
        """Return the cards of the latest reading, or an empty list before the first one."""
        with self._lock:
            return list(self._cards)

    def wait_for_cards(self, timeout_s: float) -> bool:
        """Wait until the tool has delivered a reading; return whether it did in time."""
        return self._first_reading.wait(timeout_s)

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            process = self._start()
            if process is not None:
                delay = 1.0
                self._read(process)
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, self._restart_max_s)

    def _start(self) -> subprocess.Popen | None:
        try:
            # The command is the agent's own, never one a client names.
            return subprocess.Popen(
                self._command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError:
            return None

    def _read(self, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is not None:
            for line in stream:
                self._feed(line)
                if self._stop.is_set():
                    break
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _feed(self, line: str) -> None:
        self._lines.append(line)
        cards = self._parse("\n".join(self._lines))
        if cards:
            with self._lock:
                self._cards = cards
            self._first_reading.set()


def choose_vendor_and_source(
    vendor: str,
    source: str,
    *,
    sysfs_root: Path | None = None,
    which: Callable[[str], str | None] | None = None,
    system: str | None = None,
) -> tuple[str, str]:
    """Return the vendor and the source to read, or raise :class:`AgentSetupError`.

    The three seams are looked up when the function runs, not when it is defined, so a test can
    replace them.
    """
    root = SYSFS_DRM_ROOT if sysfs_root is None else sysfs_root
    find = shutil.which if which is None else which
    name = (system or platform.system()).lower()
    amd_sysfs = bool(read_sysfs_amd(root))
    has_nvidia = find("nvidia-smi") is not None
    has_amd_tool = find("amd-smi") is not None

    if vendor == "auto":
        if amd_sysfs:
            vendor = "amd"
        elif has_nvidia:
            vendor = "nvidia"
        elif has_amd_tool:
            vendor = "amd"
        else:
            raise AgentSetupError("no supported graphics card found")

    if vendor == "amd" and name == "windows":
        raise AgentSetupError("no AMD source on Windows: neither sysfs nor amd-smi is there")

    if source == "auto":
        if vendor == "amd" and amd_sysfs:
            source = "sysfs"
        elif vendor == "amd" and has_amd_tool:
            source = "tool"
        elif vendor == "nvidia" and has_nvidia:
            source = "tool"
        else:
            raise AgentSetupError(f"no source for {vendor} on this machine")

    if source == "sysfs" and not amd_sysfs:
        raise AgentSetupError("no AMD card in sysfs")
    if source == "tool" and vendor == "nvidia" and not has_nvidia:
        raise AgentSetupError("nvidia-smi is not on the PATH")
    if source == "tool" and vendor == "amd" and not has_amd_tool:
        raise AgentSetupError("amd-smi is not on the PATH")
    return vendor, source


def tool_command(vendor: str, interval_s: float) -> list[str]:
    """Return the command that prints samples for *vendor*."""
    if vendor == "nvidia":
        return [
            "nvidia-smi",
            "--query-gpu=index,name,memory.used,memory.total",
            "--format=csv,noheader,nounits",
            f"-lms={int(interval_s * 1000)}",
        ]
    return ["amd-smi", "monitor", "-v", "--csv", "-w", "1"]


def build_agent(options: argparse.Namespace) -> Agent:
    """Return the agent the options describe, with its sample source started."""
    vendor, source = choose_vendor_and_source(options.vendor, options.source)
    ram = read_ram()
    ram_total = ram[1] if ram is not None else None
    keep_s = options.keep_minutes * 60.0

    read_cards: Callable[[], Sequence[CardReading]] | None = None
    if source == "sysfs":
        cards = read_sysfs_amd()

        def read_sample() -> tuple[int, int | None]:
            current = read_sysfs_amd()
            memory = read_ram()
            return sum(card.used_bytes for card in current), (memory[0] if memory else None)

    else:
        parse = parse_nvidia_smi if vendor == "nvidia" else parse_amd_smi
        stream = ToolStream(tool_command(vendor, options.interval), parse)
        stream.start()
        # Without the first reading the agent would report no card and a total of zero.
        if not stream.wait_for_cards(TOOL_FIRST_READING_S):
            stream.stop()
            raise AgentSetupError(f"{tool_command(vendor, options.interval)[0]} gave no reading")
        cards = []
        read_cards = stream.cards

        def read_sample() -> tuple[int, int | None]:
            memory = read_ram()
            return sum(card.used_bytes for card in stream.cards()), (memory[0] if memory else None)

    return Agent(
        vendor=vendor,
        source=source,
        interval_s=options.interval,
        keep_s=keep_s,
        cards=cards,
        ram_total_bytes=ram_total,
        read_sample=read_sample,
        agent_sha256=own_sha256(),
        read_cards=read_cards,
    )


def own_sha256() -> str:
    """Return the SHA-256 of this file, so a run can name the agent that measured."""
    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    except OSError:
        return ""


# ── the command line ─────────────────────────────────────────────────────────


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report the graphics card's and the main memory's use over HTTP."
    )
    parser.add_argument("--vendor", choices=("auto", "amd", "nvidia"), default="auto")
    parser.add_argument("--source", choices=("auto", "sysfs", "tool"), default="auto")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11436)
    parser.add_argument("--token", default=None)
    parser.add_argument("--token-file", default=None)
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--keep-minutes", type=float, default=10.0)
    return parser.parse_args(argv)


def read_token(options: argparse.Namespace) -> str | None:
    """Return the token clients must send, read once from the file when one is named."""
    if options.token_file:
        return Path(options.token_file).read_text(encoding="utf-8").strip()
    return options.token


def main(argv: Sequence[str] | None = None) -> int:
    """Run the agent until it is interrupted."""
    options = _parse_args(argv)
    try:
        token = read_token(options)
    except OSError as error:
        print(f"gpu_agent: cannot read the token file: {error}", file=sys.stderr)
        return 2
    try:
        agent = build_agent(options)
    except AgentSetupError as error:
        print(f"gpu_agent: {error}", file=sys.stderr)
        return 2
    server = AgentServer((options.bind, options.port), agent, token)
    agent.start()
    print(
        f"gpu_agent: {agent.vendor}/{agent.source} on http://{options.bind}:{options.port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        agent.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
