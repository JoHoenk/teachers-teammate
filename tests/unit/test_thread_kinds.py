"""Only three classes in the GUI own a ``QThread``; everything else uses one of them.

A query runs on the shared pool (``gui/_background.py``), a long job through
``gui/_long_job.py``.  Each kind keeps the rule for its lifetime in one place, and a new
``QThread`` subclass of its own would be a second copy of that rule.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

_PACKAGE = "teachers_teammate"

#: ``(module below the package, class)`` that may name ``QThread`` as a base: the two workers
#: that own a run of their own, and the one base of every long job.
ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        ("gui/_long_job.py", "LongJobThread"),
        ("gui/_worker.py", "OCRWorker"),
        ("gui/benchmark/_benchmark_worker.py", "BenchmarkWorker"),
    }
)


def _package_dir() -> Path:
    """Return the package folder, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / _PACKAGE
    return Path(__file__).resolve().parents[2] / _PACKAGE


def _base_name(base: ast.expr) -> str:
    """Return the name a base class is written with (``QThread``, ``x.QThread``)."""
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _qthread_subclasses() -> set[tuple[str, str]]:
    """Return ``(module, class)`` for every class that subclasses ``QThread`` itself."""
    package = _package_dir()
    found: set[tuple[str, str]] = set()
    for path in sorted(package.rglob("*.py")):
        module = path.relative_to(package).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and "QThread" in {
                _base_name(base) for base in node.bases
            }:
                found.add((module, node.name))
    return found


def test_only_the_three_known_classes_subclass_qthread() -> None:
    """
    Given  the whole package
    When   every class that names ``QThread`` as a base class is looked up
    Then   they are the two workers of a run and the one base of the long jobs — so a new
           piece of background work is a pool task or a ``LongJobThread``, and the lifetime
           rules stay in the modules that already state them
    """
    assert _qthread_subclasses() == set(ALLOWED)
