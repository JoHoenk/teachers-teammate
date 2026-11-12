"""Tests of the corpus registry, the adapters and the recipes (``corpora/``, ``recipes/``)."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.domain.correction_review import tokenize

from tests.tools import invented_corpus
from tools.model_eval import corpora, recipes, testset
from tools.model_eval.corpora._base import token_spans
from tools.model_eval.corpora._bea import _corpus_unchanged, _sentence_chars, apply_edits
from tools.model_eval.error_labels import M2Sentence
from tools.model_eval.languages import LANGUAGE_NAMES
from tools.model_eval.testset import Corpora


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[Corpora, dict]:
    """An invented corpus and the manifest that pins it, both below ``tmp_path``."""
    return invented_corpus.write_corpus(tmp_path)


# ── the registry ─────────────────────────────────────────────────────────────


def test_the_registry_finds_the_four_corpora():
    """
    Given  the corpus package
    When   the registry is asked for the corpora
    Then   the four adapters are found, by the names manifests use
    """
    assert corpora.names() == ["falko-merlin", "invented-fr", "locness", "wi"]


@pytest.mark.parametrize("name", corpora.names())
def test_every_adapter_declares_what_a_manifest_needs(name):
    """
    Given  each registered corpus
    When   its adapter class is read
    Then   it declares a licence, a hint, a boolean restriction and languages the tool can ask in,
           and its module is named after the corpus
    """
    adapter = corpora.adapter_class(name)

    assert adapter.NAME == name
    assert adapter.LICENCE.strip()
    assert adapter.HINT.strip()
    assert isinstance(adapter.RESTRICTED, bool)
    assert adapter.LANGUAGES
    assert set(adapter.LANGUAGES) <= set(LANGUAGE_NAMES)
    assert adapter.__module__.rsplit(".", 1)[-1] == name.replace("-", "_")


def test_an_unknown_corpus_is_refused_with_the_known_names():
    """
    Given  a corpus name no adapter has
    When   its adapter class is asked for
    Then   it is refused with the names that exist
    """
    with pytest.raises(ValueError, match="no corpus 'nope'; known: falko-merlin, invented-fr"):
        corpora.adapter_class("nope")


def test_a_corpora_object_refuses_an_unknown_name():
    """
    Given  a corpus name no adapter has
    When   the corpora of a command are built from it
    Then   it is refused, so a typo cannot silently drop a corpus
    """
    with pytest.raises(ValueError, match="no corpus 'nope'"):
        Corpora({"nope": Path("/tmp")})


# ── resolving an item ────────────────────────────────────────────────────────


def test_an_item_is_resolved_by_its_corpus_not_its_language(corpus):
    """
    Given  an item that names the German corpus but the English language
    When   it is resolved
    Then   it is refused, because the corpus decides which adapter reads it
    """
    corpora_of_the_set, _ = corpus
    item = {
        "id": "de-99",
        "language": "en",
        "kind": "errors",
        "corpus": "falko-merlin",
        "pointer": {"origin": "falko", "text": "falko-1", "sentences": [0, 1]},
    }

    with pytest.raises(ValueError, match="the corpus 'falko-merlin' has no 'en' texts"):
        testset.resolve(item, corpora_of_the_set)


def test_the_essay_corpus_refuses_a_native_level_and_the_native_corpus_an_essay_level(corpus):
    """
    Given  a W&I pointer to the native level and a LOCNESS pointer to an essay level
    When   each is resolved
    Then   both are refused, because the two corpora are two names with one level each
    """
    corpora_of_the_set, _ = corpus
    native = {
        "id": "en-99",
        "language": "en",
        "kind": "errors",
        "corpus": "wi",
        "pointer": {"level": "N", "document": "n-1"},
    }
    essay = {
        "id": "en-98",
        "language": "en",
        "kind": "correct",
        "corpus": "locness",
        "pointer": {"level": "A", "document": "a-1", "span": [0, 5]},
    }

    with pytest.raises(ValueError, match="has no level 'N'"):
        testset.resolve(native, corpora_of_the_set)
    with pytest.raises(ValueError, match="has no level 'A'"):
        testset.resolve(essay, corpora_of_the_set)


def test_the_native_corpus_refuses_an_essay_with_errors(corpus):
    """
    Given  a LOCNESS item of an essay with errors
    When   it is resolved
    Then   it is refused, because labelling an essay would need a file the adapter does not pin
    """
    corpora_of_the_set, _ = corpus
    item = {
        "id": "en-97",
        "language": "en",
        "kind": "errors",
        "corpus": "locness",
        "pointer": {"level": "N", "document": "n-1"},
    }

    with pytest.raises(ValueError, match=r"m2/N\.dev\.gold\.bea19\.m2"):
        testset.resolve(item, corpora_of_the_set)


def test_resolving_every_item_reads_only_pinned_files(corpus, monkeypatch):
    """
    Given  the manifest of the invented corpus
    When   every item is resolved while the file reads are recorded
    Then   every path read is a file some adapter pins, so the hashes of a manifest cover what was
           read
    """
    corpora_of_the_set, manifest = corpus
    # A fresh Corpora: the one the fixture built has already read the corpus to pin it.
    fresh = Corpora({name: corpora_of_the_set.root(name) for name in corpora_of_the_set.names})
    pinned = {
        path.resolve() for name in fresh.names for path in fresh.adapter(name).files().values()
    }
    read: list[Path] = []
    original_open, original_read_text = Path.open, Path.read_text

    def recording_open(self, *args, **kwargs):
        read.append(self)
        return original_open(self, *args, **kwargs)

    def recording_read_text(self, *args, **kwargs):
        read.append(self)
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", recording_open)
    monkeypatch.setattr(Path, "read_text", recording_read_text)

    for entry in manifest["items"]:
        testset.resolve(entry, fresh)

    assert read
    assert {path.resolve() for path in read} <= pinned


def test_the_french_corpus_needs_no_path():
    """
    Given  a command that names no corpus at all
    When   an item of the invented French set is resolved
    Then   it is read from the file in the repository, so the corpus is never required
    """
    item = {
        "id": "fr-01",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "pointer": {"text": "fr-01"},
    }

    resolved = testset.resolve(item, Corpora({}))

    assert resolved.source.startswith("Hier, je suis allé au cinéma")
    assert resolved.restricted is False


def test_apply_edits_splices_a_replacement_into_the_source():
    """
    Given  a source text and corpus edits as character offsets
    When   the edits are applied
    Then   the replacements are spliced in, tail first, and an empty list or a deletion without text
           leaves the source
    """
    source = "I has a dog."
    assert apply_edits(source, [(2, 5, "have")]) == "I have a dog."
    assert apply_edits(source, [(2, 5, "have"), (0, 1, "We")]) == "We have a dog."
    assert apply_edits(source, []) == source
    assert apply_edits(source, [(2, 5, None)]) == source


# ── splits ───────────────────────────────────────────────────────────────────


def _train_pointers() -> list[dict]:
    """Return one German and one English pointer of the train split."""
    return [
        {
            "id": "de-train",
            "language": "de",
            "kind": "errors",
            "corpus": "falko-merlin",
            "pointer": {
                "origin": "falko",
                "text": "falko-train-1",
                "sentences": [0, 1],
                "split": "train",
            },
        },
        {
            "id": "en-train",
            "language": "en",
            "kind": "errors",
            "corpus": "wi",
            "pointer": {"level": "A", "document": "a-train-1", "split": "train"},
        },
    ]


def test_a_pointer_without_a_split_reads_the_default_split(corpus):
    """
    Given  a German pointer that names no split
    When   it is resolved
    Then   it reads the test split, which is the corpus's default one
    """
    corpora_of_the_set, _ = corpus
    item = invented_corpus.item_pointers()[0]

    resolved = testset.resolve(item, corpora_of_the_set)

    assert resolved.source == "Der Hund laufen schnell."
    assert corpora_of_the_set.adapter("falko-merlin").splits_of(item) == {"test"}


def test_a_train_pointer_resolves_from_the_train_files(corpus):
    """
    Given  a German pointer that names the train split
    When   it is resolved
    Then   the text comes from the train files, and the origin names that split
    """
    corpora_of_the_set, _ = corpus

    resolved = testset.resolve(_train_pointers()[0], corpora_of_the_set)

    assert resolved.source == "Der Hund laufen schnell."
    assert resolved.reference == "Der Hund läuft schnell."
    assert resolved.origin.startswith("Falko-MERLIN train")


def test_a_manifest_pins_only_the_files_of_the_splits_its_items_use(tmp_path):
    """
    Given  a set of dev-only items and one that adds a train item
    When   each manifest is built
    Then   the first pins no train file, and the second pins the train files of both corpora
    """
    corpora_of_the_set = invented_corpus.build_corpus(tmp_path)

    dev_only = invented_corpus.manifest(tmp_path, corpora_of_the_set)
    with_train = invented_corpus.manifest(
        tmp_path,
        corpora_of_the_set,
        pointers=invented_corpus.item_pointers() + _train_pointers(),
    )

    assert not any(".train" in key for key in dev_only["corpora"]["falko-merlin"]["files"])
    assert not any(".train" in key for key in dev_only["corpora"]["wi"]["files"])
    assert "fm-train.m2" in with_train["corpora"]["falko-merlin"]["files"]
    assert "json/A.train.json" in with_train["corpora"]["wi"]["files"]


def test_a_changed_train_file_is_a_corpus_problem(tmp_path):
    """
    Given  a manifest that pins the train files, and a train file that changed since
    When   the corpora are compared with the manifest
    Then   the changed train file is named as a problem
    """
    corpora_of_the_set = invented_corpus.build_corpus(tmp_path)
    manifest = invented_corpus.manifest(tmp_path, corpora_of_the_set, pointers=_train_pointers())
    path = corpora_of_the_set.root("falko-merlin") / "fm-train.m2"
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    problems = testset.corpus_problems(manifest, corpora_of_the_set)

    assert any("fm-train.m2" in problem for problem in problems)


def test_an_unknown_split_is_refused(corpus):
    """
    Given  a pointer that names a split the corpus does not have
    When   it is resolved
    Then   it is refused with the splits that exist
    """
    corpora_of_the_set, _ = corpus
    item = dict(invented_corpus.item_pointers()[0])
    item["pointer"] = dict(item["pointer"], split="nope")

    with pytest.raises(ValueError, match="no split 'nope'; known: test, dev, train"):
        testset.resolve(item, corpora_of_the_set)


def test_a_split_on_a_corpus_without_splits_is_refused(corpus):
    """
    Given  a pointer of a corpus without splits that names one
    When   it is resolved
    Then   it is refused, because the corpus has no second set of files
    """
    corpora_of_the_set, _ = corpus
    item = dict(invented_corpus.item_pointers()[5])
    item["pointer"] = {"text": "fr-01", "split": "dev"}

    with pytest.raises(ValueError, match="invented-fr has no splits"):
        testset.resolve(item, corpora_of_the_set)


# ── sentence spans and the sentences the reference leaves alone ──────────────


def test_the_sentences_of_a_german_text_are_its_lines(corpus):
    """
    Given  the German text of two lines, of which the reference changes the first
    When   it is resolved
    Then   it has one span per line, and only the unchanged line is listed as left alone
    """
    corpora_of_the_set, _ = corpus
    item = {
        "id": "de-03",
        "language": "de",
        "kind": "errors",
        "corpus": "falko-merlin",
        "pointer": {"origin": "falko", "text": "falko-1", "sentences": [0, 2]},
    }

    resolved = testset.resolve(item, corpora_of_the_set)

    assert len(resolved.sentence_spans) == 2
    assert resolved.unchanged_sentences == (1,)


def test_an_english_sentence_with_an_uncorrected_error_is_not_unchanged(corpus):
    """
    Given  an English essay whose second sentence carries an UNK annotation and whose third is
           plain correct
    When   it is resolved
    Then   only the third sentence is listed as left alone, because the annotator saw an error in
           the second one
    """
    corpora_of_the_set, _ = corpus
    item = {
        "id": "en-02",
        "language": "en",
        "kind": "errors",
        "corpus": "wi",
        "pointer": {"level": "A", "document": "a-2"},
    }

    resolved = testset.resolve(item, corpora_of_the_set)

    assert len(resolved.sentence_spans) == 3
    assert resolved.unchanged_sentences == (2,)


@pytest.mark.parametrize("item_id", ["de-01", "de-02", "en-01"])
def test_the_sentence_spans_cover_every_word_once(corpus, item_id):
    """
    Given  an item of the invented corpus that has a segmentation
    When   its sentence spans are read
    Then   they are ordered and disjoint, and every non-whitespace token lies in exactly one span
    """
    corpora_of_the_set, _ = corpus
    pointer = next(p for p in invented_corpus.item_pointers() if p["id"] == item_id)
    resolved = testset.resolve(pointer, corpora_of_the_set)
    tokens = tokenize(resolved.source)

    assert resolved.sentence_spans
    previous = 0
    for start, end in resolved.sentence_spans:
        assert previous <= start < end <= len(tokens)
        previous = end
    covered = [
        position
        for start, end in resolved.sentence_spans
        for position in range(start, end)
        if tokens[position].strip()
    ]
    assert sorted(covered) == [index for index, token in enumerate(tokens) if token.strip()]


def test_a_sentence_without_tokens_is_skipped_in_spans_and_flags_alike():
    """
    Given  an essay block whose middle M2 sentence has no tokens
    When   the character spans and the unchanged flags are made
    Then   both skip that sentence, so they have the same length and the flags stay aligned
    """
    sentences = [M2Sentence(("A",), ()), M2Sentence((), ()), M2Sentence(("B",), (), True)]
    block = (0, [((0, 1),), (), ((2, 3),)])

    assert _sentence_chars(block) == [(0, 1), (2, 3)]
    assert _corpus_unchanged(sentences, block) == [True, False]


def test_a_span_that_cuts_a_token_is_refused():
    """
    Given  a character span that ends in the middle of a word
    When   it is mapped to token indices
    Then   it is refused, because the manifest's positions are token positions
    """
    with pytest.raises(ValueError, match="does not lie on token boundaries"):
        token_spans("Der Hund läuft.", [(0, 5)])


# ── the recipes ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("testset_id", recipes.names())
def test_every_recipe_names_its_set_its_seed_and_known_corpora(testset_id):
    """
    Given  each registered recipe
    When   its declarations are read
    Then   the module is named after its test-set id, its seed is an integer, and every corpus it
           names is registered
    """
    recipe = recipes.recipe(testset_id)

    assert isinstance(recipe, recipes.Recipe)
    assert recipe.testset == testset_id
    assert isinstance(recipe.seed, int)
    assert recipe.corpora
    assert set(recipe.corpora) <= set(corpora.names())
    assert recipe.select.__module__.rsplit(".", 1)[-1] == testset_id.replace("-", "_")


def test_an_unknown_test_set_is_refused_with_the_known_ones():
    """
    Given  a test-set id no recipe has
    When   its recipe is asked for
    Then   it is refused with the ids that exist
    """
    with pytest.raises(ValueError, match="no test set 'gec-1999-01'; known: gec-2026-12"):
        recipes.recipe("gec-1999-01")


# ── the corpus options of the command lines ──────────────────────────────────


def test_a_corpus_option_without_an_equals_sign_is_refused():
    """
    Given  a --corpus value that is not NAME=PATH
    When   the corpus paths are read
    Then   it is refused with the form the option expects
    """
    with pytest.raises(ValueError, match="is not NAME=PATH"):
        testset.corpus_paths(["falko-merlin"], None)


def test_an_unknown_corpus_name_is_refused_with_the_known_names():
    """
    Given  a --corpus value that names no corpus
    When   the corpus paths are read
    Then   it is refused with the names that exist
    """
    with pytest.raises(ValueError, match="no corpus 'nope'; known: falko-merlin"):
        testset.corpus_paths(["nope=/tmp"], None)


def test_a_corpus_named_twice_is_refused():
    """
    Given  the same corpus named by two --corpus options
    When   the corpus paths are read
    Then   it is refused, so a second path cannot silently win
    """
    with pytest.raises(ValueError, match="wi: named twice"):
        testset.corpus_paths(["wi=/tmp/a", "wi=/tmp/b"], None)


def test_a_corpora_file_names_the_corpora_and_an_option_overrides_it(tmp_path):
    """
    Given  a --corpora file with a relative path and a --corpus option for the same corpus
    When   the corpus paths are read
    Then   the relative path is relative to the file, and the option wins
    """
    (tmp_path / "fm").mkdir()
    file = tmp_path / "corpora.json"
    file.write_text(json.dumps({"falko-merlin": "fm", "wi": "bea/wi+locness"}), encoding="utf-8")

    found = testset.corpus_paths(["wi=/elsewhere"], file)

    assert found["falko-merlin"] == (tmp_path / "fm").resolve()
    assert found["wi"] == Path("/elsewhere")


def test_a_corpora_file_that_is_not_an_object_of_strings_is_refused(tmp_path):
    """
    Given  a --corpora file that holds a list
    When   the corpus paths are read
    Then   it is refused with the shape the file needs
    """
    file = tmp_path / "corpora.json"
    file.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="not a JSON object of name: path strings"):
        testset.corpus_paths([], file)


# ── pointers that do not fit are problems, not tracebacks ────────────────────


@pytest.mark.parametrize("index", [999, -1])
def test_a_correct_pointer_outside_the_text_does_not_resolve(corpus, index):
    """
    Given  a German "correct" item whose sentence index is past the end or negative
    When   the item is resolved and the manifest is checked
    Then   resolving raises ValueError and the check reports the pointer, with no traceback
    """
    found, pinned = corpus
    entry = next(item for item in pinned["items"] if item["id"] == "de-correct")
    entry["pointer"] = [{**entry["pointer"], "index": index}]

    with pytest.raises(ValueError, match="no sentence"):
        testset.resolve(entry, found)
    assert any("does not resolve" in problem for problem in testset.check_items(pinned, found))


def test_an_item_without_a_pointer_or_with_a_non_object_pointer_is_refused(corpus):
    """
    Given  items whose pointer list is empty, or holds something that is not an object
    When   they are resolved
    Then   each raises ValueError
    """
    found, pinned = corpus
    entry = next(item for item in pinned["items"] if item["id"] == "de-correct")

    for pointer in ([], ["falko-1"]):
        with pytest.raises(ValueError, match="pointer"):
            testset.resolve({**entry, "pointer": pointer}, found)


def test_files_refuses_an_unknown_split_and_none_means_every_split(corpus):
    """
    Given  a corpus with splits
    When   its files are asked for an unknown split, an empty selection, and no selection
    Then   the unknown split raises, the empty selection is empty, None gives every split
    """
    found, _ = corpus
    adapter = found.adapter("falko-merlin")

    with pytest.raises(ValueError, match="no split"):
        adapter.files({"dev2"})
    assert not adapter.files(set())
    assert adapter.files(None) == adapter.files()


def test_an_adapter_without_a_declared_flag_fails_at_discovery(monkeypatch):
    """
    Given  an adapter module that forgot RESTRICTED
    When   the adapters are discovered
    Then   discovery raises RuntimeError naming the missing attribute
    """
    from tools.model_eval.corpora import falko_merlin  # noqa: PLC0415

    class Forgetful(falko_merlin.FalkoMerlin):
        """A Falko-MERLIN adapter that has lost its RESTRICTED flag."""

    monkeypatch.setattr(falko_merlin, "ADAPTER", Forgetful)
    monkeypatch.delattr(falko_merlin.FalkoMerlin, "RESTRICTED")
    monkeypatch.setattr(corpora, "_ADAPTERS", None)

    with pytest.raises(RuntimeError, match="no RESTRICTED"):
        corpora.adapters()


def test_the_redaction_key_comes_from_the_corpus_files(tmp_path):
    """
    Given  a corpus adapter over two directories that differ in one file
    When   their redaction keys are asked for
    Then   the key is stable for one corpus and differs for the other, so only someone who holds the
           corpus can recompute a keyed hash
    """
    from tools.model_eval.corpora.locness import Locness  # noqa: PLC0415

    keys = []
    for name, text in (("one", "{}"), ("two", '{"a": 1}')):
        root = tmp_path / name
        (root / "json").mkdir(parents=True)
        (root / "json" / "N.dev.json").write_text(text, encoding="utf-8")
        adapter = Locness(root)
        keys.append(adapter.redaction_key())
        assert adapter.redaction_key() == keys[-1]

    assert keys[0] != keys[1]
    assert len(keys[0]) == 32
