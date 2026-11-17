"""What the app says about the data that leaves the computer.

The "what is sent for this file" dialog, the data-flow summary and the privacy page all
read their wording from here, so one sentence about a step reads the same everywhere.
"""

from __future__ import annotations

from ._types import Notice, NoticeKind
from .settings import WHERE_ADDONS

WHAT_IS_SENT_TITLE = "What is sent for this file"
WHAT_IS_SENT_WORKING = "Working out what is sent…"
NO_TEXT_YET = "No text recognized for this file yet."


def what_is_sent_title(name: str) -> str:
    """Return the caption of the busy dialog that describes one file."""
    return f"{WHAT_IS_SENT_TITLE} — {name}"


def what_is_sent_failed(reason: str) -> Notice:
    """Tell the teacher that the sent text could not be worked out."""
    return Notice(WHAT_IS_SENT_TITLE, reason, NoticeKind.WARNING)


def what_is_sent_without_text() -> Notice:
    """Tell the teacher that this file has no text to describe yet."""
    return Notice(WHAT_IS_SENT_TITLE, NO_TEXT_YET)


def what_is_sent_unreadable() -> Notice:
    """Tell the teacher that working out what is sent gave no answer, which is a failure."""
    return Notice(WHAT_IS_SENT_TITLE, NO_TEXT_YET, NoticeKind.WARNING)


def spacy_missing(detail: str, *, installed_app: bool) -> str:
    """Explain that name detection needs spaCy, which is not installed.

    The installed app points at the Add-ons page; a source checkout at ``pip``.
    """
    if installed_app:
        return f"spaCy is not installed.\n\nInstall it under {WHERE_ADDONS} (Name detection)."
    return f'spaCy not installed: {detail}\n\nInstall: pip install "teachers-teammate[privacy]"'


def spacy_model_missing(detail: str) -> str:
    """Explain that the name detection model is not installed."""
    return f"spaCy model missing: {detail}\n\nRun: python -m spacy download <model>"
