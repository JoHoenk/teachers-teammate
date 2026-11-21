"""The ratchet on the size of a class in ``gui/``.

A class that grew into a dozen jobs is split; this test keeps it from growing back.  A class
over the limit must be listed below at the size it has today: shrinking it means lowering the
entry by hand, so the ratchet only turns one way.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: How many lines one class may have.
LIMIT = 800

#: The packages whose classes the ratchet covers.
SCANNED = "teachers_teammate/gui"

#: The classes still over the limit, at the size they have today.
OVERSIZED: dict[str, int] = {
    "teachers_teammate/gui/_config_panel.py::ConfigPanel": 1094,
    "teachers_teammate/gui/_main_window.py::MainWindow": 1999,
}


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _classes() -> dict[str, int]:
    """Return the size in lines of every class under ``gui/``, by ``path::Class``."""
    root = _root()
    sizes: dict[str, int] = {}
    for path in sorted((root / SCANNED).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                end = node.end_lineno or node.lineno
                key = f"{path.relative_to(root).as_posix()}::{node.name}"
                sizes[key] = end - node.lineno + 1
    return sizes


def test_no_class_grows_past_the_limit() -> None:
    """
    Given  every class of the GUI and the limit of 800 lines
    When   their sizes are counted
    Then   each one is either under the limit or on the list at its recorded size
    """
    grown = [
        f"  {name}: {size} lines, the entry says {OVERSIZED[name]}"
        for name, size in _classes().items()
        if name in OVERSIZED and size > OVERSIZED[name]
    ]
    assert not grown, "Split the class instead of letting it grow:\n" + "\n".join(grown)

    over = [
        f"  {name}: {size} lines"
        for name, size in _classes().items()
        if name not in OVERSIZED and size > LIMIT
    ]
    assert not over, (
        f"These classes are over {LIMIT} lines and not on the list; split them, or add them at "
        "their size when a phase has to hand them over as they are:\n" + "\n".join(over)
    )


def test_every_entry_is_the_size_the_class_has() -> None:
    """
    Given  the list of classes still over the limit
    When   it is compared with what the sources say
    Then   each entry matches, so a class that shrank had its entry lowered
    """
    sizes = _classes()
    stale = [
        f"  {name}: the entry says {recorded}, the class has {sizes.get(name, 0)}"
        for name, recorded in OVERSIZED.items()
        if sizes.get(name, 0) < recorded
    ]
    assert not stale, "Lower these entries to the size the class has now:\n" + "\n".join(stale)
