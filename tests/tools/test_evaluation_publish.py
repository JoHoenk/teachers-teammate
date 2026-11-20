"""Unit tests for the redacted export and the index, ``publish``.

An export is a plan first: it writes the layout it promises, its own manifest round-trips and keeps
the sets of an earlier export, the test set's manifest is copied byte for byte, a second export of
the same run says ``unchanged`` rather than merging, a run that grew is replaced, an incomplete run
is skipped, a run whose key moved is listed until ``--rescore`` replaces it, and an export that
fails halfway leaves no run file the index does not list.  The export reads back as a report with
the numbers the store scored, the index is stable and names the languages its mean covers, and
accepting a marker's marks moves the key hash and not the question hash.
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from tools.evaluation.core import protocol_table
from tools.evaluation.core.hashing import file_sha256
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.manifest import Manifest, ManifestItem, read_manifest
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
from tools.evaluation.metrics.offsets import token_offsets
from tools.evaluation.metrics.quality import ordered_edits
from tools.evaluation.publish import (
    INDEX_NAME,
    REPLACE,
    SKIPPED,
    STALE,
    UNCHANGED,
    WRITE,
    export,
    index_markdown,
    plan,
)
from tools.evaluation.report.load import load, load_export
from tools.evaluation.report.pooled import ItemFacts, TextItem
from tools.evaluation.report.score import SCORER_VERSION, score_row
from tools.evaluation.store import accept, layout, reader, writer
from tools.evaluation.store.redacted import (
    EXPORT_NAME,
    read_export_manifest,
    read_redacted_file,
)

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."
TESTSET = "gec-2026-12"
OTHER_TESTSET = "gec-2026-12-heldout"
FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]


def _record(**overrides) -> TestRecord:
    """Return a test record of one error text, with sensible defaults."""
    offsets = token_offsets(SOURCE)
    start, end, replacement = ordered_edits(SOURCE, REFERENCE)[0]
    values = {
        "id": "de-01",
        "language": "de",
        "source": SOURCE,
        "kind": "errors",
        "reference": REFERENCE,
        "labels": (
            ReferenceLabel(
                start=offsets[start],
                end=offsets[end],
                replacement="".join(replacement),
                code="R:VERB:FORM",
            ),
        ),
        "sentence_spans": ((0, len(SOURCE)),),
        "origin": "a test",
        "band": "short",
        "corpus": "falko-merlin",
    }
    values.update(overrides)
    return TestRecord(**values)


def _header(records: tuple[TestRecord, ...], **overrides) -> RunHeader:
    """Return a run header of *records*, with sensible defaults."""
    values = {
        "input": Input(
            testset=TESTSET,
            question_sha256=question_sha256(list(records)),
            texts=len(records),
            languages=("de",),
        ),
        "protocol": Protocol(model="gemma4:12b", model_digest="a" * 64, fingerprint=FINGERPRINT),
        "run": RunSettings(seed=20261109),
        "environment": Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    }
    values.update(overrides)
    return RunHeader(**values)


def _answer(record: TestRecord, **overrides) -> Answer:
    """Return a stored answer of *record*, with sensible defaults."""
    values = {
        "text": f"{record.language}/{record.id}",
        "text_sha256": record.text_sha256,
        "output": REFERENCE,
        "seconds": 1.5,
    }
    values.update(overrides)
    return Answer(**values)


def _run(records: tuple[TestRecord, ...], **overrides) -> RunFile:
    """Return a run file of *records*, with sensible defaults."""
    values = {
        "header": _header(records),
        "sessions": (Session(date="2026-11-14T23:22:00+00:00", tool_revision="ffa440f"),),
        "answers": tuple(_answer(record) for record in records),
    }
    values.update(overrides)
    return RunFile(**values)


def _manifest(
    records: tuple[TestRecord, ...],
    built: str = "2026-11-14",
    key_version: int = 1,
    testset: str = TESTSET,
) -> Manifest:
    """Return the manifest of *records*, as ``prepare_gec_testset`` writes it."""
    return Manifest(
        testset=testset,
        built=built,
        selection_seed=1,
        builder_revision="x",
        key_version=key_version,
        question_sha256=question_sha256(records),
        key_sha256=key_sha256(records),
        items=tuple(
            ManifestItem(
                id=record.id,
                language=record.language,
                kind=record.kind,
                band=record.band,
                corpus=record.corpus,
                pointer={},
                source={
                    "sha256": "0" * 64,
                    "words": len(record.source.split()),
                    "chars": len(record.source),
                },
                reference={"sha256": "1" * 64},
                edits=(),
            )
            for record in records
        ),
    )


def _store(root: Path, records: tuple[TestRecord, ...] | None = None) -> Path:
    """Write a store of one test set and one run below *root* and return the store's directory."""
    records = records or (_record(),)
    store = root / layout.STORE_DIR
    writer.write_testset(store, TESTSET, records, _manifest(records))
    writer.write_run(layout.run_path(store, "r.run.jsonl", "local"), _run(records))
    return store


def _add_set(store: Path, name: str, records: tuple[TestRecord, ...]) -> None:
    """Write another test set and one run of it below *store*."""
    writer.write_testset(store, name, records, _manifest(records, testset=name))
    header = _header(
        records,
        input=Input(
            testset=name,
            question_sha256=question_sha256(list(records)),
            texts=len(records),
            languages=("de",),
        ),
    )
    writer.write_run(
        layout.run_path(store, f"{name}.run.jsonl", "local"), _run(records, header=header)
    )


def _exported(tmp_path: Path) -> tuple[Path, Path]:
    """Return the store and the export of one run, both below *tmp_path*."""
    store = _store(tmp_path)
    out = tmp_path / "evaluations"
    export(store, out=out)
    return store, out


def _run_file(out: Path) -> Path:
    """Return the one run file of the export at *out*."""
    return next((out / layout.RUNS_DIR).glob(f"*{layout.RUN_SUFFIX}"))


def _two_text_store(tmp_path: Path, answers: int) -> tuple[Path, Path]:
    """Return a store of a two-text set whose one run holds *answers* of them, and its export dir."""
    records = (_record(), _record(id="de-02"))
    store = tmp_path / layout.STORE_DIR
    writer.write_testset(store, TESTSET, records, _manifest(records))
    writer.write_run(layout.run_path(store, "r.run.jsonl", "local"), _run(records[:answers]))
    return store, tmp_path / "evaluations"


def _three_runs(tmp_path: Path) -> Path:
    """Return a store of one set and three runs of it, one per repeat."""
    records = (_record(),)
    store = tmp_path / layout.STORE_DIR
    writer.write_testset(store, TESTSET, records, _manifest(records))
    for repeat in (1, 2, 3):
        header = _header(records, run=RunSettings(seed=20261108 + repeat, repeat=repeat))
        writer.write_run(
            layout.run_path(store, f"r{repeat}.run.jsonl", "local"),
            _run(records, header=header),
        )
    return store


# ── the layout ───────────────────────────────────────────────────────────────


def test_the_export_writes_the_layout_it_promises(tmp_path: Path) -> None:
    """
    Given  a store of one test set and one run
    When   it is exported
    Then   the export's own manifest, the copied set, the run file and the index are there, and
           the run file sits directly below ``runs/``
    """
    _, out = _exported(tmp_path)

    assert (out / EXPORT_NAME).is_file()
    assert (out / layout.TESTSETS_DIR / TESTSET / layout.MANIFEST_NAME).is_file()
    assert (out / INDEX_NAME).is_file()
    runs = sorted((out / layout.RUNS_DIR).rglob(f"*{layout.RUN_SUFFIX}"))
    assert len(runs) == 1
    assert runs[0].parent == out / layout.RUNS_DIR


def test_the_export_manifest_round_trips(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   its own manifest is read back
    Then   it names the set, what pins it, and the scorer that made its numbers
    """
    _, out = _exported(tmp_path)

    manifest = read_export_manifest(out / EXPORT_NAME)

    assert manifest.scorer_version == SCORER_VERSION
    assert manifest.exported
    entry = manifest.testsets[TESTSET]
    assert entry.texts == 1
    assert entry.question_sha256 == question_sha256([_record()])
    assert entry.key_sha256 == key_sha256([_record()])
    assert entry.manifest_sha256 == file_sha256(
        out / layout.TESTSETS_DIR / TESTSET / layout.MANIFEST_NAME
    )


def test_the_copied_manifest_is_the_store_s_manifest(tmp_path: Path) -> None:
    """
    Given  a store whose set has a manifest
    When   the export is read
    Then   the copy is the store's manifest, so its hash still matches a run header
    """
    store, out = _exported(tmp_path)

    copied = out / layout.TESTSETS_DIR / TESTSET / layout.MANIFEST_NAME

    assert copied.read_bytes() == layout.manifest_path(store, TESTSET).read_bytes()


def test_a_second_export_of_the_same_run_writes_nothing(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the same run is exported again
    Then   every entry says unchanged and the file is the one that was written
    """
    store, out = _exported(tmp_path)
    before = _run_file(out).read_bytes()

    again = export(store, out=out)

    assert [entry.action for entry in again.entries] == [UNCHANGED]
    assert _run_file(out).read_bytes() == before


def test_adding_one_run_writes_only_that_run(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   a second run of the same set is added to the store and exported
    Then   the new run is written and the first is unchanged
    """
    store, out = _exported(tmp_path)
    records = (_record(),)
    second = _run(records, header=_header(records, run=RunSettings(seed=20261110, repeat=2)))
    writer.write_run(layout.run_path(store, "r2.run.jsonl", "local"), second)

    written = export(store, out=out)

    assert sorted(entry.action for entry in written.entries) == [UNCHANGED, WRITE]
    assert len(list((out / layout.RUNS_DIR).glob(f"*{layout.RUN_SUFFIX}"))) == 2


def test_a_run_that_grew_replaces_its_file(tmp_path: Path) -> None:
    """
    Given  an export of a run that held one answer of a two-text set
    When   the run holds both answers and is exported again
    Then   the file is replaced, because the answers it held are the first of the new ones
    """
    store, out = _two_text_store(tmp_path, answers=1)
    export(store, out=out, partial=True)
    records = (_record(), _record(id="de-02"))
    writer.write_run(layout.run_path(store, "r.run.jsonl", "local"), _run(records))

    written = export(store, out=out)

    assert [entry.action for entry in written.entries] == [REPLACE]
    assert len(read_redacted_file(_run_file(out)).answers) == 2


def test_an_incomplete_run_is_skipped(tmp_path: Path) -> None:
    """
    Given  a store whose run holds one answer of a two-text set
    When   it is exported without ``--partial``
    Then   the run is skipped and no run file is written
    """
    store, out = _two_text_store(tmp_path, answers=1)

    written = export(store, out=out)

    assert [entry.action for entry in written.entries] == [SKIPPED]
    assert "incomplete" in written.entries[0].reason
    assert not list((out / layout.RUNS_DIR).glob(f"*{layout.RUN_SUFFIX}"))


def test_an_aborted_export_leaves_no_run_file_without_an_index_entry(tmp_path: Path) -> None:
    """
    Given  a store of three runs, one of whose target paths is a directory
    When   the export writes them
    Then   it raises, and every run file it did write is listed in the index
    """
    store = _three_runs(tmp_path)
    out = tmp_path / "evaluations"
    third = plan(load(store), out).entries[2]
    (out / layout.RUNS_DIR / third.name).mkdir(parents=True)

    with pytest.raises(OSError, match="Is a directory"):
        export(store, out=out)

    listed = (out / INDEX_NAME).read_text(encoding="utf-8")
    on_disk = sorted(
        path.name
        for path in (out / layout.RUNS_DIR).glob(f"*{layout.RUN_SUFFIX}")
        if path.is_file()
    )
    assert len(on_disk) == 2
    assert all(name in listed for name in on_disk)


def test_two_runs_of_two_folders_land_side_by_side(tmp_path: Path) -> None:
    """
    Given  a store whose two runs came from two old folders
    When   they are exported
    Then   both files sit directly below ``runs/``, because a folder is provenance and not format
    """
    records = (_record(),)
    store = tmp_path / layout.STORE_DIR
    writer.write_testset(store, TESTSET, records, _manifest(records))
    for folder, repeat in (("results-exchange", 1), ("results-text-first", 2)):
        header = _header(records, run=RunSettings(seed=20261108 + repeat, repeat=repeat))
        writer.write_run(
            layout.run_path(store, f"r{repeat}.run.jsonl", folder),
            _run(records, header=header),
        )
    out = tmp_path / "evaluations"

    export(store, out=out)

    runs = sorted((out / layout.RUNS_DIR).rglob(f"*{layout.RUN_SUFFIX}"))
    assert len(runs) == 2
    assert {path.parent for path in runs} == {out / layout.RUNS_DIR}


def test_a_rebuilt_set_under_the_same_id_is_refused(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   another text is put under the same set id and exported
    Then   the export refuses, because a committed set is never edited
    """
    store, out = _exported(tmp_path)

    rebuilt = (_record(source=SOURCE + " Auch.", reference=REFERENCE + " Auch."),)
    writer.write_testset(store, TESTSET, rebuilt, _manifest(rebuilt, built="2026-11-15"))

    with pytest.raises(ValueError, match="a committed set is never edited"):
        export(store, out=out)


def test_a_higher_key_version_replaces_the_committed_manifest(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the same texts are exported again under a higher key version
    Then   the committed manifest is replaced, because the same runs are scored against a better key
    """
    store, out = _exported(tmp_path)

    records = (_record(),)
    writer.write_testset(store, TESTSET, records, _manifest(records, key_version=2))

    export(store, out=out, label="v2")
    copied = read_manifest(out / layout.TESTSETS_DIR / TESTSET / layout.MANIFEST_NAME)
    assert copied.key_version == 2


def test_a_key_that_moved_without_a_version_is_refused(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the key of the same texts changes but its version does not
    Then   the export refuses, because the key moved without saying so
    """
    store, out = _exported(tmp_path)

    records = (_record(),)
    moved = replace(_manifest(records), key_sha256="e" * 64)
    writer.write_testset(store, TESTSET, records, moved)

    with pytest.raises(ValueError, match="key_version did not"):
        export(store, out=out, label="v2")


def test_an_export_of_one_set_keeps_the_sets_of_an_earlier_export(tmp_path: Path) -> None:
    """Given  an export of one test set
    When   another set of the store is exported
    Then   the export's manifest holds both, so the first set's runs stay readable
    """
    store, out = _exported(tmp_path)
    other = (_record(id="de-02"),)
    _add_set(store, OTHER_TESTSET, other)

    export(store, out=out, testsets=[OTHER_TESTSET])

    manifest = read_export_manifest(out / EXPORT_NAME)
    assert sorted(manifest.testsets) == sorted([TESTSET, OTHER_TESTSET])
    assert len(load_export(out).runs) == 2


def test_a_stale_run_is_listed_and_rescore_replaces_it(tmp_path: Path) -> None:
    """Given  an export of one run
    When   a marker's marks are accepted, which moves the key, and the run is exported again
    Then   the run is left alone and listed, and ``--rescore`` replaces it
    """
    store, out = _exported(tmp_path)
    records = (_record(),)
    writer.merge_marks(
        layout.marks_path(store, "joho", question_sha256(list(records))), _char_mark(_record())
    )
    accept.accept_marks(store, TESTSET, "joho")
    accepted = reader.read_testset(store, TESTSET)
    writer.write_testset(store, TESTSET, accepted, _manifest(accepted, key_version=2))

    written = export(store, out=out)

    assert [entry.action for entry in written.entries] == [STALE]
    stale = load_export(out)
    assert stale.runs == ()
    assert "--rescore" in stale.skipped[0].reason

    export(store, out=out, rescore=True)

    assert len(load_export(out).runs) == 1


def test_an_accepted_alternative_makes_only_the_runs_holding_that_text_stale(
    tmp_path: Path,
) -> None:
    """
    Given  an export of two runs of two sets, only one of which holds the marked text
    When   a marker's marks on that text are accepted, which moves that text's key part
    Then   only the run holding the text is stale, and the other is unchanged
    """
    store, out = _exported(tmp_path)
    _add_set(store, OTHER_TESTSET, (_record(id="de-02"),))
    export(store, out=out, testsets=[OTHER_TESTSET])
    records = (_record(),)
    writer.merge_marks(
        layout.marks_path(store, "joho", question_sha256(list(records))), _char_mark(_record())
    )
    accept.accept_marks(store, TESTSET, "joho")
    accepted = reader.read_testset(store, TESTSET)
    writer.write_testset(store, TESTSET, accepted, _manifest(accepted, key_version=2))

    written = export(store, out=out)

    actions = {entry.run.header.input.testset: entry.action for entry in written.entries}
    assert actions == {TESTSET: STALE, OTHER_TESTSET: UNCHANGED}


# ── the run file ─────────────────────────────────────────────────────────────


def test_the_run_file_holds_the_numbers_and_no_output(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the run file is read
    Then   its header repeats the scorer and the key, and its answer carries scores, not an output
    """
    _, out = _exported(tmp_path)
    path = next((out / layout.RUNS_DIR).rglob(f"*{layout.RUN_SUFFIX}"))

    run = read_redacted_file(path)

    assert run.header.scorer_version == SCORER_VERSION
    assert run.header.key_sha256 == key_sha256([_record()])
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert "scores" in line
    assert "output" not in line


def test_the_export_reads_back_with_the_numbers_the_store_scored(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the export is read as a report reads it
    Then   the one answer is placed, and its numbers are the ones the store's scoring gives
    """
    _, out = _exported(tmp_path)

    exported = load_export(out)

    assert exported.answers == 1
    assert not exported.skipped
    item = exported.items[0]
    assert (item.language, item.item, item.band) == ("de", "de-01", "short")
    assert item.scored == score_row(_answer(_record()), _record(), _header((_record(),)))


def test_an_exported_item_is_not_a_text_item(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   its item is asked whether it carries the text
    Then   it does not, so a table that shows the text cannot be built from an export
    """
    _, out = _exported(tmp_path)

    item = load_export(out).items[0]

    assert isinstance(item, ItemFacts)
    assert not isinstance(item, TextItem)


# ── the index ────────────────────────────────────────────────────────────────


def test_the_index_is_stable_and_names_the_run(tmp_path: Path) -> None:
    """
    Given  an export of one run
    When   the index is rendered twice
    Then   it is the same text, and it names the run and the hash of its file
    """
    _, out = _exported(tmp_path)

    first = index_markdown(out)
    second = index_markdown(out)

    assert first == second
    path = next((out / layout.RUNS_DIR).rglob(f"*{layout.RUN_SUFFIX}"))
    assert path.name in first
    assert file_sha256(path) in first
    assert (out / INDEX_NAME).read_text(encoding="utf-8") == first


def test_the_index_of_an_empty_export_says_so(tmp_path: Path) -> None:
    """
    Given  an export of a store that holds no run
    When   the index is rendered
    Then   it says there is no run yet, instead of an empty table
    """
    store = tmp_path / layout.STORE_DIR
    records = (_record(),)
    writer.write_testset(store, TESTSET, records, _manifest(records))
    out = tmp_path / "evaluations"
    export(store, out=out)

    assert "No runs yet." in index_markdown(out)


# ── accepting marks ──────────────────────────────────────────────────────────


def test_the_index_names_the_languages_the_mean_covers(tmp_path: Path) -> None:
    """Given  an export of one run
    When   the index is generated
    Then   it says the mean is over German and English, and that French is not averaged
    """
    _, out = _exported(tmp_path)

    text = index_markdown(out)

    assert (
        "`mean F0.5` is the mean over German and English; French is reported, not averaged." in text
    )


def _char_mark(record: TestRecord) -> tuple[Mark, ...]:
    """Return one marker's ``valid`` verdict on the edit the reference makes, in character offsets."""
    offsets = token_offsets(SOURCE)
    start, end, replacement = ordered_edits(SOURCE, REFERENCE)[0]
    return (
        Mark(
            kind="edit",
            text=f"{record.language}/{record.id}",
            span=(offsets[start], offsets[end]),
            replacement="".join(replacement),
            value="valid",
        ),
    )


def test_the_index_prints_the_axes_the_runs_differ_in(tmp_path: Path) -> None:
    """
    Given  an export of two runs that differ in temperature
    When   the index is rendered
    Then   it has a temperature column with the label a report prints, and no mode column
    """
    records = (_record(),)
    store = tmp_path / layout.STORE_DIR
    writer.write_testset(store, TESTSET, records, _manifest(records))
    for temperature in (0.0, 0.4):
        header = _header(
            records,
            protocol=Protocol(
                model="gemma4:12b",
                model_digest="a" * 64,
                fingerprint=FINGERPRINT,
                temperature=temperature,
            ),
        )
        writer.write_run(
            layout.run_path(store, f"t{temperature}.run.jsonl", "local"),
            _run(records, header=header),
        )
    out = tmp_path / "evaluations"

    export(store, out=out)

    text = index_markdown(out)
    assert "| temperature |" in text
    assert "temperature 0.4" in text
    assert "| Mode |" not in text


def _marks(record: TestRecord) -> tuple[Mark, ...]:
    """Return one marker's ``valid`` verdict on the edit the reference makes."""
    start, end, replacement = ordered_edits(SOURCE, REFERENCE)[0]
    return (
        Mark(
            kind="edit",
            text=f"{record.language}/{record.id}",
            span=(start, end),
            replacement="".join(replacement),
            value="valid",
        ),
    )


def test_accepting_marks_moves_the_key_and_not_the_question(tmp_path: Path) -> None:
    """
    Given  a store whose set has one marker's marks
    When   the marker's marks are accepted
    Then   the accepted file holds the edit, and the key moved while the question did not
    """
    store = _store(tmp_path)
    records = (_record(),)
    writer.merge_marks(
        layout.marks_path(store, "joho", question_sha256(list(records))), _marks(_record())
    )
    before = reader.read_testset(store, TESTSET)

    accepted = accept.accept_marks(store, TESTSET, "joho")

    assert accepted.edits == 1
    assert accepted.texts == 1
    assert layout.accepted_path(store, TESTSET).is_file()
    after = reader.read_testset(store, TESTSET)
    assert question_sha256(after) == question_sha256(before)
    assert key_sha256(after) != key_sha256(before)


def test_accepting_a_second_marker_keeps_the_first(tmp_path: Path) -> None:
    """Given  two markers whose marks name different edits
    When   each marker's marks are accepted in turn
    Then   the file holds both edits, each with the marker that accepted it
    """
    store = _store(tmp_path)
    records = (_record(),)
    question = question_sha256(list(records))
    first = _marks(_record())
    second = (
        Mark(
            kind="edit",
            text="de/de-01",
            span=(8, 11),
            replacement="ist",
            value="valid",
        ),
    )
    writer.merge_marks(layout.marks_path(store, "joho", question), first)
    writer.merge_marks(layout.marks_path(store, "typo", question), second)

    accept.accept_marks(store, TESTSET, "joho")
    accepted = accept.accept_marks(store, TESTSET, "typo")

    assert accepted.edits == 2
    assert accepted.added == (("de/de-01", (8, 11), "ist"),)
    assert accepted.removed == ()
    stored = reader.read_accepted(store, TESTSET)["de/de-01"]
    assert [(edit.span, edit.markers) for edit in stored] == [
        ((2, 3), ("joho",)),
        ((8, 11), ("typo",)),
    ]


def test_accepting_a_marker_again_replaces_only_its_own_edits(tmp_path: Path) -> None:
    """Given  two markers' accepted edits, one of which the first marker no longer marks
    When   the first marker's marks are accepted again
    Then   its dropped edit goes, the second marker's stays, and the change is listed
    """
    store = _store(tmp_path)
    records = (_record(),)
    question = question_sha256(list(records))
    kept = _marks(_record())
    dropped = (Mark(kind="edit", text="de/de-01", span=(8, 11), replacement="ist", value="valid"),)
    other = (Mark(kind="edit", text="de/de-01", span=(12, 15), replacement="mit", value="valid"),)
    writer.merge_marks(layout.marks_path(store, "joho", question), kept + dropped)
    writer.merge_marks(layout.marks_path(store, "typo", question), other)
    accept.accept_marks(store, TESTSET, "joho")
    accept.accept_marks(store, TESTSET, "typo")
    layout.marks_path(store, "joho", question).unlink()
    writer.merge_marks(layout.marks_path(store, "joho", question), kept)

    accepted = accept.accept_marks(store, TESTSET, "joho")

    assert accepted.removed == (("de/de-01", (8, 11), "ist"),)
    assert accepted.edits == 2
    stored = reader.read_accepted(store, TESTSET)["de/de-01"]
    assert [(edit.span, edit.markers) for edit in stored] == [
        ((2, 3), ("joho",)),
        ((12, 15), ("typo",)),
    ]


def test_a_duplicate_mark_is_accepted_once(tmp_path: Path) -> None:
    """Given  one marker whose marks file names the same edit twice
    When   the marks are accepted
    Then   the edit is one accepted alternative, not two
    """
    store = _store(tmp_path)
    records = (_record(),)
    question = question_sha256(list(records))
    twice = _marks(_record()) * 2
    writer.merge_marks(layout.marks_path(store, "joho", question), twice)

    accepted = accept.accept_marks(store, TESTSET, "joho")

    assert accepted.edits == 1
    assert accepted.added == (("de/de-01", (2, 3), "ist"),)


def test_accepting_the_marks_of_an_unknown_marker_is_refused(tmp_path: Path) -> None:
    """
    Given  a store whose set has one marker's marks
    When   another marker's marks are accepted
    Then   it is refused, so a typo cannot silently accept nothing
    """
    store = _store(tmp_path)
    records = (_record(),)
    writer.merge_marks(
        layout.marks_path(store, "joho", question_sha256(list(records))), _marks(_record())
    )

    with pytest.raises(KeyError, match="no marks of 'typo'"):
        accept.accept_marks(store, TESTSET, "typo")
