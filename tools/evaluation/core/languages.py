"""The languages of the test set: their codes and the names the corrector is asked in.

This is the one table of the languages the tool can *ask* in: a corpus declares which of them it
has texts in, and a test set holds the ones it was built with.  Adding a language here changes the
prompt, so it needs a new eval version.

The names come from the app (:func:`teachers_teammate.domain.language.language_name`), so a
language is spelled the same way in a prompt and in a report.
"""

from __future__ import annotations

from collections.abc import Iterable

from teachers_teammate.domain.language import language_name

LANGUAGE_CODES: tuple[str, ...] = ("de", "en", "fr")
"""The languages the tools can ask a model to proofread in."""

LANGUAGE_NAMES: dict[str, str] = {code: language_name(code) for code in LANGUAGE_CODES}
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


def is_language_code(text: str) -> bool:
    """Return whether *text* looks like a language code: two or three lower-case ASCII letters.

    A test set's folder holds ``de.jsonl`` next to ``notes.jsonl``; only the first is a language.
    """
    return 2 <= len(text) <= 3 and text.isascii() and text.isalpha() and text.islower()


__all__ = ["LANGUAGE_CODES", "LANGUAGE_NAMES", "is_language_code", "name", "ordered"]
