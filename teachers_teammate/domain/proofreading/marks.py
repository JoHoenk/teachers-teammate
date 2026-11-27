"""The Markdown marks some writing models add to "highlight" their changes.

A model that puts literal asterisks or ``[unclear]`` into the text a teacher receives has changed
the text for nobody's benefit; a structured-output check does not see it.  The app flags it as a
problem and the tools count it apart from the words, so the list lives here once.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

MARKS = ("**", "__", "~~", "[unclear]")
"""Markdown marks some models add to "highlight" their changes; not part of the text."""


def strip_added_marks(source: str, output: str) -> str:
    """Return *output* without the Markdown marks that *source* does not contain.

    Scoring ignores them by default (``report --count-marks`` counts them): only the words matter.
    """
    for mark in MARKS[:-1]:
        if mark not in source:
            output = output.replace(mark, "")
    return output


def added_marks(source: str, output: str) -> bool:
    """Whether the output carries Markdown marks the source does not.

    A model that "highlights" its changes puts literal asterisks into the text a teacher receives;
    the structured-output check does not see it.
    """
    return any(mark in output and mark not in source for mark in MARKS)
