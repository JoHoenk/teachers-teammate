#!/usr/bin/env python3
"""Check that every add-on still installs next to the bundle's packages.

The installed app gives pip the bundle's versions as constraints
(``teachers_teammate/_build/addon_constraints.txt``).  A new release of an add-on's package can
need another numpy or pydantic than the one the bundle ships; pip then refuses, and a teacher
sees the failure under Settings → Add-ons.  This script finds that out at build time: it runs
the built app's own pip in dry-run mode, one add-on at a time, and nothing is installed.

The add-ons' packages are not in the test lock on purpose, so this is the only place the latest
``langchain-openai`` and friends are held against the bundle.

Usage (from the repo root, after the build, inside the build environment):

    python tools/build/check_addon_resolution.py dist/teachers-teammate/teachers-teammate
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

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
    (``addon_manager.pip_policy_arguments``), plus ``--dry-run``.
    """
    return [
        str(app),
        "--_pip_install_mode",
        "install",
        "--dry-run",
        "--ignore-installed",
        "--target",
        str(target),
        "--only-binary",
        ":all:",
        "--no-cache-dir",
        "-c",
        str(constraints),
        *requirements,
    ]


def check_addon(app: Path, constraints: Path, name: str, requirements: list[str]) -> str:
    """Return ``""`` when *requirements* resolve against the bundle, else the end of pip's output."""
    with tempfile.TemporaryDirectory(prefix="addon_resolution_") as target:
        result = subprocess.run(
            dry_run_command(app, constraints, Path(target), requirements),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    if result.returncode == 0:
        return ""
    output = (result.stdout + result.stderr).strip().splitlines()
    return f"{name} ({' '.join(requirements)}):\n    " + "\n    ".join(output[-12:])


def main(argv: list[str] | None = None) -> int:
    """Check every add-on; return 1 when one cannot be installed next to the bundle."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("app", type=Path, help="the built executable (dist/.../teachers-teammate)")
    args = parser.parse_args(argv)
    from teachers_teammate.infrastructure.addon_manager import addon_requirements  # noqa: PLC0415

    try:
        constraints = find_constraints(args.app)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    problems = []
    for name, requirements in addon_requirements().items():
        problem = check_addon(args.app, constraints, name, requirements)
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
