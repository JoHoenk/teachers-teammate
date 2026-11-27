"""The window keeps no log pane: the log file holds the record, the status bar the last line.

Only the installer dialogs and the Benchmark window keep a text pane, and that one shows progress
(pip output, benchmark steps).  This test keeps a log widget from growing back into the GUI.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: The package scanned for a log widget.
SCANNED = "teachers_teammate"

#: Class and module names that belonged to the pane.
_GONE = ("LogWidget", "_log_widget", "QtStream", "_qt_stream")


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def test_no_module_defines_a_log_pane_again() -> None:
    """
    Given  the sources of the package
    When   every class and module name is compared with the names the pane had
    Then   none of them is back: the log file carries the record and the status bar the last line
    """
    found: list[str] = []
    package = _root() / SCANNED
    for path in sorted(package.rglob("*.py")):
        if path.stem in _GONE:
            found.append(f"{path.relative_to(package.parent)}: module")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name in _GONE:
                found.append(f"{path.relative_to(package.parent)}:{node.lineno}: class {node.name}")

    assert not found, "The GUI shows a line in the status bar, not in a pane:\n" + "\n".join(found)
