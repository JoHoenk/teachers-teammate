#!/usr/bin/env python3
"""Check what the bundle's smoke test reported against what the repository says it should be.

``.python-version`` is the one place the Python version is written down: rules_python builds the
Bazel tests with it, ``actions/setup-python`` installs it for the bundle, and the smoke test
(``<bundle> --_smoke_test smoke.json``) reports what PyInstaller actually froze.  A mismatch means
the bundle and the tests disagree.

The versions it reports are held to the inventory for the same reason: the Qt and OpenCV the
frozen bundle carries decide which sources we owe, so a lock bump that reaches the bundle before
``third_party_licenses/inventory.csv`` and NOTICE fails here, not in a teacher's hands.

Usage (from the repo root, after a smoke test):

    python tools/build/check_smoke.py smoke.json
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON_VERSION_FILE = ROOT / ".python-version"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.licenses import artifacts  # noqa: E402


def _read(smoke: Path) -> dict:
    """Return the smoke result *smoke* holds.

    Raises:
        ValueError: the file is not a smoke result.
    """
    try:
        return json.loads(smoke.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"{smoke} holds no smoke result: {exc}") from exc


def bundle_python(smoke: Path) -> str:
    """Return the Python version the smoke test reported.

    Raises:
        ValueError: the file is not a smoke result, or carries no Python version.
    """
    try:
        return str(_read(smoke)["versions"]["python"])
    except (KeyError, TypeError) as exc:
        raise ValueError(f"{smoke} holds no smoke result with a Python version: {exc}") from exc


def smoke_versions(smoke: Path) -> dict[str, str]:
    """Return the versions of the runtime packages the smoke test reported.

    Raises:
        ValueError: the file is not a smoke result, or carries no versions.
    """
    try:
        versions = _read(smoke)["versions"]
        return {str(name): str(value) for name, value in versions.items()}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{smoke} holds no smoke result with a version: {exc}") from exc


def check_python_version(actual: str, expected: str) -> str | None:
    """Return a message when the bundle's Python differs from *expected*, else ``None``."""
    if actual != expected:
        return f"the bundle runs on Python {actual}, .python-version says {expected}"
    return None


def check_locked_versions(reported: Mapping[str, str], tree: Path = ROOT) -> list[str]:
    """Return the versions the bundle reported that the inventory of *tree* disagrees with."""
    locked = artifacts.locked_versions(tree)
    return artifacts.version_problems(reported, locked, artifacts.SMOKE_REPORTED)


def main(argv: list[str] | None = None) -> int:
    """Hold the smoke result's Python version and bundled versions to the repository."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("smoke", type=Path, help="the smoke.json the bundle wrote")
    parser.add_argument(
        "--python-version-file",
        type=Path,
        default=PYTHON_VERSION_FILE,
        help="the file that names the one Python version (default: .python-version)",
    )
    parser.add_argument(
        "--tree",
        type=Path,
        default=ROOT,
        help="the repository whose inventory the versions are held to (default: this checkout)",
    )
    args = parser.parse_args(argv)

    expected = args.python_version_file.read_text(encoding="utf-8").strip()
    try:
        actual = bundle_python(args.smoke)
        reported = smoke_versions(args.smoke)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    mismatch = check_python_version(actual, expected)
    problems = [] if mismatch is None else [mismatch]
    problems += check_locked_versions(reported, args.tree)
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"the bundle runs on Python {actual} and carries the versions the inventory names")
    return 0


if __name__ == "__main__":
    sys.exit(main())
