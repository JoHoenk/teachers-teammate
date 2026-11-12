"""The languages of the test set: their codes and the names the corrector is asked in.

A module of its own, so the offline parts (the protocol, the store) and the renderer share the
names without one depending on the other.  This is the one table of the languages the tool can
*ask* in: a corpus declares which of them it has texts in, and a test set holds the ones it was
built with.  Adding a language here changes the prompt, so it needs a new eval version.
"""

from __future__ import annotations

from collections.abc import Iterable

LANGUAGE_NAMES: dict[str, str] = {"de": "German", "en": "English", "fr": "French"}
"""Language codes of the test set and how they are printed."""


def ordered(codes: Iterable[str]) -> list[str]:
    """Return *codes* in the order of the table, then the codes it does not know, sorted.

    A report iterates this instead of the table, so a text in a language without a name is
    printed by its code rather than left out.
    """
    wanted = set(codes)
    known = [code for code in LANGUAGE_NAMES if code in wanted]
    return known + sorted(wanted - set(LANGUAGE_NAMES))


def name(code: str) -> str:
    """Return the name of *code*, or the code itself when the table has no name for it."""
    return LANGUAGE_NAMES.get(code, code)


__all__ = ["LANGUAGE_NAMES", "name", "ordered"]
