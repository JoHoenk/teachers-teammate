"""WSL (Windows Subsystem for Linux): am I in it, and where is the Windows computer?

An app that runs inside a WSL session reaches a program on the Windows side — Ollama, for
instance — under an address of its own, not ``127.0.0.1``.  Reading it needs two small files
and nothing else: no subprocess and no call to ``wsl.exe``, which would start a distribution.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from ..domain.ollama_discovery import windows_host_addresses as _windows_host_addresses

_ROUTE_TABLE = Path("/proc/net/route")
_RESOLV_CONF = Path("/etc/resolv.conf")
_KERNEL_VERSION = Path("/proc/version")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def running_in_wsl() -> bool:
    """Return whether this process runs inside a WSL session."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    return "microsoft" in _read(_KERNEL_VERSION).lower()


@functools.cache
def windows_host_addresses() -> tuple[str, ...]:
    """Return the addresses of the Windows computer behind this WSL session (``()`` outside)."""
    if not running_in_wsl():
        return ()
    return _windows_host_addresses(_read(_ROUTE_TABLE), _read(_RESOLV_CONF))
