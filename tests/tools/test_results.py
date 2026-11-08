"""Unit tests for the versioned result rows and test records of the writing-model tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.model_eval import results as results_module
from tools.model_eval.results import (
    SCHEMA_VERSION,
    ResultRow,
    added_marks,
    read_all,
    read_done_ids,
    read_records,
    read_rows,
    rescore,
    rows_by_model,
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
def test_rows_are_grouped_by_model_and_temperature() -> None:
    """
    Given  rows of two models at two temperatures
    When   they are grouped
    Then   there is one group per model and temperature, in a stable order
    """
    rows = [
        _row(model="qwen3:4b", temperature=0.2),
        _row(model="gemma4:e4b", temperature=0.2),
        _row(model="qwen3:4b", temperature=0.7),
    ]
    keys = [key for key, _ in rows_by_model(rows)]
    assert keys == [("gemma4:e4b", 0.2), ("qwen3:4b", 0.2), ("qwen3:4b", 0.7)]
