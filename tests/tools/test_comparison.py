"""Unit tests for the writing-model comparison runner and its two reports."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import (
    ChangeNote,
    CorrectionOutcome,
    CorrectionReview,
    SentenceNote,
)
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.infrastructure.correction import (
    FALLBACK_WARNING,
    MISSING_REVIEW_WARNING,
    ChunkingCorrector,
)
from teachers_teammate.interfaces import Corrector
from tools.model_eval.comparison import WritingModelComparison
from tools.model_eval.error_labels import ReferenceLabel
from tools.model_eval.performance import Performance, RequestStats
from tools.model_eval.gleu import add, from_scores, gleu
from tools.model_eval.item_scores import item_scores
from tools.model_eval.report import (
    band_table,
    error_table,
    performance_table,
    summarise,
    summarise_performance,
    table,
)
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


class _ScriptedCorrector(Corrector):
    """A corrector that answers with a fixed text and records the calls."""

    def __init__(
        self,
        text: str,
        *,
        failed: bool = False,
        warning: str = "",
        review: CorrectionReview | None = None,
    ) -> None:
        self._text = text
        self._failed = failed
        self._warning = warning
        self._review = review
        self.calls: list[tuple[str, str]] = []

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Record the call and return the scripted answer."""
        self.calls.append((raw_text, language))
        return CorrectionOutcome(
            text=self._text,
            review=self._review,
            failed=self._failed,
            warning=self._warning or None,
        )


class _BudgetedCorrector(_ScriptedCorrector):
    """A scripted corrector with a request budget, so a long text is sent in pieces."""

    def __init__(self, text: str, *, max_chars: int, **kwargs) -> None:
        super().__init__(text, **kwargs)
        self._max_chars = max_chars

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int:
        """Return the small budget this corrector claims."""
        return self._max_chars


def _comparison(corrector, **kwargs) -> WritingModelComparison:
    """Return a comparison whose factory always returns *corrector*."""
    return WritingModelComparison(lambda model, temperature: corrector, clock=lambda: 0.0, **kwargs)


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
def test_a_record_of_an_unknown_language_stops_the_run_before_the_first_request() -> None:
    """
    Given  a test record in a language the corrector has no name for
    When   the comparison runs
    Then   it raises before the first request, naming the code, so a run never stops halfway
    """
    corrector = _ScriptedCorrector(_RECORD.reference)
    unknown = results_module.TestRecord(
        id="xx-01",
        language="xx",
        kind="errors",
        origin="invented",
        source=_RECORD.source,
        reference=_RECORD.reference,
    )

    with pytest.raises(ValueError, match="no prompt language for 'xx'"):
        list(_comparison(corrector).run([_RECORD, unknown], model="gemma4:e4b", temperature=0.2))

    assert corrector.calls == []


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


_MARKED = results_module.TestRecord(
    id="de-03",
    language="de",
    kind="errors",
    origin="Falko-MERLIN test",
    source="Der Hund laufen schnell.",
    reference="Der Hund läuft schnell.",
)

_CORRECT = results_module.TestRecord(
    id="de-correct",
    language="de",
    kind="correct",
    origin="Falko-MERLIN test",
    source="Das ist ein Test. Der Hund läuft schnell.",
    sentences=("Das ist ein Test.", "Der Hund läuft schnell."),
)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_fix_wrapped_in_marks_is_one_true_positive_and_no_false_positive() -> None:
    """
    Given  a model that fixes the error and wraps its fix in bold marks
    When   the comparison scores it
    Then   the fix counts as found and nothing as invented, while the row keeps the raw output
    """
    (row,) = _comparison(_ScriptedCorrector("Der Hund **läuft** schnell.")).run(
        [_MARKED], model="gemma4:e4b", temperature=0.2
    )

    assert (row.true_positives, row.false_positives, row.false_negatives) == (1, 0, 0)
    assert results_module.added_marks(row)
    assert row.output == "Der Hund **läuft** schnell."


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_correct_sentence_that_only_gained_marks_is_unchanged() -> None:
    """
    Given  a model that leaves an already-correct text alone but marks one word of it
    When   the comparison counts the unchanged sentences
    Then   both sentences count as unchanged, because the marks are not part of the text
    """
    (row,) = _comparison(_ScriptedCorrector("Das ist ein Test. Der Hund **läuft** schnell.")).run(
        [_CORRECT], model="gemma4:e4b", temperature=0.2
    )

    assert row.unchanged == 2
    assert results_module.added_marks(row)


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


_BANDED = results_module.TestRecord(
    id="de-02",
    language="de",
    kind="errors",
    origin="Falko-MERLIN test",
    source="Gestern hat sie ins Kino gegangen . Mit sein Freundin .",
    reference="Gestern ist sie ins Kino gegangen . Mit sein Freundin .",
    band="page",
    sentence_spans=((0, 13), (14, 21)),
    unchanged_sentences=(1,),
)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_row_carries_the_band_and_the_numbers_of_its_text() -> None:
    """
    Given  a test record with a band and with sentence spans
    When   the comparison runs
    Then   the row names the band and stores the GLEU, the halves, left alone and coverage
    """
    (row,) = _comparison(_ScriptedCorrector(_BANDED.reference)).run(
        [_BANDED], model="gemma4:e4b", temperature=0.2
    )

    assert row.band == "page"
    assert {"gleu_c", "gleu_r", "gleu_src_c", "tp_h1", "fn_h2"} <= set(row.scores)
    assert (row.scores["alone"], row.scores["alone_of"]) == (1, 1)
    assert (row.scores["cover"], row.scores["cover_of"]) == (1, 1)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_review_mode_fallback_counts_its_changes_as_unexplained() -> None:
    """
    Given  a review-mode corrector that fell back to corrections only and changed the text
    When   the comparison runs
    Then   the changes need an explanation and none is explained, so the fallback costs the share
    """
    (row,) = _comparison(
        _ScriptedCorrector(_RECORD.reference, warning=FALLBACK_WARNING), mode="review"
    ).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert row.fallback
    assert row.needed > 0
    assert row.explained == 0


@pytest.mark.use_case("Model_Eval_Comparison")
def test_scoring_the_stored_row_again_gives_the_numbers_the_run_wrote() -> None:
    """
    Given  a row a review-mode fallback just wrote
    When   the stored row is scored again, as a report does
    Then   the edit counts and the explanation counts are the same, because both go through the
           same scoring rules
    """
    from tools.model_eval.results import rescore  # noqa: PLC0415

    (row,) = _comparison(
        _ScriptedCorrector(_RECORD.reference, warning=FALLBACK_WARNING), mode="review"
    ).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    again = rescore(row, count_marks=False)

    for name in (
        "true_positives",
        "false_positives",
        "false_negatives",
        "explained",
        "needed",
        "orphans",
        "unchanged",
    ):
        assert getattr(again, name) == getattr(row, name), name


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_mode_row_needs_no_explanations() -> None:
    """
    Given  a text-mode corrector that changed the text
    When   the comparison runs
    Then   no change needs an explanation, because text mode never promised one
    """
    (row,) = _comparison(_ScriptedCorrector(_RECORD.reference), mode="text").run(
        [_RECORD], model="gemma4:e4b", temperature=0.2
    )

    assert row.needed == 0
    assert row.explained == 0


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
    assert "`gemma4:e4b` at 0.2 (review): F0.5 1.00, GLEU — (1 languages)" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_a_language_that_scored_nothing_shows_a_dash_and_is_not_in_the_mean() -> None:
    """
    Given  a language whose only error text has no reference edit and no model edit
    When   the summaries and the table are built
    Then   the row shows a dash for its scores and the mean line leaves that language out
    """
    scored = _result_row()
    unscored = _result_row(
        id="en/en-01",
        language="en",
        output="a b c",
        true_positives=0,
        false_positives=0,
        false_negatives=0,
    )

    rendered = table(summarise([scored, unscored]))

    assert "| — | — | — |" in rendered
    assert "`gemma4:e4b` at 0.2 (review): F0.5 1.00, GLEU — (1 languages)" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_band_rows_add_up_to_the_row_of_all_texts() -> None:
    """
    Given  three German rows, two of them in one band and one in another
    When   the summaries are built
    Then   the counts of the band rows add up to the row over all texts, because both are counts
    """
    rows = [
        _result_row(id="de/de-01", band="short", true_positives=1, false_positives=1),
        _result_row(
            id="de/de-02", band="short", true_positives=0, false_positives=2, false_negatives=1
        ),
        _result_row(id="de/de-03", band="page", true_positives=2, false_negatives=2),
    ]

    summaries = summarise(rows)
    everything = next(summary for summary in summaries if not summary.band)
    bands = [summary for summary in summaries if summary.band]

    assert [summary.band for summary in bands] == ["short", "page"]
    assert sum(summary.rows for summary in bands) == everything.rows
    assert sum(summary.score.true_positives for summary in bands) == everything.score.true_positives
    assert (
        sum(summary.score.false_positives for summary in bands) == everything.score.false_positives
    )
    assert (
        sum(summary.score.false_negatives for summary in bands) == everything.score.false_negatives
    )


@pytest.mark.use_case("Model_Eval_Report")
def test_the_gleu_of_a_language_comes_from_the_summed_counts() -> None:
    """
    Given  two German rows whose GLEU differs
    When   the summary of the language is built
    Then   its GLEU is the one of the summed counts, not the mean of the two scores
    """
    first = _result_row(
        id="de/de-01",
        source="Der Hund laufen schnell .",
        output="Der Hund läuft schnell .",
        reference="Der Hund läuft schnell .",
        scores=item_scores(
            "Der Hund laufen schnell .", "Der Hund läuft schnell .", "Der Hund läuft schnell ."
        ),
    )
    second = _result_row(
        id="de/de-02",
        source="Die Kinder spielt gern .",
        output="Die Kinder spielt gern .",
        reference="Die Kinder spielen gern .",
        scores=item_scores(
            "Die Kinder spielt gern .", "Die Kinder spielt gern .", "Die Kinder spielen gern ."
        ),
    )

    summaries = summarise([first, second])
    assert len(summaries) == 1
    summary = summaries[0]
    summed = gleu(add([from_scores(first.scores), from_scores(second.scores)]))
    averaged = (gleu(from_scores(first.scores)) + gleu(from_scores(second.scores))) / 2

    assert summary.gleu == pytest.approx(summed)
    assert summary.gleu != pytest.approx(averaged)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_deciding_mean_ignores_french() -> None:
    """
    Given  a German, an English and a French row of one model
    When   the table is built
    Then   the deciding line means German and English only, and says French is reported only
    """
    rows = [
        _result_row(id="de/de-01", language="de", true_positives=1),
        _result_row(id="en/en-01", language="en", true_positives=0, false_positives=1),
        _result_row(id="fr/fr-01", language="fr", true_positives=1),
    ]

    rendered = table(summarise(rows))

    assert "F0.5 0.50, GLEU — (2 languages)" in rendered
    assert "French is reported only" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_pages_band_is_marked_as_a_diagnostic() -> None:
    """
    Given  two rows of the pages band, which has two texts per language
    When   the band table is built
    Then   the band carries its text count and the mark that it is a diagnostic
    """
    rows = [_result_row(id="de/de-01", band="pages"), _result_row(id="de/de-02", band="pages")]

    rendered = band_table(summarise(rows))

    assert "pages (2 texts, diagnostic)" in rendered


_LABELLED = results_module.TestRecord(
    id="de-02",
    language="de",
    kind="errors",
    origin="Falko-MERLIN test",
    source="Er gehen heim.",
    reference="Er geht heim.",
    labels=(ReferenceLabel(2, 3, ("geht",), "R:VERB:SVA"),),
)


def _review(category: str) -> CorrectionReview:
    """Return a review that explains "gehen → geht" with *category*."""
    note = ChangeNote(original="gehen", corrected="geht", category=category, explanation="x")
    return CorrectionReview(
        sentences=(SentenceNote(original="gehen", corrected="geht", changes=(note,)),)
    )


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_rows_carry_the_mode_the_comparison_runs_in() -> None:
    """
    Given  a comparison that is told it asks for the corrected text only
    When   it runs, with the corrector behind the chunking wrapper that carries no mode
    Then   the rows name that mode, so text and review results are never mixed up
    """
    corrector = ChunkingCorrector(_BudgetedCorrector(_RECORD.reference, max_chars=10_000))

    (row,) = _comparison(corrector, mode="text").run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert row.mode == "text"


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_comparison_given_no_mode_records_review() -> None:
    """
    Given  a comparison that is not told its mode
    When   it runs
    Then   the row is a review row, the app's default
    """
    (row,) = _comparison(_ScriptedCorrector(_RECORD.reference)).run(
        [_RECORD], model="gemma4:e4b", temperature=0.2
    )

    assert row.mode == "review"


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_over_the_budget_is_sent_in_pieces_and_the_row_says_how_many() -> None:
    """
    Given  a corrector with a budget smaller than the text, wrapped as the app wraps it
    When   the comparison runs
    Then   the text goes out in pieces, one request each, and the row counts them
    """
    corrector = ChunkingCorrector(_BudgetedCorrector(_RECORD.reference, max_chars=20))

    (row,) = _comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert row.pieces == len(corrector.inner.calls) > 1


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_within_the_budget_is_one_piece() -> None:
    """
    Given  a corrector whose budget is larger than the text
    When   the comparison runs
    Then   the text is one piece and one request
    """
    corrector = ChunkingCorrector(_BudgetedCorrector(_RECORD.reference, max_chars=10_000))

    (row,) = _comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert (row.pieces, len(corrector.inner.calls)) == (1, 1)


class _FallingBackCorrector(_BudgetedCorrector):
    """A budgeted corrector whose first piece falls back to corrections only."""

    def __init__(self, text: str, *, max_chars: int, **kwargs) -> None:
        super().__init__(text, max_chars=max_chars, **kwargs)
        self._answered = 0

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Fall back on the first piece, answer normally afterwards."""
        self._answered += 1
        outcome = super().correct(raw_text, language, text_format=text_format)
        if self._answered == 1:
            return CorrectionOutcome(text=outcome.text, warning=FALLBACK_WARNING)
        return outcome


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_piece_that_fell_back_flags_the_row_although_the_warnings_are_joined() -> None:
    """
    Given  a text whose first piece fell back to corrections only, so the warnings are joined
    When   the comparison runs
    Then   the row is flagged as a fallback, which a strict equality check would have missed
    """
    corrector = ChunkingCorrector(
        _FallingBackCorrector(_RECORD.reference, max_chars=20, review=_review("grammar"))
    )

    (row,) = _comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert MISSING_REVIEW_WARNING in row.warning, "the warnings of the pieces are joined"
    assert row.fallback is True
    assert row.failed is False


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_category_of_a_fixed_error_is_compared_with_its_corpus_label() -> None:
    """
    Given  a labelled error that the model fixed exactly and explained as grammar
    When   the comparison runs
    Then   the row counts one checked error and one agreeing note
    """
    corrector = _ScriptedCorrector(_LABELLED.reference, review=_review("grammar"))

    (row,) = _comparison(corrector).run([_LABELLED], model="gemma4:e4b", temperature=0.2)

    assert (row.category_checked, row.category_agreed) == (1, 1)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_no_category_is_checked_for_a_record_without_labels() -> None:
    """
    Given  a record without labels (an invented French text, say)
    When   the comparison runs
    Then   no category is checked
    """
    corrector = _ScriptedCorrector(_RECORD.reference, review=_review("grammar"))

    (row,) = _comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert (row.category_checked, row.category_agreed) == (0, 0)


def _found(**overrides) -> ResultRow:
    """Return the row of a model that fixed the labelled error exactly."""
    values = {
        "id": "de/de-02",
        "source": _LABELLED.source,
        "output": _LABELLED.reference,
        "reference": _LABELLED.reference,
        "category_checked": 1,
        "category_agreed": 1,
    }
    values.update(overrides)
    return _result_row(**values)


@pytest.mark.use_case("Model_Eval_Report")
def test_the_error_table_counts_found_errors_per_category() -> None:
    """
    Given  a model that fixed a labelled grammar error exactly, and one that left it untouched
    When   the errors are grouped by category
    Then   the first shows 1 (1) / 1 under grammar and the second 0 (0) / 1
    """
    rows = [
        _found(),
        _found(model="qwen3:4b", output=_LABELLED.source, category_checked=0, category_agreed=0),
    ]

    rendered = error_table(rows, {"de/de-02": _LABELLED.labels})

    assert "| `gemma4:e4b` | 0.2 | review | 1 (1) / 1 | 1 (1) / 1 |" in rendered
    assert "| `qwen3:4b` | 0.2 | review | 0 (0) / 1 | 0 (0) / 1 |" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_error_table_separates_noticed_from_fixed_exactly() -> None:
    """
    Given  a model that changed the labelled word, but to another form than the reference
    When   the errors are grouped
    Then   the cell shows 0 found exactly and 1 found in place
    """
    rows = [_found(output="Er ging heim.", category_checked=0, category_agreed=0)]

    rendered = error_table(rows, {"de/de-02": _LABELLED.labels})

    assert "| 0 (1) / 1 | 0 (1) / 1 |" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_error_table_can_group_by_the_corpus_word_type() -> None:
    """
    Given  a labelled verb error
    When   the errors are grouped by type
    Then   the column is the word type of the label, not the app category
    """
    rendered = error_table([_found()], {"de/de-02": _LABELLED.labels}, by="type")

    assert "| VERB | all |" in rendered
    assert "grammar" not in rendered.split("### Category")[0]


@pytest.mark.use_case("Model_Eval_Report")
def test_the_error_table_reports_the_category_agreement() -> None:
    """
    Given  a model whose one checked note agrees with the corpus label
    When   the error table is built
    Then   it ends with the agreement of that model, 1/1 (100%)
    """
    rendered = error_table([_found()], {"de/de-02": _LABELLED.labels})

    assert "| `gemma4:e4b` | 0.2 | review | 1/1 (100%) |" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_the_error_table_says_so_when_the_test_set_has_no_labels() -> None:
    """
    Given  result rows and a test set without labels
    When   the error table is built
    Then   it tells the user to rebuild the test set instead of printing an empty table
    """
    assert "build the test set again" in error_table([_found()], {})


class _Instrument:
    """An instrumentation that notes when it is called and returns a fixed record."""

    def __init__(self, record: Performance) -> None:
        self._record = record
        self.events: list[str] = []

    def begin(self) -> None:
        self.events.append("begin")

    def end(self) -> Performance:
        self.events.append("end")
        return self._record


class _OrderedCorrector(_ScriptedCorrector):
    """A scripted corrector that tells the instrument when the text is corrected."""

    def __init__(self, text: str, events: list[str]) -> None:
        super().__init__(text)
        self._events = events

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        self._events.append("correct")
        return super().correct(raw_text, language, text_format=text_format)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_instrumentation_wraps_each_text_and_its_record_lands_on_the_row() -> None:
    """
    Given  an instrumentation and a test record
    When   the comparison runs
    Then   the instrument begins before the text is corrected and ends after it, and the row
           carries the performance it returned
    """
    record = Performance(vram_peak_mb=6200)
    instrument = _Instrument(record)
    corrector = _OrderedCorrector(_RECORD.reference, instrument.events)
    comparison = WritingModelComparison(
        lambda model, temperature: corrector, clock=lambda: 0.0, instrumentation=instrument
    )

    (row,) = comparison.run([_RECORD], model="gemma4:e4b", temperature=0.2)

    assert instrument.events == ["begin", "correct", "end"]
    assert row.performance == record


@pytest.mark.use_case("Model_Eval_Comparison")
def test_without_an_instrumentation_the_row_has_no_performance() -> None:
    """
    Given  a comparison without an instrumentation
    When   it runs
    Then   the row has no performance record
    """
    (row,) = _comparison(_ScriptedCorrector(_RECORD.reference)).run(
        [_RECORD], model="gemma4:e4b", temperature=0.2
    )

    assert row.performance is None


def _timed(**overrides) -> ResultRow:
    """Return a row of a 10-word text that took 5 s, with one request of 100 answer tokens."""
    request = RequestStats("gemma4:e4b", 200, 100, 0.0, 0.1, 2.0, 2.2, 2.4, "stop")
    values = {
        "source": "eins zwei drei vier fünf sechs sieben acht neun zehn",
        "seconds": 5.0,
        "performance": Performance(requests=(request,), vram_peak_mb=6200, vram_baseline_mb=700),
    }
    values.update(overrides)
    return _result_row(**values)


@pytest.mark.use_case("Model_Eval_Report")
def test_speed_is_scaled_to_a_hundred_words_and_tokens_to_the_generation_time() -> None:
    """
    Given  a 10-word text that took 5 s and 100 answer tokens in 2 s of generation
    When   the performance is summarised
    Then   it is 50 s per 100 words, 50 answer tokens/s, 1000 answer tokens per 100 words and
           5500 MB of graphics memory added over the baseline
    """
    summary = summarise_performance([_timed()])[0]

    assert summary.seconds_per_100_words == 50.0
    assert summary.answer_tokens_per_s == 50.0
    assert summary.answer_tokens_per_100_words == 1000.0
    assert summary.vram_used_mb == 5500
    assert (summary.texts, summary.comparable) == (1, 1)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_shared_text_does_not_enter_the_speed_figures() -> None:
    """
    Given  one text that ran alone in 5 s and one that shared the server and took 50 s
    When   the performance is summarised
    Then   both count as texts but only the first is comparable and decides the speed
    """
    request = RequestStats("gemma4:e4b", 200, 100, 0.0, 0.1, 20.0, 22.0, 24.0, "stop")
    shared = _timed(
        seconds=50.0,
        performance=Performance(requests=(request,), shared=True),
        id="de/de-02",
    )

    summary = summarise_performance([_timed(), shared])[0]

    assert (summary.texts, summary.comparable) == (2, 1)
    assert summary.seconds_per_100_words == 50.0


@pytest.mark.use_case("Model_Eval_Report")
def test_rows_without_performance_are_left_out_of_the_performance_summary() -> None:
    """
    Given  a row written before performance was recorded
    When   the performance is summarised
    Then   there is no summary for it
    """
    assert summarise_performance([_result_row()]) == []


@pytest.mark.use_case("Model_Eval_Report")
def test_the_performance_table_shows_a_dash_for_what_could_not_be_measured() -> None:
    """
    Given  a measured text without a graphics memory sampler
    When   the table is built
    Then   the speed is printed and the memory column holds a dash
    """
    row = _timed(
        performance=Performance(
            requests=(RequestStats("gemma4:e4b", 200, 100, 0.0, 0.1, 2.0, 2.2, 2.4, "stop"),)
        )
    )

    rendered = performance_table(summarise_performance([row]))

    assert "| `gemma4:e4b` | 0.2 | review | 1 (1) | 50.0 | 50 | 1000 | 1.00 | - | - |" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_a_failed_correct_text_shows_in_the_reliability_columns_of_its_language() -> None:
    """
    Given  one error text and one already-correct text of a language, and the correct one failed
    When   the summaries are built
    Then   the language row counts both texts and the failure, as it did before bands existed
    """
    rows = [
        _result_row(),
        _result_row(
            id="de/de-02",
            kind="correct",
            sentences=("a b c",),
            unchanged=1,
            failed=True,
            seconds=30,
        ),
    ]

    summary = summarise(rows)[0]

    assert (summary.failed, summary.rows) == (1, 2)
    assert summary.seconds_per_text == 20
    assert (summary.alone, summary.alone_of) == (1, 1)


@pytest.mark.use_case("Model_Eval_Report")
def test_a_report_without_bands_prints_no_band_table() -> None:
    """
    Given  rows that belong to no band
    When   the band table is built
    Then   it is empty, not a table with only a header
    """
    assert band_table(summarise([_result_row()])) == ""
