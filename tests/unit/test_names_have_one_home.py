"""A stage word and the Ollama address are spelled in one module each.

``domain/stages.py`` writes the stage words (``Stage.OCR`` is the ``"ocr"`` a stored file
carries) and ``domain/ollama_discovery.py`` writes the address the app looks at first.  A
literal elsewhere means a word with a second home: a typo cannot be caught, and a changed
address silently reaches only the paths that read the constant.
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
    "domain/model_catalog.py": 9,  # ModelCategory
    "domain/performance_estimate.py": 4,
    "domain/proofreading/review.py": 1,  # "1 minor correction"
    "exceptions.py": 1,  # OCRError's stage: the pkg layer cannot import domain
    "gui/_ocr_config_selector.py": 1,
    "gui/settings/_page.py": 1,  # FOCUS_OCR, a deep link
    "gui/settings/addons/_ollama_models_tab.py": 7,
    "infrastructure/benchmark/run_store.py": 2,  # the "ocr" key of a stored run
    "texts/models.py": 3,  # ModelCategory, and the word "ocr" in a model's abilities
    "texts/review.py": 1,  # "3 spelling and punctuation corrections"
}

#: The port the address in ``domain/ollama_discovery.py`` names.
_ADDRESS_MARKER = "11434"
_ADDRESS_HOME = "domain/ollama_discovery.py"


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
