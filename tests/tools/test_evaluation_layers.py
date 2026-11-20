"""The layers of the evaluation tooling: an import that reaches up is a test failure.

``tools/evaluation/BUILD`` declares one target per layer and its ``deps`` are the graph, so Bazel
refuses a module that imports a layer above it.  This test reads the same graph from the source and
names the file and the line, which a build error about a target does not.

``core`` is the bottom: it may import the app's domain, whose types both sides speak, and nothing
else of the app at run time.  An import of the infrastructure is allowed under ``TYPE_CHECKING``,
where it costs nothing and keeps the record's types honest.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

BASE = "tools/evaluation"
"""The package whose layers this test keeps in order."""

LAYERS: dict[str, frozenset[str]] = {
    "core": frozenset(),
    "metrics": frozenset({"core"}),
    "input": frozenset({"core", "metrics"}),
    "store": frozenset({"core", "metrics"}),
    "convert": frozenset({"core", "input", "metrics", "store"}),
    "run": frozenset({"core", "input", "metrics", "store"}),
    "report": frozenset({"core", "metrics", "store"}),
    "viewer": frozenset({"core", "input", "metrics", "report", "run", "store"}),
    "checks": frozenset({"core", "metrics", "run"}),
}
"""Each layer and the layers it may import, which is the ``deps`` of its target in the ``BUILD``."""

VIEWER = "report/viewer"
"""The one layer that is a folder inside another: the page may import ``run``, ``report`` may not."""

APP = "teachers_teammate"
"""The app the tooling sits beside."""

DOMAIN = "teachers_teammate.domain"
"""The one part of the app ``core`` may import at run time: the types both sides speak."""


def _tree(path: Path) -> ast.Module:
    """Return the syntax tree of *path*."""
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _layer(rel: Path) -> str:
    """Return the layer of *rel*, a path below the tooling's root, or ``""`` when it is none."""
    parts = rel.parts
    if not parts:
        return ""
    if parts[0] == "report" and len(parts) > 1 and parts[1] == "viewer":
        return "viewer"
    return parts[0] if parts[0] in LAYERS else ""


def _relative(rel: Path, node: ast.ImportFrom) -> Path:
    """Return the module *node* names, as a path below the tooling's root."""
    base = rel.parent
    for _ in range(node.level - 1):
        base = base.parent
    parts = node.module.split(".") if node.module else []
    return base / Path(*parts) if parts else base


def _absolute(name: str) -> Path:
    """Return the module *name* names, as a path below the tooling's root, or ``Path()``."""
    prefix = f"{BASE.replace('/', '.')}."
    if not name.startswith(prefix):
        return Path()
    return Path(*name[len(prefix) :].split("."))


def _imports(rel: Path, tree: ast.Module) -> list[tuple[int, str]]:
    """Return ``(line, layer)`` for every import of *tree* that names a layer of the tooling."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            found.append((node.lineno, _layer(_relative(rel, node))))
        elif isinstance(node, ast.Import):
            found += [(node.lineno, _layer(_absolute(alias.name))) for alias in node.names]
    return [(line, layer) for line, layer in found if layer]


def _problems(base: Path) -> list[str]:
    """Return one line per import that reaches above the layer that makes it."""
    problems: list[str] = []
    for path in sorted(base.rglob("*.py")):
        rel = path.relative_to(base)
        layer = _layer(rel)
        if not layer:
            continue
        for line, target in _imports(rel, _tree(path)):
            if target != layer and target not in LAYERS[layer]:
                problems.append(
                    f"{rel.as_posix()}:{line}: {layer} imports {target}, which is above it"
                )
    return problems


def _type_checking_lines(tree: ast.Module) -> set[int]:
    """Return the lines of every block that only runs for a type checker."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not node.body:
            continue
        test = node.test
        name = test.id if isinstance(test, ast.Name) else getattr(test, "attr", "")
        if name == "TYPE_CHECKING":
            last = node.body[-1]
            found.update(range(node.body[0].lineno, (last.end_lineno or last.lineno) + 1))
    return found


def _app_imports(node: ast.Import | ast.ImportFrom) -> list[str]:
    """Return the dotted names of the app that *node* imports."""
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    module = node.module or ""
    if module == APP:
        return [f"{APP}.{alias.name}" for alias in node.names]
    return [module] if module else []


def _core_problems(base: Path) -> list[str]:
    """Return one line per import of the app above the domain that ``core`` makes at run time."""
    problems: list[str] = []
    for path in sorted((base / "core").rglob("*.py")):
        rel = path.relative_to(base)
        tree = _tree(path)
        lazy = _type_checking_lines(tree)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for name in _app_imports(node):
                if not name.startswith(f"{APP}.") or name.startswith(f"{DOMAIN}."):
                    continue
                if node.lineno not in lazy:
                    problems.append(
                        f"{rel.as_posix()}:{node.lineno}: core imports {name} at run time"
                    )
    return problems


def test_no_layer_imports_a_layer_above_it():
    """
    Given  every module of ``tools/evaluation``
    When   each relative and absolute import is read
    Then   none reaches above the layer that makes it, so the graph is the one the BUILD declares
    """
    problems = _problems(ROOT / BASE)

    assert problems == [], "\n".join(problems)


def test_core_imports_the_app_only_for_the_type_checker():
    """
    Given  every module of ``tools/evaluation/core``
    When   each import of the app outside its domain is read
    Then   it sits under ``TYPE_CHECKING``, so the bottom layer needs no infrastructure to start
    """
    problems = _core_problems(ROOT / BASE)

    assert problems == [], "\n".join(problems)


def test_the_rule_recognises_what_it_forbids(tmp_path: Path):
    """
    Given  a tree whose store imports the report, and whose core imports the infrastructure
    When   the two searches read it
    Then   each one finds its own line, and the import under ``TYPE_CHECKING`` is not a hit
    """
    files = {
        "core/__init__.py": "",
        "core/records.py": (
            "from typing import TYPE_CHECKING\n"
            "\n"
            "from teachers_teammate.domain.proofreading.review import CorrectionReview\n"
            "\n"
            "if TYPE_CHECKING:\n"
            "    from teachers_teammate.infrastructure.ollama_utils import ChatStats\n"
            "\n"
            "from teachers_teammate.infrastructure.ollama_utils import ChatStats\n"
        ),
        "store/__init__.py": "",
        "store/reader.py": "from ..report import load\n",
        "report/__init__.py": "",
        "report/viewer/__init__.py": "",
        "report/viewer/page.py": "from ...store import reader\n",
    }
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")

    assert _problems(tmp_path) == ["store/reader.py:1: store imports report, which is above it"]
    assert _core_problems(tmp_path) == [
        "core/records.py:8: core imports teachers_teammate.infrastructure.ollama_utils at run time"
    ]


def test_the_tooling_tree_is_available():
    """
    Given  the repository, or the runfiles tree Bazel builds
    When   the modules are listed
    Then   every layer of the graph is there, so the two searches above are not vacuous
    """
    base = ROOT / BASE
    if not base.is_dir():
        pytest.skip("the repository tree is not available")

    found = {_layer(path.relative_to(base)) for path in base.rglob("*.py")}

    assert found - {""} == set(LAYERS)
