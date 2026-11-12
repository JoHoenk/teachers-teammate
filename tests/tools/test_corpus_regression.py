"""The committed sets still resolve from the local corpora.

Skipped unless ``GEC_CORPORA`` names a ``{corpus: path}`` file, which is never the case in CI: the
corpora are licensed and stay outside the repository.  Run it with::

    bazel test //tests:test_model_eval_corpus_regression --test_output=errors \\
      --test_env=GEC_CORPORA=$HOME/gec-testset/corpora.json

The built set files are not in the manifest, so they are compared by hand after a change of the
adapters.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.tools.store_workspace import COMMITTED
from tools.model_eval import recipes, testset

NEW_MANIFEST = COMMITTED / "testsets" / "gec-2026-12" / "manifest.json"
"""The committed manifest of the set the runs use, with bands and sentence spans."""

pytestmark = pytest.mark.skipif(
    not os.environ.get("GEC_CORPORA"),
    reason="the corpora are licensed and stay outside the repository; set GEC_CORPORA",
)


@pytest.fixture(scope="module")
def corpora() -> testset.Corpora:
    """The local corpora, as the ``GEC_CORPORA`` file names them."""
    return testset.Corpora(testset.corpus_paths([], Path(os.environ["GEC_CORPORA"])))


@pytest.fixture(scope="module")
def new_manifest() -> dict:
    """The committed manifest of ``gec-2026-12``."""
    return testset.read_manifest(NEW_MANIFEST)


def test_the_committed_new_set_still_rebuilds(corpora, new_manifest):
    """
    Given  the committed manifest of gec-2026-12 and the local corpora
    When   the corpus files are compared with what it pinned and every item is rebuilt
    Then   nothing differs, so the adapters still read the texts the set was built from
    """
    assert testset.corpus_problems(new_manifest, corpora) == []
    assert testset.check_items(new_manifest, corpora) == []


def test_the_committed_new_set_is_written_back_byte_for_byte(tmp_path, corpora, new_manifest):
    """
    Given  the committed manifest of gec-2026-12
    When   it is built again from its own pointers, day, seed and revision, and written
    Then   the bytes are the ones in the repository, so the format did not drift
    """
    rebuilt = testset.build_manifest(
        new_manifest["items"],
        corpora,
        testset=new_manifest["testset"],
        built=new_manifest["built"],
        selection_seed=new_manifest["selection_seed"],
        builder_revision=new_manifest["builder_revision"],
    )
    path = tmp_path / "manifest.json"

    testset.write_manifest(path, rebuilt)

    assert path.read_bytes() == NEW_MANIFEST.read_bytes()


def test_the_new_recipe_selects_the_committed_items(corpora, new_manifest):
    """
    Given  the recipe of gec-2026-12 and the local corpora
    When   it selects the texts
    Then   the items are the committed ones, in order, bands and pointers included
    """
    keys = ("id", "language", "kind", "corpus", "band", "pointer")

    selected = recipes.recipe("gec-2026-12").select(corpora)

    assert [{key: item.get(key) for key in keys} for item in selected] == [
        {key: item.get(key) for key in keys} for item in new_manifest["items"]
    ]


def test_every_german_and_english_text_of_the_new_set_is_whole(corpora, new_manifest):
    """
    Given  the committed manifest of gec-2026-12 and the local corpora
    When   every German and English item is resolved
    Then   no origin says "(excerpt)", so each item is a whole text of its corpus
    """
    origins = [
        testset.resolve(item, corpora).origin
        for item in new_manifest["items"]
        if item["language"] in {"de", "en"}
    ]

    assert len(origins) == 22
    assert not [origin for origin in origins if origin.endswith("(excerpt)")]
