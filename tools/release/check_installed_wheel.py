#!/usr/bin/env python3
"""Check a pip-installed wheel: every console script loads, and the data files are there.

The wheel is built by Bazel and tested against the source tree (``tests/unit/test_wheel.py``).
This is the other half, run by ``build_wheel.yml`` after ``pip install``: in a clean environment,
from a folder that is not the repository (the repository root would shadow the installed package
with the source tree), what ``pip`` put there has to start.

Usage (the interpreter of the environment the wheel is installed in):

    QT_QPA_PLATFORM=offscreen python -I tools/release/check_installed_wheel.py
"""

from __future__ import annotations

from importlib import metadata, resources
import sys

DISTRIBUTION = "teachers-teammate"

#: One asset and one help page: the rest is held to pyproject and the sources by the Bazel test.
_DATA_FILES = ("assets/teachers_teammate.png", "_docs/index.md")


def console_script_problems() -> list[str]:
    """Load every console script of the installed distribution; return what fails."""
    problems: list[str] = []
    scripts = [
        ep
        for ep in metadata.distribution(DISTRIBUTION).entry_points
        if ep.group == "console_scripts"
    ]
    if not scripts:
        return [f"{DISTRIBUTION} declares no console scripts"]
    for entry in scripts:
        try:
            entry.load()
        except (ImportError, AttributeError) as exc:
            problems.append(f"{entry.name} = {entry.value}: {exc}")
        else:
            print(f"ok  {entry.name} = {entry.value}")
    return problems


def data_file_problems() -> list[str]:
    """Return the data files the installed package lacks."""
    package = resources.files("teachers_teammate")
    return [
        f"teachers_teammate/{name} is missing"
        for name in _DATA_FILES
        if not package.joinpath(name).is_file()
    ]


def main() -> int:
    """Run the checks; return 1 when the installed wheel cannot start."""
    problems = console_script_problems() + data_file_problems()
    for problem in problems:
        print(f"FAIL {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
