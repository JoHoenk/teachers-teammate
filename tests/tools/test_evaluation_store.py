"""Unit tests for the store layer: the layout, the reader and writer, validation and the marks.

The acceptance tests of the converter live in ``test_evaluation_convert_v1``: a run file
round-trips through the reader,
an appended answer is a line and not a rewrite, a test set is written per language and read back in
the table's order, every rule of the format is refused with a message a person can act on, and a
mark's identity is what two markers can disagree about.

Nothing here reads the real store: the records are built in the test, so a failure names the rule
that broke and not a file on somebody's disk.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from tools.evaluation.core import protocol_table
from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.hashing import sha256_text
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem
from tools.evaluation.input.corpora.invented_fr import InventedFrench
from tools.evaluation.input.testset import Corpora, build_manifest, resolve, to_record
from tools.evaluation.core.records import (
    AcceptedEdit,
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
from tools.evaluation.store import layout, marks, reader, validate, writer

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."

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


def _header(records: tuple[TestRecord, ...], **changes) -> RunHeader:
    """Return the header of a run of *records*, with *changes* applied."""
    header = RunHeader(
        input=Input(
            testset="gec-2026-12",
            question_sha256=question_sha256(records),
            texts=len(records),
            languages=("de",),
        ),
        protocol=Protocol(model="gemma4:12b", model_digest="a" * 64, fingerprint=FINGERPRINT),
        run=RunSettings(seed=20261109),
        environment=Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    )
    return replace(header, **changes) if changes else header


def _answer(record: TestRecord, **changes) -> Answer:
    """Return one answer of *record*, with *changes* applied."""
    answer = Answer(
        text=f"{record.language}/{record.id}", text_sha256=record.text_sha256, output=REFERENCE
    )
    return replace(answer, **changes) if changes else answer


def _run(records: tuple[TestRecord, ...], **changes) -> RunFile:
    """Return a run of *records* with one answer, with *changes* applied to the file."""
    run = RunFile(
        header=_header(records),
        sessions=(Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        answers=(_answer(records[0]),),
    )
    return replace(run, **changes) if changes else run


# ── the layout ──────────────────────────────────────────────────────────────


def test_the_store_root_is_found_by_the_testsets_it_holds(tmp_path):
    """
    Given  a path below a directory that holds ``testsets/``, and one that holds none
    When   the store root is asked for
    Then   the directory with the test sets is returned whatever it is called, so a clone of the
           private repository may have any name, and a path outside a store is its own root
    """
    root = tmp_path / "a-clone"
    (root / "testsets").mkdir(parents=True)
    inside = root / "runs" / "results-exchange" / "a.run.jsonl"

    assert layout.store_root(inside) == root
    assert layout.store_root(root) == root
    assert layout.store_root(tmp_path / "elsewhere") == tmp_path / "elsewhere"


def test_the_layout_names_every_file_of_the_store(tmp_path):
    """
    Given  a store root
    When   the paths of a test set, a run and a marks file are asked for
    Then   each sits in its own part of the store, and a marks file names its marker and question
    """
    root = tmp_path / "store"

    assert layout.testset_file(root, "gec-2026-12", "de") == (
        root / "testsets" / "gec-2026-12" / "de.jsonl"
    )
    assert layout.manifest_path(root, "gec-2026-12") == (
        root / "testsets" / "gec-2026-12" / "manifest.json"
    )
    assert layout.run_path(root, "a.run.jsonl", "results-exchange") == (
        root / "runs" / "results-exchange" / "a.run.jsonl"
    )
    path = layout.marks_path(root, "extra-edits", "b" * 64)
    assert path == root / "marks" / f"extra-edits__{'b' * 64}.jsonl"
    assert layout.marker_of(path) == "extra-edits"
    assert layout.question_of(path) == "b" * 64


def test_the_layout_lists_what_the_store_holds(tmp_path):
    """
    Given  a store with two test sets and two folders of runs
    When   the sets, the folders and the missing names are asked for
    Then   each is listed in name order, and a name the store does not have is reported
    """
    root = tmp_path / "store"
    for testset in ("gec-2026-12", "gec-2026-12-heldout"):
        layout.testset_dir(root, testset).mkdir(parents=True)
    for folder in ("results-text", "results-exchange"):
        layout.run_dir(root, folder).mkdir(parents=True)
    (root / "runs" / "results-exchange" / "a.run.jsonl").write_text("{}\n", encoding="utf-8")

    assert layout.testsets(root) == ["gec-2026-12", "gec-2026-12-heldout"]
    assert layout.run_folders(root) == ["results-exchange", "results-text"]
    assert layout.run_files(root) == [root / "runs" / "results-exchange" / "a.run.jsonl"]
    assert layout.missing(root, ["gec-2026-12", "nope"]) == ["nope"]


def test_the_languages_of_a_set_are_read_in_the_table_order(tmp_path):
    """
    Given  a test set whose files were written in another order than the table's
    When   its languages are asked for
    Then   they come back in the table's order, so two readers see the same sequence
    """
    root = tmp_path / "store"
    directory = layout.testset_dir(root, "gec-2026-12")
    directory.mkdir(parents=True)
    for language in ("fr", "de", "en"):
        (directory / f"{language}.jsonl").write_text("", encoding="utf-8")

    assert layout.languages_of(root, "gec-2026-12") == ["de", "en", "fr"]


# ── the reader and the writer ───────────────────────────────────────────────


def test_a_test_set_round_trips_through_the_store(tmp_path):
    """
    Given  a test set and its manifest
    When   it is written and read back
    Then   the records are the ones written, in the table's order, and the manifest is unchanged
    """
    root = tmp_path / "store"
    records = (_record(), _record(id="de-02", language="de"))
    manifest = Manifest(testset="gec-2026-12", question_sha256=question_sha256(records))

    writer.write_testset(root, "gec-2026-12", records, manifest)

    assert reader.read_testset(root, "gec-2026-12") == list(records)
    assert reader.read_manifest(root, "gec-2026-12") == manifest
    assert reader.read_testset_by_id(root, "gec-2026-12") == {
        "de/de-01": records[0],
        "de/de-02": records[1],
    }


def test_a_run_file_round_trips_through_the_store(tmp_path):
    """
    Given  a run with a session and an answer
    When   it is written and read back
    Then   the header, the session and the answer are the ones written
    """
    records = (_record(),)
    run = _run(records)
    path = layout.run_path(tmp_path / "store", "a.run.jsonl", "results-exchange")

    writer.write_run(path, run)

    assert reader.read_run(path) == run
    assert reader.read_runs(tmp_path / "store")[0].folder == "results-exchange"


def test_an_appended_answer_is_a_line_and_not_a_rewrite(tmp_path):
    """
    Given  a run whose header was written and whose first answer was appended
    When   a second answer is appended
    Then   the file holds the header and both answers, and the header line is unchanged
    """
    records = (_record(), _record(id="de-02"))
    run = _run(records)
    path = layout.run_path(tmp_path / "store", "a.run.jsonl")
    writer.start_run(path, run)
    writer.append_answer(path, _answer(records[0]))
    first_line = path.read_text(encoding="utf-8").splitlines()[0]

    writer.append_answers(path, [_answer(records[1])])

    read = reader.read_run(path)
    assert read.header == run.header
    assert [answer.text for answer in read.answers] == ["de/de-01", "de/de-02"]
    assert path.read_text(encoding="utf-8").splitlines()[0] == first_line


def test_a_marks_file_round_trips_through_the_store(tmp_path):
    """
    Given  two marks of one marker on one set
    When   they are written and read back
    Then   they are the ones written, and the store lists them under their marker
    """
    root = tmp_path / "store"
    found = [
        Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid"),
        Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="hat", value="wrong"),
    ]
    path = layout.marks_path(root, "extra-edits", "b" * 64)

    writer.merge_marks(path, found)

    assert reader.read_marks(path) == found
    assert reader.read_all_marks(root) == {"extra-edits": found}


def test_marking_a_second_runs_sheet_keeps_the_first(tmp_path):
    """
    Given  one marks file, marked once for one run's sheet and once for another's
    When   the second sheet's marks are written
    Then   both sets of marks are in the file, so marking a run does not erase the last one
    """
    path = layout.marks_path(tmp_path / "store", "extra-edits", "b" * 64)
    first = [Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")]
    second = [Mark(kind="edit", text="de/de-02", span=(0, 7), replacement="Gestern", value="wrong")]

    writer.merge_marks(path, first)
    change = writer.merge_marks(path, second)

    assert reader.read_marks(path) == first + second
    assert (change.added, change.changed, change.kept) == (1, 0, 1)


def test_a_mark_of_an_edit_that_is_already_there_is_replaced(tmp_path):
    """
    Given  a marks file that holds one verdict on one edit
    When   the same edit is marked again with another verdict
    Then   the file holds one mark, the new one, and the change says it replaced one
    """
    path = layout.marks_path(tmp_path / "store", "extra-edits", "b" * 64)
    before = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")
    after = replace(before, value="wrong")
    writer.merge_marks(path, [before])

    change = writer.merge_marks(path, [after])

    assert reader.read_marks(path) == [after]
    assert (change.added, change.changed, change.kept) == (0, 1, 0)


def test_a_manifest_that_is_not_a_json_object_is_refused(tmp_path):
    """
    Given  a manifest file that holds a list
    When   it is read
    Then   the reader refuses it, naming the file
    """
    root = tmp_path / "store"
    path = layout.manifest_path(root, "gec-2026-12")
    path.parent.mkdir(parents=True)
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(FormatError, match="a manifest is a JSON object"):
        reader.read_manifest(root, "gec-2026-12")


def test_a_notes_file_in_a_set_folder_is_not_a_language(tmp_path):
    """
    Given  a test set whose folder holds a language file, the accepted file and a person's notes
    When   the languages of the set are asked for
    Then   only the language file counts, so a note beside it is not read as a language
    """
    root = tmp_path / "store"
    records = (_record(),)
    manifest = Manifest(testset="gec-2026-12", question_sha256=question_sha256(records))
    writer.write_testset(root, "gec-2026-12", records, manifest)
    (layout.testset_dir(root, "gec-2026-12") / "notes.jsonl").write_text("", encoding="utf-8")

    assert layout.languages_of(root, "gec-2026-12") == ["de"]


# ── validation ──────────────────────────────────────────────────────────────


def test_a_run_checked_without_its_set_passes_the_format_check():
    """
    Given  a run file and no records of the set it names
    When   it is checked
    Then   only its format is checked, and a note says the texts were not
    """
    records = (_record(),)
    run = _run(records)

    assert validate.problems_of_run(run.header, run.answers, {}) == []
    assert validate.notes_of_run("gec-2026-12", {}) == [
        "test set gec-2026-12 is not in the store; texts not checked"
    ]
    assert validate.notes_of_run("gec-2026-12", {"de/de-01": records[0]}) == []


def test_a_run_that_names_no_test_set_is_reported():
    """
    Given  a run whose header names no test set
    When   it is checked
    Then   the missing name is reported, so a run cannot be filed under nothing
    """
    records = (_record(),)
    run = _run(
        records, header=replace(_header(records), input=replace(_header(records).input, testset=""))
    )

    assert "the run names no test set" in validate.problems_of_run(run.header, run.answers, {})


def test_a_good_test_set_passes_validation():
    """
    Given  a test record whose label is an edit of its own reference
    When   it is validated
    Then   nothing is reported
    """
    records = (_record(),)

    assert validate.problems_of_testset(records) == []


def test_a_duplicate_id_is_reported():
    """
    Given  a set that holds the same id twice
    When   it is validated
    Then   the duplicate is reported, because an answer could not find its text
    """
    records = (_record(), _record())

    assert "de-01: the id appears twice" in validate.problems_of_testset(records)


def test_an_unknown_kind_is_reported():
    """
    Given  a record whose kind is neither errors nor correct
    When   it is validated
    Then   the kind is reported
    """
    records = (_record(kind="poetry"),)

    assert any("kind 'poetry'" in problem for problem in validate.problems_of_testset(records))


def test_a_label_that_is_no_longer_an_edit_is_reported():
    """
    Given  a record whose label names a replacement the reference does not make
    When   it is validated
    Then   the label is listed as unmatched, never dropped
    """
    label = replace(_label(SOURCE, REFERENCE), replacement="etwas anderes")
    records = (_record(labels=(label,)),)

    assert any(
        "is no longer an edit of the reference" in problem
        for problem in validate.problems_of_testset(records)
    )


def test_a_span_off_the_token_boundaries_is_reported():
    """
    Given  a record whose label starts inside a word
    When   it is validated
    Then   the span is reported, because the scorer could not map it to tokens
    """
    label = replace(_label(SOURCE, REFERENCE), start=1)
    records = (_record(labels=(label,)),)

    assert any(
        "does not lie on token boundaries" in problem
        for problem in validate.problems_of_testset(records)
    )


def test_a_span_outside_the_text_is_reported():
    """
    Given  a record whose sentence span reaches past its source
    When   it is validated
    Then   the span is reported with the length of the text
    """
    records = (_record(sentence_spans=((0, len(SOURCE) + 5),)),)

    assert any(
        "is outside the text" in problem for problem in validate.problems_of_testset(records)
    )


def test_an_unchanged_sentence_index_out_of_range_is_reported():
    """
    Given  a record that calls sentence 3 unchanged while it has one span
    When   it is validated
    Then   the index is reported
    """
    records = (_record(unchanged_sentences=(3,)),)

    assert any(
        "unchanged sentence 3" in problem for problem in validate.problems_of_testset(records)
    )


def test_a_freshly_built_set_passes_validation():
    """
    Given  a set built by the builder every writer uses
    When   the records are validated against its manifest
    Then   nothing is reported, so the tools accept the sets they write
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    pointer = {"text": "fr-01"}
    item = {
        "id": "fr-01",
        "language": "fr",
        "kind": "errors",
        "corpus": "invented-fr",
        "band": "short",
        "pointer": pointer,
    }
    manifest = build_manifest(
        [item],
        corpora,
        testset="gec-2026-12",
        built="2026-01-01",
        selection_seed=1,
        builder_revision="test",
    )
    records = (to_record(resolve(item, corpora), band="short", pointer=pointer),)

    assert validate.problems_of_testset(records, manifest) == []


def test_a_manifest_that_pins_another_set_is_reported():
    """
    Given  a manifest whose question hash is not the records'
    When   the set is validated against it
    Then   the hash is reported, so a rebuilt set is not mistaken for this one
    """
    records = (_record(),)
    manifest = Manifest(
        testset="gec-2026-12",
        question_sha256="c" * 64,
        items=(ManifestItem(id="de-01", language="de", kind="errors"),),
    )

    assert any(
        "question_sha256" in problem for problem in validate.problems_of_testset(records, manifest)
    )


def test_a_manifest_that_lists_another_text_is_reported():
    """
    Given  a manifest that lists a text the set does not have and omits one it has
    When   the set is validated against it
    Then   both are reported
    """
    records = (_record(),)
    manifest = Manifest(
        testset="gec-2026-12",
        question_sha256=question_sha256(records),
        items=(ManifestItem(id="de-99", language="de", kind="errors"),),
    )

    found = validate.problems_of_testset(records, manifest)
    assert "de-01: the record is not in the manifest" in found
    assert "de-99: the manifest lists a text the set does not have" in found


def test_a_manifest_whose_text_hash_is_not_the_records_is_reported():
    """
    Given  a manifest entry whose text hash belongs to another text
    When   the set is validated against it
    Then   the hash is reported
    """
    records = (_record(),)
    manifest = Manifest(
        testset="gec-2026-12",
        question_sha256=question_sha256(records),
        items=(ManifestItem(id="de-01", language="de", kind="errors", text_sha256="d" * 64),),
    )

    assert any(
        "text hash is not the record's" in problem
        for problem in validate.problems_of_testset(records, manifest)
    )


def test_a_good_run_passes_validation():
    """
    Given  a run of a set, whose answer belongs to that set
    When   it is validated
    Then   nothing is reported
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records)

    assert validate.problems_of_run(run.header, run.answers, by_text) == []


def test_a_run_of_another_set_is_reported():
    """
    Given  a run whose question hash is not the set's
    When   it is validated
    Then   the run is reported as asked about another set
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records, header=_header(records, input=Input("gec-2026-12", "e" * 64, 1, ("de",))))

    assert any(
        "asked about another set" in problem
        for problem in validate.problems_of_run(run.header, run.answers, by_text)
    )


def test_an_answer_of_another_question_is_reported():
    """
    Given  an answer whose text hash is not the record's
    When   it is validated
    Then   the answer is reported as belonging to another question
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records, answers=(_answer(records[0], text_sha256="f" * 64),))

    assert any(
        "belongs to another question" in problem
        for problem in validate.problems_of_run(run.header, run.answers, by_text)
    )


def test_a_text_answered_twice_is_reported():
    """
    Given  a run that answers one text twice
    When   it is validated
    Then   the duplicate is reported
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records, answers=(_answer(records[0]), _answer(records[0])))

    assert "de/de-01: the text is answered twice" in validate.problems_of_run(
        run.header, run.answers, by_text
    )


def test_an_answer_of_a_text_the_set_does_not_have_is_reported():
    """
    Given  a run that answers a text the set does not hold
    When   it is validated
    Then   the answer is reported
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records, answers=(_answer(records[0], text="de/de-99"),))

    assert "de/de-99: the answer is not a text of the set" in validate.problems_of_run(
        run.header, run.answers, by_text
    )


def test_an_unrecorded_protocol_is_reported():
    """
    Given  a run whose fingerprint the table does not record
    When   it is validated
    Then   the protocol is reported, because nobody can say what it used
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    protocol = replace(_header(records).protocol, fingerprint="0" * 64)
    run = _run(records, header=_header(records, protocol=protocol))

    assert any(
        "is not in the table" in problem
        for problem in validate.problems_of_run(run.header, run.answers, by_text)
    )


def test_an_ollama_run_without_a_machine_is_reported():
    """
    Given  an Ollama run whose machine is empty and says nothing about it
    When   it is validated
    Then   the missing machine is reported, because its speed says nothing about a machine
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    run = _run(records, header=_header(records, environment=Environment()))

    assert any(
        "names the machine" in problem
        for problem in validate.problems_of_run(run.header, run.answers, by_text)
    )


def test_an_assumed_machine_is_accepted():
    """
    Given  an Ollama run whose machine was assumed and says so
    When   it is validated
    Then   nothing is reported, because the assumption is recorded
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    environment = Environment(machine="rx9070xt-server", machine_source="assumed by convert_v1: x")
    run = _run(records, header=_header(records, environment=environment))

    assert validate.problems_of_run(run.header, run.answers, by_text) == []


def test_a_mark_of_a_text_the_set_does_not_have_is_reported():
    """
    Given  a mark that names a text the set does not hold
    When   the marks are validated
    Then   the mark is reported
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    found = [Mark(kind="edit", text="de/de-99", span=(0, 1), replacement="x", value="valid")]

    assert "de/de-99: the mark names a text the set does not have" in validate.problems_of_marks(
        found, by_text
    )


def test_a_mark_off_the_token_boundaries_is_reported():
    """
    Given  a mark whose span starts inside a word
    When   the marks are validated
    Then   the span is reported
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    found = [Mark(kind="edit", text="de/de-01", span=(1, 7), replacement="x", value="valid")]

    assert any(
        "does not lie on token boundaries" in problem
        for problem in validate.problems_of_marks(found, by_text)
    )


def test_a_mark_no_answer_makes_any_more_is_listed():
    """
    Given  a mark whose edit no stored answer makes
    When   the unmatched marks are asked for
    Then   the mark is listed, never dropped
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    kept = Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="ist", value="valid")
    moved = Mark(kind="edit", text="de/de-01", span=(8, 12), replacement="ist", value="valid")

    answers = {"de/de-01": [_answer(records[0])]}
    assert validate.unmatched_marks([kept, moved], answers, by_text) == [moved]


def _review(span: tuple[int, int], corrected: str, explanation: str) -> CorrectionReview:
    """Return a review of one edit, explained by *explanation*."""
    return CorrectionReview(
        edits=(
            EditNote(
                id=1,
                parts=(
                    EditPart(
                        raw_start=span[0],
                        raw_end=span[1],
                        original=SOURCE[span[0] : span[1]],
                        corrected=corrected,
                    ),
                ),
                sentences=(0,),
                category="grammar",
                explanation=explanation,
            ),
        )
    )


def test_an_explanation_mark_is_matched_only_on_its_own_explanation():
    """
    Given  two models' answers that make one edit, explained differently, and a mark of one of them
    When   the unmatched marks are asked for
    Then   the mark is matched where its explanation is, and unmatched where only the other is
    """
    records = (_record(),)
    by_text = {f"{record.language}/{record.id}": record for record in records}
    mine = _answer(records[0], review=_review((0, 7), "Gestern", "the verb comes second"))
    other = _answer(records[0], review=_review((0, 7), "Gestern", "a different reason"))
    mark = Mark(
        kind="explanation",
        text="de/de-01",
        span=(0, 7),
        replacement="Gestern",
        value="wrong",
        explanation_sha256=sha256_text("the verb comes second"),
    )

    assert validate.unmatched_marks([mark], {"de/de-01": [mine, other]}, by_text) == []
    assert validate.unmatched_marks([mark], {"de/de-01": [other]}, by_text) == [mark]


def test_checked_refuses_a_broken_file_with_a_readable_message():
    """
    Given  a set that holds the same id twice
    When   it is checked
    Then   a ValidationError names the first problem
    """
    records = (_record(), _record())

    with pytest.raises(validate.ValidationError, match="the test set is not valid"):
        validate.checked_testset(records)


# ── the marks ───────────────────────────────────────────────────────────────


def test_a_mark_is_identified_by_its_text_span_and_replacement():
    """
    Given  two marks of the same edit and one of another
    When   they are grouped by identity
    Then   the two are one entry, so a disagreement is one edit with two verdicts
    """
    first = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")
    second = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="wrong")
    other = Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="ist", value="valid")

    assert marks.identity(first) == ("de/de-01", (0, 7), "Gestern", "")
    assert marks.edit_identity(first) == ("de/de-01", (0, 7), "Gestern")
    assert len(marks.by_identity([first, second, other])) == 2
    assert marks.by_text([first, other]) == {"de/de-01": [first, other]}


def test_a_disagreement_is_listed_with_both_markers():
    """
    Given  one edit called valid by one marker and wrong by another
    When   the disagreements are asked for
    Then   the edit is listed with both markers, and an edit only one marker judged is not
    """
    agreed = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")
    disputed = Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="ist", value="valid")
    refused = replace(disputed, value="wrong")

    assert marks.disagreements({"a": [agreed, disputed], "b": [refused]}) == [
        (("de/de-01", (8, 11), "ist"), ["a", "b"])
    ]


def test_accepted_takes_the_valid_edits_no_other_marker_called_wrong():
    """
    Given  a marker whose two valid edits another marker called wrong once
    When   its accepted alternatives are asked for
    Then   only the edit nobody refused is accepted, as a key edit
    """
    kept = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")
    refused = Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="ist", value="valid")
    vague = Mark(kind="edit", text="de/de-01", span=(12, 14), replacement="ins", value="vague")

    found = marks.accepted(
        {"a": [kept, refused, vague], "b": [replace(refused, value="wrong")]}, "a"
    )

    assert found == [AcceptedEdit(span=(0, 7), replacement="Gestern")]


def test_accepted_by_text_groups_the_alternatives_by_their_text():
    """
    Given  a marker with one valid edit on each of two texts
    When   its accepted alternatives are grouped by text
    Then   each text holds its own alternative
    """
    first = Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")
    second = Mark(kind="edit", text="de/de-02", span=(0, 4), replacement="Heute", value="valid")

    assert marks.accepted_by_text({"a": [first, second]}, "a") == {
        "de/de-01": [AcceptedEdit(span=(0, 7), replacement="Gestern")],
        "de/de-02": [AcceptedEdit(span=(0, 4), replacement="Heute")],
    }


def test_accepted_refuses_a_marker_the_store_does_not_have():
    """
    Given  a store whose only marks belong to another marker
    When   the accepted alternatives of a misspelt marker are asked for
    Then   the call is refused, so a typo cannot silently accept nothing
    """
    found = [Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")]

    with pytest.raises(KeyError, match="no marks of 'extra-edit'"):
        marks.accepted({"extra-edits": found}, "extra-edit")


def test_an_accepted_alternative_moves_the_key_hash_and_not_the_question_hash():
    """
    Given  a test record and the same record with an accepted alternative
    When   both hashes are taken
    Then   the key hash moves and the question hash does not, so no run is invalidated
    """
    record = _record()
    accepted = replace(record, accepted=(AcceptedEdit(span=(0, 7), replacement="Gestern"),))

    assert question_sha256([accepted]) == question_sha256([record])
    assert key_sha256([accepted]) != key_sha256([record])


def test_a_mark_holds_no_text_of_the_corpus(tmp_path):
    """
    Given  a mark of an edit
    When   it is written
    Then   the line holds the identity and the verdict, and no source or reference text
    """
    path = layout.marks_path(tmp_path / "store", "extra-edits", "b" * 64)
    found = [Mark(kind="edit", text="de/de-01", span=(0, 7), replacement="Gestern", value="valid")]

    writer.merge_marks(path, found)

    line = path.read_text(encoding="utf-8")
    assert SOURCE not in line
    assert REFERENCE not in line
    assert '"value": "valid"' in line
