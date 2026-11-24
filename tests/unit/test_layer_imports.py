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
    "infrastructure": frozenset({"pkg", "domain", "core", "texts"}),
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
        "frozen_entry.py",
        "frozen_modes.py",
        "gui/__main__.py",
        "gui/benchmark/__main__.py",
        "gui/benchmark/composition.py",
        "gui/composition.py",
    }
)

_PACKAGE = "teachers_teammate"
_PKG_MODULES = {"exceptions", "__version__", "messages"}
"""The package root's own modules (``_PKG_SRCS`` in ``teachers_teammate/BUILD``)."""
_CORE_MODULES = {
    "atomic_file",
    "cache_keys",
    "config",
    "interfaces",
    "run_rules",
    "settings_schema",
    "_model_defaults",
}
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
    if not parts or parts[0] in _PKG_MODULES:
        return "pkg"
    first = parts[0]
    if (package / first).is_dir():
        return first
    if first in _CORE_MODULES:
        return "core"
    if first in _CLI_MODULES:
        return "cli"
    if first in {"frozen_entry", "frozen_modes"}:
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


def _exported(path: Path) -> list[str]:
    """Return the names *path* lists in a module-level ``__all__``."""
    names: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets
        ):
            names += [
                element.value
                for element in getattr(node.value, "elts", [])
                if isinstance(element, ast.Constant)
            ]
    return names


def _imported(path: Path, relative_to: list[str]) -> dict[str, str]:
    """Return ``name -> module`` for every name *path* imports from the package."""
    found: dict[str, str] = {}
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = _absolute_module(node, relative_to)
            if module.split(".")[0] == _PACKAGE:
                found.update({(alias.asname or alias.name): module for alias in node.names})
    return found


def test_no_module_re_exports_a_name_it_does_not_define() -> None:
    """
    Given  the layers of the package and the modules in them
    When   every way of passing a name through is read: ``from … import Name as Name``, and a
           name of ``__all__`` that the module imported from another layer
    Then   a module publishes only what it defines, so a caller is pointed at the one home
           instead of at a second copy that can drift from it
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
        relative_to = list(relative.parts[:-1])
        for name, module in _re_exports(path, relative_to):
            if module.split(".")[0] == _PACKAGE:
                violations.append(
                    f"{relative} re-exports {name} from {module}: import it from there instead"
                )
        imported = _imported(path, relative_to)
        # A subpackage's ``__init__`` lists the names of its own modules, which is the API of that
        # package; a name of another layer in ``__all__`` is a second home like any other.
        for name in _exported(path):
            module = imported.get(name)
            if module is None:
                continue
            other = _layer_of(module.split(".")[1:], package)
            if other != layer:
                violations.append(
                    f"{relative} ({layer}) lists {name} in __all__, imported from {module} "
                    f"({other}): import it from there instead"
                )
    assert not violations, "\n".join(violations)


def test_the_rule_recognises_a_name_passed_through(tmp_path: Path) -> None:
    """
    Given  a module that passes a name on with ``import Name as Name``, imports another, and
           lists all of them in ``__all__``
    When   the three ways it can publish a name are read
    Then   the two names it imported are seen, with the module each comes from, and the name it
           defines itself is only in ``__all__``
    """
    module = tmp_path / "passing_on.py"
    module.write_text(
        "from teachers_teammate.domain.teacher_work import WorkTooNewError as WorkTooNewError\n"
        "from teachers_teammate.exceptions import OCRError\n"
        '__all__ = ["WorkTooNewError", "OCRError", "own_name"]\n'
        "own_name = 1\n",
        encoding="utf-8",
    )

    assert _re_exports(module, []) == [("WorkTooNewError", "teachers_teammate.domain.teacher_work")]
    assert _exported(module) == ["WorkTooNewError", "OCRError", "own_name"]
    assert _imported(module, []) == {
        "WorkTooNewError": "teachers_teammate.domain.teacher_work",
        "OCRError": "teachers_teammate.exceptions",
    }
