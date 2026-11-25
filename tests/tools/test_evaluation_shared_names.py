"""One spelling per fact: the text id, the shared words, and the host of a run.

Three facts are written in more than one place if nothing stops it, and each of them was: the name
a text has outside its own set file, the words ``ollama`` / ``errors`` / ``correct``, and the rule
that a run's host is its machine or, for a hosted service, its provider.  Each lives once — in
:mod:`tools.evaluation.core.records`, :mod:`tools.evaluation.core.constants` and
:mod:`tools.evaluation.core.axes` — and this test fails on the first module that spells it again.

The search reads the syntax tree, not the text: a docstring that explains the rule, or a JSON key
that happens to be called ``errors``, is not a second copy of it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

THIS_FILE = Path(__file__).resolve()

TREES = ("tools",)
"""The first-party trees the rule applies to.

The app has its own vocabulary and its own copy of these words; what this test keeps is the
*tooling* from spelling them a second time beside ``core``.
"""

CORE = "tools/evaluation/core"
"""Where the one copy of each fact lives."""

SHARED_WORDS: dict[str, str] = {
    "ollama": "core.constants.OLLAMA",
    "errors": "core.constants.TextKind.ERRORS",
    "correct": "core.constants.TextKind.CORRECT",
}
"""The words that are a value somewhere and a name elsewhere, and where the value lives."""

ALLOWED_WORDS: dict[tuple[str, str], str] = {
    ("tools/evaluation/report/viewer/page.py", "errors"): "a key of the page's JSON",
    ("tools/evaluation/report/viewer/model.py", "errors"): "a key of the report model's JSON",
    ("tools/evaluation/report/spot_check.py", "correct"): "a mark's verdict, not a text's kind",
    ("tools/evaluation/run/measure.py", "correct"): "the name of a step, not a text's kind",
    ("tools/evaluation/run/protocol.py", "correct"): "the name of a step, not a text's kind",
    ("tools/evaluation/run/one_request/correction.py", "correct"): "the name of a step",
}
"""``(path, word)`` of a literal that is a name and not the value, with the reason."""


def _modules() -> list[Path]:
    """Return every first-party module the rule applies to."""
    found: list[Path] = []
    for tree in TREES:
        base = ROOT / tree
        if base.is_dir():
            found += base.rglob("*.py")
    return sorted(path for path in found if path.resolve() != THIS_FILE)


def _tree(path: Path) -> ast.Module:
    """Return the syntax tree of *path*."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _docstrings(tree: ast.Module) -> set[int]:
    """Return the line of every docstring of *tree*, which is prose and not a value."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", ())
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(first.value.lineno)
    return found


def _strings(path: Path) -> list[tuple[int, str]]:
    """Return ``(line, value)`` for every string literal of *path* that is not a docstring."""
    tree = _tree(path)
    prose = _docstrings(tree)
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.lineno not in prose
    ]


def _where(path: Path) -> str:
    """Return *path* as the repository names it."""
    return path.relative_to(ROOT).as_posix()


def test_no_module_builds_a_text_id_by_hand():
    """
    Given  every first-party module
    When   each f-string is read for the shape ``{….language}/{….id}``
    Then   only ``core.records.text_id`` builds one, so a text has one name everywhere
    """
    problems: list[str] = []
    for path in _modules():
        if _where(path).startswith(CORE):
            continue
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.JoinedStr):
                continue
            parts = node.values
            if len(parts) != 3:
                continue
            first, middle, last = parts
            if not isinstance(middle, ast.Constant) or middle.value != "/":
                continue
            if not isinstance(first, ast.FormattedValue) or not isinstance(
                last, ast.FormattedValue
            ):
                continue
            if _attribute(first.value) == "language" and _attribute(last.value) == "id":
                problems.append(f"{_where(path)}:{node.lineno}: builds a text id by hand")

    assert problems == [], "\n".join(problems)


def _attribute(node: ast.expr) -> str:
    """Return the attribute name of *node*, or ``""`` when it is not one."""
    return node.attr if isinstance(node, ast.Attribute) else ""


def test_no_module_spells_a_shared_word_itself():
    """
    Given  every first-party module outside ``core``
    When   each string literal that is not a docstring is read
    Then   none of them is ``ollama``, ``errors`` or ``correct``, except the names listed
    """
    problems: list[str] = []
    for path in _modules():
        where = _where(path)
        if where.startswith(CORE):
            continue
        for line, value in _strings(path):
            if value not in SHARED_WORDS:
                continue
            if (where, value) in ALLOWED_WORDS:
                continue
            problems.append(f"{where}:{line}: {value!r} is spelled here; use {SHARED_WORDS[value]}")

    assert problems == [], "\n".join(problems)


def test_no_module_names_the_host_by_hand():
    """
    Given  every first-party module outside ``core``
    When   each ``or`` expression is read for ``….machine or ….provider``
    Then   none builds one, because ``core.axes.host_of`` is the one rule
    """
    problems: list[str] = []
    for path in _modules():
        where = _where(path)
        if where.startswith(CORE):
            continue
        for node in ast.walk(_tree(path)):
            if not isinstance(node, ast.BoolOp) or not isinstance(node.op, ast.Or):
                continue
            names = {_attribute(value) for value in node.values}
            if {"machine", "provider"} <= names:
                problems.append(
                    f"{where}:{node.lineno}: names the host by hand; use core.axes.host_of"
                )

    assert problems == [], "\n".join(problems)


def test_the_rule_recognises_what_it_forbids(tmp_path: Path):
    """
    Given  a module that builds a text id, spells a shared word and names the host by hand
    When   the three searches read it
    Then   each one finds its own line, and a docstring that explains the rule is not a hit
    """
    source = tmp_path / "sample.py"
    source.write_text(
        '"""A docstring may say ``"errors"`` and ``machine or provider``."""\n'
        "\n"
        'name = f"{record.language}/{record.id}"\n'
        'kind = "errors"\n'
        "host = header.environment.machine or header.protocol.provider\n",
        encoding="utf-8",
    )

    tree = _tree(source)
    joined = [
        node for node in ast.walk(tree) if isinstance(node, ast.JoinedStr) and len(node.values) == 3
    ]
    assert len(joined) == 1
    assert sorted(_strings(source)) == [(3, "/"), (4, "errors")]
    hosts = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.BoolOp)
        and {"machine", "provider"} <= {_attribute(value) for value in node.values}
    ]
    assert len(hosts) == 1


def test_the_repository_tree_is_available():
    """
    Given  the repository, or the runfiles tree Bazel builds
    When   the modules are listed
    Then   there are some, so the three searches above are not vacuous
    """
    modules = _modules()
    if not modules:
        pytest.skip("the repository tree is not available")

    assert any(_where(path).startswith(CORE) for path in modules)
