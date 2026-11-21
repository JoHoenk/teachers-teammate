"""The ratchet on the size of a class in ``gui/``.

A class that grew into a dozen jobs is split; this test keeps it from growing back.  A class
over the limit must be listed below at the size it has today: shrinking it means lowering the
entry by hand, so the ratchet only turns one way.

Two numbers are counted: the lines of the class and the attributes it assigns to itself.  The
second is what tells a window that kept every section from a window that hands each section to
an object of its own.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: How many lines one class may have.
LIMIT = 800

#: How many ``self.x = …`` names one class may assign.
ATTRIBUTE_LIMIT = 35

#: The packages whose classes the ratchet covers.
SCANNED = "teachers_teammate/gui"

#: The classes still over the limit, at the size they have today.
OVERSIZED: dict[str, int] = {
    "teachers_teammate/gui/_config_panel.py::ConfigPanel": 1094,
    "teachers_teammate/gui/_main_window.py::MainWindow": 1427,
}

#: The classes still over the attribute limit, at the number they assign today.
OVERSIZED_ATTRIBUTES: dict[str, int] = {
    "teachers_teammate/gui/_main_window.py::MainWindow": 41,
}


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _key(path: Path, node: ast.ClassDef) -> str:
    """Return the ``path::Class`` name the lists below are keyed by."""
    root = _root()
    return f"{path.relative_to(root).as_posix()}::{node.name}"


def _trees() -> list[tuple[Path, ast.ClassDef]]:
    """Return every class under ``gui/`` with the file it is defined in."""
    root = _root()
    found: list[tuple[Path, ast.ClassDef]] = []
    for path in sorted((root / SCANNED).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend((path, node) for node in ast.walk(tree) if isinstance(node, ast.ClassDef))
    return found


def _classes() -> dict[str, int]:
    """Return the size in lines of every class under ``gui/``, by ``path::Class``."""
    return {
        _key(path, node): (node.end_lineno or node.lineno) - node.lineno + 1
        for path, node in _trees()
    }


def _assigned_attributes(node: ast.ClassDef) -> set[str]:
    """Return the ``self.x`` names the class assigns, also from a helper nested in a method."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Assign):
            targets = list(inner.targets)
        elif isinstance(inner, (ast.AnnAssign, ast.AugAssign)):
            targets = [inner.target]
        else:
            continue
        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and isinstance(target.value, ast.Name)
                and target.value.id == "self"
            ):
                names.add(target.attr)
    return names


def _attribute_counts() -> dict[str, int]:
    """Return how many attributes every class under ``gui/`` assigns, by ``path::Class``."""
    return {_key(path, node): len(_assigned_attributes(node)) for path, node in _trees()}


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


def test_no_class_assigns_more_attributes_than_allowed() -> None:
    """
    Given  every class of the GUI and the limit of 35 attributes
    When   the names each class assigns to itself are counted
    Then   each one is either under the limit or on the list at its recorded number
    """
    counts = _attribute_counts()
    grown = [
        f"  {name}: {count} attributes, the entry says {OVERSIZED_ATTRIBUTES[name]}"
        for name, count in counts.items()
        if name in OVERSIZED_ATTRIBUTES and count > OVERSIZED_ATTRIBUTES[name]
    ]
    assert not grown, (
        "Hand the new state to the object that owns it instead of the window:\n" + "\n".join(grown)
    )

    over = [
        f"  {name}: {count} attributes"
        for name, count in counts.items()
        if name not in OVERSIZED_ATTRIBUTES and count > ATTRIBUTE_LIMIT
    ]
    assert not over, (
        f"These classes assign more than {ATTRIBUTE_LIMIT} attributes and are not on the list; "
        "split them, or add them at their number when a phase has to hand them over as they "
        "are:\n" + "\n".join(over)
    )


def test_every_attribute_entry_is_the_number_the_class_has() -> None:
    """
    Given  the list of classes still over the attribute limit
    When   it is compared with what the sources say
    Then   each entry matches, so a class that handed state over had its entry lowered
    """
    counts = _attribute_counts()
    stale = [
        f"  {name}: the entry says {recorded}, the class has {counts.get(name, 0)}"
        for name, recorded in OVERSIZED_ATTRIBUTES.items()
        if counts.get(name, 0) < recorded
    ]
    assert not stale, "Lower these entries to the number the class has now:\n" + "\n".join(stale)
