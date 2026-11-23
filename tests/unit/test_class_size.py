"""The ratchets on the size of a class and of a function.

A class that grew into a dozen jobs, or a function that grew into three, is split; these tests
keep it from growing back.  A unit over its limit must be listed below at the number it has
today: shrinking it means lowering the entry by hand, so the ratchet only turns one way.

Six numbers are counted, each for every layer: the lines of a class, the attributes a class
assigns to itself, its public methods, and the lines, the branches and the parameters of a
function.  The attributes tell a window that kept every section from a window that hands each
section to an object of its own; the branches tell a function that decides from one that calls.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
import os
from pathlib import Path

import pytest

#: The package the ratchets cover.
SCANNED = "teachers_teammate"

#: How many lines one class may have.
CLASS_LIMIT = 800

#: How many ``self.x = …`` names one class may assign.
ATTRIBUTE_LIMIT = 35

#: How many public methods (a name without a leading ``_``) one class may have.
METHOD_LIMIT = 20

#: How many lines one function may have.
FUNCTION_LIMIT = 80

#: How many branches one function may have.
COMPLEXITY_LIMIT = 20

#: How many parameters one function may have (keyword-only ones and ``*args``/``**kwargs`` too).
PARAMETER_LIMIT = 8

#: The classes still over the line limit, at the size they have today.
OVERSIZED_CLASSES: dict[str, int] = {
    "teachers_teammate/gui/_main_window.py::MainWindow": 1031,
}

#: The classes still over the attribute limit, at the number they assign today.
OVERSIZED_ATTRIBUTES: dict[str, int] = {}

#: The classes still over the public-method limit, at the number they have today.
OVERSIZED_METHODS: dict[str, int] = {
    "teachers_teammate/application/providers.py::ProviderService": 25,
    "teachers_teammate/gui/_check_controller.py::CheckController": 27,
    "teachers_teammate/gui/_config_panel.py::ConfigPanel": 33,
    "teachers_teammate/gui/_results_table.py::ResultsTable": 27,
}

#: The functions still over the line limit, at the size they have today.
OVERSIZED_FUNCTIONS: dict[str, int] = {
    "teachers_teammate/application/composition.py::build_app_services": 122,
    "teachers_teammate/application/setup_plan.py::_downloads_plan": 81,
    "teachers_teammate/cli.py::_add_correction_args": 91,
    "teachers_teammate/cli.py::_add_ocr_args": 85,
    "teachers_teammate/cli.py::run_cli": 87,
    "teachers_teammate/gui/_config_panel.py::ConfigPanel.__init__": 81,
    "teachers_teammate/gui/_fix_targets.py::fix_for": 82,
    "teachers_teammate/gui/_main_window.py::main_gui": 82,
    "teachers_teammate/gui/settings/_ai_service_section.py::AiServiceSection._build": 88,
    (
        "teachers_teammate/gui/settings/addons/_ollama_models_tab.py"
        "::OllamaModelsTab._update_model_suitability"
    ): 82,
    (
        "teachers_teammate/infrastructure/ollama_utils.py"
        "::_consume_iterator_with_first_token_timeout"
    ): 82,
    "teachers_teammate/infrastructure/ollama_utils.py::OllamaClient.chat_with_stats": 137,
    "teachers_teammate/infrastructure/pipeline.py::OCRPipeline._setup_services": 83,
    (
        "teachers_teammate/infrastructure/workflow/file_processor.py"
        "::FileProcessor._run_preprocess_and_ocr"
    ): 81,
}

#: The functions still over the branch limit, at the number of branches they have today.
COMPLEX_FUNCTIONS: dict[str, int] = {
    "teachers_teammate/application/setup_plan.py::_downloads_plan": 21,
    "teachers_teammate/cli.py::run_cli": 21,
    "teachers_teammate/gui/_results_table.py::ResultsTable._draw_stage_cells": 22,
    (
        "teachers_teammate/gui/settings/addons/_ollama_models_tab.py"
        "::OllamaModelsTab._update_model_suitability"
    ): 22,
    "teachers_teammate/infrastructure/ollama_discovery.py::scan_network": 23,
    "teachers_teammate/infrastructure/ollama_utils.py::OllamaClient.chat_with_stats": 21,
    "teachers_teammate/infrastructure/providers/_mistral_common.py::curate_mistral_models": 25,
    "teachers_teammate/infrastructure/state_repository.py::StateRepository.load_valid": 29,
    (
        "teachers_teammate/infrastructure/workflow/file_processor.py"
        "::FileProcessor._run_preprocess_and_ocr"
    ): 26,
}

#: The functions still over the parameter limit, at the number of parameters they have today.
WIDE_FUNCTIONS: dict[str, int] = {
    "teachers_teammate/application/model_recommendations.py"
    "::ModelRecommendationService.rate_ollama_models": 9,
    "teachers_teammate/application/run.py::RunService.__init__": 16,
    "teachers_teammate/application/run.py::RunService.run_selected": 9,
    "teachers_teammate/application/speed_estimates.py::SpeedChoices._of": 9,
    "teachers_teammate/gui/_check_controller.py::CheckController.__init__": 9,
    "teachers_teammate/gui/_ocr_config_selector.py::OcrConfigSelector.__init__": 9,
    "teachers_teammate/gui/_settings_launcher.py::SettingsLauncher.__init__": 9,
    "teachers_teammate/gui/settings/_ai_service_section.py::AiServiceSection.__init__": 13,
    "teachers_teammate/infrastructure/_llm_invoke.py::invoke_ollama_chat": 13,
    "teachers_teammate/infrastructure/_llm_invoke.py::invoke_ollama_structured": 10,
    "teachers_teammate/infrastructure/ollama_utils.py::OllamaClient.chat": 11,
    "teachers_teammate/infrastructure/ollama_utils.py::OllamaClient.chat_with_stats": 10,
    "teachers_teammate/infrastructure/proofreading/corrector.py::ollama_exchange_corrector": 12,
    "teachers_teammate/infrastructure/proofreading/exchanges/t.py::TExchange._ask_explain": 9,
    "teachers_teammate/infrastructure/state_repository.py"
    "::StateRepository.record_preview_artifact": 9,
    "teachers_teammate/infrastructure/state_repository.py"
    "::StateRepository.record_correction_result": 11,
    "teachers_teammate/infrastructure/workflow/cache_service.py"
    "::CacheReconciliationService.record_correction": 9,
    "teachers_teammate/infrastructure/workflow/file_processor.py::FileProcessor._write_outputs": 9,
}

#: The nodes whose body is scanned like a module's: a class or a function defined under one of
#: them is a definition at that place, not one inside a function.
_BLOCKS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith, ast.Try, ast.Match)

_Branch = (
    ast.If | ast.For | ast.AsyncFor | ast.While | ast.ExceptHandler | ast.IfExp | ast.match_case
)


@dataclass(frozen=True)
class _Ratchet:
    """One ratchet: what it counts, its limit, and the units still over the limit."""

    what: str
    limit: int
    recorded: Mapping[str, int]
    measure: Callable[[], dict[str, int]]
    hint: str


def _root() -> Path:
    """Return the repository root, from the Bazel runfiles or from the source checkout."""
    runfiles = os.environ.get("TEST_SRCDIR", "")
    workspace = os.environ.get("TEST_WORKSPACE", "")
    if runfiles and workspace:
        return Path(runfiles) / workspace
    return Path(__file__).resolve().parents[2]


def _key(path: Path, name: str) -> str:
    """Return the ``path::name`` key the lists below are keyed by."""
    return f"{path.relative_to(_root()).as_posix()}::{name}"


def _definitions() -> Iterator[tuple[Path, str, ast.AST]]:
    """Return every class and every function of the package with its ``path::name`` key.

    Nested definitions carry their outer names, so a method reads ``Class.method``; a definition
    under an ``if`` or a ``try`` is at the place that block sits in.
    """
    for path in sorted((_root() / SCANNED).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        yield from _walk(path, tree.body, "")


def _walk(path: Path, body: list[ast.stmt], prefix: str) -> Iterator[tuple[Path, str, ast.AST]]:
    """Return the definitions in *body*, named below *prefix*."""
    for node in body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            yield path, _key(path, name), node
            yield from _walk(path, node.body, f"{name}.")
        elif isinstance(node, _BLOCKS):
            yield from _walk(path, _blocks_of(node), prefix)


def _blocks_of(node: ast.AST) -> list[ast.stmt]:
    """Return the bodies one ``if``, loop, ``with``, ``try`` or ``match`` holds."""
    bodies: list[ast.stmt] = []
    for field in ("body", "orelse", "finalbody"):
        bodies.extend(getattr(node, field, []) or [])
    for handler in getattr(node, "handlers", []) or []:
        bodies.extend(handler.body)
    for case in getattr(node, "cases", []) or []:
        bodies.extend(case.body)
    return bodies


def _units() -> tuple[dict[str, ast.ClassDef], dict[str, ast.AST]]:
    """Return the classes and the functions of the package, by key."""
    classes: dict[str, ast.ClassDef] = {}
    functions: dict[str, ast.AST] = {}
    for _, key, node in _definitions():
        if isinstance(node, ast.ClassDef):
            classes[key] = node
        else:
            functions[key] = node
    return classes, functions


def _lines(node: ast.AST) -> int:
    """Return how many lines *node* covers."""
    return (node.end_lineno or node.lineno) - node.lineno + 1


def _class_lines() -> dict[str, int]:
    """Return the lines of every class of the package."""
    classes, _ = _units()
    return {key: _lines(node) for key, node in classes.items()}


def _class_attributes() -> dict[str, int]:
    """Return how many attributes every class of the package assigns."""
    classes, _ = _units()
    return {key: len(_assigned_attributes(node)) for key, node in classes.items()}


def _class_methods() -> dict[str, int]:
    """Return how many public methods every class of the package has."""
    classes, _ = _units()
    return {key: len(_public_methods(node)) for key, node in classes.items()}


def _function_lengths() -> dict[str, int]:
    """Return the lines of every function of the package."""
    _, functions = _units()
    return {key: _lines(node) for key, node in functions.items()}


def _function_complexity() -> dict[str, int]:
    """Return the branches of every function of the package."""
    _, functions = _units()
    return {key: _complexity(node) for key, node in functions.items()}


def _function_parameters() -> dict[str, int]:
    """Return the parameters of every function of the package."""
    _, functions = _units()
    return {key: _parameters(node) for key, node in functions.items()}


def _public_methods(node: ast.ClassDef) -> set[str]:
    """Return the methods of *node* a caller outside the class reads, i.e. without ``_``."""
    return {
        inner.name
        for inner in node.body
        if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not inner.name.startswith("_")
    }


def _assigned_attributes(node: ast.ClassDef) -> set[str]:
    """Return the ``self.x`` names the class assigns, also from a helper nested in a method."""
    names: set[str] = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Assign):
            targets = list(inner.targets)
        elif isinstance(inner, (ast.AnnAssign, ast.AugAssign)):
            targets = [inner.target]
        else:
            continue
        for target in targets:
            for leaf in _leaves(target):
                if (
                    isinstance(leaf, ast.Attribute)
                    and isinstance(leaf.value, ast.Name)
                    and leaf.value.id == "self"
                ):
                    names.add(leaf.attr)
    return names


def _leaves(target: ast.expr) -> list[ast.expr]:
    """Return the names assigned by *target*, also those inside ``a, (b, c) = …``."""
    if isinstance(target, (ast.Tuple, ast.List)):
        return [leaf for element in target.elts for leaf in _leaves(element)]
    if isinstance(target, ast.Starred):
        return _leaves(target.value)
    return [target]


def _complexity(node: ast.AST) -> int:
    """Return how many ways one call of *node* can go: one plus every branch and extra condition."""
    branches = 1
    for inner in ast.walk(node):
        if isinstance(inner, _Branch):
            branches += 1
        elif isinstance(inner, ast.BoolOp):
            branches += len(inner.values) - 1
        elif isinstance(inner, ast.comprehension):
            branches += 1 + len(inner.ifs)
    return branches


def _parameters(node: ast.AST) -> int:
    """Return how many parameters *node* takes, of every kind."""
    args = node.args
    counted = len(args.posonlyargs) + len(args.args) + len(args.kwonlyargs)
    return counted + bool(args.vararg) + bool(args.kwarg)


RATCHETS: tuple[_Ratchet, ...] = (
    _Ratchet(
        what="lines of a class",
        limit=CLASS_LIMIT,
        recorded=OVERSIZED_CLASSES,
        measure=_class_lines,
        hint="Split the class into the jobs it does.",
    ),
    _Ratchet(
        what="attributes of a class",
        limit=ATTRIBUTE_LIMIT,
        recorded=OVERSIZED_ATTRIBUTES,
        measure=_class_attributes,
        hint="Hand the state to the object that owns it instead of the class.",
    ),
    _Ratchet(
        what="public methods of a class",
        limit=METHOD_LIMIT,
        recorded=OVERSIZED_METHODS,
        measure=_class_methods,
        hint="Split the class along the answers it gives.",
    ),
    _Ratchet(
        what="lines of a function",
        limit=FUNCTION_LIMIT,
        recorded=OVERSIZED_FUNCTIONS,
        measure=_function_lengths,
        hint="Split the function into the steps it runs.",
    ),
    _Ratchet(
        what="branches of a function",
        limit=COMPLEXITY_LIMIT,
        recorded=COMPLEX_FUNCTIONS,
        measure=_function_complexity,
        hint="Split the decision into the steps it decides between.",
    ),
    _Ratchet(
        what="parameters of a function",
        limit=PARAMETER_LIMIT,
        recorded=WIDE_FUNCTIONS,
        measure=_function_parameters,
        hint="Group the parameters into the object they describe.",
    ),
)

_IDS: list[str] = [ratchet.what for ratchet in RATCHETS]


@pytest.mark.parametrize("ratchet", RATCHETS, ids=_IDS)
def test_no_unit_grows_past_its_limit(ratchet: _Ratchet) -> None:
    """
    Given  every class and function of the package, and one limit
    When   the ratchet counts the units
    Then   each one is under the limit, or on the list at the number it has today
    """
    measured = ratchet.measure()
    grown = [
        f"  {name}: {count}, the entry says {ratchet.recorded[name]}"
        for name, count in measured.items()
        if name in ratchet.recorded and count > ratchet.recorded[name]
    ]
    assert not grown, f"{ratchet.hint}\n" + "\n".join(grown)

    over = [
        f"  {name}: {count}"
        for name, count in measured.items()
        if name not in ratchet.recorded and count > ratchet.limit
    ]
    assert not over, (
        f"These are over the limit of {ratchet.limit} {ratchet.what} and are not on the list; "
        f"{ratchet.hint.lower()} Or add them at their number while a phase hands them over as "
        "they are:\n" + "\n".join(over)
    )


@pytest.mark.parametrize("ratchet", RATCHETS, ids=_IDS)
def test_every_entry_is_the_number_the_unit_has(ratchet: _Ratchet) -> None:
    """
    Given  the list of units still over one limit
    When   it is compared with what the sources say
    Then   no entry is higher than the unit, so a unit that shrank had its entry lowered
    """
    measured = ratchet.measure()
    stale = [
        f"  {name}: the entry says {recorded}, the unit has {measured.get(name, 0)}"
        for name, recorded in ratchet.recorded.items()
        if measured.get(name, 0) < recorded
    ]
    assert not stale, "Lower these entries to what the unit has now:\n" + "\n".join(stale)


def test_attributes_assigned_in_a_tuple_are_counted() -> None:
    """
    Given  a class that assigns self.a, self.b and a nested pair in one statement
    When   its assigned attributes are collected
    Then   every self name in the statement counts, and a plain local does not
    """
    node = ast.parse(
        "class C:\n"
        "    def f(self):\n"
        "        self.a, (self.b, local) = 1, (2, 3)\n"
        "        *self.rest, last = [1]\n"
    ).body[0]
    assert isinstance(node, ast.ClassDef)

    assert _assigned_attributes(node) == {"a", "b", "rest"}


def test_a_method_is_named_with_the_class_it_belongs_to() -> None:
    """
    Given  a module with a class, its method and a nested helper
    When   the definitions are collected
    Then   each one carries the names it sits below, so two ``__init__`` never share a key
    """
    tree = ast.parse(
        "class C:\n"
        "    def m(self):\n"
        "        def helper():\n"
        "            pass\n"
        "        return helper\n"
    )
    path = _root() / SCANNED / "sample.py"

    keys = [key for _, key, _ in _walk(path, tree.body, "")]

    assert keys == [
        f"{SCANNED}/sample.py::C",
        f"{SCANNED}/sample.py::C.m",
        f"{SCANNED}/sample.py::C.m.helper",
    ]
