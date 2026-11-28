"""Unit tests for the report's views: the model and band tables, the selection, the spot-check sheet.

The tables print one row per language and mark a band too small to decide on; the selection reads the
newest run of a model to learn which evaluation a command means, and refuses two digests or two
request profiles instead of picking one; the sheet is drawn from a run, filled in by a teacher and
read back as marks that identify an edit and not a note number.

Nothing here reads the real store: the records and the runs are built in the test, so a failure
names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.core import protocol_table
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.core.modes import Exchange
from tools.evaluation.core.records import (
    Answer,
    Environment,
    Input,
    Mark,
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
from tools.evaluation.report import groups, load, select, spot_check, summary
from tools.evaluation.report.tables import models
from tools.evaluation.store import layout, writer

DE_SOURCE = "Er geht heute nach Hause. Das Wetter ist schön."
DE_REFERENCE = "Er geht heute heim. Das Wetter ist schön."
EN_SOURCE = "He go to school. The weather is nice."
EN_REFERENCE = "He goes to school. The weather is nice."
PAGES_SOURCE = "Sie hat das Buch gelesen. Er hat es auch gelesen."
PAGES_REFERENCE = "Sie hat das Buch gelesen. Er hat es ebenfalls gelesen."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a run of it is not refused for an unknown protocol."""


def _spans(text: str) -> tuple[tuple[int, int], ...]:
    """Return the character span of every sentence of *text*, the full stop included."""
    spans: list[tuple[int, int]] = []
    start = 0
    for index, char in enumerate(text):
        if char == ".":
            spans.append((start, index + 1))
            start = index + 2
    return tuple(spans)


def _label(source: str, reference: str) -> ReferenceLabel:
    """Return the first edit of *reference* against *source* as a label, in characters."""
    offsets = text_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[0]
    return ReferenceLabel(
        start=offsets[start],
        end=offsets[end],
        replacement="".join(replacement),
        code="R:VERB:FORM",
    )


def _record(
    language: str, source: str, reference: str, *, number: int = 1, band: str = "page"
) -> TestRecord:
    """Return one test record of *language* in *band*."""
    return TestRecord(
        id=f"{language}-{number:02d}",
        language=language,
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference),),
        sentence_spans=_spans(source),
        unchanged_sentences=(1,),
        origin="a test",
        band=band,
        corpus="falko-merlin",
    )


def _records() -> tuple[TestRecord, ...]:
    """Return two ``page`` texts and one ``pages`` text, in German and English."""
    return (
        _record("de", DE_SOURCE, DE_REFERENCE),
        _record("en", EN_SOURCE, EN_REFERENCE),
        _record("de", PAGES_SOURCE, PAGES_REFERENCE, number=2, band="pages"),
    )


def _review(record: TestRecord) -> CorrectionReview:
    """Return the review of *record*: its one edit, explained."""
    offsets = text_offsets(record.source)
    start, end, replacement = ordered_edits(record.source, record.reference or record.source)[0]
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=offsets[start],
                        raw_end=offsets[end],
                        original=record.source[offsets[start] : offsets[end]],
                        corrected="".join(replacement),
                    ),
                ),
                sentences=(0,),
                category="R:VERB:FORM",
                explanation="The verb agrees with the subject.",
            ),
        )
    )


def _header(
    records: tuple[TestRecord, ...],
    *,
    model: str = "gemma4:12b",
    temperature: float = 0.0,
    repeat: int = 1,
    testset: str = "gec-2026-12",
    machine: str = "rx9070xt-server",
    digest: str = "a" * 64,
    exchange: Exchange = Exchange.TEXT_FIRST,
) -> RunHeader:
    """Return the header of a run of *records*, of one model, temperature and repeat."""
    return RunHeader(
        input=Input(
            testset=testset,
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=tuple(sorted({record.language for record in records})),
        ),
        protocol=Protocol(
            model=model,
            model_digest=digest,
            fingerprint=FINGERPRINT,
            temperature=temperature,
            exchange=exchange,
        ),
        run=RunSettings(seed=20261109, repeat=repeat),
        environment=Environment(machine=machine, ollama_version="0.40.16"),
    )


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
    *,
    date: str = "2026-11-14T23:22:00+00:00",
    answers: tuple[Answer, ...] | None = None,
    **header,
) -> RunFile:
    """Return a run of *records* with one answer each, of the header *header* names."""
    return RunFile(
        header=_header(records, **header),
        sessions=(Session(date=date, tool_revision="ffa440f"),),
        answers=answers if answers is not None else tuple(_answer(record) for record in records),
    )


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


def _repeats(
    records: tuple[TestRecord, ...], *, answers: tuple[Answer, ...] | None = None, **header
) -> tuple[RunFile, ...]:
    """Return three runs of *records*, one per repeat, of the header *header* names."""
    return tuple(_run(records, repeat=number, answers=answers, **header) for number in range(1, 4))


# ── the model table ─────────────────────────────────────────────────────────


def _summaries(root: Path) -> list[summary.ModelSummary]:
    """Return the summaries of a store of three texts, run three times."""
    records = _records()
    loaded = load.load(_store(root, records, *_repeats(records)))
    return summary.summarise(loaded.items)


def test_the_model_table_prints_one_row_per_language(tmp_path):
    """
    Given  a store of two German and one English text
    When   the model table is written
    Then   each language has one row over all its texts, and the deciding mean closes the table
    """
    summaries = _summaries(tmp_path / "store")

    table = models.model_table(summaries)

    lines = table.splitlines()
    assert lines[0].startswith("| Model | T | Mode | Language |")
    assert len([line for line in lines if line.startswith("| `gemma4:12b`")]) == 2
    assert "Mean over German and English" in table
    assert "French is reported only" in table


def test_the_lenient_score_counts_a_change_at_the_references_place(tmp_path):
    """
    Given  a summary whose model changed three places, one exactly as the reference and one at a
           reference change in other words, out of two reference changes
    When   its lenient F0.5 is read and the model table is written
    Then   precision counts two of three and recall both reference changes, and the table prints
           the lenient figure next to the exact one and in the deciding mean
    """
    summaries = _summaries(tmp_path / "store")
    row = replace(
        summaries[0],
        score=replace(summaries[0].score, true_positives=1, false_positives=2),
        lenient_tp=2,
        located=2,
        ref_edits=2,
    )

    table = models.model_table([row])

    precision, recall = 2 / 3, 1.0
    assert row.lenient_f_beta() == pytest.approx(
        1.25 * precision * recall / (0.25 * precision + recall)
    )
    assert "| F0.5 | F0.5 lenient |" in table
    assert f"| {row.lenient_f_beta():.2f} |" in table
    assert "(lenient " in table


def test_a_language_without_a_score_shows_a_dash(tmp_path):
    """
    Given  a summary of a language whose texts scored nothing
    When   the model table is written
    Then   the score is a dash, so an empty row is not read as a zero
    """
    summaries = _summaries(tmp_path / "store")
    empty = replace(summaries[0], f_beta=None, precision=None, recall=None, gleu=None)

    table = models.model_table([empty])

    assert "| — | — |" in table


def test_the_band_table_marks_the_pages_band_as_a_diagnostic(tmp_path):
    """
    Given  a store with a ``page`` and a ``pages`` text
    When   the band table is written
    Then   both bands have a row, and the ``pages`` band says it is too few texts to decide on
    """
    summaries = _summaries(tmp_path / "store")

    table = models.band_table(summaries)

    assert "| Band |" in table
    assert "| page |" in table
    assert "pages (3 texts, diagnostic)" in table


def test_a_run_without_bands_gives_no_band_table(tmp_path):
    """
    Given  a store whose texts carry no band
    When   the band table is written
    Then   it is empty, not a table with only a header
    """
    records = tuple(
        _record(record.language, record.source, record.reference, band="") for record in _records()
    )
    loaded = load.load(_store(tmp_path / "store", records, *_repeats(records)))

    table = models.band_table(summary.summarise(loaded.items))

    assert table == ""


def test_the_loss_lines_name_the_texts_that_lost_text(tmp_path):
    """
    Given  a store whose model dropped the text of one record
    When   the loss lines are written
    Then   that text is named with its words in and out, and the clean ones are not
    """
    records = _records()
    dropped = tuple(
        _answer(record, output=record.source.split()[0])
        if record.id == "de-01"
        else _answer(record)
        for record in records
    )
    loaded = load.load(_store(tmp_path / "store", records, _run(records, answers=dropped)))

    lines = models.loss_lines(loaded.items)

    assert "de-01" in lines
    assert "en-01" not in lines
    assert "words, cut" in lines


def test_a_store_that_lost_nothing_has_no_loss_lines(tmp_path):
    """
    Given  a store whose model fixed every text
    When   the loss lines are written
    Then   they are empty, so a clean run prints no heading
    """
    records = _records()
    loaded = load.load(_store(tmp_path / "store", records, _run(records)))

    assert models.loss_lines(loaded.items) == ""


# ── the selection ───────────────────────────────────────────────────────────


def test_the_newest_run_of_the_model_names_the_evaluation(tmp_path):
    """
    Given  a store with two runs of one model on two test sets
    When   the model is selected without naming a test set
    Then   the newest run's test set is read, and one group comes back
    """
    records = _records()
    older = _run(records, date="2026-11-10T10:00:00+00:00", testset="gec-2026-11")
    newer = _run(records, date="2026-11-14T10:00:00+00:00", testset="gec-2026-12")
    loaded = load.load(_store(tmp_path / "store", records, older, newer))

    chosen = select.select(loaded, select.Selection(model="gemma4:12b", temperature=0.0))

    assert len(chosen) == 1
    assert chosen[0].key.testset == "gec-2026-12"


def test_a_model_with_no_run_is_refused(tmp_path):
    """
    Given  a store of one model
    When   another model is selected
    Then   the selection is refused, naming the model it could not find
    """
    records = _records()
    loaded = load.load(_store(tmp_path / "store", records, _run(records)))

    with pytest.raises(ValueError, match="no run of 'other:7b'"):
        select.select(loaded, select.Selection(model="other:7b", temperature=0.0))


def test_two_digests_of_one_tag_are_refused(tmp_path):
    """
    Given  a store with two digests of one model tag
    When   the model is selected without naming a digest
    Then   the selection is refused, because two digests are two models
    """
    records = _records()
    first = _run(records, digest="a" * 64)
    second = _run(records, digest="b" * 64)
    loaded = load.load(_store(tmp_path / "store", records, first, second))

    with pytest.raises(ValueError, match="2 digests stored"):
        select.select(loaded, select.Selection(model="gemma4:12b", temperature=0.0))


def test_a_named_digest_chooses_between_two(tmp_path):
    """
    Given  a store with two digests of one model tag
    When   the model is selected with one digest
    Then   only the runs of that digest are read
    """
    records = _records()
    first = _run(records, digest="a" * 64)
    second = _run(records, digest="b" * 64)
    loaded = load.load(_store(tmp_path / "store", records, first, second))

    chosen = select.select(
        loaded, select.Selection(model="gemma4:12b", temperature=0.0, digest="b" * 64)
    )

    assert len(chosen) == 1
    assert chosen[0].runs[0].header.protocol.model_digest == "b" * 64


def test_a_comparison_reads_both_models_on_the_first_models_machine(tmp_path):
    """
    Given  two models, the second run on another machine
    When   the two are selected for a comparison
    Then   the second is refused, because a comparison is one evaluation
    """
    records = _records()
    mine = _run(records, model="gemma4:12b", machine="rx9070xt-server")
    other = _run(records, model="other:7b", machine="another-server")
    loaded = load.load(_store(tmp_path / "store", records, mine, other))

    with pytest.raises(ValueError, match="no run of 'other:7b' on gec-2026-12 at rx9070xt-server"):
        select.select(
            loaded, select.Selection(model="gemma4:12b", temperature=0.0, against="other:7b")
        )


def test_the_second_side_takes_the_first_sides_axes():
    """
    Given  a selection that names one temperature and one exchange
    When   its sides are asked for
    Then   the second side takes both, unless the selection names its own
    """
    shared = select.Selection(
        model="a:1b", temperature=0.2, against="b:2b", exchange=Exchange.TEXT_FIRST
    )
    own = select.Selection(
        model="a:1b",
        temperature=0.2,
        against="b:2b",
        against_temperature=0.7,
        against_exchange=Exchange.ONE_REQUEST_REVIEW,
    )

    assert [side.temperature for side in shared.sides()] == [0.2, 0.2]
    assert [side.exchange for side in shared.sides()] == [Exchange.TEXT_FIRST] * 2
    assert [side.temperature for side in own.sides()] == [0.2, 0.7]
    assert own.sides()[1].exchange is Exchange.ONE_REQUEST_REVIEW


# ── the spot-check sheet ────────────────────────────────────────────────────


def _reviewed(root: Path) -> load.LoadedRun:
    """Return the run of repeat 1 of a store whose answers carry an explained review."""
    records = _records()
    answers = tuple(_answer(record, review=_review(record)) for record in records)
    runs = _repeats(records, answers=answers, exchange=Exchange.ONE_REQUEST_REVIEW)
    loaded = load.load(_store(root, records, *runs))
    return groups.groups(loaded.runs)[0].first()


def _filled(sheet: str, mark: str = "correct") -> str:
    """Return *sheet* with every row's mark column filled in."""
    return sheet.replace("|  |", f"| {mark} |")


def test_the_sample_is_the_same_on_every_machine(tmp_path):
    """
    Given  a run whose answers carry explained reviews
    When   the sample is drawn twice
    Then   it is the same sample, because the run's key seeds the draw
    """
    run = _reviewed(tmp_path / "store")

    assert spot_check.sample(run) == spot_check.sample(run)
    assert spot_check.languages_of(spot_check.sample(run).notes) == ("de", "en")


def test_a_renamed_run_file_draws_the_same_sample(tmp_path):
    """
    Given  a run, and the same run under another file name
    When   the sample is drawn from each
    Then   it is the same sample, because the run key seeds the draw and the name is only a label
    """
    run = _reviewed(tmp_path / "store")
    renamed = replace(run, path=run.path.with_name("another-name.run.jsonl"))

    assert spot_check.sample(renamed).notes == spot_check.sample(run).notes
    assert spot_check.sample(renamed).run_id == "another-name.run.jsonl"


def test_a_verdict_on_another_models_explanation_does_not_mark_this_one(tmp_path):
    """
    Given  a sample, and a mark on the same edit that names another explanation
    When   the marks are read back
    Then   the note is still unmarked and counts as nothing, because the verdict is about the text
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    note = drawn.notes[0]
    other = Mark(
        kind="explanation",
        text=note.text,
        span=note.span,
        replacement=note.replacement,
        value="correct",
        explanation_sha256="0" * 64,
    )

    assert spot_check.unmarked(drawn, [other]) == list(drawn.notes)
    assert spot_check.correct_share(drawn, [other]) == {"de": (0, 1), "en": (0, 1)}


def test_the_same_explanation_text_shares_the_verdict(tmp_path):
    """
    Given  a sample, and a mark that names the explanation the sample holds
    When   the marks are read back
    Then   the note counts as marked and correct, because the verdict is about that text
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    note = drawn.notes[0]
    same = Mark(
        kind="explanation",
        text=note.text,
        span=note.span,
        replacement=note.replacement,
        value="correct",
        explanation_sha256=note.explanation_sha256,
    )

    assert spot_check.unmarked(drawn, [same]) == list(drawn.notes[1:])
    assert spot_check.correct_share(drawn, [same]) == {"de": (1, 1), "en": (0, 1)}


def test_the_sheet_round_trips_through_the_marks(tmp_path):
    """
    Given  a sample of two explained texts
    When   the sheet is written, filled in and read back
    Then   every note is marked, and both languages count as correct
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    items = {item.text: item for item in run.items}

    sheet = spot_check.sheet(drawn, items)
    marks = spot_check.marks_from_sheet(_filled(sheet), drawn)

    assert "| Mark |" in sheet
    assert spot_check.unmarked(drawn, marks) == []
    assert spot_check.correct_share(drawn, marks) == {"de": (1, 1), "en": (1, 1)}


def test_a_mark_that_is_not_one_of_the_three_is_refused(tmp_path):
    """
    Given  a filled-in sheet whose mark is not ``correct``, ``wrong`` or ``vague``
    When   the sheet is read
    Then   it is refused, naming the mark it cannot use
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    sheet = _filled(spot_check.sheet(drawn, {item.text: item for item in run.items}), "fine")

    with pytest.raises(ValueError, match="'fine' is not one of"):
        spot_check.marks_from_sheet(sheet, drawn)


def test_a_sheet_of_another_run_is_refused(tmp_path):
    """
    Given  a sheet of one run and a sample of another
    When   the sheet is read against that sample
    Then   it is refused, because the two name different runs
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    sheet = _filled(spot_check.sheet(drawn, {item.text: item for item in run.items}))
    other = replace(drawn, run_key="0" * 64)

    with pytest.raises(ValueError, match="the sheet is of run key"):
        spot_check.marks_from_sheet(sheet, other)


def test_an_unmarked_sheet_leaves_every_note_unmarked(tmp_path):
    """
    Given  a sheet nobody filled in
    When   it is read back
    Then   no mark is made, and every note is listed as unmarked
    """
    run = _reviewed(tmp_path / "store")
    drawn = spot_check.sample(run)
    sheet = spot_check.sheet(drawn, {item.text: item for item in run.items})

    marks = spot_check.marks_from_sheet(sheet, drawn)

    assert marks == []
    assert len(spot_check.unmarked(drawn, marks)) == len(drawn.notes)
