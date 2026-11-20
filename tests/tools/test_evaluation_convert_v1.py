"""The converter: the old ``results-*`` folders and ``set-2026-12`` read as they are.

Two halves.  The unit tests build a small old store in a temporary directory and check every rule
the converter applies — the hash it recomputes, the positions it maps, the steps it infers, the
machine it refuses to guess, the files it refuses to read.  The acceptance tests run the
conversion over the real folders and check the result against the old rows they were read from;
they are skipped unless ``GEC_RESULTS`` names the old store and ``GEC_EVALS`` the private store
that holds the manifests, because both are outside the repository::

    bazel test //tests:test_evaluation_convert_v1 --test_output=errors \\
      --test_env=GEC_RESULTS=$HOME/gec-testset \\
      --test_env=GEC_EVALS=$HOME/git/teachers-teammate-evals

The three hashes need no old data — they are a property of the records — so that part runs
everywhere.
"""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module names by design

from __future__ import annotations

from dataclasses import replace
import ast
import itertools
import json
import os
from pathlib import Path
import re

import pytest
from teachers_teammate.domain.proofreading.review import align
from teachers_teammate.domain.proofreading.word_diff import tokenize

from tools.evaluation.core import axes, protocol_table
from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.hashing import sha256_text
from tools.evaluation.core.modes import (
    ALL_STEPS,
    AnswerMode,
    Exchange,
    exchange_and_steps,
    parse_mode,
)
from tools.evaluation.core.records import (
    AcceptedEdit,
    Protocol,
    TestRecord,
    key_sha256,
    question_sha256,
)
from tools.evaluation.core.text import word_count
from tools.evaluation.input.corpora.invented_fr import InventedFrench
from tools.evaluation.input.testset import Corpora, build_manifest, resolve, to_record
from tools.evaluation.metrics.offsets import char_span_to_tokens, text_under
from tools.evaluation.report.score import strip_added_marks
from tools.evaluation.run.protocol import instructions_sha256
from tools.evaluation.convert import v1 as convert_v1
from tools.evaluation.store import layout, reader, validate

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."
OTHER_SOURCE = "Er geht."
OTHER_REFERENCE = "Er läuft."

FINGERPRINT = protocol_table.fingerprints_of(protocol_table.current_version())[0]
"""A fingerprint the table records, so a converted run is not refused for an unknown protocol."""

GEC_RESULTS = os.environ.get("GEC_RESULTS")
"""The old store, when the acceptance tests are to run; never set in CI."""

GEC_EVALS = os.environ.get("GEC_EVALS")
"""The private store that holds the manifests, when the acceptance tests are to run."""

needs_results = pytest.mark.skipif(
    not (GEC_RESULTS and GEC_EVALS),
    reason="the old runs and the private store are outside the repository; "
    "set GEC_RESULTS and GEC_EVALS",
)

OLD_ROOT = Path(GEC_RESULTS).expanduser() if GEC_RESULTS else Path()
"""The old store's directory, which the acceptance tests read and never write."""

MANIFESTS = {
    name: Path(GEC_EVALS or ".").expanduser() / "testsets" / name / "manifest.json"
    for name in ("gec-2026-12", "gec-2026-12-heldout")
}
"""The private store's manifests of the two sets the in-scope runs were asked about."""


# ── an old store, built in the test ─────────────────────────────────────────


def _old_line(**changes) -> dict:
    """Return one old test record of ``de-01``, with *changes* applied."""
    line = {
        "schema_version": 1,
        "id": "de-01",
        "language": "de",
        "kind": "errors",
        "origin": "a test",
        "source": SOURCE,
        "reference": REFERENCE,
        "sentences": [SOURCE],
        "labels": [{"start": 2, "end": 3, "replacement": ["ist"], "code": "R:VERB:FORM"}],
        "band": "short",
        "sentence_spans": [[0, 18]],
        "unchanged_sentences": [],
        "corpus": "falko-merlin",
        "pointer": {"file": "x.txt", "line": 3},
    }
    return {**line, **changes}


def _old_other(**changes) -> dict:
    """Return one old test record of ``de-02``, with *changes* applied."""
    line = {
        "schema_version": 1,
        "id": "de-02",
        "language": "de",
        "kind": "errors",
        "origin": "a test",
        "source": OTHER_SOURCE,
        "reference": OTHER_REFERENCE,
        "sentences": [OTHER_SOURCE],
        "labels": [{"start": 2, "end": 3, "replacement": ["läuft"], "code": "R:VERB:FORM"}],
        "band": "short",
        "sentence_spans": [[0, 4]],
        "unchanged_sentences": [],
        "corpus": "falko-merlin",
        "pointer": {"file": "y.txt", "line": 7},
    }
    return {**line, **changes}


def _manifest_item(line: dict) -> dict:
    """Return the manifest's entry for one old record: its meta and its hashes."""
    source = line["source"]
    item = {
        "id": line["id"],
        "language": line["language"],
        "kind": line["kind"],
        "corpus": line["corpus"],
        "band": line["band"],
        "pointer": line["pointer"],
        "source": {
            "sha256": sha256_text(source),
            "words": word_count(source),
            "chars": len(source),
        },
        "reference": {"sha256": sha256_text(line["reference"])},
    }
    if "level" in line:
        item["level"] = line["level"]
    return item


def _manifest(*lines: dict) -> dict:
    """Return the manifest of *lines*, as ``prepare_gec_testset`` writes it."""
    return {
        "format": 2,
        "testset": "set-2026-12",
        "built": "2026-11-01",
        "selection_seed": 7,
        "builder_revision": "abc1234",
        "corpora": {"falko-merlin": "1.0"},
        "items": [_manifest_item(line) for line in lines],
    }


def _old_header(**changes) -> dict:
    """Return one old header session, with *changes* applied."""
    header = {
        "date": "2026-11-14T23:22:00+00:00",
        "tool_revision": "ffa440f",
        "eval_version": 4,
        "fingerprint": FINGERPRINT,
        "instructions_sha256": "",
        "answer_format_version": 1,
        "provider": "ollama",
        "machine": "rx9070xt-server",
        "ollama_version": "0.40.16",
        "model": "gemma4:12b",
        "model_info": {
            "digest": "a" * 64,
            "quantization": "Q4_K_M",
            "parameter_size": "12B",
            "family": "gemma",
        },
        "mode": "exchange",
        "temperature": 0.0,
        "request_profile": {"compact_json": True, "temperature": 0.0},
        "num_ctx": 8192,
        "timeout": 900.0,
        "seed": 20261109,
        "repeat": 1,
        "isolated": False,
        "gpu_sampler": {
            "kind": "nvidia",
            "vendor": "nvidia",
            "source": "nvidia-smi",
            "agent_sha256": "b" * 64,
        },
        "testset": "set-2026-12",
        "testset_sha256": "",
        "texts": 2,
        "loaded_before": ["gemma4:12b"],
        "vram_baseline_mb": 512,
    }
    return {**header, **changes}


def _request(**changes) -> dict:
    """Return one old request record, with *changes* applied."""
    request = {
        "model": "gemma4:12b",
        "prompt_tokens": 100,
        "answer_tokens": 20,
        "load_s": 1.0,
        "prompt_eval_s": 0.5,
        "generation_s": 2.0,
        "total_s": 3.5,
        "wall_s": 4.0,
        "done_reason": "stop",
        "failed": False,
    }
    return {**request, **changes}


def _old_row(text: str, output: str, **changes) -> dict:
    """Return one old result row of *text*, with *changes* applied."""
    row = {
        "schema_version": 1,
        "id": text,
        "model": "gemma4:12b",
        "temperature": 0.0,
        "language": "de",
        "kind": "errors",
        "origin": "a test",
        "seconds": 12.5,
        "failed": False,
        "fallback": False,
        "has_review": True,
        "source": SOURCE,
        "output": output,
        "warning": "",
        "reference": REFERENCE,
        "notes": [],
        "changes": [{"edit": [2, 3, "ist"], "category": "grammar", "explanation": "Verbform."}],
        "unclear": 0,
        "explained": 1,
        "needed": 1,
        "orphans": 0,
        "true_positives": 1,
        "false_positives": 0,
        "false_negatives": 0,
        "sentences": [SOURCE],
        "unchanged": 0,
        "band": "short",
        "scores": {},
        "mode": "exchange",
        "pieces": 1,
        "performance": {
            "requests": [_request(), _request(), _request()],
            "loaded": [
                {
                    "name": "gemma4:12b",
                    "size_bytes": 8_000_000_000,
                    "size_vram_bytes": 7_500_000_000,
                    "context_length": 8192,
                }
            ],
            "shared": False,
            "vram_peak_mb": 9000,
            "vram_baseline_mb": 512,
            "seed": 20261109,
            "repeat": 1,
        },
    }
    return {**row, **changes}


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    """Write *lines* to *path* as JSONL, the way the old store holds them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8"
    )


@pytest.fixture
def old_root(tmp_path: Path) -> Path:
    """An old store of one test set and one run folder, as the converter reads it."""
    root = tmp_path / "old"
    lines = [_old_line(), _old_other()]
    _write_jsonl(root / "set-2026-12" / "de.jsonl", lines)
    (root / "manifest.json").write_text(json.dumps(_manifest(*lines)), encoding="utf-8")
    header = _old_header(testset_sha256=convert_v1.old_records_sha256(lines))
    (root / "results-exchange").mkdir()
    (root / "results-exchange" / "a.header.json").write_text(json.dumps([header]), encoding="utf-8")
    _write_jsonl(
        root / "results-exchange" / "a.jsonl",
        [
            _old_row("de/de-01", REFERENCE),
            _old_row(
                "de/de-02",
                OTHER_REFERENCE,
                source=OTHER_SOURCE,
                reference=OTHER_REFERENCE,
                sentences=[OTHER_SOURCE],
                changes=[{"edit": [2, 3, "läuft"], "category": "grammar", "explanation": "Verb."}],
            ),
        ],
    )
    return root


@pytest.fixture
def converted(old_root: Path, tmp_path: Path) -> tuple[Path, convert_v1.ConversionReport]:
    """The old store converted into a new one, and what the conversion reported."""
    root = tmp_path / "store"
    report = convert_v1.convert(
        old_root, root, manifests={"gec-2026-12": old_root / "manifest.json"}
    )
    return root, report


def _protocol(**changes) -> Protocol:
    """Return the protocol of an exchange run, with *changes* applied."""
    protocol = Protocol(model="gemma4:12b", exchange=Exchange.TEXT_FIRST, steps=ALL_STEPS)
    return replace(protocol, **changes) if changes else protocol


# ── the old test set ────────────────────────────────────────────────────────


def test_the_old_hash_is_recomputed_from_the_old_records():
    """
    Given  two old records
    When   their hash is recomputed
    Then   it is the hash of their ``to_dict`` output, which is what the old headers recorded
    """
    lines = [_old_line(), _old_other()]
    expected = [
        {
            "schema_version": 1,
            "id": line["id"],
            "language": line["language"],
            "kind": line["kind"],
            "origin": line["origin"],
            "source": line["source"],
            "reference": line["reference"],
            "sentences": line["sentences"],
            "labels": line["labels"],
            "band": line["band"],
            "sentence_spans": line["sentence_spans"],
        }
        for line in lines
    ]

    assert convert_v1.old_records_sha256(lines) == sha256_text(
        json.dumps(expected, sort_keys=True, ensure_ascii=False)
    )


def test_the_old_hash_leaves_out_the_empty_optional_fields():
    """
    Given  a record that carries an empty band and empty spans
    When   it is hashed
    Then   it hashes like the record that never had those fields, so an old set keeps its hash
    """
    bare = _old_line()
    for name in ("band", "sentence_spans", "unchanged_sentences"):
        bare.pop(name)
    empty = _old_line(band="", sentence_spans=[], unchanged_sentences=[])

    assert convert_v1.old_records_sha256([bare]) == convert_v1.old_records_sha256([empty])


def test_a_record_maps_its_token_positions_to_characters():
    """
    Given  an old record whose label and sentence span are token indices
    When   it is converted
    Then   both spans are character offsets into the same text, and the replacement is joined
    """
    line = _old_line()

    record = convert_v1._record_of(line)

    assert record.labels[0].start == SOURCE.index("hat")
    assert record.labels[0].end == SOURCE.index("hat") + len("hat")
    assert record.labels[0].replacement == "ist"
    assert record.sentence_spans == ((0, len(SOURCE)),)
    assert record.corpus == "falko-merlin"
    assert record.pointer == {"file": "x.txt", "line": 3}


def test_a_converted_manifest_item_holds_character_positions(tmp_path):
    """
    Given  a manifest whose item positions are not the record's
    When   the set is converted
    Then   the item's spans are the record's characters, and its hashes are the record's
    """
    line = _old_line()
    item = _manifest_item(line)
    item["sentence_spans"] = [[0, 1]]
    item["edits"] = [{"start": 0, "end": 1, "h": "0" * 16, "words": 1, "label": "R:VERB:FORM"}]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({**_manifest(line), "items": [item]}), encoding="utf-8")
    record = convert_v1._record_of(line)

    manifest = convert_v1._manifest_of(path, "set-2026-12", (record,))

    item = manifest.items[0]
    assert item.sentence_spans == ((0, len(SOURCE)),)
    assert item.edits[0]["start"] == SOURCE.index("hat")
    assert item.edits[0]["h"] == sha256_text("ist")
    assert item.text_sha256 == record.text_sha256
    assert item.source["sha256"] == sha256_text(SOURCE)
    assert item.source["words"] == word_count(SOURCE)
    assert item.source["chars"] == len(SOURCE)


def test_a_converted_manifest_keeps_the_level_and_the_correct_sentences(tmp_path):
    """
    Given  an old manifest whose items carry a level and a correct text's sentences
    When   the set is converted
    Then   both come through, so the converted set is the one the old tools pinned
    """
    errors = _old_line(level="B1")
    correct = _old_line(
        id="de-correct", kind="correct", reference=SOURCE, labels=[], unchanged_sentences=[0]
    )
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest(errors, correct)), encoding="utf-8")
    records = (convert_v1._record_of(errors), convert_v1._record_of(correct))

    manifest = convert_v1._manifest_of(path, "set-2026-12", records)

    assert manifest.items[0].level == "B1"
    assert manifest.items[1].sentence_sha256 == (sha256_text(SOURCE),)
    assert not manifest.items[1].reference


def test_the_converter_and_the_builder_write_the_same_item(tmp_path):
    """
    Given  one text, pinned by the builder of a new set and by the converter of an old one
    When   both manifests are read
    Then   the items are equal, so a converted set and a rebuilt one are the same file
    """
    corpora = Corpora({"invented-fr": InventedFrench.DEFAULT_ROOT})
    pointer = {"text": "fr-01"}
    resolved = resolve(
        {
            "id": "fr-01",
            "language": "fr",
            "kind": "errors",
            "corpus": "invented-fr",
            "pointer": pointer,
        },
        corpora,
    )
    record = to_record(resolved, band="short", pointer=pointer)
    built = build_manifest(
        [
            {
                "id": "fr-01",
                "language": "fr",
                "kind": "errors",
                "corpus": "invented-fr",
                "band": "short",
                "pointer": pointer,
            }
        ],
        corpora,
        testset="gec-2026-12",
        built="2026-01-01",
        selection_seed=1,
        builder_revision="test",
    )
    old = {
        "format": 2,
        "testset": "gec-2026-12",
        "built": "2026-01-01",
        "selection_seed": 1,
        "builder_revision": "test",
        "corpora": {},
        "items": [
            {
                "id": "fr-01",
                "language": "fr",
                "kind": "errors",
                "corpus": "invented-fr",
                "band": "short",
                "pointer": pointer,
                "source": {"sha256": sha256_text(record.source)},
                "reference": {"sha256": sha256_text(record.reference)},
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(old), encoding="utf-8")

    converted = convert_v1._manifest_of(path, "gec-2026-12", (record,))

    assert converted.items == built.items
    assert converted.question_sha256 == built.question_sha256
    assert converted.key_sha256 == built.key_sha256


def test_a_manifest_of_another_format_is_refused(tmp_path):
    """
    Given  a manifest of the old schema, which pinned token positions
    When   the set is converted
    Then   the manifest is refused, naming the old schema
    """
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema": 1, "items": []}), encoding="utf-8")

    with pytest.raises(FormatError, match="the old schema 1"):
        convert_v1._manifest_of(path, "gec-2026-12", ())


def test_a_manifest_that_lists_a_text_the_set_has_not_is_refused(tmp_path):
    """
    Given  a manifest that lists a text the records do not hold
    When   the set is converted
    Then   the entry is refused, because the two are not the same set
    """
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(_manifest(_old_other())), encoding="utf-8")

    with pytest.raises(FormatError, match="the set has not"):
        convert_v1._manifest_of(path, "gec-2026-12", (convert_v1._record_of(_old_line()),))


def test_a_manifest_whose_text_hash_differs_is_refused(tmp_path):
    """
    Given  a manifest whose text hash belongs to another text
    When   the set is converted
    Then   the hash is refused, so a rebuilt set is not mistaken for this one
    """
    item = _manifest_item(_old_line())
    item["source"] = {**item["source"], "sha256": "c" * 64}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({**_manifest(), "items": [item]}), encoding="utf-8")

    with pytest.raises(FormatError, match="the manifest's text hash is not the record's"):
        convert_v1._manifest_of(path, "gec-2026-12", (convert_v1._record_of(_old_line()),))


def test_a_directory_without_records_is_refused(tmp_path):
    """
    Given  a directory that holds no ``<language>.jsonl``
    When   its records are asked for
    Then   the directory is refused, naming it
    """
    directory = tmp_path / "set-2026-12"
    directory.mkdir()

    with pytest.raises(FormatError, match=r"no <language>\.jsonl test records"):
        convert_v1.old_testset_lines(directory)


# ── the old run files ───────────────────────────────────────────────────────


def test_machine_evidence_holds_every_reading_of_a_machine():
    """
    Given  two headers of one machine, one of them without a sampler, and one of another
    When   the evidence is collected
    Then   each machine appears once with every reading it was seen with, so an older run that
           recorded no sampler does not hide the newer one's
    """
    headers = [
        _old_header(),
        _old_header(date="2026-11-15T10:00:00+00:00", gpu_sampler={}),
        _old_header(machine="other", ollama_version="0.40.10"),
        _old_header(machine=""),
    ]

    assert convert_v1.machine_evidence(headers) == {
        "rx9070xt-server": frozenset({("0.40.16", "b" * 64), ("0.40.16", "")}),
        "other": frozenset({("0.40.10", "b" * 64)}),
    }


def test_the_steps_of_a_tagged_run_are_kept():
    """
    Given  requests that already carry their step
    When   they are converted
    Then   the steps are kept and nothing is marked as inferred
    """
    performance = {"requests": [_request(step="correct"), _request(step="explain")]}

    requests, inferred = convert_v1._requests_of(performance, _protocol(), 1)

    assert [request.step for request in requests] == ["correct", "explain"]
    assert inferred is False


def test_the_steps_of_an_untagged_run_are_inferred_when_the_count_matches():
    """
    Given  three untagged requests of a three-step exchange
    When   they are converted
    Then   the steps are the exchange's, in order, and the answer says they were inferred
    """
    performance = {"requests": [_request(), _request(), _request()]}

    requests, inferred = convert_v1._requests_of(performance, _protocol(), 1)

    assert [request.step for request in requests] == ["correct", "explain", "unclear"]
    assert inferred is True


def test_the_steps_of_a_two_piece_run_are_inferred_per_piece():
    """
    Given  six untagged requests of a three-step exchange sent in two pieces
    When   they are converted
    Then   the steps repeat per piece, which is the order the exchange runs them in
    """
    performance = {"requests": [_request() for _ in range(6)]}

    requests, inferred = convert_v1._requests_of(performance, _protocol(), 2)

    assert [request.step for request in requests] == [
        "correct",
        "explain",
        "unclear",
        "correct",
        "explain",
        "unclear",
    ]
    assert inferred is True


def test_the_steps_of_an_untagged_run_stay_empty_when_the_count_does_not_match():
    """
    Given  four untagged requests of a three-step exchange
    When   they are converted
    Then   the steps stay empty, because the count does not say which request was which
    """
    performance = {"requests": [_request() for _ in range(4)]}

    requests, inferred = convert_v1._requests_of(performance, _protocol(), 1)

    assert [request.step for request in requests] == ["", "", "", ""]
    assert inferred is False


def test_a_text_run_is_tagged_text():
    """
    Given  an untagged request of a run that asked for the corrected text only
    When   it is converted
    Then   the step is ``text``, which is the one request that exchange makes
    """
    protocol = _protocol(exchange=Exchange.ONE_REQUEST_TEXT, steps=())
    performance = {"requests": [_request()]}

    requests, inferred = convert_v1._requests_of(performance, protocol, 1)

    assert [request.step for request in requests] == ["text"]
    assert inferred is False


def test_a_text_answered_in_one_request_has_no_review():
    """
    Given  a row of a run that asked for the corrected text only
    When   its review is converted
    Then   there is none, because the run asked for no explanations
    """
    protocol = _protocol(exchange=Exchange.ONE_REQUEST_TEXT, steps=())

    assert convert_v1._review_of([], SOURCE, protocol) is None


def test_a_review_is_one_note_per_change_and_an_orphan_has_no_parts():
    """
    Given  a change and an orphan
    When   they are converted
    Then   each is one note, the change carries its span in characters, the orphan carries none
    """
    changes = [
        {"edit": [2, 3, "ist"], "category": "grammar", "explanation": "Verbform."},
        {"edit": None, "category": "style", "explanation": "Nichts geändert.", "orphan": True},
    ]

    review = convert_v1._review_of(changes, SOURCE, _protocol())

    assert review is not None
    assert [note.id for note in review.edits] == [1, 2]
    assert review.edits[0].parts[0].raw_start == SOURCE.index("hat")
    assert review.edits[0].parts[0].original == "hat"
    assert review.edits[0].parts[0].corrected == "ist"
    assert review.edits[0].category == "grammar"
    assert review.edits[1].parts == ()
    assert review.edits[1].explanation == "Nichts geändert."


def test_an_empty_machine_is_filled_when_the_evidence_agrees():
    """
    Given  a header with no machine and the evidence of the machine it ran on
    When   the environment is converted with that machine assumed
    Then   the machine is filled and the assumption is written down and noted
    """
    header = _old_header(machine="")
    evidence = {"rx9070xt-server": frozenset({("0.40.16", "b" * 64)})}
    notes: list[str] = []

    environment = convert_v1._environment_of(header, "rx9070xt-server", evidence, notes)

    assert environment.machine == "rx9070xt-server"
    assert environment.machine_source.startswith("assumed by convert_v1: ollama 0.40.16")
    assert notes == ["the machine rx9070xt-server was assumed from the evidence"]


def test_an_empty_machine_stays_empty_when_the_evidence_differs():
    """
    Given  a header whose server version is not the assumed machine's
    When   the environment is converted with that machine assumed
    Then   the machine stays empty and the refusal is noted, so nothing is guessed
    """
    header = _old_header(machine="", ollama_version="0.40.10")
    evidence = {"rx9070xt-server": frozenset({("0.40.16", "b" * 64)})}
    notes: list[str] = []

    environment = convert_v1._environment_of(header, "rx9070xt-server", evidence, notes)

    assert environment.machine == ""
    assert environment.machine_source == ""
    assert notes == [
        "the machine rx9070xt-server was not assumed: the evidence (ollama 0.40.10, "
        "sampler bbbbbbbb) does not match"
    ]


def test_a_named_machine_is_kept_as_it_is():
    """
    Given  a header that names its machine
    When   the environment is converted with another machine assumed
    Then   the header's own machine wins and nothing is noted
    """
    notes: list[str] = []

    environment = convert_v1._environment_of(
        _old_header(), "other", {"other": ("0.40.16", "b" * 64)}, notes
    )

    assert environment.machine == "rx9070xt-server"
    assert environment.machine_source == ""
    assert notes == []


def test_a_header_that_is_not_a_list_of_sessions_is_refused(tmp_path):
    """
    Given  a header file that holds an object
    When   it is read
    Then   the file is refused, because an old header is a list of sessions
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps({"date": "2026-11-14"}), encoding="utf-8")

    with pytest.raises(FormatError, match="a non-empty list of sessions"):
        convert_v1._read_sessions(path)


def test_a_run_of_another_set_is_refused(tmp_path):
    """
    Given  a header whose recorded test set hash is not the records'
    When   the run is converted
    Then   the run is refused, so a run of another set is never read as this one
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps([_old_header(testset_sha256="d" * 64)]), encoding="utf-8")
    rows = tmp_path / "a.jsonl"
    _write_jsonl(rows, [_old_row("de/de-01", REFERENCE)])
    records = (convert_v1._record_of(_old_line()),)

    with pytest.raises(FormatError, match="the run was asked about another set"):
        convert_v1.convert_run(
            path,
            rows,
            {"gec-2026-12": records},
            old_sha256=convert_v1.old_records_sha256([_old_line()]),
        )


def test_a_row_of_another_set_is_refused(tmp_path):
    """
    Given  a row whose id is not a text of the set
    When   the run is converted
    Then   the row is refused, naming the file
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps([_old_header()]), encoding="utf-8")
    rows = tmp_path / "a.jsonl"
    _write_jsonl(rows, [_old_row("de/de-99", REFERENCE)])
    records = (convert_v1._record_of(_old_line()),)

    with pytest.raises(FormatError, match="'de/de-99' is not a text of the set"):
        convert_v1.convert_run(path, rows, {"gec-2026-12": records})


def test_a_run_of_a_set_that_was_not_converted_is_refused(tmp_path):
    """
    Given  a header of a test set the caller did not convert
    When   the run is converted
    Then   the run is refused, so a run is never read against the wrong key
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps([_old_header()]), encoding="utf-8")
    rows = tmp_path / "a.jsonl"
    _write_jsonl(rows, [_old_row("de/de-01", REFERENCE)])

    with pytest.raises(FormatError, match="the test set 'gec-2026-12' was not converted"):
        convert_v1.convert_run(path, rows, {})


def test_the_instructions_hook_is_checked_against_the_stored_hash(tmp_path):
    """
    Given  a header whose stored instructions hash is not what the hook computes
    When   the run is converted
    Then   the hash is refused, so a changed prompt cannot pass as the old one
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps([_old_header(instructions_sha256="e" * 64)]), encoding="utf-8")
    rows = tmp_path / "a.jsonl"
    _write_jsonl(rows, [_old_row("de/de-01", REFERENCE)])
    records = (convert_v1._record_of(_old_line()),)

    with pytest.raises(FormatError, match="is not what the hook computes"):
        convert_v1.convert_run(
            path, rows, {"gec-2026-12": records}, instructions=lambda header, languages: "f" * 64
        )


def test_without_a_hook_the_instruction_hashes_are_empty_and_noted(tmp_path):
    """
    Given  a run converted without an instructions hook
    When   it is converted
    Then   the per-language hashes are empty and the report says why
    """
    path = tmp_path / "a.header.json"
    path.write_text(json.dumps([_old_header()]), encoding="utf-8")
    rows = tmp_path / "a.jsonl"
    _write_jsonl(rows, [_old_row("de/de-01", REFERENCE)])
    records = (convert_v1._record_of(_old_line()),)

    run = convert_v1.convert_run(path, rows, {"gec-2026-12": records})

    assert run.run.header.protocol.instructions == ()
    assert any("no hook was given" in note for note in run.notes)


def test_a_converted_run_names_itself_from_its_axes(tmp_path):
    """
    Given  an old run of one text
    When   it is converted
    Then   its new name is the label of its axes, and it remembers the folder and name it came from
    """
    path = tmp_path / "results-exchange" / "gemma4_12b__T0__exchange__r1.header.json"
    path.parent.mkdir()
    path.write_text(json.dumps([_old_header()]), encoding="utf-8")
    rows = path.with_name("gemma4_12b__T0__exchange__r1.jsonl")
    _write_jsonl(rows, [_old_row("de/de-01", REFERENCE)])
    records = (convert_v1._record_of(_old_line()),)

    run = convert_v1.convert_run(path, rows, {"gec-2026-12": records})

    assert run.folder == "results-exchange"
    assert run.old_name == "gemma4_12b__T0__exchange__r1"
    assert run.new_name == axes.run_file_name(run.run.header, "2026-11-14")
    assert run.answers == 1
    assert run.sessions == 1


def test_the_old_name_of_an_unknown_test_set_is_refused():
    """
    Given  a test set id the converter does not know
    When   its old folder is asked for
    Then   the call is refused and names the sets it does know
    """
    with pytest.raises(KeyError, match="no old folder for the test set 'nope'"):
        convert_v1._old_name_of("nope")


# ── the whole conversion ────────────────────────────────────────────────────


def test_a_conversion_writes_the_sets_and_the_runs(converted):
    """
    Given  an old store of one set and one run
    When   it is converted
    Then   the set, its manifest and the run are in the store, and the report counts them
    """
    root, report = converted

    assert report.testsets == (("gec-2026-12", 2),)
    assert len(report.runs) == 1
    assert report.answers == 2
    assert layout.testset_file(root, "gec-2026-12", "de").is_file()
    assert layout.manifest_path(root, "gec-2026-12").is_file()
    assert layout.run_files(root) == [
        layout.run_path(root, report.runs[0].new_name, "results-exchange")
    ]


def test_a_conversion_lists_the_folders_it_did_not_convert(converted):
    """
    Given  an old store that holds one of the seven folders
    When   it is converted
    Then   the six it does not hold are listed with a reason, so nothing disappears silently
    """
    _, report = converted

    assert [where for where, _ in report.skipped] == [
        folder for folder in convert_v1.IN_SCOPE if folder != "results-exchange"
    ]
    assert {why for _, why in report.skipped} == {"the folder is not there"}


def test_a_conversion_skips_a_header_without_rows(old_root, tmp_path):
    """
    Given  a header with no rows file beside it
    When   the store is converted
    Then   the header is listed as skipped, and the run that has rows is converted
    """
    (old_root / "results-exchange" / "b.header.json").write_text(
        json.dumps([_old_header()]), encoding="utf-8"
    )

    report = convert_v1.convert(
        old_root, tmp_path / "store", manifests={"gec-2026-12": old_root / "manifest.json"}
    )

    assert len(report.runs) == 1
    assert ("no rows file beside the header") in {why for _, why in report.skipped}


def test_a_conversion_skips_a_run_of_a_set_it_did_not_convert(old_root, tmp_path):
    """
    Given  a run of the held-out set, which the caller did not convert
    When   the store is converted
    Then   the run is listed as skipped, naming the set
    """
    header = _old_header(testset="set-2026-12-heldout")
    (old_root / "results-exchange" / "b.header.json").write_text(
        json.dumps([header]), encoding="utf-8"
    )
    _write_jsonl(old_root / "results-exchange" / "b.jsonl", [_old_row("de/de-01", REFERENCE)])

    report = convert_v1.convert(
        old_root, tmp_path / "store", manifests={"gec-2026-12": old_root / "manifest.json"}
    )

    assert len(report.runs) == 1
    assert "the test set 'gec-2026-12-heldout' was not converted" in {
        why for _, why in report.skipped
    }


def test_two_old_runs_of_one_name_are_refused_before_anything_is_written(old_root, tmp_path):
    """
    Given  two old runs of one setup, a copy of one run in another folder
    When   the store is converted
    Then   the conversion is refused, naming both files, and the store is left empty
    """
    header = _old_header(testset_sha256=convert_v1.old_records_sha256([_old_line(), _old_other()]))
    (old_root / "results-text").mkdir()
    (old_root / "results-text" / "b.header.json").write_text(json.dumps([header]), encoding="utf-8")
    _write_jsonl(old_root / "results-text" / "b.jsonl", [_old_row("de/de-01", REFERENCE)])
    root = tmp_path / "store"

    with pytest.raises(ValueError, match=r"results-exchange/a\.header\.json, results-text/b"):
        convert_v1.convert(old_root, root, manifests={"gec-2026-12": old_root / "manifest.json"})
    assert not root.exists()


def test_a_run_left_out_on_request_is_not_converted(old_root, tmp_path):
    """
    Given  two old runs of one name, one of them left out on request
    When   the store is converted
    Then   the other is converted, and the one left out is listed with the reason
    """
    header = _old_header(testset_sha256=convert_v1.old_records_sha256([_old_line(), _old_other()]))
    (old_root / "results-text").mkdir()
    (old_root / "results-text" / "b.header.json").write_text(json.dumps([header]), encoding="utf-8")
    _write_jsonl(old_root / "results-text" / "b.jsonl", [_old_row("de/de-01", REFERENCE)])

    report = convert_v1.convert(
        old_root,
        tmp_path / "store",
        manifests={"gec-2026-12": old_root / "manifest.json"},
        skip=("b.header.json",),
    )

    assert len(report.runs) == 1
    assert ("left out on request") in {why for _, why in report.skipped}


def test_a_run_left_out_is_named_by_its_path_below_the_old_root(old_root, tmp_path):
    """
    Given  two old runs of one name, one of them left out by its path below the old root
    When   the store is converted
    Then   the other is converted, and the one left out is listed with the reason
    """
    header = _old_header(testset_sha256=convert_v1.old_records_sha256([_old_line(), _old_other()]))
    (old_root / "results-text").mkdir()
    (old_root / "results-text" / "b.header.json").write_text(json.dumps([header]), encoding="utf-8")
    _write_jsonl(old_root / "results-text" / "b.jsonl", [_old_row("de/de-01", REFERENCE)])

    report = convert_v1.convert(
        old_root,
        tmp_path / "store",
        manifests={"gec-2026-12": old_root / "manifest.json"},
        skip=("results-text/b.header.json",),
    )

    assert len(report.runs) == 1
    assert [where for where, why in report.skipped if why == "left out on request"] == [
        str(old_root / "results-text" / "b.header.json")
    ]


def test_a_conversion_is_idempotent(old_root, tmp_path):
    """
    Given  an old store
    When   it is converted twice into two stores
    Then   every file is byte-identical, so a second conversion is not a change
    """
    manifests = {"gec-2026-12": old_root / "manifest.json"}
    first = tmp_path / "first"
    second = tmp_path / "second"

    convert_v1.convert(old_root, first, manifests=manifests)
    convert_v1.convert(old_root, second, manifests=manifests)

    written = sorted(path.relative_to(first) for path in first.rglob("*") if path.is_file())
    assert written
    for relative in written:
        assert (first / relative).read_bytes() == (second / relative).read_bytes()


def test_a_converted_run_passes_validation(converted):
    """
    Given  a converted store
    When   its set and its run are validated
    Then   nothing is reported, so the conversion wrote a store the reader accepts
    """
    root, _ = converted
    records = reader.read_testset(root, "gec-2026-12")
    by_text = {f"{record.language}/{record.id}": record for record in records}

    assert validate.problems_of_testset(records, reader.read_manifest(root, "gec-2026-12")) == []
    for stored in reader.read_runs(root):
        assert validate.problems_of_run(stored.run.header, stored.run.answers, by_text) == []


# ── the three hashes, which need no old data ────────────────────────────────


def _record(**changes) -> TestRecord:
    """Return a small converted test record, with *changes* applied."""
    record = TestRecord(
        id="de-01",
        language="de",
        source=SOURCE,
        kind="errors",
        reference=REFERENCE,
        labels=(),
        sentence_spans=((0, len(SOURCE)),),
        origin="a test",
        band="short",
        corpus="falko-merlin",
    )
    return replace(record, **changes) if changes else record


def test_a_changed_reference_moves_the_key_and_not_the_question():
    """
    Given  a record and the same record with another reference
    When   both hashes are taken
    Then   the question hash stays and the key hash moves, so no run is invalidated
    """
    record = _record()
    changed = replace(record, reference="Gestern ist sie ins Kino gegangen.")

    assert question_sha256([changed]) == question_sha256([record])
    assert key_sha256([changed]) != key_sha256([record])


def test_a_changed_source_moves_the_question():
    """
    Given  a record and the same record with another source
    When   both hashes are taken
    Then   the question hash moves, because the run was asked about another text
    """
    record = _record()
    changed = replace(record, source="Gestern hat sie das Kino gegangen mit sein Freundin.")

    assert question_sha256([changed]) != question_sha256([record])


def test_an_accepted_alternative_moves_neither_the_question_nor_the_text():
    """
    Given  a record and the same record with an accepted alternative
    When   the hashes are taken
    Then   the question hash and the text hash stay and only the key hash moves
    """
    record = _record()
    accepted = replace(record, accepted=(AcceptedEdit(span=(0, 7), replacement="Gestern"),))

    assert question_sha256([accepted]) == question_sha256([record])
    assert accepted.text_sha256 == record.text_sha256
    assert key_sha256([accepted]) != key_sha256([record])


# ── the acceptance tests, on the real folders ───────────────────────────────


_instructions = convert_v1.instructions_hook(instructions_sha256)
"""The converter's instructions hook, from the run layer's own hash function."""


_LEFT_OUT = (
    "results-text/gemma4_e2b__T1__text__r1.header.json",
    "results-text/nemotron-3.5-bolt_e4b__T1__text__r1.header.json",
)
"""Two runs the isolated folder holds again: the same answers, only the timings differ.

The old tool counted repeats per folder, so both carry repeat 1 and ask for one new name.  The
isolated folder's copy is the one kept: it is the newer one and it carries the sampler's evidence.
"""


@pytest.fixture(scope="module")
def real(tmp_path_factory) -> tuple[Path, convert_v1.ConversionReport]:
    """The old store converted into a new one, once for every acceptance test."""
    root = tmp_path_factory.mktemp("store")
    report = convert_v1.convert(
        OLD_ROOT,
        root,
        manifests=MANIFESTS,
        instructions=_instructions,
        assume_machine="rx9070xt-server",
        skip=_LEFT_OUT,
    )
    return root, report


_OLD_ROWS: dict[str, dict[str, list[dict]]] = {}
"""The old rows of each folder, read once: a folder holds a hundred runs of the same texts."""


def _old_rows(folder: str) -> dict[str, list[dict]]:
    """Return the old rows of every run of *folder*, keyed by the run's old name."""
    if folder not in _OLD_ROWS:
        found: dict[str, list[dict]] = {}
        for path in sorted((OLD_ROOT / folder).glob("*.jsonl")):
            found[path.stem] = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        _OLD_ROWS[folder] = found
    return _OLD_ROWS[folder]


@needs_results
def test_every_old_row_becomes_one_answer(real):
    """
    Given  the old folders and the converted store
    When   the answers are counted per run and per folder
    Then   every row is one answer and every run is one file, and the only runs left out are the
           two the isolated folder holds again
    """
    _, report = real

    assert {where for where, _ in report.skipped} == {str(OLD_ROOT / name) for name in _LEFT_OUT}
    assert {why for _, why in report.skipped} == {"left out on request"}
    for folder in convert_v1.IN_SCOPE:
        old = _old_rows(folder)
        converted = [run for run in report.runs if run.folder == folder]
        left_out = [name for name in _LEFT_OUT if name.startswith(f"{folder}/")]
        assert len(converted) == len(old) - len(left_out), folder
        for run in converted:
            assert run.answers == len(old[run.old_name]), f"{folder}/{run.old_name}"


@needs_results
def test_every_review_aligns_to_the_changes_it_was_read_from(real):
    """
    Given  the converted reviews and the old ``changes``
    When   every review is aligned against its own answer
    Then   each note keeps its category and explanation, the three notes that explained no change
           are still orphans, and the notes whose stored span no longer meets a hunk are counted

    The alignment is against the output with the added Markdown marks stripped, which is the text
    the old ``changes`` were aligned against: a mark the model added is not a change.

    The count is 455 and not the 3 the first estimate expected.  The old ``changes`` were aligned against the
    answer *before* the run normalised it — a note that added ``".\\n\\n"`` is stored against the
    space the diff no longer sees, because the stored output has one newline — so re-aligning
    against the stored output reports 452 notes as orphans that the run itself did not.  That is a
    finding about the old rows, not about the converter: the converter maps the stored token span
    to characters and keeps the stored replacement, which is all it can do.
    """
    root, report = real
    records = {record.id: record for record in reader.read_testset(root, "gec-2026-12")}
    differences: list[str] = []
    orphans = 0
    unexplained = 0

    for run in report.runs:
        old_rows = _old_rows(run.folder)[run.old_name]
        for answer, old_row in zip(run.run.answers, old_rows, strict=True):
            if answer.review is None:
                continue
            record = records[answer.text.split("/", 1)[1]]
            output = strip_added_marks(record.source, answer.output)
            aligned = align(record.source, output, answer.review)
            where = f"{run.folder}/{run.old_name}/{answer.text}"
            old_pairs = sorted(
                (str(entry.get("category", "other")), str(entry.get("explanation", "")))
                for entry in old_row["changes"]
            )
            new_pairs = sorted(
                [
                    (change.note.category, change.note.explanation)
                    for change in aligned.changes
                    if change.note
                ]
                + [(note.category, note.explanation) for note in aligned.orphan_notes]
            )
            if new_pairs != old_pairs:
                differences.append(f"{where}: {new_pairs} vs {old_pairs}")
            orphans += len(aligned.orphan_notes)
            unexplained += sum(1 for note in aligned.orphan_notes if not note.parts)

    assert differences == []
    assert unexplained == 3
    assert orphans == 455


@needs_results
def test_the_performance_sums_are_the_old_ones(real):
    """
    Given  the converted answers and the old ``performance``
    When   the tokens, the seconds, the loaded models and the memory numbers are compared
    Then   every sum and every entry is the old one
    """
    _, report = real
    differences: list[str] = []

    for run in report.runs:
        old_rows = _old_rows(run.folder)[run.old_name]
        for answer, old_row in zip(run.run.answers, old_rows, strict=True):
            performance = old_row["performance"]
            where = f"{run.folder}/{run.old_name}/{answer.text}"
            for name, mine in (
                ("prompt_tokens", sum(request.prompt_tokens for request in answer.requests)),
                ("answer_tokens", sum(request.answer_tokens for request in answer.requests)),
                ("total_s", sum(request.total_s for request in answer.requests)),
                ("wall_s", sum(request.wall_s for request in answer.requests)),
            ):
                theirs = sum(request[name] for request in performance["requests"])
                if abs(mine - theirs) > 1e-6:
                    differences.append(f"{where}: {name} {mine} vs {theirs}")
            if answer.shared != performance["shared"]:
                differences.append(f"{where}: shared")
            if [loaded.name for loaded in answer.loaded] != [
                item["name"] for item in performance["loaded"]
            ]:
                differences.append(f"{where}: loaded")
            if (answer.vram_peak_mb, answer.vram_baseline_mb) != (
                performance["vram_peak_mb"],
                performance["vram_baseline_mb"],
            ):
                differences.append(f"{where}: memory")

    assert differences == []


@needs_results
def test_converting_twice_gives_byte_identical_files(real, tmp_path):
    """
    Given  the old store and a store converted from it
    When   it is converted again into another store
    Then   every file is byte-identical, so a second conversion is not a change
    """
    root, _ = real
    again = tmp_path / "again"

    convert_v1.convert(
        OLD_ROOT,
        again,
        manifests=MANIFESTS,
        instructions=_instructions,
        assume_machine="rx9070xt-server",
        skip=_LEFT_OUT,
    )

    written = sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
    assert written
    for relative in written:
        assert (root / relative).read_bytes() == (again / relative).read_bytes(), str(relative)


@needs_results
def test_every_position_maps_back_to_its_token(real):
    """
    Given  the converted records and the old ones
    When   every label, sentence span and manifest edit is mapped back to tokens
    Then   the token span is the old one and the text under the character span is the old tokens;
           the manifest's own positions are checked for token alignment, because the manifest
           holds characters now and the old token positions are no longer stored
    """
    root, _ = real
    old_records = {
        line["id"]: line for line in convert_v1.old_testset_lines(OLD_ROOT / "set-2026-12")
    }
    manifest = reader.read_manifest(root, "gec-2026-12")
    records = reader.read_testset(root, "gec-2026-12")
    by_id = {record.id: record for record in records}
    differences: list[str] = []

    for record in records:
        old = old_records[record.id]
        tokens = tokenize(record.source)
        for label, old_label in zip(record.labels, old.get("labels") or [], strict=True):
            span = (label.start, label.end)
            if char_span_to_tokens(record.source, span) != (old_label["start"], old_label["end"]):
                differences.append(f"{record.id}: label {span}")
            if text_under(record.source, span) != "".join(
                tokens[old_label["start"] : old_label["end"]]
            ):
                differences.append(f"{record.id}: label text {span}")
        for span, old_span in zip(
            record.sentence_spans, old.get("sentence_spans") or [], strict=True
        ):
            if char_span_to_tokens(record.source, span) != tuple(old_span):
                differences.append(f"{record.id}: sentence {span}")
            if text_under(record.source, span) != "".join(tokens[old_span[0] : old_span[1]]):
                differences.append(f"{record.id}: sentence text {span}")

    for item in manifest.items:
        record = by_id[item.id]
        tokens = tokenize(record.source)
        for edit in item.edits:
            span = (edit["start"], edit["end"])
            start, end = char_span_to_tokens(record.source, span)
            if text_under(record.source, span) != "".join(tokens[start:end]):
                differences.append(f"{item.id}: manifest edit {span} is not token-aligned")
        for span in item.sentence_spans:
            start, end = char_span_to_tokens(record.source, span)
            if text_under(record.source, span) != "".join(tokens[start:end]):
                differences.append(f"{item.id}: manifest sentence {span} is not token-aligned")

    assert differences == []


def _axes_of_old_tags(tags: list[str]) -> set[str]:
    """Return the new file-name tags the old name's *tags* stand for.

    An old name writes the mode and the step-one prompt always, and a new name writes an axis only
    when it differs from its default, so the default exchange and the default steps are dropped
    here exactly as :func:`~tools.evaluation.core.axes.file_name_tags` drops them.
    """
    found: set[str] = set()
    for tag in tags:
        if tag.startswith("T"):
            found.add(f"temperature-{tag[1:]}")
        elif tag.startswith("prompt-"):
            found.add(f"step_one_prompt-{tag[len('prompt-') :]}")
        elif tag.startswith("r") and tag[1:].isdigit():
            found.add(f"repeat-{tag[1:]}")
        elif tag in {mode.value for mode in AnswerMode}:
            exchange, steps = exchange_and_steps(parse_mode(tag))
            if exchange.value != axes.AXIS_BY_NAME["exchange"].default:
                found.add(f"exchange-{exchange.value}")
            values = tuple(step.value for step in steps)
            if values != axes.AXIS_BY_NAME["steps"].default:
                found.add("steps-" + ("+".join(values) or "none"))
        else:
            found.add(f"sampling.{tag}")
    return found


@needs_results
def test_the_new_name_carries_the_axes_the_old_name_carried(real):
    """
    Given  the old file names and the converted headers
    When   the old tags are read as axes and the new name is derived from the header
    Then   every old tag is an axis of the new name, and the new name is exactly the header's label
    """
    _, report = real
    differences: list[str] = []

    for run in report.runs:
        tags = run.old_name.split("__")[1:]
        mine = set(axes.file_name_tags(run.run.header))
        missing = _axes_of_old_tags(tags) - mine
        if missing:
            differences.append(
                f"{run.folder}/{run.old_name}: {sorted(missing)} not in {sorted(mine)}"
            )
        date = axes.date_of(run.run.sessions[0].date) if run.run.sessions else ""
        if run.new_name != axes.run_file_name(run.run.header, date):
            differences.append(
                f"{run.folder}/{run.old_name}: the new name is not the header's label"
            )
        if run.new_name.split("__")[1:-1] != axes.file_name_tags(run.run.header):
            differences.append(
                f"{run.folder}/{run.old_name}: the new name's tags are not the axes'"
            )

    assert differences == []
