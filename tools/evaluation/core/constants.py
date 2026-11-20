"""The few words the whole tooling spells the same way.

Each of these was a string literal in several modules, and each is a value a reader compares
against: a provider name, the name an empty machine is written as, and the kind of a text.  One
copy, so a typo is a name error and not a run that silently pools with nothing.
"""

from __future__ import annotations

from enum import StrEnum

OLLAMA = "ollama"
"""The provider of a server of ours; every other provider ran on none of our machines."""

UNKNOWN_MACHINE = "unknown-machine"
"""What an empty machine is written as in a file name, so the name stays one path segment."""


class TextKind(StrEnum):
    """What a test text is: one with mistakes, or one that is already correct."""

    ERRORS = "errors"
    """A text with mistakes, with a reference correction."""

    CORRECT = "correct"
    """A text that is already correct; the run must leave it alone."""


__all__ = ["OLLAMA", "UNKNOWN_MACHINE", "TextKind"]
