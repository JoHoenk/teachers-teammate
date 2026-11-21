"""Every reader shows the document as it is now, not the machine's texts.

The cache holds what the models produced; the teacher's work holds what the teacher changed and
decided.  ``application/merged_view.py`` is the one place the two become the view a reader shows,
so a module that reads a ``DocumentState`` text itself would show the machine's text where the
teacher's exists — in the queue, in the Check tab, in an output file.  This test keeps that from
growing back: the cache's own type may be named only in the cache, in the view it becomes, in the
merge and in the service that hands it to the merge.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

PACKAGE = "teachers_teammate"
"""The package the rule applies to."""

ALLOWED = {
    "teachers_teammate/application/document_view.py",
    "teachers_teammate/application/merged_view.py",
    "teachers_teammate/application/service.py",
}
"""The modules that may name ``DocumentState``: the view it becomes, the merge that fills in the
teacher's texts, and the service that calls both."""

CACHE = "teachers_teammate/infrastructure/"
"""The cache's own code, which is where a ``DocumentState`` is read and written."""


def _root() -> Path:
    """Return the tree that holds the package, from the runfiles or the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return ROOT


def _modules() -> list[Path]:
    """Return every module of the package, in a stable order."""
    return sorted((_root() / PACKAGE).rglob("*.py"))


def _names(path: Path) -> set[str]:
    """Return every identifier *path* mentions, as ``ast`` sees them."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }


def test_only_the_merge_names_the_cache_state() -> None:
    """
    Given  every module of the package
    When   the identifiers it mentions are collected
    Then   ``DocumentState`` appears only in the cache, in the merge and in the service, so no
           reader can show or write a machine text where the teacher's exists
    """
    root = _root()
    offenders = [
        path.relative_to(root).as_posix()
        for path in _modules()
        if "DocumentState" in _names(path)
        and path.relative_to(root).as_posix() not in ALLOWED
        and not path.relative_to(root).as_posix().startswith(CACHE)
    ]

    assert not offenders, (
        "Read the merged view instead of a DocumentState "
        "(application/merged_view.py:merged_view):\n" + "\n".join(f"  {name}" for name in offenders)
    )


def test_the_gui_names_no_cache_state_at_all() -> None:
    """
    Given  the GUI package
    When   its modules are searched for the cache's type
    Then   none of them names it, so the window reads views and never a stored document
    """
    root = _root()
    offenders = [
        path.relative_to(root).as_posix()
        for path in sorted((root / PACKAGE / "gui").rglob("*.py"))
        if "DocumentState" in _names(path)
    ]

    assert not offenders, "The window reads DocumentStateView, never DocumentState:\n" + "\n".join(
        f"  {name}" for name in offenders
    )
