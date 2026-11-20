"""Tests for the test-set builder: a committed set is never edited, and the builder stays light.

A committed set is a fixed question, so a rebuild may only find the same set, a new key, or a
different question that has to get a new id.  The builder also runs where the corpora are, on a
machine that has no app: importing it may not load the app's infrastructure.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.evaluation.core.manifest import read_manifest
from tools.evaluation.input import recipes
from tools.evaluation.input import testset as testset_module
from tools.evaluation.input.recipes import Recipe
from tools.evaluation.store import layout
from tools.prepare_gec_testset import main

TESTSET = "gec-test-fake"
"""The id of the set these tests build; its recipe is added to the registry, not to the tree."""

ITEMS = [
    {
        "id": "fr-01",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "band": "short",
        "pointer": {"text": "fr-01"},
    },
    {
        "id": "fr-correct",
        "language": "fr",
        "kind": "correct",
        "corpus": "invented-fr",
        "pointer": {"text": "fr-correct"},
    },
]
"""The pointers of the fake set: one text with errors and one already correct, from the corpus in
the repository, so no learner corpus has to be present."""


def _recipe(items: list[dict] | None = None) -> Recipe:
    """Return the recipe of the fake set, drawing *items* (the two above by default)."""
    chosen = ITEMS if items is None else items
    return Recipe(
        testset=TESTSET,
        seed=20260101,
        corpora=("invented-fr",),
        select=lambda _corpora: [dict(item) for item in chosen],
    )


@pytest.fixture
def fake_set(monkeypatch):
    """Register the recipe of the fake set for one test."""
    monkeypatch.setitem(recipes.recipes(), TESTSET, _recipe())


def _build(monkeypatch, capsys, store: Path) -> tuple[int, str]:
    """Run the builder into *store* and return its exit code and everything it said."""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_gec_testset",
            "--testset",
            TESTSET,
            "--store",
            str(store),
            "--built",
            "2026-01-01",
        ],
    )
    code = main()
    said = capsys.readouterr()
    return code, said.out + said.err


def test_a_rebuild_with_another_question_is_refused(fake_set, monkeypatch, capsys, tmp_path):
    """
    Given  a committed set
    When   the builder runs again with a selection that makes another question
    Then   it refuses, says to give the rebuilt set a new id, and leaves the manifest alone
    """
    store = tmp_path / "store"
    assert _build(monkeypatch, capsys, store)[0] == 0
    path = layout.manifest_path(store, TESTSET)
    committed = path.read_bytes()

    monkeypatch.setitem(recipes.recipes(), TESTSET, _recipe(ITEMS[:1]))
    code, said = _build(monkeypatch, capsys, store)

    assert code == 2
    assert "never edited" in said
    assert path.read_bytes() == committed


def test_a_rebuild_with_another_key_raises_the_key_version(fake_set, monkeypatch, capsys, tmp_path):
    """
    Given  a committed set
    When   the builder runs again with the same question and another key
    Then   it writes the set with the next key version, and says so
    """
    store = tmp_path / "store"
    assert _build(monkeypatch, capsys, store)[0] == 0
    path = layout.manifest_path(store, TESTSET)
    assert read_manifest(path).key_version == 1

    built = testset_module.build_manifest
    monkeypatch.setattr(
        testset_module,
        "build_manifest",
        lambda *args, **kwargs: replace(built(*args, **kwargs), key_sha256="another key"),
    )
    code, said = _build(monkeypatch, capsys, store)

    assert code == 0
    assert "key_version is 2" in said
    assert read_manifest(path).key_version == 2


def test_a_rebuild_of_the_same_set_writes_nothing(fake_set, monkeypatch, capsys, tmp_path):
    """
    Given  a committed set
    When   the builder runs again with the same selection
    Then   it says the set is unchanged and does not touch the manifest
    """
    store = tmp_path / "store"
    assert _build(monkeypatch, capsys, store)[0] == 0
    path = layout.manifest_path(store, TESTSET)
    committed = path.read_bytes()
    stamp = path.stat().st_mtime_ns

    code, said = _build(monkeypatch, capsys, store)

    assert code == 0
    assert "unchanged" in said
    assert path.read_bytes() == committed
    assert path.stat().st_mtime_ns == stamp


def test_importing_the_builder_does_not_import_the_app():
    """
    Given  a machine that only builds a test set
    When   the builder is imported in a fresh interpreter
    Then   the app's infrastructure is not loaded, so the builder needs no app dependency
    """
    code = (
        "import sys, tools.prepare_gec_testset;"
        "print('teachers_teammate.infrastructure' in sys.modules)"
    )

    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "False"
