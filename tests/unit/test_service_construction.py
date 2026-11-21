"""A service is built in one place, so every adapter shares its caches.

``ProcessingApplicationService`` was built in about twenty places — eight in the add-on installer
dialog, three in the anonymizer widget, the worker, the frozen-mode check, both entry points.
Each copy had its own anonymizer cache, its own model listing cache and its own list of installed
models, so a download in one place was invisible in another, and the main window had to clear the
long-lived cache by hand after an install.

``application/composition.py`` builds the process's services once.  This test keeps a second
construction from growing back.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
"""The repository root, or the runfiles tree that holds the same files under Bazel."""

PACKAGE = "teachers_teammate"
"""The package the rule applies to."""

COMPOSITION = "teachers_teammate/application/composition.py"
"""The one module that may construct a service."""

CONSTRUCTED = {
    "AddonService",
    "AnonymizationService",
    "AppServices",
    "DataFlowService",
    "ModelRecommendationService",
    "OllamaServerService",
    "ProcessingApplicationService",
    "ProviderService",
    "ReaderService",
    "SetupService",
    "SpeedEstimateService",
}
"""The names that build a service, and so the names only the composition module may call."""


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


def _constructions(path: Path) -> list[str]:
    """Return the service names *path* constructs, as ``ast`` sees them."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name in CONSTRUCTED:
            found.append(name)
    return found


def test_a_service_is_built_only_in_the_composition_module() -> None:
    """
    Given  every module of the package
    When   the calls that build a service are collected
    Then   only ``application/composition.py`` builds one, so every adapter shares the same
           caches
    """
    root = _root()
    offenders = [
        f"{path.relative_to(root).as_posix()}: {name}"
        for path in _modules()
        if path.relative_to(root).as_posix() != COMPOSITION
        for name in _constructions(path)
    ]

    assert not offenders, (
        "Take the service from application/composition.py instead of building one:\n"
        + "\n".join(f"  {name}" for name in offenders)
    )


def test_the_composition_module_builds_the_service() -> None:
    """
    Given  the composition module
    When   its calls are collected
    Then   it builds the service, so the rule above is not satisfied by an empty module
    """
    root = _root()

    assert "ProcessingApplicationService" in _constructions(root / COMPOSITION)
