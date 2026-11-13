"""The Ollama program on this computer: where it is, how to start it, how much room it has.

The app can only ask an address whether Ollama answers.  A teacher who has just installed the
app needs more than that: whether the program is there at all, and how to start it.  This
module looks for it and starts it; nothing here installs anything (Ollama's own installer
comes from another vendor and asks for administrator rights).

Starting is offered only where a normal user can do it: the tray app on Windows, the app
bundle on macOS, and the Windows tray app from inside WSL.  A plain ``ollama`` command (a
Homebrew install, ``ollama.exe`` on the ``PATH``) is started with ``serve``.  On Linux the
server is a system service, which needs administrator rights, and ``ollama serve`` run as the
user would use another models folder -- the models downloaded before would seem to have
vanished.  So Linux gets the command to type instead.

The models folder is a guess: Ollama's own settings can move it, and the app cannot see that.
It is only used to warn about too little free space before a large download.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Where Ollama is downloaded from.  The page picks the right installer for the computer.
OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"

#: The folder Ollama keeps its models in when its own settings say nothing.
_DEFAULT_MODELS_DIR = Path(".ollama") / "models"
#: The folder of the Linux system service, which runs as its own user.
_LINUX_SERVICE_MODELS_DIR = Path("/usr/share/ollama/.ollama/models")


def _which(name: str) -> list[Path]:
    """Return the program *name* on the ``PATH`` as a one-entry list, or nothing."""
    found = shutil.which(name)
    return [Path(found)] if found else []


def _windows_app_paths() -> list[Path]:
    """Return where the Windows installer puts the Ollama tray app."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    return [Path(local) / "Programs" / "Ollama" / "ollama app.exe"]


def _wsl_windows_app_paths() -> list[Path]:
    """Return the Windows tray app as a WSL session sees it (nothing outside WSL)."""
    from .wsl import running_in_wsl  # noqa: PLC0415  # reads files; only asked when needed

    if not running_in_wsl():
        return []
    users = Path("/mnt/c/Users")
    try:
        names = [entry for entry in users.iterdir() if entry.is_dir()]
    except OSError:
        return []
    return [name / "AppData" / "Local" / "Programs" / "Ollama" / "ollama app.exe" for name in names]


def _program_candidates() -> Iterator[Path]:
    """Yield the places the Ollama program may be, best first, for this platform.

    Lazy, so the slow places (the Windows user folders seen from WSL) are only looked at when
    nothing better was found.
    """
    if sys.platform == "win32":
        yield from _windows_app_paths()
        yield from _which("ollama.exe")
    elif sys.platform == "darwin":
        yield Path("/Applications/Ollama.app")
        yield Path.home() / "Applications" / "Ollama.app"
        yield from _which("ollama")
    else:
        yield from _which("ollama")
        yield Path("/usr/local/bin/ollama")
        yield Path("/usr/bin/ollama")
        yield from _wsl_windows_app_paths()


def find_ollama_program() -> Path | None:
    """Return the Ollama program on this computer, or ``None`` when none was found.

    "Not found" rather than "not installed": a custom install path cannot be seen, so the
    wording never claims more than the app knows.
    """
    return next((path for path in _program_candidates() if path.exists()), None)


def can_start_ollama() -> bool:
    """Return whether the app can start Ollama for the teacher.

    Windows and macOS, where the program is a tray app, an app bundle or a command that can
    serve; and WSL when the program found is the Windows tray app.  On Linux the server is a
    system service (see the module docstring).
    """
    if sys.platform in ("win32", "darwin"):
        return True
    program = find_ollama_program()
    return program is not None and program.suffix.lower() == ".exe"


def _start_command(program: Path) -> list[str]:
    """Return the command that starts the server behind *program*.

    The tray app and the app bundle start it themselves; a plain ``ollama`` command only
    prints its help unless it is told to ``serve``.
    """
    if sys.platform == "darwin" and program.suffix == ".app":
        return ["open", "-a", str(program)]
    if program.name.lower() in ("ollama", "ollama.exe"):
        return [str(program), "serve"]
    return [str(program)]


def start_ollama(program: Path) -> None:
    """Start the Ollama program at *program* and return at once.

    It is detached, so it keeps running when the app closes.

    Raises:
        OSError: The program could not be started.
    """
    command = _start_command(program)
    if sys.platform == "win32":
        # No console window, and the tray app outlives the app that started it.
        subprocess.Popen(
            command,
            close_fds=True,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return
    subprocess.Popen(command, close_fds=True)


def ollama_models_dir() -> Path:
    """Return the folder Ollama keeps its models in, as well as the app can tell.

    ``OLLAMA_MODELS`` wins (the person set it), then the folder of the Linux system service
    when it exists, then the default under the home folder.
    """
    configured = os.environ.get("OLLAMA_MODELS", "").strip()
    if configured:
        return Path(configured)
    if sys.platform.startswith("linux") and _LINUX_SERVICE_MODELS_DIR.exists():
        return _LINUX_SERVICE_MODELS_DIR
    return Path.home() / _DEFAULT_MODELS_DIR


def free_disk_gb(path: Path) -> float | None:
    """Return the free space (GB) of the disk *path* is on, or ``None`` when it can't be read.

    The nearest folder that exists is measured: the models folder itself may not be there yet.
    """
    for candidate in (path, *path.parents):
        try:
            usage = shutil.disk_usage(candidate)
        except OSError:
            continue
        return usage.free / 1_000_000_000
    return None
