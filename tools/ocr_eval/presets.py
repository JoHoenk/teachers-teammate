"""What a page is asked under: no preparation, the app's own, one step less, or in colour.

The preset is a name a record holds, so a figure can be read back to what produced it:

============ =========================================================================
``raw``      the page as it is, but turned by its rotation tag — what the setting
             "Original page" does
``R``        the automatic preparation, exactly as a run prepares a page, in the colour
             the engine wants
``R-<step>`` ``R`` without one step of it, by the step's name in ``preprocess_steps``:
             what the step was worth
``R-colour`` ``R``, but colour handed to the engine that otherwise gets a gray page
============ =========================================================================

``R-<step>`` and ``R-colour`` are the tool's own: the app has two choices, and the tool takes the
app's own list apart rather than keeping a second copy of it.  Which steps there are is the app's
to say: :func:`tools.ocr_eval.prepare.leave_outs` reads them off its list, so this module only
reads the name.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

RAW = "raw"
"""The page as it is: no preparation, the rotation tag applied."""

AUTO = "R"
"""The app's automatic preparation."""

COLOUR = f"{AUTO}-colour"
"""The automatic preparation, with colour kept for an engine that would get a gray page."""

_WITHOUT = re.compile(rf"{AUTO}-([a-z]+)")


@dataclass(frozen=True)
class Preset:
    """One way of asking for a reading, as the command line names it."""

    name: str
    """The preset's own name: ``raw``, ``R``, ``R-light``, …."""

    method: str
    """The app's preprocessing method it is run with: ``none`` or ``auto``."""

    without: str = ""
    """The step it leaves out, or ``""``."""

    colour: bool = False
    """Whether colour is handed to the engine whatever the engine wants."""


def parse(text: str) -> Preset:
    """Return the preset *text* names; whether its step exists is the app's to say.

    Raises:
        ValueError: if *text* is neither ``raw`` nor a preset of the automatic preparation.
    """
    if text == RAW:
        return Preset(RAW, "none")
    if text == AUTO:
        return Preset(AUTO, "auto")
    if text == COLOUR:
        return Preset(COLOUR, "auto", colour=True)
    found = _WITHOUT.fullmatch(text)
    if found:
        return Preset(text, "auto", without=found.group(1))
    raise ValueError(f"{text}: not a preset; name {RAW}, {AUTO}, {COLOUR}, or {AUTO}-<step>")
