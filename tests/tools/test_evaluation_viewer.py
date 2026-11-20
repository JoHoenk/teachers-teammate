"""Unit tests for the page's model: the texts of a store, keyed by their test set.

Two test sets of one store both hold ``de-01``, and the page must show two texts, each diffed
against its own source and each answered by the run of its own set.  The store is built in the
test, so a failure names the rule that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.core import protocol_table
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
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
from tools.evaluation.report import load
from tools.evaluation.report.viewer import model
from tools.evaluation.store import layout, writer

MAIN_SOURCE = "Er geht heute nach Hause."
MAIN_REFERENCE = "Er geht heute heim."
HELDOUT_SOURCE = "Sie geht heute nach Hause."
HELDOUT_REFERENCE = "Sie geht heute heim."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a run of it is not refused for an unknown protocol."""


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


def _record(source: str, reference: str) -> TestRecord:
    """Return the text ``de-01`` of a set, with *source* and *reference*."""
    return TestRecord(
        id="de-01",
        language="de",
        source=source,
        kind="errors",
        reference=reference,
        labels=(_label(source, reference),),
        sentence_spans=((0, len(source)),),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )


def _header(records: tuple[TestRecord, ...], testset: str) -> RunHeader:
    """Return the header of a run of *records* of the test set *testset*."""
    return RunHeader(
        input=Input(
            testset=testset,
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


def _review(record: TestRecord) -> CorrectionReview:
    """Return the review of *record*: its one edit, explained."""
    offsets = token_offsets(record.source)
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


def _answer(record: TestRecord) -> Answer:
    """Return one answer of *record* that fixes it as the reference does."""
    return Answer(
        text=f"{record.language}/{record.id}",
        text_sha256=record.text_sha256,
        output=record.reference or record.source,
        review=_review(record),
    )


def _run(records: tuple[TestRecord, ...], testset: str) -> RunFile:
    """Return a run of *records* of the test set *testset*, one answer each."""
    return RunFile(
        header=_header(records, testset),
        sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        answers=tuple(_answer(record) for record in records),
    )


def _manifest(records: tuple[TestRecord, ...], testset: str) -> Manifest:
    """Return a manifest that pins *records* as the test set *testset*."""
    return Manifest(
        testset=testset,
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


def _two_sets(root: Path) -> Path:
    """Write two test sets that both hold ``de-01``, each with a run of its own, and return *root*."""
    main = (_record(MAIN_SOURCE, MAIN_REFERENCE),)
    heldout = (_record(HELDOUT_SOURCE, HELDOUT_REFERENCE),)
    writer.write_testset(root, "gec-2026-12", main, _manifest(main, "gec-2026-12"))
    writer.write_testset(
        root, "gec-2026-12-heldout", heldout, _manifest(heldout, "gec-2026-12-heldout")
    )
    writer.write_run(layout.run_path(root, "main.run.jsonl"), _run(main, "gec-2026-12"))
    writer.write_run(
        layout.run_path(root, "heldout.run.jsonl"), _run(heldout, "gec-2026-12-heldout")
    )
    return root


def test_two_sets_that_hold_one_id_are_two_texts(tmp_path):
    """
    Given  a store of two test sets that both hold ``de-01``
    When   the page's model is built
    Then   each set's text is a text of its own, keyed by its set
    """
    report = model.build(load.load(_two_sets(tmp_path / "store")))

    assert sorted(report.texts) == ["gec-2026-12-heldout:de/de-01", "gec-2026-12:de/de-01"]
    assert [text.testset for text in report.texts.values()] == [
        "gec-2026-12",
        "gec-2026-12-heldout",
    ]


def test_each_text_is_diffed_against_its_own_source(tmp_path):
    """
    Given  a store of two test sets that both hold ``de-01``, with different sources
    When   the page's model is built
    Then   each text's rows hold its own source, and its own run's answer
    """
    report = model.build(load.load(_two_sets(tmp_path / "store")))

    main = report.texts["gec-2026-12:de/de-01"]
    heldout = report.texts["gec-2026-12-heldout:de/de-01"]
    assert "".join(main.tokens) == MAIN_SOURCE
    assert "".join(heldout.tokens) == HELDOUT_SOURCE
    assert len(report.items) == 2
    assert {item.text for item in report.items.values()} == set(report.texts)


def test_the_label_names_the_set_only_when_there_are_two(tmp_path):
    """
    Given  a store of two test sets, and one of a single set
    When   the page's model is built for each
    Then   the label carries the set only where a reader could confuse two texts
    """
    one = (_record(MAIN_SOURCE, MAIN_REFERENCE),)
    single = tmp_path / "single"
    writer.write_testset(single, "gec-2026-12", one, _manifest(one, "gec-2026-12"))
    writer.write_run(layout.run_path(single, "main.run.jsonl"), _run(one, "gec-2026-12"))

    both = model.build(load.load(_two_sets(tmp_path / "store")))
    alone = model.build(load.load(single))

    assert both.texts["gec-2026-12:de/de-01"].label == "gec-2026-12:de-01"
    assert alone.texts["gec-2026-12:de/de-01"].label == "de-01"


def test_a_text_of_another_set_is_not_answered_by_this_runs_answer(tmp_path):
    """
    Given  a store whose held-out set has no run
    When   the page's model is built
    Then   the held-out text has no version, because the run answered the other set's text
    """
    main = (_record(MAIN_SOURCE, MAIN_REFERENCE),)
    heldout = (_record(HELDOUT_SOURCE, HELDOUT_REFERENCE),)
    root = tmp_path / "store"
    writer.write_testset(root, "gec-2026-12", main, _manifest(main, "gec-2026-12"))
    writer.write_testset(
        root, "gec-2026-12-heldout", heldout, _manifest(heldout, "gec-2026-12-heldout")
    )
    writer.write_run(layout.run_path(root, "main.run.jsonl"), _run(main, "gec-2026-12"))

    report = model.build(load.load(root))

    assert [item.text for item in report.items.values()] == ["gec-2026-12:de/de-01"]
    assert report.texts["gec-2026-12-heldout:de/de-01"].ref_edits
