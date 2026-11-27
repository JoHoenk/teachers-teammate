"""Unit tests for the report layer's tables and for ``compare --along``.

The by-error table counts what a model found of each kind of error, the sweep prints one row per
configuration with its
difference against a reference, the passes table says what a vote over the repeats would buy, the
false-alarm table classifies the changes the reference does not make, the performance table says
what a text costs, and ``compare --along`` picks the groups a comparison may read.

Nothing here reads the real store: the records and the runs are built in the test, so a failure
names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tools.evaluation.core import protocol_table
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.modes import Exchange, Step
from tools.evaluation.core.performance import LoadedModel, RequestStats
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
from tools.evaluation.metrics.offsets import token_offsets
from tools.evaluation.metrics.quality import ordered_edits
from tools.evaluation.report import compare, groups, load, stats
from tools.evaluation.report.tables import by_error, false_alarms, passes, performance, sweep
from tools.evaluation.store import layout, writer

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."
OTHER_SOURCE = "Er geht heute nach Hause."
OTHER_REFERENCE = "Er geht heute heim."
EN_SOURCE = "He go to school every day."
EN_REFERENCE = "He goes to school every day."
CORRECT_SOURCE = "Ein Satz. Noch einer."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a run of it is not refused for an unknown protocol."""


def _label(
    source: str, reference: str, number: int = 0, code: str = "R:VERB:FORM"
) -> ReferenceLabel:
    """Return the *number*-th edit of *reference* against *source* as a label, in characters."""
    offsets = token_offsets(source)
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
        labels=(_label(OTHER_SOURCE, OTHER_REFERENCE, code="R:DET:FORM"),),
        sentence_spans=((0, len(OTHER_SOURCE)),),
        origin="a test",
        band="page",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _english_record(**changes) -> TestRecord:
    """Return a small English test record whose one error is a missing mark, with *changes*."""
    record = TestRecord(
        id="en-01",
        language="en",
        source=EN_SOURCE,
        kind="errors",
        reference=EN_REFERENCE,
        labels=(_label(EN_SOURCE, EN_REFERENCE, code="R:VERB:FORM"),),
        sentence_spans=((0, len(EN_SOURCE)),),
        origin="a test",
        band="short",
        corpus="wi-locness",
    )
    return replace(record, **changes) if changes else record


def _correct_record(**changes) -> TestRecord:
    """Return a small already-correct German test record, with *changes* applied."""
    record = TestRecord(
        id="de-03",
        language="de",
        source=CORRECT_SOURCE,
        kind="correct",
        sentences=("Ein Satz.", "Noch einer."),
        sentence_spans=((0, 9), (10, 21)),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def _records() -> tuple[TestRecord, ...]:
    """Return the four texts of the store the tests read."""
    return (_record(), _other_record(), _english_record(), _correct_record())


def _header(records: tuple[TestRecord, ...], **changes) -> RunHeader:
    """Return the header of a run of *records*, with *changes* applied."""
    header = RunHeader(
        input=Input(
            testset="gec-2026-12",
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=tuple(sorted({record.language for record in records})),
        ),
        protocol=Protocol(
            model="gemma4:12b",
            model_digest="a" * 64,
            fingerprint=FINGERPRINT,
            temperature=0.0,
        ),
        run=RunSettings(seed=20260901),
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


def _run(
    records: tuple[TestRecord, ...],
    outputs: dict[str, str] | None = None,
    **changes,
) -> RunFile:
    """Return a run of *records*, with *outputs* overriding the answer of a text by its id."""
    chosen = outputs or {}
    answers = tuple(
        _answer(record, output=chosen[record.id]) if record.id in chosen else _answer(record)
        for record in records
    )
    run = RunFile(
        header=_header(records),
        sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        answers=answers,
    )
    return replace(run, **changes) if changes else run


def _manifest(records: tuple[TestRecord, ...]) -> Manifest:
    """Return a manifest that pins *records*, as the store writes it."""
    return Manifest(
        testset="gec-2026-12",
        built="2026-11-14T23:22:00+00:00",
        selection_seed=20260901,
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


def _with(header: RunHeader, *, model: str, digest: str, temperature: float, repeat: int):
    """Return *header* of another model, temperature and repeat."""
    return replace(
        header,
        protocol=replace(
            header.protocol, model=model, model_digest=digest, temperature=temperature
        ),
        run=replace(header.run, seed=20260901 + repeat - 1, repeat=repeat),
    )


@pytest.fixture
def store(tmp_path) -> Path:
    """Return a store of four texts and twelve runs: two models at two temperatures, three repeats."""
    records = _records()
    base = _header(records)
    runs: list[RunFile] = []
    for model, digest in (("gemma4:12b", "a" * 64), ("qwen3:8b", "b" * 64)):
        for temperature in (0.0, 0.7):
            for repeat in (1, 2, 3):
                header = _with(
                    base, model=model, digest=digest, temperature=temperature, repeat=repeat
                )
                outputs = (
                    {"en-01": EN_SOURCE, "de-01": REFERENCE.replace("Kino", "Theater")}
                    if model == "qwen3:8b"
                    else {}
                )
                runs.append(_run(records, outputs, header=header))
    return _store(tmp_path / "store", records, *runs)


def _groups(root: Path) -> list[groups.RunGroup]:
    """Return the groups of the store at *root*."""
    return groups.groups(load.load(root).runs)


# ── the by-error table ──────────────────────────────────────────────────────


def test_the_by_error_table_counts_what_a_model_found_of_each_category(store):
    """
    Given  a store where one model fixes every error and one misses the English one
    When   the by-error table is made by category
    Then   the first model found every error of every category and the second missed one
    """
    table = by_error.error_table(load.load(store).items)

    assert "### German: errors found exactly (found in place) / errors" in table
    assert "### English: errors found exactly (found in place) / errors" in table
    assert "| `gemma4:12b` | 0 | text-first | 6 (6) / 6 | 6 (6) / 6 |" in table
    assert "| `qwen3:8b` | 0 | text-first | 6 (6) / 6 | 6 (6) / 6 |" in table
    assert "| `qwen3:8b` | 0 | text-first | 0 (0) / 3 | 0 (0) / 3 |" in table


def test_the_by_error_table_can_group_by_the_corpus_type(store):
    """
    Given  a store whose labels name a verb and a determiner
    When   the by-error table is made by type
    Then   the columns are the corpus's kinds of word and not the app's categories
    """
    table = by_error.error_table(load.load(store).items, by="type")

    assert "| Model | T | Mode | DET | VERB | all |" in table


def test_an_unknown_by_error_grouping_is_refused(store):
    """
    Given  a grouping name that is neither a category nor a type
    When   the by-error table is asked for it
    Then   it is refused, naming the two groupings there are
    """
    with pytest.raises(ValueError, match="unknown grouping 'colour'"):
        by_error.error_table(load.load(store).items, by="colour")


def test_a_text_without_labels_is_left_out_of_the_by_error_table(tmp_path):
    """
    Given  a store whose only error text carries no corpus label
    When   the by-error table is made
    Then   it says so instead of printing a table of zeros
    """
    record = _record(labels=())
    root = _store(tmp_path / "unlabelled", (record,), _run((record,)))

    table = by_error.error_table(load.load(root).items)

    assert table == "No labelled error texts: build the test set again with the corpus labels."


def test_the_by_error_table_prints_the_category_the_model_gave(store):
    """
    Given  a store whose answers carry no review
    When   the by-error table is made
    Then   the category agreement block shows a dash, because nothing was checked
    """
    table = by_error.error_table(load.load(store).items)

    assert "### Category the model gave, where it fixed the error exactly" in table
    assert "| `gemma4:12b` | 0 | text-first | - |" in table


def test_a_label_that_maps_to_no_category_is_grouped_as_unlabelled():
    """
    Given  a reference label with no corpus code
    When   its group is asked for
    Then   it is the unlabelled group, so no error is silently dropped
    """
    label = ReferenceLabel(start=0, end=1, replacement="x", code="")

    assert by_error.group_of(label, "category") == "unlabelled"
    assert by_error.group_of(label, "type") == "unlabelled"


# ── the sweep ───────────────────────────────────────────────────────────────


def test_the_sweep_has_one_row_per_group_and_the_first_of_a_model_is_the_reference(store):
    """
    Given  a store of two models at two temperatures
    When   the sweep rows are made without a reference
    Then   every row is measured against the first group of its own model
    """
    rows = sweep.sweep_rows(_groups(store))

    assert len(rows) == 4
    assert [row.temperature for row in rows] == [0.0, 0.7, 0.0, 0.7]
    assert rows[0].delta is None
    assert rows[0].comparable is True
    assert rows[1].delta is not None


def test_the_sweep_profile_names_the_request_axes_that_are_not_the_temperature(store):
    """
    Given  a group whose run asked for a prompt variant and a sampling option
    When   its profile is asked for
    Then   it names both, and a plain run says "default"
    """
    records = _records()
    base = _header(records)
    variant = replace(
        base,
        protocol=replace(
            base.protocol,
            step_one_prompt="find",
            sampling=(("presence_penalty", 0.0),),
        ),
    )
    root = _store(
        store.parent / "profile",
        records,
        _run(records),
        _run(records, header=variant),
    )

    rows = sweep.sweep_rows(_groups(root))

    assert sorted(row.profile for row in rows) == [
        "default",
        "presence_penalty 0.0, step one prompt find",
    ]


def test_the_sweep_spread_is_the_highest_minus_the_lowest_repeat(store):
    """
    Given  a store whose three repeats of one configuration differ
    When   the sweep rows are made
    Then   the spread is the highest F0.5 minus the lowest, and ``None`` for one repeat
    """
    records = (_record(),)
    base = _header(records)
    runs = [
        _run(
            records,
            {"de-01": SOURCE} if repeat == 2 else None,
            header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=repeat),
        )
        for repeat in (1, 2, 3)
    ]
    root = _store(store.parent / "spread", records, *runs)

    rows = sweep.sweep_rows(_groups(root))

    assert rows[0].spread_de is not None
    assert rows[0].spread_de > 0


def test_the_sweep_per_page_figures_pool_the_repeats(store):
    """
    Given  a store of three repeats
    When   the sweep rows are made
    Then   the fixed-per-page figure counts the three repeats and the error texts only
    """
    rows = sweep.sweep_rows(_groups(store))

    assert rows[0].fixed_per_page is not None
    assert rows[0].fixed_per_page > 0
    assert rows[0].wrong_per_page == pytest.approx(0.0)


def test_a_row_of_another_window_is_not_comparable(store):
    """
    Given  a store whose second run measured another context window
    When   the sweep rows are made
    Then   the second row is not comparable and shows no difference
    """
    records = _records()
    base = _header(records)
    elsewhere = replace(
        _with(base, model="gemma4:12b", digest="a" * 64, temperature=0.7, repeat=1),
        protocol=replace(base.protocol, num_ctx=4096),
    )
    root = _store(
        store.parent / "machines",
        records,
        _run(records),
        _run(records, header=elsewhere),
    )

    rows = sweep.sweep_rows(_groups(root))

    assert rows[1].comparable is False
    assert rows[1].delta is None
    assert "not comparable" in sweep.sweep_table(rows)


def test_the_best_row_is_the_highest_f_score_and_its_ties_are_named(store):
    """
    Given  a store where one model is better than the other
    When   the best row of each model is asked for
    Then   the better model's row is the best and the other model is not a tie with it
    """
    rows = sweep.sweep_rows(_groups(store))
    best, ties = sweep.best_and_ties([row for row in rows if row.model == "gemma4:12b"])

    assert best is not None
    assert best.model == "gemma4:12b"
    assert all(row.model == "gemma4:12b" for row in ties)


def test_the_sweep_table_prints_one_table_per_model(store):
    """
    Given  the rows of two models
    When   the sweep table is made
    Then   each model gets its own heading and its own best row
    """
    table = sweep.sweep_table(sweep.sweep_rows(_groups(store)))

    assert table.count("## `gemma4:12b`") == 1
    assert table.count("## `qwen3:8b`") == 1
    assert table.count("Best:") == 2
    assert "| T | Profile | F0.5 |" in table


# ── the passes ──────────────────────────────────────────────────────────────


def test_a_group_of_one_repeat_has_no_vote(store):
    """
    Given  a store whose runs are one repeat each
    When   the pass rows are made
    Then   no row is printed, because a single pass is all there is
    """
    records = _records()
    root = _store(store.parent / "single", records, _run(records))

    assert passes.pass_rows(_groups(root)) == []


def test_the_vote_keeps_the_edits_two_repeats_made(store):
    """
    Given  a store whose three repeats make the same edits
    When   the pass rows are made
    Then   the vote and the single pass agree, because every repeat made every edit
    """
    rows = passes.pass_rows(_groups(store))

    assert len(rows) == 4
    row = rows[0]
    assert row.repeats == 3
    assert row.vote_two == row.all_repeats
    assert row.vote_two.true_positives == row.single.true_positives


def test_the_flag_is_the_edits_not_every_repeat_made(store):
    """
    Given  a store whose three repeats make the same edits
    When   the pass rows are made
    Then   nothing is flagged, because every repeat made every edit
    """
    rows = passes.pass_rows(_groups(store))

    assert rows[0].flagged_share == pytest.approx(0.0)
    assert rows[0].flagged_false_alarms is None
    assert rows[0].flagged_true_fixes == pytest.approx(0.0)


def test_the_precision_by_votes_has_one_entry_per_repeat(store):
    """
    Given  a store of three repeats
    When   the pass rows are made
    Then   the precision by votes has three entries, and only the third has an edit
    """
    rows = passes.pass_rows(_groups(store))

    assert len(rows[0].precision_by_votes) == 3
    assert rows[0].precision_by_votes[0] is None
    assert rows[0].precision_by_votes[2] == pytest.approx(1.0)


def test_the_time_of_the_passes_comes_from_the_share_of_step_one(store):
    """
    Given  a store whose exchange requests carry a step tag
    When   the pass rows are made
    Then   the time of three passes is one pass plus two shares of step ①
    """
    records = (_record(),)
    base = _header(records)
    runs = [
        _run(
            records,
            header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=repeat),
            answers=tuple(
                replace(
                    answer,
                    requests=(
                        _request("gemma4:12b", 1.0, step="correct"),
                        _request("gemma4:12b", 3.0, step="explain"),
                    ),
                )
                for answer in _run(records).answers
            ),
        )
        for repeat in (1, 2, 3)
    ]
    root = _store(store.parent / "timed", records, *runs)

    rows = passes.pass_rows(_groups(root))

    assert rows[0].step_one_share == pytest.approx(0.25)
    assert rows[0].passes_time == pytest.approx(1.5)


def test_a_correct_only_row_takes_its_share_from_the_exchange_rows(store):
    """
    Given  a store with an exchange run and a correct-only run of one model
    When   the pass rows are made
    Then   the correct-only row prices its passes from the exchange row's share
    """
    records = (_record(),)
    base = _header(records)
    exchange = [
        _run(
            records,
            header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=repeat),
            answers=tuple(
                replace(
                    answer,
                    requests=(
                        _request("gemma4:12b", 1.0, step="correct"),
                        _request("gemma4:12b", 3.0, step="explain"),
                    ),
                )
                for answer in _run(records).answers
            ),
        )
        for repeat in (1, 2, 3)
    ]
    only = [
        _run(
            records,
            header=replace(
                _with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=repeat),
                protocol=replace(base.protocol, steps=(Step.CORRECT,)),
            ),
            answers=tuple(
                replace(answer, requests=(_request("gemma4:12b", 1.0, step="correct"),))
                for answer in _run(records).answers
            ),
        )
        for repeat in (1, 2, 3)
    ]
    root = _store(store.parent / "correct-only", records, *exchange, *only)

    rows = passes.pass_rows(_groups(root))

    by_mode = {row.mode: row for row in rows}
    assert by_mode["text-first/correct"].step_one_share == pytest.approx(0.25)


def test_an_interrupted_run_is_refused_by_the_passes(store):
    """
    Given  a store whose second repeat lacks a text the first has
    When   the pass rows are made
    Then   the vote is refused, because a vote needs every text in every repeat
    """
    records = (_record(), _other_record())
    base = _header(records)
    first = _run(
        records, header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=1)
    )
    second = _run(
        records,
        header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.0, repeat=2),
        answers=first.answers[:1],
    )
    root = _store(store.parent / "interrupted", records, first, second)

    with pytest.raises(ValueError, match="does not hold the same texts"):
        passes.pass_rows(_groups(root))


def test_the_passes_table_prints_one_row_per_configuration(store):
    """
    Given  the pass rows of two models at two temperatures
    When   the passes table is made
    Then   every configuration has a row and the note explains the vote
    """
    table = passes.passes_table(passes.pass_rows(_groups(store)))

    assert table.count("| `gemma4:12b` |") == 2
    assert table.count("| `qwen3:8b` |") == 2
    assert "The single pass is the mean of the repeats" in table


def _request(model: str, wall_s: float, *, step: str = "") -> RequestStats:
    """Return one request of *model* that took *wall_s* seconds and wrote ten tokens."""
    return RequestStats(
        model=model,
        prompt_tokens=100,
        answer_tokens=10,
        load_s=0.0,
        prompt_eval_s=0.1,
        generation_s=wall_s,
        total_s=wall_s,
        wall_s=wall_s,
        step=step,
    )


# ── the false alarms ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("before", "after", "kind"),
    [
        ("word,", "word", "comma"),
        ("word.", "word!", "punct"),
        ("Word", "word", "case"),
        ("color", "colour", "bre_ame"),
        ("teh", "the", "small"),
        ("word", "", "delete"),
        ("", "word", "insert"),
        ("a long phrase", "another phrase", "reword"),
        (" ", "", "space"),
    ],
)
def test_the_kind_of_a_change_says_what_it_does_to_the_words(before, after, kind):
    """
    Given  the words a change removes and the words it puts there
    When   the kind of the change is asked for
    Then   it is the kind the classification names
    """
    assert false_alarms.kind_of(before, after) == kind


def test_a_change_in_a_sentence_the_reference_leaves_alone_is_kept(tmp_path):
    """
    Given  a text of two sentences whose reference changes only the first
    When   the model changes the second
    Then   the change is classified as ``kept``, because nobody asked for it
    """
    source = "Der Hund bellt. Die Katze schläft."
    reference = "Der Hund bellt laut. Die Katze schläft."
    record = TestRecord(
        id="de-05",
        language="de",
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference),),
        sentence_spans=((0, 15), (16, 33)),
        unchanged_sentences=(1,),
        band="short",
    )
    output = "Der Hund bellt laut. Die Katze pennt."
    root = _store(tmp_path / "kept", (record,), _run((record,), {"de-05": output}))

    found = false_alarms.classify(load.load(root).items[0])

    assert [(alarm.where, alarm.kind) for alarm in found] == [("kept", "reword")]


def test_a_change_that_touches_a_reference_change_is_an_alternative(tmp_path):
    """
    Given  a text whose reference replaces one word
    When   the model replaces the same word with another one
    Then   the change is classified as ``alt``: noticed, fixed differently
    """
    record = _other_record()
    root = _store(
        tmp_path / "alt",
        (record,),
        _run((record,), {"de-02": "Er geht heute heimwärts."}),
    )

    found = false_alarms.classify(load.load(root).items[0])

    assert [(alarm.where, alarm.kind) for alarm in found] == [("alt", "reword")]


def test_a_change_elsewhere_in_an_error_sentence_is_extra(tmp_path):
    """
    Given  a text whose reference changes one word
    When   the model changes another word of the same sentence
    Then   the change is classified as ``extra``
    """
    record = _other_record()
    root = _store(
        tmp_path / "extra",
        (record,),
        _run((record,), {"de-02": "Er geht heute nach Hause!"}),
    )

    found = false_alarms.classify(load.load(root).items[0])

    assert [(alarm.where, alarm.kind) for alarm in found] == [("extra", "punct")]


def test_a_correct_text_is_left_out_of_the_false_alarm_tally(store):
    """
    Given  a store with a correct text the model leaves untouched
    When   the false alarms are tallied
    Then   only the error texts are counted, so the figure is per error text
    """
    found = false_alarms.tally(load.load(store).items)

    assert found["gemma4:12b"]["de"].texts == 12
    assert found["gemma4:12b"]["en"].texts == 6
    assert found["gemma4:12b"]["de"].total == 0
    assert found["qwen3:8b"]["de"].total == 12


def test_the_false_alarm_table_prints_one_table_per_language(store):
    """
    Given  a store where one model changes a mark the reference keeps
    When   the false-alarm table is made
    Then   each language has a table and the model's false alarm is counted
    """
    table = false_alarms.false_alarm_table(load.load(store).items)

    assert "### German: false alarms per error text" in table
    assert "### English: false alarms per error text" in table
    assert "| `gemma4:12b` | 12 | 0.0 |" in table
    assert "| `qwen3:8b` | 12 | 1.0 |" in table


# ── the performance ─────────────────────────────────────────────────────────


def test_only_the_texts_that_were_not_shared_count_for_speed(tmp_path):
    """
    Given  a store whose two texts were measured, one of them shared with another model
    When   the performance is summarised
    Then   only the unshared text enters the figures, and the shared one is counted as measured
    """
    records = (_record(), _other_record())
    run = _run(records)
    answers = (
        replace(
            run.answers[0],
            seconds=2.0,
            requests=(_request("gemma4:12b", 2.0),),
            loaded=(LoadedModel(name="gemma4:12b", size_bytes=1, size_vram_bytes=1),),
        ),
        replace(
            run.answers[1],
            seconds=9.0,
            shared=True,
            requests=(_request("gemma4:12b", 9.0),),
        ),
    )
    root = _store(tmp_path / "shared", records, replace(run, answers=answers))

    found = performance.summarise_performance(load.load(root).items)

    assert len(found) == 1
    assert found[0].texts == 2
    assert found[0].comparable == 1
    assert found[0].seconds_per_100_words == pytest.approx(
        2.0 / load.load(root).items[0].words * 100
    )


def test_two_machines_are_two_speed_rows(tmp_path):
    """
    Given  a store with one run on each of two machines
    When   the performance is summarised
    Then   each machine is a row of its own, because they measured the same quality and other speeds
    """
    records = (_record(), _other_record())
    run = _run(records)
    answers = tuple(
        replace(answer, requests=(_request("gemma4:12b", 1.0),)) for answer in run.answers
    )
    elsewhere = replace(run.header, environment=replace(run.header.environment, machine="rx9070xt"))
    root = _store(
        tmp_path / "machines",
        records,
        replace(run, answers=answers),
        replace(run, answers=answers, header=elsewhere),
    )

    found = performance.summarise_performance(load.load(root).items)

    assert [row.machine for row in found] == ["rx9070xt", "rx9070xt-server"]


def test_the_answer_length_spread_is_the_middle_eighty_percent(tmp_path):
    """
    Given  a store whose three texts cost different numbers of answer tokens
    When   the performance is summarised
    Then   the spread has a low, a typical and a high figure
    """
    records = (_record(), _other_record(), _english_record())
    run = _run(records)
    answers = tuple(
        replace(answer, requests=(_request("gemma4:12b", 1.0),)) for answer in run.answers
    )
    root = _store(tmp_path / "spread", records, replace(run, answers=answers))

    found = performance.summarise_performance(load.load(root).items)

    assert found[0].answer_tokens_spread is not None
    low, typical, high = found[0].answer_tokens_spread.rounded()
    assert low <= typical <= high


def test_the_catalog_lines_are_only_for_review_mode(tmp_path):
    """
    Given  a store whose runs asked the exchange and not the one-request review
    When   the catalog lines are made
    Then   there are none, because a text-mode answer's length is not a figure per model
    """
    records = (_record(),)
    run = _run(records)
    answers = tuple(
        replace(answer, requests=(_request("gemma4:12b", 1.0),)) for answer in run.answers
    )
    root = _store(tmp_path / "catalog", records, replace(run, answers=answers))

    lines = performance.catalog_lines(performance.summarise_performance(load.load(root).items))

    assert lines == "No review-mode text was measured: nothing to put in the catalog."


def test_the_performance_table_prints_a_dash_for_what_was_not_measured(store):
    """
    Given  a store whose answers carry no performance record
    When   the performance table is made
    Then   there is no row, because nothing was measured
    """
    assert performance.summarise_performance(load.load(store).items) == []


# ── compare --along ─────────────────────────────────────────────────────────


def test_the_axes_of_an_along_value_are_the_axes_of_the_table():
    """
    Given  an ``--along`` value naming two axes
    When   it is parsed
    Then   the two names come back in order
    """
    assert compare.axes_of("temperature,steps") == ("temperature", "steps")
    assert compare.axes_of("sampling.presence_penalty") == ("sampling.presence_penalty",)


def test_an_unknown_axis_is_refused():
    """
    Given  an ``--along`` value naming something that is not an axis
    When   it is parsed
    Then   it is refused, naming the axes there are
    """
    with pytest.raises(ValueError, match="unknown axis 'warmth'"):
        compare.axes_of("warmth")


def test_the_environment_is_an_axis_a_command_can_name():
    """
    Given  an ``--along`` value naming the machine and whether the card was isolated
    When   it is parsed
    Then   both names come back, because runs that differ only there are groups to be told apart
    """
    assert compare.axes_of("machine,isolated") == ("machine", "isolated")
    assert compare.where_of(["isolated=true"]) == {"isolated": "true"}


def test_an_empty_along_value_is_refused():
    """
    Given  an ``--along`` value with no axis in it
    When   it is parsed
    Then   it is refused, because a comparison has to vary something
    """
    with pytest.raises(ValueError, match="at least one axis"):
        compare.axes_of(" , ")


def test_a_where_pair_without_a_value_is_refused():
    """
    Given  a ``--where`` pair with no ``=``
    When   it is parsed
    Then   it is refused, naming the form it wants
    """
    with pytest.raises(ValueError, match="axis=value"):
        compare.where_of(["model"])


def test_a_comparison_along_temperature_has_one_row_per_temperature(store):
    """
    Given  a store of one model at two temperatures
    When   the runs are compared along the temperature
    Then   there are two rows, in the order of the temperatures
    """
    along = compare.Along(axes=("temperature",), where={"model": "gemma4:12b"})

    found = compare.compare(_groups(store), along=along)

    assert [found.values(row) for row in found.rows] == [("0",), ("0.7",)]
    assert found.baseline is found.rows[0]


def test_two_runs_that_differ_only_in_isolation_are_told_apart_by_naming_it(tmp_path):
    """
    Given  two runs of one model and temperature, one with the card isolated and one without
    When   they are compared without naming the isolation, and then along it
    Then   the first is refused with a hint that works, and the second has one row per value
    """
    records = _records()
    run = _run(records)
    shared = replace(run.header, environment=replace(run.header.environment, isolated=False))
    alone = replace(run.header, environment=replace(run.header.environment, isolated=True))
    root = _store(
        tmp_path / "isolation",
        records,
        replace(run, header=shared),
        replace(run, header=alone),
    )

    with pytest.raises(ValueError, match="--where <axis>=<value>"):
        compare.compare(_groups(root), along=compare.Along(axes=("model",), where={}))
    found = compare.compare(_groups(root), along=compare.Along(axes=("isolated",), where={}))

    assert [found.values(row) for row in found.rows] == [("false",), ("true",)]


def test_a_tie_is_worded_as_a_tie_with_the_baseline(store):
    """
    Given  a comparison whose verdicts name each of the three outcomes
    When   the wording is looked up
    Then   a tie reads "a tie with the baseline" and not "tie than the baseline"
    """
    assert compare.VERDICT_WORDS[stats.Verdict.TIE] == "a tie with the baseline"
    assert compare.VERDICT_WORDS[stats.Verdict.BETTER] == "better than the baseline"
    assert compare.VERDICT_WORDS[stats.Verdict.WORSE] == "worse than the baseline"
    assert set(compare.VERDICT_WORDS) == set(stats.Verdict)


def test_a_comparison_refuses_two_runs_that_differ_in_an_unnamed_axis(store):
    """
    Given  a store of two models at two temperatures
    When   the runs are compared along the model alone
    Then   it is refused, because the temperature differs too and would be attributed to the model
    """
    along = compare.Along(axes=("model",), where={})

    with pytest.raises(ValueError, match="differ in temperature"):
        compare.compare(_groups(store), along=along)


def test_a_comparison_along_the_model_needs_the_temperature_fixed(store):
    """
    Given  a store of two models at two temperatures
    When   the runs are compared along the model at one temperature
    Then   there are two rows, one per model
    """
    along = compare.Along(axes=("model",), where={"temperature": "0"})

    found = compare.compare(_groups(store), along=along)

    assert [found.values(row) for row in found.rows] == [("gemma4:12b",), ("qwen3:8b",)]


def test_a_comparison_measures_every_row_against_the_baseline(store):
    """
    Given  a store of one model at two temperatures and a named baseline
    When   the runs are compared along the temperature
    Then   the baseline row has no difference and the other row has one
    """
    along = compare.Along(axes=("temperature",), where={"model": "gemma4:12b"}, baseline="0.7")

    found = compare.compare(_groups(store), along=along)

    assert found.baseline is found.rows[1]
    assert found.rows[1].delta is None
    assert found.rows[0].delta is not None


def test_a_baseline_that_names_no_row_is_refused(store):
    """
    Given  a baseline value no row has
    When   the runs are compared
    Then   it is refused, naming the values there are
    """
    along = compare.Along(axes=("temperature",), where={"model": "gemma4:12b"}, baseline="1.5")

    with pytest.raises(ValueError, match=r"the baseline '1\.5' is not one of"):
        compare.compare(_groups(store), along=along)


def test_a_where_that_matches_no_run_is_refused(store):
    """
    Given  a filter no run matches
    When   the runs are compared
    Then   it is refused, naming what was fixed
    """
    along = compare.Along(axes=("temperature",), where={"model": "no-such-model"})

    with pytest.raises(ValueError, match="no run matches model=no-such-model"):
        compare.compare(_groups(store), along=along)


def test_a_comparison_that_hides_a_difference_is_refused(store):
    """
    Given  two runs of two models at two temperatures
    When   they are compared along the temperature alone
    Then   it is refused, naming the model, because the difference would be read as the temperature's
    """
    records = _records()
    base = _header(records)
    other = _with(base, model="qwen3:8b", digest="b" * 64, temperature=0.7, repeat=1)
    root = _store(
        store.parent / "hidden",
        records,
        _run(
            records,
            header=_with(base, model="gemma4:12b", digest="a" * 64, temperature=0.2, repeat=1),
        ),
        _run(records, header=other),
    )

    with pytest.raises(ValueError, match="the runs differ in model"):
        compare.compare(_groups(root), along=compare.Along(axes=("temperature",), where={}))


def test_a_comparison_across_two_test_sets_is_refused(store):
    """
    Given  a store whose two runs were asked about two test sets
    When   they are compared along the temperature
    Then   it is refused, naming the sets, because they share no text to pair
    """
    records = _records()
    base = _header(records)
    root = _store(
        store.parent / "sets",
        records,
        _run(records),
        _run(
            records,
            header=replace(base, protocol=replace(base.protocol, temperature=0.7)),
        ),
    )
    writer.write_testset(
        root,
        "gec-2026-12-heldout",
        records,
        replace(_manifest(records), testset="gec-2026-12-heldout"),
    )
    held_out = replace(base, input=replace(base.input, testset="gec-2026-12-heldout"))
    writer.write_run(
        layout.run_path(root, "run-2.run.jsonl"),
        _run(
            records,
            header=replace(held_out, protocol=replace(base.protocol, temperature=0.7)),
        ),
    )

    with pytest.raises(ValueError, match="the sets share no text"):
        compare.compare(_groups(root), along=compare.Along(axes=("temperature",), where={}))


def test_a_comparison_refuses_a_prompt_that_changed_without_its_axis(store):
    """
    Given  two runs of one model at two temperatures, made under two protocol versions
    When   they are compared along the temperature
    Then   it is refused, naming the two versions, because the prompt text is not the temperature's
    """
    records = _records()
    base = _header(records)
    old_version = protocol_table.fingerprints_of(1)[0]
    new_version = protocol_table.fingerprints_of(protocol_table.current_version())[0]
    root = _store(
        store.parent / "prompt",
        records,
        _run(
            records,
            header=replace(
                base,
                protocol=replace(
                    base.protocol,
                    temperature=0.0,
                    fingerprint=old_version,
                    instructions=(("de", "a" * 64),),
                ),
            ),
        ),
        _run(
            records,
            header=replace(
                base,
                protocol=replace(
                    base.protocol,
                    temperature=0.7,
                    fingerprint=new_version,
                    instructions=(("de", "b" * 64),),
                ),
            ),
        ),
    )

    with pytest.raises(ValueError, match=r"the prompt text differs \(protocol version 1 vs 5\)"):
        compare.compare(_groups(root), along=compare.Along(axes=("temperature",), where={}))


def test_a_comparison_along_the_prompt_variant_is_allowed(store):
    """
    Given  two runs of one model that differ in the step-one prompt and in its text
    When   they are compared along the prompt variant
    Then   the two rows are the two variants, because the prompt is the axis that was named
    """
    records = _records()
    base = _header(records)
    root = _store(
        store.parent / "variant",
        records,
        _run(records),
        _run(
            records,
            header=replace(
                base,
                protocol=replace(
                    base.protocol,
                    step_one_prompt="keep",
                    instructions=(("de", "b" * 64),),
                ),
            ),
        ),
    )

    found = compare.compare(_groups(root), along=compare.Along(axes=("step_one_prompt",), where={}))

    assert [found.values(row) for row in found.rows] == [("keep",), ("standard",)]


def test_the_comparison_table_leaves_out_the_column_the_axis_already_shows(store):
    """
    Given  a comparison along the temperature
    When   its table is made
    Then   the temperature is a leading column and the sweep's own T column is gone
    """
    along = compare.Along(axes=("temperature",), where={"model": "gemma4:12b"})

    table = compare.compare(_groups(store), along=along).table()

    assert "## Along temperature" in table
    assert "| temperature | Profile | F0.5 |" in table
    assert "| T |" not in table


def test_the_comparison_names_the_baseline_and_the_verdicts(store):
    """
    Given  a comparison along the temperature
    When   its verdicts are made
    Then   the baseline says so and the other row says what its difference is
    """
    along = compare.Along(axes=("temperature",), where={"model": "gemma4:12b"})

    verdicts = compare.compare(_groups(store), along=along).verdicts()

    assert verdicts[0].endswith(": the baseline")
    assert "the baseline (" in verdicts[1]


def test_a_sampling_option_is_an_axis_of_its_own(store):
    """
    Given  a store whose two runs differ in one sampling option
    When   the runs are compared along that option
    Then   the two rows are the two values of the option
    """
    records = _records()
    base = _header(records)
    plain = _run(records)
    tuned = _run(
        records,
        header=replace(
            base,
            protocol=replace(base.protocol, sampling=(("presence_penalty", 0.0),)),
        ),
    )
    root = _store(store.parent / "sampling", records, plain, tuned)

    found = compare.compare(
        _groups(root), along=compare.Along(axes=("sampling.presence_penalty",), where={})
    )

    assert [found.values(row) for row in found.rows] == [("0",), ("",)]


def test_the_mode_of_a_group_names_the_exchange_and_its_steps(store):
    """
    Given  a group of the whole exchange and one of step ① alone
    When   their modes are asked for
    Then   the first is the exchange's name and the second names the step
    """
    records = (_record(),)
    base = _header(records)
    only = replace(base, protocol=replace(base.protocol, steps=(Step.CORRECT,)))
    root = _store(
        store.parent / "modes",
        records,
        _run(records),
        _run(records, header=only),
    )

    found = _groups(root)

    assert [group.key.mode for group in found] == ["text-first/correct", "text-first"]


def test_the_mode_of_a_one_request_run_is_the_exchange_alone(store):
    """
    Given  a group of a run that asks the corrected text in one request, which has no steps
    When   its mode is asked for
    Then   it is the exchange's name, with no empty step after a slash
    """
    records = (_record(),)
    base = _header(records)
    one_request = replace(
        base,
        protocol=replace(base.protocol, exchange=Exchange.ONE_REQUEST_TEXT, steps=()),
    )
    root = _store(
        store.parent / "one-request",
        records,
        _run(records),
        _run(records, header=one_request),
    )

    found = _groups(root)

    assert [group.key.mode for group in found] == ["one-request-text", "text-first"]


def test_a_review_run_and_an_exchange_run_are_not_measured_alike(store):
    """
    Given  two keys of one measurement, one asking the exchange and one the one-request review
    When   they are asked whether they were measured alike
    Then   they were not, because the two answer another question
    """
    records = (_record(),)
    base = _header(records)
    review = replace(
        base,
        protocol=replace(base.protocol, exchange=Exchange.ONE_REQUEST_REVIEW, steps=()),
    )

    assert groups.GroupKey.of(base).measured_alike(groups.GroupKey.of(review)) is False
