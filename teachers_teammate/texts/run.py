"""What a run says about itself: the notes a teacher sees while it works.

The pipeline reports *what* happened (a file was saved, a run is on battery power); the
sentence is here, so the wording is reviewable in one place.
"""

from __future__ import annotations

from ..domain.stages import Stage

#: What the banner and the main window say when a run starts on battery power.
BATTERY_WARNING = "Running on battery: plug in the charger, and keep the lid open."

#: What a row says about a file a stop cut short, by the step it did not get to.
_STOPPED: dict[str, str] = {
    Stage.OCR: "Stopped before the text was read.",
    Stage.CORRECTION: "Stopped before proofreading.",
    Stage.EVALUATION: "Stopped before the review.",
}

#: What a row says about a file the stop reached before any step of it ran.
STOPPED_BY_USER = "Stopped by user."


def stopped_message(stage: str) -> str:
    """Return what a row says for a file a stop cut short at *stage*."""
    return _STOPPED.get(stage, STOPPED_BY_USER)
