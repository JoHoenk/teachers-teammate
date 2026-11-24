"""Tests for the list of installed spaCy models."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from teachers_teammate.infrastructure.spacy_models import installed_spacy_models


def _install_model(folder: Path, name: str) -> None:
    """Put a model package folder where pip would."""
    (folder / name).mkdir()


def _install_spacy(folder: Path) -> None:
    """Put a ``spacy`` package folder where pip would."""
    (folder / "spacy").mkdir()


@pytest.fixture
def path_entry(tmp_path: Path):
    """A folder that is put on ``sys.path`` by the test and taken off again."""
    yield tmp_path
    while str(tmp_path) in sys.path:
        sys.path.remove(str(tmp_path))


def test_a_model_installed_while_the_app_runs_is_listed(path_entry: Path) -> None:
    """
    Given  a first call that found no model
    When   a model is installed into a folder on the path and the list is asked again
    Then   the model is in it, without a restart
    """
    sys.path.insert(0, str(path_entry))
    _install_spacy(path_entry)
    assert "en_core_web_sm" not in installed_spacy_models()

    _install_model(path_entry, "en_core_web_sm")
    importlib.invalidate_caches()  # what the add-on code does after an install

    assert "en_core_web_sm" in installed_spacy_models()


def test_models_are_listed_sorted(path_entry: Path) -> None:
    """
    Given  two models installed
    When   the list is asked for
    Then   both are in it, sorted
    """
    sys.path.insert(0, str(path_entry))
    _install_spacy(path_entry)
    _install_model(path_entry, "xx_ent_wiki_sm")
    _install_model(path_entry, "de_core_news_sm")
    importlib.invalidate_caches()

    names = installed_spacy_models()

    assert names == sorted(names)
    assert {"de_core_news_sm", "xx_ent_wiki_sm"} <= set(names)


def test_without_spacy_the_list_is_empty(path_entry: Path) -> None:
    """
    Given  a model package but no spaCy
    When   the list is asked for
    Then   it is empty, so a missing add-on reads like a missing model
    """
    sys.path.insert(0, str(path_entry))
    _install_model(path_entry, "en_core_web_sm")
    importlib.invalidate_caches()

    assert installed_spacy_models() == []
