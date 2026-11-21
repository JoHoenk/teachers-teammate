"""What a content review returns: one report, and what a teacher should know about it."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvaluationOutcome:
    """One content review, as a :class:`~teachers_teammate.interfaces.Evaluator` returns it.

    A review is optional, so one that could not be written is not a failure of the file: it is an
    outcome with no text and a warning, like one that was written but could not cover the whole
    text.
    """

    text: str = ""
    warning: str | None = None
    """Why the review is not what it could be (the request failed, or the text did not fit the
    window); ``None`` when there is nothing to say."""
