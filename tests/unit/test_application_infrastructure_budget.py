"""The application layer reaches into ``infrastructure`` only where this table says it does.

A service in ``application/`` is given what it uses: a collaborator comes in through its
constructor, and where a service branches on one, the branch is a Protocol in
``application/ports.py``.  What still names an ``infrastructure`` module is an object the service
was handed and has to name to call it (a store, the pipeline), or a subprocess it starts (an
installer, a download).

The table below is the ratchet: a module that does not import ``infrastructure`` is not in it,
and a count that does not match fails.  So a removal lowers a number here, and a new import is a
decision taken in this file.  It counts the names imported, not the statements, so joining two
imports changes nothing.  ``composition.py`` is not in the table: naming the concrete classes is
its whole job.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

PACKAGE = "teachers_teammate"
LAYER = "application"
PREFIX = "teachers_teammate.infrastructure"

#: Which application modules import ``infrastructure``, and how many names they import from it.
PINNED = {
    "addons.py": 6,
    "anonymization.py": 6,
    "api_keys.py": 1,
    "benchmark_service.py": 7,
    "commands.py": 3,
    "correction_options.py": 1,
    "data_flow.py": 2,
    "documents.py": 3,
    "export.py": 1,
    "formatting.py": 4,
    "ollama_server.py": 14,
    "ports.py": 1,
    "providers.py": 7,
    "reader.py": 5,
    "run.py": 15,
    "setup_plan.py": 5,
    "speed_estimates.py": 16,
    "stored_results.py": 2,
}
"""The ratchet: a module's imported names from ``infrastructure``.  Only ever lowered."""

#: The composition root names the concrete classes on purpose: it is the one place that wires them.
COMPOSITION_ROOT = "composition.py"


def _layer_dir() -> Path:
    """Return the application layer's folder, from the Bazel runfiles or the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / PACKAGE / LAYER
    return Path(__file__).resolve().parents[2] / PACKAGE / LAYER


def _resolved(node: ast.ImportFrom) -> str:
    """Return the module an ``ImportFrom`` names, as a dotted path from the package."""
    module = node.module or ""
    if node.level == 1:
        return module
    # Every module of this layer sits one folder below the package, so level 2 is the package.
    return f"{PACKAGE}.{module}" if module else PACKAGE


def _names(path: Path) -> int:
    """Return how many names *path* imports from an ``infrastructure`` module."""
    count = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = _resolved(node)
            if module == PACKAGE:  # from .. import infrastructure
                count += sum(alias.name == "infrastructure" for alias in node.names)
            elif module == PREFIX or module.startswith(f"{PREFIX}."):
                count += len(node.names)
        elif isinstance(node, ast.Import):
            count += sum(alias.name.startswith(PREFIX) for alias in node.names)
    return count


def _reach() -> dict[str, int]:
    """Return ``{module: count}`` of the names imported from ``infrastructure``, biggest first."""
    found: dict[str, int] = {}
    for path in sorted(_layer_dir().glob("*.py")):
        if path.name == COMPOSITION_ROOT:
            continue
        count = _names(path)
        if count:
            found[path.name] = count
    return dict(sorted(found.items(), key=lambda item: (-item[1], item[0])))


def test_the_application_layer_imports_infrastructure_only_where_pinned() -> None:
    """
    Given  the modules of ``teachers_teammate/application/``
    When   the names they import from ``infrastructure`` are counted
    Then   each count equals the pinned one: a removal lowers it, a new import is a decision
    """
    found = _reach()
    removed = sorted(set(PINNED) - set(found))
    added = sorted(set(found) - set(PINNED))
    moved = {
        module: (count, found[module])
        for module, count in PINNED.items()
        if module in found and found[module] != count
    }
    breakdown = "\n".join(f"  {count:3d}  {module}" for module, count in found.items())

    assert not (removed or added or moved), (
        f"application/ reaches into infrastructure off the table.\n"
        f"no longer importing (lower or drop the entry): {removed}\n"
        f"importing now, not pinned: {added}\n"
        f"pinned / counted: {moved}\n"
        f"{breakdown}"
    )
