"""GUI tests for the assistant's page order: the table, one case per row."""

from __future__ import annotations

import pytest

from teachers_teammate.application.setup_plan import HandIn, ProofreadingPlace, SetupAnswers
from teachers_teammate.gui.setup._flow import ORDER, SetupPageId, next_page


def _answers(
    hand_in: HandIn = HandIn.HANDWRITTEN,
    proofreading: ProofreadingPlace = ProofreadingPlace.LOCAL,
) -> SetupAnswers:
    """Return answers with only the two fields the order depends on set."""
    return SetupAnswers(
        language="German",
        hand_in=hand_in,
        proofreading=proofreading,
        ollama_url="http://localhost:11434",
        hardware_profile="auto",
        local_ai_shown=hand_in is HandIn.HANDWRITTEN or proofreading is ProofreadingPlace.LOCAL,
    )


def test_every_page_is_in_the_order_once() -> None:
    """
    Given  the page ids
    When   the wizard's order is built
    Then   every id appears exactly once, so a page index is a page id
    """
    assert ORDER == tuple(SetupPageId)
    assert len(set(ORDER)) == len(ORDER)


def test_welcome_leads_to_the_classes() -> None:
    """
    Given  the welcome page
    When   the next page is asked for
    Then   it is "Your classes", whatever the answers say
    """
    assert next_page(SetupPageId.WELCOME, _answers()) is SetupPageId.CLASSES
    assert next_page(SetupPageId.WELCOME, _answers(HandIn.TYPED)) is SetupPageId.CLASSES


def test_handwritten_pages_go_to_the_local_ai_first() -> None:
    """
    Given  handwritten pages
    When   the classes page is left
    Then   the local AI comes next, because Ollama is needed anyway
    """
    assert next_page(SetupPageId.CLASSES, _answers()) is SetupPageId.LOCAL_AI


def test_typed_texts_go_to_the_proofreading_first() -> None:
    """
    Given  only typed texts
    When   the classes page is left
    Then   the proofreading question comes next, because Ollama may not be needed at all
    """
    assert next_page(SetupPageId.CLASSES, _answers(HandIn.TYPED)) is SetupPageId.PROOFREADING


def test_the_local_ai_leads_to_the_proofreading_with_handwriting() -> None:
    """
    Given  handwritten pages
    When   the local AI page is left
    Then   the proofreading question follows, now able to rate the models for this computer
    """
    assert next_page(SetupPageId.LOCAL_AI, _answers()) is SetupPageId.PROOFREADING


def test_the_local_ai_leads_to_the_summary_with_typed_texts() -> None:
    """
    Given  typed texts whose proofreading runs on the local AI
    When   the local AI page is left
    Then   the summary follows: the proofreading question was already answered
    """
    answers = _answers(HandIn.TYPED, ProofreadingPlace.LOCAL)
    assert next_page(SetupPageId.LOCAL_AI, answers) is SetupPageId.SUMMARY


def test_online_proofreading_asks_about_names() -> None:
    """
    Given  proofreading at an online service
    When   the proofreading page is left
    Then   the privacy page follows, because text leaves this computer
    """
    answers = _answers(HandIn.HANDWRITTEN, ProofreadingPlace.ONLINE)
    assert next_page(SetupPageId.PROOFREADING, answers) is SetupPageId.PRIVACY


def test_typed_texts_with_local_proofreading_go_to_the_local_ai() -> None:
    """
    Given  typed texts and proofreading on the local AI
    When   the proofreading page is left
    Then   the local AI page follows, because it was skipped on the way in
    """
    answers = _answers(HandIn.TYPED, ProofreadingPlace.LOCAL)
    assert next_page(SetupPageId.PROOFREADING, answers) is SetupPageId.LOCAL_AI


def test_handwritten_pages_with_local_proofreading_go_to_the_summary() -> None:
    """
    Given  handwritten pages and proofreading on the local AI
    When   the proofreading page is left
    Then   the summary follows: the local AI page was already shown
    """
    answers = _answers(HandIn.HANDWRITTEN, ProofreadingPlace.LOCAL)
    assert next_page(SetupPageId.PROOFREADING, answers) is SetupPageId.SUMMARY


def test_handwritten_pages_without_proofreading_go_to_the_summary() -> None:
    """
    Given  handwritten pages and "Not now" for the proofreading
    When   the proofreading page is left
    Then   the summary follows, with no privacy question
    """
    answers = _answers(HandIn.HANDWRITTEN, ProofreadingPlace.OFF)
    assert next_page(SetupPageId.PROOFREADING, answers) is SetupPageId.SUMMARY


def test_the_privacy_page_leads_to_the_summary() -> None:
    """
    Given  the privacy page
    When   it is left
    Then   the summary follows
    """
    answers = _answers(HandIn.HANDWRITTEN, ProofreadingPlace.ONLINE)
    assert next_page(SetupPageId.PRIVACY, answers) is SetupPageId.SUMMARY


def test_the_summary_leads_to_the_done_page() -> None:
    """
    Given  the summary page
    When   Set up was pressed
    Then   the done page follows
    """
    assert next_page(SetupPageId.SUMMARY, _answers()) is SetupPageId.DONE


def test_the_done_page_is_the_last() -> None:
    """
    Given  the done page
    When   the next page is asked for
    Then   there is none, so Finish closes the window
    """
    assert next_page(SetupPageId.DONE, _answers()) is None


@pytest.mark.parametrize("hand_in", list(HandIn))
@pytest.mark.parametrize("place", list(ProofreadingPlace))
def test_every_path_reaches_the_summary(hand_in: HandIn, place: ProofreadingPlace) -> None:
    """
    Given  any combination of answers
    When   the pages are followed from the welcome page
    Then   the summary is reached, and no page is visited twice
    """
    answers = _answers(hand_in, place)
    seen: list[SetupPageId] = []
    page: SetupPageId | None = SetupPageId.WELCOME
    while page is not None and page is not SetupPageId.SUMMARY:
        assert page not in seen, f"{page} was visited twice"
        seen.append(page)
        page = next_page(page, answers)
    assert page is SetupPageId.SUMMARY
