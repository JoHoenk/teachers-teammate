#!/usr/bin/env python3
"""Create the venv the bundle build and the tests install into, from the pins.

PyInstaller freezes whatever is importable in the running interpreter, so the build must not run
in a venv that carries the optional extras (langchain, spaCy, …): they would be swept into the
binary, and the license inventory covers only the base runtime. A fresh venv also leaves out the
runner's own tools (pipx and its dependencies on Windows), which check_pins.py rightly reports.

Every version comes from ``requirements_lock.txt`` plus ``tools/build/requirements_platform.txt``
(through ``pyinstaller-work/constraints.txt``; the ``dev`` venv adds the dev and add-on locks), so
two builds of one commit give the same bundle and the same license list.

An existing venv at the given folder is deleted and made again, unless it is the one running the
script: deactivate it first.

Usage (from the repo root):

    python tools/build/make_venv.py .build_venv --for build
    python tools/build/make_venv.py .venv --for dev

Standard library only, so it runs under any Python 3.14 before the venv exists. It prints the
venv's script folder on its last line: the caller puts that folder on the PATH.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
CONSTRAINTS = ROOT / "pyinstaller-work" / "constraints.txt"
CHECK_PINS = ROOT / "tools" / "build" / "check_pins.py"
MAKE_CONSTRAINTS = ROOT / "tools" / "build" / "make_constraints.py"

#: What each purpose installs on top of the constraints: the base runtime the bundle freezes, the
#: extra the tests import, or everything a developer needs.
INSTALLS: dict[str, tuple[str, ...]] = {
    "build": ("-e", ".", "pyinstaller", "pyinstaller-hooks-contrib"),
    "test": ("-e", ".[test]"),
    "dev": ("-e", ".[dev,test,llm]"),
}


def scripts_folder(venv: Path) -> Path:
    """Return the folder of the venv's executables."""
    return venv / ("Scripts" if sys.platform == "win32" else "bin")


def venv_python(venv: Path) -> Path:
    """Return the venv's interpreter."""
    return scripts_folder(venv) / ("python.exe" if sys.platform == "win32" else "python")


def run(command: list[str]) -> None:
    """Run *command* in the repository root and fail with it."""
    subprocess.run(command, cwd=ROOT, check=True)


def pip_install_command(venv: Path, purpose: str) -> list[str]:
    """Return the pip command that installs *purpose* against the pinned constraints."""
    return [
        str(venv_python(venv)),
        "-m",
        "pip",
        "install",
        "--quiet",
        "-c",
        str(CONSTRAINTS),
        *INSTALLS[purpose],
    ]


def create(venv: Path, purpose: str) -> None:
    """Delete and recreate *venv*, then install *purpose* into it from the pins.

    Only a venv is deleted: a folder without ``pyvenv.cfg`` is somebody's files, and the script
    stops instead of removing them. The venv that runs the script is not deleted either: its
    interpreter would be gone before the new one is made.
    """
    if venv.resolve() == Path(sys.prefix).resolve():
        msg = f"{venv} is the venv running this script; run `deactivate` and start it again"
        raise SystemExit(msg)
    if venv.exists():
        if not (venv / "pyvenv.cfg").is_file():
            msg = f"{venv} exists and is not a venv; remove it or choose another folder"
            raise SystemExit(msg)
        shutil.rmtree(venv)
    run([sys.executable, "-m", "venv", str(venv)])
    run([sys.executable, str(MAKE_CONSTRAINTS), *(["--dev"] if purpose == "dev" else [])])
    run(
        [
            str(venv_python(venv)),
            "-m",
            "pip",
            "install",
            "--quiet",
            "--upgrade",
            "-c",
            str(CONSTRAINTS),
            "pip",
        ]
    )
    run(pip_install_command(venv, purpose))
    if purpose == "build":
        run([str(venv_python(venv)), str(CHECK_PINS)])


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line: where the venv goes and what it installs."""
    parser = argparse.ArgumentParser(description="Create a venv from the pinned versions.")
    parser.add_argument("venv", type=Path, help="where the venv goes, for example .build_venv")
    parser.add_argument(
        "--for",
        dest="purpose",
        choices=sorted(INSTALLS),
        default="build",
        help="build installs the bundle's base runtime, test the test extra, dev every extra",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Create the venv and print its script folder."""
    args = parse_args(argv)
    venv = args.venv if args.venv.is_absolute() else Path.cwd() / args.venv
    create(venv, args.purpose)
    print(scripts_folder(venv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
