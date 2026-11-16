"""Tests of the recipe of ``gec-2026-12-heldout``: the splits it reads, its slots and its levels.

The corpora are not part of the repository, so the tests build their own: ``band_corpus`` writes a
Falko-MERLIN ``dev`` split and a W&I ``train`` split, the two this recipe reads, with invented texts
of every band.  The last tests read the committed manifest instead, without the corpora.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest

from tests.tools import band_corpus
from tools import prepare_gec_testset
from tools.model_eval import measure, protocol, recipes, results, testset
from tools.model_eval.corpora._bea import cefr_level
from tools.model_eval.recipes import gec_2026_12, gec_2026_12_heldout

_DRAW_SHA256 = "857c1d88b7b8a239151114ed5fc1879c5cd1aed227c5c1c0bc62e21b306d6811"
"""The sha256 of the items this recipe draws from the band corpus (sorted-key JSON)."""

_COMMITTED = Path(__file__).resolve().parents[2] / "evaluations" / "proofreading"
"""The repository directory the committed manifests live in."""


@pytest.fixture(scope="module")
def corpora(tmp_path_factory) -> testset.Corpora:
    """The band corpus with the splits of the held-out set, built once for the module."""
    return band_corpus.build_band_corpus(
        tmp_path_factory.mktemp("heldout"), german_split="dev", english_split="train"
    )


@pytest.fixture(scope="module")
def items(corpora) -> list[dict]:
    """The items the recipe selects from the band corpus."""
    return gec_2026_12_heldout.select(corpora)


@pytest.fixture(scope="module")
def committed() -> dict:
    """The committed manifest of ``gec-2026-12-heldout``."""
    path = _COMMITTED / "testsets" / "gec-2026-12-heldout" / "manifest.json"
    if not path.is_file():
        pytest.skip("the committed manifest of gec-2026-12-heldout is not in the runfiles")
    return testset.read_manifest(path)


def _texts(items: list[dict], language: str) -> list[dict]:
    """Return the items of *language*."""
    return [item for item in items if item["language"] == language]


def test_the_recipe_is_found_by_its_id():
    """
    Given  the recipe modules
    When   the held-out set's id is looked up
    Then   its recipe is found, next to the one of gec-2026-12, with its own seed
    """
    assert recipes.recipe("gec-2026-12-heldout") is gec_2026_12_heldout.RECIPE
    assert gec_2026_12_heldout.SEED != gec_2026_12.SEED
    assert "gec-2026-12" in recipes.names()


def test_every_slot_is_filled_from_splits_the_first_set_never_reads(items):
    """
    Given  the band corpus with a German dev and an English train split
    When   the recipe selects the texts
    Then   each language has 4 short, 5 page and 2 pages texts, every pointer names dev (German) or
           train (English), and no French or other text is part of it
    """
    assert {item["language"] for item in items} == {"de", "en"}
    for language in ("de", "en"):
        bands = [item["band"] for item in _texts(items, language)]
        assert (bands.count("short"), bands.count("page"), bands.count("pages")) == (4, 5, 2)
    assert {item["pointer"]["split"] for item in _texts(items, "de")} == {"dev"}
    assert {item["pointer"]["split"] for item in _texts(items, "en")} == {"train"}


def test_the_draw_is_pinned(items):
    """
    Given  the band corpus and the items the recipe draws from it
    When   the items are hashed
    Then   the hash is the one written down, so a change of the shared code in ``_bands`` cannot
           move what this frozen recipe selects without a test failing
    """
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()

    assert digest == _DRAW_SHA256, (
        "gec-2026-12-heldout now selects other texts from the band corpus; its committed "
        "manifest would no longer be what the recipe builds"
    )


def test_the_recipe_owns_its_numbers():
    """
    Given  the rules of the held-out recipe and those of gec-2026-12
    When   they are compared
    Then   they hold the same numbers but are two objects, so changing one cannot change the other
    """
    assert gec_2026_12_heldout.RULES == gec_2026_12.RULES
    assert gec_2026_12_heldout.RULES is not gec_2026_12.RULES
    assert gec_2026_12_heldout.RULES.bands is not gec_2026_12.RULES.bands


def test_both_pages_texts_are_within_the_one_request_cap(items, corpora):
    """
    Given  the two "pages" texts of each language
    When   their lengths are read
    Then   both are at most ONE_REQUEST_CHARS long: the cap of the slot, which the first set's
           rule keeps, though no longer a promise about what the app does with the text
    """
    for language in ("de", "en"):
        for item in _texts(items, language):
            if item["band"] == "pages":
                length = len(testset.resolve(item, corpora).source)
                assert length <= gec_2026_12_heldout.RULES.one_request_chars


def test_an_english_item_records_its_cefr_level_and_a_german_one_none(items, corpora):
    """
    Given  W&I essays whose cefr field is "B1.ii" and Falko-MERLIN texts that carry no level
    When   the items are selected and resolved
    Then   an English item holds the level without the sub-level, equal to what its essay resolves
           to, and a German item has no level
    """
    for item in _texts(items, "en"):
        assert item["level"] == f"{item['pointer']['level']}1"
        assert testset.resolve(item, corpora).level == item["level"]
    assert all("level" not in item for item in _texts(items, "de"))


def test_the_first_set_records_no_level(tmp_path):
    """
    Given  the band corpus of gec-2026-12 (a test and a dev split)
    When   its recipe selects the texts
    Then   no item carries a level, so its committed manifest stays what it was
    """
    corpora = band_corpus.build_band_corpus(tmp_path)
    selected = gec_2026_12.select(corpora)

    assert all("level" not in item for item in selected)
    assert all("split" not in item["pointer"] for item in selected if item["language"] != "fr")


def test_the_selection_does_not_change_between_two_draws(items, corpora):
    """
    Given  the recipe and one corpus
    When   the texts are selected a second time
    Then   the items are identical, because the seed is part of each slot's generator
    """
    assert gec_2026_12_heldout.select(corpora) == items


def test_a_text_that_fails_a_filter_is_never_chosen(items):
    """
    Given  the texts with too few edits, too many per 100 words, a length outside every band or
           no labels
    When   the recipe selects the texts
    Then   none of them is among the chosen
    """
    chosen = {item["pointer"].get("text") or item["pointer"]["document"] for item in items}

    assert chosen.isdisjoint(band_corpus.NEVER_CHOSEN)


def test_a_missing_split_is_refused_not_guessed(tmp_path):
    """
    Given  a corpus that holds the German dev split but only the English dev split
    When   the held-out recipe is asked for its German dev and English train texts
    Then   it refuses with the English train file it did not find, and does not fall back to dev
    """
    corpora = band_corpus.build_band_corpus(tmp_path, german_split="dev", english_split="dev")

    with pytest.raises(FileNotFoundError, match=r"A\.train\.json"):
        gec_2026_12_heldout.select(corpora)


def test_the_committed_manifest_has_the_shape_of_the_first_set(committed):
    """
    Given  the committed manifest of gec-2026-12-heldout
    When   its items are counted
    Then   it holds 11 German and 11 English texts in the bands 4 / 5 / 2, and no French
    """
    assert committed["testset"] == "gec-2026-12-heldout"
    assert committed["selection_seed"] == gec_2026_12_heldout.SEED
    for language in ("de", "en"):
        bands = [i["band"] for i in committed["items"] if i["language"] == language]
        assert (bands.count("short"), bands.count("page"), bands.count("pages")) == (4, 5, 2)
    assert {i["language"] for i in committed["items"]} == {"de", "en"}


def test_the_committed_manifest_pins_no_file_of_the_first_set(committed):
    """
    Given  the committed manifests of both sets
    When   the corpus files each one pins are compared
    Then   the held-out set pins dev and train files only, and none the first set pins
    """
    first = testset.read_manifest(_COMMITTED / "testsets" / "gec-2026-12" / "manifest.json")
    held = {key for record in committed["corpora"].values() for key in record["files"]}
    used = {key for record in first["corpora"].values() for key in record["files"]}

    assert held
    assert held.isdisjoint(used)
    assert all(("dev" in key or "train" in key) and "test" not in key for key in held)


def test_the_committed_manifest_records_the_english_levels(committed):
    """
    Given  the committed manifest
    When   the English items are read
    Then   each one has a CEFR level of A1 to C2, and the German ones have none
    """
    for item in committed["items"]:
        if item["language"] == "en":
            assert item["level"] in {"A1", "A2", "B1", "B2", "C1", "C2"}
        else:
            assert "level" not in item


@pytest.mark.parametrize(
    ("cefr", "level"),
    [("B1.ii", "B1"), ("C2", "C2"), ("A2.i", "A2"), ("A", ""), ("", ""), ("B12", ""), ("N", "")],
)
def test_the_level_is_the_cefr_level_without_the_sub_level(cefr, level):
    """
    Given  a cefr field as W&I writes it
    When   the level is read
    Then   it is the level without the sub-level, or empty when the field names no CEFR level
    """
    assert cefr_level(cefr) == level


def test_a_record_writes_its_level_only_when_it_has_one():
    """
    Given  a test record with a level and one without
    When   both are written and read back
    Then   the first keeps the level, and the second writes the line it wrote before levels existed
    """
    plain = results.TestRecord(
        id="de-01", language="de", kind="errors", origin="o", source="a", reference="a"
    )
    leveled = results.TestRecord(
        id="en-01", language="en", kind="errors", origin="o", source="a", reference="a", level="B1"
    )

    assert "level" not in plain.to_dict()
    assert results.TestRecord.from_dict(leveled.to_dict()).level == "B1"
    assert results.TestRecord.from_dict(plain.to_dict()) == plain


def test_every_pages_text_is_split_at_the_current_budget_and_a_page_text_is_not(committed):
    """
    Given  the committed manifest of gec-2026-12-heldout and the budget of the current eval version
    When   the characters of the texts of each band are compared with it
    Then   every "pages" text is above the budget, so the set takes the app through its long-text
           path, and at least one "page" text is not, so it still has one-request texts
    """
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
            f"this eval version is {budget}: the set no longer takes the app through the "
            "long-text path"
        )
        assert min(lengths["page"]) <= budget, (
            f"every {language} page text is {lengths['page']} characters and the budget of this "
            f"eval version is {budget}: the set has no one-request page text left"
        )


# ── the level in the manifest and the records ────────────────────────────────


def _manifest(items: list[dict], corpora: testset.Corpora) -> dict:
    """Return the manifest of *items*, built the way the builder builds it."""
    return testset.build_manifest(
        items,
        corpora,
        testset="gec-2026-12-heldout",
        built="2026-12-01",
        selection_seed=gec_2026_12_heldout.SEED,
        builder_revision="abc1234",
    )


def test_the_manifest_holds_the_level_the_corpus_gives(items, corpora):
    """
    Given  the items of the held-out recipe, whose English items carry a level
    When   the manifest is built
    Then   an English entry holds the level its essay resolves to, and a German entry none
    """
    manifest = _manifest(items, corpora)

    for entry in manifest["items"]:
        if entry["language"] == "en":
            assert entry["level"] == testset.resolve(entry, corpora).level != ""
        else:
            assert "level" not in entry


def test_a_changed_level_is_a_problem_of_the_check(items, corpora):
    """
    Given  a manifest that records another level for an English text than its essay has
    When   its items are checked
    Then   the one item is a problem that names both levels, and the unchanged manifest has none
    """
    manifest = _manifest(items, corpora)
    assert testset.check_items(manifest, corpora) == []
    entry = next(entry for entry in manifest["items"] if entry["language"] == "en")
    found = entry["level"]
    entry["level"] = "C2" if found != "C2" else "A1"

    problems = testset.check_items(manifest, corpora)

    assert problems == [f"{entry['id']}: the level is {found!r}, the manifest another"]


def test_the_record_level_is_the_one_of_the_manifest(items, corpora):
    """
    Given  the items of the recipe, a record built for each, and the manifest
    When   the level of each record is compared with that of its manifest entry
    Then   they are equal for every item, and a recipe that records no level leaves the record's
           level empty although the corpus gives one
    """
    manifest = {entry["id"]: entry for entry in _manifest(items, corpora)["items"]}

    for item in items:
        resolved = testset.resolve(item, corpora)
        record = testset.to_record(resolved, band=item["band"], with_level="level" in item)
        assert record.level == manifest[item["id"]].get("level", "")
    english = next(item for item in items if item["language"] == "en")
    plain = testset.to_record(testset.resolve(english, corpora), band=english["band"])
    assert plain.level == ""


def test_a_build_of_the_held_out_set_writes_the_level_into_manifest_and_records(
    tmp_path, capsys, monkeypatch
):
    """
    Given  the band corpus with a German dev and an English train split and a clean tool revision
    When   the builder builds gec-2026-12-heldout
    Then   it exits with 0, the English records and manifest entries carry the same level, the
           German ones carry none, and both checks are empty
    """
    corpora = band_corpus.build_band_corpus(
        tmp_path / "corpora", german_split="dev", english_split="train"
    )
    monkeypatch.setattr(measure, "tool_revision", lambda: "abc1234")
    out = tmp_path / "set"
    manifest_path = tmp_path / "manifest.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_gec_testset",
            "--testset",
            "gec-2026-12-heldout",
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

    assert "0 corpus problems, 0 item problems" in capsys.readouterr().out
    manifest = {entry["id"]: entry for entry in testset.read_manifest(manifest_path)["items"]}
    records = results.read_records(out / "de.jsonl") + results.read_records(out / "en.jsonl")
    assert len(records) == 22
    for record in records:
        assert record.level == manifest[record.id].get("level", "")
    assert all(record.level for record in records if record.language == "en")
    assert not any(record.level for record in records if record.language == "de")
