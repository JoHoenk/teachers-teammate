"""The order of the assistant's pages, decided by the answers alone.

The order follows what the teacher needs: with handwritten pages Ollama is needed anyway, so the
local AI comes before the proofreading question and can rate the models for the real computer;
with typed texts Ollama may not be needed at all, so the proofreading question comes first and
the local AI page is only shown when the proofreading runs there.

The function is pure, so the table can be tested without a window.
"""

from __future__ import annotations

from enum import StrEnum

from ...application.setup_plan import HandIn, ProofreadingPlace, SetupAnswers


class SetupPageId(StrEnum):
    """The pages of the assistant, in the order they are added to the wizard."""

    WELCOME = "welcome"
    CLASSES = "classes"
    LOCAL_AI = "local_ai"
    PROOFREADING = "proofreading"
    PRIVACY = "privacy"
    SUMMARY = "summary"
    DONE = "done"


#: Every page, in the order the wizard holds them (the index is the page id).
ORDER: tuple[SetupPageId, ...] = tuple(SetupPageId)


def next_page(page: SetupPageId, answers: SetupAnswers) -> SetupPageId | None:
    """Return the page that follows *page* for *answers*; ``None`` on the last page."""
    handwritten = answers.hand_in is HandIn.HANDWRITTEN
    online = answers.proofreading is ProofreadingPlace.ONLINE
    if page is SetupPageId.WELCOME:
        return SetupPageId.CLASSES
    if page is SetupPageId.CLASSES:
        return SetupPageId.LOCAL_AI if handwritten else SetupPageId.PROOFREADING
    if page is SetupPageId.LOCAL_AI:
        return SetupPageId.PROOFREADING if handwritten else SetupPageId.SUMMARY
    if page is SetupPageId.PROOFREADING:
        if online:
            return SetupPageId.PRIVACY
        return SetupPageId.SUMMARY if handwritten else SetupPageId.LOCAL_AI
    if page is SetupPageId.PRIVACY:
        return SetupPageId.SUMMARY
    if page is SetupPageId.SUMMARY:
        return SetupPageId.DONE
    return None
