"""The corpus label of a reference change, and the app's category it stands for.

The corpora (Falko-MERLIN, W&I+LOCNESS) ship, besides the corrected text, an M2 file that lists
every correction with a label such as ``R:DET:FORM`` (a determiner replaced by another form) or
``M:PUNCT`` (a missing mark).  A test record keeps the *code* of every reference change, never the
corpus text, so a labelled test set stays as shareable as an unlabelled one.

Only the vocabulary lives here: reading an M2 file and finding a label's change is the input
layer's work.  A record needs the type, and the report needs the category, so both are here.
"""

from __future__ import annotations

from dataclasses import dataclass

from teachers_teammate.domain.proofreading.review import CHANGE_CATEGORIES

UNLABELLED = "unlabelled"
"""Name of the group that holds the reference changes no annotated edit could be found for."""

CATEGORIES: tuple[str, ...] = CHANGE_CATEGORIES
"""The app's categories, imported so a new one is added in one place."""

_CLOSED_CLASS = frozenset(
    {"DET", "PREP", "ADP", "PRON", "PART", "CONJ", "SCONJ", "AUX", "CONTR", "MORPH"}
)
_OPEN_CLASS = frozenset({"NOUN", "VERB", "ADJ", "ADV", "PNOUN"})
_FORM_SUBTYPES = frozenset({"FORM", "INFL", "NUM", "SVA", "TENSE", "POSS"})


@dataclass(frozen=True)
class ReferenceLabel:
    """One change the reference correction makes, with the corpus label of that error.

    ``start`` and ``end`` are **character** offsets into the text's ``source``, end
    exclusive, so a stored label does not depend on the tokenizer.  The scorer maps them to tokens
    when it needs to.
    """

    start: int
    """First character of the change."""

    end: int
    """One past the last character; equal to ``start`` for an insertion."""

    replacement: str
    """The text that replaces the source characters; empty for a deletion."""

    code: str
    """The corpus label (``R:DET:FORM``); empty when no annotated edit matched this change."""

    @property
    def span(self) -> tuple[int, int]:
        """Return the change's character span, as the test record stores it."""
        return (self.start, self.end)

    @property
    def category(self) -> str:
        """Return the app's category for this error (see :func:`category_of`); ``""`` if none."""
        return category_of(self.code)

    @property
    def main_type(self) -> str:
        """Return the kind of word the error is about (``DET``, ``PUNCT``…), or the unlabelled name."""
        return main_type(self.code)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable label."""
        return {
            "span": [self.start, self.end],
            "replacement": self.replacement,
            "code": self.code,
        }

    @classmethod
    def from_dict(cls, record: dict) -> ReferenceLabel:
        """Build a label from :meth:`to_dict` output."""
        start, end = record["span"]
        return cls(
            start=int(start),
            end=int(end),
            replacement=str(record.get("replacement", "")),
            code=str(record.get("code", "")),
        )


def _parts(code: str) -> tuple[str, str, str]:
    """Return ``(operation, type, subtype)`` of a label; empty strings for what is missing."""
    parts = code.split(":")
    if parts and parts[0] in {"M", "R", "U"}:
        operation, parts = parts[0], parts[1:]
    else:
        operation = ""
    return operation, parts[0] if parts else "", parts[1] if len(parts) > 1 else ""


def main_type(code: str) -> str:
    """Return the type of a label without operation and subtype: ``R:VERB:FORM`` → ``VERB``."""
    return _parts(code)[1] or UNLABELLED


def category_of(code: str) -> str:
    """Return the app's category (``grammar``, ``spelling``…) a corpus label stands for.

    A coarse mapping, for the one question "did the model call the error roughly what the corpus
    calls it?": a label with no clear counterpart (``OTHER``, a missing noun) maps to ``other``,
    and an empty code to ``""``.  Neither is checked.
    """
    if not code:
        return ""
    operation, kind, sub = _parts(code)
    if kind == "PUNCT":
        return "punctuation"
    if kind in {"SPELL", "ORTH"}:
        return "spelling"
    if kind == "WO":
        return "word_order"
    if kind in _CLOSED_CLASS:
        return "grammar"
    if kind in _OPEN_CLASS:
        if sub in _FORM_SUBTYPES or (operation in {"M", "U"} and kind == "VERB"):
            return "grammar"
        return "word_choice" if operation == "R" else "other"
    return "other"


__all__ = [
    "CATEGORIES",
    "UNLABELLED",
    "ReferenceLabel",
    "category_of",
    "main_type",
]
