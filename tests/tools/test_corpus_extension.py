"""A new corpus needs one adapter module and nothing else.

The corpus of this file is invented here, in the test, and taken through the whole way: build a
manifest, check it, export a run over it, validate that run in both modes, and name it on the
command line.  Nothing outside this file changes for it — that is the proof.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tests.tools import invented_corpus
from tests.tools.store_workspace import one, review, workspace
from tools import prepare_gec_testset, proofreading_store
from tools.model_eval import corpora, run_format, testset, validation
from tools.model_eval.corpora import CorpusAdapter, ResolvedItem, pointers
from teachers_teammate.domain.correction_review import ChangeNote

TOY_SOURCE = "Das ist ein Toy ."
TOY_REFERENCE = "Das ist ein Spielzeug ."


class ToyCorpus(CorpusAdapter):
    """A corpus of one invented German text, defined here and nowhere else."""

    NAME = "toy"
    LICENCE = "invented for this test"
    RESTRICTED = True
    LANGUAGES = ("de",)
    HINT = "<a JSONL file of toy texts>"

    def _files_of(self, split: str) -> dict[str, Path]:
        return {"toy.jsonl": self.root}

    def resolve(self, item: Mapping[str, Any]) -> ResolvedItem:
        """Build the one toy text from its pointer."""
        found = pointers(item)
        if len(found) != 1:
            raise ValueError(f"{item['id']}: a toy text is one pointer, not {len(found)}")
        record = self.records().get(found[0]["text"])
        if record is None:
            raise ValueError(f"{found[0]['text']!r} is not a text of the toy corpus")
        return self.make(
            item,
            origin=record["origin"],
            source=record["source"],
            reference=record["reference"],
        )

    def records(self) -> dict[str, dict]:
        """``{id: record}`` of the toy file, in file order."""
        return self._reading("records", "", self._read)

    def _read(self) -> dict[str, dict]:
        lines = self.root.read_text(encoding="utf-8").splitlines()
        return {record["id"]: record for record in (json.loads(line) for line in lines if line)}


def _toy_file(root: Path) -> Path:
    """Write the toy corpus below *root* and return its file."""
    path = root / "toy.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "toy-1",
                "origin": "invented for this test",
                "source": TOY_SOURCE,
                "reference": TOY_REFERENCE,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _toy_item() -> dict:
    """Return the one item of the toy corpus."""
    return {
        "id": "toy-01",
        "language": "de",
        "kind": "errors",
        "corpus": "toy",
        "pointer": {"text": "toy-1"},
    }


@pytest.fixture
def toy(tmp_path: Path, monkeypatch) -> Path:
    """Register the toy corpus and return its file, below ``tmp_path``."""
    path = _toy_file(tmp_path)
    monkeypatch.setitem(corpora.adapters(), ToyCorpus.NAME, ToyCorpus)
    return path


def test_a_new_corpus_builds_checks_exports_and_validates(tmp_path, toy):
    """
    Given  a corpus whose adapter is defined in this test file
    When   a set of one toy item and one Falko-MERLIN item is built, checked, exported and
           validated
    Then   the manifest records the toy corpus as restricted with its file pinned, both checks are
           empty, the run stores no toy words, and both validation modes pass
    """
    space = workspace(
        tmp_path,
        extra={"toy": toy},
        pointers=[_toy_item(), invented_corpus.item_pointers()[0]],
        overrides={"toy-01": lambda text: text.replace("Toy", "Spielzeug")},
        reviews={
            "toy-01": review(
                ChangeNote(
                    original="Toy",
                    corrected="Spielzeug",
                    category="word_choice",
                    explanation="Das ist ein Toy ist kein deutscher Satz.",
                ),
                original=TOY_SOURCE,
                corrected=TOY_REFERENCE,
            )
        },
    )
    manifest = testset.read_manifest(space.manifest)

    assert manifest["corpora"]["toy"]["restricted"] is True
    assert manifest["corpora"]["toy"]["files"] == {"toy.jsonl": testset.file_sha256(toy)}
    assert testset.corpus_problems(manifest, space.corpora) == []
    assert testset.check_items(manifest, space.corpora) == []

    written = one(space)
    item = next(i for i in run_format.read_run(written.path).records if i["item"] == "toy-01")
    assert item["edits"], "the model did change the text"
    for edit in item["edits"]:
        assert set(edit[2]) == {"h", "n"}, edit
    (note,) = item["notes"]
    assert note["explanation"] == ""
    assert validation.validate_run(written.path) == []
    assert validation.validate_run(written.path, space.corpora) == []


def test_a_new_corpus_is_named_on_the_command_line_like_the_others(toy, capsys, monkeypatch):
    """
    Given  a corpus whose adapter is defined in this test file
    When   it is named with --corpus and the help of the two command lines is printed
    Then   the option is accepted and both helps list the corpus with what its path expects
    """
    monkeypatch.setenv("COLUMNS", "200")  # one line, so the hint is not wrapped
    assert testset.corpus_paths([f"toy={toy}"], None) == {"toy": toy}

    with pytest.raises(SystemExit):
        proofreading_store.build_parser().parse_args(["export", "--help"])

    assert "toy=<a JSONL file of toy texts>" in capsys.readouterr().out
    assert "toy=<a JSONL file of toy texts>" in prepare_gec_testset._corpus_help()
