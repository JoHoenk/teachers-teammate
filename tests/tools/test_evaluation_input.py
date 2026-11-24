"""Unit tests for the input layer: the corpora, the labels of their M2 files, and the manifest.

The acceptance test that needs a live Ollama server is not here: an M2 file is read as sentences
and annotator-0 edits, a
label finds its change in characters, a pointer becomes a text, and a manifest built from pointers
is checked back against the corpora it pinned.

Nothing here reads a real learner corpus: the M2 files are written in the test, so a failure names
the rule that broke and not a file on somebody's disk.  The one corpus that is in the repository
(``invented-fr``) is read as it is, because it is the only one that needs no licence to be present.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from tools.evaluation.core.hashing import sha256_text
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest
from tools.evaluation.input.corpora import adapter_class, names
from tools.evaluation.input.corpora._base import ResolvedItem, pointers
from tools.evaluation.input.corpora.invented_fr import InventedFrench
from tools.evaluation.input.labels import (
    M2Edit,
    M2Sentence,
    label_reference,
    locate,
    read_m2,
    split_into_texts,
)
from tools.evaluation.input.testset import (
    Corpora,
    band_summary,
    build_manifest,
    check_items,
    corpus_paths,
    corpus_problems,
    edit_hashes,
    read_manifest,
    resolve,
    to_record,
    write_manifest,
)

# ── the M2 file ─────────────────────────────────────────────────────────────

_M2 = """\
S Der Hund lauft schnell .
A 2 3|||R:VERB:FORM|||läuft|||-|||-|||0
S Der Hund bellt .
A 1 1|||noop|||-|||-|||-|||0
S Der Hund bellt laut .
A 3 3|||UNK|||-|||-|||-|||0
A 1 2|||R:NOUN|||Katze|||-|||-|||1
"""


def _write_m2(tmp_path: Path, text: str = _M2) -> Path:
    """Return the path of an M2 file holding *text*."""
    path = tmp_path / "corpus.m2"
    path.write_text(text, encoding="utf-8")
    return path


def test_an_m2_file_is_read_as_sentences_and_annotator_zero_edits(tmp_path):
    """
    Given  an M2 file whose first sentence carries one edit of annotator 0
    When   it is read
    Then   the sentence's tokens and that edit come back, and the other annotator's edit does not
    """
    path = _write_m2(tmp_path)

    sentences = read_m2(path)

    assert sentences[0] == M2Sentence(
        ("Der", "Hund", "lauft", "schnell", "."), (M2Edit(2, 3, "R:VERB:FORM"),)
    )
    assert [edit.code for sentence in sentences for edit in sentence.edits] == ["R:VERB:FORM"]


def test_a_noop_is_no_edit_and_an_unk_marks_the_sentence(tmp_path):
    """
    Given  an M2 file with a ``noop`` sentence and one whose error the annotator could not correct
    When   it is read
    Then   neither adds an edit, and only the second is marked uncorrected
    """
    path = _write_m2(tmp_path)

    sentences = read_m2(path)

    assert sentences[1].edits == ()
    assert sentences[1].uncorrected is False
    assert sentences[2].edits == ()
    assert sentences[2].uncorrected is True


def test_an_m2_file_without_a_sentence_is_empty(tmp_path):
    """
    Given  an M2 file with no ``S`` line
    When   it is read
    Then   there is no sentence, and no traceback
    """
    path = _write_m2(tmp_path, "A 0 1|||R:X|||y|||-|||-|||0\n")

    assert read_m2(path) == []


# ── finding the M2 tokens in the text ───────────────────────────────────────


def test_locate_returns_the_character_span_of_every_token():
    """
    Given  a text and the M2 sentence that spells it out token by token
    When   the tokens are located
    Then   every token has its character span, and the spans spell the text back
    """
    text = "Der Hund lauft schnell."
    sentences = [M2Sentence(("Der", "Hund", "lauft", "schnell", "."), ())]

    located = locate(text, sentences)

    assert located is not None
    assert located == [((0, 3), (4, 8), (9, 14), (15, 22), (22, 23))]
    assert [text[start:end] for start, end in located[0]] == list(sentences[0].tokens)


def test_locate_returns_none_when_a_token_is_not_where_it_should_be():
    """
    Given  a text and M2 sentences of a different text
    When   the tokens are located
    Then   the answer is ``None``, not a span of the wrong words
    """
    sentences = [M2Sentence(("Der", "Katze", "lauft", "."), ())]

    assert locate("Der Hund lauft schnell.", sentences) is None


def test_locate_returns_none_when_the_sentences_end_before_the_text():
    """
    Given  a text of two sentences and the M2 sentence of only the first
    When   the tokens are located
    Then   the answer is ``None``: these are not the sentences of this text
    """
    sentences = [M2Sentence(("Der", "Hund", "lauft", "."), ())]

    assert locate("Der Hund lauft. Der Hund bellt.", sentences) is None


def test_locate_stops_when_the_text_is_used_up():
    """
    Given  a text of one sentence and the M2 sentences of two
    When   the tokens are located
    Then   only the sentence the text holds comes back
    """
    sentences = [
        M2Sentence(("Der", "Hund", "lauft", "."), ()),
        M2Sentence(("Der", "Hund", "bellt", "."), ()),
    ]

    located = locate("Der Hund lauft.", sentences)

    assert located == [((0, 3), (4, 8), (9, 14), (14, 15))]


def test_locate_normalises_the_punctuation_the_bea_conversion_normalised():
    """
    Given  a text with a typographic apostrophe and an M2 token with a straight one
    When   the tokens are located
    Then   the token is found, because the conversion normalised the same characters
    """
    sentences = [M2Sentence(("C'est", "bon", "."), ())]

    located = locate("C\u2019est bon.", sentences)

    assert located == [((0, 5), (6, 9), (9, 10))]


# ── splitting the M2 sentences into texts ───────────────────────────────────


def test_split_into_texts_finds_each_text_in_the_m2_sentences():
    """
    Given  two texts and the M2 sentences of both, one after another
    When   the texts are split
    Then   each text names its first sentence and the spans of its own tokens
    """
    texts = ["Der Hund lauft.", "Der Hund bellt."]
    sentences = [
        M2Sentence(("Der", "Hund", "lauft", "."), ()),
        M2Sentence(("Der", "Hund", "bellt", "."), ()),
    ]

    split = split_into_texts(texts, sentences)

    assert [found[0] if found else None for found in split] == [0, 1]
    assert split[1] == (1, [((0, 3), (4, 8), (9, 14), (14, 15))])


def test_split_into_texts_resynchronises_after_a_text_it_cannot_find():
    """
    Given  three texts and the M2 sentences of the first and the last
    When   the texts are split
    Then   the middle text is ``None`` and the last is still found
    """
    texts = ["Der Hund lauft.", "Der Hund bellt laut.", "Der Hund bellt."]
    sentences = [
        M2Sentence(("Der", "Hund", "lauft", "."), ()),
        M2Sentence(("Der", "Hund", "bellt", "."), ()),
    ]

    split = split_into_texts(texts, sentences)

    assert split[1] is None
    assert split[2] is not None
    assert split[2][0] == 1


# ── labelling the reference changes ─────────────────────────────────────────


def _located(source: str, tokens: tuple[str, ...], edits: tuple[M2Edit, ...]):
    """Return the located M2 sentences of *source*, spelled out as *tokens*."""
    sentence = M2Sentence(tokens, edits)
    found = locate(source, [sentence])
    assert found is not None
    return [(sentence, found[0])]


def test_a_reference_change_takes_the_label_of_the_annotated_edit():
    """
    Given  a source, its reference correction and the M2 edit that covers the corrected word
    When   the reference changes are labelled
    Then   the change carries the corpus code
    """
    source = "Der Hund lauft schnell."
    located = _located(
        source,
        ("Der", "Hund", "lauft", "schnell", "."),
        (M2Edit(2, 3, "R:VERB:FORM"),),
    )

    labels = label_reference(source, "Der Hund läuft schnell.", located)

    assert labels == (ReferenceLabel(9, 14, "läuft", "R:VERB:FORM"),)


def test_a_reference_change_far_from_every_edit_keeps_an_empty_code():
    """
    Given  a reference correction that also changes a word no annotated edit is near
    When   the reference changes are labelled
    Then   that change keeps an empty code, and the labelled one keeps its own
    """
    source = "Der Hund lauft schnell und gern."
    located = _located(
        source,
        ("Der", "Hund", "lauft", "schnell", "und", "gern", "."),
        (M2Edit(2, 3, "R:VERB:FORM"),),
    )

    labels = label_reference(source, "Der Hund läuft schnell und gern!", located)

    assert [label.code for label in labels] == ["R:VERB:FORM", ""]


def test_a_label_holds_character_positions_and_the_joined_replacement():
    """
    Given  a reference correction that deletes a word
    When   the reference changes are labelled
    Then   the label's span is in characters of the source and its replacement is empty
    """
    source = "Der Hund lauft schnell."
    located = _located(
        source,
        ("Der", "Hund", "lauft", "schnell", "."),
        (M2Edit(2, 3, "R:VERB:FORM"),),
    )

    labels = label_reference(source, "Der Hund schnell.", located)

    assert labels[0].span == (9, 15)
    assert source[labels[0].start : labels[0].end] == "lauft "
    assert labels[0].replacement == ""


def test_a_label_of_an_insertion_sits_between_two_characters():
    """
    Given  a reference correction that inserts a word
    When   the reference changes are labelled
    Then   the label's span is empty and sits where the word goes

    The replacement keeps the whitespace tokens the word diff gives it, because the validator
    compares it against ``"".join(replacement)`` of the reference's own edits.
    """
    source = "Der Hund lauft schnell."
    located = _located(
        source,
        ("Der", "Hund", "lauft", "schnell", "."),
        (M2Edit(2, 3, "R:VERB:FORM"),),
    )

    labels = label_reference(source, "Der Hund lauft sehr schnell.", located)

    assert labels[0].start == labels[0].end
    assert labels[0].replacement.strip() == "sehr"


# ── the record ──────────────────────────────────────────────────────────────


def _resolved(**overrides) -> ResolvedItem:
    """Return a resolved item of the invented French set, with *overrides* applied."""
    fields: dict[str, Any] = {
        "id": "fr-01",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "restricted": False,
        "origin": "invented",
        "source": "Der Hund lauft schnell.",
        "reference": "Der Hund läuft schnell.",
    }
    return ResolvedItem(**{**fields, **overrides})


def test_a_record_carries_the_corpus_and_the_pointer():
    """
    Given  a resolved item and the pointer it was built from
    When   it becomes a record
    Then   the record names the corpus and keeps the pointer as provenance
    """
    record = to_record(_resolved(), band="page", pointer={"text": "fr-01"})

    assert record.corpus == "invented-fr"
    assert record.pointer == {"text": "fr-01"}
    assert record.band == "page"


def test_a_record_without_a_band_or_a_pointer_has_neither():
    """
    Given  a resolved item and no band and no pointer
    When   it becomes a record
    Then   both are empty, and the stored form leaves them out
    """
    record = to_record(_resolved())

    assert record.band == ""
    assert record.pointer == {}
    assert "band" not in record.to_dict()
    assert "pointer" not in record.to_dict()


def test_a_record_keeps_no_level():
    """
    Given  a resolved item whose corpus gives it a CEFR level
    When   it becomes a record
    Then   the level is not in the record: it is a fact about the corpus, not about the text
    """
    record = to_record(_resolved(level="B1"))

    assert "level" not in record.to_dict()


# ── the reference edits of a manifest item ──────────────────────────────────


def test_edit_hashes_are_character_positions():
    """
    Given  a resolved item whose reference corrects one word
    When   its edits are hashed
    Then   the position is in characters of the source, and the replacement is a hash only
    """
    resolved = _resolved(labels=(ReferenceLabel(9, 14, "läuft", "R:VERB:FORM"),))

    entries = edit_hashes(resolved)

    assert entries == [
        {
            "start": 9,
            "end": 14,
            "h": sha256_text("läuft"),
            "words": 1,
            "label": "R:VERB:FORM",
        }
    ]


def test_a_correct_item_has_no_edits():
    """
    Given  a resolved item of the ``correct`` kind
    When   its edits are hashed
    Then   there are none: nothing is corrected, so nothing is an edit
    """
    assert edit_hashes(_resolved(kind="correct", reference="")) == []


# ── the corpora of one command ──────────────────────────────────────────────


def test_corpora_refuses_an_unknown_name():
    """
    Given  a corpus name no adapter has
    When   the corpora are built
    Then   the refusal lists the known names
    """
    with pytest.raises(ValueError, match="no corpus 'nope'"):
        Corpora({"nope": Path("/tmp")})


def test_corpora_refuses_a_corpus_without_a_path():
    """
    Given  no path for a corpus that has no default location
    When   its adapter is asked for
    Then   the refusal says what ``--corpus`` expects
    """
    corpora = Corpora({})

    with pytest.raises(ValueError, match="falko-merlin: not given"):
        corpora.adapter("falko-merlin")


def test_corpora_missing_lists_the_corpora_without_a_path():
    """
    Given  one corpus that is in the repository and one that is not
    When   the missing ones are asked for
    Then   only the one without a default location is listed
    """
    corpora = Corpora({})

    assert corpora.missing(["invented-fr", "falko-merlin"]) == ["falko-merlin"]


def test_corpora_reads_a_corpus_once():
    """
    Given  a corpus named twice
    When   its adapter is asked for twice
    Then   it is the same adapter, so the corpus is read once
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})

    assert corpora.adapter("invented-fr") is corpora.adapter("invented-fr")
    assert corpora.root("invented-fr") == InventedFrench.DEFAULT_ROOT


def test_corpus_paths_reads_name_equals_path():
    """
    Given  a ``--corpus NAME=PATH`` option
    When   the paths are read
    Then   the name maps to the path
    """
    assert corpus_paths(["invented-fr=/tmp/fr.jsonl"], None) == {
        "invented-fr": Path("/tmp/fr.jsonl")
    }


def test_corpus_paths_refuses_a_value_without_an_equals_sign():
    """
    Given  a ``--corpus`` value that is not ``NAME=PATH``
    When   the paths are read
    Then   the refusal says what the option expects
    """
    with pytest.raises(ValueError, match="is not NAME=PATH"):
        corpus_paths(["invented-fr"], None)


def test_corpus_paths_refuses_an_unknown_corpus():
    """
    Given  a ``--corpus`` option that names no corpus
    When   the paths are read
    Then   the refusal lists the known names
    """
    with pytest.raises(ValueError, match="no corpus 'nope'"):
        corpus_paths(["nope=/tmp"], None)


def test_corpus_paths_refuses_a_name_twice():
    """
    Given  the same corpus named by two ``--corpus`` options
    When   the paths are read
    Then   the second is refused rather than silently winning
    """
    with pytest.raises(ValueError, match="named twice"):
        corpus_paths(["invented-fr=/tmp/a", "invented-fr=/tmp/b"], None)


def test_corpus_paths_reads_a_corpora_file_relative_to_it(tmp_path):
    """
    Given  a ``--corpora`` file whose path is relative
    When   the paths are read
    Then   the path is taken from the file's own directory
    """
    file = tmp_path / "corpora.json"
    file.write_text(json.dumps({"invented-fr": "fr.jsonl"}), encoding="utf-8")

    assert corpus_paths([], file) == {"invented-fr": (tmp_path / "fr.jsonl").resolve()}


def test_a_corpora_file_that_is_not_an_object_of_strings_is_refused(tmp_path):
    """
    Given  a ``--corpora`` file that holds a list
    When   the paths are read
    Then   the refusal names the file and what it should hold
    """
    file = tmp_path / "corpora.json"
    file.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="not a JSON object of name: path strings"):
        corpus_paths([], file)


# ── a pointer becomes a text ────────────────────────────────────────────────


def test_resolve_builds_a_text_from_its_pointer():
    """
    Given  a pointer into the invented French set
    When   the item is resolved
    Then   the source and the reference come back, and the corpus is named
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    item = {
        "id": "fr-01",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "pointer": {"text": "fr-01"},
    }

    resolved = resolve(item, corpora)

    assert resolved.corpus == "invented-fr"
    assert resolved.restricted is False
    assert resolved.source != resolved.reference


def test_resolve_refuses_a_language_the_corpus_does_not_have():
    """
    Given  an item whose language the corpus has no texts in
    When   it is resolved
    Then   the refusal names the item and the corpus
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    item = {
        "id": "de-01",
        "language": "de",
        "kind": "errors",
        "corpus": "invented-fr",
        "pointer": {"text": "fr-01"},
    }

    with pytest.raises(ValueError, match="has no 'de' texts"):
        resolve(item, corpora)


def test_resolve_refuses_a_text_the_corpus_does_not_have():
    """
    Given  a pointer that names no text of the corpus
    When   it is resolved
    Then   the refusal names the text
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    item = {
        "id": "fr-99",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "pointer": {"text": "fr-99"},
    }

    with pytest.raises(ValueError, match="is not a text of the invented French set"):
        resolve(item, corpora)


def test_pointers_accepts_one_pointer_or_several():
    """
    Given  an item with one pointer and one with a list of them
    When   the pointers are read
    Then   both come back as a list
    """
    assert pointers({"id": "x", "pointer": {"text": "a"}}) == [{"text": "a"}]
    assert pointers({"id": "x", "pointer": [{"text": "a"}, {"text": "b"}]}) == [
        {"text": "a"},
        {"text": "b"},
    ]


def test_pointers_refuses_an_item_without_a_pointer():
    """
    Given  an item whose pointer list is empty
    When   the pointers are read
    Then   the refusal names the item
    """
    with pytest.raises(ValueError, match="x: the item has no pointer"):
        pointers({"id": "x", "pointer": []})


# ── the manifest ────────────────────────────────────────────────────────────


def _items() -> list[dict]:
    """Return the pointers of a small test set: one text with errors, one already correct."""
    return [
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


def _manifest(corpora: Corpora) -> Manifest:
    """Return the manifest of :func:`_items`."""
    return build_manifest(
        _items(),
        corpora,
        testset="gec-2026-12",
        built="2026-01-01",
        selection_seed=20260101,
        builder_revision="test",
    )


def test_a_built_manifest_checks_back_against_its_corpora():
    """
    Given  a manifest built from the pointers of a test set
    When   it is checked against the same corpora
    Then   nothing differs
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})

    manifest = _manifest(corpora)

    assert check_items(manifest, corpora) == []
    assert corpus_problems(manifest, corpora) == []


def test_a_manifest_holds_no_corpus_text():
    """
    Given  a manifest built from a text with errors
    When   its item is read
    Then   the reference is a hash, and the edits are positions and hashes
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})

    manifest = _manifest(corpora)

    item = manifest.items[0]
    assert set(item.reference) == {"sha256"}
    assert all(set(edit) == {"start", "end", "h", "words", "label"} for edit in item.edits)


def test_a_correct_item_pins_its_sentences_and_not_a_reference():
    """
    Given  a manifest built from an already-correct text
    When   its item is read
    Then   it holds the hash of every sentence and no reference
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})

    manifest = _manifest(corpora)

    item = manifest.items[1]
    assert not item.reference
    assert item.sentence_sha256
    assert item.edits == ()


def test_a_manifest_round_trips_through_the_file(tmp_path):
    """
    Given  a manifest written to a file
    When   it is read back
    Then   it is the same manifest
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    path = tmp_path / "testsets" / "gec-2026-12" / "manifest.json"

    write_manifest(path, manifest)

    assert read_manifest(path) == manifest


def test_a_manifest_of_another_format_is_refused(tmp_path):
    """
    Given  a manifest whose format version is not this reader's
    When   it is read
    Then   the refusal names the version, the file and the converter that writes this one
    """
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"format": 3, "testset": "x"}), encoding="utf-8")

    with pytest.raises(ValueError, match="format 3, this reader knows format 2"):
        read_manifest(path)


def test_a_manifest_of_the_old_schema_is_refused(tmp_path):
    """
    Given  a manifest of the old schema, which pinned token positions
    When   it is read
    Then   the refusal names the old schema and the format this reader knows
    """
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": 1, "testset": "x"}), encoding="utf-8")

    with pytest.raises(ValueError, match="the old schema 1, which pinned token positions"):
        read_manifest(path)


def test_a_changed_source_is_reported():
    """
    Given  a manifest whose item pins another source hash
    When   it is checked
    Then   the item is reported, and the rest of it is not checked
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    manifest = replace(
        manifest,
        items=(replace(manifest.items[0], source={"sha256": "0" * 64}), *manifest.items[1:]),
    )

    assert check_items(manifest, corpora) == [
        "fr-01: source has hash 76a9799989b0, the manifest another"
    ]


def test_a_pointer_that_does_not_resolve_is_reported():
    """
    Given  a manifest whose item names a text the corpus does not have
    When   it is checked
    Then   the item is reported with the reason, not with a traceback
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    manifest = replace(
        manifest, items=(replace(manifest.items[0], pointer={"text": "fr-99"}), *manifest.items[1:])
    )

    problems = check_items(manifest, corpora)

    assert len(problems) == 1
    assert problems[0].startswith("fr-01: the pointer does not resolve")


def test_changed_reference_edits_are_reported():
    """
    Given  a manifest whose item pins no edits
    When   it is checked
    Then   the item is reported: the corpus still makes the annotators correct it
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    manifest = replace(manifest, items=(replace(manifest.items[0], edits=()), *manifest.items[1:]))

    assert check_items(manifest, corpora) == ["fr-01: the reference edits are not the ones stored"]


def test_a_changed_corpus_file_is_reported(tmp_path):
    """
    Given  a manifest that pinned a corpus file and a corpus whose file has changed
    When   the corpora are checked
    Then   the file is reported with both hashes
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    changed = tmp_path / "gec_fr_invented.jsonl"
    changed.write_text("{}\n", encoding="utf-8")

    problems = corpus_problems(manifest, Corpora({"invented-fr": changed}))

    assert len(problems) == 1
    assert problems[0].startswith("invented-fr: tools/gec_fr_invented.jsonl has hash ")


def test_a_manifest_that_pins_another_restricted_flag_is_reported():
    """
    Given  a manifest whose corpus entry says the corpus is restricted
    When   the corpora are checked
    Then   the difference is reported: format and strict mode would apply other caps
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    pinned = dict(manifest.corpora["invented-fr"], restricted=True)
    manifest = replace(manifest, corpora={**manifest.corpora, "invented-fr": pinned})

    problems = corpus_problems(manifest, corpora)

    assert len(problems) == 1
    assert "the manifest says restricted=True" in problems[0]


def test_a_manifest_that_names_an_unknown_corpus_is_reported():
    """
    Given  a manifest whose corpus entry names no corpus of the code
    When   the corpora are checked
    Then   the name is reported with the known ones
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    unknown = {"licence": "x", "restricted": False, "files": {}}
    manifest = replace(manifest, corpora={**manifest.corpora, "nope": unknown})

    problems = corpus_problems(manifest, corpora)

    assert problems == [
        "nope: no corpus of that name (known: falko-merlin, invented-fr, locness, wi)"
    ]


def test_a_manifest_that_names_a_corpus_without_a_path_is_reported():
    """
    Given  a manifest that pins a corpus this command was not given
    When   the corpora are checked
    Then   the corpus is reported with the ``--corpus`` option that would fix it
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    manifest = _manifest(corpora)
    missing = {"licence": "x", "restricted": True, "files": {}}
    manifest = replace(manifest, corpora={**manifest.corpora, "falko-merlin": missing})

    problems = corpus_problems(manifest, corpora)

    assert len(problems) == 1
    assert problems[0].startswith("falko-merlin: not given (--corpus falko-merlin=")


# ── the small helpers ───────────────────────────────────────────────────────


def test_band_summary_lists_the_known_bands_first():
    """
    Given  a count per band, one of them a band the recipes do not know
    When   the summary is written
    Then   the known bands come in their own order and the rest follow by name
    """
    assert band_summary({"pages": 2, "short": 4, "page": 5, "huge": 1}) == (
        "short 4, page 5, pages 2, huge 1"
    )


def test_the_corpora_are_the_ones_the_adapters_name():
    """
    Given  the adapters found at run time
    When   their names are asked for
    Then   every name is the one its adapter class declares
    """
    assert names() == ["falko-merlin", "invented-fr", "locness", "wi"]
    assert all(adapter_class(name).NAME == name for name in names())
