"""Unit tests for the blank-line gaps of the proofreading exchange (``exchanges/text_first.py``)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from teachers_teammate.domain.proofreading.paragraph_gaps import BREAK
from teachers_teammate.infrastructure.ollama_client import OllamaClient
from teachers_teammate.infrastructure.proofreading.exchanges import TextFirstExchange
from teachers_teammate.infrastructure.proofreading.exchanges.base import ProofreadContext
from teachers_teammate.infrastructure.proofreading.port import OllamaPort
from tests.fakes.fake_ollama import FakeOllama, ProofreadingAnswers

GAP = "\n\n\n\n\n"
"""Four blank lines, as the two essays of the held-out en-10 text stand apart."""

FIRST = "Meine Familie und ich wohnen in einem kleinen Haus am Stadtrand."

SECOND = (
    "Am Wochenende fahren wir mit dem Fahrrad in den Wald und picknicken dort. "
    "Mein Bruder nimmt immer einen Ball mit, und unsere Mutter packt Brote und Äpfel ein. "
    "Nach dem Essen spielen wir Verstecken, bis die Sonne untergeht. "
    "Dann fahren wir müde nach Hause und freuen uns auf das nächste Wochenende."
)

SOURCE = f"{FIRST}{GAP}{SECOND}"


@pytest.fixture(name="fake")
def _fake() -> Iterator[FakeOllama]:
    """A fake server that stops answering when the test is over."""
    with FakeOllama() as server:
        yield server


def _ctx() -> ProofreadContext:
    """Return the context of a German B1 text."""
    return ProofreadContext(language="German", level="B1")


def _asked_text(fake: FakeOllama) -> str:
    """Return the user message of the first step ① request."""
    chat = next(request for request in fake.requests if request.path == "/api/chat")
    return "".join(str(message.get("content") or "") for message in chat.body["messages"])


def test_a_model_that_stops_at_a_long_gap_still_answers_the_whole_text(fake: FakeOllama) -> None:
    """
    Given  a model that answers only the part after a run of blank lines, and a text of two essays
           with four blank lines between them
    When   the exchange proofreads the text
    Then   the request carried one blank line, the answer corrected both essays, and the stored
           text keeps the student's own gap with nothing left out
    """
    fake.answer_chats(ProofreadingAnswers(last_word="Zielwort", skips_before_gap=3))
    port = OllamaPort(OllamaClient(fake.url), "m:1b")

    result = TextFirstExchange(port).proofread(SOURCE, _ctx())

    assert _asked_text(fake).endswith(f"{FIRST}{BREAK}{SECOND}")
    assert result.failed is False
    assert result.left_out == 0
    assert result.warnings == ()
    assert result.text == f"{FIRST}{GAP}{SECOND.rsplit(' ', 1)[0]} Zielwort"
