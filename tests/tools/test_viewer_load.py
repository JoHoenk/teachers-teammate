"""Tests of the viewer's reading: the test set, the caches, and the variants they make."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval.viewer import load, variants


def test_a_cache_is_read_into_runs_and_rows(tmp_path):
    """
    Given  a cache of one model, two repeats, every text answered
    When   it is read against the test set
    Then   both runs are there with both rows, and nothing was skipped
    """
    directory = fixtures.cache(tmp_path / "results", repeats=2)
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert [run.repeat for run in cache.runs] == [1, 2]
    assert cache.rows == 4
    assert cache.skipped == ()


def test_a_run_without_a_header_is_skipped_with_its_reason(tmp_path):
    """
    Given  a results file whose header file is missing
    When   the cache is read
    Then   the file is skipped and the reason names the header it looked for
    """
    directory = fixtures.cache(tmp_path / "results")
    (directory / f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1.header.json").unlink()
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs == ()
    assert len(cache.skipped) == 1
    assert "no " in cache.skipped[0].reason
    assert ".header.json" in cache.skipped[0].reason


def test_a_run_of_another_test_set_is_skipped_with_its_reason(tmp_path):
    """
    Given  a run whose header names another test set
    When   the cache is read against this one
    Then   the run is skipped and the reason names both sets
    """
    directory = tmp_path / "results"
    fixtures.write_cache(
        directory,
        "other__T1__exchange__r1",
        fixtures.header(testset="set-2026-11"),
        [fixtures.row(text) for text in fixtures.records()],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs == ()
    assert "set-2026-11" in cache.skipped[0].reason
    assert fixtures.TESTSET in cache.skipped[0].reason


def test_a_row_of_another_text_is_skipped_and_the_rest_kept(tmp_path):
    """
    Given  a run whose first row carries a text the test set does not have
    When   the cache is read
    Then   that row is skipped with its line number and the other row is kept
    """
    directory = tmp_path / "results"
    texts = fixtures.records()
    fixtures.write_cache(
        directory,
        "run__T1__exchange__r1",
        fixtures.header(),
        [replace(fixtures.row(texts[0]), source="Ein anderer Text."), fixtures.row(texts[1])],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert len(cache.runs[0].rows) == 1
    assert "line 1" in cache.skipped[0].reason
    assert "not the test set's" in cache.skipped[0].reason


def test_a_half_written_last_line_is_skipped_and_the_rows_before_it_kept(tmp_path):
    """
    Given  a results file whose last line was cut off by a run that is still writing
    When   the cache is read
    Then   the cut line is skipped with its line number and the complete rows are kept
    """
    directory = fixtures.cache(tmp_path / "results")
    path = directory / f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"id": "de/de-0', encoding="utf-8")
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert len(cache.runs[0].rows) == 2
    assert "line 3" in cache.skipped[0].reason


def test_the_last_header_of_a_run_is_the_one_read(tmp_path):
    """
    Given  a header file holding one entry per session, the later one with another baseline
    When   the cache is read
    Then   the last entry is the run's header
    """
    directory = tmp_path / "results"
    first = fixtures.header()
    second = {**fixtures.header(), "vram_baseline_mb": 672}
    fixtures.write_cache(
        directory,
        "run__T1__exchange__r1",
        first,
        [fixtures.row(text) for text in fixtures.records()],
        headers=[first, second],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs[0].header["vram_baseline_mb"] == 672


def test_a_test_set_without_records_is_refused(tmp_path):
    """
    Given  a directory with no <language>.jsonl file
    When   it is read as a test set
    Then   it is refused, because a report of no texts has nothing to show
    """
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match=r"no <language>\.jsonl"):
        load.read_testset(empty)


def test_the_repeats_of_one_measurement_are_one_variant(tmp_path):
    """
    Given  a cache of one model in three repeats
    When   the variants are built
    Then   they are one variant with three repeats, and it is complete
    """
    directory = fixtures.cache(tmp_path / "results", repeats=3)
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert len(built) == 1
    assert built[0].repeats == (1, 2, 3)
    assert built[0].texts_done == {1: 2, 2: 2, 3: 2}
    assert built[0].complete


def test_a_run_that_is_still_being_written_is_not_complete(tmp_path):
    """
    Given  a variant whose third repeat holds one text of the two
    When   the variants are built
    Then   the variant is not complete and its counts say how far each repeat got
    """
    directory = fixtures.cache(tmp_path / "results", repeats=2)
    texts = fixtures.records()
    fixtures.write_cache(
        directory,
        f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r3",
        fixtures.header(repeat=3),
        [fixtures.row(texts[0])],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert built[0].repeats == (1, 2, 3)
    assert built[0].texts_done == {1: 2, 2: 2, 3: 1}
    assert not built[0].complete


def test_two_models_and_two_modes_are_four_variants(tmp_path):
    """
    Given  two models, each in exchange and text mode
    When   the variants are built
    Then   there are four, each labelled by the facts that differ
    """
    directory = tmp_path / "results"
    for model in (fixtures.MODEL, fixtures.OTHER_MODEL):
        for mode in ("exchange", "text"):
            fixtures.write_cache(
                directory,
                f"{model.replace(':', '_')}__T1__{mode}__r1",
                fixtures.header(model=model, mode=mode),
                [fixtures.row(text, model=model, mode=mode) for text in fixtures.records()],
            )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert len(built) == 4
    assert {variant.label for variant in built} == {
        f"{fixtures.MODEL} · exchange",
        f"{fixtures.MODEL} · text",
        f"{fixtures.OTHER_MODEL} · exchange",
        f"{fixtures.OTHER_MODEL} · text",
    }


def test_a_label_shows_only_the_facts_that_differ(tmp_path):
    """
    Given  one model at two temperatures
    When   the variants are labelled
    Then   the model is printed once and the temperature separates the two
    """
    directory = tmp_path / "results"
    for temperature in (0.2, 1.0):
        fixtures.write_cache(
            directory,
            f"run__T{temperature:g}__exchange__r1",
            fixtures.header(temperature=temperature),
            [fixtures.row(text, temperature=temperature) for text in fixtures.records()],
        )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert [variant.label for variant in built] == [
        f"{fixtures.MODEL} · T0.2",
        f"{fixtures.MODEL} · T1",
    ]
    assert variants.differing(built) == ("temperature",)


def test_two_runs_of_one_repeat_are_refused(tmp_path):
    """
    Given  two runs of one variant that both carry repeat 1
    When   the variants are built
    Then   the build is refused, because a repeat is one run
    """
    directory = tmp_path / "results"
    for name in ("a", "b"):
        fixtures.write_cache(
            directory,
            f"{name}__T1__exchange__r1",
            fixtures.header(),
            [fixtures.row(text) for text in fixtures.records()],
        )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    with pytest.raises(ValueError, match="two runs of repeat"):
        variants.build(cache.runs, len(testset.records))


def test_two_eval_versions_are_two_tabs(tmp_path):
    """
    Given  two runs of one model under two eval versions
    When   the variants are built
    Then   they are two variants and the versions are listed apart
    """
    directory = tmp_path / "results"
    for version in (2, 4):
        fixtures.write_cache(
            directory,
            f"run__v{version}__r1",
            fixtures.header(eval_version=version),
            [fixtures.row(text) for text in fixtures.records()],
        )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert len(built) == 2
    assert variants.eval_versions(built) == (2, 4)


def test_the_facts_of_a_header_are_the_ones_the_page_shows(tmp_path):
    """
    Given  a run header
    When   its facts are read
    Then   the digest, the prompt and the test set hash are shortened, and the rest is as stored
    """
    facts = variants.facts_of([fixtures.header()])

    assert facts["model"] == fixtures.MODEL
    assert facts["digest8"] == fixtures.DIGEST[:8]
    assert facts["prompt8"] == fixtures.PROMPT[:8]
    assert facts["mode"] == "exchange"
    assert facts["temperature"] == "1"
    assert facts["profile"] == "full"
    assert facts["isolated"] == "isolated"
    assert facts["explainer"] == "-"


def test_a_header_written_by_a_run_is_read_as_a_group_key():
    """
    Given  a local cache's header, which has no run id and names the texts by testset_sha256
    When   its group key is built
    Then   the key carries the test set hash and the facts the grouping needs
    """
    from tools.model_eval import grouping  # noqa: PLC0415

    key = grouping.GroupKey.of_local(fixtures.header())

    assert key.model == fixtures.MODEL
    assert key.manifest_sha256 == fixtures.header()["testset_sha256"]
    assert key.mode.value == "exchange"
    assert key.temperature == 1.0


def test_a_header_without_a_field_the_key_needs_is_refused():
    """
    Given  a header that lacks the model
    When   its group key is built
    Then   it is refused, because a variant without a model is not a variant
    """
    from tools.model_eval import grouping  # noqa: PLC0415

    broken = fixtures.header()
    del broken["model"]

    with pytest.raises(KeyError):
        grouping.GroupKey.of_local(broken)


def test_the_header_file_is_json_and_holds_a_list(tmp_path):
    """
    Given  a cache written by the fixture
    When   its header file is read as JSON
    Then   it is a list of header objects, as a run appends one per session
    """
    directory = fixtures.cache(tmp_path / "results")

    stored = json.loads(
        (directory / f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1.header.json").read_text(
            encoding="utf-8"
        )
    )

    assert isinstance(stored, list)
    assert stored[0]["model"] == fixtures.MODEL


def _two_runs(tmp_path, first: dict, second: dict, names=("a", "b")):
    """Return the runs of two caches files of one text each, written with the given headers."""
    directory = tmp_path / "results"
    for name, header in zip(names, (first, second), strict=True):
        fixtures.write_cache(
            directory,
            f"{name}__T1__exchange__r1",
            header,
            [fixtures.row(text) for text in fixtures.records()],
        )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    return load.read_cache(directory, testset), testset


def test_runs_that_differ_in_isolation_are_two_variants_not_two_repeats(tmp_path):
    """
    Given  two runs of one model and one repeat number, one isolated and one not
    When   the variants are built
    Then   they are two variants, the label says which is which, and nothing is refused
    """
    cache, testset = _two_runs(
        tmp_path, fixtures.header(isolated=True), fixtures.header(isolated=False)
    )

    built = variants.build(cache.runs, len(testset.records))

    assert len(built) == 2
    assert sorted(variant.label for variant in built) == [
        f"{fixtures.MODEL} · isolated",
        f"{fixtures.MODEL} · shared",
    ]
    assert all(variant.repeats == (1,) for variant in built)


def test_runs_asked_with_another_prompt_are_two_variants(tmp_path):
    """
    Given  two runs of one model that differ only in the hash of the prompt
    When   the variants are built
    Then   they are two variants, told apart by the prompt in their labels
    """
    other = "ab" * 32
    cache, testset = _two_runs(tmp_path, fixtures.header(), fixtures.header(prompt=other))

    built = variants.build(cache.runs, len(testset.records))

    assert len(built) == 2
    assert sorted(variant.facts["prompt8"] for variant in built) == sorted(
        [fixtures.PROMPT[:8], other[:8]]
    )
    assert len({variant.label for variant in built}) == 2


def test_a_label_adds_a_provenance_fact_only_where_it_separates(tmp_path):
    """
    Given  two variants that differ in the prompt only
    When   they are labelled
    Then   the prompt is added and the digest, which is the same, is not
    """
    other = "cd" * 32
    cache, testset = _two_runs(tmp_path, fixtures.header(), fixtures.header(prompt=other))

    labels = [variant.label for variant in variants.build(cache.runs, len(testset.records))]

    assert all(fixtures.DIGEST[:8] not in label for label in labels)
    assert any(fixtures.PROMPT[:8] in label for label in labels)


def test_the_facts_of_a_variant_list_what_its_repeats_do_not_share():
    """
    Given  the headers of two repeats with different seeds and dates
    When   the facts of the variant are read
    Then   each value is listed once and a shared one is not repeated
    """
    facts = variants.facts_of(
        [
            fixtures.header(repeat=1, date="2026-11-14T10:00:00+00:00"),
            fixtures.header(repeat=2, date="2026-11-14T22:00:00+00:00"),
        ]
    )

    assert facts["model"] == fixtures.MODEL
    assert facts["seed"] == "20261109, 20261110"
    assert facts["date"] == "2026-11-14"


def test_a_run_written_twice_keeps_the_more_complete_file(tmp_path):
    """
    Given  one run in two caches, one with both texts and one with one
    When   the doubles are removed
    Then   the complete file stays and the other is reported as replaced
    """
    directory_a = tmp_path / "a"
    directory_b = tmp_path / "b"
    name = f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1"
    fixtures.write_cache(
        directory_a, name, fixtures.header(), [fixtures.row(text) for text in fixtures.records()]
    )
    fixtures.write_cache(
        directory_b, name, fixtures.header(), [fixtures.row(fixtures.records()[0])]
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    runs = [
        run for cache in load.read_caches([directory_b, directory_a], testset) for run in cache.runs
    ]

    kept, dropped = variants.newest_runs(runs)

    assert [len(run.rows) for run in kept] == [2]
    assert [(len(lost.rows), len(keep.rows)) for lost, keep in dropped] == [(1, 2)]


def test_the_texts_a_run_has_done_are_counted_once_each(tmp_path):
    """
    Given  a run file that holds the same text twice (a resumed run)
    When   the variants are built
    Then   the repeat has done two texts, not three
    """
    directory = tmp_path / "results"
    texts = fixtures.records()
    fixtures.write_cache(
        directory,
        f"{fixtures.MODEL.replace(':', '_')}__T1__exchange__r1",
        fixtures.header(),
        [fixtures.row(texts[0]), fixtures.row(texts[1]), fixtures.row(texts[0])],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    cache = load.read_cache(directory, testset)

    built = variants.build(cache.runs, len(testset.records))

    assert built[0].texts_done == {1: 2}
    assert built[0].complete


def test_a_run_asked_about_other_texts_is_skipped_by_the_hash(tmp_path):
    """
    Given  a run whose header names the hash of other texts than the set holds now
    When   the cache is read
    Then   the file is skipped, and the reason says the texts are not the same
    """
    directory = tmp_path / "results"
    header = fixtures.header()
    header["testset_sha256"] = "00" * 32
    fixtures.write_cache(
        directory,
        "old__T1__exchange__r1",
        header,
        [fixtures.row(text) for text in fixtures.records()],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs == ()
    assert "other texts" in cache.skipped[0].reason


def test_a_row_with_another_reference_is_skipped(tmp_path):
    """
    Given  a row whose reference is not the test set's (the set was relabelled)
    When   the cache is read
    Then   that row is skipped and the other kept
    """
    directory = tmp_path / "results"
    texts = fixtures.records()
    stale = replace(fixtures.row(texts[1]), reference="Etwas ganz anderes.")
    fixtures.write_cache(
        directory,
        "run__T1__exchange__r1",
        fixtures.header(),
        [fixtures.row(texts[0]), stale],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert [row.id for row in cache.runs[0].rows] == ["de/de-01"]
    assert "reference" in cache.skipped[0].reason


def test_a_stored_row_is_scored_again_when_it_is_read(tmp_path):
    """
    Given  a row whose stored counts are out of date
    When   the cache is read
    Then   the row carries the counts the scoring gives for its texts
    """
    directory = tmp_path / "results"
    text = fixtures.records()[0]
    outdated = replace(fixtures.row(text), true_positives=9, false_positives=9, false_negatives=9)
    fixtures.write_cache(directory, "run__T1__exchange__r1", fixtures.header(), [outdated])
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    row = cache.runs[0].rows[0]
    assert (row.true_positives, row.false_positives, row.false_negatives) == (1, 0, 0)


def test_a_text_the_filter_left_out_is_not_a_skip(tmp_path):
    """
    Given  a cache of two texts and a test set filtered to one of them
    When   the cache is read
    Then   the other text's row is dropped without a skip message
    """
    directory = fixtures.cache(tmp_path / "results")
    full = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    kept = replace(
        full,
        records=full.records[:1],
        by_key={key: record for key, record in full.by_key.items() if key == "de/de-01"},
    )

    cache = load.read_cache(directory, kept)

    assert [row.id for row in cache.runs[0].rows] == ["de/de-01"]
    assert cache.skipped == ()


def test_a_run_whose_header_lacks_a_field_is_skipped_not_fatal(tmp_path):
    """
    Given  a run whose header lacks the provider, as an older tool wrote it
    When   the cache is read
    Then   the run is skipped with the missing field named, and the report can still be built
    """
    directory = tmp_path / "results"
    old = fixtures.header()
    del old["provider"]
    fixtures.write_cache(
        directory, "old__T1__exchange__r1", old, [fixtures.row(text) for text in fixtures.records()]
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs == ()
    assert "provider" in cache.skipped[0].reason


def test_a_run_whose_sessions_disagree_is_skipped(tmp_path):
    """
    Given  a run resumed under another model, so its sessions are not one run
    When   the cache is read
    Then   the file is skipped and the reason says the sessions disagree
    """
    directory = tmp_path / "results"
    fixtures.write_cache(
        directory,
        "mixed__T1__exchange__r1",
        fixtures.header(),
        [fixtures.row(text) for text in fixtures.records()],
        headers=[fixtures.header(), fixtures.header(model=fixtures.OTHER_MODEL)],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.runs == ()
    assert "disagree" in cache.skipped[0].reason


def test_a_row_with_a_unicode_line_separator_is_still_read(tmp_path):
    """
    Given  a row whose output holds U+2028, which the writer leaves unescaped
    When   the cache is read
    Then   the row is read whole and nothing is skipped
    """
    directory = tmp_path / "results"
    texts = fixtures.records()
    fixtures.write_cache(
        directory,
        "sep__T1__exchange__r1",
        fixtures.header(),
        [fixtures.row(texts[0], output=texts[0].reference + chr(0x2028)), fixtures.row(texts[1])],
    )
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))

    cache = load.read_cache(directory, testset)

    assert cache.rows == 2
    assert cache.skipped == ()


def test_the_progress_callback_names_each_file(tmp_path):
    """
    Given  a cache of two repeats
    When   it is read with a progress callback
    Then   the callback is called once per results file
    """
    directory = fixtures.cache(tmp_path / "results", repeats=2)
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    seen: list[str] = []

    load.read_cache(directory, testset, progress=seen.append)

    assert len(seen) == 2
