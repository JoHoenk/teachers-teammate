"""How a model is asked to proofread: the app's review answer, or the corrected text only.

A module of its own, so the offline parts (the protocol, the store) and the run share the values
without one depending on the other.  A ``StrEnum`` serialises as its value, so result rows,
headers, run ids and the fingerprint keep the bytes they had when the mode was a bare string.
"""

from __future__ import annotations

from enum import StrEnum


class AnswerMode(StrEnum):
    """How a model is asked to proofread."""

    REVIEW = "review"
    """The app's JSON answer: corrections with explanations."""

    TEXT = "text"
    """The corrected text only."""

    TWO_STEP = "two-step"
    """Step 1 in text mode, step 2 explains (planned; nothing writes it yet)."""

    @property
    def asks_corrector(self) -> bool:
        """Whether the app's corrector can be asked in this mode (review and text)."""
        return self in CORRECTOR_MODES


CORRECTOR_MODES: tuple[AnswerMode, ...] = (AnswerMode.REVIEW, AnswerMode.TEXT)
"""The modes the app's corrector answers in; the fingerprint covers these."""

DEFAULT_MODE = AnswerMode.REVIEW
"""The answer mode of the app's proofreading: corrections with explanations in one JSON answer."""


def parse_mode(value: str) -> AnswerMode:
    """Return the mode *value* names.

    Raises:
        ValueError: if *value* is not a mode, naming the allowed values.
    """
    try:
        return AnswerMode(value)
    except ValueError:
        allowed = ", ".join(mode.value for mode in AnswerMode)
        raise ValueError(f"unknown answer mode {value!r}; use one of: {allowed}") from None


__all__ = ["CORRECTOR_MODES", "DEFAULT_MODE", "AnswerMode", "parse_mode"]
