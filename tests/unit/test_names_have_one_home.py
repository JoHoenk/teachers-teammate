"""A stage word, the Ollama address and the Ollama provider id are spelled in one module each.

``domain/stages.py`` writes the stage words (``Stage.OCR`` is the ``"ocr"`` a stored file
carries), ``domain/ollama_discovery.py`` writes the address the app looks at first and
``domain/data_location.py`` writes the provider id.  A literal elsewhere means a name with a
second home: a typo cannot be caught, and a changed name silently reaches only the paths that
read the constant.  A name is read from the module that defines it, never from one that merely
imported it.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

_PACKAGE = "teachers_teammate"

#: The words :class:`~teachers_teammate.domain.stages.Stage` spells.
_STAGE_WORDS = frozenset({"ocr", "correction", "evaluation"})
_STAGE_HOME = "domain/stages.py"

#: The stage words still spelled elsewhere, per module: they name something that is not a
#: :class:`Stage` — a ``ModelCategory``, a settings-key prefix, an English word, a stored key.
_STAGE_WORDS_LEFT: dict[str, int] = {
    "application/check_items.py": 1,  # ItemKind.CORRECTION, the word a hand-over file carries
    "application/data_flow.py": 1,  # the settings-key prefix of the step
    "application/model_recommendations.py": 1,
    "application/setup_plan.py": 1,
    "application/speed_estimates.py": 4,
    "cli.py": 2,  # the argument groups a teacher sees as "--correction", "--evaluation"
    "domain/model_catalog.py": 7,  # ModelCategory, the catalog's own use of it
    "domain/model_fit.py": 2,  # ModelCategory, the recommendations and detected uses
    "domain/performance_estimate.py": 4,
    "domain/proofreading/review.py": 1,  # "1 minor correction"
    "exceptions.py": 1,  # OCRError's stage: the pkg layer cannot import domain
    "gui/_ocr_config_selector.py": 1,
    "gui/settings/_page.py": 1,  # FOCUS_OCR, a deep link
    "gui/settings/addons/_ollama_models_tab.py": 7,
    "texts/models.py": 3,  # ModelCategory, and the word "ocr" in a model's abilities
    "texts/review.py": 1,  # "3 spelling and punctuation corrections"
}

#: The port the address in ``domain/ollama_discovery.py`` names.
_ADDRESS_MARKER = "11434"
_ADDRESS_HOME = "domain/ollama_discovery.py"

#: The key of the saved Ollama address in the settings dict.
_ADDRESS_KEY = "ollama_url"

#: The provider id ``domain/data_location.py`` spells as ``PROVIDER_OLLAMA``.
_PROVIDER_ID = "ollama"
_PROVIDER_HOME = "domain/data_location.py"

#: The provider id still spelled elsewhere, per module: it names something that is not the
#: provider — a package to import or the program on disk.
_PROVIDER_ID_LEFT: dict[str, int] = {
    "frozen_modes.py": 2,  # the ``ollama`` package: imported for the check, read for its version
    "infrastructure/ollama_install.py": 3,  # the program looked for, and started as ``serve``
}


def _package_dir() -> Path:
    """Return the package folder, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace / _PACKAGE
    return Path(__file__).resolve().parents[2] / _PACKAGE


def _strings(path: Path) -> list[str]:
    """Return the string constants in *path* that are not docstrings."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _modules() -> list[tuple[str, Path]]:
    """Return ``(module, path)`` for every module of the package."""
    package = _package_dir()
    return [(path.relative_to(package).as_posix(), path) for path in sorted(package.rglob("*.py"))]


def _defined_names(path: Path) -> set[str]:
    """Return the names *path* defines at module level: its functions, classes and constants."""
    names: set[str] = set()

    def collect(target: ast.expr) -> None:
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                collect(element)

    def walk(body: list[ast.stmt]) -> None:
        for statement in body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(statement.name)
            elif isinstance(statement, ast.Assign):
                for target in statement.targets:
                    collect(target)
            elif isinstance(statement, ast.AnnAssign):
                collect(statement.target)
            elif isinstance(statement, ast.TypeAlias):
                collect(statement.name)
            elif isinstance(statement, (ast.If, ast.Try, ast.With)):
                walk(statement.body)
                walk(getattr(statement, "orelse", []))
                walk(getattr(statement, "finalbody", []))
                for handler in getattr(statement, "handlers", []):
                    walk(handler.body)

    walk(ast.parse(path.read_text(encoding="utf-8")).body)
    return names


def _named_module(importer: Path, module: str) -> Path | None:
    """Return the module a ``from .<module>`` in *importer* names, or ``None`` for a package.

    A package is its own surface: its ``__init__.py`` gathers what its submodules define, which
    is not a second home for them.
    """
    file = (importer.parent / Path(*module.split("."))).with_suffix(".py")
    return file if file.exists() else None


def test_a_stage_word_is_spelled_in_domain_stages_only() -> None:
    """
    Given  every module of the package
    When   the string constants that spell a stage word are counted per module
    Then   each module spells exactly as many as pinned, so a freed slot is not reused
    """
    counted = {
        module: sum(1 for value in _strings(path) if value in _STAGE_WORDS)
        for module, path in _modules()
        if module != _STAGE_HOME
    }
    off = {
        module: (counted.get(module, 0), pinned)
        for module in counted.keys() | _STAGE_WORDS_LEFT.keys()
        if (pinned := _STAGE_WORDS_LEFT.get(module, 0)) != counted.get(module, 0)
    }
    assert off == {}, f"spell Stage.* instead, or fix the pin (`module: (now, pinned)`): {off}"


def test_the_ollama_address_is_spelled_in_domain_ollama_discovery_only() -> None:
    """
    Given  every module of the package
    When   the string constants that name the Ollama port are looked for
    Then   only domain/ollama_discovery.py has one, so a run cannot fall back to localhost
    """
    spelled = {
        module: value
        for module, path in _modules()
        if module != _ADDRESS_HOME
        for value in _strings(path)
        if _ADDRESS_MARKER in value
    }
    assert spelled == {}, f"use DEFAULT_SERVER_URL instead: {spelled}"


def test_the_ollama_provider_id_is_spelled_in_domain_data_location_only() -> None:
    """
    Given  every module of the package
    When   the string constants that spell the Ollama provider id are counted per module
    Then   each module spells exactly as many as pinned, so a freed slot is not reused
    """
    counted = {
        module: sum(1 for value in _strings(path) if value == _PROVIDER_ID)
        for module, path in _modules()
        if module != _PROVIDER_HOME
    }
    off = {
        module: (counted.get(module, 0), pinned)
        for module in counted.keys() | _PROVIDER_ID_LEFT.keys()
        if (pinned := _PROVIDER_ID_LEFT.get(module, 0)) != counted.get(module, 0)
    }
    assert off == {}, (
        f"use PROVIDER_OLLAMA instead, or fix the pin (`module: (now, pinned)`): {off}"
    )


def _reads_saved_address(node: ast.AST) -> bool:
    """Return whether *node* reads the saved Ollama address out of a mapping."""
    return any(
        isinstance(sub, ast.Subscript)
        and isinstance(sub.slice, ast.Constant)
        and sub.slice.value == _ADDRESS_KEY
        for sub in ast.walk(node)
    )


def _defaulted_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Return the names of the parameters of *node* that carry a default."""
    positional = [*node.args.posonlyargs, *node.args.args]
    keyword = [
        arg
        for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
        if default is not None
    ]
    carrying = positional[len(positional) - len(node.args.defaults) :]
    return {arg.arg for arg in (*carrying, *keyword)}


def test_a_saved_ollama_address_is_never_a_fallback_behind_an_or() -> None:
    """
    Given  every module of the package
    When   an ``or`` that reads the saved Ollama address is looked for
    Then   there is none, so a blank address is normalised instead of silently replaced
    """
    found = {
        f"{module}:{node.lineno}"
        for module, path in _modules()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.BoolOp)
        and isinstance(node.op, ast.Or)
        and any(_reads_saved_address(operand) for operand in node.values)
    }
    assert found == set(), f"normalize_ollama_url() instead of a default: {sorted(found)}"


def test_the_ollama_provider_asks_for_its_address() -> None:
    """
    Given  every provider module
    When   the parameters that carry a default are read
    Then   none names ``base_url``: the address comes from the caller that owns it
    """
    providers = _package_dir() / "infrastructure" / "providers"
    defaulted = {
        f"{path.name}:{node.lineno}"
        for path in sorted(providers.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "base_url" in _defaulted_parameters(node)
    }
    assert defaulted == set(), f"take base_url as a required argument: {sorted(defaulted)}"


def test_a_relative_import_names_a_module_that_defines_the_name() -> None:
    """
    Given  every module of the package that is not a package's own surface
    When   each ``from .x import name`` is resolved to the module it names
    Then   *name* is defined there, so an import never reaches through a re-export
    """
    undefined = {
        f"{module}:{node.lineno}: from .{node.module} import {alias.name}"
        for module, path in _modules()
        if path.name != "__init__.py"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module
        for alias in node.names
        if alias.name != "*"
        if (target := _named_module(path, node.module)) is not None
        if alias.name not in _defined_names(target)
    }
    assert undefined == set(), (
        f"import the name from the module that defines it: {sorted(undefined)}"
    )
