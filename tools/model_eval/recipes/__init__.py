"""The selection rule of each test set: which texts, how many, in which bands.

A recipe belongs to a test-set id, not to a corpus: one corpus serves several sets (``gec-2026-12``,
later a MultiGEC-identical one), and one set draws on several corpora.  So the rule
lives here, one module per id, and the corpora stay in :mod:`tools.model_eval.corpora`.

A recipe module sets ``RECIPE`` to a :class:`Recipe`.  The modules are found like the corpus
adapters: every module whose name does not start with ``_``, imported once, sorted by name
(:func:`~tools.model_eval._plugins.discover`).

A recipe never changes once its manifest is committed: another selection is another test-set id.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .._plugins import discover

if TYPE_CHECKING:
    from ..testset import Corpora


@dataclass(frozen=True)
class Recipe:
    """The selection of one test set."""

    testset: str
    """The test-set id: ``"gec-2026-12"``.  The module is called the same with ``_`` for ``-``."""

    seed: int
    """The day the rule was fixed; the seed decides only where a pool is larger than its slot."""

    corpora: tuple[str, ...]
    """The corpora this selection reads; the builder refuses to start without them."""

    select: Callable[[Corpora], list[dict]]
    """Choose the items of the set from the corpora: one manifest item per text."""


_RECIPES: dict[str, Recipe] | None = None


def recipes() -> dict[str, Recipe]:
    """Return ``{test-set id: recipe}``, found once and then kept.

    The dict is the same object on every call, so a test can add a recipe to it.
    """
    global _RECIPES  # noqa: PLW0603  # pylint: disable=global-statement  # found once, so a test can add a recipe to the dict
    if _RECIPES is None:
        _RECIPES = _discover()
    return _RECIPES


def names() -> list[str]:
    """Return the ids of the test sets a recipe exists for, sorted."""
    return sorted(recipes())


def recipe(testset_id: str) -> Recipe:
    """Return the recipe of the test set *testset_id*.

    Raises:
        ValueError: if no recipe has that id; the message lists the known ones.
    """
    found = recipes().get(testset_id)
    if found is None:
        raise ValueError(f"no test set {testset_id!r}; known: {', '.join(names())}")
    return found


def _check(module: str, candidate: object) -> None:
    """Raise :exc:`RuntimeError` unless *candidate* is a complete :class:`Recipe`."""
    if not isinstance(candidate, Recipe):
        raise RuntimeError(f"{module}: RECIPE is not a Recipe")
    if not isinstance(candidate.testset, str):
        raise RuntimeError(f"{module}: the test-set id is not a string")
    if not isinstance(candidate.seed, int):
        raise RuntimeError(f"{module}: the seed is not an integer")
    if not callable(candidate.select):
        raise RuntimeError(f"{module}: select is not callable")


def _discover() -> dict[str, Recipe]:
    """Import every recipe module and check that it is one."""
    return discover(
        __name__,
        __path__,
        attribute="RECIPE",
        kind="test set",
        name_of=lambda candidate: candidate.testset,
        check=_check,
    )


__all__ = ["Recipe", "names", "recipe", "recipes"]
