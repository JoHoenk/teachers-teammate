"""Settings vocabulary of the proofreading stage that every layer shares."""

from __future__ import annotations

LEVELS: tuple[str, ...] = ("A1", "A2", "B1", "B2", "C1", "C2")
"""CEFR levels accepted by ``Config.correction_level`` (language-neutral)."""

DEFAULT_LEVEL = "B2"

OUTPUT_MODES: tuple[str, ...] = ("review", "text")
"""``review`` = structured answer with explanations; ``text`` = corrections only."""

DEFAULT_OUTPUT_MODE = "review"


def normalize_level(level: str) -> str:
    """Return *level* when it is a CEFR level of :data:`LEVELS`, else :data:`DEFAULT_LEVEL`."""
    cleaned = str(level).strip().upper()
    return cleaned if cleaned in LEVELS else DEFAULT_LEVEL


def normalize_output_mode(mode: str) -> str:
    """Return *mode* when it is one of :data:`OUTPUT_MODES`, else :data:`DEFAULT_OUTPUT_MODE`.

    Every layer reads the setting through this function, so an unknown value (a typo in the
    config file) means the same everywhere: explanations on.
    """
    cleaned = str(mode).strip().lower()
    return cleaned if cleaned in OUTPUT_MODES else DEFAULT_OUTPUT_MODE


CUT_OFF_TRIES = 2
"""How often a review request is sent when the window cuts its answer off.

A cut-off is usually a runaway (a model that starts listing correct fragments with an empty
change), not a text that is too long, so a second try very likely answers.  After the last try
the corrector falls back to corrections only."""


def cut_off_tries(*, online: bool, temperature: float) -> int:
    """Return how often a cut-off review request is sent: :data:`CUT_OFF_TRIES`, or once.

    A second try costs money at an online service and is the same answer again at temperature 0,
    so it is only worth sending to a model on this computer or the school's own server, with
    some randomness in the sampling.
    """
    return 1 if online or temperature <= 0 else CUT_OFF_TRIES
