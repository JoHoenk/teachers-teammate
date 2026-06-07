"""Bazel py_test entry point: runs pytest on the unit test suite.

Invoked by the ``//tests:unit_tests`` Bazel target.  Uses ``__file__`` to
locate the test files inside the runfiles tree rather than relying on a
fixed working directory.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest


def _prepare_tmp_runtime() -> None:
    """Route app temp/cache writes into Bazel sandbox temp root when available."""
    if os.environ.get("TEACHERS_TEAMMATE_TMPDIR", "").strip():
        return
    test_tmp = os.environ.get("TEST_TMPDIR", "").strip()
    if not test_tmp:
        return
    root = Path(test_tmp) / "teachers_teammate_cache"
    root.mkdir(parents=True, exist_ok=True)
    os.environ["TEACHERS_TEAMMATE_TMPDIR"] = str(root)


def main() -> None:
    """Run pytest on the unit test directory."""
    _prepare_tmp_runtime()
    # __file__ is <runfiles>/ocr/tests/pytest_main.py at test time.
    this_dir = os.path.dirname(os.path.abspath(__file__))
    unit_dir = os.path.join(this_dir, "unit")
    args = [
        unit_dir,
        "--tb=short",
        "-p",
        "no:cacheprovider",
        *sys.argv[1:],
    ]
    sys.exit(pytest.main(args))


if __name__ == "__main__":
    main()
