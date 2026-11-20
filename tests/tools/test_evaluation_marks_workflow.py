"""The marks workflow end to end: a sheet, a filled-in sheet, the marks, and what they decide.

The layers are tested alone elsewhere; this file walks the path a person walks.  A sheet is printed
from a stored run, filled in, read back as marks, checked, and then either counted by the gate or
turned into the key's accepted alternatives.  The blind key of the old sheet is imported the same way.

Each test builds its own store in ``tmp_path``, so a failure names the step that broke and not a file
on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.convert import blind_key
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
    text_id,
)
from tools.evaluation.metrics.offsets import char_span_to_tokens, token_offsets
from tools.evaluation.metrics.quality import ordered_edits
from tools.evaluation.report import gates, groups, load, score, spot_check
from tools.evaluation.store import accept, layout, reader, validate, writer

TESTSET = "gec-2026-12"

DE_SOURCE = "Er geht heute nach Hause. Das Wetter ist schön."
DE_REFERENCE = "Er geht heute heim. Das Wetter ist schön."
DE_EXTRA = "Er geht heute heim. Das Wetter ist sehr schön."
"""The reference, plus one change the reference does not make: ``sehr`` before ``schön``."""

EN_SOURCE = "He go to school. The weather is nice."
EN_REFERENCE = "He goes to school. The weather is nice."

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
    offsets = token_offsets(source)
    start, end, replacement = ordered_edits(source, reference)[0]
    return ReferenceLabel(
        start=offsets[start],
        end=offsets[end],
        replacement="".join(replacement),
        code="R:VERB:FORM",
    )


def _record(language: str, source: str, reference: str) -> TestRecord:
    """Return one ``page`` test record of *language*."""
    return TestRecord(
        id=f"{language}-01",
        language=language,
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference),),
        sentence_spans=_spans(source),
        unchanged_sentences=(1,),
        origin="a test",
        band="page",
        corpus="falko-merlin",
    )


def _records() -> tuple[TestRecord, ...]:
    """Return one German and one English ``page`` text."""
    return (
        _record("de", DE_SOURCE, DE_REFERENCE),
        _record("en", EN_SOURCE, EN_REFERENCE),
    )


def _review(record: TestRecord) -> CorrectionReview:
    """Return the review of *record*: its one edit, explained."""
    offsets = token_offsets(record.source)
    start, end, replacement = ordered_edits(record.source, record.reference)[0]
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


def _header(records: tuple[TestRecord, ...], *, repeat: int = 1) -> RunHeader:
    """Return the header of a review run of *records*, of one repeat."""
    return RunHeader(
        input=Input(
            testset=TESTSET,
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=tuple(sorted({record.language for record in records})),
        ),
        protocol=Protocol(
            model="gemma4:12b",
            model_digest="a" * 64,
            fingerprint=FINGERPRINT,
            temperature=0.0,
            exchange=Exchange.ONE_REQUEST_REVIEW,
        ),
        run=RunSettings(seed=20261109, repeat=repeat),
        environment=Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    )


def _answer(record: TestRecord, *, output: str = "") -> Answer:
    """Return one answer of *record*, explained, whose text is *output* or the reference."""
    return Answer(
        text=text_id(record),
        text_sha256=record.text_sha256,
        output=output or record.reference,
        review=_review(record),
    )


def _manifest(records: tuple[TestRecord, ...]) -> Manifest:
    """Return a manifest that pins *records*, as the store writes it."""
    return Manifest(
        testset=TESTSET,
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


def _store(root: Path, records: tuple[TestRecord, ...], *, output: str = "") -> Path:
    """Write *records* and three repeats of one review run into a store at *root*."""
    writer.write_testset(root, TESTSET, records, _manifest(records))
    for repeat in range(1, 4):
        run = RunFile(
            header=_header(records, repeat=repeat),
            sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
            answers=tuple(
                _answer(record, output=output if record.language == "de" else "")
                for record in records
            ),
        )
        writer.write_run(layout.run_path(root, f"run-{repeat}.run.jsonl"), run)
    return root


def _filled(sheet: str, mark: str) -> str:
    """Return *sheet* with every row's last column filled in with *mark*."""
    return sheet.replace("|  |", f"| {mark} |")


def _first(root: Path, *, options: score.ScoreOptions | None = None) -> load.LoadedRun:
    """Return the run of repeat 1 of the store at *root*."""
    loaded = load.load(root, options=options)
    return groups.groups(loaded.runs)[0].first()


def _check(judgement: gates.Judgement, name: str) -> gates.Check:
    """Return the check called *name* of *judgement*."""
    return next(check for check in judgement.checks if check.name == name)


def _marks_path(root: Path, marker: str, records: tuple[TestRecord, ...]) -> Path:
    """Return the marks file of *marker* on the set *records* make."""
    return layout.marks_path(root, marker, question_sha256(list(records)))


# ── the explanation sheet ─────────────────────────────────────────────────────


def test_a_filled_in_sheet_becomes_marks_the_gate_counts(tmp_path):
    """
    Given  a store of one review run, and the explanation sheet of its first repeat
    When   the sheet is filled in, read back as marks, checked and judged
    Then   the marks are valid, they match the answers, and the gate counts them as correct
    """
    records = _records()
    root = _store(tmp_path / "store", records)
    run = _first(root)
    drawn = spot_check.sample(run)

    sheet = spot_check.sheet(drawn, {item.text: item for item in run.items})
    marks = spot_check.marks_from_sheet(_filled(sheet, "correct"), drawn)

    by_text = {text_id(record): record for record in records}
    answers = {answer.text: [answer] for answer in run.run.answers}
    assert validate.problems_of_marks(marks, by_text) == []
    assert validate.unmatched_marks(marks, answers, by_text) == []
    judgement = gates.GateTwoRule().judge(groups.groups(load.load(root).runs)[0], marks)
    assert _check(judgement, "explanations, de").value == "1/1 correct"
    assert _check(judgement, "explanations, de").passed is True


def test_a_mark_names_the_text_the_way_an_answer_does(tmp_path):
    """
    Given  a filled-in explanation sheet
    When   its marks are read back
    Then   each names the text as ``<language>/<id>``, which is what an answer and a key use
    """
    root = _store(tmp_path / "store", _records())
    run = _first(root)
    drawn = spot_check.sample(run)

    marks = spot_check.marks_from_sheet(
        _filled(spot_check.sheet(drawn, {item.text: item for item in run.items}), "correct"), drawn
    )

    assert [mark.text for mark in marks] == ["de/de-01", "en/en-01"]


# ── the extra-edits sheet ─────────────────────────────────────────────────────


def test_an_accepted_extra_edit_scores_as_a_true_positive(tmp_path):
    """
    Given  a run whose German answer makes one change the reference does not
    When   the blind sheet of those changes is filled in, marked valid and accepted
    Then   the change counts as a true positive, and without the accepted key as a false one
    """
    records = _records()
    root = _store(tmp_path / "store", records, output=DE_EXTRA)
    run = _first(root)

    sheet = spot_check.extra_edits_sheet(run)
    marks = spot_check.marks_from_extra_sheet(
        _filled(sheet, "valid"), spot_check.extra_edits_sample(run)
    )
    writer.merge_marks(_marks_path(root, "jh", records), marks)
    accepted = accept.accept_marks(root, TESTSET, "jh")

    with_key = _first(root).items[0]
    without = _first(root, options=score.ScoreOptions(accepted=False)).items[0]
    assert accepted.edits == 1
    assert (with_key.true_positives, with_key.false_positives) == (2, 0)
    assert (without.true_positives, without.false_positives) == (1, 1)


def test_the_extra_edits_sheet_is_blind(tmp_path):
    """
    Given  a run of one model
    When   its extra-edits sheet is printed
    Then   it names the run by its key and never by the model or the file
    """
    root = _store(tmp_path / "store", _records(), output=DE_EXTRA)
    run = _first(root)

    sheet = spot_check.extra_edits_sheet(run)

    assert "gemma4" not in sheet
    assert run.name not in sheet
    assert f"Run key: `{spot_check.extra_edits_sample(run).run_key}`" in sheet
    assert "| Value |" in sheet


# ── the blind key of the old sheet ────────────────────────────────────────────


def _blind_sheet(rows: list[tuple[int, str, str]]) -> str:
    """Return an old-style blind sheet with one row per ``(number, bracket, mark)``."""
    lines = [
        "# Sheet: model edits the reference does not have (blind)",
        "",
        "| # | Lang | Student sentence (change marked) | After the model's change "
        "| Annotator's version of the sentence | Mark | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {number} | DE | Der Satz [{shown}] hier. | Der Satz [{shown}] hier. "
        f"| Der Satz hier. | {mark} |  |"
        for number, shown, mark in rows
    )
    return "\n".join(lines) + "\n"


def test_the_old_blind_key_becomes_edit_marks(tmp_path):
    """
    Given  the old blind sheet's key, in token positions, and the sheet filled in
    When   the key is imported and the marker's verdicts are accepted
    Then   the mapped edits are marks, the one that does not map is listed, and only ``A`` is accepted
    """
    records = _records()
    root = _store(tmp_path / "store", records, output=DE_EXTRA)
    source = records[0].source
    before = source.index("schön")
    insert = char_span_to_tokens(source, (before, before))
    replace_span = char_span_to_tokens(
        source, (source.index("nach Hause"), source.index("nach Hause") + len("nach Hause"))
    )
    key = {
        "1": {"id": "de/de-01", "edit": [insert[0], insert[1], ["sehr", " "]]},
        "2": {"id": "de/de-01", "edit": [replace_span[0], replace_span[1], ["heim"]]},
        "3": {"id": "de/de-01", "edit": [replace_span[0], replace_span[1], ["heim"]]},
    }
    key_path = tmp_path / "key.json"
    key_path.write_text(json.dumps(key), encoding="utf-8")
    sheet_path = tmp_path / "sheet.md"
    sheet_path.write_text(
        _blind_sheet([(1, "∅", "A"), (2, "nach Hause", "C"), (3, "etwas anderes", "A")]),
        encoding="utf-8",
    )

    imported = blind_key.import_blind_key(root, TESTSET, key_path, sheet_path, "jh")
    accepted = accept.accept_marks(root, TESTSET, "jh")

    assert (imported.added, imported.changed) == (2, 0)
    assert len(imported.failed) == 1
    assert "the tokenizer moved" in imported.failed[0]
    assert accepted.edits == 1
    assert accepted.added == (("de/de-01", (before, before), "sehr "),)


# ── two markers ───────────────────────────────────────────────────────────────


def test_two_markers_who_disagree_are_listed_and_nothing_is_accepted(tmp_path):
    """
    Given  one change, called valid by one marker and wrong by another
    When   the first marker's verdicts are accepted
    Then   nothing is accepted, and the disagreement is listed with both markers
    """
    records = _records()
    root = _store(tmp_path / "store", records, output=DE_EXTRA)
    run = _first(root)
    drawn = spot_check.extra_edits_sample(run)
    sheet = spot_check.extra_edits_sheet(run)
    valid = spot_check.marks_from_extra_sheet(_filled(sheet, "valid"), drawn)
    wrong = spot_check.marks_from_extra_sheet(_filled(sheet, "wrong"), drawn)
    writer.merge_marks(_marks_path(root, "jh", records), valid)
    writer.merge_marks(_marks_path(root, "ab", records), wrong)

    accepted = accept.accept_marks(root, TESTSET, "jh")

    assert accepted.edits == 0
    assert reader.read_accepted(root, TESTSET) == {}
    assert [markers for _, markers in accepted.disagreements] == [["ab", "jh"]]


def test_an_explanation_called_wrong_never_refuses_an_edit(tmp_path):
    """
    Given  one change marked valid, and an explanation of the same edit marked wrong
    When   the marker's verdicts are accepted
    Then   the change is accepted, because an explanation verdict is not about the edit
    """
    records = _records()
    root = _store(tmp_path / "store", records, output=DE_EXTRA)
    run = _first(root)
    drawn = spot_check.extra_edits_sample(run)
    edit = drawn.edits[0]
    writer.merge_marks(
        _marks_path(root, "jh", records),
        spot_check.marks_from_extra_sheet(
            _filled(spot_check.extra_edits_sheet(run), "valid"), drawn
        ),
    )
    writer.merge_marks(
        _marks_path(root, "ab", records),
        [
            Mark(
                kind="explanation",
                text=edit.text,
                span=edit.span,
                replacement=edit.replacement,
                value="wrong",
                explanation_sha256="0" * 64,
            )
        ],
    )

    accepted = accept.accept_marks(root, TESTSET, "jh")

    assert accepted.edits == 1
    assert accepted.disagreements == ()
