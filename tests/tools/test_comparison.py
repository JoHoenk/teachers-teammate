"""Unit tests for the writing-model comparison runner and its two reports."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import CorrectionOutcome
from teachers_teammate.infrastructure.correction import FALLBACK_WARNING
from tools.model_eval.comparison import WritingModelComparison
from tools.model_eval.report import summarise, table
from tools.model_eval import results as results_module
from tools.model_eval.results import ResultRow

_RECORD = results_module.TestRecord(
    id="de-01",
    language="de",
    kind="errors",
    origin="Falko-MERLIN test",
    source="Gestern hat sie ins Kino gegangen mit sein Freundin.",
    reference="Gestern ist sie mit ihrer Freundin ins Kino gegangen.",
)


class _ScriptedCorrector:
    """A corrector that answers with a fixed text and records the calls."""

    def __init__(self, text: str, *, failed: bool = False, warning: str = "") -> None:
        self._text = text
        self._failed = failed
        self._warning = warning
        self.calls: list[tuple[str, str]] = []

    def correct(self, text: str, language: str) -> CorrectionOutcome:
        """Record the call and return the scripted answer."""
        self.calls.append((text, language))
        return CorrectionOutcome(
            text=self._text,
            review=None,
            failed=self._failed,
            warning=self._warning or None,
        )


def _comparison(corrector) -> WritingModelComparison:
    """Return a comparison whose factory always returns *corrector*."""
    return WritingModelComparison(lambda model, temperature: corrector, clock=lambda: 0.0)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_one_row_is_produced_per_test_record() -> None:
    """
    Given  one test record and a corrector with a scripted answer
    When   the comparison runs
    Then   there is one row, naming the language and the id of the record
    """
    rows = list(
        _comparison(_ScriptedCorrector(_RECORD.reference)).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2
        )
    )
    assert len(rows) == 1
    assert rows[0].id == "de/de-01"
    assert rows[0].language == "de"


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_corrector_is_asked_with_the_text_and_the_language_name() -> None:
    """
    Given  a German test record
    When   the comparison runs
    Then   the corrector is asked with the source text and the language name the app uses
    """
    corrector = _ScriptedCorrector(_RECORD.reference)
    list(_comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2))
    assert corrector.calls == [(_RECORD.source, "German")]


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_reference_answer_scores_a_perfect_precision() -> None:
    """
    Given  a corrector that returns the reference correction
    When   the comparison scores it
    Then   every reference change counts as found and none as invented
    """
    row = next(
        iter(
            _comparison(_ScriptedCorrector(_RECORD.reference)).run(
                [_RECORD], model="gemma4:e4b", temperature=0.2
            )
        )
    )
    assert (row.true_positives, row.false_positives, row.false_negatives) == (3, 0, 0)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_an_unchanged_answer_misses_the_reference_change() -> None:
    """
    Given  a corrector that returns the source untouched
    When   the comparison scores it
    Then   the reference changes count as not found
    """
    row = next(
        iter(
            _comparison(_ScriptedCorrector(_RECORD.source)).run(
                [_RECORD], model="gemma4:e4b", temperature=0.2
            )
        )
    )
    assert (row.true_positives, row.false_negatives) == (0, 3)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_fallback_answer_is_flagged_and_not_counted_as_failed() -> None:
    """
    Given  a corrector that fell back to corrections only
    When   the comparison runs
    Then   the row is marked as a fallback, so the report shows the missing explanations
    """
    row = next(
        iter(
            _comparison(_ScriptedCorrector(_RECORD.reference, warning=FALLBACK_WARNING)).run(
                [_RECORD], model="gemma4:e4b", temperature=0.2
            )
        )
    )
    assert row.fallback
    assert not row.failed


@pytest.mark.use_case("Model_Eval_Comparison")
def test_ids_already_done_are_skipped() -> None:
    """
    Given  a run whose only id is already stored
    When   the comparison is asked to resume
    Then   no corrector call is made, so an interrupted run costs nothing twice
    """
    corrector = _ScriptedCorrector(_RECORD.reference)
    rows = list(
        _comparison(corrector).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2, done={"de/de-01"}
        )
    )
    assert rows == []
    assert corrector.calls == []


def _result_row(**overrides) -> ResultRow:
    """Return a result row with sensible defaults."""
    values = {
        "id": "de/de-01",
        "model": "gemma4:e4b",
        "temperature": 0.2,
        "language": "de",
        "kind": "errors",
        "origin": "Falko-MERLIN test",
        "seconds": 10.0,
        "failed": False,
        "fallback": False,
        "has_review": True,
        "source": "a b c",
        "output": "a b d",
        "reference": "a b d",
        "true_positives": 1,
        "explained": 1,
        "needed": 1,
    }
    values.update(overrides)
    return ResultRow(**values)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_table_summarises_the_scored_rows() -> None:
    """
    Given  one scored German row
    When   the summaries and the table are built
    Then   there is one summary for the model and the table names it with its F-score
    """
    summaries = summarise([_result_row()])
    assert len(summaries) == 1
    rendered = table(summaries)
    assert "gemma4:e4b" in rendered
    assert "1.00" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_a_language_without_an_error_text_has_no_summary() -> None:
    """
    Given  rows of already-correct texts only
    When   the summaries are built
    Then   there is none, because there is nothing to score for that language
    """
    rows = [_result_row(kind="correct", sentences=("a b c",), unchanged=1)]
    assert summarise(rows) == []


@pytest.mark.use_case("Model_Eval_Report")
def test_the_mean_f_score_is_reported_per_model() -> None:
    """
    Given  a scored row of one model
    When   the table is built
    Then   it ends with the mean F-score of that model over the languages it has
    """
    rendered = table(summarise([_result_row()]))
    assert "`gemma4:e4b` at 0.2: 1.000 (1 languages)" in rendered
