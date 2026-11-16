"""The build's check that the model folder it bundles holds every pinned file."""

from __future__ import annotations

from pathlib import Path

from teachers_teammate.domain.local_models import PAGE_MODELS, READER_MODELS
from tools.build import fetch_models


def _write(folder: Path, model) -> None:
    """Write *model* into *folder* the way the fetch does: every file, then the marker."""
    target = folder / model.repo.split("/")[-1] / model.revision
    target.mkdir(parents=True)
    for file in model.files:
        (target / file.name).write_bytes(b"x" * file.size)
    (target / ".verified").write_text(model.revision, encoding="utf-8")


def test_incomplete_names_the_models_a_folder_does_not_hold(tmp_path: Path) -> None:
    """
    Given  a models folder with every bundled model, and one with a model folder left empty
    When   the build asks which models are incomplete
    Then   the first has none, and the second names the empty one
    """
    models = (*READER_MODELS, *PAGE_MODELS)
    for model in models:
        _write(tmp_path, model)
    assert fetch_models.incomplete(tmp_path) == []

    left = models[0]
    for item in (tmp_path / left.repo.split("/")[-1] / left.revision).iterdir():
        item.unlink()

    assert fetch_models.incomplete(tmp_path) == [left.key]


def test_incomplete_names_everything_for_an_empty_folder(tmp_path: Path) -> None:
    """
    Given  an existing but empty models folder (a cancelled fetch)
    When   the build asks which models are incomplete
    Then   every bundled model is named
    """
    assert fetch_models.incomplete(tmp_path) == [
        model.key for model in (*READER_MODELS, *PAGE_MODELS)
    ]
