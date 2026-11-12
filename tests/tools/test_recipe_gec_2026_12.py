"""Tests of the recipe of ``gec-2026-12``: the bands, the slots and the filters.

The corpora are not part of the repository, so the tests build their own: ``band_corpus`` writes a
Falko-MERLIN test split and a W&I dev split with invented texts of every band, one candidate more
per slot than the recipe needs, and the texts a filter must keep out.

The last four tests read the committed manifest instead, without the corpora: they pin the shape of
the set that the model screen runs on.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.tools import band_corpus
from tools import prepare_gec_testset
from tools.model_eval import measure, protocol, recipes, results, testset
from tools.model_eval.recipes import gec_2026_12

_COMMITTED = Path(__file__).resolve().parents[2] / "evaluations" / "proofreading"
"""The repository directory the committed manifests live in."""

_CHILD = """
import json, sys
from pathlib import Path
from tools.model_eval import recipes, testset

corpora = testset.Corpora({"falko-merlin": Path(sys.argv[1]), "wi": Path(sys.argv[2])})
print(json.dumps(recipes.recipe("gec-2026-12").select(corpora), ensure_ascii=False))
"""


@pytest.fixture(scope="module")
def corpora(tmp_path_factory) -> testset.Corpora:
    """The band corpus, built once for the whole module."""
    return band_corpus.build_band_corpus(tmp_path_factory.mktemp("band"))


@pytest.fixture(scope="module")
def items(corpora) -> list[dict]:
    """The items the recipe selects from the band corpus."""
    return gec_2026_12.select(corpora)


@pytest.fixture(scope="module")
def committed() -> dict:
    """The committed manifest of ``gec-2026-12``."""
    path = _COMMITTED / "testsets" / "gec-2026-12" / "manifest.json"
    if not path.is_file():
        pytest.skip("the committed manifest of gec-2026-12 is not in the runfiles")
    return testset.read_manifest(path)


def _texts(items: list[dict], language: str) -> list[dict]:
    """Return the items of *language* that carry a band."""
    return [item for item in items if item["language"] == language and item.get("band")]


def _resolved(items: list[dict], corpora) -> list[tuple[dict, testset.ResolvedItem]]:
    """Return every German and English item with what it resolves to."""
    return [
        (item, testset.resolve(item, corpora)) for item in items if item["language"] in {"de", "en"}
    ]


def test_every_slot_is_filled_from_the_default_splits(items, corpora):
    """
    Given  the band corpus
    When   the recipe selects the texts
    Then   each language has 4 short, 5 page and 2 pages texts, from the origins and levels of
           SLOTS, no pointer names a split, and every German pointer covers its whole text
    """
    for language in ("de", "en"):
        bands = [item["band"] for item in _texts(items, language)]
        assert bands.count("short") == 4
        assert bands.count("page") == 5
        assert bands.count("pages") == 2
    german = _texts(items, "de")
    assert {item["pointer"]["origin"] for item in german if item["band"] == "short"} == {"merlin"}
    assert {item["pointer"]["origin"] for item in german if item["band"] == "page"} == {
        "falko",
        "merlin",
    }
    english = _texts(items, "en")
    assert {item["pointer"]["level"] for item in english if item["band"] == "short"} == {"A"}
    assert {item["pointer"]["level"] for item in english if item["band"] == "page"} == {"B", "C"}
    for item in items:
        assert "split" not in item["pointer"]
    for item, resolved in _resolved(items, corpora):
        if item["language"] == "de":
            start, end = item["pointer"]["sentences"]
            assert (start, end) == (0, len(resolved.sentence_spans))


def test_the_ids_run_in_band_order_and_by_length(items, corpora):
    """
    Given  the selected items
    When   the German ids are read in order
    Then   they run de-01 to de-11 through short, page and pages, with the words ascending
    """
    german = [item for item in items if item["language"] == "de"]
    assert [item["id"] for item in german] == [f"de-{number:02d}" for number in range(1, 12)]
    order = [list(gec_2026_12.BANDS).index(item["band"]) for item in german]
    assert order == sorted(order)
    words = [testset.words(testset.resolve(item, corpora).source) for item in german]
    for band in gec_2026_12.BANDS:
        within = [count for count, item in zip(words, german, strict=True) if item["band"] == band]
        assert within == sorted(within)


def test_the_pages_band_has_one_text_the_app_splits_and_one_it_sends_whole(items, corpora):
    """
    Given  the two "pages" texts of each language
    When   their lengths are read
    Then   one is at least SPLIT_CHARS long and the other at most ONE_REQUEST_CHARS, so the app
           splits one and sends the other as one long request
    """
    for language in ("de", "en"):
        lengths = sorted(
            len(testset.resolve(item, corpora).source)
            for item in _texts(items, language)
            if item["band"] == "pages"
        )
        assert lengths[0] <= gec_2026_12.ONE_REQUEST_CHARS
        assert lengths[1] >= gec_2026_12.SPLIT_CHARS


def test_a_text_that_fails_a_filter_is_never_chosen(items):
    """
    Given  the texts with too few edits, too many per 100 words, a length outside every band or
           no labels
    When   the recipe selects the texts
    Then   none of them is chosen
    """
    chosen = {item["pointer"].get("text") or item["pointer"].get("document") for item in items}

    assert chosen.isdisjoint(band_corpus.NEVER_CHOSEN)


def test_a_slot_without_enough_candidates_is_refused_with_its_name(tmp_path):
    """
    Given  a corpus with two of the four Falko page texts left out
    When   the recipe selects the texts
    Then   it raises ValueError naming the slot and the count it found
    """
    corpora = band_corpus.build_band_corpus(tmp_path, leave_out=("falko-page-1", "falko-page-2"))

    with pytest.raises(ValueError, match="de page falko any: 2 candidates for 3"):
        gec_2026_12.select(corpora)


def test_the_builder_refuses_a_set_the_corpora_cannot_fill(tmp_path, capsys, monkeypatch):
    """
    Given  the same corpus with two Falko page texts left out
    When   the builder runs for gec-2026-12
    Then   it exits with 2 and says the corpora do not fit the test set
    """
    corpora = band_corpus.build_band_corpus(
        tmp_path / "corpora", leave_out=("falko-page-1", "falko-page-2")
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_gec_testset",
            "--testset",
            "gec-2026-12",
            "--corpus",
            f"falko-merlin={corpora.root('falko-merlin')}",
            "--corpus",
            f"wi={corpora.root('wi')}",
            "--out",
            str(tmp_path / "set"),
        ],
    )

    assert prepare_gec_testset.main() == 2

    err = capsys.readouterr().err
    assert "the corpora do not fit the test set 'gec-2026-12'" in err
    assert "de page falko any: 2 candidates for 3" in err


def test_the_selection_does_not_depend_on_the_hash_seed(corpora):
    """
    Given  the band corpus
    When   the recipe selects the texts in two processes with PYTHONHASHSEED 1 and 2
    Then   both print the same items, so a committed manifest can be rebuilt anywhere
    """
    outputs = []
    for seed in ("1", "2"):
        environment = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=os.pathsep.join(sys.path))
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                _CHILD,
                str(corpora.root("falko-merlin")),
                str(corpora.root("wi")),
            ],
            capture_output=True,
            text=True,
            env=environment,
            check=True,
        )
        outputs.append(json.loads(result.stdout))

    assert outputs[0] == outputs[1]


def test_the_selection_depends_on_the_seed(corpora, monkeypatch):
    """
    Given  the band corpus and another seed
    When   the recipe selects the texts
    Then   at least one chosen text differs, so the seed still means what it says
    """
    before = gec_2026_12.select(corpora)

    monkeypatch.setattr(gec_2026_12, "SEED", gec_2026_12.SEED + 1)
    after = gec_2026_12.select(corpora)

    assert [item["pointer"] for item in before] != [item["pointer"] for item in after]


def test_the_french_items_are_the_invented_set_without_a_band(items):
    """
    Given  the selected items
    When   the French ones are read
    Then   they are the invented set in file order, with the ids and pointers the retired
           gec-2026-11 had, and no band
    """
    french = [item for item in items if item["language"] == "fr"]

    assert [item["id"] for item in french] == [
        "fr-01",
        "fr-02",
        "fr-03",
        "fr-04",
        "fr-05",
        "fr-06",
        "fr-07",
        "fr-08",
        "fr-correct",
    ]
    assert all("band" not in item for item in french)
    assert [item["pointer"] for item in french] == [{"text": item["id"]} for item in french]
    assert {item["corpus"] for item in french} == {"invented-fr"}


def test_a_build_of_the_new_set_records_bands_and_sentences(tmp_path, capsys, monkeypatch):
    """
    Given  the band corpus and a clean tool revision
    When   the builder builds gec-2026-12
    Then   it exits with 0, the records carry the band and the spans, the manifest carries the
           band, the characters and the spans, and both checks are empty
    """
    corpora = band_corpus.build_band_corpus(tmp_path / "corpora")
    monkeypatch.setattr(measure, "tool_revision", lambda: "abc1234")
    out = tmp_path / "set"
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_gec_testset",
            "--testset",
            "gec-2026-12",
            "--corpus",
            f"falko-merlin={corpora.root('falko-merlin')}",
            "--corpus",
            f"wi={corpora.root('wi')}",
            "--out",
            str(out),
            "--manifest",
            str(manifest_path),
            "--built",
            "2026-12-01",
        ],
    )

    assert prepare_gec_testset.main() == 0

    records = results.read_records(out / "de.jsonl") + results.read_records(out / "en.jsonl")
    assert all(record.band for record in records)
    assert all(record.sentence_spans for record in records)
    manifest = testset.read_manifest(manifest_path)
    for entry in manifest["items"]:
        if entry["language"] in {"de", "en"}:
            assert entry["band"]
            assert entry["source"]["chars"]
            assert entry["sentence_spans"]
    assert testset.corpus_problems(manifest, corpora) == []
    assert testset.check_items(manifest, corpora) == []
    assert "short 4, page 5, pages 2" in capsys.readouterr().out


def test_the_committed_new_set_has_four_short_five_page_and_two_pages_texts_per_language(committed):
    """
    Given  the committed manifest of gec-2026-12
    When   its items are counted per language and band
    Then   German and English have 4 short, 5 page and 2 pages texts, and French has its nine
           items without a band
    """
    for language in ("de", "en"):
        bands = [item["band"] for item in committed["items"] if item["language"] == language]
        assert bands.count("short") == 4
        assert bands.count("page") == 5
        assert bands.count("pages") == 2
        assert len(bands) == 11
    french = [item for item in committed["items"] if item["language"] == "fr"]

    assert len(french) == 9
    assert all("band" not in item for item in french)


def test_the_committed_new_set_records_the_sentences_of_every_text(committed):
    """
    Given  the committed manifest of gec-2026-12
    When   the German and English items are read
    Then   each has a band, its characters and the spans of its sentences, the spans are ordered
           and disjoint, and every unchanged index points at one of them
    """
    for item in committed["items"]:
        if item["language"] not in {"de", "en"}:
            continue
        assert item["band"] in gec_2026_12.BANDS
        assert item["source"]["chars"] > 0
        spans = item["sentence_spans"]
        assert spans
        assert spans[0][0] == 0
        assert all(start < end for start, end in spans)
        assert all(end <= start for (_, end), (start, _) in itertools.pairwise(spans))
        assert all(0 <= index < len(spans) for index in item["unchanged_sentences"])


def test_every_pages_text_is_split_at_window_8192_and_a_page_text_is_not(committed):
    """
    Given  the committed manifest of gec-2026-12 and the budget of the current eval version
    When   the characters of the texts of each band are compared with it
    Then   every "pages" text is above the budget, so the set takes the app through its
           long-text path, and at least one "page" text is not, so it still has one-request texts
    """
    # The plain profile is the one the app asks with until a gate-1 run decides otherwise.
    budgets = protocol.fingerprint()["text_budget"]["review"]
    for language in ("de", "en"):
        lengths = {
            band: [
                item["source"]["chars"]
                for item in committed["items"]
                if item["language"] == language and item["band"] == band
            ]
            for band in ("page", "pages")
        }
        budget = budgets[language]["plain"]
        assert min(lengths["pages"]) > budget, (
            f"the {language} pages texts are {lengths['pages']} characters and the budget of "
            f"this eval version is {budget}: the set no longer takes the app through "
            "the long-text path, so a new test set is needed"
        )
        assert min(lengths["page"]) <= budget, (
            f"every {language} page text is {lengths['page']} characters and the budget of this "
            f"eval version is {budget}: the set has no one-request page text left"
        )


def test_the_committed_new_set_reads_the_default_splits_only(committed):
    """
    Given  the committed manifest of gec-2026-12
    When   its pointers and the corpus files it pins are read
    Then   no pointer names a split, the files are those of the Falko-MERLIN test split and the
           W&I dev split, and LOCNESS is not pinned at all
    """
    assert all("split" not in item["pointer"] for item in committed["items"])
    assert set(committed["corpora"]) == {"falko-merlin", "wi", "invented-fr"}
    german = committed["corpora"]["falko-merlin"]["files"]
    assert all(name == "fm-test.m2" or name.endswith(".test") for name in german)
    english = committed["corpora"]["wi"]["files"]
    assert all(".dev." in name for name in english)
