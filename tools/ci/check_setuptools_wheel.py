#!/usr/bin/env python3
"""Check that a setuptools-built wheel carries the assets ``pyproject.toml`` declares.

A source install builds this wheel: setuptools reads the ``package-data`` list, so a file the
list names must be in it.  The help pages are not (see ``[build-system]`` in ``pyproject.toml``).

Usage (from the repository root, after ``python -m pip wheel --no-deps -w <folder> .``):

    python tools/ci/check_setuptools_wheel.py <folder>
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import tomllib
import zipfile

PACKAGE = "teachers_teammate"


def declared_assets(pyproject: Path) -> set[str]:
    """Return the wheel paths ``package-data`` in *pyproject* promises."""
    declared = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    names = declared["tool"]["setuptools"]["package-data"][PACKAGE]
    return {f"{PACKAGE}/{name}" for name in names}


def missing_assets(wheel: Path, wanted: set[str]) -> list[str]:
    """Return the members of *wanted* that *wheel* does not hold, sorted."""
    with zipfile.ZipFile(wheel) as archive:
        return sorted(wanted - set(archive.namelist()))


def main(argv: Sequence[str] | None = None) -> int:
    """Check the one wheel of a folder against ``pyproject.toml``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path, help="the folder holding the built wheel")
    parser.add_argument("--pyproject", type=Path, default=Path("pyproject.toml"))
    options = parser.parse_args(argv)
    wheels = sorted(options.folder.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(f"{options.folder} holds {len(wheels)} wheels, expected one")
    wanted = declared_assets(options.pyproject)
    missing = missing_assets(wheels[0], wanted)
    if not wanted or missing:
        raise SystemExit(f"{wheels[0].name} does not carry: {missing}")
    print(f"{wheels[0].name} carries the {len(wanted)} declared assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
