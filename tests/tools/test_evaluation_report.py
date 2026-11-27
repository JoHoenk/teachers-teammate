"""Unit tests for the report layer's foundation: the loader, the groups, the numbers and the draws.

A store built in the test is read back as scored items, the score cache is used only when its key
matches, two runs of one
measurement group together and two of another do not, the summaries pool the error texts and count
the reliability of every text, and the paired bootstrap gives the same interval on every machine.

Nothing here reads the real store: the records and the runs are built in the test, so a failure
names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.core import protocol_table
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.modes import Exchange, Step
from tools.evaluation.core.records import (
    Answer,
    Environment,
    Input,
    Protocol,
    RunFile,
    RunHeader,
    RunSettings,
    Session,
    TestRecord,
    key_sha256,
    question_sha256,
)
from teachers_teammate.domain.proofreading.word_diff import text_offsets
from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits
from tools.evaluation.report import groups, load, pooled, stats, summary
from tools.evaluation.report.score import SCORER_VERSION, ScoreOptions, Scored
from tools.evaluation.store import layout, writer

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."
OTHER_SOURCE = "Er geht heute nach Hause."
OTHER_REFERENCE = "Er geht heute heim."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a run of it is not refused for an unknown protocol."""


def _label(
    source: str, reference: str, number: int = 0, code: str = "R:VERB:FORM"
) -> ReferenceLabel:
    """Return the *number*-th edit of *reference* against *source* as a label, in characters."""
    offsets = text_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[number]
    return ReferenceLabel(
        start=offsets[start],
        end=offsets[end],
        replacement="".join(replacement),
        code=code,
    )


def _record(**changes) -> TestRecord:
    """Return a small German test record, with *changes* applied."""
    record = TestRecord(
        id="de-01",
        language="de",
        source=SOURCE,
        kind="errors",
        reference=REFERENCE,
        labels=(_label(SOURCE, REFERENCE),),
        sentence_spans=((0, len(SOURCE)),),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _other_record(**changes) -> TestRecord:
    """Return a second small German test record, with *changes* applied."""
    record = TestRecord(
        id="de-02",
        language="de",
        source=OTHER_SOURCE,
        kind="errors",
        reference=OTHER_REFERENCE,
        labels=(_label(OTHER_SOURCE, OTHER_REFERENCE),),
        sentence_spans=((0, len(OTHER_SOURCE)),),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _correct_record(**changes) -> TestRecord:
    """Return a small already-correct German test record, with *changes* applied."""
    record = TestRecord(
        id="de-03",
        language="de",
        source="Ein Satz. Noch einer.",
        kind="correct",
        sentences=("Ein Satz.", "Noch einer."),
        sentence_spans=((0, 9), (10, 21)),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _header(records: tuple[TestRecord, ...], **changes) -> RunHeader:
    """Return the header of a run of *records*, with *changes* applied."""
    header = RunHeader(
        input=Input(
            testset="gec-2026-12",
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=("de",),
        ),
        protocol=Protocol(
            model="gemma4:12b",
            model_digest="a" * 64,
            fingerprint=FINGERPRINT,
            temperature=0.0,
        ),
        run=RunSettings(seed=20261109),
        environment=Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    )
    return replace(header, **changes) if changes else header


def _answer(record: TestRecord, **changes) -> Answer:
    """Return one answer of *record* that fixes it as the reference does, with *changes* applied."""
    answer = Answer(
        text=f"{record.language}/{record.id}",
        text_sha256=record.text_sha256,
        output=record.reference or record.source,
    )
    return replace(answer, **changes) if changes else answer


def _run(records: tuple[TestRecord, ...], **changes) -> RunFile:
    """Return a run of *records* with one answer each, with *changes* applied to the file."""
    run = RunFile(
        header=_header(records),
        sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        answers=tuple(_answer(record) for record in records),
    )
    return replace(run, **changes) if changes else run


def _manifest(records: tuple[TestRecord, ...]) -> Manifest:
    """Return a manifest that pins *records*, as the store writes it."""
    return Manifest(
        testset="gec-2026-12",
        built="2026-11-14T23:22:00+00:00",
        selection_seed=20261109,
        builder_revision="ffa440f",
        question_sha256=question_sha256(list(records)),
        key_sha256=key_sha256(list(records)),
        items=tuple(
            ManifestItem(
                id=record.id,
                language=record.language,
                kind=record.kind,
                corpus=record.corpus,
                band=record.band,
                pointer={},
                source={"sha256": "0" * 64, "words": 1, "chars": len(record.source)},
                reference={"sha256": "0" * 64},
            )
            for record in records
        ),
    )


def _store(root: Path, records: tuple[TestRecord, ...], *runs: RunFile) -> Path:
    """Write *records* and *runs* into a store at *root* and return the root."""
    writer.write_testset(root, "gec-2026-12", records, _manifest(records))
    for number, run in enumerate(runs):
        writer.write_run(layout.run_path(root, f"run-{number}.run.jsonl"), run)
    return root


@pytest.fixture
def store(tmp_path) -> Path:
    """Return a store of three texts and one run that fixes them all."""
    records = (_record(), _other_record(), _correct_record())
    return _store(tmp_path / "store", records, _run(records))


# ── the loader ──────────────────────────────────────────────────────────────


def test_the_loader_scores_every_answer_of_the_store(store):
    """
    Given  a store of three texts and one run that answers all of them
    When   the store is loaded
    Then   every answer is scored, and the item names its model, language and record
    """
    loaded = load.load(store)

    assert loaded.answers == 3
    assert len(loaded.runs) == 1
    assert loaded.skipped == ()
    item = loaded.items[0]
    assert (item.model, item.language, item.item) == ("gemma4:12b", "de", "de-01")
    assert item.mode == "text-first"
    assert item.true_positives > 0


def test_the_loader_skips_an_answer_of_another_text(store):
    """
    Given  a store whose run holds an answer of a text the set does not have
    When   the store is loaded
    Then   that answer is skipped with a reason and the others are kept
    """
    records = (_record(), _other_record(), _correct_record())
    stray = Answer(text="de/de-99", text_sha256="0" * 64, output=SOURCE)
    run = _run(records, answers=(*_run(records).answers, stray))
    root = _store(store.parent / "stray", records, run)

    loaded = load.load(root)

    assert loaded.answers == 3
    assert [skipped.reason for skipped in loaded.skipped] == ["no text 'de/de-99'"]


def test_the_loader_skips_an_answer_asked_about_another_text(store):
    """
    Given  a store whose run holds an answer whose question hash is not the record's
    When   the store is loaded
    Then   the answer is skipped, because it was asked about a text the set no longer holds
    """
    records = (_record(), _other_record(), _correct_record())
    answers = tuple(
        replace(answer, text_sha256="0" * 64) if answer.text == "de/de-01" else answer
        for answer in _run(records).answers
    )
    root = _store(store.parent / "moved", records, _run(records, answers=answers))

    loaded = load.load(root)

    assert loaded.answers == 2
    assert "another text" in loaded.skipped[0].reason


def test_the_loader_skips_a_run_of_a_test_set_it_was_not_asked_for(store):
    """
    Given  a store whose run names a test set that is not in the store
    When   the store is loaded
    Then   the run is skipped with a reason instead of stopping the report
    """
    records = (_record(), _other_record(), _correct_record())
    header = _header(records, input=replace(_header(records).input, testset="gec-2099-01"))
    root = _store(store.parent / "elsewhere", records, _run(records, header=header))

    loaded = load.load(root)

    assert loaded.runs == ()
    assert "gec-2099-01" in loaded.skipped[0].reason


def test_a_review_of_another_version_is_kept_and_flagged(store):
    """
    Given  a store whose run holds a review of a version this tool does not read
    When   the store is loaded
    Then   the answer is scored, its explanations are unknown, and the run says why
    """
    records = (_record(), _other_record(), _correct_record())
    answers = tuple(
        replace(answer, review_unreadable="99", extra={"review": {"version": 99, "edits": []}})
        if answer.text == "de/de-01"
        else answer
        for answer in _run(records).answers
    )
    root = _store(store.parent / "unreadable", records, _run(records, answers=answers))

    loaded = load.load(root)

    assert loaded.answers == 3
    assert [item.explanations_known for item in loaded.items] == [False, True, True]
    assert "reviews of version 99" in loaded.skipped[0].reason


def test_a_broken_run_file_is_skipped_and_the_others_are_read(store):
    """
    Given  a store with one whole run and one file that is not a run
    When   the store is loaded
    Then   the whole run is read, and the broken file is named with its reason
    """
    records = (_record(), _other_record(), _correct_record())
    root = _store(store.parent / "broken", records, _run(records))
    (root / layout.RUNS_DIR / "broken.run.jsonl").write_text("not a run\n", encoding="utf-8")

    loaded = load.load(root)

    assert loaded.answers == 3
    assert [skipped.file for skipped in loaded.skipped] == ["broken.run.jsonl"]
    assert "not JSON" in loaded.skipped[0].reason


def test_a_torn_last_line_is_left_out_and_noted(store):
    """
    Given  a store whose run file was cut off in the middle of its last answer
    When   the store is loaded
    Then   the answers above the torn line are scored, and the file says one line was dropped
    """
    records = (_record(), _other_record(), _correct_record())
    root = _store(store.parent / "torn", records, _run(records))
    path = root / layout.RUNS_DIR / "run-0.run.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(lines[:-1]) + '\n{"type": "answer", "text": "de/de-0', encoding="utf-8"
    )

    loaded = load.load(root)

    assert loaded.answers == 2
    assert [skipped.file for skipped in loaded.skipped] == ["run-0.run.jsonl"]
    assert "torn last line" in loaded.skipped[0].reason


def test_the_loader_reads_only_the_folders_it_is_given(store):
    """
    Given  a store with a run in a folder and a run directly below runs/
    When   the store is loaded for that folder only
    Then   only that folder's run is read
    """
    records = (_record(), _other_record(), _correct_record())
    root = _store(store.parent / "folders", records, _run(records))
    writer.write_run(
        layout.run_path(root, "second.run.jsonl", folder="results-exchange"),
        _run(records),
    )

    loaded = load.load(root, folders=["results-exchange"])

    assert [run.folder for run in loaded.runs] == ["results-exchange"]


# ── the score cache ─────────────────────────────────────────────────────────


def test_the_score_cache_is_written_and_read_back(store, tmp_path):
    """
    Given  a store and an empty cache directory
    When   the store is loaded twice through the cache
    Then   the second load writes nothing new and gives the same numbers
    """
    cache = tmp_path / "cache"

    first = load.load(store, cache=cache)
    written = sorted(path.name for path in cache.glob(f"*{load.SCORES_SUFFIX}"))
    second = load.load(store, cache=cache)

    assert len(written) == 1
    assert [item.scored for item in first.items] == [item.scored for item in second.items]


def test_a_cache_keeps_the_lines_it_served(store, tmp_path):
    """
    Given  a cache of a run of two answers
    When   the run is written again with a third answer and loaded through the same cache
    Then   the cache holds all three lines, because a hit is written back with the new line
    """
    records = (_record(), _other_record(), _correct_record())
    root = _store(store.parent / "growing", records, _run(records[:2]))
    cache = tmp_path / "cache"
    load.load(root, cache=cache)
    path = next(cache.glob(f"*{load.SCORES_SUFFIX}"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2

    writer.write_run(layout.run_path(root, "run-0.run.jsonl"), _run(records))
    loaded = load.load(root, cache=cache)

    assert loaded.answers == 3
    assert len(path.read_text(encoding="utf-8").splitlines()) == 3


def test_a_cache_keeps_both_variants_of_the_report_options(store, tmp_path):
    """
    Given  a cache written without counting the marks
    When   the store is loaded with the marks counted and then without them again
    Then   the cache holds both variants, so neither report has to score the answers again
    """
    cache = tmp_path / "cache"
    load.load(store, cache=cache)
    path = next(cache.glob(f"*{load.SCORES_SUFFIX}"))

    load.load(store, cache=cache, options=ScoreOptions(count_marks=True))
    load.load(store, cache=cache)

    assert len(path.read_text(encoding="utf-8").splitlines()) == 6


def test_two_answers_of_one_output_keep_two_cache_lines(store, tmp_path):
    """
    Given  a run of two texts that the model answered with the same output
    When   the store is loaded through a cache
    Then   the cache holds a line for each text, because the text's hash is part of the key
    """
    twin_source = "Gestern hat er ins Kino gegangen mit sein Freundin."
    twin = _record(
        id="de-04",
        source=twin_source,
        labels=(_label(twin_source, REFERENCE),),
        sentence_spans=((0, len(twin_source)),),
    )
    records = (_record(), twin)
    root = _store(store.parent / "twins", records, _run(records))
    cache = tmp_path / "cache"

    load.load(root, cache=cache)

    path = next(cache.glob(f"*{load.SCORES_SUFFIX}"))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_two_machines_have_two_caches(store, tmp_path):
    """
    Given  two runs of one compare key, one on each of two machines
    When   the store is loaded through one cache directory
    Then   each run has a cache file of its own, because a run key names one file
    """
    records = (_record(), _other_record(), _correct_record())
    elsewhere = replace(
        _header(records), environment=Environment(machine="other", ollama_version="0.40.16")
    )
    root = _store(
        store.parent / "machines",
        records,
        _run(records),
        _run(records, header=elsewhere),
    )
    cache = tmp_path / "cache"

    load.load(root, cache=cache)

    assert len(sorted(cache.glob(f"*{load.SCORES_SUFFIX}"))) == 2


def test_a_cache_line_of_another_scorer_is_not_trusted(store, tmp_path):
    """
    Given  a cache whose lines were written by an older scorer
    When   the store is loaded through it
    Then   every answer is scored again, so a stale cache costs time and never a wrong number
    """
    cache = tmp_path / "cache"
    load.load(store, cache=cache)
    path = next(cache.glob(f"*{load.SCORES_SUFFIX}"))
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line["scorer_version"] = SCORER_VERSION - 1
        line["scores"]["tp"] = 999
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )

    loaded = load.load(store, cache=cache)

    assert all(item.true_positives != 999 for item in loaded.items)


def test_a_cache_line_of_another_key_is_not_trusted(store, tmp_path):
    """
    Given  a cache whose lines were written against another key of the text
    When   the store is loaded through it
    Then   the answers are scored again, because a corrected reference is another number
    """
    cache = tmp_path / "cache"
    load.load(store, cache=cache)
    path = next(cache.glob(f"*{load.SCORES_SUFFIX}"))
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for line in lines:
        line["key_part_sha256"] = "0" * 64
        line["scores"]["tp"] = 999
    path.write_text(
        "".join(json.dumps(line, sort_keys=True) + "\n" for line in lines), encoding="utf-8"
    )

    loaded = load.load(store, cache=cache)

    assert all(item.true_positives != 999 for item in loaded.items)


def test_the_cache_key_moves_with_the_run_steps(store):
    """
    Given  one answer and two headers that differ only in the steps they asked for
    When   the answer's cache key is made for each
    Then   the two keys differ, because the explanations are counted under one and not the other
    """
    record = _record()
    answer = _answer(record)
    header = _header((record,))
    other = replace(
        header,
        protocol=replace(header.protocol, steps=(Step.CORRECT,)),
    )

    assert load.answer_sha256(answer, header, ScoreOptions()) != load.answer_sha256(
        answer, other, ScoreOptions()
    )


def test_the_cache_key_moves_with_the_report_options(store):
    """
    Given  one answer and two sets of report options
    When   the answer's cache key is made for each
    Then   the two keys differ, because counting the marks is another number
    """
    record = _record()
    answer = _answer(record)
    header = _header((record,))

    assert load.answer_sha256(answer, header, ScoreOptions()) != load.answer_sha256(
        answer, header, ScoreOptions(count_marks=True)
    )


# ── the numbers ─────────────────────────────────────────────────────────────


def test_the_pooled_counts_are_the_sum_of_the_texts():
    """
    Given  three scored texts
    When   their counts are pooled
    Then   the three counts are the sums, and the F-score is the pooled one
    """
    items = [
        Scored(true_positives=2, false_positives=1, found=2, false_negatives=3),
        Scored(true_positives=1, false_positives=0, found=1, false_negatives=1),
    ]

    score = pooled.pooled(items)

    assert (score.true_positives, score.false_positives, score.false_negatives) == (3, 1, 4)
    assert pooled.f_beta(score) == score.f_beta(0.5)


def test_nothing_scored_has_no_f_score():
    """
    Given  a text whose reference has no edit and whose answer made none
    When   its F-score is asked for
    Then   it is ``None`` and not 0.00, so a mean is not dragged down by a text with no score
    """
    score = pooled.pooled([Scored()])

    assert pooled.f_beta(score) is None
    assert pooled.precision_recall(score) == (None, None)


def test_the_mean_over_languages_leaves_out_the_ones_without_a_score():
    """
    Given  two languages, one of which has nothing scored
    When   the mean F-score is taken
    Then   only the language with a score counts
    """
    by_language = {
        "de": pooled.pooled([Scored(true_positives=1, found=1, false_negatives=1)]),
        "en": pooled.pooled([Scored()]),
    }

    assert pooled.mean_f_beta(by_language) == pytest.approx(0.8333333333333334)
    assert pooled.mean_gleu({"de": 0.4, "en": None}) == pytest.approx(0.4)


def test_the_mode_label_names_the_exchange_and_its_steps():
    """
    Given  three protocols: the exchange asking every step, the exchange asking step ① alone, and
           the one-request text exchange, which carries the corrected text alone
    When   their mode labels are made
    Then   an exchange that asks its own steps is named by the exchange, and one that asks fewer
           names them, so two runs of one exchange are two rows
    """
    every = Protocol(model="gemma4:12b")
    one = Protocol(model="gemma4:12b", steps=(Step.CORRECT,))
    text = Protocol(model="gemma4:12b", exchange=Exchange.ONE_REQUEST_TEXT, steps=(Step.CORRECT,))

    assert pooled.mode_label(every) == "text-first"
    assert pooled.mode_label(one) == "text-first/correct"
    assert pooled.mode_label(text) == "one-request-text"
    assert pooled.mode_label(replace(every, steps=(Step.CORRECT, Step.EXPLAIN))) == (
        "text-first/correct/explain"
    )


# ── the summaries ───────────────────────────────────────────────────────────


def test_a_summary_pools_the_error_texts_and_counts_every_text(store):
    """
    Given  a store of two error texts and one correct text
    When   the items are summarised
    Then   the scores are the error texts' and the reliability counts all three
    """
    loaded = load.load(store)
    found = summary.summarise(loaded.items)

    assert [row.band for row in found] == ["", "short"]
    row = found[0]
    assert (row.language, row.band, row.texts, row.rows) == ("de", "", 2, 3)
    assert row.words == sum(item.words for item in loaded.items if item.kind == "errors")
    assert row.seconds_per_text == pytest.approx(0.0)


def test_a_summary_has_one_row_per_band_and_one_over_all_texts(store):
    """
    Given  a store whose error texts are of two bands
    When   the items are summarised
    Then   there is a row over all texts and one row per band, in the order of the table
    """
    records = (_record(), _other_record(band="page"), _correct_record())
    root = _store(store.parent / "bands", records, _run(records))

    found = summary.summarise(load.load(root).items)

    assert [row.band for row in found] == ["", "short", "page"]


def test_an_unreadable_review_is_left_out_of_the_explanation_numbers(store):
    """
    Given  a store whose run holds a review of a version this tool does not read
    When   the items are summarised
    Then   that text is left out of the explained and needed counts, and counted as unknown
    """
    records = (_record(), _other_record(), _correct_record())
    answers = tuple(
        replace(answer, review_unreadable="99", extra={"review": {"version": 99, "edits": []}})
        if answer.text == "de/de-01"
        else answer
        for answer in _run(records).answers
    )
    root = _store(store.parent / "unknown", records, _run(records, answers=answers))
    loaded = load.load(root)

    found = summary.summarise(loaded.items)

    row = found[0]
    assert row.explanations_unknown == 1
    assert row.needed == sum(
        item.needed for item in loaded.items if item.kind == "errors" and item.explanations_known
    )


def test_two_prompt_variants_are_two_rows(store):
    """
    Given  a store with two runs of one model that differ only in the step-one prompt
    When   the items are summarised
    Then   each prompt variant is a row of its own, labelled by the axis that varies
    """
    records = (_record(), _other_record(), _correct_record())
    header = _header(records)
    other = replace(header, protocol=replace(header.protocol, step_one_prompt="find"))
    root = _store(store.parent / "prompts", records, _run(records), _run(records, header=other))

    found = summary.summarise(load.load(root).items)

    assert [row.label for row in found if not row.band] == [
        "gemma4:12b (step_one_prompt=find)",
        "gemma4:12b (step_one_prompt=standard)",
    ]


def test_the_per_page_figures_pool_the_repeats_and_skip_the_band_rows(store):
    """
    Given  the summaries of two repeats
    When   a count is asked for per page
    Then   the counts and the words of both repeats are summed and the band rows are skipped
    """
    loaded = load.load(store)
    parts = [summary.summarise(loaded.items), summary.summarise(loaded.items)]

    fixed = summary.pooled_per_page(parts, "true_positives", ("de",))

    words = 2 * sum(item.words for item in loaded.items if item.kind == "errors")
    assert fixed == pytest.approx(
        sum(item.true_positives for item in loaded.items if item.kind == "errors")
        * 2
        / words
        * summary.WORDS_PER_PAGE
    )


# ── the groups ──────────────────────────────────────────────────────────────


def test_two_runs_of_one_measurement_group_together(store):
    """
    Given  a store with two runs that differ only in their repeat number
    When   the runs are grouped
    Then   they are one group of two repeats, in repeat order
    """
    records = (_record(), _other_record(), _correct_record())
    first = _run(records)
    second = _run(records, header=replace(first.header, run=RunSettings(seed=20261110, repeat=2)))
    root = _store(store.parent / "repeats", records, first, second)

    found = groups.groups(load.load(root).runs)

    assert len(found) == 1
    assert found[0].repeats == (1, 2)
    assert found[0].complete is False


def test_two_runs_of_another_temperature_are_two_groups(store):
    """
    Given  a store with two runs of one model at two temperatures
    When   the runs are grouped
    Then   they are two groups, because the temperature is part of the compare key
    """
    records = (_record(), _other_record(), _correct_record())
    first = _run(records)
    warm = replace(first.header.protocol, temperature=0.7)
    root = _store(
        store.parent / "warm",
        records,
        first,
        _run(records, header=replace(first.header, protocol=warm)),
    )

    found = groups.groups(load.load(root).runs)

    assert [group.key.temperature for group in found] == [0.0, 0.7]


def test_two_copies_of_one_run_are_one_group_and_one_skip(store):
    """
    Given  a store with two files of one run key
    When   the store is loaded and its runs are grouped
    Then   one file counts and the other is reported, because it is the same measurement twice
    """
    records = (_record(), _other_record(), _correct_record())
    root = _store(store.parent / "twice", records, _run(records), _run(records))

    loaded = load.load(root)

    assert [run.name for run in loaded.runs] == ["run-1.run.jsonl"]
    assert [(entry.file, entry.reason) for entry in loaded.skipped] == [
        ("run-0.run.jsonl", "the same run key as run-1.run.jsonl, which was kept")
    ]
    assert len(groups.groups(loaded.runs)) == 1


def test_two_machines_are_two_groups_and_not_a_refusal(store):
    """
    Given  a store with one run of one setup on each of two machines
    When   the runs are grouped
    Then   they are two groups, because a group is the repeats of one setup on one machine
    """
    records = (_record(), _other_record(), _correct_record())
    here = _run(records)
    there = _run(
        records,
        header=replace(
            here.header, environment=replace(here.header.environment, machine="rx9070xt")
        ),
    )
    root = _store(store.parent / "machines", records, here, there)

    found = groups.groups(load.load(root).runs)

    assert [group.key.machine for group in found] == ["rx9070xt", "rx9070xt-server"]


def test_two_machines_are_measured_alike(store):
    """
    Given  the keys of one setup on two machines
    When   they are asked whether they were measured alike
    Then   they were, because two machines measured the same quality and only the speed differs
    """
    records = (_record(),)
    here = _header(records)
    there = replace(here, environment=replace(here.environment, machine="rx9070xt"))

    assert groups.GroupKey.of(here).measured_alike(groups.GroupKey.of(there))


def test_a_comparison_may_vary_the_model_and_its_request(store):
    """
    Given  two keys of one measurement that differ in the model, the digest and the temperature
    When   they are asked whether they were measured alike
    Then   they were, because each model is run with its own request
    """
    records = (_record(),)
    one = groups.GroupKey.of(_header(records))
    other = groups.GroupKey.of(
        replace(
            _header(records),
            protocol=replace(
                _header(records).protocol,
                model="gemma4:26b",
                model_digest="b" * 64,
                temperature=0.7,
            ),
        )
    )

    assert one.measured_alike(other) is True


def test_a_comparison_may_not_vary_the_steps(store):
    """
    Given  two keys of one measurement that differ in the steps they asked for
    When   they are asked whether they were measured alike
    Then   they were not, because step ① alone answers another question than the whole exchange
    """
    records = (_record(),)
    one = groups.GroupKey.of(_header(records))
    other = groups.GroupKey.of(
        replace(
            _header(records),
            protocol=replace(_header(records).protocol, steps=(Step.CORRECT,)),
        )
    )

    assert one.measured_alike(other) is False


def test_a_comparison_may_vary_the_step_one_prompt(store):
    """
    Given  two keys of one measurement that differ in the step-one prompt and its instruction hash
    When   they are asked whether they were measured alike
    Then   they were, because the prompt variant is the model's own request and not another question
    """
    records = (_record(),)
    one = groups.GroupKey.of(_header(records))
    other = groups.GroupKey.of(
        replace(
            _header(records),
            protocol=replace(
                _header(records).protocol,
                step_one_prompt="find",
                instructions=(("de", "b" * 64),),
            ),
        )
    )

    assert one.measured_alike(other) is True


def test_the_corrections_rule_lets_the_exchange_modes_compare(store):
    """
    Given  two keys of one measurement, one asking every step and one asking step ① alone
    When   they are asked whether they were measured alike with the corrections rule
    Then   they were, because step ① writes the corrections of both
    """
    records = (_record(),)
    one = groups.GroupKey.of(_header(records))
    other = groups.GroupKey.of(
        replace(
            _header(records),
            protocol=replace(_header(records).protocol, steps=(Step.CORRECT,)),
        )
    )

    assert one.measured_alike(other, corrections=True) is True


def test_a_range_beyond_another_is_a_difference():
    """
    Given  two ranges of three repeats that do not overlap
    When   they are compared
    Then   one lies beyond the other and they are not within the spread
    """
    high = groups.Range.of([0.5, 0.6, 0.7])
    low = groups.Range.of([0.1, 0.2, 0.3])

    assert groups.beyond(high, low) is True
    assert groups.within_the_spread(high, low) is False
    assert groups.within_the_spread(high, groups.Range.of([0.6, 0.7, 0.8])) is True


def test_a_range_of_nothing_is_refused():
    """
    Given  no values
    When   a range is made of them
    Then   it is refused, because a range of nothing has no spread
    """
    with pytest.raises(ValueError, match="at least one value"):
        groups.Range.of([])


def test_a_range_with_a_repeat_missing_is_not_a_range(store):
    """
    Given  the summaries of two repeats, one of which has no F-score
    When   the range of the F-score is asked for
    Then   it is ``None``, because a spread of fewer repeats would look tighter than it is
    """
    loaded = load.load(store)
    parts = [summary.summarise(loaded.items), []]

    assert groups.ranges(parts, groups.deciding_mean_f_beta) is None


# ── the draws ───────────────────────────────────────────────────────────────


def _group(store: Path, *, better: bool) -> groups.RunGroup:
    """Return one group of a store whose answers fix the texts, or fix them worse."""
    records = (_record(), _other_record(), _correct_record())
    answers = tuple(
        _answer(record)
        if better or record.kind == "correct"
        else _answer(record, output=record.source)
        for record in records
    )
    root = _store(store, records, _run(records, answers=answers))
    return groups.groups(load.load(root).runs)[0]


def _botched(record: TestRecord) -> str:
    """Return an answer that changes one word of *record* and fixes nothing the reference asks."""
    return record.source.replace("Kino", "Theater").replace("heute", "morgen")


def _group_fixing(
    root: Path, records: tuple[TestRecord, ...], fixed: tuple[str, ...], **protocol
) -> groups.RunGroup:
    """Return one group of *records* whose answers fix the texts named in *fixed*.

    A text not named is answered with one wrong change, so it counts as a false alarm and a miss.
    """
    answers = tuple(
        _answer(record) if record.id in fixed else _answer(record, output=_botched(record))
        for record in records
    )
    run = _run(records, answers=answers)
    if protocol:
        run = replace(
            run, header=replace(run.header, protocol=replace(run.header.protocol, **protocol))
        )
    return groups.groups(load.load(_store(root, records, run)).runs)[0]


def test_the_bootstrap_is_the_same_on_every_machine(tmp_path):
    """
    Given  two groups of the same texts
    When   the paired bootstrap is drawn twice with the same seed
    Then   the intervals are equal, so a report is reproducible from the files alone
    """
    one = _group(tmp_path / "one", better=True)
    other = _group(tmp_path / "other", better=False)

    first = stats.paired_bootstrap(one, other)
    second = stats.paired_bootstrap(one, other)

    assert first == second
    assert {"F0.5", "recall", "coverage"} <= set(first)


def test_a_model_that_fixes_everything_is_better(tmp_path):
    """
    Given  one group that fixes the texts and one that leaves them alone
    When   the interval of the F-score is read
    Then   it lies above zero and the verdict is "better"
    """
    one = _group(tmp_path / "one", better=True)
    other = _group(tmp_path / "other", better=False)

    interval = stats.paired_bootstrap(one, other)["F0.5"]

    assert interval.delta > 0
    assert interval.low > 0
    assert interval.verdict() is stats.Verdict.BETTER
    assert interval.negated().verdict() is stats.Verdict.WORSE


def test_a_verdict_without_a_page_band_is_the_intervals(tmp_path):
    """
    Given  two groups of twelve error texts, one fixing eleven of them and one fixing the twelfth
    When   the verdict is read
    Then   it is the interval's, because a set without a page text has no band to guard
    """
    records = tuple(_record(id=f"de-{number:02d}") for number in range(1, 13))
    one = _group_fixing(tmp_path / "one", records, tuple(record.id for record in records[:11]))
    other = _group_fixing(tmp_path / "other", records, ("de-12",))

    assert stats.paired_bootstrap(one, other)["F0.5"].verdict() is stats.Verdict.BETTER
    assert stats.verdict_of(one, other) is stats.Verdict.BETTER


def test_a_loss_on_the_page_band_turns_the_verdict_into_a_tie(tmp_path):
    """
    Given  the same two groups, with the twelfth text in the page band
    When   the verdict is read
    Then   it is a tie, because the group ahead on F0.5 is behind on the page band
    """
    records = (
        *(_record(id=f"de-{number:02d}") for number in range(1, 12)),
        _record(id="de-12", band="page"),
    )
    one = _group_fixing(tmp_path / "one", records, tuple(record.id for record in records[:11]))
    other = _group_fixing(tmp_path / "other", records, ("de-12",))

    assert stats.paired_bootstrap(one, other)["F0.5"].verdict() is stats.Verdict.BETTER
    assert stats.verdict_of(one, other) is stats.Verdict.TIE


def test_a_language_that_does_not_decide_does_not_move_the_draws(tmp_path):
    """
    Given  two groups of German texts, one of them with a French text as well
    When   the bootstrap is drawn for each
    Then   the German interval is the same, because each language draws from its own stream
    """
    german = tuple(_record(id=f"de-{number:02d}") for number in range(1, 13))
    french = _record(id="fr-01", language="fr")
    one = _group_fixing(
        tmp_path / "one", german, tuple(f"de-{number:02d}" for number in range(1, 12))
    )
    other = _group_fixing(tmp_path / "other", german, ("de-12",))
    one_with_french = _group_fixing(
        tmp_path / "one-fr",
        (*german, french),
        tuple(f"de-{number:02d}" for number in range(1, 12)),
    )
    other_with_french = _group_fixing(tmp_path / "other-fr", (*german, french), ("de-12",))

    alone = stats.paired_bootstrap(one, other)["F0.5"]
    shared = stats.paired_bootstrap(one_with_french, other_with_french)["F0.5"]

    assert alone == shared


def test_a_verdict_of_a_pair_the_bootstrap_cannot_judge_is_none(tmp_path):
    """
    Given  two groups of one text that were measured at two context windows
    When   the verdict is asked for
    Then   it is ``None``, because a difference there would be a number without a meaning
    """
    records = (_record(),)
    one = _group_fixing(tmp_path / "one", records, ("de-01",))
    other = _group_fixing(tmp_path / "other", records, (), num_ctx=4096)

    assert stats.verdict_of(one, other) is None


def test_a_gap_below_the_minimum_effect_is_a_tie():
    """
    Given  an interval whose lower bound is above zero but whose gap is below the minimum effect
    When   the verdict is read
    Then   it is a tie, because a gap that small is not worth a reader's attention
    """
    interval = stats.Interval(delta=0.005, low=0.001, high=0.009, p_not_better=0.0, p_not_worse=1.0)

    assert interval.verdict() is stats.Verdict.TIE
    assert interval.verdict(minimum=0.0) is stats.Verdict.BETTER


def test_an_interval_that_straddles_zero_is_a_tie():
    """
    Given  an interval that straddles zero
    When   the verdict is read
    Then   it is a tie, because the sampler could have produced the gap
    """
    interval = stats.Interval(delta=0.2, low=-0.1, high=0.5, p_not_better=0.2, p_not_worse=0.8)

    assert interval.verdict() is stats.Verdict.TIE


def test_two_groups_of_other_languages_have_no_interval(tmp_path):
    """
    Given  two groups whose texts are in no deciding language
    When   the paired bootstrap is drawn
    Then   there is no interval, because there is nothing to average over
    """
    records = (_record(language="fr"),)
    root = _store(tmp_path / "fr", records, _run(records))
    one = groups.groups(load.load(root).runs)[0]

    assert stats.paired_bootstrap(one, one) == {}


def test_the_per_text_counts_are_pooled_over_the_repeats(store):
    """
    Given  a group of two repeats of one text
    When   the per-text counts are read
    Then   the counts of both repeats are summed into one entry
    """
    records = (_record(),)
    first = _run(records)
    second = _run(records, header=replace(first.header, run=RunSettings(seed=20261110, repeat=2)))
    root = _store(store.parent / "pooled", records, first, second)
    group = groups.groups(load.load(root).runs)[0]

    found = stats.per_text(group, ("de",))

    assert list(found) == ["de"]
    assert list(found["de"]) == ["de-01"]
    assert found["de"]["de-01"][0] == 2 * group.items[0][0].true_positives


def test_a_correct_text_is_not_in_the_per_text_counts(store):
    """
    Given  a group that holds an error text and a correct one
    When   the per-text counts are read
    Then   only the error text is there, because a correct text has no errors to compare
    """
    group = groups.groups(load.load(store).runs)[0]

    found = stats.per_text(group, ("de",))

    assert "de-03" not in found["de"]
