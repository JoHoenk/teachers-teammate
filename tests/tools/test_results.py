"""Unit tests for the versioned result rows and test records of the writing-model tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.model_eval import results as results_module
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.error_labels import ReferenceLabel
from tools.model_eval.performance import Performance, RequestStats
from tools.model_eval.results import (
    SCHEMA_VERSION,
    ResultRow,
    added_marks,
    missing_ids,
    read_all,
    read_done_ids,
    read_labels,
    read_records,
    read_rows,
    result_file_name,
    rescore,
    write_records,
    write_rows,
)


def _row(**overrides) -> ResultRow:
    """Return a result row of an error text, with sensible defaults."""
    values = {
        "id": "de/de-01",
        "model": "gemma4:e4b",
        "temperature": 0.2,
        "language": "de",
        "kind": "errors",
        "origin": "Falko-MERLIN test",
        "seconds": 12.5,
        "failed": False,
        "fallback": False,
        "has_review": True,
        "source": "Gestern hat sie ins Kino gegangen mit sein Freundin.",
        "output": "Gestern ist sie mit ihrer Freundin ins Kino gegangen.",
        "reference": "Gestern ist sie mit ihrer Freundin ins Kino gegangen.",
    }
    values.update(overrides)
    return ResultRow(**values)


def _record(**overrides) -> results_module.TestRecord:
    """Return a test record of an error text, with sensible defaults."""
    values = {
        "id": "de-01",
        "language": "de",
        "kind": "errors",
        "origin": "Falko-MERLIN test",
        "source": "Gestern hat sie ins Kino gegangen mit sein Freundin.",
        "reference": "Gestern ist sie mit ihrer Freundin ins Kino gegangen.",
    }
    values.update(overrides)
    return results_module.TestRecord(**values)


@pytest.mark.use_case("Model_Eval_Results")
def test_a_written_row_carries_the_schema_version(tmp_path: Path) -> None:
    """
    Given  a result row
    When   it is written and read back
    Then   the stored line names the schema version and the row comes back unchanged
    """
    path = tmp_path / "results.jsonl"
    write_rows(path, [_row()])
    line = json.loads(path.read_text(encoding="utf-8"))
    assert line["schema_version"] == SCHEMA_VERSION
    assert read_rows(path) == [_row()]


@pytest.mark.use_case("Model_Eval_Results")
def test_the_raw_output_is_not_stored(tmp_path: Path) -> None:
    """
    Given  a row whose raw output differs from its output
    When   it is written
    Then   only the output is stored, so a re-scoring always starts from the plain text
    """
    path = tmp_path / "results.jsonl"
    write_rows(path, [_row(output="a b c", raw_output="a **b** c")])
    assert "raw_output" not in json.loads(path.read_text(encoding="utf-8"))
    assert read_rows(path)[0].output == "a b c"


@pytest.mark.use_case("Model_Eval_Results")
def test_rows_are_appended_so_an_interrupted_run_resumes(tmp_path: Path) -> None:
    """
    Given  two rows written one after the other
    When   the file is read
    Then   both are there, in the order they were run, and their ids say what is done
    """
    path = tmp_path / "results.jsonl"
    write_rows(path, [_row()])
    write_rows(path, [_row(id="de/de-02")])
    assert [row.id for row in read_rows(path)] == ["de/de-01", "de/de-02"]
    assert read_done_ids(path) == {"de/de-01", "de/de-02"}


@pytest.mark.use_case("Model_Eval_Results")
def test_a_missing_results_file_has_nothing_done(tmp_path: Path) -> None:
    """
    Given  a results file that does not exist yet
    When   the ids already done are read
    Then   the set is empty, so the first run starts from the beginning
    """
    assert read_done_ids(tmp_path / "missing.jsonl") == set()


@pytest.mark.use_case("Model_Eval_Results")
def test_a_row_without_a_version_is_read_as_the_older_format(tmp_path: Path) -> None:
    """
    Given  a results line written before the schema was versioned
    When   it is read
    Then   it is accepted and mapped, so the earlier results stay readable
    """
    path = tmp_path / "old.jsonl"
    stored = _row().to_dict()
    del stored["schema_version"]
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    assert read_rows(path)[0].id == "de/de-01"


@pytest.mark.use_case("Model_Eval_Results")
def test_an_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    """
    Given  a results line of a schema version this tool does not know
    When   it is read
    Then   a ValueError says so instead of silently guessing the meaning of the fields
    """
    path = tmp_path / "future.jsonl"
    stored = _row().to_dict() | {"schema_version": SCHEMA_VERSION + 1}
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported result schema_version"):
        read_rows(path)


@pytest.mark.use_case("Model_Eval_Results")
def test_a_written_test_record_comes_back_unchanged(tmp_path: Path) -> None:
    """
    Given  a test record
    When   it is written and read back
    Then   every field is the same, and the sentences are a tuple again
    """
    path = tmp_path / "de.jsonl"
    record = _record(kind="correct", sentences=("Ein Satz.", "Noch einer."))
    write_records(path, [record])
    assert read_records(path) == [record]


@pytest.mark.use_case("Model_Eval_Results")
def test_a_record_without_band_or_sentences_writes_the_line_it_wrote_before(tmp_path: Path) -> None:
    """
    Given  a record without a band and without sentence spans
    When   it is written
    Then   the line and the hash of the records are the ones of a record built without the fields,
           so the old set keeps its records_sha256
    """
    path = tmp_path / "de.jsonl"
    record = _record()
    write_records(path, [record])
    line = json.loads(path.read_text(encoding="utf-8"))

    assert "band" not in line
    assert "sentence_spans" not in line
    assert "unchanged_sentences" not in line
    assert results_module.records_sha256([record]) == results_module.records_sha256(
        [results_module.TestRecord.from_dict(line)]
    )


@pytest.mark.use_case("Model_Eval_Results")
def test_band_and_sentence_spans_survive_a_round_trip(tmp_path: Path) -> None:
    """
    Given  a record with a band, sentence spans and the indices of the unchanged sentences
    When   it is written and read back
    Then   the record is the same, with the spans as tuples of tuples
    """
    path = tmp_path / "de.jsonl"
    record = _record(band="page", sentence_spans=((0, 8), (8, 14)), unchanged_sentences=(1,))
    write_records(path, [record])

    read = read_records(path)

    assert read == [record]
    assert read[0].sentence_spans == ((0, 8), (8, 14))


@pytest.mark.use_case("Model_Eval_Results")
def test_a_test_record_of_an_unknown_version_is_rejected(tmp_path: Path) -> None:
    """
    Given  a test record of a schema version this tool does not know
    When   it is read
    Then   a ValueError says so
    """
    path = tmp_path / "de.jsonl"
    stored = _record().to_dict() | {"schema_version": 99}
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported test record schema_version"):
        read_records(path)


@pytest.mark.use_case("Model_Eval_Results")
def test_all_results_files_of_a_directory_are_read(tmp_path: Path) -> None:
    """
    Given  two results files, one per model
    When   the whole directory is read
    Then   the rows of both are returned, in file-name order
    """
    write_rows(tmp_path / "a.jsonl", [_row(model="gemma4:e4b")])
    write_rows(tmp_path / "b.jsonl", [_row(model="qwen3:4b")])
    assert [row.model for row in read_all(tmp_path)] == ["gemma4:e4b", "qwen3:4b"]


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_recomputes_the_score_from_the_stored_texts() -> None:
    """
    Given  a row whose stored counts are wrong
    When   it is re-scored
    Then   the counts match the stored source, output and reference
    """
    row = _row(true_positives=99, false_positives=99, false_negatives=99)
    updated = rescore(row, count_marks=False)
    assert updated.true_positives == 3
    assert updated.false_positives == 0
    assert updated.false_negatives == 0


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_keeps_the_original_output_for_a_later_check() -> None:
    """
    Given  a row whose output carries Markdown marks the source does not have
    When   it is re-scored without counting marks
    Then   the marks are gone from the output and kept in the raw output
    """
    row = _row(output="Gestern **ist** sie ins Kino gegangen.")
    updated = rescore(row, count_marks=False)
    assert updated.output == "Gestern ist sie ins Kino gegangen."
    assert updated.raw_output == "Gestern **ist** sie ins Kino gegangen."


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_a_correct_text_counts_the_untouched_sentences() -> None:
    """
    Given  a row of an already-correct text whose answer kept one of two sentences
    When   it is re-scored
    Then   one of two sentences counts as unchanged and no edit is scored
    """
    row = _row(
        kind="correct",
        source="Ein Satz. Noch einer.",
        output="Ein Satz. Ein anderer.",
        sentences=("Ein Satz.", "Noch einer."),
    )
    updated = rescore(row, count_marks=False)
    assert updated.unchanged == 1
    assert updated.true_positives == 0


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_with_the_record_recomputes_the_band_and_the_numbers() -> None:
    """
    Given  a row and the test record of its text, with a band and sentence spans
    When   the row is re-scored with the record
    Then   the band and the numbers are the ones the record's spans give
    """
    source = "Gestern hat sie ins Kino gegangen . Mit sein Freundin ."
    reference = "Gestern ist sie ins Kino gegangen . Mit sein Freundin ."
    record = _record(
        source=source,
        reference=reference,
        band="page",
        sentence_spans=((0, 13), (14, 21)),
        unchanged_sentences=(1,),
    )
    row = _row(source=source, reference=reference, output=reference)

    updated = rescore(row, count_marks=False, record=record)

    assert updated.band == "page"
    assert (updated.scores["alone"], updated.scores["alone_of"]) == (1, 1)
    assert (updated.scores["cover"], updated.scores["cover_of"]) == (1, 1)
    assert updated.scores["tp_h1"] == 1


@pytest.mark.use_case("Model_Eval_Results")
def test_a_review_mode_fallback_counts_its_changes_as_unexplained() -> None:
    """
    Given  a review-mode row that fell back to corrections only, with no explanation stored
    When   it is re-scored
    Then   its changes need an explanation and none is explained, so falling back cannot help
    """
    row = _row(mode=AnswerMode.REVIEW, fallback=True, has_review=False, explained=0, needed=0)

    updated = rescore(row, count_marks=False)

    assert updated.needed > 0
    assert updated.explained == 0


@pytest.mark.use_case("Model_Eval_Results")
def test_a_text_mode_row_needs_no_explanation() -> None:
    """
    Given  a text-mode row, which was never asked for explanations
    When   it is re-scored
    Then   nothing needs an explanation, so the explained share stays empty
    """
    row = _row(mode=AnswerMode.TEXT, fallback=False, has_review=False, explained=0, needed=0)

    updated = rescore(row, count_marks=False)

    assert updated.needed == 0


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_without_the_record_keeps_the_stored_numbers() -> None:
    """
    Given  a row that already carries a band and numbers
    When   it is re-scored without its test record
    Then   both are kept, because a row alone does not know the spans of its text
    """
    row = _row(band="page", scores={"gleu_c": 7})

    updated = rescore(row, count_marks=False)

    assert updated.band == "page"
    assert updated.scores == {"gleu_c": 7}


@pytest.mark.use_case("Model_Eval_Results")
def test_a_row_written_before_the_numbers_were_stored_reads_as_before(tmp_path: Path) -> None:
    """
    Given  a stored line without a band and without numbers
    When   it is read back
    Then   the row has an empty band and no numbers, and still reports
    """
    path = tmp_path / "results.jsonl"
    line = _row().to_dict()
    del line["band"], line["scores"]
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")

    (row,) = read_rows(path)

    assert row.band == ""
    assert row.scores == {}
    assert rescore(row, count_marks=False).true_positives == 3


@pytest.mark.use_case("Model_Eval_Results")
def test_marks_the_source_lacks_are_reported() -> None:
    """
    Given  a row whose output uses bold text the source does not have
    When   the added marks are checked
    Then   they are reported, because a teacher would see literal asterisks
    """
    assert added_marks(_row(output="Gestern **ist** sie ins Kino gegangen."))


@pytest.mark.use_case("Model_Eval_Results")
def test_marks_the_source_already_has_are_not_reported() -> None:
    """
    Given  a Markdown source that already uses bold text
    When   the added marks are checked
    Then   nothing is reported, because the model added nothing
    """
    row = _row(source="Gestern **ist** sie ins Kino gegangen.", output="Gestern **ist** sie da.")
    assert not added_marks(row)


@pytest.mark.use_case("Model_Eval_Results")
def test_a_row_written_before_the_mode_was_recorded_is_a_review_row(tmp_path: Path) -> None:
    """
    Given  a stored result line without the mode and the category fields
    When   it is read
    Then   it is a review row with nothing checked for categories
    """
    path = tmp_path / "old.jsonl"
    stored = _row().to_dict()
    for name in ("mode", "category_checked", "category_agreed"):
        del stored[name]
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")

    row = read_rows(path)[0]

    assert row.mode == "review"
    assert (row.category_checked, row.category_agreed) == (0, 0)


@pytest.mark.use_case("Model_Eval_Results")
def test_the_labels_of_a_test_record_survive_writing_and_reading(tmp_path: Path) -> None:
    """
    Given  a test record with the corpus labels of its reference changes
    When   it is written and read back
    Then   the labels are the same objects again, not dicts
    """
    path = tmp_path / "de.jsonl"
    record = _record(labels=(ReferenceLabel(2, 3, ("ist",), "R:VERB:TENSE"),))

    write_records(path, [record])

    assert read_records(path) == [record]
    assert read_records(path)[0].labels[0].category == "grammar"


@pytest.mark.use_case("Model_Eval_Results")
def test_a_test_record_without_labels_is_still_readable(tmp_path: Path) -> None:
    """
    Given  a test record line written before the labels existed
    When   it is read
    Then   it has no labels, so the older test sets keep working
    """
    path = tmp_path / "de.jsonl"
    stored = _record().to_dict()
    del stored["labels"]
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")

    assert read_records(path)[0].labels == ()


@pytest.mark.use_case("Model_Eval_Results")
def test_labels_are_looked_up_by_language_and_id(tmp_path: Path) -> None:
    """
    Given  a test set directory with one labelled record
    When   the labels are read
    Then   they are keyed like the result rows (language/id)
    """
    label = ReferenceLabel(2, 3, ("ist",), "R:VERB:TENSE")
    write_records(tmp_path / "de.jsonl", [_record(labels=(label,))])

    assert read_labels(tmp_path) == {"de/de-01": (label,)}


@pytest.mark.use_case("Model_Eval_Results")
def test_the_performance_of_a_text_survives_writing_and_reading(tmp_path: Path) -> None:
    """
    Given  a result row with the requests, memory and seed of its text
    When   it is written and read back
    Then   the row comes back with the same performance record
    """
    performance = Performance(
        requests=(RequestStats("m:1b", 200, 100, 0.0, 0.1, 1.0, 1.2, 1.3, "stop"),),
        vram_peak_mb=6200,
        vram_baseline_mb=700,
        seed=5,
    )
    path = tmp_path / "results.jsonl"

    write_rows(path, [_row(performance=performance)])

    assert read_rows(path)[0].performance == performance


@pytest.mark.use_case("Model_Eval_Results")
def test_a_row_written_before_performance_was_recorded_has_none(tmp_path: Path) -> None:
    """
    Given  a stored result line without a performance field
    When   it is read
    Then   the row has no performance, so the older results stay readable
    """
    path = tmp_path / "old.jsonl"
    stored = _row().to_dict()
    del stored["performance"]
    path.write_text(json.dumps(stored) + "\n", encoding="utf-8")

    assert read_rows(path)[0].performance is None


@pytest.mark.use_case("Model_Eval_Results")
@pytest.mark.parametrize(
    ("mode", "repeat", "expected"),
    [
        (AnswerMode.REVIEW, None, "qwen3.5_4b__T0.2.jsonl"),
        (AnswerMode.TEXT, None, "qwen3.5_4b__T0.2__text.jsonl"),
        (AnswerMode.REVIEW, 2, "qwen3.5_4b__T0.2__r2.jsonl"),
        (AnswerMode.TEXT, 3, "qwen3.5_4b__T0.2__text__r3.jsonl"),
    ],
)
def test_result_files_are_named_by_model_temperature_mode_and_repeat(
    mode: AnswerMode, repeat: int | None, expected: str
) -> None:
    """
    Given  a model, a temperature, an answer mode and perhaps a repeat number
    When   the file name is built
    Then   a single review run keeps the old name and the others get a suffix
    """
    assert result_file_name("qwen3.5:4b", 0.2, mode, repeat) == expected


@pytest.mark.use_case("Model_Eval_Results")
def test_a_model_from_a_registry_path_gets_a_file_safe_name() -> None:
    """
    Given  a Hugging Face model tag with slashes and a colon
    When   the file name is built
    Then   neither character is in it
    """
    name = result_file_name("hf.co/Colby/apertus-v1.5-8b-text-Q4_K_M-GGUF", 0.2)

    assert "/" not in name and ":" not in name


def _text_record(language: str, record_id: str) -> results_module.TestRecord:
    return results_module.TestRecord(
        id=record_id, language=language, kind="errors", origin="test", source="Text."
    )


@pytest.mark.use_case("Model_Eval_Results")
def test_a_language_not_yet_run_is_missing_even_when_other_languages_are_done() -> None:
    """
    Given  a results file that holds the rows of English and French, more than German has texts
    When   the ids still missing for a run of the German texts alone are asked for
    Then   every German text is missing, because done ids are compared by name, not by count
    """
    done = {"en/en-01", "en/en-02", "fr/fr-01", "fr/fr-02", "fr/fr-03"}
    records = [_text_record("de", "de-01"), _text_record("de", "de-02")]

    assert missing_ids(records, done) == ["de/de-01", "de/de-02"]


@pytest.mark.use_case("Model_Eval_Results")
def test_nothing_is_missing_when_every_text_has_its_row() -> None:
    """
    Given  a results file with a row for every text of the run (and one more of another language)
    When   the ids still missing are asked for
    Then   there are none, so the run is complete
    """
    records = [_text_record("de", "de-01"), _text_record("en", "en-01")]
    done = {"de/de-01", "en/en-01", "fr/fr-01"}

    assert missing_ids(records, done) == []


def test_only_files_named_like_a_language_code_are_a_language(tmp_path):
    """
    Given  a test-set directory with de.jsonl, fr.jsonl, notes.jsonl and results.json
    When   the languages of the set are listed
    Then   only de and fr count
    """
    for name in ("de.jsonl", "fr.jsonl", "notes.jsonl", "results.json"):
        (tmp_path / name).write_text("")

    assert results_module.testset_languages(tmp_path) == ["de", "fr"]


@pytest.mark.use_case("Model_Eval_Results")
def test_rescoring_that_counts_the_marks_without_the_record_drops_the_stored_numbers() -> None:
    """
    Given  a row whose stored numbers were computed on the text without the marks
    When   it is re-scored with the marks counted and without its test record
    Then   the stored numbers are dropped, so they cannot contradict the scores that count marks
    """
    row = _row(band="page", scores={"gleu_c": 7, "tp_h1": 1})

    updated = rescore(row, count_marks=True)

    assert updated.scores == {}
    assert updated.band == "page"
