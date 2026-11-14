"""Unit tests for exchange T (``infrastructure/proofreading/exchanges/t.py``)."""

from __future__ import annotations

import json

from teachers_teammate.domain.context_budget import ContextBudget, answers_with_the_same_text
from teachers_teammate.domain.correction_prompts import EXPLAIN_SYSTEM
from teachers_teammate.domain.model_catalog import RequestProfile, StepProfile
from teachers_teammate.infrastructure.proofreading.exchanges import TExchange
from teachers_teammate.infrastructure.proofreading.exchanges.base import ProofreadContext
from teachers_teammate.infrastructure.proofreading.exchanges.t import (
    EXPLAIN_EDITS_CHARS,
    CORRECT_FAILED,
    EXPLAIN_FAILED,
    FRAMING_WARNING,
    UNCLEAR_FAILED,
)
from tests.fakes.fake_port import FakePort, answer, cut_off, invalid, transport

SOURCE = "Ich habe ein grosse Hund. Er ist nett."
CORRECTED = "Ich habe einen großen Hund. Er ist nett."


def _notes(*notes: tuple[int, str, str]) -> str:
    """Return an explain answer as JSON, from ``(id, category, explanation)`` triples."""
    return json.dumps(
        {
            "edits": [
                {"id": number, "category": category, "explanation": explanation}
                for number, category, explanation in notes
            ]
        }
    )


def _unclear(*quotes: str) -> str:
    """Return an unclear answer as JSON, one expression per quote."""
    return json.dumps(
        {
            "unclear": [
                {
                    "quote": quote,
                    "why": "Der Leser kann es nicht wissen.",
                    "suggestion": "Klarer formulieren.",
                    "determinable": False,
                }
                for quote in quotes
            ]
        }
    )


def _ctx(**overrides: object) -> ProofreadContext:
    """Return the context of a German B1 text, with *overrides* applied."""
    values: dict[str, object] = {"language": "German", "level": "B1"}
    values.update(overrides)
    return ProofreadContext(**values)  # ty: ignore[invalid-argument-type]


def test_a_corrected_text_gets_one_explained_edit() -> None:
    """
    Given  a text with one mistake and a model that corrects and explains it
    When   the exchange proofreads the text
    Then   the corrected text is stored and the edit carries its explanation
    """
    port = FakePort(
        answer(CORRECTED),
        answer(_notes((1, "grammar", "Der Akkusativ nach 'haben'."))),
        answer(_unclear()),
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.text == CORRECTED
    assert result.failed is False
    assert result.review is not None
    assert [note.id for note in result.review.edits] == [1]
    assert result.review.edits[0].explanation == "Der Akkusativ nach 'haben'."
    assert result.review.edits[0].category == "grammar"


def test_every_step_leaves_a_report() -> None:
    """
    Given  a text that needs all three steps
    When   the exchange proofreads it
    Then   one report per request names its step
    """
    port = FakePort(answer(CORRECTED), answer(_notes((1, "grammar", "x"))), answer(_unclear()))

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert [report.step for report in result.reports] == ["correct", "explain", "unclear"]


def test_a_text_without_edits_is_not_explained() -> None:
    """
    Given  a text the model leaves unchanged
    When   the exchange proofreads it
    Then   step ② is not sent, because there is nothing to explain
    """
    port = FakePort(answer(SOURCE), answer(_unclear()))

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert [request.step for request in port.requests] == ["correct", "unclear"]
    assert result.review is not None
    assert result.review.edits == ()


def test_a_cut_off_correction_fails_the_file() -> None:
    """
    Given  a model whose correction is cut off
    When   the exchange proofreads the text
    Then   the file fails and the source text is kept
    """
    port = FakePort(cut_off())

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is True
    assert result.text == SOURCE
    assert result.review is None


def test_an_unreachable_server_fails_the_file() -> None:
    """
    Given  a server that cannot be reached
    When   the exchange proofreads the text
    Then   the file fails with the warning of the request
    """
    port = FakePort(transport("Correction failed (connection refused)."))

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is True
    assert result.warnings == ("Correction failed (connection refused).",)


def test_a_lead_in_is_asked_for_once_more() -> None:
    """
    Given  a model that first writes a lead-in and then answers with the text
    When   the exchange proofreads the text
    Then   the second answer is used and the file is corrected
    """
    port = FakePort(
        answer(f"Hier ist der korrigierte Text:\n\n{CORRECTED}"),
        answer(CORRECTED),
        answer(_notes((1, "grammar", "x"))),
        answer(_unclear()),
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is False
    assert result.text == CORRECTED
    assert [request.step for request in port.requests].count("correct") == 2


def test_a_lead_in_twice_fails_the_file() -> None:
    """
    Given  a model that writes a lead-in twice
    When   the exchange proofreads the text
    Then   the file fails, because a remark must never be stored in the text
    """
    framed = f"Hier ist der korrigierte Text:\n\n{CORRECTED}"
    port = FakePort(answer(framed), answer(framed))

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is True
    assert result.warnings == (FRAMING_WARNING,)


def test_an_unusable_explanation_only_warns() -> None:
    """
    Given  a model whose explain answer is not valid JSON twice
    When   the exchange proofreads the text
    Then   the text is corrected, the edit stays unexplained and the file does not fail
    """
    port = FakePort(
        answer(CORRECTED),
        invalid("not json"),
        invalid("still not json"),
        answer(_unclear()),
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is False
    assert result.text == CORRECTED
    assert EXPLAIN_FAILED in result.warnings
    assert result.review is not None
    assert result.review.edits[0].explanation == ""


def test_a_missing_note_is_asked_for_once_more() -> None:
    """
    Given  a correction with two edits and an answer that explains only the first
    When   the exchange proofreads the text
    Then   a second explain request asks for the missing id alone
    """
    source = "Ich habe ein grosse Hund. Er hat ein klein Katze."
    corrected = "Ich habe einen großen Hund. Er hat eine kleine Katze."
    port = FakePort(
        answer(corrected),
        answer(_notes((1, "grammar", "x"))),
        answer(_notes((2, "grammar", "y"))),
        answer(_unclear()),
    )

    result = TExchange(port).proofread(source, _ctx())

    explain = [request for request in port.requests if request.step == "explain"]
    assert len(explain) == 2
    assert result.review is not None
    assert [note.explanation for note in result.review.edits] == ["x", "y"]


def test_step_three_can_be_switched_off() -> None:
    """
    Given  a profile with the unclear check off
    When   the exchange proofreads the text
    Then   step ③ is not sent and the review has no unclear list
    """
    port = FakePort(answer(CORRECTED), answer(_notes((1, "grammar", "x"))))

    result = TExchange(port).proofread(SOURCE, _ctx(profile=RequestProfile(unclear_check=False)))

    assert [request.step for request in port.requests] == ["correct", "explain"]
    assert result.review is not None
    assert result.review.unclear == ()


def test_an_unclear_expression_is_anchored_on_the_students_text() -> None:
    """
    Given  a model that quotes the corrected text
    When   the exchange proofreads the text
    Then   the unclear span points at the same words in the student's text
    """
    port = FakePort(
        answer(CORRECTED),
        answer(_notes((1, "grammar", "x"))),
        answer(_unclear("Er ist nett.")),
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.review is not None
    note = result.review.unclear[0]
    assert SOURCE[note.raw_start : note.raw_end] == "Er ist nett."
    assert note.is_suggestion is True


def test_an_unclear_expression_on_an_edit_is_dropped() -> None:
    """
    Given  a model that flags words the correction changed
    When   the exchange proofreads the text
    Then   the flag is dropped, because the edit already says what is wrong there
    """
    port = FakePort(
        answer(CORRECTED),
        answer(_notes((1, "grammar", "x"))),
        answer(_unclear("einen großen Hund")),
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.review is not None
    assert result.review.unclear == ()


def test_an_unusable_unclear_answer_only_warns() -> None:
    """
    Given  a model whose unclear answer is not valid JSON twice
    When   the exchange proofreads the text
    Then   the text is corrected and the file does not fail
    """
    port = FakePort(
        answer(CORRECTED), answer(_notes((1, "grammar", "x"))), invalid("nope"), invalid("nope")
    )

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is False
    assert UNCLEAR_FAILED in result.warnings


def test_markdown_marks_and_crossed_out_words_survive() -> None:
    """
    Given  a Markdown text with a heading, a crossed-out word and a bold word
    When   the exchange proofreads it
    Then   the stored text keeps the heading, the crossed-out word and the bold mark
    """
    source = "## Mein Tag\n\nIch ~~gehe~~ ging **nach** Hause."
    port = FakePort(
        answer("Mein Tag\n\nIch ging nach Hause."),
        answer(_unclear()),
    )

    result = TExchange(port).proofread(source, _ctx(text_format="markdown"))

    assert result.text == source
    assert result.failed is False


def test_the_request_carries_the_notes_and_the_parameters() -> None:
    """
    Given  teacher notes and a level
    When   the exchange proofreads the text
    Then   the first request names the language, the level and the notes
    """
    port = FakePort(answer(SOURCE), answer(_unclear()))

    TExchange(port).proofread(SOURCE, _ctx(teacher_notes="Klasse 7b"))

    user = port.requests[0].user
    assert "Text language: German" in user
    assert "CEFR level of the student: B1" in user
    assert "Klasse 7b" in user


def test_each_step_is_sent_at_its_own_temperature() -> None:
    """
    Given  a profile with a different temperature per step
    When   the exchange proofreads the text
    Then   every request carries the temperature of its own step
    """
    profile = RequestProfile(
        correct=StepProfile(temperature=0.1),
        explain=StepProfile(temperature=0.2),
        unclear=StepProfile(temperature=0.3),
    )
    port = FakePort(answer(CORRECTED), answer(_notes((1, "grammar", "x"))), answer(_unclear()))

    TExchange(port).proofread(SOURCE, _ctx(profile=profile))

    assert [request.temperature for request in port.requests] == [0.1, 0.2, 0.3]


def test_a_provider_with_its_own_window_has_no_text_limit() -> None:
    """
    Given  a port that manages its own context window
    When   the exchange is asked how much text one request carries
    Then   there is no limit
    """
    assert TExchange(FakePort()).max_text_chars(_ctx()) is None


def test_a_local_window_gives_a_text_limit() -> None:
    """
    Given  a port with a context window
    When   the exchange is asked how much text one request carries
    Then   the limit is a positive number of characters
    """
    exchange = TExchange(FakePort(window=ContextBudget(8192)))

    limit = exchange.max_text_chars(_ctx())

    assert limit is not None
    assert 0 < limit < 8192 * 4


def test_a_text_of_the_limit_passes_the_request_check_of_every_step() -> None:
    """
    Given  a port with an 8192-token window and a text exactly as long as the exchange's limit
    When   the requests of the three steps are sized the way the client checks them
    Then   none is refused: step ① and ③ carry the text once, step ② twice next to its edit list
    """
    window = ContextBudget(8192)
    exchange = TExchange(FakePort(window=window))
    limit = exchange.max_text_chars(_ctx())
    assert limit is not None
    text = "a" * limit
    allowance = answers_with_the_same_text(8192)

    correct = exchange._correct_request(text, _ctx())
    unclear = exchange._unclear_request(text, _ctx())
    explain = len(EXPLAIN_SYSTEM) + len(exchange._explain_head(_ctx(), text + text))

    assert window.request_fits(len(correct.system) + len(correct.user), answer_tokens=allowance)
    assert window.request_fits(len(unclear.system) + len(unclear.user), answer_tokens=allowance)
    assert window.request_fits(explain + EXPLAIN_EDITS_CHARS, answer_tokens=allowance)


def test_the_cost_model_names_the_three_steps() -> None:
    """
    Given  the exchange
    When   its cost model is read
    Then   every step has a request count and an answer length
    """
    cost = TExchange(FakePort()).cost_model()

    assert set(cost.requests_per_page) == {"correct", "explain", "unclear"}
    assert set(cost.answer_tokens_per_100_words) == {"correct", "explain", "unclear"}


def test_an_empty_correction_fails_the_file() -> None:
    """
    Given  a model that answers with nothing at all
    When   the exchange proofreads the text
    Then   the file fails, because an empty answer must never replace the student's text
    """
    port = FakePort(answer(""))

    result = TExchange(port).proofread(SOURCE, _ctx())

    assert result.failed is True
    assert result.text == SOURCE
    assert result.warnings == (CORRECT_FAILED,)
