"""Bazel py_test entry point: runs pytest on a single test file.

Usage (set via the ``args`` attribute in each ``py_test`` BUILD target):

    python run_test.py unit/test_foo.py [extra pytest args …]

The path is interpreted relative to the directory that contains this script
(i.e. the ``tests/`` package root), so conftest.py is always discoverable via
pytest's package-based collection.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

import pytest


def _prepare_qt_runtime() -> None:
    """Best-effort setup so PySide6 can locate libQt6*.so in Bazel runfiles."""
    qt_lib_dirs: list[Path] = []
    for entry in map(Path, sys.path):
        candidate = entry / "PySide6" / "Qt" / "lib"
        if candidate.is_dir() and candidate not in qt_lib_dirs:
            qt_lib_dirs.append(candidate)

    if not qt_lib_dirs:
        return

    ld_paths = [str(p) for p in qt_lib_dirs]
    if os.environ.get("LD_LIBRARY_PATH"):
        ld_paths.append(os.environ["LD_LIBRARY_PATH"])
    os.environ["LD_LIBRARY_PATH"] = ":".join(ld_paths)

    # Preload Qt libs to satisfy extension-module dependencies in strict sandboxes.
    for lib_dir in qt_lib_dirs:
        libs = sorted(lib_dir.glob("libQt6*.so.6"))
        for lib in libs:
            try:
                ctypes.CDLL(str(lib), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                pass


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
    _prepare_qt_runtime()
    _prepare_tmp_runtime()
    this_dir = os.path.dirname(os.path.abspath(__file__))
    test_path = os.path.join(this_dir, sys.argv[1])
    args = [test_path, "--tb=short", "-p", "no:cacheprovider", *sys.argv[2:]]
    sys.exit(pytest.main(args))


if __name__ == "__main__":
    main()
