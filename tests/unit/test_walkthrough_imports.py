"""What the app walkthrough may import: only what the shipped bundle carries.

``tests/e2e/walkthrough.py`` runs inside the app's own process, and in the bundle that process
holds the app's modules, Qt, Pillow and python-docx — no test framework, no ``QtTest``, nothing
of ``tests``.  So the script's imports are a closed list, and a new one is a conscious edit of
this file: the first bundle run proves it is there.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: The script under test.
SCRIPT = Path(__file__).resolve().parents[1] / "e2e" / "walkthrough.py"

#: The standard library the bundle carries.  ``collections.abc`` and
#: ``typing`` are here for the type annotations every module of the app uses.
STDLIB = frozenset(
    {
        "__future__",
        "argparse",
        "collections.abc",
        "contextlib",
        "dataclasses",
        "faulthandler",
        "json",
        "logging",
        "os",
        "pathlib",
        "shutil",
        "sys",
        "tempfile",
        "threading",
        "time",
        "traceback",
        "typing",
    }
)

#: The third-party packages the bundle carries.
THIRD_PARTY = frozenset({"PySide6", "PIL", "docx"})

#: The app's own package: anything below it is in the bundle.
APP = "teachers_teammate"


def _imported(tree: ast.AST) -> set[str]:
    """Return what *tree* imports, each as far as the check needs it."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def _allowed(name: str) -> bool:
    """Whether *name* is in the bundle."""
    if name == APP or name.startswith(APP + "."):
        return True
    if name.split(".", maxsplit=1)[0] in THIRD_PARTY:
        return True
    return name in STDLIB


def test_the_walkthrough_imports_only_what_the_bundle_carries() -> None:
    """
    Given  the app walkthrough script
    When   its imports are read
    Then   every one of them is a module the shipped bundle holds
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))

    assert _imported(tree), "the script imports nothing?"
    assert sorted(name for name in _imported(tree) if not _allowed(name)) == []


def test_the_walkthrough_imports_nothing_from_the_tests() -> None:
    """
    Given  the app walkthrough script
    When   its imports are read
    Then   nothing of ``tests`` is among them, since the bundle carries no test package
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))

    assert [name for name in _imported(tree) if name.split(".")[0] == "tests"] == []


def test_the_walkthrough_is_a_script_the_runner_can_execute() -> None:
    """
    Given  the app walkthrough script
    When   its module level is read
    Then   it has a ``main`` entry and runs on ``__main__``, which is what the runner expects
    """
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT))

    functions = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    guards = [
        node
        for node in tree.body
        if isinstance(node, ast.If) and ast.unparse(node.test) == "__name__ == '__main__'"
    ]
    assert "main" in functions
    assert guards, 'the script has no `if __name__ == "__main__"` block'
