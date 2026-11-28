"""Tests of the asking half of a run: one answer per text, and what the answer records.

The comparison no longer scores: an :class:`~tools.evaluation.core.records.Answer` holds what
the model said and what it cost, and the edit counts, the explanation counts, the category
agreement and the item scores are :mod:`tools.evaluation.report.score`'s work.  Those are tested
in ``test_evaluation_score.py`` and ``test_evaluation_report_tables.py``; what is tested here is
the loop that sends the texts and the fields it fills in.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from teachers_teammate.exceptions import StageError, StageErrorKind
from teachers_teammate.infrastructure.proofreading.chunking import ChunkingCorrector
from teachers_teammate.interfaces import Corrector
from teachers_teammate.texts.review import EXPLANATIONS_MISSING
from tools.evaluation.core.performance import Performance, RequestStats
from tools.evaluation.core.records import Answer, TestRecord
from tools.evaluation.run.comparison import WritingModelComparison
from tools.evaluation.run.one_request.correction import FALLBACK_WARNING


def _single(answers: Iterable[Answer]) -> Answer:
    """Return the one answer of a run (astroid cannot follow ``(answer,) = run(...)`` on 3.14)."""
    found = list(answers)
    assert len(found) == 1
    return found[0]


_RECORD = TestRecord(
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
        fell_back: bool = False,
        left_out: int = 0,
    ) -> None:
        self._text = text
        self._failed = failed
        self._warning = warning
        self._review = review
        self._fell_back = fell_back
        self._left_out = left_out
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
            fell_back=self._fell_back,
            left_out=self._left_out,
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


def _review(source: str, original: str, corrected: str, category: str) -> CorrectionReview:
    """Return a review of *source* that explains *original* → *corrected* with *category*."""
    start = source.find(original)
    note = EditNote(
        id=1,
        parts=(EditPart(start, start + len(original), original, corrected),),
        sentences=(0,),
        category=category,
        explanation="x",
    )
    return CorrectionReview(edits=(note,))


@pytest.mark.use_case("Model_Eval_Comparison")
def test_one_answer_is_produced_per_test_record() -> None:
    """
    Given  one test record and a corrector with a scripted answer
    When   the comparison runs
    Then   there is one answer, naming the language and the id of the record
    """
    answers = list(
        _comparison(_ScriptedCorrector(_RECORD.reference)).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2
        )
    )

    assert len(answers) == 1
    assert answers[0].text == "de/de-01"
    assert answers[0].output == _RECORD.reference


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_record_of_an_unknown_language_stops_the_run_before_the_first_request() -> None:
    """
    Given  a test record in a language the corrector has no name for
    When   the comparison runs
    Then   it raises before the first request, naming the code, so a run never stops halfway
    """
    corrector = _ScriptedCorrector(_RECORD.reference)
    unknown = TestRecord(
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
def test_a_fallback_answer_is_flagged_and_not_counted_as_failed() -> None:
    """
    Given  a corrector that fell back to corrections only
    When   the comparison runs
    Then   the answer is marked as a fallback, so the report shows the missing explanations
    """
    answer = _single(
        _comparison(
            _ScriptedCorrector(_RECORD.reference, warning=FALLBACK_WARNING, fell_back=True)
        ).run([_RECORD], model="gemma4:e4b", temperature=0.2)
    )

    assert answer.fallback
    assert not answer.failed


@pytest.mark.use_case("Model_Eval_Comparison")
def test_the_count_of_put_back_stretches_lands_on_the_answer() -> None:
    """
    Given  a corrector whose answer left stretches out and had them put back
    When   the comparison runs
    Then   the answer carries the count, so the report can call the row lost text although the
           stored output is whole
    """
    answer = _single(
        _comparison(_ScriptedCorrector(_RECORD.reference, left_out=2)).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2
        )
    )

    assert answer.left_out == 2


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_fallback_is_read_from_the_outcome_and_not_from_its_warning() -> None:
    """
    Given  a corrector that carries the fallback sentence but did not fall back
    When   the comparison runs
    Then   the answer is not marked as a fallback, because the app's typed field decides and the
           sentence is only what a teacher reads
    """
    answer = _single(
        _comparison(_ScriptedCorrector(_RECORD.reference, warning=FALLBACK_WARNING)).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2
        )
    )

    assert answer.fallback is False


@pytest.mark.use_case("Model_Eval_Comparison")
def test_ids_already_done_are_skipped() -> None:
    """
    Given  a run whose only id is already stored
    When   the comparison is asked to resume
    Then   no corrector call is made, so an interrupted run costs nothing twice
    """
    corrector = _ScriptedCorrector(_RECORD.reference)

    answers = list(
        _comparison(corrector).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2, done={"de/de-01"}
        )
    )

    assert answers == []
    assert corrector.calls == []


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_over_the_budget_is_sent_in_pieces_and_the_answer_says_how_many() -> None:
    """
    Given  a corrector with a budget smaller than the text, wrapped as the app wraps it
    When   the comparison runs
    Then   the text goes out in pieces, one request each, and the answer counts them
    """
    corrector = ChunkingCorrector(_BudgetedCorrector(_RECORD.reference, max_chars=20))

    answer = _single(_comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert answer.pieces == len(corrector.inner.calls) > 1


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_within_the_budget_is_one_piece() -> None:
    """
    Given  a corrector whose budget is larger than the text
    When   the comparison runs
    Then   the text is one piece and one request
    """
    corrector = ChunkingCorrector(_BudgetedCorrector(_RECORD.reference, max_chars=10_000))

    answer = _single(_comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert (answer.pieces, len(corrector.inner.calls)) == (1, 1)


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
            return CorrectionOutcome(text=outcome.text, warning=FALLBACK_WARNING, fell_back=True)
        return outcome


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_piece_that_fell_back_flags_the_answer_although_the_warnings_are_joined() -> None:
    """
    Given  a text whose first piece fell back to corrections only, so the warnings are joined
    When   the comparison runs
    Then   the answer is flagged as a fallback, which a strict equality check would have missed
    """
    corrector = ChunkingCorrector(
        _FallingBackCorrector(
            _RECORD.reference,
            max_chars=20,
            review=_review(_RECORD.source, "sein", "ihrer", "grammar"),
        )
    )

    answer = _single(_comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert EXPLANATIONS_MISSING in answer.warning, "the warnings of the pieces are joined"
    assert answer.fallback is True
    assert answer.failed is False


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_failed_piece_of_a_long_text_fails_the_answer() -> None:
    """
    Given  a long text whose pieces are proofread by a corrector that reports a failed outcome
    When   the comparison runs
    Then   the answer is failed and keeps the source, so a half-corrected text is never scored
    """
    corrector = ChunkingCorrector(
        _BudgetedCorrector("x", max_chars=20, failed=True, warning="Correction failed.")
    )

    answer = _single(_comparison(corrector).run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert answer.failed is True
    assert answer.output == _RECORD.source
    assert answer.warning == "Correction failed."


class _Unreachable(Corrector):
    """A corrector whose request never reaches the model."""

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Raise the failure of the proofreading stage."""
        raise StageError("no server", kind=StageErrorKind.PROVIDER, stage="correction")


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_stage_error_is_a_failed_answer_and_not_the_end_of_the_run() -> None:
    """
    Given  a corrector that raises the failure of the proofreading stage
    When   the comparison runs
    Then   the answer is failed, keeps the source and carries what the stage said
    """
    answer = _single(
        _comparison(_Unreachable()).run([_RECORD], model="gemma4:e4b", temperature=0.2)
    )

    assert answer.failed is True
    assert answer.output == _RECORD.source
    assert answer.warning == "no server"


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
def test_the_instrumentation_wraps_each_text_and_its_record_lands_on_the_answer() -> None:
    """
    Given  an instrumentation and a test record
    When   the comparison runs
    Then   the instrument begins before the text is corrected and ends after it, and the answer
           carries the requests, the memory and the sharing it returned
    """
    record = Performance(
        requests=(RequestStats("gemma4:e4b", 100, 50, 0.1, 0.2, 0.3, 0.6, 0.7),),
        shared=True,
        vram_peak_mb=6200,
        vram_baseline_mb=700,
    )
    instrument = _Instrument(record)
    corrector = _OrderedCorrector(_RECORD.reference, instrument.events)
    comparison = WritingModelComparison(
        lambda model, temperature: corrector, clock=lambda: 0.0, instrumentation=instrument
    )

    answer = _single(comparison.run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert instrument.events == ["begin", "correct", "end"]
    assert answer.requests == record.requests
    assert answer.shared is True
    assert (answer.vram_peak_mb, answer.vram_baseline_mb) == (6200, 700)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_without_an_instrumentation_the_answer_has_no_performance() -> None:
    """
    Given  a comparison without an instrumentation
    When   it runs
    Then   the answer has no requests and no memory figure, and only the seconds of the text
    """
    answer = _single(
        _comparison(_ScriptedCorrector(_RECORD.reference)).run(
            [_RECORD], model="gemma4:e4b", temperature=0.2
        )
    )

    assert answer.requests == ()
    assert answer.vram_peak_mb is None
    assert answer.shared is False


def _request(step: str, answer: str) -> RequestStats:
    """Return one finished request of *step* that answered *answer*."""
    return RequestStats("gemma4:e4b", 100, 50, 0.1, 0.2, 0.3, 0.6, 0.7, step=step, answer=answer)


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_that_lost_text_keeps_the_models_own_step_one_answers() -> None:
    """
    Given  a text whose two step-① answers left a stretch out, which the app put back, and whose
           step ② answered too
    When   the comparison runs, and the answer is written and read back
    Then   the answer keeps both step-① answers in order and not step ②'s, the requests are written
           without their text, and the line reads back the same
    """
    record = Performance(
        requests=(
            _request("correct", "first try"),
            _request("correct", "second try"),
            _request("explain", "{}"),
        )
    )
    comparison = WritingModelComparison(
        lambda model, temperature: _ScriptedCorrector(_RECORD.reference, left_out=1),
        clock=lambda: 0.0,
        instrumentation=_Instrument(record),
    )

    answer = _single(comparison.run([_RECORD], model="gemma4:e4b", temperature=0.2))
    line = answer.to_dict()

    assert answer.step_one_answers == ("first try", "second try")
    assert all("answer" not in request for request in line["requests"])
    assert Answer.from_dict(line).step_one_answers == ("first try", "second try")


@pytest.mark.use_case("Model_Eval_Comparison")
def test_a_text_that_went_through_keeps_no_step_one_answer() -> None:
    """
    Given  a text whose step-① answer was used as it came
    When   the comparison runs
    Then   the answer keeps no copy of it: the output is that answer already
    """
    comparison = WritingModelComparison(
        lambda model, temperature: _ScriptedCorrector(_RECORD.reference),
        clock=lambda: 0.0,
        instrumentation=_Instrument(Performance(requests=(_request("correct", "the text"),))),
    )

    answer = _single(comparison.run([_RECORD], model="gemma4:e4b", temperature=0.2))

    assert answer.step_one_answers == ()
    assert "step_one_answers" not in answer.to_dict()
