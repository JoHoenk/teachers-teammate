"""Unit tests for the step runner (``infrastructure/proofreading/runner.py``)."""

from __future__ import annotations

import json

from pydantic import BaseModel

from teachers_teammate.domain.context_budget import ContextBudget
from teachers_teammate.exceptions import TRUNCATED_MESSAGE
from teachers_teammate.infrastructure.proofreading.port import PortAnswer, StepRequest
from teachers_teammate.infrastructure.proofreading.runner import StepRunner
from tests.fakes.fake_port import FakePort, answer, cut_off, invalid, transport


class Note(BaseModel):
    """A minimal answer schema: one note, so a test can see what the runner parsed."""

    note: str


def _parse(text: str) -> tuple[Note | None, str]:
    """Return the note of *text*, or the reason it is not one."""
    try:
        return Note.model_validate_json(text), ""
    except ValueError as exc:
        return None, str(exc)


def _request(*, repairable: bool = True, answer_factor: float = 1.0) -> StepRequest:
    """Return a structured request of the explain step."""
    return StepRequest(
        step="explain",
        system="You explain edits.",
        user="Text language: German\n\nText:\n\nEin Text.",
        schema=Note,
        answer_factor=answer_factor,
        repairable=repairable,
    )


def test_a_valid_answer_is_parsed_and_reported() -> None:
    """
    Given  a port that answers with a valid note
    When   the runner sends the request
    Then   the note is the value, the step did not fail, and one report names the step
    """
    port = FakePort(answer(json.dumps({"note": "Zeitform falsch."})))

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value == Note(note="Zeitform falsch.")
    assert outcome.failure == ""
    assert [report.step for report in outcome.reports] == ["explain"]
    assert len(port.requests) == 1


def test_a_cut_off_answer_is_asked_again() -> None:
    """
    Given  a port whose first answer is cut off and whose second one is valid
    When   the runner sends the request with two tries
    Then   the second answer is the value, and both requests were sent unchanged
    """
    port = FakePort(cut_off(), answer(json.dumps({"note": "Zeitform falsch."})))

    outcome = StepRunner(port, cut_off_tries=2).run(_request(), _parse)

    assert outcome.value == Note(note="Zeitform falsch.")
    assert [report.failure for report in outcome.reports] == ["truncated", ""]
    assert port.requests[0] == port.requests[1]


def test_a_cut_off_answer_that_stays_cut_off_fails_the_step() -> None:
    """
    Given  a port whose answers are cut off
    When   the runner sends the request with two tries
    Then   the step fails as truncated after exactly two requests, with the warning to show
    """
    port = FakePort(cut_off(), cut_off())

    outcome = StepRunner(port, cut_off_tries=2).run(_request(), _parse)

    assert outcome.value is None
    assert outcome.failure == "truncated"
    assert outcome.error == TRUNCATED_MESSAGE
    assert len(port.requests) == 2


def test_an_invalid_answer_is_repaired_once_with_the_error_and_the_answer() -> None:
    """
    Given  a port whose first answer does not match the schema and whose second one does
    When   the runner sends the request
    Then   the repair request quotes the previous answer and the validation error
    """
    port = FakePort(invalid("not JSON at all"), answer(json.dumps({"note": "Zeitform falsch."})))

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value == Note(note="Zeitform falsch.")
    assert len(port.requests) == 2
    repair = port.requests[1].user
    assert repair.startswith(port.requests[0].user)
    assert "not JSON at all" in repair
    assert "Validation error:" in repair


def test_a_second_invalid_answer_fails_the_step_without_a_second_repair() -> None:
    """
    Given  a port whose answers never match the schema
    When   the runner sends the request
    Then   the step fails as invalid after exactly one repair request
    """
    port = FakePort(invalid("not JSON at all"), invalid("still not JSON"))

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value is None
    assert outcome.failure == "invalid"
    assert len(port.requests) == 2


def test_an_empty_invalid_answer_is_not_repaired() -> None:
    """
    Given  a port that answers nothing at all
    When   the runner sends the request
    Then   no repair request is sent, because there is nothing to quote
    """
    port = FakePort(invalid(""))

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.failure == "invalid"
    assert len(port.requests) == 1


def test_an_unrepairable_request_is_not_repaired() -> None:
    """
    Given  a request whose step says an invalid answer is not worth repairing
    When   the runner sends it and the answer does not match the schema
    Then   the step fails after one request
    """
    port = FakePort(invalid("not JSON at all"))

    outcome = StepRunner(port).run(_request(repairable=False), _parse)

    assert outcome.failure == "invalid"
    assert len(port.requests) == 1


def test_a_repair_that_cannot_fit_the_window_is_not_sent() -> None:
    """
    Given  a port with a small context window and a request that nearly fills it
    When   the answer does not match the schema
    Then   the repair is skipped before it is sent, and the step fails as invalid
    """
    port = FakePort(invalid("not JSON at all"), window=ContextBudget(100))
    request = StepRequest(
        step="explain",
        system="You explain edits.",
        user="x" * 200,
        schema=Note,
        answer_factor=1.0,
    )

    outcome = StepRunner(port).run(request, _parse)

    assert outcome.failure == "invalid"
    assert len(port.requests) == 1


def test_a_repair_that_fits_the_window_is_sent() -> None:
    """
    Given  a port with a window that leaves room for the repair request
    When   the answer does not match the schema
    Then   the repair request is sent
    """
    port = FakePort(
        invalid("not JSON at all"), answer(json.dumps({"note": "ok"})), window=ContextBudget(8192)
    )

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value == Note(note="ok")
    assert len(port.requests) == 2


def test_a_transport_error_ends_the_step() -> None:
    """
    Given  a port whose request never reaches the model
    When   the runner sends the request
    Then   the step fails as transport with the warning to show, and nothing is retried
    """
    port = FakePort(transport())

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value is None
    assert outcome.failure == "transport"
    assert outcome.error == "Correction failed (connection refused); keeping original text."
    assert len(port.requests) == 1


def test_a_plain_text_request_is_answered_as_it_is() -> None:
    """
    Given  a request without a schema
    When   the runner sends it
    Then   the answer text is the value, unchanged
    """
    port = FakePort(answer("Ein korrigierter Text."))
    request = StepRequest(step="correct", system="Correct it.", user="Ein Text.")

    outcome = StepRunner(port).run(request, lambda text: (text, ""))

    assert outcome.value == "Ein korrigierter Text."
    assert outcome.failure == ""


def test_the_reports_carry_what_the_request_cost() -> None:
    """
    Given  a port that reports tokens and seconds for its answer
    When   the runner sends the request
    Then   the report carries them, so a run can say what a step cost
    """
    port = FakePort(
        PortAnswer(raw=json.dumps({"note": "ok"}), prompt_tokens=120, answer_tokens=8, seconds=1.5)
    )

    outcome = StepRunner(port).run(_request(), _parse)

    assert len(outcome.reports) == 1
    report = outcome.reports[0]
    assert (report.prompt_tokens, report.answer_tokens, report.seconds) == (120, 8, 1.5)


def test_the_repair_check_measures_the_error_that_is_really_sent() -> None:
    """
    Given  a model answer that does not parse, with a window that fits the repair request only
           without the parse error in it
    When   the runner decides whether the repair fits
    Then   the error counts, so the repair is skipped before the server refuses it
    """
    from teachers_teammate.infrastructure.correction_schema import repair_message  # noqa: PLC0415

    request = _request()
    raw = "not JSON at all"
    _, error = _parse(raw)
    without = len(request.system) + len(request.user) + 2 + len(repair_message(raw, ""))
    num_ctx = 2 * -(-without // 3)
    port = FakePort(answer(raw), window=ContextBudget(num_ctx))

    outcome = StepRunner(port).run(request, _parse)

    assert error
    assert outcome.failure == "invalid"
    assert len(port.requests) == 1


def test_a_provider_that_validated_the_answer_gives_the_error_to_quote() -> None:
    """
    Given  a tool-calling provider whose raw answer is a list of tool arguments and whose own
           validation said what was wrong
    When   the runner repairs the answer
    Then   the repair request quotes the provider's reason, not a parse error of the list
    """
    reason = "note: Field required"
    port = FakePort(invalid('[{"nope": 1}]', error=reason), answer(json.dumps({"note": "ok"})))

    outcome = StepRunner(port).run(_request(), _parse)

    assert outcome.value == Note(note="ok")
    repair = port.requests[1].user
    assert reason in repair
    assert "valid dictionary" not in repair
