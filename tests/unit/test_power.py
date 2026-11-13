"""Unit tests for teachers_teammate.infrastructure.power (no test ever blocks real sleep)."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure import power
from teachers_teammate.infrastructure.power import (
    KEEP_AWAKE_REASON,
    LinuxSleepBlocker,
    MacSleepBlocker,
    NullSleepBlocker,
    WindowsSleepBlocker,
    default_sleep_blocker,
    on_battery,
)


def test_only_the_null_blocker_is_unavailable() -> None:
    """
    Given  the null blocker and the real ones
    When   available is read
    Then   only the null blocker says it has no way to ask the system
    """
    assert NullSleepBlocker().available is False
    assert LinuxSleepBlocker().available is True
    assert MacSleepBlocker().available is True


def test_null_blocker_yields_false() -> None:
    """
    Given  the null blocker
    When   hold() is used
    Then   it yields False and does nothing
    """
    with NullSleepBlocker().hold("x") as held:
        assert held is False


# ── Windows ─────────────────────────────────────────────────────────────────


class _Fn:
    """A callable that accepts attributes (``restype``), like a ctypes function object."""

    def __init__(self, call) -> None:
        self._call = call
        self.restype = None

    def __call__(self, *args):
        return self._call(*args)


class _FakeKernel32:
    """Records the Win32 calls; ``create`` is the handle PowerCreateRequest returns."""

    def __init__(self, *, create: int = 99, set_ok: int = 1) -> None:
        self.calls: list[str] = []
        self.PowerCreateRequest = _Fn(lambda _context: self._record("create", create))
        self.PowerSetRequest = _Fn(
            lambda handle, kind: self._record(f"set:{handle}:{kind}", set_ok)
        )
        self.PowerClearRequest = _Fn(lambda handle, kind: self._record(f"clear:{handle}:{kind}", 1))
        self.CloseHandle = _Fn(lambda handle: self._record(f"close:{handle}", 1))
        self.SetThreadExecutionState = _Fn(lambda flags: self._record(f"state:{flags:#x}", 1))

    def _record(self, call: str, result: int) -> int:
        self.calls.append(call)
        return result


@pytest.fixture(name="win_types")
def _win_types() -> None:
    """Skip where ``ctypes.wintypes`` cannot be imported (the Windows code builds a struct)."""
    pytest.importorskip("ctypes.wintypes")


def test_windows_blocker_sets_and_clears_the_power_request(win_types) -> None:
    """
    Given  a Windows kernel that grants the request
    When   hold() is used
    Then   the request is created and set while held, then cleared and closed afterwards
    """
    api = _FakeKernel32()
    with WindowsSleepBlocker(api).hold(KEEP_AWAKE_REASON) as held:
        assert held is True
        assert api.calls == ["create", "set:99:1"]

    assert api.calls[2:] == ["clear:99:1", "close:99"]


def test_windows_blocker_releases_when_the_body_raises(win_types) -> None:
    """
    Given  a held request and an exception inside the with block
    When   the block is left
    Then   the request is still cleared and closed
    """
    api = _FakeKernel32()
    with pytest.raises(RuntimeError), WindowsSleepBlocker(api).hold("x"):
        raise RuntimeError("boom")

    assert api.calls[-2:] == ["clear:99:1", "close:99"]


def test_windows_blocker_falls_back_to_the_thread_execution_state(win_types) -> None:
    """
    Given  a kernel whose PowerCreateRequest returns no handle
    When   hold() is used
    Then   the thread execution state flag is set while held and reset afterwards
    """
    api = _FakeKernel32(create=0)
    with WindowsSleepBlocker(api).hold("x") as held:
        assert held is True

    assert api.calls[-2:] == ["state:0x80000001", "state:0x80000000"]


def test_windows_blocker_never_raises_when_the_api_breaks() -> None:
    """
    Given  a kernel API that raises
    When   hold() is used
    Then   it yields False and the body still runs
    """
    api = MagicMock()
    api.PowerCreateRequest.side_effect = OSError("no")
    ran = []
    with WindowsSleepBlocker(api).hold("x") as held:
        ran.append(held)

    assert ran == [False]


# ── macOS / Linux helper processes ──────────────────────────────────────────


class _FakeProc:
    """A helper process that is still running (``exits=False``) or exits at once."""

    def __init__(
        self, *, exits: bool = False, stderr: bytes = b"", stdout: bytes = b"", gate=None
    ) -> None:
        self._exits = exits
        self.returncode = 1 if exits else None
        self.stderr = MagicMock(read=lambda: stderr)
        # What the helper prints; with a *gate* the first read blocks until it is set.
        self.stdout = MagicMock(
            readline=(lambda: (gate.wait(), stdout)[1]) if gate is not None else (lambda: stdout)
        )
        self.terminated = False

    def wait(self, timeout=None) -> int:
        if self._exits or self.terminated:
            return self.returncode or 0
        raise subprocess.TimeoutExpired("cmd", timeout or 0)

    def poll(self):
        return self.returncode if (self._exits or self.terminated) else None

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.terminated = True


def test_mac_blocker_runs_caffeinate_tied_to_our_process_and_stops_it() -> None:
    """
    Given  caffeinate that keeps running
    When   hold() is used
    Then   the command is caffeinate -i -w <our pid>, held is True, and it is terminated after
    """
    proc = _FakeProc()
    with patch.object(power.subprocess, "Popen", return_value=proc) as popen:
        with MacSleepBlocker().hold("x") as held:
            assert held is True
            assert popen.call_args.args[0] == ["caffeinate", "-i", "-w", str(os.getpid())]

    assert proc.terminated is True


def test_linux_blocker_command_holds_idle_and_sleep_until_we_end() -> None:
    """
    Given  the Linux blocker
    When   its command is built
    Then   it blocks idle and sleep, names the reason, and ends with our process (tail --pid)
    """
    command = LinuxSleepBlocker()._command(KEEP_AWAKE_REASON)

    assert command[0] == "systemd-inhibit"
    assert "--what=idle:sleep" in command
    assert "--mode=block" in command
    assert f"--why={KEEP_AWAKE_REASON}" in command
    assert any(f"--pid={os.getpid()}" in part for part in command)


def test_a_helper_that_exits_at_once_means_not_held() -> None:
    """
    Given  a helper that refuses and exits immediately
    When   hold() is used
    Then   held is False and the body still runs
    """
    proc = _FakeProc(exits=True, stderr=b"no permission")
    with patch.object(power.subprocess, "Popen", return_value=proc):
        with LinuxSleepBlocker().hold("x") as held:
            assert held is False


def test_linux_blocker_is_held_once_the_helper_reports_the_lock() -> None:
    """
    Given  systemd-inhibit that granted the lock (its command printed the ready line)
    When   hold() is used
    Then   held is True, and the helper is stopped afterwards
    """
    proc = _FakeProc(stdout=b"ready\n")
    with patch.object(power.subprocess, "Popen", return_value=proc):
        with LinuxSleepBlocker().hold("x") as held:
            assert held is True

    assert proc.terminated is True


def test_linux_blocker_is_not_held_when_the_lock_is_refused_late(monkeypatch) -> None:
    """
    Given  systemd-inhibit that is still waiting for the system, so no ready line comes
    When   the wait for the lock runs out
    Then   held is False (a slow refusal is not reported as held) and the helper is stopped
    """
    monkeypatch.setattr(power, "_HELPER_READY_S", 0.05)
    gate = threading.Event()
    proc = _FakeProc(gate=gate)
    try:
        with patch.object(power.subprocess, "Popen", return_value=proc):
            with LinuxSleepBlocker().hold("x") as held:
                assert held is False
    finally:
        gate.set()

    assert proc.terminated is True


def test_a_missing_helper_means_not_held() -> None:
    """
    Given  a helper program that cannot be started
    When   hold() is used
    Then   held is False (no exception)
    """
    with patch.object(power.subprocess, "Popen", side_effect=FileNotFoundError("caffeinate")):
        with MacSleepBlocker().hold("x") as held:
            assert held is False


# ── choosing the blocker ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("platform", "tools", "wsl", "expected"),
    [
        ("win32", set(), False, WindowsSleepBlocker),
        ("darwin", {"caffeinate"}, False, MacSleepBlocker),
        ("darwin", set(), False, NullSleepBlocker),
        ("linux", {"systemd-inhibit", "tail"}, False, LinuxSleepBlocker),
        ("linux", {"systemd-inhibit", "tail"}, True, NullSleepBlocker),
        ("linux", set(), False, NullSleepBlocker),
        ("freebsd14", {"caffeinate"}, False, NullSleepBlocker),
    ],
)
def test_default_sleep_blocker_by_platform(platform, tools, wsl, expected) -> None:
    """
    Given  a platform, the tools found on it and whether it is WSL
    When   default_sleep_blocker() chooses
    Then   it returns the matching blocker, and nothing where none works (WSL included)
    """
    blocker = default_sleep_blocker(
        platform=platform,
        which=lambda name: f"/usr/bin/{name}" if name in tools else None,
        in_wsl=lambda: wsl,
    )

    assert type(blocker) is expected


# ── battery ─────────────────────────────────────────────────────────────────


def _with_psutil(**attrs):
    """Return a patch that installs a fake ``psutil`` (the lean test targets have none)."""
    return patch.dict(sys.modules, {"psutil": MagicMock(**attrs)})


@pytest.mark.parametrize(
    ("battery", "expected"),
    [
        (None, None),
        (MagicMock(power_plugged=True), False),
        (MagicMock(power_plugged=False), True),
        (MagicMock(power_plugged=None), None),
    ],
)
def test_on_battery(battery, expected) -> None:
    """
    Given  psutil reporting no battery, plugged in, unplugged, or unknown
    When   on_battery() is asked
    Then   it answers None, False, True, None
    """
    with _with_psutil(sensors_battery=MagicMock(return_value=battery)):
        assert on_battery() is expected


def test_on_battery_is_none_when_psutil_raises() -> None:
    """
    Given  psutil raising (containers, some platforms)
    When   on_battery() is asked
    Then   it answers None instead of raising
    """
    with _with_psutil(sensors_battery=MagicMock(side_effect=RuntimeError("no"))):
        assert on_battery() is None


def test_on_battery_is_none_without_psutil() -> None:
    """
    Given  psutil that cannot be imported
    When   on_battery() is asked
    Then   it answers None
    """
    with patch.dict(sys.modules, {"psutil": None}):
        assert on_battery() is None
