"""The stages of the pipeline: the ones whose result the store keeps, and the steps reported.

Both are :class:`~enum.StrEnum` members, so a stored JSON file, a settings value and a log line
carry the plain word (``"ocr"``) while the code names the member.
"""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """A stage whose result is stored with the document."""

    OCR = "ocr"
    CORRECTION = "correction"
    EVALUATION = "evaluation"


#: The stored stages in the order a run does them: the order a stale one cascades in.
STAGE_ORDER: tuple[Stage, ...] = (Stage.OCR, Stage.CORRECTION, Stage.EVALUATION)


class ProgressStage(StrEnum):
    """A step the window reports while one file is processed.

    Not a :class:`Stage`: preparing the pages leaves no result of its own to invalidate.
    """

    PREPROCESSING = "preprocessing"
    OCR = "ocr"
    CORRECTION = "correction"


def normalize_stage(stage: str) -> Stage:
    """Return the stage *stage* names.

    Raises:
        ValueError: When *stage* names none of :data:`STAGE_ORDER`.
    """
    value = stage.strip().lower()
    try:
        return Stage(value)
    except ValueError:
        msg = f"Invalid stage '{stage}'. Expected one of: {', '.join(STAGE_ORDER)}"
        raise ValueError(msg) from None
