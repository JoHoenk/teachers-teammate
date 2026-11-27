"""Unit tests for the core layer: the formats, the identities, the axes and the protocol table.

The formats round-trip, an unknown field survives a read and a write, a changed reference moves
``key_sha256`` and not ``question_sha256``, added request data moves neither, character spans
convert to tokens, and changing each record field in turn moves exactly the hash it should.
"""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import replace
import json
from pathlib import Path
from typing import Any

import pytest

from tools.evaluation.core import (
    axes,
    axis_table,
    formats,
    hashing,
    labels,
    modes,
    paths,
    protocol_table,
    records,
)
from tools.evaluation.core.records import (
    AcceptedEdit,
    Answer,
    Environment,
    Input,
    Mark,
    Protocol,
    RequestData,
    RunFile,
    RunHeader,
    RunSettings,
    Session,
    TestRecord,
    key_sha256,
    question_sha256,
    read_run_file,
)
from tools.evaluation.core.performance import RequestStats
from tools.evaluation.metrics import offsets

SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."


def _record(**overrides) -> TestRecord:
    """Return a test record of an error text, with sensible defaults."""
    values: dict[str, Any] = {
        "id": "de-01",
        "language": "de",
        "source": SOURCE,
        "reference": REFERENCE,
        "origin": "Falko-MERLIN test",
        "band": "short",
        "corpus": "falko-merlin",
    }
    values.update(overrides)
    return TestRecord(**values)


def _header(**overrides) -> RunHeader:
    """Return a run header of one German text, with sensible defaults."""
    values: dict[str, Any] = {
        "input": Input(
            testset="gec-2026-12",
            question_sha256=question_sha256([_record()]),
            texts=1,
            languages=("de",),
        ),
        "protocol": Protocol(model="gemma4:12b", model_digest="sha256:6114515dabc"),
        "run": RunSettings(seed=20261109),
        "environment": Environment(machine="rx9070xt-server", ollama_version="0.40.16"),
    }
    values.update(overrides)
    return RunHeader(**values)


# ── the formats round-trip ────────────────────────────────────────────────────


def test_a_test_record_comes_back_unchanged() -> None:
    """
    Given  a record with every group filled
    When   it is written and read back
    Then   nothing moved
    """
    record = _record(
        request=RequestData(level="B1", notes="Bitte korrigieren.", passages=((0, 6),)),
        labels=(labels.ReferenceLabel(start=21, end=22, replacement="ist", code="R:VERB:FORM"),),
        sentence_spans=((0, 6), (7, 10)),
        unchanged_sentences=(1,),
        accepted=(AcceptedEdit(span=(21, 22), replacement="ist"),),
        pointer={"origin": "merlin", "text": "1061_0120359"},
    )

    back = TestRecord.from_dict(record.to_dict())

    assert back == record


def test_a_run_file_comes_back_unchanged() -> None:
    """
    Given  a run with a session and an answer
    When   its lines are read back
    Then   the header, the sessions and the answers are the ones written
    """
    run = RunFile(
        header=_header(),
        sessions=(Session(date="2026-11-16T10:00:00Z", tool_revision="abc", vram_baseline_mb=607),),
        answers=(
            Answer(
                text="de/de-01",
                text_sha256=_record().text_sha256,
                output=REFERENCE,
                seconds=15.2,
                pieces=2,
            ),
        ),
    )

    back = read_run_file(run.lines())

    assert back.header == run.header
    assert back.sessions == run.sessions
    assert back.answers == run.answers
    assert back.done() == {"de/de-01"}


def test_a_mark_comes_back_unchanged() -> None:
    """
    Given  a mark on one edit
    When   it is written and read back
    Then   nothing moved
    """
    mark = Mark(kind="edit", text="de/de-01", span=(21, 22), replacement="ist", value="valid")

    back = Mark.from_dict(mark.to_dict())

    assert back == mark


def test_an_explanation_mark_comes_back_unchanged() -> None:
    """
    Given  a mark on one explanation
    When   it is written and read back
    Then   nothing moved, and the hash of the explanation is part of it
    """
    mark = Mark(
        kind="explanation",
        text="de/de-01",
        span=(21, 22),
        replacement="ist",
        value="correct",
        explanation_sha256="a" * 64,
    )

    back = Mark.from_dict(mark.to_dict())

    assert back == mark


def test_a_mark_of_an_unknown_value_is_refused() -> None:
    """
    Given  a mark whose value no marker can give
    When   it is read
    Then   the reader refuses it
    """
    record = {
        "format": formats.MARKS_FORMAT,
        "kind": "edit",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "maybe",
    }

    with pytest.raises(ValueError, match="unknown edit mark value"):
        Mark.from_dict(record)


def test_a_mark_of_an_unknown_kind_is_refused() -> None:
    """
    Given  a mark whose kind no marker gives
    When   it is read
    Then   the reader refuses it
    """
    record = {
        "format": formats.MARKS_FORMAT,
        "kind": "style",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "valid",
    }

    with pytest.raises(ValueError, match="unknown mark kind"):
        Mark.from_dict(record)


def test_each_kind_refuses_the_other_kind_s_values() -> None:
    """
    Given  an edit mark with an explanation's value, and the other way round
    When   each is read
    Then   both are refused, because the two questions have different answers
    """
    edit = {
        "format": formats.MARKS_FORMAT,
        "kind": "edit",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "correct",
    }
    explanation = {
        "format": formats.MARKS_FORMAT,
        "kind": "explanation",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "valid",
        "explanation_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="unknown edit mark value"):
        Mark.from_dict(edit)
    with pytest.raises(ValueError, match="unknown explanation mark value"):
        Mark.from_dict(explanation)


def test_an_explanation_mark_without_a_hash_is_refused() -> None:
    """
    Given  an explanation mark that does not say which explanation it is about
    When   it is read
    Then   the reader refuses it
    """
    record = {
        "format": formats.MARKS_FORMAT,
        "kind": "explanation",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "correct",
    }

    with pytest.raises(ValueError, match="needs the hash of the explanation"):
        Mark.from_dict(record)


def test_an_edit_mark_with_a_hash_is_refused() -> None:
    """
    Given  an edit mark that carries an explanation's hash
    When   it is read
    Then   the reader refuses it, because an edit is the same edit whoever explained it
    """
    record = {
        "format": formats.MARKS_FORMAT,
        "kind": "edit",
        "text": "de/de-01",
        "span": [0, 1],
        "value": "valid",
        "explanation_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="not about an explanation"):
        Mark.from_dict(record)


def test_a_file_of_an_unknown_format_is_refused() -> None:
    """
    Given  a record of a format this reader does not know
    When   it is read
    Then   the reader refuses it and names the version it knows
    """
    record = {**_record().to_dict(), "format": 99}

    with pytest.raises(formats.FormatError, match="format 99"):
        TestRecord.from_dict(record)


def test_a_line_without_a_format_is_refused() -> None:
    """
    Given  a record written before the format was named
    When   it is read
    Then   the reader refuses it
    """
    record = _record().to_dict()
    del record["format"]

    with pytest.raises(formats.FormatError, match="no format version"):
        TestRecord.from_dict(record)


def test_a_run_file_whose_first_line_is_not_a_header_is_refused() -> None:
    """
    Given  a file whose first line is an answer
    When   it is read as a run
    Then   the reader refuses it
    """
    answer = Answer(text="de/de-01", text_sha256="x", output="y").to_dict()

    with pytest.raises(formats.FormatError, match="not a run header"):
        read_run_file([answer])


def test_a_run_file_with_a_stray_line_is_refused() -> None:
    """
    Given  a run whose second line is neither a session nor an answer
    When   it is read
    Then   the reader refuses it and names the line
    """
    lines = [_header().to_dict(), {"format": formats.RUN_FORMAT, "type": "note"}]

    with pytest.raises(formats.FormatError, match=":2: neither a session nor an answer"):
        read_run_file(lines)


# ── an unknown field survives a read and a write ─────────────────────────────


def test_a_field_a_newer_writer_added_survives_a_round_trip() -> None:
    """
    Given  a record with a field this reader does not know
    When   it is read and written again
    Then   the field is still there
    """
    record = {**_record().to_dict(), "future_field": {"a": 1}}

    written = TestRecord.from_dict(record).to_dict()

    assert written["future_field"] == {"a": 1}


def test_an_unknown_field_of_a_run_header_survives_a_round_trip() -> None:
    """
    Given  a header with a field this reader does not know
    When   it is read and written again
    Then   the field is still there
    """
    header = {**_header().to_dict(), "future_axis": "thinking"}

    written = RunHeader.from_dict(header).to_dict()

    assert written["future_axis"] == "thinking"


def test_an_unknown_field_of_an_answer_survives_a_round_trip() -> None:
    """
    Given  an answer with a field this reader does not know
    When   it is read and written again
    Then   the field is still there
    """
    answer = {
        **Answer(text="de/de-01", text_sha256="x", output="y").to_dict(),
        "future_measure": 12,
    }

    written = Answer.from_dict(answer).to_dict()

    assert written["future_measure"] == 12


def test_a_rewrite_keeps_the_unknown_fields_and_takes_the_known_ones_from_the_record() -> None:
    """
    Given  a line a newer writer added a field to, and the fields this reader writes
    When   the two are merged for a rewrite
    Then   the unknown field is kept and the known one is the record's
    """
    line = {**_record().to_dict(), "future_field": 1}
    known = {"format": formats.TESTSET_FORMAT, "id": "de-01"}

    merged = formats.merge_unknown(line, known)

    assert merged["future_field"] == 1
    assert merged["id"] == "de-01"


def test_an_unknown_field_of_a_request_block_survives_a_round_trip() -> None:
    """
    Given  a request block with a part this reader does not know
    When   the record is read and written again
    Then   the part is still there, so a newer writer's request data is not dropped
    """
    record = {**_record().to_dict(), "request": {"level": "B1", "future_part": "x"}}

    written = TestRecord.from_dict(record).to_dict()

    assert written["request"]["future_part"] == "x"


def test_an_unknown_field_of_every_nested_block_survives_a_round_trip() -> None:
    """
    Given  a header whose input, protocol, run and environment blocks each hold an unknown field
    When   it is read and written again
    Then   every one of them is still there, so a newer writer's axis is not dropped
    """
    header = _header().to_dict()
    header["input"]["future_input"] = 1
    header["protocol"]["thinking"] = True
    header["run"]["future_setting"] = "x"
    header["environment"]["future_place"] = "y"

    written = RunHeader.from_dict(header).to_dict()

    assert written["input"]["future_input"] == 1
    assert written["protocol"]["thinking"] is True
    assert written["run"]["future_setting"] == "x"
    assert written["environment"]["future_place"] == "y"


def test_an_unknown_protocol_field_reaches_the_compare_key() -> None:
    """
    Given  two headers that differ only in a protocol field this reader does not know
    When   their compare keys are built
    Then   the keys differ, so two runs of a newer writer never pool by accident
    """
    plain = _header()
    newer = RunHeader.from_dict(
        {**plain.to_dict(), "protocol": {**plain.protocol.to_dict(), "thinking": True}}
    )

    assert axes.compare_key(plain) != axes.compare_key(newer)


def test_an_unknown_field_of_a_request_stat_survives_a_round_trip() -> None:
    """
    Given  a request record with a measurement this reader does not know
    When   the answer is read and written again
    Then   the measurement is still there
    """
    answer = Answer(
        text="de/de-01",
        text_sha256="x",
        output="y",
        requests=(
            RequestStats(
                model="gemma4:12b",
                prompt_tokens=1,
                answer_tokens=2,
                load_s=0.0,
                prompt_eval_s=0.0,
                generation_s=0.0,
                total_s=0.0,
                wall_s=0.0,
            ),
        ),
    ).to_dict()
    answer["requests"][0]["future_measure"] = 12

    written = Answer.from_dict(answer).to_dict()

    assert written["requests"][0]["future_measure"] == 12


def test_a_count_of_put_back_stretches_survives_a_round_trip() -> None:
    """
    Given  an answer whose stretches the app put back
    When   it is written and read again
    Then   the count is still there, and an answer without one writes no such field
    """
    written = Answer(text="de/de-01", text_sha256="x", output="y", left_out=2).to_dict()

    assert written["left_out"] == 2
    assert Answer.from_dict(written).left_out == 2
    assert "left_out" not in Answer(text="de/de-01", text_sha256="x", output="y").to_dict()


def test_a_review_of_another_version_survives_a_round_trip_and_is_flagged() -> None:
    """
    Given  an answer whose stored review is of a version this reader does not know
    When   it is read and written again
    Then   the raw review is kept, the answer says which version it was, and no review is claimed
    """
    answer = {
        **Answer(text="de/de-01", text_sha256="x", output="y").to_dict(),
        "review": {"version": 99, "edits": [], "unclear": []},
    }

    read = Answer.from_dict(answer)

    assert read.review is None
    assert read.review_unreadable == "99"
    assert read.to_dict()["review"] == {"version": 99, "edits": [], "unclear": []}


def test_a_malformed_line_names_the_file_and_the_line() -> None:
    """
    Given  a run file whose second line is an answer without its text hash
    When   it is read
    Then   the reader refuses it with a message naming the file and the line, not a bare KeyError
    """
    lines = [_header().to_dict(), {"format": formats.RUN_FORMAT, "type": "answer", "text": "de/x"}]

    with pytest.raises(formats.FormatError, match=r"runs/x\.run\.jsonl:2: "):
        read_run_file(lines, source="runs/x.run.jsonl")


def test_the_known_fields_are_not_fields_of_a_record() -> None:
    """
    Given  the records of the run file
    When   their dataclass fields are listed
    Then   no record carries ``_KNOWN`` as a field, so it never enters a comparison or a record
    """
    for record in (Answer, RunHeader, Session, TestRecord, Mark):
        assert "_KNOWN" not in {item.name for item in dataclasses.fields(record)}


# ── the question and the key have separate identities ────────────────────────


def test_a_changed_reference_moves_the_key_and_not_the_question() -> None:
    """
    Given  a set and the same set with a corrected reference
    When   the two identities are compared
    Then   the question is the same and the key moved
    """
    before = _record()
    after = replace(before, reference=REFERENCE + " Danke.")

    assert question_sha256([after]) == question_sha256([before])
    assert after.key_sha256 != before.key_sha256


def test_added_request_data_moves_neither_the_question_nor_the_key() -> None:
    """
    Given  a set and the same set with the teacher's notes added
    When   the two identities are compared
    Then   neither moved, and the notes have a hash of their own
    """
    before = _record()
    after = replace(before, request=RequestData(notes="Bitte korrigieren."))

    assert question_sha256([after]) == question_sha256([before])
    assert after.key_sha256 == before.key_sha256
    assert after.request_hashes() == {
        "notes": hashing.sha256_json("Bitte korrigieren."),
    }


def test_a_changed_source_moves_the_question() -> None:
    """
    Given  a set and the same set with another source
    When   the two identities are compared
    Then   the question moved
    """
    before = _record()
    after = replace(before, source=SOURCE + " Danke.")

    assert question_sha256([after]) != question_sha256([before])


def test_a_changed_language_moves_the_question() -> None:
    """
    Given  a set and the same text in another language
    When   the two identities are compared
    Then   the question moved, because the text is asked about in another language
    """
    before = _record()
    after = replace(before, language="en")

    assert question_sha256([after]) != question_sha256([before])


def test_the_order_of_the_texts_is_part_of_the_question() -> None:
    """
    Given  two texts
    When   the set is hashed in both orders
    Then   the two hashes differ
    """
    first = _record()
    second = _record(id="de-02", source="Ein anderer Text.")

    assert question_sha256([first, second]) != question_sha256([second, first])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "correct"),
        ("reference", REFERENCE + " Danke."),
        ("sentences", ("Ein Satz.",)),
        ("labels", (labels.ReferenceLabel(start=0, end=1, replacement="x", code="R:SPELL"),)),
        ("sentence_spans", ((0, 6),)),
        ("unchanged_sentences", (0,)),
        ("accepted", (AcceptedEdit(span=(0, 1), replacement="x"),)),
    ],
)
def test_every_key_field_moves_the_key_hash(field: str, value: object) -> None:
    """
    Given  a record and the same record with one key field changed
    When   the two key hashes are compared
    Then   the key moved and the question did not
    """
    before = _record()
    after = replace(before, **{field: value})

    assert after.key_sha256 != before.key_sha256
    assert question_sha256([after]) == question_sha256([before])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("origin", "another corpus"),
        ("band", "page"),
        ("corpus", "wi"),
        ("pointer", {"origin": "falko"}),
    ],
)
def test_no_meta_field_moves_either_hash(field: str, value: object) -> None:
    """
    Given  a record and the same record with one meta field changed
    When   the two identities are compared
    Then   neither moved: meta is provenance, not identity
    """
    before = _record()
    after = replace(before, **{field: value})

    assert after.key_sha256 == before.key_sha256
    assert question_sha256([after]) == question_sha256([before])


def test_the_key_of_a_whole_set_covers_every_text() -> None:
    """
    Given  two texts
    When   one text's key changes
    Then   the set's key moved
    """
    first = _record()
    second = _record(id="de-02", source="Ein anderer Text.")

    changed = replace(second, reference="Ein anderer Text!")

    assert key_sha256([first, changed]) != key_sha256([first, second])


# ── character spans to token spans ──────────────────────────────────────────


def test_the_text_under_a_character_span_is_the_tokens_joined() -> None:
    """
    Given  a token span of a text
    When   it is mapped to characters
    Then   the characters under it are the tokens joined
    """
    text = "Gestern hat sie ins Kino gegangen."
    span = (4, 9)

    start, end = offsets.token_span_to_chars(text, span)

    assert offsets.text_under(text, (start, end)) == "sie ins Kino"


def test_a_span_that_does_not_lie_on_token_boundaries_is_refused() -> None:
    """
    Given  a character span that starts inside a word
    When   it is mapped to tokens
    Then   the conversion refuses it
    """
    text = "Gestern hat sie"

    with pytest.raises(ValueError, match="does not lie on token boundaries"):
        offsets.char_span_to_tokens(text, (3, 7))


# ── the axes and the compare key ─────────────────────────────────────────────


def test_the_compare_key_leaves_out_the_fingerprint_the_seed_and_the_repeat() -> None:
    """
    Given  a run and the same run with another fingerprint, seed and repeat
    When   the two compare keys are compared
    Then   they are the same: none of the three is part of the key
    """
    before = _header()
    after = replace(
        before,
        protocol=replace(before.protocol, fingerprint="a" * 64),
        run=RunSettings(seed=1, repeat=3, timeout=60.0),
    )

    assert axes.compare_key(after) == axes.compare_key(before)


def test_the_compare_key_moves_with_an_axis() -> None:
    """
    Given  a run and the same run at another temperature
    When   the two compare keys are compared
    Then   they differ
    """
    before = _header()
    after = replace(before, protocol=replace(before.protocol, temperature=0.4))

    assert axes.compare_key(after) != axes.compare_key(before)


def test_the_compare_key_moves_with_the_test_set() -> None:
    """
    Given  a run and the same run on another set
    When   the two compare keys are compared
    Then   they differ
    """
    before = _header()
    after = replace(before, input=replace(before.input, testset="gec-2026-12-heldout"))

    assert axes.compare_key(after) != axes.compare_key(before)


def test_the_environment_key_adds_where_the_run_ran() -> None:
    """
    Given  a run and the same run on another machine
    When   the two environment keys are compared
    Then   they differ, while the compare keys are the same
    """
    before = _header()
    after = replace(before, environment=replace(before.environment, machine="rtx3070-laptop"))

    assert axes.environment_key(after) != axes.environment_key(before)
    assert axes.compare_key(after) == axes.compare_key(before)


def test_the_instructions_enter_the_compare_key_per_language() -> None:
    """
    Given  a run and the same run with another instruction hash for one language
    When   the two compare keys are compared
    Then   they differ, because what was sent differs
    """
    before = _header()
    after = replace(
        before,
        protocol=replace(before.protocol, instructions=(("de", "a" * 64), ("en", "b" * 64))),
    )

    assert axes.compare_key(after) != axes.compare_key(before)


# ── the file name is a label derived from the axes ───────────────────────────


def test_a_default_run_gets_only_the_temperature_and_the_repeat() -> None:
    """
    Given  a run that differs from the defaults in nothing but its temperature
    When   its tags are built
    Then   only the two always-present tags are there
    """
    header = _header(protocol=Protocol(model="gemma4:12b", temperature=0.05))

    assert axes.file_name_tags(header) == ["temperature-0.05", "repeat-1"]


def test_a_run_that_ran_no_step_says_none_and_not_nothing() -> None:
    """
    Given  a one-request review, which runs no step of the exchange
    When   its tags are built
    Then   the empty step list is written as a value, so the tag does not read as a lost one
    """
    header = _header(
        protocol=Protocol(model="gemma4:12b", exchange=modes.Exchange.ONE_REQUEST_REVIEW, steps=())
    )

    assert axes.file_name_tags(header) == [
        "exchange-one-request-review",
        "steps-none",
        "temperature-0",
        "repeat-1",
    ]


def test_every_non_default_axis_gets_one_tag() -> None:
    """
    Given  a run that differs from the defaults in four axes
    When   its tags are built
    Then   each axis has exactly one tag, and the sampling option is its own
    """
    header = _header(
        protocol=Protocol(
            model="gemma4:12b",
            steps=(modes.Step.CORRECT,),
            step_one_prompt="find",
            temperature=0.4,
            sampling=(("presence_penalty", 0.0),),
        ),
        run=RunSettings(seed=20261109, repeat=3),
    )

    assert axes.file_name_tags(header) == [
        "steps-correct",
        "step_one_prompt-find",
        "sampling.presence_penalty-0",
        "temperature-0.4",
        "repeat-3",
    ]


def test_the_file_name_is_built_from_the_axes_and_the_compare_key() -> None:
    """
    Given  a run on a named machine
    When   its file name is built
    Then   it names the date, the machine, the model, the digest, the tags and the key
    """
    header = _header(
        protocol=Protocol(
            model="gemma4:12b",
            model_digest="sha256:6114515dabc",
            steps=(modes.Step.CORRECT,),
            temperature=0.4,
        ),
        run=RunSettings(seed=20261109, repeat=3),
    )

    name = axes.run_file_name(header, "2026-11-16")

    assert name == (
        "2026-11-16_rx9070xt-server_gemma4-12b_6114515d"
        "__steps-correct__temperature-0.4__repeat-3"
        f"__{axes.compare_key_sha256(header)[:8]}.run.jsonl"
    )


def test_an_empty_machine_is_written_as_unknown() -> None:
    """
    Given  a run whose machine was never recorded
    When   its file name is built
    Then   the host part says so instead of leaving a gap
    """
    header = _header(
        protocol=Protocol(model="gemma4:12b", provider=""),
        environment=Environment(),
    )

    assert axes.run_file_name(header, "2026-11-16").split("_")[1] == axes.UNKNOWN_MACHINE


def test_two_runs_that_differ_in_one_axis_get_different_names() -> None:
    """
    Given  two runs that differ only in their temperature
    When   their names are built
    Then   the names differ
    """
    first = _header(protocol=Protocol(model="gemma4:12b", temperature=0.05))
    second = _header(protocol=Protocol(model="gemma4:12b", temperature=0.4))

    assert axes.run_file_name(first, "2026-11-16") != axes.run_file_name(second, "2026-11-16")


def test_the_repeats_of_one_run_share_the_key_and_differ_in_the_name() -> None:
    """
    Given  two repeats of one run
    When   their keys and names are compared
    Then   the key is the same and the name is not
    """
    first = _header(run=RunSettings(seed=20261109, repeat=1))
    second = _header(run=RunSettings(seed=20261110, repeat=2))

    assert axes.compare_key_sha256(first) == axes.compare_key_sha256(second)
    assert axes.run_file_name(first, "2026-11-16") != axes.run_file_name(second, "2026-11-16")


def test_an_isolated_run_and_a_shared_one_get_different_names() -> None:
    """
    Given  one setup run on one machine, once with nothing else loaded and once with another model
    When   their keys and names are compared
    Then   the run keys differ and so do the names, because isolation is in the run key
    """
    shared = _header(environment=Environment(machine="rx9070xt-server", isolated=False))
    isolated = _header(environment=Environment(machine="rx9070xt-server", isolated=True))

    assert axes.compare_key_sha256(shared) == axes.compare_key_sha256(isolated)
    assert axes.run_key_sha256(shared) != axes.run_key_sha256(isolated)
    assert axes.run_file_name(shared, "2026-11-16") != axes.run_file_name(isolated, "2026-11-16")
    assert "isolated" in axes.file_name_tags(isolated)
    assert "isolated" not in axes.file_name_tags(shared)


def test_the_repeats_of_one_setup_share_the_series_and_differ_in_the_run_key() -> None:
    """
    Given  two repeats of one setup on one machine
    When   their keys are compared
    Then   the compare and series keys are the same, and the run keys differ: one repeat, one file
    """
    first = _header(run=RunSettings(seed=20261109, repeat=1))
    second = _header(run=RunSettings(seed=20261110, repeat=2))

    assert axes.series_key_sha256(first) == axes.series_key_sha256(second)
    assert axes.run_key_sha256(first) != axes.run_key_sha256(second)


def test_two_machines_share_the_compare_key_and_not_the_series_key() -> None:
    """
    Given  one setup run on two machines
    When   their keys are compared
    Then   the quality may be pooled, and the gate groups stay apart
    """
    first = _header(environment=Environment(machine="rx9070xt-server"))
    second = _header(environment=Environment(machine="rtx3070-laptop"))

    assert axes.compare_key(first) == axes.compare_key(second)
    assert axes.series_key(first) != axes.series_key(second)


def test_another_server_version_stays_one_series() -> None:
    """
    Given  one setup whose server was upgraded between two repeats
    When   their series keys are compared
    Then   they are the same series, because a server upgrade is not another setup
    """
    before = _header(environment=Environment(machine="rx9070xt-server", ollama_version="0.40.16"))
    after = _header(environment=Environment(machine="rx9070xt-server", ollama_version="0.41.0"))

    assert axes.series_key(before) == axes.series_key(after)
    assert axes.environment_key(before) != axes.environment_key(after)


def test_an_explainer_tag_stays_one_path_segment() -> None:
    """
    Given  a run whose explainer is a registry tag with a slash and a quantisation mark
    When   its tags are built
    Then   the explainer tag holds neither, so the name stays one path segment
    """
    header = _header(
        protocol=Protocol(
            model="gemma4:12b",
            explainer="hf.co/bartowski/Qwen3-8B-GGUF:Q4_K_M",
        )
    )

    tags = axes.file_name_tags(header)

    assert "explainer-bartowski-qwen3-8b-gguf-q4_k_m" in tags
    assert all("/" not in tag and ":" not in tag for tag in tags)


# ── the protocol table ───────────────────────────────────────────────────────


def _table(**overrides) -> dict:
    """Return a small, valid protocol table."""
    table = {
        "schema": 1,
        "current": 2,
        "versions": [
            {
                "version": 1,
                "date": "2026-11-12",
                "fingerprints": ["a" * 64],
                "why": "first",
            },
            {
                "version": 2,
                "date": "2026-11-16",
                "fingerprints": ["b" * 64, "c" * 64],
                "why": "additive",
            },
        ],
    }
    table.update(overrides)
    return table


def test_a_fingerprint_is_looked_up_to_its_version() -> None:
    """
    Given  a table whose current version accepts two fingerprints
    When   each fingerprint is looked up
    Then   it names the version that accepts it
    """
    table = _table()

    assert protocol_table.version_of("a" * 64, table) == 1
    assert protocol_table.version_of("b" * 64, table) == 2
    assert protocol_table.version_of("c" * 64, table) == 2


def test_an_unknown_fingerprint_has_no_version() -> None:
    """
    Given  a table
    When   a fingerprint it does not record is looked up
    Then   there is no version
    """
    table = _table()

    assert protocol_table.version_of("d" * 64, table) is None


def test_an_additive_change_joins_the_current_version() -> None:
    """
    Given  a table whose current version accepts two fingerprints
    When   the current version is asked for its fingerprints
    Then   both are there, so an additive change needs no new version
    """
    table = _table()

    assert protocol_table.fingerprints_of(2, table) == ("b" * 64, "c" * 64)
    assert protocol_table.current_version(table) == 2


def test_a_valid_table_has_no_problems() -> None:
    """
    Given  a valid table
    When   it is checked
    Then   nothing is wrong with it
    """
    assert protocol_table.problems(_table()) == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"schema": 2}, "schema is 2"),
        ({"current": 9}, "current 9 is not one of the versions"),
        ({"versions": []}, "versions is not a non-empty list"),
    ],
)
def test_a_broken_table_is_reported(change: dict, message: str) -> None:
    """
    Given  a table with one thing wrong with it
    When   it is checked
    Then   the problem is named
    """
    table = _table(**change)

    assert any(message in problem for problem in protocol_table.problems(table))


def test_a_fingerprint_accepted_by_two_versions_is_reported() -> None:
    """
    Given  a table where one fingerprint belongs to two versions
    When   it is checked
    Then   the duplicate is named
    """
    table = _table()
    table["versions"][1]["fingerprints"] = ["a" * 64]

    assert any(
        "also accepted by version 1" in problem for problem in protocol_table.problems(table)
    )


def test_a_version_without_a_fingerprint_is_reported() -> None:
    """
    Given  a table with a version that accepts nothing
    When   it is checked
    Then   the empty list is named
    """
    table = _table()
    table["versions"][0]["fingerprints"] = []

    assert any(
        "version 1 accepts no fingerprint" in problem for problem in protocol_table.problems(table)
    )


def test_the_committed_table_is_valid() -> None:
    """
    Given  the table this package ships
    When   it is checked
    Then   it is valid and its current version accepts a fingerprint
    """
    assert protocol_table.problems() == []
    assert protocol_table.fingerprints_of(protocol_table.current_version())


def test_a_table_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    """
    Given  a file that is not JSON
    When   it is read
    Then   the reader refuses it
    """
    path = tmp_path / "protocol.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(protocol_table.ProtocolTableError, match="cannot be read as JSON"):
        protocol_table.read_table(path)


# ── the modes: one vocabulary ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "exchange", "steps"),
    [
        (modes.AnswerMode.REVIEW, modes.Exchange.ONE_REQUEST_REVIEW, modes.ALL_STEPS),
        (modes.AnswerMode.TEXT, modes.Exchange.ONE_REQUEST_TEXT, (modes.Step.CORRECT,)),
        (modes.AnswerMode.EXCHANGE, modes.Exchange.TEXT_FIRST, modes.ALL_STEPS),
        (modes.AnswerMode.CORRECT_ONLY, modes.Exchange.TEXT_FIRST, (modes.Step.CORRECT,)),
    ],
)
def test_an_old_mode_maps_to_an_exchange_and_its_steps(
    mode: modes.AnswerMode, exchange: modes.Exchange, steps: tuple[modes.Step, ...]
) -> None:
    """
    Given  an answer mode of the old vocabulary
    When   it is translated
    Then   it names the exchange and the steps its one answer holds
    """
    assert modes.exchange_and_steps(mode) == (exchange, steps)


def test_a_text_first_run_without_the_unclear_check_drops_only_that_step() -> None:
    """
    Given  the exchange mode and a profile that switched the unclear check off
    When   the mode is translated
    Then   the exchange keeps its two steps, and only the unclear one is gone
    """
    assert modes.exchange_and_steps(modes.AnswerMode.EXCHANGE, unclear_check=False) == (
        modes.Exchange.TEXT_FIRST,
        (modes.Step.CORRECT, modes.Step.EXPLAIN),
    )


def test_the_unclear_check_does_not_touch_a_one_request_mode() -> None:
    """
    Given  a review mode, whose answer always carries the unclear expressions
    When   it is translated with the unclear check switched off
    Then   the steps are unchanged, because only the app's exchange applies that check
    """
    assert modes.exchange_and_steps(modes.AnswerMode.REVIEW, unclear_check=False) == (
        modes.Exchange.ONE_REQUEST_REVIEW,
        modes.ALL_STEPS,
    )


def test_an_unknown_exchange_is_refused() -> None:
    """
    Given  a value that is not an exchange
    When   it is read
    Then   the reader refuses it and names the allowed ones
    """
    with pytest.raises(ValueError, match="unknown exchange"):
        modes.exchange_of("T")


def test_an_unknown_step_is_refused() -> None:
    """
    Given  a list with a value that is not a step
    When   it is read
    Then   the reader refuses it
    """
    with pytest.raises(ValueError, match="unknown step"):
        modes.steps_of(["correct", "explain", "polish"])


# ── the JSONL helpers ────────────────────────────────────────────────────────


def test_a_written_file_has_sorted_keys_and_one_record_per_line(tmp_path: Path) -> None:
    """
    Given  two records
    When   they are written
    Then   each line has sorted keys and the file ends with a newline
    """
    path = tmp_path / "records.jsonl"

    formats.write_jsonl(path, [{"b": 1, "a": 2}, {"c": 3}])

    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ['{"a": 2, "b": 1}', '{"c": 3}']


def test_appending_keeps_what_was_there(tmp_path: Path) -> None:
    """
    Given  a file with one record
    When   another is appended
    Then   both are there, in order
    """
    path = tmp_path / "records.jsonl"
    formats.write_jsonl(path, [{"a": 1}])

    formats.append_jsonl(path, [{"a": 2}])

    assert formats.read_jsonl(path) == [{"a": 1}, {"a": 2}]


def test_a_line_that_is_not_an_object_is_refused(tmp_path: Path) -> None:
    """
    Given  a file with a JSON array on one line
    When   it is read
    Then   the reader refuses it and names the line
    """
    path = tmp_path / "records.jsonl"
    path.write_text("[1, 2]\n", encoding="utf-8")

    with pytest.raises(formats.FormatError, match=":1: not a JSON object"):
        formats.read_jsonl(path)


def test_a_line_that_is_not_json_is_refused(tmp_path: Path) -> None:
    """
    Given  a file with a broken line
    When   it is read
    Then   the reader refuses it
    """
    path = tmp_path / "records.jsonl"
    path.write_text("{oops\n", encoding="utf-8")

    with pytest.raises(formats.FormatError, match=":1: not JSON"):
        formats.read_jsonl(path)


def test_a_written_run_file_is_byte_identical_when_written_twice(tmp_path: Path) -> None:
    """
    Given  a run
    When   it is written twice
    Then   the two files are the same bytes
    """
    run = RunFile(
        header=_header(),
        answers=(Answer(text="de/de-01", text_sha256=_record().text_sha256, output=REFERENCE),),
    )

    first = tmp_path / "first.run.jsonl"
    second = tmp_path / "second.run.jsonl"
    formats.write_jsonl(first, run.lines())
    formats.write_jsonl(second, run.lines())

    assert first.read_bytes() == second.read_bytes()


def test_a_run_file_read_from_disk_is_the_one_written(tmp_path: Path) -> None:
    """
    Given  a run written to disk
    When   it is read back
    Then   it is the run that was written
    """
    run = RunFile(
        header=_header(),
        sessions=(Session(date="2026-11-16T10:00:00Z"),),
        answers=(Answer(text="de/de-01", text_sha256=_record().text_sha256, output=REFERENCE),),
    )
    path = tmp_path / "run.run.jsonl"
    formats.write_jsonl(path, run.lines())

    back = read_run_file(formats.read_jsonl(path))

    assert back.header == run.header
    assert back.answers == run.answers


def test_the_header_line_says_what_it_is() -> None:
    """
    Given  a header
    When   it is written
    Then   it names its format and its type, so a file of answers alone is not mistaken for a run
    """
    line = _header().to_dict()

    assert line["format"] == formats.RUN_FORMAT
    assert line["type"] == "run"
    assert json.loads(json.dumps(line))["task"] == "proofread"


# ── a write is atomic, and a torn last line is not a broken file ─────────────


def test_a_write_that_fails_leaves_the_old_file_intact(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a file with one record
    When   a rewrite fails while it replaces the file
    Then   the old file is still there, unchanged
    """
    path = tmp_path / "records.jsonl"
    formats.write_jsonl(path, [{"a": 1}])

    def refuse(self: Path, target: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        formats.write_jsonl(path, [{"a": 2}])

    assert formats.read_jsonl(path) == [{"a": 1}]


def test_a_torn_last_line_is_left_out_and_reported(tmp_path: Path) -> None:
    """
    Given  a file whose last line was cut off mid-write
    When   it is read with the torn tail reported
    Then   the complete records come back and the torn line is named
    """
    path = tmp_path / "run.run.jsonl"
    formats.write_jsonl(path, [{"a": 1}, {"b": 2}])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"c": 3')

    lines, torn = formats.read_jsonl(path, torn_tail="report")

    assert lines == [{"a": 1}, {"b": 2}]
    assert torn is not None
    assert torn.line == 3
    assert torn.bytes == len('{"c": 3')


def test_a_torn_last_line_is_refused_by_default(tmp_path: Path) -> None:
    """
    Given  a file whose last line was cut off mid-write
    When   it is read without asking for the torn tail
    Then   the reader refuses it, as it always did
    """
    path = tmp_path / "run.run.jsonl"
    path.write_text('{"a": 1}\n{"b": 2', encoding="utf-8")

    with pytest.raises(formats.FormatError, match=":2: not JSON"):
        formats.read_jsonl(path)


def test_a_broken_line_in_the_middle_is_still_refused(tmp_path: Path) -> None:
    """
    Given  a file with a broken line that is not the last one
    When   it is read with the torn tail reported
    Then   the reader refuses it, because only a killed append tears the last line
    """
    path = tmp_path / "run.run.jsonl"
    path.write_text('{"a": 1}\n{"b": 2\n{"c": 3}\n', encoding="utf-8")

    with pytest.raises(formats.FormatError, match=":2: not JSON"):
        formats.read_jsonl(path, torn_tail="report")


def test_a_run_file_cut_mid_answer_reads_with_the_torn_line_noted(tmp_path: Path) -> None:
    """
    Given  a run file whose last answer was cut off mid-write
    When   it is read
    Then   the answers before it are there and the file says which line was torn
    """
    run = RunFile(
        header=_header(),
        answers=(Answer(text="de/de-01", text_sha256=_record().text_sha256, output=REFERENCE),),
    )
    path = tmp_path / "run.run.jsonl"
    formats.write_jsonl(path, run.lines())
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"format": 2, "type": "answer", "text": "de/de-02"')

    lines, torn = formats.read_jsonl(path, torn_tail="report")
    back = read_run_file(lines, str(path), torn)

    assert back.answers == run.answers
    assert back.torn is not None
    assert back.torn.line == 3


# ── one text id ──────────────────────────────────────────────────────────────


def test_a_text_is_named_by_its_language_and_its_record_id() -> None:
    """
    Given  a record of a German text
    When   its id outside its own set file is asked for
    Then   it is ``<language>/<id>``, the form an answer and a mark use
    """
    assert records.text_id(_record()) == "de/de-01"


def test_no_module_builds_a_text_id_by_hand() -> None:
    """
    Given  the tooling's source
    When   it is searched for a hand-built ``<language>/<id>``
    Then   only :func:`~tools.evaluation.core.records.text_id` builds one
    """
    root = Path(records.__file__).parent.parent
    definition = Path(records.__file__)
    offenders = [
        f"{path.relative_to(root)}:{number}"
        for path in sorted(root.rglob("*.py"))
        if path != definition
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if "{record.language}/{record.id}" in line or "{item.language}/{item.id}" in line
    ]

    assert offenders == []
    assert definition.read_text(encoding="utf-8").count('f"{record.language}/{record.id}"') == 1


# ── the task registry ────────────────────────────────────────────────────────


def test_the_only_task_is_proofreading() -> None:
    """
    Given  the task registry
    When   it is read
    Then   it holds the one task the tools can score
    """
    assert records.TASKS == (records.TASK_PROOFREAD,)


def test_a_header_of_an_unknown_task_is_read_and_left_to_the_validator() -> None:
    """
    Given  a header whose task this reader does not know
    When   it is read
    Then   the task is kept, so ``validate`` can refuse it with a message naming the known ones
    """
    header = {**_header().to_dict(), "task": "translate"}

    assert RunHeader.from_dict(header).task == "translate"


# ── the mode table ───────────────────────────────────────────────────────────


def test_the_mode_table_maps_exactly_the_stored_modes() -> None:
    """
    Given  the old vocabulary
    When   every mode is translated
    Then   each one maps, and the table holds no mode nothing writes
    """
    assert {mode.value for mode in modes.AnswerMode} == {
        "review",
        "text",
        "exchange",
        "correct-only",
    }
    for mode in modes.AnswerMode:
        _, steps = modes.exchange_and_steps(mode)
        assert steps


def test_every_exchange_has_a_default_step_list() -> None:
    """
    Given  every exchange
    When   its default steps are asked for
    Then   each one names steps, and a one-request text answer is the corrected text alone
    """
    assert modes.default_steps(modes.Exchange.ONE_REQUEST_TEXT) == (modes.Step.CORRECT,)
    assert modes.default_steps(modes.Exchange.TEXT_FIRST) == modes.ALL_STEPS
    for exchange in modes.Exchange:
        assert modes.default_steps(exchange)


# ── the protocol table is read once ──────────────────────────────────────────


def test_the_committed_table_is_read_once_per_process(monkeypatch) -> None:
    """
    Given  the committed version file
    When   two lookups ask for it
    Then   the file is parsed once, so a report does not re-read it per run
    """
    protocol_table._read_cached.cache_clear()
    reads: list[Path] = []
    original = Path.read_text

    def counting(self: Path, *args, **kwargs) -> str:
        reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting)
    protocol_table.read_table()
    protocol_table.read_table()

    assert len(reads) == 1
    protocol_table._read_cached.cache_clear()


# ── one declaration per axis ─────────────────────────────────────────────────


def test_the_protocol_fields_are_the_axes_and_nothing_else() -> None:
    """
    Given  the axis table and the protocol record
    When   both are read
    Then   every axis is a field and every field is an axis, apart from the fingerprint and the
           unknown fields, so a new axis cannot be declared in one place only
    """
    fields = {item.name for item in dataclasses.fields(Protocol)}

    assert fields == set(axis_table.AXIS_NAMES) | {"fingerprint", "extra"}


def test_every_axis_says_how_a_comparison_treats_it() -> None:
    """
    Given  the axis table
    When   each axis is read
    Then   each one names a compare role, so a new axis cannot be added without that decision
    """
    assert [
        axis.name for axis in axis_table.AXES if axis.compare_role not in axis_table.COMPARE_ROLES
    ] == []


def test_a_new_axis_round_trips_through_the_key_the_tag_and_the_label(monkeypatch) -> None:
    """
    Given  an axis added to the table and to the protocol, and nothing else
    When   a header is written, read, keyed, named and labelled
    Then   the axis is in all of them, so adding one is one declaration and one field
    """
    extra_axis = axis_table.Axis("thinking", "thinking", False, compare_role=axis_table.FIXED)
    monkeypatch.setattr(axis_table, "AXES", (*axis_table.AXES, extra_axis))
    monkeypatch.setattr(axis_table, "AXIS_NAMES", tuple(axis.name for axis in axis_table.AXES))
    monkeypatch.setattr(axis_table, "AXIS_BY_NAME", {axis.name: axis for axis in axis_table.AXES})
    monkeypatch.setattr(axes, "AXES", axis_table.AXES)
    base = _header().protocol
    fields = [
        (axis.field, object, dataclasses.field(default=getattr(base, axis.field)))
        for axis in axis_table.AXES
        if axis.field != "thinking"
    ]
    protocol = dataclasses.make_dataclass(
        "Protocol",
        [*fields, ("thinking", bool, dataclasses.field(default=False))],
    )(thinking=True)

    written = {axis.name: axis.dump(getattr(protocol, axis.field)) for axis in axis_table.AXES}

    assert written["thinking"] is True
    assert axis_table.axis_label("thinking", True) == "true"
    assert axes.axis_tag("thinking", True) == "true"


def test_adding_a_defaulted_axis_moves_no_key_and_no_file_name(monkeypatch) -> None:
    """
    Given  a header, and an axis added to the table with a default
    When   the compare key, the run key and the file name are built
    Then   none of them moved, so a new axis does not redraw samples or caches
    """
    header = _header()
    before = (
        axes.compare_key_sha256(header),
        axes.run_key_sha256(header),
        axes.run_file_name(header, "2026-11-16"),
    )
    extra_axis = axis_table.Axis("thinking", "thinking", False, compare_role=axis_table.FIXED)
    monkeypatch.setattr(axis_table, "AXES", (*axis_table.AXES, extra_axis))
    monkeypatch.setattr(axes, "AXES", axis_table.AXES)
    monkeypatch.setattr(
        axis_table,
        "axis_values",
        lambda protocol: {
            **{
                axis.name: axis.dump(getattr(protocol, axis.field)) for axis in axis_table.AXES[:-1]
            },
            "thinking": False,
        },
    )
    monkeypatch.setattr(axes, "axis_values", axis_table.axis_values)

    after = (
        axes.compare_key_sha256(header),
        axes.run_key_sha256(header),
        axes.run_file_name(header, "2026-11-16"),
    )

    assert after == before


def test_an_ollama_run_without_a_machine_is_named_unknown_machine() -> None:
    """
    Given  an Ollama run that recorded no machine
    When   its host is asked for
    Then   it is ``unknown-machine`` and not the provider, which is always set
    """
    header = _header(environment=Environment(machine="", ollama_version="0.40.16"))

    assert axes.host_of(header) == axes.UNKNOWN_MACHINE


def test_an_online_run_is_named_after_its_provider() -> None:
    """
    Given  a run of a provider that ran on none of our machines
    When   its host is asked for
    Then   it is the provider
    """
    header = _header(
        protocol=Protocol(model="mistral-large", provider="mistral"),
        environment=Environment(machine=""),
    )

    assert axes.host_of(header) == "mistral"


def test_two_model_tags_that_differ_read_differently() -> None:
    """
    Given  two model tags that share their first sixteen characters
    When   each is labelled
    Then   the two labels differ, so a report does not print one model twice
    """
    first = axis_table.axis_label("model", "qwen3:30b-a3b-instruct-q4_K_M")
    second = axis_table.axis_label("model", "qwen3:30b-a3b-thinking-q4_K_M")

    assert first != second


def test_a_digest_is_labelled_by_its_eight_hex_digits() -> None:
    """
    Given  a model digest in Ollama's form
    When   it is labelled
    Then   the label is its eight hex digits, not ``sha256:a``
    """
    assert axis_table.axis_label("model_digest", "sha256:6114515dabc123") == "6114515d"


def test_temperature_is_labelled_with_its_name() -> None:
    """
    Given  a temperature
    When   it is labelled
    Then   the label says ``temperature``, never ``T``
    """
    assert axis_table.axis_label("temperature", 0.4) == "temperature 0.4"


def test_an_unknown_axis_is_labelled_as_unknown() -> None:
    """
    Given  a compare-key entry of an axis this reader does not know
    When   it is labelled
    Then   the label says so, instead of printing a bare value
    """
    assert axis_table.axis_label("unknown.thinking", True) == "unknown axis thinking=true"


# ── where a path on a command line points ────────────────────────────────────


def test_a_relative_path_is_relative_to_the_repository(monkeypatch, tmp_path) -> None:
    """
    Given  a tool started by ``bazel run``, which names the repository in the environment
    When   a relative path and an absolute one are resolved
    Then   the relative one is the repository's, so it does not point into the runfiles tree, and
           the absolute one is left as typed
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path / "repo"))

    assert paths.workspace_path("docs/x.md") == tmp_path / "repo" / "docs" / "x.md"
    assert paths.workspace_path(str(tmp_path / "elsewhere")) == tmp_path / "elsewhere"


def test_a_store_inside_the_app_repository_is_refused(monkeypatch, tmp_path) -> None:
    """
    Given  the app repository's working tree
    When   a store inside it is named, and one outside it
    Then   the one inside is refused with the store that is meant, because the records hold the
           corpora's text and a slip in this repository's history cannot be taken back
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path / "repo"))
    monkeypatch.setattr(paths, "repository_root", lambda: tmp_path / "repo")

    with pytest.raises(ValueError, match="inside the app repository"):
        paths.store_path("store")
    with pytest.raises(ValueError, match="teachers-teammate-evals"):
        paths.store_path(str(tmp_path / "repo" / "evaluations"))
    assert paths.store_path(str(tmp_path / "elsewhere")) == tmp_path / "elsewhere"


def test_a_refused_store_is_a_message_on_the_command_line(monkeypatch, tmp_path) -> None:
    """
    Given  a store inside the app repository
    When   it is read as an ``argparse`` argument
    Then   the refusal is an argument error, so the command line prints it and exits instead of
           raising through the caller
    """
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(tmp_path / "repo"))
    monkeypatch.setattr(paths, "repository_root", lambda: tmp_path / "repo")

    with pytest.raises(argparse.ArgumentTypeError, match="inside the app repository"):
        paths.store_argument("store")


def test_the_guard_does_not_depend_on_the_workspace_the_tool_started_in(
    monkeypatch, tmp_path
) -> None:
    """
    Given  a tool started by ``bazel run`` from the evaluation repository, whose workspace Bazel
           names in the environment, from a directory that is that repository
    When   the app repository's own folder and the evaluation repository are named
    Then   the first is refused and the second accepted, because the app repository is found from
           the tool's own files and not from the workspace or the current directory
    """
    other = tmp_path / "evals"
    other.mkdir()
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(other))
    monkeypatch.chdir(other)

    with pytest.raises(ValueError, match="inside the app repository"):
        paths.store_path(str(paths.repository_root() / "store"))
    assert paths.store_path(str(other)) == other
    assert paths.store_path(".") == other
