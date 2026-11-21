"""The package's import direction matches the Bazel libraries of ``//teachers_teammate``."""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: Layer -> the layers it may import (``teachers_teammate/BUILD`` lists the same as ``deps``).
#: An adapter reaches the concrete classes only through ``application``; the modules that wire
#: them (``_COMPOSITION_MODULES``) are the exception, because wiring is their whole job.
ALLOWED: dict[str, frozenset[str]] = {
    "pkg": frozenset(),
    "domain": frozenset({"pkg"}),
    "core": frozenset({"pkg", "domain"}),
    "texts": frozenset({"pkg", "domain"}),
    "infrastructure": frozenset({"pkg", "domain", "core"}),
    "application": frozenset({"pkg", "domain", "core", "infrastructure"}),
    "cli": frozenset({"pkg", "domain", "core", "application", "texts"}),
    "gui": frozenset({"pkg", "domain", "core", "application", "texts"}),
}

#: The modules that build the concrete classes and hand them to the adapters.  They may import
#: ``infrastructure``; every other adapter module may not.
_COMPOSITION_MODULES: frozenset[str] = frozenset(
    {
        "__main__.py",
        "cli_composition.py",
        "frozen_modes.py",
        "gui/__main__.py",
        "gui/benchmark/__main__.py",
        "gui/benchmark/composition.py",
        "gui/composition.py",
    }
)

_PACKAGE = "teachers_teammate"
_CORE_MODULES = {"config", "interfaces", "settings_schema", "_model_defaults"}
_CLI_MODULES = {"cli", "cli_composition", "__main__"}


def _package_dir() -> Path:
    """Return the package folder, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / _PACKAGE
    return Path(__file__).resolve().parents[2] / _PACKAGE


def _layer_of(parts: list[str], package: Path) -> str:
    """Return the layer of the module whose dotted path below the package is *parts*."""
    if not parts or parts[0] in {"exceptions", "__version__"}:
        return "pkg"
    first = parts[0]
    if (package / first).is_dir():
        return first
    if first in _CORE_MODULES:
        return "core"
    if first in _CLI_MODULES:
        return "cli"
    if first == "frozen_modes":
        return "gui"
    raise AssertionError(f"{first} is in no layer: add it to teachers_teammate/BUILD and here")


def _absolute_module(node: ast.ImportFrom, relative_to: list[str]) -> str:
    """Return the dotted module an ``import from`` names, relative levels resolved."""
    if not node.level:
        return node.module or ""
    base = [_PACKAGE, *relative_to]
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, *([node.module] if node.module else [])])


def _imported_modules(path: Path, relative_to: list[str]) -> list[list[str]]:
    """Return the dotted paths (below the package) of every package import in *path*."""
    found: list[list[str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, relative_to)
            names = [module, *(f"{module}.{alias.name}" for alias in node.names)]
        else:
            continue
        for name in names:
            parts = name.split(".")
            if parts[0] == _PACKAGE:
                found.append(parts[1:])
    return found


def test_every_import_points_down_the_layers() -> None:
    """
    Given  the layers of the package and the libraries Bazel builds for them
    When   every import of every module (also the ones inside functions) is read
    Then   each one stays in its own layer or in one the layer's library depends on, so a test
           that lists only its layer finds everything it imports
    """
    package = _package_dir()
    violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        own = (
            []
            if relative.name == "__init__.py" and len(relative.parts) == 1
            else list(relative.with_suffix("").parts)
        )
        layer = _layer_of(own, package)
        allowed = ALLOWED[layer]
        if relative.as_posix() in _COMPOSITION_MODULES:
            allowed = allowed | {"infrastructure"}
        for target in _imported_modules(path, list(relative.parts[:-1])):
            other = _layer_of(target, package)
            if other != layer and other not in allowed:
                violations.append(f"{relative} ({layer}) imports {'.'.join(target)} ({other})")
    assert not violations, "\n".join(violations)


def _re_exports(path: Path, relative_to: list[str]) -> list[tuple[str, str]]:
    """Return ``(name, module)`` for every ``from module import name as name`` in *path*."""
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, relative_to)
            found += [(alias.name, module) for alias in node.names if alias.asname == alias.name]
    return found


def test_no_module_re_exports_a_name_of_another_layer() -> None:
    """
    Given  the layers of the package and the modules in them
    When   every ``from … import Name as Name`` — this code base's way of re-exporting — is read
    Then   none of them takes the name from another layer, so a name has one home and a caller
           is pointed there instead of to a second copy that can drift from it
    """
    package = _package_dir()
    violations: list[str] = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        own = (
            []
            if relative.name == "__init__.py" and len(relative.parts) == 1
            else list(relative.with_suffix("").parts)
        )
        layer = _layer_of(own, package)
        for name, module in _re_exports(path, list(relative.parts[:-1])):
            parts = module.split(".")
            if parts[0] != _PACKAGE:
                continue
            other = _layer_of(parts[1:], package)
            if other != layer:
                violations.append(
                    f"{relative} ({layer}) re-exports {name} from {'.'.join(parts)} ({other})"
                )
    assert not violations, "\n".join(violations)
