"""Tests of the viewer's model: the texts, the versions, their sentences and their numbers."""

from __future__ import annotations

import json

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval.viewer import load, model

TEXTS = fixtures.records()


def _model(tmp_path, *, outputs: dict[str, str] | None = None, repeats: int = 1):
    """Return the report of the fixture test set and one cache of *repeats* repeats."""
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    for repeat in range(1, repeats + 1):
        fixtures.write_cache(
            directory,
            f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r{repeat}",
            fixtures.header(repeat=repeat),
            [fixtures.row(text, output=(outputs or {}).get(text.id)) for text in TEXTS],
        )
    return model.build(testset, load.read_caches([directory], testset))


def test_a_text_has_one_row_per_sentence(tmp_path):
    """
    Given  a text of two sentences
    When   the report is built
    Then   the text has two rows, and their texts join to the student's text
    """
    built = _model(tmp_path)

    text = built.texts["de-01"]

    assert len(text.rows) == 2
    assert "".join(row.source.text for row in text.rows) == fixtures.SOURCE_ONE


def test_the_reference_row_is_the_reference_sentence(tmp_path):
    """
    Given  a text whose reference fixes one word
    When   the report is built
    Then   the rows' reference texts join to the reference, and the fix is marked
    """
    built = _model(tmp_path)

    text = built.texts["de-01"]

    assert "".join(row.reference.text for row in text.rows) == fixtures.REFERENCE_ONE
    marks = text.rows[0].reference.marks
    assert [text.rows[0].reference.text[mark.start : mark.end] for mark in marks] == ["einen"]


def test_a_reference_error_is_marked_in_the_source_row(tmp_path):
    """
    Given  a text whose reference fixes one word
    When   the report is built
    Then   the student's row marks the word the reference changes, not the new one
    """
    built = _model(tmp_path)

    row = built.texts["de-01"].rows[0]

    assert [row.source.text[mark.start : mark.end] for mark in row.source.marks] == ["ein"]
    assert row.source.marks[0].index == 0


def test_a_version_row_is_the_models_sentence(tmp_path):
    """
    Given  a version that answers a text with a text of its own
    When   the report is built
    Then   its rows join to that answer, and every mark covers the change it points at
    """
    answer = "Ich habe einen Hund. Er ist sehr nett und freundlich."
    built = _model(tmp_path, outputs={"de-01": answer})

    item = built.item("v1", 1, "de-01")

    assert item is not None
    assert "".join(row.text for row in item.rows) == answer
    for row in item.rows:
        for mark in row.marks:
            assert row.text[mark.start : mark.end] == item.edits[mark.index].text


def test_the_counts_of_a_version_add_up_to_the_stored_ones(tmp_path):
    """
    Given  a version that fixes one reference error and adds one of its own
    When   the report is built
    Then   its outcomes add up to the true and false positives the row stores
    """
    built = _model(tmp_path, outputs={"de-01": "Ich habe einen Hund. Er ist sehr lieb."})

    item = built.item("v1", 1, "de-01")

    assert item is not None
    counts = item.counts
    assert counts["exact"] + counts["located"] + counts["extra"] == counts["tp"] + counts["fp"]
    assert counts["fixed"] + counts["located_ref"] + counts["missed"] == counts["tp"] + counts["fn"]
    assert counts["exact"] == 1
    assert counts["extra"] == 1


def test_a_version_that_has_not_answered_a_text_has_no_item(tmp_path):
    """
    Given  a cache whose second repeat holds one text of the two
    When   the report is built
    Then   the missing version has no item, and the variant says how far it got
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    fixtures.write_cache(
        directory,
        f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1",
        fixtures.header(),
        [fixtures.row(text) for text in TEXTS],
    )
    fixtures.write_cache(
        directory,
        f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r2",
        fixtures.header(repeat=2),
        [fixtures.row(TEXTS[0])],
    )

    built = model.build(testset, load.read_caches([directory], testset))

    assert built.item("v1", 2, "de-02") is None
    assert built.variants[0].texts_done == {1: 2, 2: 1}
    assert not built.variants[0].complete


def test_the_numbers_of_a_variant_are_the_scorecard(tmp_path):
    """
    Given  a variant of one repeat
    When   the report is built
    Then   its numbers hold the row of the language and the row over all languages
    """
    built = _model(tmp_path)

    found = built.numbers["v1"]

    assert found.row("de") is not None
    assert found.row(model.numbers.DECIDING) is not None
    assert found.row("de").measures["recall"].values == (1.0,)


def test_the_footer_lists_the_cache_and_what_was_skipped(tmp_path):
    """
    Given  a cache with one run and one file that cannot be read
    When   the report is built
    Then   the footer names the directory, the files and the reason of the skip
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = fixtures.cache(tmp_path / "results")
    (directory / "broken__T1__exchange__r9.jsonl").write_text("", encoding="utf-8")

    built = model.build(testset, load.read_caches([directory], testset))

    assert len(built.inputs) == 1
    assert built.inputs[0].files == 1
    assert built.inputs[0].rows == 2
    assert [entry.file for entry in built.inputs[0].skipped] == ["broken__T1__exchange__r9.jsonl"]


def test_the_model_serialises_to_json(tmp_path):
    """
    Given  a report of one variant
    When   it is turned into the page's data
    Then   it is JSON, with the texts, the variants, the items and the numbers
    """
    built = _model(tmp_path)

    stored = json.loads(json.dumps(built.to_dict()))

    assert set(stored) == {
        "inputs",
        "texts",
        "variants",
        "items",
        "numbers",
        "errors",
        "eval_versions",
        "eval_notes",
        "prompts",
        "deciding",
        "beta",
    }
    assert stored["eval_versions"] == [2]
    assert stored["texts"]["de-01"]["rows"][0]["source"]["text"] == "Ich habe ein Hund. "
    assert stored["variants"][0]["label"] == fixtures.MODEL
    assert stored["items"]["v1/r1/de-01"]["counts"]["exact"] == 1
    assert stored["numbers"]["v1"]["rows"]["de/all"]["measures"]["recall"]["mean"] == 1.0
    errors = {entry["group"]: entry for entry in stored["errors"]}
    assert errors["DET"]["total"] == 1
    assert errors["DET"]["shares"]["v1"]["fixed"] == 1.0
    assert errors["DET"]["missed_by_all"] == {}
    assert errors["WO"]["total"] == 2


def test_a_row_a_version_left_alone_is_not_stored_again(tmp_path):
    """
    Given  a version that changes the first sentence of a text and not the second
    When   the report is serialised
    Then   the second row is null, which the page reads as the text's own sentence
    """
    built = _model(tmp_path)

    stored = built.to_dict()["items"]["v1/r1/de-01"]

    assert stored["rows"][0]["text"].startswith("Ich habe einen Hund.")
    assert stored["rows"][1] is None


def test_an_empty_explanation_is_left_out_of_the_data(tmp_path):
    """
    Given  a text-mode version, which has no explanations and no categories
    When   the report is serialised
    Then   its edits carry neither key, and the ones with a reference edit carry ref
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    fixtures.write_cache(
        directory,
        "run__T1__text__r1",
        fixtures.header(mode="text"),
        [
            fixtures.row(text, mode="text", explanations=[""] * 9, categories=[""] * 9)
            for text in TEXTS
        ],
    )
    built = model.build(testset, load.read_caches([directory], testset))

    edit = built.to_dict()["items"]["v1/r1/de-01"]["edits"][0]

    assert "explanation" not in edit
    assert "category" not in edit
    assert edit["ref"] == 0


def _two_variants(tmp_path, outputs_b: dict[str, str]):
    """Return a report of two variants of one model that differ in the temperature."""
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    for temperature, outputs in ((0.2, {}), (1.0, outputs_b)):
        fixtures.write_cache(
            directory,
            f"run__T{temperature:g}__exchange__r1",
            fixtures.header(temperature=temperature),
            [
                fixtures.row(text, temperature=temperature, output=outputs.get(text.id))
                for text in TEXTS
            ],
        )
    return model.build(testset, load.read_caches([directory], testset))


def test_an_error_is_missed_by_all_only_when_every_version_missed_it(tmp_path):
    """
    Given  two variants, the first fixing every error and the second fixing none
    When   the error table is read
    Then   no error is missed by all, because one variant fixed it
    """
    built = _two_variants(tmp_path, {"de-01": fixtures.SOURCE_ONE, "de-02": fixtures.SOURCE_TWO})

    errors = {entry.group: entry for entry in built.errors}

    assert all(not entry.missed_by_all for entry in errors.values())
    assert errors["DET"].shares["v1"].fixed == 1.0
    assert errors["DET"].shares["v2"].fixed == 0.0
    assert errors["DET"].shares["v2"].missed == 1.0


def test_an_error_no_variant_fixed_is_missed_by_all(tmp_path):
    """
    Given  two variants that both leave the texts as they were
    When   the error table is read
    Then   every reference error is missed by all, once each, of the eval version
    """
    both = {"de-01": fixtures.SOURCE_ONE, "de-02": fixtures.SOURCE_TWO}
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    for temperature in (0.2, 1.0):
        fixtures.write_cache(
            directory,
            f"run__T{temperature:g}__exchange__r1",
            fixtures.header(temperature=temperature),
            [fixtures.row(text, temperature=temperature, output=both[text.id]) for text in TEXTS],
        )
    built = model.build(testset, load.read_caches([directory], testset))

    errors = {entry.group: entry for entry in built.errors}

    assert errors["DET"].missed_by_all == {"2": 1}
    assert errors["WO"].missed_by_all == {"2": 2}
    assert all(entry.total >= entry.missed_by_all.get("2", 0) for entry in errors.values())


def test_the_shares_of_an_error_are_the_mean_of_the_repeats_not_a_sum(tmp_path):
    """
    Given  one variant of two repeats, the first fixing the error and the second not
    When   the error table is read
    Then   the share fixed is one half, however many repeats there are
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    for repeat, output in ((1, None), (2, fixtures.SOURCE_ONE)):
        fixtures.write_cache(
            directory,
            f"run__T1__exchange__r{repeat}",
            fixtures.header(repeat=repeat),
            [fixtures.row(text, output=output if text.id == "de-01" else None) for text in TEXTS],
        )
    built = model.build(testset, load.read_caches([directory], testset))

    errors = {entry.group: entry for entry in built.errors}

    assert errors["DET"].shares["v1"].fixed == 0.5
    assert errors["DET"].shares["v1"].repeats == 2


def test_two_eval_versions_are_listed_for_the_page_to_split_its_tables(tmp_path):
    """
    Given  two runs of one model measured under eval version 1 and 2
    When   the report is built
    Then   the variants name their eval version and the model lists both
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = tmp_path / "results"
    for version, mode in ((1, "text"), (2, "exchange")):
        fixtures.write_cache(
            directory,
            f"run__{mode}__r1",
            fixtures.header(eval_version=version, mode=mode),
            [fixtures.row(text, mode=mode) for text in TEXTS],
        )

    built = model.build(testset, load.read_caches([directory], testset)).to_dict()

    assert built["eval_versions"] == [1, 2]
    assert sorted(variant["eval_version"] for variant in built["variants"]) == [1, 2]


def test_a_doubled_run_is_listed_as_skipped_in_its_cache(tmp_path):
    """
    Given  one run that two caches both hold
    When   the report is built
    Then   one of them lists it as skipped with the reason, and the variant has one repeat
    """
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    one = fixtures.cache(tmp_path / "one")
    two = fixtures.cache(tmp_path / "two")

    built = model.build(testset, load.read_caches([one, two], testset))

    assert built.variants[0].repeats == (1,)
    skipped = [entry for cache in built.inputs for entry in cache.skipped]
    assert len(skipped) == 1
    assert "the same run" in skipped[0].reason


def test_a_reference_edit_across_a_sentence_boundary_does_not_need_a_model_edit(tmp_path):
    """
    Given  a text whose reference merges two paragraphs, and a model that changed nothing
    When   the report is built
    Then   the two sentences are one row and the reference edit lies inside it
    """
    from teachers_teammate.domain.correction_review import tokenize  # noqa: PLC0415
    from tools.model_eval import quality  # noqa: PLC0415

    source = "I like it.\n\nBut you not"
    reference = "I like it, but you do not"
    tokens = tokenize(source)
    stop = tokens.index(".") + 1
    count = len(quality.ordered_edits(source, reference))
    text = fixtures.record(
        item="de-03",
        source=source,
        reference=reference,
        codes=("R:PUNCT",) * count,
        spans=((0, stop), (stop, len(tokens))),
        unchanged=(),
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET, (text,)))
    header = fixtures.header()
    header["testset_sha256"] = testset.sha256
    directory = tmp_path / "results"
    fixtures.write_cache(
        directory, "one__T1__exchange__r1", header, [fixtures.row(text, output=source)]
    )

    built = model.build(testset, load.read_caches([directory], testset))

    built_text = built.texts["de-03"]
    assert len(built_text.rows) == 1
    assert all(edit.row == 0 for edit in built_text.ref_edits)


def test_the_data_names_what_each_eval_version_is(tmp_path):
    """
    Given  a report of runs under the current eval version
    When   it is turned into the page's data
    Then   the data carries the protocol file's date and reason for that version
    """
    built = _model(tmp_path)
    version = str(built.to_dict()["eval_versions"][0])

    notes = built.to_dict()["eval_notes"]

    assert notes[version]["why"]
    assert notes[version]["date"]


def test_the_data_holds_the_instructions_of_each_variant(tmp_path):
    """
    Given  a report of a run in exchange mode
    When   it is turned into the page's data
    Then   the prompts hold the instructions text under the variant's 8-character hash
    """
    built = _model(tmp_path)

    prompts = built.to_dict()["prompts"]

    assert set(prompts) == {built.variants[0].facts["prompt8"]}
    assert "proofreading" in next(iter(prompts.values()))["text"]


def test_a_reference_error_the_version_missed_is_marked_in_its_sentence(tmp_path):
    """
    Given  a version that changes only the final mark of a text with reference errors
    When   the report is built
    Then   every missed reference error has a mark in the version's sentence, over the
           student's own words
    """
    source = fixtures.SOURCE_TWO
    built = _model(tmp_path, outputs={"de-02": source[:-1] + "!"})

    item = built.item("v1", 1, "de-02")
    text = built.texts["de-02"]

    assert item is not None
    missed = [index for index, outcome in enumerate(item.ref_outcomes) if outcome == "missed"]
    assert missed
    marks = [mark for row in item.rows if row for mark in row.marks if mark.missed]
    assert sorted(mark.index for mark in marks) == missed
    for row in item.rows:
        for mark in row.marks if row else ():
            if mark.missed and mark.end > mark.start:
                edit = text.ref_edits[mark.index]
                assert row.text[mark.start : mark.end] == "".join(
                    text.tokens[edit.start : edit.end]
                )
