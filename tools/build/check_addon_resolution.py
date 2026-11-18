#!/usr/bin/env python3
"""Check that every add-on still installs next to the bundle's packages.

The installed app gives pip the bundle's versions as constraints
(``teachers_teammate/_build/addon_constraints.txt``).  A new release of an add-on's package can
need another numpy or pydantic than the one the bundle ships; pip then refuses, and a teacher
sees the failure under Settings → Add-ons.  This script finds that out at build time: it runs
pip in dry-run mode, one add-on at a time, and nothing is installed.

The add-ons' packages are not in the test lock on purpose, so this is the only place the latest
``langchain-openai`` and friends are held against the bundle.

Every add-on is resolved twice when the add-on lock is given: *newest* (the bundle's constraints
only, so a new release that no longer fits is found) and *locked* (the bundle's constraints plus
``addon_lock.txt``, which is exactly what the installed app gives pip).  The second run is the one
that proves a teacher's install works: a lock pin that conflicts with the bundle, or has no wheel
on one system, fails it.

Two modes:

* With the built app, inside the build environment (the bundle's own pip, as the app runs it):

      python tools/build/check_addon_resolution.py dist/teachers-teammate/teachers-teammate

* Without a built app, on any system, against the constraints of a build environment
  (``.github/workflows/addon_resolution.yml`` runs this weekly on all three build runners):

      python tools/build/addon_constraints.py
      python tools/build/addon_lock.py
      python tools/build/check_addon_resolution.py --python .venv/bin/python \\
          --constraints teachers_teammate/_build/addon_constraints.txt \\
          --lock teachers_teammate/_build/addon_lock.txt

With ``--report DIR`` pip writes what the *locked* install would bring, one JSON file per
add-on.  ``tools/licenses/inventory.py --check-resolved DIR`` then holds that against the license
inventory: the add-on lock is resolved on Linux, so a dependency that only exists on Windows or
macOS never appears in it, and this is the check that finds one.  The *newest* run is not
reported: its versions are newer than the lock by design.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from functools import partial
from pathlib import Path
import re
import subprocess
import sys
import tempfile

#: Longest an add-on's dry run may take (pip downloads metadata, and some wheels, to resolve).
TIMEOUT_S = 900


def _find_in_bundle(app: Path, filename: str, what: str) -> Path:
    """Return *filename* from the build folder of the built app *app* is the executable of.

    PyInstaller 6 keeps the bundle's files in ``_internal`` next to the executable.

    Raises:
        FileNotFoundError: when there is none (the build did not bundle it).
    """
    relative = Path("teachers_teammate") / "_build" / filename
    candidates = [app.parent / "_internal" / relative, app.parent / relative]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no {what} in the bundle: " + ", ".join(map(str, candidates)))


def find_constraints(app: Path) -> Path:
    """Return the constraints file inside the built app (see :func:`_find_in_bundle`)."""
    return _find_in_bundle(app, "addon_constraints.txt", "add-on constraints file")


def find_lock(app: Path) -> Path:
    """Return the add-on lock inside the built app (see :func:`_find_in_bundle`)."""
    return _find_in_bundle(app, "addon_lock.txt", "add-on lock")


def _report_arguments(report: Path | None) -> list[str]:
    """Return the ``--report`` argument, or nothing when the caller wants no report."""
    return [] if report is None else ["--report", str(report)]


def _constraint_arguments(constraints: Path, lock: Path | None) -> list[str]:
    """Return the ``-c`` arguments the app passes: the bundle's constraints, then the lock."""
    arguments = ["-c", str(constraints)]
    if lock is not None:
        arguments += ["-c", str(lock)]
    return arguments


def dry_run_command(
    app: Path,
    constraints: Path,
    target: Path,
    requirements: list[str],
    lock: Path | None = None,
    report: Path | None = None,
) -> list[str]:
    """Return the command that resolves *requirements* with the app's pip and installs nothing.

    The arguments are the ones the app uses for a real install
    (``addon_manager.pip_policy_arguments``, ``--upgrade`` included: the newest release is
    what the weekly job exists to try), plus ``--dry-run``.  With *lock* the add-on lock is a
    second ``-c``, as in the installed app.  With *report* pip writes what it would install as
    JSON, which ``tools/licenses/inventory.py --check-resolved`` reads.
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
        *_report_arguments(report),
        *_constraint_arguments(constraints, lock),
        *requirements,
    ]


def python_dry_run_command(
    interpreter: Path,
    constraints: Path,
    target: Path,
    requirements: list[str],
    lock: Path | None = None,
    report: Path | None = None,
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
        *_report_arguments(report),
        *_constraint_arguments(constraints, lock),
        *requirements,
    ]


def _run(
    command_for: Callable[[Path, Path | None], list[str]],
    name: str,
    requirements: list[str],
    report: Path | None = None,
) -> str:
    """Return ``""`` when the command succeeds, else the end of pip's output.

    With *report* pip writes what it would install to that path, which
    ``tools/licenses/inventory.py --check-resolved`` then reads.
    """
    with tempfile.TemporaryDirectory(prefix="addon_resolution_") as target:
        result = subprocess.run(
            command_for(Path(target), report),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
            check=False,
        )
    if result.returncode == 0:
        return ""
    output = (result.stdout + result.stderr).strip().splitlines()
    return f"{name} ({' '.join(requirements)}):\n    " + "\n    ".join(output[-12:])


def check_addon(
    app: Path,
    constraints: Path,
    name: str,
    requirements: list[str],
    lock: Path | None = None,
    report: Path | None = None,
) -> str:
    """Return ``""`` when *requirements* resolve against the bundle, else pip's last lines."""
    return _run(
        lambda target, path: dry_run_command(app, constraints, target, requirements, lock, path),
        name,
        requirements,
        report,
    )


def check_addon_with_python(
    interpreter: Path,
    constraints: Path,
    name: str,
    requirements: list[str],
    lock: Path | None = None,
    report: Path | None = None,
) -> str:
    """The same check without a built app, with *interpreter*'s own pip."""
    return _run(
        lambda target, path: python_dry_run_command(
            interpreter, constraints, target, requirements, lock, path
        ),
        name,
        requirements,
        report,
    )


def _slug(name: str) -> str:
    """Return *name* as a file name: the add-on's label, without spaces or punctuation."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def addons_to_check() -> dict[str, list[str]]:
    """Return the add-ons to resolve: every one the app offers under Settings → Add-ons."""
    from teachers_teammate.infrastructure.addon_manager import (  # noqa: PLC0415
        addon_requirements,
    )

    return addon_requirements()


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
    parser.add_argument(
        "--lock",
        type=Path,
        default=None,
        help="the add-on lock for --python (teachers_teammate/_build/addon_lock.txt); "
        "without it only the newest releases are tried, not what the app installs",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        metavar="DIR",
        help="write what the locked install would bring, one JSON file per add-on, into DIR; "
        "tools/licenses/inventory.py --check-resolved reads them (needs --lock)",
    )
    args = parser.parse_args(argv)

    try:
        check, lock = _checker(args, parser)
    except FileNotFoundError as exc:
        print(exc)
        return 1

    if args.report is not None:
        if lock is None:
            parser.error("--report needs --lock: only the locked install is what the app brings")
        args.report.mkdir(parents=True, exist_ok=True)

    return _check_all(check, lock, args.report)


def _checker(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> tuple[Callable[..., str], Path | None]:
    """Return the function that checks one add-on, and the lock it uses (or ``None``).

    Raises:
        FileNotFoundError: a constraints or lock file the chosen mode needs is missing.
    """
    if args.python is not None:
        if args.constraints is None:
            parser.error("--python needs --constraints")
        if not args.constraints.is_file():
            raise FileNotFoundError(
                f"{args.constraints} is missing; run tools/build/addon_constraints.py first"
            )
        lock: Path | None = None
        if args.lock is not None:
            if not args.lock.is_file():
                raise FileNotFoundError(
                    f"{args.lock} is missing; run tools/build/addon_lock.py first"
                )
            lock = args.lock
        return partial(check_addon_with_python, args.python, args.constraints), lock
    if args.app is None:
        parser.error("give the built app, or --python and --constraints")
    return partial(check_addon, args.app, find_constraints(args.app)), find_lock(args.app)


def _check_all(check: Callable[..., str], lock: Path | None, report_dir: Path | None) -> int:
    """Check every add-on in each mode; return 1 when one cannot be installed."""
    problems = []
    for name, requirements in addons_to_check().items():
        # "newest": the bundle's constraints alone, to meet a release the lock does not know yet.
        # "locked": what the installed app passes to pip, to prove a teacher's install resolves.
        modes: list[tuple[str, Path | None]] = [("newest", None)]
        if lock is not None:
            modes.append(("locked", lock))
        for mode, mode_lock in modes:
            # Only the locked run is reported: it is what the installed app brings, so its
            # versions are the inventory's.  The newest run exists to meet a release the lock
            # does not know yet, and its versions are newer by design.
            report = None
            if report_dir is not None and mode_lock is not None:
                report = report_dir / f"{_slug(name)}.json"
            problem = check(f"{name} [{mode}]", requirements, mode_lock, report)
            print(f"{'FAIL' if problem else 'ok  '} {name} [{mode}]: {' '.join(requirements)}")
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
