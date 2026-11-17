"""Opt-in diagnostics for a window that starts slowly or stops answering.

Off unless asked for: start the app with ``--diagnose`` or set ``TEACHERS_TEAMMATE_DIAGNOSE=1``.
Then a file ``diagnostics.log`` in the data folder (:func:`resolve_data_root`) gets

* a line for each phase of the start (how long after the process began), and
* a report whenever the GUI thread does not answer for longer than :data:`STALL_S`: what the
  GUI thread was doing at that moment (the call stack: module, function, line) and where the
  other threads were, then a line when it answers again.

A stall cannot be explained afterwards, only while it lasts, so a second thread watches a
heartbeat the GUI thread sets from its own timer (:meth:`Diagnostics.beat`).  The file holds
code locations and times only: no document, no file name, no setting, no value of a variable.
A windowed program on Windows has no console, which is why this writes a file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import logging
from pathlib import Path
import sys
import threading
import time
import traceback
from types import FrameType
from typing import TextIO

from .storage_root import resolve_data_root

_logger = logging.getLogger(__name__)

#: The command-line switch and the environment variable that turn the diagnostics on.
FLAG = "--diagnose"
ENVIRONMENT_VARIABLE = "TEACHERS_TEAMMATE_DIAGNOSE"
LOG_NAME = "diagnostics.log"
#: How long the GUI thread may stay silent before it is reported (seconds).
STALL_S = 0.3
#: How often the watching thread looks (seconds).
POLL_S = 0.1
#: The log of an earlier session is cleared at start when it is bigger than this.
MAX_LOG_BYTES = 1_000_000
#: A stall is reported again when it reaches each of these lengths (seconds): the second
#: report shows whether the GUI thread is still in the same place.
_REPEAT_AT_S = (1.0, 3.0, 10.0, 30.0)
#: Frames shown per thread, innermost last.
_FRAMES = 14

_instance: Diagnostics | None = None


def requested(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> bool:
    """Return whether the diagnostics were asked for (``--diagnose`` or the environment)."""
    args = sys.argv if argv is None else argv
    env = _environment() if environ is None else environ
    return FLAG in args or env.get(ENVIRONMENT_VARIABLE, "").strip() not in ("", "0")


def _environment() -> Mapping[str, str]:
    import os  # noqa: PLC0415

    return os.environ


def default_log_path() -> Path:
    """Return where the log is written: the data folder of the user."""
    return resolve_data_root() / LOG_NAME


class Diagnostics:
    """The log file and the thread that watches the GUI thread's heartbeat."""

    def __init__(
        self,
        path: Path,
        *,
        stall_s: float = STALL_S,
        poll_s: float = POLL_S,
        clock: Callable[[], float] = time.monotonic,
        gui_thread_id: int | None = None,
    ) -> None:
        """Open *path* for appending.

        Args:
            path: The log file (its folder is created).
            stall_s: Silence of the GUI thread that counts as a stall.
            poll_s: How often the watching thread looks at the heartbeat.
            clock: A monotonic clock in seconds (tests pass their own).
            gui_thread_id: The thread whose heartbeat is watched; the calling thread when
                ``None``.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
            path.write_text("", encoding="utf-8")
        self._path = path
        self._file: TextIO | None = path.open("a", encoding="utf-8", buffering=1)
        self._stall_s = stall_s
        self._poll_s = poll_s
        self._clock = clock
        self._gui_thread_id = gui_thread_id or threading.get_ident()
        self._started = clock()
        self._beat = self._started
        self._lock = threading.Lock()
        self._stalled_since: float | None = None
        self._reported = 0  # how many reports of the running stall were written
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def path(self) -> Path:
        """Return the log file."""
        return self._path

    # ── what the GUI side calls ─────────────────────────────────────────────

    def note(self, text: str) -> None:
        """Write one line, with the time since the diagnostics began."""
        self._write(f"[+{self._clock() - self._started:7.3f} s] {text}")

    def phase(self, name: str) -> None:
        """Write a phase of the start, with how long the process has been running."""
        age = _process_age_s()
        suffix = f" (process age {age:.1f} s)" if age is not None else ""
        self.note(f"phase: {name}{suffix}")

    def beat(self) -> None:
        """Say that the GUI thread is answering (call it from a timer of the GUI thread)."""
        now = self._clock()
        with self._lock:
            since, self._stalled_since = self._stalled_since, None
            self._beat = now
            self._reported = 0
        if since is not None:
            self.note(f"the GUI thread answers again after {now - since:.2f} s")

    def start_watching(self) -> None:
        """Start the thread that reports a GUI thread that stays silent."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._watch, name="diagnostics", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop watching and close the file."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    # ── the watching thread ─────────────────────────────────────────────────

    def _watch(self) -> None:
        while not self._stop.wait(self._poll_s):
            self.check()

    def check(self) -> None:
        """Look at the heartbeat once and report a stall that is new or has grown."""
        now = self._clock()
        with self._lock:
            silent = now - self._beat
            if silent < self._stall_s:
                return
            if self._stalled_since is None:
                self._stalled_since = self._beat
            limit = self._next_report_s()
            if limit is None or silent < limit:
                return
            self._reported += 1
            count = self._reported
        frames = sys._current_frames()  # noqa: SLF001  # the one way to see another thread's stack
        self._report_stall(silent, count, frames)

    def _next_report_s(self) -> float | None:
        """Return the silence at which the next report is due, or ``None`` when all are done."""
        if self._reported == 0:
            return self._stall_s
        if self._reported - 1 < len(_REPEAT_AT_S):
            return _REPEAT_AT_S[self._reported - 1]
        return None

    def _report_stall(self, silent_s: float, count: int, frames: Mapping[int, FrameType]) -> None:
        lines = [
            f"the GUI thread has not answered for {silent_s:.2f} s (report {count}); it is in:"
        ]
        gui_frame = frames.get(self._gui_thread_id)
        lines += _format(gui_frame)
        names = {thread.ident: thread.name for thread in threading.enumerate()}
        for ident, frame in frames.items():
            if ident in (self._gui_thread_id, threading.get_ident()):
                continue
            lines.append(f"other thread {names.get(ident, ident)}:")
            lines += _format(frame, limit=5)
        self.note("\n".join(lines))

    # ── the file ────────────────────────────────────────────────────────────

    def _write(self, text: str) -> None:
        with self._lock:
            if self._file is None:
                return
            try:
                self._file.write(text + "\n")
            except OSError, ValueError:
                _logger.debug("the diagnostics log could not be written", exc_info=True)


def _format(frame: FrameType | None, *, limit: int = _FRAMES) -> list[str]:
    """Return the innermost *limit* calls of *frame* as ``module:line in function`` lines."""
    if frame is None:
        return ["    (no frame)"]
    stack = traceback.extract_stack(frame)[-limit:]
    return [f"    {_short(item.filename)}:{item.lineno} in {item.name}" for item in stack]


def _short(filename: str) -> str:
    """Return the part of *filename* from the package on: no user name, no folder of a user."""
    parts = Path(filename).parts
    for marker in ("teachers_teammate", "site-packages", "PySide6"):
        if marker in parts:
            return "/".join(parts[parts.index(marker) :])
    return Path(filename).name


def _process_age_s() -> float | None:
    """Return the seconds since the process started, or ``None`` when it cannot be told."""
    try:
        import psutil  # noqa: PLC0415

        return time.time() - psutil.Process().create_time()
    except Exception:  # noqa: BLE001  # a diagnostic aid must never be the reason the app fails
        return None


# ── the one instance of a session ──────────────────────────────────────────


def start_if_requested(version: str = "") -> Diagnostics | None:
    """Start the diagnostics when asked for (``--diagnose`` or the environment), once.

    Returns the instance, or ``None`` when they are off or the log cannot be opened (the app
    then runs as without them).
    """
    global _instance  # noqa: PLW0603  # pylint: disable=global-statement  # one log per process
    if _instance is not None:
        return _instance
    if not requested():
        return None
    try:
        _instance = Diagnostics(default_log_path())
    except OSError:
        _logger.warning("the diagnostics log could not be opened", exc_info=True)
        return None
    frozen = bool(getattr(sys, "frozen", False))
    _instance.note(f"diagnostics on: version {version or '?'}, {sys.platform}, frozen={frozen}")
    _instance.note(f"log file: {_instance.path.name}")
    return _instance


def get() -> Diagnostics | None:
    """Return the running diagnostics, or ``None`` when they are off."""
    return _instance


def phase(name: str) -> None:
    """Write a phase of the start when the diagnostics are on (nothing otherwise)."""
    if _instance is not None:
        _instance.phase(name)
