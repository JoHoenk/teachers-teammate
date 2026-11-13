"""Keeping the computer awake while documents are processed, and reading the battery.

A laptop that goes to sleep pauses the run; the requests to the AI server then time out after
waking and files fail.  :func:`default_sleep_blocker` picks the platform's own mechanism without
any extra package:

=========  ============================================================  ====================
Platform   Mechanism                                                      Gone if the app dies
=========  ============================================================  ====================
Windows    a power request (``PowerCreateRequest`` / ``PowerSetRequest``)  yes (handle closes)
macOS      ``caffeinate -i -w <our pid>``                                  yes (``-w``)
Linux      ``systemd-inhibit … tail --pid=<our pid> -f /dev/null``         yes (``tail --pid``)
WSL, other nothing; the Windows side decides                               n/a
=========  ============================================================  ====================

Only *idle sleep of the system* is requested; the screen may still turn off.  Closing the lid
and a critical battery can still put the computer to sleep.  The sleep entry of the system menu
still works on Windows and macOS; on Linux the lock is a ``systemd-inhibit`` lock in block mode
for ``idle:sleep`` (the idle part alone would not stop a desktop that suspends through logind),
so logind refuses that entry, or asks for an administrator password, until the run ends.
Holding never raises: a refusal is logged and reported as ``False``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Any

from ..interfaces import SleepBlocker
from .subprocess_utils import no_console_window_kwargs
from .wsl import running_in_wsl

_logger = logging.getLogger(__name__)

KEEP_AWAKE_REASON = "Teacher's Teammate is processing documents"
"""What the system's power tools (``powercfg /requests``, ``systemd-inhibit --list``) show."""

#: Seconds a helper without a ready signal gets to refuse (exit at once) before it counts as
#: running.
_HELPER_START_S = 0.3

#: Seconds a helper with a ready signal gets to report it.  ``systemd-inhibit`` starts the
#: command only once the system has granted the lock, and asking the system can take a moment on
#: a computer that has just woken up.
_HELPER_READY_S = 5.0

# Windows: POWER_REQUEST_TYPE and flags.
_POWER_REQUEST_SYSTEM_REQUIRED = 1
_POWER_REQUEST_CONTEXT_VERSION = 0
_POWER_REQUEST_CONTEXT_SIMPLE_STRING = 0x1
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


class NullSleepBlocker(SleepBlocker):
    """Holds nothing (WSL, an unknown platform, or the teacher turned it off)."""

    @property
    def available(self) -> bool:
        return False

    def hold(self, reason: str) -> AbstractContextManager[bool]:
        _ = reason
        return _null_hold()


@contextmanager
def _null_hold() -> Iterator[bool]:
    yield False


class WindowsSleepBlocker(SleepBlocker):
    """A Windows power request of type "system required"; ``powercfg /requests`` lists it."""

    def __init__(self, kernel32: Any | None = None) -> None:
        self._kernel32 = kernel32

    def _api(self) -> Any:
        if self._kernel32 is not None:
            return self._kernel32
        import ctypes  # noqa: PLC0415

        return ctypes.WinDLL("kernel32", use_last_error=True)  # ty: ignore[unresolved-attribute]  # Windows only

    def hold(self, reason: str) -> AbstractContextManager[bool]:
        return self._hold(reason)

    @contextmanager
    def _hold(self, reason: str) -> Iterator[bool]:
        try:
            api = self._api()
            handle = self._create_request(api, reason)
        except Exception as exc:  # noqa: BLE001  # never fatal: the run goes on without it
            _logger.warning("Could not keep the computer awake: %s", exc)
            yield False
            return
        if handle:
            try:
                yield True
            finally:
                self._release_request(api, handle)
            return
        # The power request API refused (very old Windows): the per-thread flag still works,
        # set and cleared on this same thread.
        with self._execution_state(api) as held:
            yield held

    @staticmethod
    def _create_request(api: Any, reason: str) -> Any:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        class ReasonContext(ctypes.Structure):
            _fields_ = (
                ("Version", wintypes.ULONG),
                ("Flags", wintypes.DWORD),
                ("SimpleReasonString", wintypes.LPWSTR),
            )

        context = ReasonContext(
            _POWER_REQUEST_CONTEXT_VERSION, _POWER_REQUEST_CONTEXT_SIMPLE_STRING, reason
        )
        api.PowerCreateRequest.restype = wintypes.HANDLE
        handle = api.PowerCreateRequest(ctypes.byref(context))
        invalid = ctypes.c_void_p(-1).value
        if not handle or handle == invalid:
            return None
        if not api.PowerSetRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED):
            api.CloseHandle(handle)
            return None
        return handle

    @staticmethod
    def _release_request(api: Any, handle: Any) -> None:
        try:
            api.PowerClearRequest(handle, _POWER_REQUEST_SYSTEM_REQUIRED)
            api.CloseHandle(handle)
        except Exception as exc:  # noqa: BLE001  # releasing is best effort; the OS frees it at exit
            _logger.warning("Could not release the keep-awake request: %s", exc)

    @contextmanager
    def _execution_state(self, api: Any) -> Iterator[bool]:
        try:
            held = bool(api.SetThreadExecutionState(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED))
        except Exception as exc:  # noqa: BLE001  # never fatal
            _logger.warning("Could not keep the computer awake: %s", exc)
            held = False
        try:
            yield held
        finally:
            if held:
                api.SetThreadExecutionState(_ES_CONTINUOUS)


class _HelperProcessBlocker(SleepBlocker):
    """Holds by running a helper that the system counts as a reason not to sleep."""

    #: Set when the helper prints this line once the system granted the request; without it
    #: a helper that is still running after :data:`_HELPER_START_S` counts as having accepted.
    ready_line: str | None = None

    def _command(self, reason: str) -> list[str]:
        raise NotImplementedError

    def hold(self, reason: str) -> AbstractContextManager[bool]:
        return self._hold(reason)

    @contextmanager
    def _hold(self, reason: str) -> Iterator[bool]:
        proc: subprocess.Popen[Any] | None = None
        try:
            proc = subprocess.Popen(
                self._command(reason),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if self.ready_line is None else subprocess.PIPE,
                stderr=subprocess.PIPE,
                **no_console_window_kwargs(),
            )
            held = self._accepted(proc)
            if not held:
                _logger.warning("Could not keep the computer awake: %s", _stderr_of(proc))
        except OSError as exc:
            _logger.warning("Could not keep the computer awake: %s", exc)
            held = False
        try:
            yield held
        finally:
            if proc is not None:
                _stop(proc)

    def _accepted(self, proc: subprocess.Popen[Any]) -> bool:
        """Return whether the helper took the request."""
        if self.ready_line is None:
            try:
                proc.wait(timeout=_HELPER_START_S)
            except subprocess.TimeoutExpired:
                return True  # still running: the helper accepted the request
            return False  # exited at once: refused
        return _prints_line(proc, self.ready_line, _HELPER_READY_S)


def _prints_line(proc: subprocess.Popen[Any], line: str, timeout: float) -> bool:
    """Return whether *proc* prints *line* within *timeout* seconds (``False``: it ended first)."""
    answered = threading.Event()
    seen: list[bool] = []

    def read() -> None:
        try:
            first = proc.stdout.readline() if proc.stdout is not None else b""
            seen.append(first.decode("utf-8", errors="replace").strip() == line)
        except (OSError, ValueError):  # the pipe was closed under us
            seen.append(False)
        finally:
            answered.set()

    threading.Thread(target=read, daemon=True).start()
    return answered.wait(timeout) and seen == [True]


def _stderr_of(proc: subprocess.Popen[Any]) -> str:
    if proc.poll() is None:
        proc.terminate()  # it never answered: stop it before reading what it said
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    try:
        data = proc.stderr.read() if proc.stderr is not None else b""
    except OSError:
        data = b""
    return data.decode("utf-8", errors="replace").strip() or f"exit code {proc.returncode}"


def _stop(proc: subprocess.Popen[Any]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    for pipe in (proc.stdout, proc.stderr):
        if pipe is not None:
            pipe.close()


class MacSleepBlocker(_HelperProcessBlocker):
    """``caffeinate -i``: no idle sleep; ``-w`` ends it with this process."""

    def _command(self, reason: str) -> list[str]:
        _ = reason
        return ["caffeinate", "-i", "-w", str(os.getpid())]


class LinuxSleepBlocker(_HelperProcessBlocker):
    """A ``systemd-inhibit`` lock; ``tail --pid`` makes it end with this process.

    ``systemd-inhibit`` runs its command only after the system granted the lock, so the command
    prints a line first: seeing it means the lock is held, and a refusal (which ends the helper
    without it) is told apart from a slow answer.
    """

    ready_line = "ready"

    def _command(self, reason: str) -> list[str]:
        return [
            "systemd-inhibit",
            "--what=idle:sleep",
            "--mode=block",
            "--who=Teacher's Teammate",
            f"--why={reason}",
            "sh",
            "-c",
            f"echo {self.ready_line}; exec tail --pid={os.getpid()} -f /dev/null",
        ]


def default_sleep_blocker(
    *,
    platform: str | None = None,
    which: Callable[[str], str | None] = shutil.which,
    in_wsl: Callable[[], bool] = running_in_wsl,
) -> SleepBlocker:
    """Return the blocker for this computer (:class:`NullSleepBlocker` where none works)."""
    platform = platform or sys.platform
    if platform == "win32":
        return WindowsSleepBlocker()
    if platform == "darwin":
        return MacSleepBlocker() if which("caffeinate") else NullSleepBlocker()
    if platform.startswith("linux"):
        if in_wsl():
            return NullSleepBlocker()
        if which("systemd-inhibit") and which("tail"):
            return LinuxSleepBlocker()
    return NullSleepBlocker()


def on_battery() -> bool | None:
    """Return whether the computer runs on battery power; ``None`` without a readable battery."""
    try:
        import psutil  # noqa: PLC0415

        battery = psutil.sensors_battery()
    except Exception:  # noqa: BLE001  # psutil raises on some platforms/containers; treat as unknown
        return None
    if battery is None or battery.power_plugged is None:
        return None
    return not battery.power_plugged
