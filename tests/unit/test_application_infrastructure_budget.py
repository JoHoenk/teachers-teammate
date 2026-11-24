"""The application layer reaches into ``infrastructure`` only where this table says it does.

A service in ``application/`` is given what it uses: a collaborator comes in through its
constructor, and where a service branches on one, the branch is a Protocol in
``application/ports.py``.  What still names an ``infrastructure`` module is an object the service
was handed and has to name to call it (a store, the pipeline), or a subprocess it starts (an
installer, a download).

The table below is the ratchet: a module that no longer imports ``infrastructure`` fails, and so
does a count that does not match.  So a removal lowers a number here, and a new import is a
decision taken in this file.  ``composition.py`` is not in the table — naming the concrete classes
is its whole job.

The second test holds the other half: a rule that is pure (the document record, its reset fields,
the freshness flags, the anonymizer settings, the automatic identity, the keys of a stored result)
is imported from ``domain`` or the core, never from ``infrastructure``.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

PACKAGE = "teachers_teammate"
LAYER = "application"
PREFIX = "teachers_teammate.infrastructure"

#: Which application modules import ``infrastructure``, and how many import statements.
PINNED = {
    "addons.py": 6,
    "anonymization.py": 5,
    "api_keys.py": 1,
    "benchmark_service.py": 4,
    "commands.py": 2,
    "correction_options.py": 1,
    "data_flow.py": 2,
    "documents.py": 2,
    "export.py": 1,
    "formatting.py": 4,
    "ollama_server.py": 8,
    "ports.py": 1,
    "providers.py": 1,
    "reader.py": 5,
    "run.py": 11,
    "setup_plan.py": 4,
    "speed_estimates.py": 5,
    "stored_results.py": 2,
}
"""The ratchet: a module's imports into ``infrastructure``.  Only ever lowered."""

#: The composition root names the concrete classes on purpose: it is the one place that wires them.
COMPOSITION_ROOT = "composition.py"

#: The module that held the keys of a stored result.  It holds the reconciliation service only now,
#: so no service may name it: a key is computed by the core.
KEYS_MODULE = f"{PREFIX}.workflow.cache_service"

#: Names that are pure rules below this layer now.  Imported from ``infrastructure`` they would pin
#: a service to where the rule used to live.
PURE_RULES = frozenset(
    {
        "DocumentState",
        "ExpectedHashes",
        "reset_fields",
        "stage_hashes",
        "compute_config_hash",
        "anonymizer_config_from",
        "resolve_anonymizer_config",
        "auto_identity",
        "get_ocr_prompt",
        "input_settings_fingerprint",
        "preprocess_fingerprint",
        "ocr_key",
        "correction_key",
        "review_key",
        "privacy_key",
        "expected_hashes",
        "sent_record",
    }
)


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


def _imports(path: Path) -> list[tuple[str, tuple[str, ...]]]:
    """Return ``(module, names)`` for every import of *path* that names an infrastructure module."""
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = _resolved(node)
            names = tuple(alias.name for alias in node.names)
            if module == PACKAGE and "infrastructure" in names:  # from .. import infrastructure
                found.append((PREFIX, ("infrastructure",)))
            elif module == PREFIX or module.startswith(f"{PREFIX}."):
                found.append((module, names))
        elif isinstance(node, ast.Import):
            found.extend((alias.name, ()) for alias in node.names if alias.name.startswith(PREFIX))
    return found


def _reach() -> dict[str, int]:
    """Return ``{module: count}`` of the import statements into ``infrastructure``, biggest first."""
    found: dict[str, int] = {}
    for path in sorted(_layer_dir().glob("*.py")):
        if path.name == COMPOSITION_ROOT:
            continue
        count = len(_imports(path))
        if count:
            found[path.name] = count
    return dict(sorted(found.items(), key=lambda item: (-item[1], item[0])))


def test_the_application_layer_imports_infrastructure_only_where_pinned() -> None:
    """
    Given  the modules of ``teachers_teammate/application/``
    When   their import statements into ``infrastructure`` are counted
    Then   each count equals the pinned one — a removal lowers it, a new import is a decision
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


def test_no_application_module_imports_a_rule_that_moved_below_it() -> None:
    """
    Given  the imports of every module of ``teachers_teammate/application/``
    When   their names and modules are compared with the pure rules below the layer
    Then   none of them comes from ``infrastructure``: a pure rule is imported from domain or core
    """
    offences: list[str] = []
    for path in sorted(_layer_dir().glob("*.py")):
        for module, names in _imports(path):
            if module == KEYS_MODULE:
                offences.append(f"{path.name}: imports {KEYS_MODULE}")
            offences.extend(
                f"{path.name}: imports {name} from {module}" for name in names if name in PURE_RULES
            )

    assert not offences, (
        "A pure rule is back behind infrastructure.  Import it from teachers_teammate.domain or\n"
        "the core module that owns it:\n" + "\n".join(f"  {line}" for line in offences)
    )
