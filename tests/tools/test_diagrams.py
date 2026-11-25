"""The diagrams say what the code has: the exception tree and the diagrams a page shows."""

from __future__ import annotations

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

DIAGRAMS = ROOT / "docs" / "assets"


def _exception_bases() -> dict[str, list[str]]:
    """Return every exception class of ``exceptions.py`` with the names of its bases."""
    tree = ast.parse((ROOT / "teachers_teammate" / "exceptions.py").read_text(encoding="utf-8"))
    return {
        node.name: [base.id for base in node.bases if isinstance(base, ast.Name)]
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.endswith(("Error", "Skipped"))
    }


def test_the_exception_diagram_has_every_exception_and_its_base() -> None:
    """
    Given the exception classes of ``exceptions.py``
    When they are compared with ``error_handling.puml``
    Then every class is drawn, with an edge to each base class it has
    """
    diagram = (DIAGRAMS / "error_handling.puml").read_text(encoding="utf-8")
    drawn = set(re.findall(r"^\s*class (\w+)", diagram, re.MULTILINE))
    edges = set(re.findall(r"(\w+) <\|-- (\w+)", diagram))

    code = _exception_bases()

    assert set(code) <= drawn
    missing = {(base, name) for name, bases in code.items() for base in bases} - edges
    assert not missing


def test_the_exception_diagram_draws_no_class_the_code_lacks() -> None:
    """
    Given ``error_handling.puml``
    When its package of application exceptions is read
    Then each class in it is one of ``exceptions.py``
    """
    diagram = (DIAGRAMS / "error_handling.puml").read_text(encoding="utf-8")
    package = diagram.split('package "teachers_teammate.exceptions" {', 1)[1].split("\n}", 1)[0]

    drawn = set(re.findall(r"^\s*class (\w+)", package, re.MULTILINE))

    assert drawn <= set(_exception_bases())


def test_every_diagram_is_shown_by_a_page_and_every_page_diagram_exists() -> None:
    """
    Given the ``.puml`` files and the Markdown pages of ``docs/``
    When the ``uml`` directives of the pages are collected
    Then a diagram nobody shows is not kept, and no page points at a missing one
    """
    shown: set[str] = set()
    for page in (ROOT / "docs").glob("*.md"):
        shown.update(re.findall(r"\{uml\}\s+assets/(\S+\.puml)", page.read_text(encoding="utf-8")))

    kept = {path.name for path in DIAGRAMS.glob("*.puml")}

    assert kept == shown
