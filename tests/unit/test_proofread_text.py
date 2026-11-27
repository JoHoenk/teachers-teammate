"""Tests for ``infrastructure/workflow/proofread_text.py``: hiding, proofreading, restoring."""

from __future__ import annotations

from typing import Any

import pytest

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.performance_estimate import SpeedStep
from teachers_teammate.domain.proofreading.edits import EditPart, edits
from teachers_teammate.domain.proofreading.result import CorrectionOutcome
from teachers_teammate.domain.proofreading.review import CorrectionReview, edit_note
from teachers_teammate.exceptions import AnonymizerError, RunStoppedError
from teachers_teammate.infrastructure.workflow.proofread_text import (
    ProofreadText,
    hide_personal_info,
    names_left_hidden,
    restore_personal_info,
    restore_review,
)
from teachers_teammate.interfaces import AnonymizationMap, Anonymizer, Corrector

STUDENT = "Lena geht heim."
SENT = "[PERSON_1] geht heim."
NAMES: AnonymizationMap = {"[PERSON_1]": "Lena"}


class _Anonymizer(Anonymizer):
    """Replaces a name with a placeholder, and puts it back — or fails, when told to."""

    def __init__(self, *, restore_fails: bool = False, hides: bool = True) -> None:
        self._restore_fails = restore_fails
        self._hides = hides
        self.anonymize_calls = 0

    def anonymize(self, text: str) -> tuple[str, AnonymizationMap]:
        self.anonymize_calls += 1
        return (SENT, dict(NAMES)) if self._hides else (text, {})

    def restore(self, text: str, mapping: AnonymizationMap) -> str:
        if self._restore_fails:
            raise ValueError("a placeholder the mapping does not have")
        for placeholder, original in mapping.items():
            text = text.replace(placeholder, original)
        return text


class _Corrector(Corrector):
    """Answers with a text and a review the test names, and counts what it was asked."""

    def __init__(self, outcome: CorrectionOutcome, pieces: int | None = None) -> None:
        self._outcome = outcome
        self._pieces = pieces
        self.sent: list[str] = []

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        self.sent.append(raw_text)
        return self._outcome

    def pieces(
        self,
        text: str,  # the count is fixed for the test
        language: str,
        *,
        text_format: TextFormat = "plain",
    ) -> int | None:
        return self._pieces


class _Reporter:
    """Records the status lines a proofreading writes."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def status(self, line: str) -> None:
        self.lines.append(line)

    def warn(self, line: str) -> None:
        self.lines.append(line)

    def error(self, line: str) -> None:
        self.lines.append(line)


class _Sampler:
    """Records the samples a proofreading reports."""

    def __init__(self) -> None:
        self.begun: list[SpeedStep] = []
        self.finished: list[dict[str, Any]] = []

    def begin_sample(self, step: SpeedStep) -> None:
        self.begun.append(step)

    def finish_sample(self, *, words: int = 0, mode: str = "", usable: bool = True) -> None:
        self.finished.append({"words": words, "mode": mode, "usable": usable})


def _review(source: str, corrected: str, category: str, explanation: str) -> CorrectionReview:
    """Return a review with one explained change of *source* → *corrected*."""
    return CorrectionReview(
        edits=(edit_note(edits(source, corrected)[0], category=category, explanation=explanation),)
    )


def test_without_an_anonymizer_the_text_goes_out_as_it_is() -> None:
    """
    Given  hiding off and a corrector that answers with the same text
    When   the text is proofread
    Then   the model saw the student's text and nothing was hidden or restored
    """
    corrector = _Corrector(CorrectionOutcome(STUDENT))
    unit = ProofreadText(corrector, None, "German", reporter=_Reporter())

    result = unit(STUDENT)

    assert corrector.sent == [STUDENT]
    assert (result.text, result.sent_text, result.sent_map) == (STUDENT, STUDENT, {})
    assert result.names_hidden == 0
    assert result.warnings == ()


def test_the_names_are_hidden_before_the_model_and_back_in_the_answer() -> None:
    """
    Given  hiding on and a corrector that answers with the anonymized text
    When   the text is proofread
    Then   the model saw the placeholder and the answer has the name back
    """
    corrector = _Corrector(CorrectionOutcome(SENT))
    unit = ProofreadText(corrector, _Anonymizer(), "German", reporter=_Reporter())

    result = unit(STUDENT)

    assert corrector.sent == [SENT]
    assert result.text == STUDENT
    assert (result.names_hidden, result.sent_text) == (1, SENT)


def test_hiding_is_reported_before_the_request() -> None:
    """
    Given  hiding on
    When   the text is proofread
    Then   the report says how many places were hidden
    """
    reporter = _Reporter()
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(SENT)), _Anonymizer(), "German", reporter=reporter
    )

    unit(STUDENT)

    assert len(reporter.lines) == 1
    assert "Personal info removed (1 items)" in reporter.lines[0]


def test_the_review_gets_the_names_back_at_the_right_offsets() -> None:
    """
    Given  a model that fixes a word after a name, in the anonymized text
    When   the text is proofread
    Then   the review quotes the real name and still points at the right words
    """
    corrected = "[PERSON_1] geht gleich heim."
    corrector = _Corrector(
        CorrectionOutcome(corrected, _review(SENT, corrected, "word_choice", "Zeitangabe."))
    )
    unit = ProofreadText(corrector, _Anonymizer(), "German", reporter=_Reporter())

    result = unit(STUDENT)

    assert result.text == "Lena geht gleich heim."
    assert result.review is not None
    note = result.review.edits[0]
    assert note.parts[0].corrected == "gleich"


def test_a_review_of_another_text_is_dropped() -> None:
    """
    Given  a review made for a text that is not the one the answer belongs to
    When   the review is restored
    Then   it is dropped, because its offsets would explain the wrong words
    """
    review = _review(SENT, "[PERSON_1] geht gleich heim.", "word_choice", "Zeitangabe.")

    restored, dropped = restore_review(
        _Anonymizer(),
        review,
        dict(NAMES),
        sent_text=SENT,
        raw_text="Lena geht heute heim.",
        text_format="plain",
    )

    assert (restored, dropped) == (None, True)


def test_a_failing_restore_ends_the_file_with_its_sentence() -> None:
    """
    Given  an anonymizer that cannot put the names back
    When   the text is proofread
    Then   the file ends with an AnonymizerError that carries the sentence a teacher reads
    """
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(SENT)),
        _Anonymizer(restore_fails=True),
        "German",
        reporter=_Reporter(),
    )

    with pytest.raises(AnonymizerError) as raised:
        unit(STUDENT)

    assert "restore" in str(raised.value).lower()


def test_a_placeholder_the_answer_keeps_is_reported() -> None:
    """
    Given  an answer that keeps the placeholder instead of using the name
    When   the text is proofread
    Then   a warning says names are still hidden, and the stored warning carries it too
    """
    unit = ProofreadText(
        # The model mangled the second stand-in, so no restore can put the name back.
        _Corrector(CorrectionOutcome("[PERSON_1] geht heim, sagte PERSON_1.")),
        _Anonymizer(),
        "German",
        reporter=_Reporter(),
    )

    result = unit(STUDENT)

    assert result.warnings
    assert result.stored_warning == result.warnings[-1]


def test_a_stopped_run_never_reaches_the_model() -> None:
    """
    Given  a run the teacher stopped before the proofreading
    When   the text would be proofread
    Then   the stop ends it, and no request is sent
    """
    corrector = _Corrector(CorrectionOutcome(SENT))
    unit = ProofreadText(corrector, _Anonymizer(), "German", reporter=_Reporter())

    with pytest.raises(RunStoppedError):
        unit(STUDENT, stop_requested=lambda: True)

    assert corrector.sent == []


def test_a_long_text_says_how_many_requests_it_takes_before_they_start() -> None:
    """
    Given  a corrector that cuts the text into three pieces
    When   the text is proofread
    Then   the report says so before the request, and the outcome carries the count
    """
    reporter = _Reporter()
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(STUDENT), pieces=3), None, "German", reporter=reporter
    )

    result = unit(STUDENT, name="Lena.txt")

    assert result.pieces == 3
    assert "Proofreading Lena.txt in 3 parts" in reporter.lines[0]


def test_a_finished_answer_records_its_sample_in_the_mode_it_used() -> None:
    """
    Given  a corrector that answers without a review
    When   the text is proofread
    Then   one proofreading sample is recorded, in text mode
    """
    sampler = _Sampler()
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(STUDENT)),
        None,
        "German",
        reporter=_Reporter(),
        sampler=sampler,
    )

    unit(STUDENT)

    assert sampler.begun == [SpeedStep.PROOFREADING]
    assert sampler.finished == [{"words": 3, "mode": "text", "usable": True}]


def test_a_fallen_back_answer_records_no_sample() -> None:
    """
    Given  a corrector whose answer fell back to corrections only
    When   the text is proofread
    Then   the sample is dropped, because it would undercount the answer
    """
    sampler = _Sampler()
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(STUDENT, fell_back=True)),
        None,
        "German",
        reporter=_Reporter(),
        sampler=sampler,
    )

    unit(STUDENT)

    assert sampler.finished == [{"words": 3, "mode": "text", "usable": False}]


def test_hiding_twice_is_the_callers_choice() -> None:
    """
    Given  a caller that already hid the text — a run shares it with the anonymized file
    When   the text is proofread
    Then   the anonymizer is not asked again
    """
    anonymizer = _Anonymizer()
    unit = ProofreadText(
        _Corrector(CorrectionOutcome(SENT)), anonymizer, "German", reporter=_Reporter()
    )

    result = unit(STUDENT, hide=lambda _text, _format: (SENT, dict(NAMES)))

    assert anonymizer.anonymize_calls == 0
    assert result.text == STUDENT
    assert result.names_hidden == 1


def test_a_name_outside_a_mapping_is_no_warning() -> None:
    """
    Given  a text with nothing hidden in it
    When   the warning of names left hidden is asked for
    Then   there is none
    """
    assert names_left_hidden("Lena geht heim.", {}) == ""


def test_the_hiding_of_one_text_is_one_function() -> None:
    """
    Given  an anonymizer and a text
    When   the hiding is taken directly
    Then   the text and its mapping come back, and the report says what was hidden
    """
    reporter = _Reporter()

    text, mapping = hide_personal_info(_Anonymizer(), STUDENT, reporter=reporter)

    assert (text, mapping) == (SENT, NAMES)
    assert "Personal info removed (1 items)" in reporter.lines[0]


def test_restoring_without_a_mapping_leaves_the_text_alone() -> None:
    """
    Given  nothing hidden and an anonymizer
    When   the text is restored
    Then   it comes back unchanged, because there is nothing to put back
    """
    assert restore_personal_info(_Anonymizer(), STUDENT, {}) == STUDENT


@pytest.mark.parametrize("text", ["", "[PERSON_1]"])
def test_the_restored_text_is_the_name_or_nothing(text: str) -> None:
    """
    Given  a text that is empty or only a placeholder
    When   it is restored
    Then   the name comes back, or the empty text stays empty
    """
    assert restore_personal_info(_Anonymizer(), text, NAMES) == text.replace("[PERSON_1]", "Lena")
