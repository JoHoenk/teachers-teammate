"""The prompts of the content review: its default instructions, the Markdown hint, the message.

The instructions are the one prompt a teacher may replace (*Options…* of *Review content*); the
hint is appended to them by the app and the message carries the text.  Pure text, no I/O.  The
proofreading prompts are in ``correction_prompts.py``.
"""

from __future__ import annotations

from .formatted_text import TextFormat

REVIEW_SYSTEM = (
    "You help a teacher read a student's text. Review its content, not its spelling, "
    "grammar or punctuation. Write a short report with: "
    "1) what the text is about, in one sentence; "
    "2) whether it is complete and consistent; "
    "3) gaps, contradictions or passages that are hard to follow, each with a short quote; "
    "4) one short suggestion for the teacher. "
    "Write in the language of the text and keep the report brief."
)
"""What the content review asks when the teacher has not replaced it."""

_NOTATION_HINT = (
    "The text is written in Markdown. ~~text~~ marks words the writer crossed out; they are "
    "not part of the submission and must not be evaluated. <sup>text</sup> marks words the "
    "writer inserted above the line; treat them as part of the sentence. "
    "All other Markdown marks only describe formatting."
)


def notation_hint(text_format: TextFormat) -> str:
    """Return the hint for a *text_format* the review reads as Markdown, ``""`` for plain text."""
    return _NOTATION_HINT if text_format == "markdown" else ""


def review_system(instructions: str, text_format: TextFormat) -> str:
    """Return the system prompt of a review: *instructions*, then the hint for a Markdown text.

    The hint stays out of what a teacher edits, so a plain text's prompt is the instructions.
    """
    hint = notation_hint(text_format)
    return f"{instructions}\n\n{hint}" if hint else instructions


def review_message(language: str, text: str) -> str:
    """Return the user message of a review request: the language, then the text."""
    return f"Language: {language}\n\nCorrected text:\n\n{text}"


def review_wording() -> str:
    """Return the wording the app adds around the instructions, for the key of a stored review.

    A change to the message or to the hint changes what a model reads, so a review made with the
    old wording is not the one the new wording would give.
    """
    return "\n\n".join((review_message("{language}", "{text}"), notation_hint("markdown")))
