#!/usr/bin/env python3
"""Check what the bundle's smoke test reported against what the repository says it should be.

``.python-version`` is the one place the Python version is written down: rules_python builds the
Bazel tests with it, ``actions/setup-python`` installs it for the bundle, and the smoke test
(``<bundle> --_smoke_test smoke.json``) reports what PyInstaller actually froze.  A mismatch means
the bundle and the tests disagree.

Usage (from the repo root, after a smoke test):

    python tools/build/check_smoke.py smoke.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
PYTHON_VERSION_FILE = ROOT / ".python-version"


def bundle_python(smoke: Path) -> str:
    """Return the Python version the smoke test reported.

    Raises:
        ValueError: the file is not a smoke result, or carries no Python version.
    """
    try:
        data = json.loads(smoke.read_text(encoding="utf-8"))
        version = data["versions"]["python"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"{smoke} holds no smoke result with a Python version: {exc}") from exc
    return str(version)


def check_python_version(actual: str, expected: str) -> str | None:
    """Return a message when the bundle's Python differs from *expected*, else ``None``."""
    if actual != expected:
        return f"the bundle runs on Python {actual}, .python-version says {expected}"
    return None


def main(argv: list[str] | None = None) -> int:
    """Hold the smoke result's Python version to ``.python-version``."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("smoke", type=Path, help="the smoke.json the bundle wrote")
    parser.add_argument(
        "--python-version-file",
        type=Path,
        default=PYTHON_VERSION_FILE,
        help="the file that names the one Python version (default: .python-version)",
    )
    args = parser.parse_args(argv)

    expected = args.python_version_file.read_text(encoding="utf-8").strip()
    try:
        actual = bundle_python(args.smoke)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    problem = check_python_version(actual, expected)
    if problem:
        print(problem, file=sys.stderr)
        return 1
    print(f"the bundle runs on Python {actual}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
