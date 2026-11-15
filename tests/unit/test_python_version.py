"""The one Python version: `.python-version` and the interpreter the tests run on.

`.python-version` holds the full patch (3.14.10).  rules_python reads it for the Bazel
toolchain, `setup-python` reads it in CI, and `tools/build/build.sh` / `build.bat` name the same
minor.  This test is what keeps the file and the interpreter from drifting apart: a bump that
touches only one of them fails here instead of in a bundle nobody looked at.
"""

from __future__ import annotations

import os
from pathlib import Path
import platform


def _version_file() -> Path:
    """Return `.python-version` from the Bazel runfiles, or from the source checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get("PYTHON_VERSION_FILE", "")
    if root and relative:
        return Path(root) / relative
    return Path(__file__).resolve().parents[2] / ".python-version"


def test_the_interpreter_is_the_one_in_python_version() -> None:
    """
    Given  the interpreter Bazel runs the tests with
    When   its version is compared with `.python-version`
    Then   they are the same, so the tests and the bundles cannot drift apart
    """
    expected = _version_file().read_text(encoding="utf-8").strip()

    assert platform.python_version() == expected
