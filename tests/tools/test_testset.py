"""Tests of the pointers, resolvers and manifest (``testset.py``)."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.tools import invented_corpus
from tools.model_eval import testset
from tools.model_eval.testset import Corpora


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Corpora, dict]:
    """An invented corpus and the manifest that pins it, both below ``tmp_path``."""
    return invented_corpus.write_corpus(tmp_path)


# ── building and rebuilding ──────────────────────────────────────────────────


def test_a_manifest_built_from_pointers_resolves_back_to_its_hashes(corpus):
    """
    Given  a manifest built from the pointers of the invented corpus
    When   its items are rebuilt from the corpus and checked
    Then   every hash matches, so there are no problems
    """
    _, manifest = corpus
    problems = testset.check_items(manifest, corpus[0])
    assert problems == []


def test_the_invented_set_covers_every_kind_and_language(corpus):
    """
    Given  the manifest of the invented corpus
    When   its items and corpora are listed
    Then   German, English and French texts with errors and correct sentences are there, and only
           the restricted corpus is marked restricted
    """
    _, manifest = corpus
    assert [item["id"] for item in manifest["items"]] == [
        "de-01",
        "de-02",
        "de-correct",
        "en-01",
        "en-correct",
        "fr-01",
        "fr-correct",
    ]
    assert set(manifest["corpora"]) == {"falko-merlin", "wi", "locness", "invented-fr"}
    assert manifest["corpora"]["locness"]["restricted"] is True
    assert manifest["corpora"]["wi"]["restricted"] is False


def test_the_manifest_holds_no_corpus_text(corpus):
    """
    Given  the manifest of the invented corpus
    When   it is searched for the texts of the corpus
    Then   none of them is in it, and no pointer holds a source text
    """
    _, manifest = corpus
    stored = json.dumps(manifest, ensure_ascii=False)
    for text in (
        "Der Hund laufen schnell",
        "Der Hund läuft schnell",
        "Ich habe ein Hund",
        "I has a dog",
        invented_corpus.LOCNESS_TEXT,
        "Elle ont un chat",
    ):
        assert text not in stored
    for item in manifest["items"]:
        assert "source" not in item["pointer"]
        assert set(item["source"]) == {"sha256", "words", "chars"}


def test_edit_hashes_are_positions_and_hashes_only(corpus):
    """
    Given  the reference edit of a German text
    When   it is stored in the manifest
    Then   it holds position, label, word count and the hash of the replacement, nothing else
    """
    _, manifest = corpus
    (edit,) = manifest["items"][0]["edits"]
    assert set(edit) == {"start", "end", "h", "words", "label"}
    assert edit["label"] == "R:VERB:FORM"
    assert edit["h"] == testset.sha256_text("läuft")


def test_a_changed_corpus_file_is_detected(corpus):
    """
    Given  a corpus file that has changed since the manifest was built
    When   the corpora are compared with the manifest
    Then   the changed file is named as a problem
    """
    corpora, manifest = corpus
    path = corpora.root("falko-merlin") / "fm-test.m2"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    problems = testset.corpus_problems(manifest, corpora)
    assert any("fm-test.m2" in problem for problem in problems)


def test_a_missing_corpus_file_is_detected(corpus):
    """
    Given  a corpus file that has been deleted
    When   the corpora are compared with the manifest
    Then   the missing file is named as a problem
    """
    corpora, manifest = corpus
    (corpora.root("wi") / "json" / "A.dev.json").unlink()
    problems = testset.corpus_problems(manifest, corpora)
    assert any("A.dev.json" in problem for problem in problems)


def test_a_manifest_naming_an_unknown_corpus_is_a_problem_not_a_crash(corpus):
    """
    Given  a manifest that names a corpus the code has no adapter for
    When   the corpora are compared with the manifest
    Then   the name is a problem line with the known names, not a traceback
    """
    corpora, manifest = corpus
    manifest["corpora"]["nope"] = {"licence": "x", "restricted": False, "files": {}}

    problems = testset.corpus_problems(manifest, corpora)

    assert problems == [
        "nope: no corpus of that name (known: falko-merlin, invented-fr, locness, wi)"
    ]


def test_a_corpus_the_command_was_not_given_is_a_problem_with_the_flag_to_add(corpus):
    """
    Given  a manifest that names a corpus the command was not pointed at
    When   the corpora are compared with the manifest
    Then   the corpus is named once, with the --corpus option that would give it
    """
    corpora, manifest = corpus
    without_english = Corpora(
        {
            "falko-merlin": corpora.root("falko-merlin"),
            "invented-fr": corpora.root("invented-fr"),
        }
    )

    problems = testset.corpus_problems(manifest, without_english)

    assert problems == [
        "wi: not given (--corpus wi=<the extracted wi+locness/ directory>)",
        "locness: not given (--corpus locness=<the extracted wi+locness/ directory>)",
    ]


def test_a_restricted_flag_that_differs_from_the_adapter_is_a_problem(corpus):
    """
    Given  a manifest whose restricted flag for a corpus differs from the adapter's
    When   the corpora are compared with the manifest
    Then   the difference is a problem, because format and strict mode would apply other caps
    """
    corpora, manifest = corpus
    manifest["corpora"]["locness"]["restricted"] = False

    problems = testset.corpus_problems(manifest, corpora)

    assert problems == [
        "locness: the manifest says restricted=False, the adapter True: format and strict mode "
        "would apply different caps"
    ]


def test_an_item_that_does_not_resolve_is_a_problem_of_check_items(corpus):
    """
    Given  a manifest whose pointer names no text of its corpus
    When   its items are checked
    Then   the item is a problem line with the reason, not a traceback
    """
    corpora, manifest = corpus
    manifest["items"][0]["pointer"] = {
        "origin": "falko",
        "text": "no-such-text",
        "sentences": [0, 1],
    }

    problems = testset.check_items(manifest, corpora)

    assert problems == [
        "de-01: the pointer does not resolve "
        "(falko:no-such-text is not a text of the German test split)"
    ]


def test_a_changed_item_is_detected_even_with_the_same_corpus(corpus):
    """
    Given  a manifest that pins another hash for a source than the corpus gives
    When   its items are checked
    Then   the item is named with the hash found
    """
    corpora, manifest = corpus
    manifest["items"][0]["source"]["sha256"] = "0" * 64
    problems = testset.check_items(manifest, corpora)
    found = testset.sha256_text(testset.resolve(manifest["items"][0], corpora).source)[:12]
    assert problems == [f"de-01: source has hash {found}, the manifest another"]


def test_a_changed_reference_edit_is_detected(corpus):
    """
    Given  a manifest that stores another hash for a reference edit
    When   its items are checked
    Then   the item is reported as not having the stored reference edits
    """
    corpora, manifest = corpus
    manifest["items"][0]["edits"][0]["h"] = testset.sha256_text("läuft schnell")
    assert testset.check_items(manifest, corpora) == [
        "de-01: the reference edits are not the ones stored"
    ]


# ── characters, bands and sentence spans ─────────────────────────────────────


def test_the_manifest_records_characters_spans_and_band(tmp_path, corpus):
    """
    Given  a set whose German item carries a band and whose French item carries none
    When   the manifest is built
    Then   every item records the length of its source, the errors items record their sentence
           spans, and the band is copied only where the item has one
    """
    corpora, _ = corpus
    pointers = [
        dict(invented_corpus.item_pointers()[0], band="short"),
        invented_corpus.item_pointers()[5],
    ]

    manifest = invented_corpus.manifest(tmp_path, corpora, pointers=pointers)

    german = manifest["items"][0]
    french = manifest["items"][1]
    assert german["band"] == "short"
    assert "band" not in french
    for entry in manifest["items"]:
        assert entry["source"]["chars"] == len(testset.resolve(entry, corpora).source)
    assert german["sentence_spans"] == [[0, 8]]
    assert german["unchanged_sentences"] == []
    assert "sentence_spans" not in french


def test_check_items_reports_changed_sentence_spans(corpus):
    """
    Given  a manifest whose unchanged sentences were edited
    When   its items are checked
    Then   the item is reported as not having the stored sentence spans
    """
    corpora, manifest = corpus
    manifest["items"][0]["unchanged_sentences"] = [0]

    assert testset.check_items(manifest, corpora) == [
        "de-01: the sentence spans are not the ones stored"
    ]


def test_a_manifest_without_sentence_spans_is_still_checked(corpus):
    """
    Given  a manifest built before the sentence spans were recorded
    When   its items are checked
    Then   nothing is reported, because an absent key means "not recorded"
    """
    corpora, manifest = corpus
    for entry in manifest["items"]:
        entry.pop("sentence_spans", None)
        entry.pop("unchanged_sentences", None)

    assert testset.check_items(manifest, corpora) == []


# ── reading a manifest ───────────────────────────────────────────────────────


def test_a_manifest_round_trips_through_its_file(tmp_path, corpus):
    """
    Given  a manifest written to a file
    When   it is read back
    Then   it is the same manifest
    """
    _, manifest = corpus
    path = tmp_path / "read-back.json"
    testset.write_manifest(path, manifest)
    assert testset.read_manifest(path) == manifest


def test_an_unknown_manifest_schema_is_refused(tmp_path, corpus):
    """
    Given  a manifest of a schema version this tool does not know
    When   it is read
    Then   it is refused with the version named
    """
    _, manifest = corpus
    manifest["schema"] = 99
    path = tmp_path / "future.json"
    testset.write_manifest(path, manifest)
    with pytest.raises(ValueError, match="unsupported manifest schema 99"):
        testset.read_manifest(path)


def test_a_manifest_without_its_items_is_refused(tmp_path, corpus):
    """
    Given  a manifest file without its items
    When   it is read
    Then   it is refused with the missing key named
    """
    _, manifest = corpus
    del manifest["items"]
    path = tmp_path / "broken.json"
    testset.write_manifest(path, manifest)
    with pytest.raises(ValueError, match="'items'"):
        testset.read_manifest(path)


# ── pointers and resolvers ───────────────────────────────────────────────────


@pytest.mark.parametrize("index", range(len(invented_corpus.item_pointers())))
def test_every_pointer_resolves_to_the_hashes_of_the_manifest(corpus, index):
    """
    Given  each item of the invented manifest
    When   its pointer is resolved against the corpora
    Then   the source has the pinned hash and the item keeps its id, language and kind, and only the
           restricted corpus is restricted
    """
    corpora, manifest = corpus
    entry = manifest["items"][index]
    resolved = testset.resolve(entry, corpora)
    assert testset.sha256_text(resolved.source) == entry["source"]["sha256"]
    assert resolved.id == entry["id"]
    assert resolved.language == entry["language"]
    assert resolved.kind == entry["kind"]
    assert resolved.restricted is (entry["corpus"] == "locness")


def test_a_pointer_that_names_no_text_is_refused(corpus):
    """
    Given  a pointer to a text the corpus does not have
    When   it is resolved
    Then   it is refused with the text id named
    """
    corpora, _ = corpus
    item = {
        "id": "de-99",
        "language": "de",
        "kind": "errors",
        "corpus": "falko-merlin",
        "pointer": {"origin": "falko", "text": "no-such-text", "sentences": [0, 1]},
    }
    with pytest.raises(ValueError, match="no-such-text"):
        testset.resolve(item, corpora)


def test_the_correct_sentences_of_an_item_come_from_its_pointer(corpus):
    """
    Given  the pointer of a group of correct sentences
    When   it is resolved
    Then   the sentences and the source are those of the corpus
    """
    corpora, _ = corpus
    resolved = testset.resolve(invented_corpus.item_pointers()[4], corpora)
    assert resolved.sentences == (invented_corpus.LOCNESS_TEXT,)
    assert resolved.source == invented_corpus.LOCNESS_TEXT


def test_a_reference_edit_of_several_words_hashes_the_words_without_whitespace_tokens():
    """
    Given  a reference that inserts two words in one place
    When   the edits are stored as positions and hashes
    Then   the hash is that of the two words joined by one space and the count is two
    """
    resolved = testset.ResolvedItem(
        id="x-01",
        language="en",
        kind="errors",
        corpus="wi",
        restricted=False,
        origin="invented",
        source="I have dog.",
        reference="I have a big dog.",
    )

    (edit,) = testset.edit_hashes(resolved)

    assert edit["words"] == 2
    assert edit["h"] == testset.sha256_text("a big")


def test_a_deleted_word_hashes_the_empty_string():
    """
    Given  a reference that deletes one word
    When   the edits are stored as positions and hashes
    Then   the hash is that of the empty string and the word count is zero
    """
    resolved = testset.ResolvedItem(
        id="x-02",
        language="en",
        kind="errors",
        corpus="wi",
        restricted=False,
        origin="invented",
        source="I have a the dog.",
        reference="I have a dog.",
    )

    (edit,) = testset.edit_hashes(resolved)

    assert (edit["words"], edit["h"]) == (0, testset.sha256_text(""))
