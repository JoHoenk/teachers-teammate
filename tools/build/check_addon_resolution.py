#!/usr/bin/env python3
"""Check that every add-on still installs next to the bundle's packages.

The installed app gives pip the bundle's versions as constraints
(``teachers_teammate/_build/addon_constraints.txt``).  A new release of an add-on's package can
need another numpy or pydantic than the one the bundle ships; pip then refuses, and a teacher
sees the failure under Settings → Add-ons.  This script finds that out at build time: it runs
pip in dry-run mode, one add-on at a time, and nothing is installed.

The add-ons' packages are not in the test lock on purpose, so this is the only place the latest
``langchain-openai`` and friends are held against the bundle.

Two modes:

* With the built app, inside the build environment (the bundle's own pip, as the app runs it):

      python tools/build/check_addon_resolution.py dist/teachers-teammate/teachers-teammate

* Without a built app, on any system, against the constraints of a build environment
  (``.github/workflows/addon_resolution.yml`` runs this weekly on all three build runners):

      python tools/build/addon_constraints.py
      python tools/build/check_addon_resolution.py --python .venv/bin/python \\
          --constraints teachers_teammate/_build/addon_constraints.txt
"""

from __future__ import annotations

import argparse
from functools import partial
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable

#: Longest an add-on's dry run may take (pip downloads metadata, and some wheels, to resolve).
TIMEOUT_S = 900


def find_constraints(app: Path) -> Path:
    """Return the constraints file inside the built app that *app* is the executable of.

    PyInstaller 6 keeps the bundle's files in ``_internal`` next to the executable.

    Raises:
        FileNotFoundError: when there is none (the build did not bundle it).
    """
    relative = Path("teachers_teammate") / "_build" / "addon_constraints.txt"
    candidates = [app.parent / "_internal" / relative, app.parent / relative]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "no add-on constraints file in the bundle: " + ", ".join(map(str, candidates))
    )


def dry_run_command(
    app: Path, constraints: Path, target: Path, requirements: list[str]
) -> list[str]:
    """Return the command that resolves *requirements* with the app's pip and installs nothing.

    The arguments are the ones the app uses for a real install
    (``addon_manager.pip_policy_arguments``, ``--upgrade`` included: the newest release is
    what the weekly job exists to try), plus ``--dry-run``.
    """
    return [
        str(app),
        "--_pip_install_mode",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--upgrade",
        "--target",
        str(target),
        "--only-binary",
        ":all:",
        "--no-cache-dir",
        "-c",
        str(constraints),
        *requirements,
    ]


def python_dry_run_command(
    interpreter: Path, constraints: Path, target: Path, requirements: list[str]
) -> list[str]:
    """Return the same dry run without a built app: *interpreter*'s own pip.

    ``--ignore-installed`` is what makes this the app's question: the build environment has the
    bundle's packages installed, and the frozen app's pip does not see them.
    """
    return [
        str(interpreter),
        "-m",
        "pip",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--upgrade",
        "--target",
        str(target),
        "--only-binary",
        ":all:",
        "--no-cache-dir",
        "-c",
        str(constraints),
        *requirements,
    ]


def _run(command_for: Callable[[Path], list[str]], name: str, requirements: list[str]) -> str:
    """Return ``""`` when the command succeeds, else the end of pip's output."""
    with tempfile.TemporaryDirectory(prefix="addon_resolution_") as target:
        result = subprocess.run(
            command_for(Path(target)),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    if result.returncode == 0:
        return ""
    output = (result.stdout + result.stderr).strip().splitlines()
    return f"{name} ({' '.join(requirements)}):\n    " + "\n    ".join(output[-12:])


def check_addon(app: Path, constraints: Path, name: str, requirements: list[str]) -> str:
    """Return ``""`` when *requirements* resolve against the bundle, else pip's last lines."""
    return _run(
        lambda target: dry_run_command(app, constraints, target, requirements), name, requirements
    )


def check_addon_with_python(
    interpreter: Path, constraints: Path, name: str, requirements: list[str]
) -> str:
    """The same check without a built app, with *interpreter*'s own pip."""
    return _run(
        lambda target: python_dry_run_command(interpreter, constraints, target, requirements),
        name,
        requirements,
    )


def addons_to_check(platform_name: str | None = None) -> dict[str, list[str]]:
    """Return the add-ons to resolve on *platform_name* (``sys.platform`` by default).

    ``pyamdgpuinfo`` reads the Linux ``amdgpu`` sysfs interface, so the AMD add-on is checked
    on Linux only — the same rule as ``addon_manager.gpu_monitoring_addon``.
    """
    from teachers_teammate.infrastructure.addon_manager import (  # noqa: PLC0415
        ADDON_AMD,
        addon_requirements,
    )

    platform_name = platform_name or sys.platform
    return {
        name: requirements
        for name, requirements in addon_requirements().items()
        if name != ADDON_AMD or platform_name.startswith("linux")
    }


def main(argv: list[str] | None = None) -> int:
    """Check every add-on; return 1 when one cannot be installed next to the bundle."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "app",
        type=Path,
        nargs="?",
        help="the built executable (dist/.../teachers-teammate); not needed with --python",
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=None,
        help="check without a built app: this interpreter's own pip, in dry-run mode",
    )
    parser.add_argument(
        "--constraints",
        type=Path,
        default=None,
        help="the constraints file for --python (teachers_teammate/_build/addon_constraints.txt)",
    )
    args = parser.parse_args(argv)

    if args.python is not None:
        if args.constraints is None:
            parser.error("--python needs --constraints")
        if not args.constraints.is_file():
            print(f"{args.constraints} is missing; run tools/build/addon_constraints.py first")
            return 1
        check = partial(check_addon_with_python, args.python, args.constraints)
    else:
        if args.app is None:
            parser.error("give the built app, or --python and --constraints")
        try:
            constraints = find_constraints(args.app)
        except FileNotFoundError as exc:
            print(exc)
            return 1
        check = partial(check_addon, args.app, constraints)

    problems = []
    for name, requirements in addons_to_check().items():
        problem = check(name, requirements)
        print(f"{'FAIL' if problem else 'ok  '} {name}: {' '.join(requirements)}")
        if problem:
            problems.append(problem)
    if problems:
        print("\nThese add-ons cannot be installed next to the bundled packages:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
