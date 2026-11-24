"""The guard: a forgotten argument fails instead of degrading.

A default that stands in for a collaborator — an optional service, a store built on the quiet, a
temperature the caller need not give — turns a missing argument into quiet wrong behaviour: the
check does not run, the wrong folder is read, another model's temperature is used.  These tests
read the sources and fail on such a default.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

PACKAGE = "teachers_teammate"

#: The one module that wires the concrete classes: it is where a store may be built.
COMPOSITION = f"{PACKAGE}/application/composition.py"

#: The modules that build the app's services for an entry point.  They may start without one;
#: nothing below them may.
ENTRY_POINTS = (
    COMPOSITION,
    f"{PACKAGE}/gui/composition.py",
    f"{PACKAGE}/gui/benchmark/composition.py",
    f"{PACKAGE}/cli.py",
    f"{PACKAGE}/cli_composition.py",
)

#: The collaborators a module must be given, never build for itself.
STORES = ("ModelStore", "TeacherWorkStore", "SpacyModels", "MachineKeyFacts")

#: The modules, besides the composition root, that wire concrete classes for an entry point.
WIRING = (
    COMPOSITION,
    f"{PACKAGE}/gui/composition.py",
    f"{PACKAGE}/gui/__main__.py",
    f"{PACKAGE}/gui/benchmark/composition.py",
    f"{PACKAGE}/gui/benchmark/__main__.py",
    f"{PACKAGE}/cli_composition.py",
    f"{PACKAGE}/__main__.py",
    f"{PACKAGE}/frozen_modes.py",
)

#: The modules that build the model of an online provider; each is given a temperature.
MODEL_BUILDERS = (f"{PACKAGE}/infrastructure/llm_factory.py",)


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _sources() -> dict[str, str]:
    """Return every module of the package, by its path from the root."""
    root = _root()
    return {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in sorted((root / PACKAGE).rglob("*.py"))
    }


def _functions(module: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return every function and method of *module*."""
    return [
        node
        for node in ast.walk(module)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _temperature_has_a_default(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return whether *node* takes a ``temperature`` and gives it a value."""
    return any(
        arg.arg == "temperature" and default is not None
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
    )


def test_no_module_declares_an_optional_service() -> None:
    """
    Given  the modules below the entry points
    When   their sources are read for ``AppServices | None`` and ``set_service``
    Then   none holds one: a window is given the services it uses and never looks for them
    """
    findings = [
        f"{name}: AppServices | None"
        for name, text in _sources().items()
        if name not in ENTRY_POINTS and "AppServices | None" in text
    ]
    findings += [
        f"{name}: def set_service" for name, text in _sources().items() if "def set_service" in text
    ]

    assert not findings, (
        "A service is an argument, never something to look up later:\n" + "\n".join(findings)
    )


def _calls(text: str) -> set[str]:
    """Return the names *text* calls as a plain function or class: ``Name(...)``."""
    return {
        node.func.id
        for node in ast.walk(ast.parse(text))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_no_module_builds_its_own_store() -> None:
    """
    Given  every module of the package but the ones that wire an entry point
    When   its source is read for a call of ``ModelStore``, ``SpacyModels`` and the like
    Then   none builds one, in a default or anywhere else: it is given the one the app runs with
    """
    findings = [
        f"{name}: {store}()"
        for name, text in _sources().items()
        if name not in WIRING
        for store in sorted(_calls(text) & set(STORES))
    ]

    assert not findings, "Only the modules that wire an entry point build a store:\n" + "\n".join(
        findings
    )


def test_every_online_model_is_built_at_the_callers_temperature() -> None:
    """
    Given  ``build_llm`` and the ``create`` of every provider
    When   the ``temperature`` each takes is read
    Then   none has a default: a caller that skipped request_profile() fails instead of guessing
    """
    findings: list[str] = []
    for name, text in _sources().items():
        if name not in MODEL_BUILDERS and not name.startswith(
            f"{PACKAGE}/infrastructure/providers/"
        ):
            continue
        for node in _functions(ast.parse(text)):
            if node.name != "create" and not (node.name == "build_llm" and name in MODEL_BUILDERS):
                continue
            if not any(arg.arg == "temperature" for arg in node.args.kwonlyargs):
                findings.append(f"{name}: {node.name}:{node.lineno} takes no temperature")
            elif _temperature_has_a_default(node):
                findings.append(f"{name}: {node.name}:{node.lineno} has a default temperature")

    assert not findings, (
        "A model is built at the temperature its step's profile names:\n" + "\n".join(findings)
    )
