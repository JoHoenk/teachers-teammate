"""The stored review of a correction: edits with their notes, unclear flags, and their alignment.

A review is *edit-based*: the edits are the hunks of the word diff between the student's text and
the corrected text, numbered 1..k in text order, and every note belongs to one of those numbers.
The model explains edits; it never places them.  That is what keeps a stored review valid when the
teacher edits the text afterwards: the diff is taken again and every current hunk is matched to the
stored edit it came from.

Coordinates are character offsets into the *plain* text (``TextFormatter.plain_text``, crossed-out
words included), so a review is anchored on exactly the strings :func:`align` sees.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import re
from typing import Any, cast

from .edits import Edit, EditPart
from .word_diff import Hunk, word_diff

REVIEW_VERSION = 2
"""Version written by :meth:`CorrectionReview.to_dict`; ``from_dict`` rejects any other."""

CHANGE_CATEGORIES: tuple[str, ...] = (
    "grammar",
    "spelling",
    "punctuation",
    "word_choice",
    "word_order",
    "other",
)

MINOR_CATEGORIES: frozenset[str] = frozenset({"spelling", "punctuation"})
"""Categories whose edits explain themselves: a typo, a comma.  They are marked in the text but
not listed one by one in the overview of the changes."""

_WORD = re.compile(r"\w+", re.UNICODE)
_WORD_CHAR_RE = re.compile(r"\w", flags=re.UNICODE)


@dataclass(frozen=True)
class EditNote:
    """One edit of the correction, with the explanation shown to the teacher."""

    id: int
    """Number of the edit, 1..k in text order over the whole text."""
    parts: tuple[EditPart, ...]
    """The hunks of the edit, in text order and disjoint."""
    sentences: tuple[int, ...]
    """Indices of the source sentences the edit touches (context only)."""
    category: str
    """One of :data:`CHANGE_CATEGORIES` (free-form; unknown values are kept as given)."""
    explanation: str
    """One short sentence saying why the edit was made; empty when it was not explained."""
    optional: bool = False
    """``True`` for a comma that is a matter of style, not a mistake."""
    needed: bool | None = None
    """The model's own doubt about the edit; a marker only, never shown as a correction."""

    @property
    def raw_start(self) -> int:
        """Offset of the first character the edit changes."""
        return self.parts[0].raw_start

    @property
    def raw_end(self) -> int:
        """Offset after the last character the edit changes."""
        return max(part.raw_end for part in self.parts)

    @property
    def original(self) -> str:
        """The student's words of every part, in order."""
        return " ".join(part.original for part in self.parts if part.original)

    @property
    def corrected(self) -> str:
        """The corrected words of every part, in order."""
        return " ".join(part.corrected for part in self.parts if part.corrected)


@dataclass(frozen=True)
class UnclearNote:
    """Wording that is grammatically fine but whose meaning a reader cannot tell."""

    raw_start: int
    raw_end: int
    """Half-open span in the raw text; equal ends when the quote could not be found."""
    expression: str
    """The words the model quoted."""
    why: str
    recommendation: str
    is_suggestion: bool = False
    """``True`` when the intended meaning could not be determined: the fix is a suggestion."""


@dataclass(frozen=True)
class CorrectionReview:
    """Everything the model explained about one document."""

    edits: tuple[EditNote, ...] = ()
    unclear: tuple[UnclearNote, ...] = ()

    @property
    def edit_count(self) -> int:
        """Number of edits the review carries."""
        return len(self.edits)

    @property
    def unclear_count(self) -> int:
        """Number of unclear or ambiguous expressions."""
        return len(self.unclear)

    def note(self, edit_id: int) -> EditNote | None:
        """Return the note of edit *edit_id*, or ``None``."""
        return next((note for note in self.edits if note.id == edit_id), None)

    def map_text(self, fn: Callable[[str], str]) -> CorrectionReview:
        """Return a copy with *fn* applied to every text field (used to restore personal data).

        The offsets stay as they are: they are only valid while *fn* keeps the length of what it
        replaces, which is what the anonymiser's restore does.
        """
        return CorrectionReview(
            edits=tuple(
                EditNote(
                    id=note.id,
                    parts=tuple(
                        EditPart(
                            raw_start=part.raw_start,
                            raw_end=part.raw_end,
                            original=fn(part.original),
                            corrected=fn(part.corrected),
                        )
                        for part in note.parts
                    ),
                    sentences=note.sentences,
                    category=note.category,
                    explanation=fn(note.explanation),
                    optional=note.optional,
                    needed=note.needed,
                )
                for note in self.edits
            ),
            unclear=tuple(
                UnclearNote(
                    raw_start=note.raw_start,
                    raw_end=note.raw_end,
                    expression=fn(note.expression),
                    why=fn(note.why),
                    recommendation=fn(note.recommendation),
                    is_suggestion=note.is_suggestion,
                )
                for note in self.unclear
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable record, tagged with :data:`REVIEW_VERSION`."""
        return {
            "version": REVIEW_VERSION,
            "edits": [
                {
                    "id": note.id,
                    "parts": [
                        {
                            "raw_start": part.raw_start,
                            "raw_end": part.raw_end,
                            "original": part.original,
                            "corrected": part.corrected,
                        }
                        for part in note.parts
                    ],
                    "sentences": list(note.sentences),
                    "category": note.category,
                    "explanation": note.explanation,
                    "optional": note.optional,
                    "needed": note.needed,
                }
                for note in self.edits
            ],
            "unclear": [
                {
                    "raw_start": note.raw_start,
                    "raw_end": note.raw_end,
                    "expression": note.expression,
                    "why": note.why,
                    "recommendation": note.recommendation,
                    "is_suggestion": note.is_suggestion,
                }
                for note in self.unclear
            ],
        }

    @classmethod
    def from_dict(cls, data: object) -> CorrectionReview | None:
        """Return the review stored in *data*, or ``None`` when it is not one of ours.

        A record without :data:`REVIEW_VERSION` (an older review, or none at all) yields ``None``:
        the caller then simply shows no review.
        """
        if not isinstance(data, dict):
            return None
        record = cast("dict[str, Any]", data)
        if record.get("version") != REVIEW_VERSION:
            return None
        try:
            return cls(
                edits=tuple(
                    EditNote(
                        id=int(raw_edit["id"]),
                        parts=tuple(
                            EditPart(
                                raw_start=int(raw_part["raw_start"]),
                                raw_end=int(raw_part["raw_end"]),
                                original=str(raw_part.get("original", "")),
                                corrected=str(raw_part.get("corrected", "")),
                            )
                            for raw_part in raw_edit.get("parts", [])
                        ),
                        sentences=tuple(int(i) for i in raw_edit.get("sentences", [])),
                        category=str(raw_edit.get("category", "other")),
                        explanation=str(raw_edit.get("explanation", "")),
                        optional=bool(raw_edit.get("optional", False)),
                        needed=raw_edit.get("needed"),
                    )
                    for raw_edit in record.get("edits", [])
                ),
                unclear=tuple(
                    UnclearNote(
                        raw_start=int(raw_note.get("raw_start", 0)),
                        raw_end=int(raw_note.get("raw_end", 0)),
                        expression=str(raw_note.get("expression", "")),
                        why=str(raw_note.get("why", "")),
                        recommendation=str(raw_note.get("recommendation", "")),
                        is_suggestion=bool(raw_note.get("is_suggestion", False)),
                    )
                    for raw_note in record.get("unclear", [])
                ),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


def edit_note(
    edit: Edit,
    *,
    category: str = "other",
    explanation: str = "",
    optional: bool = False,
    needed: bool | None = None,
) -> EditNote:
    """Return *edit* as a stored note, with what the explain step said about it."""
    return EditNote(
        id=edit.id,
        parts=edit.parts,
        sentences=edit.sentences,
        category=category,
        explanation=explanation,
        optional=optional,
        needed=needed,
    )


# ── Alignment ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AlignedChange:
    """A current diff hunk together with the stored edit it came from."""

    number: int
    hunk: Hunk
    note: EditNote | None
    """The stored note, or ``None`` for a hunk the review does not know."""
    position: int = 0
    """Character offset of the hunk in the raw text; orders notes against unclear spans."""
    by_teacher: bool = False
    """``True`` for a hunk that is not part of the model's version of the text (the teacher's own
    edit)."""
    edit_id: int | None = None
    """Number of the stored edit the hunk belongs to, or ``None``."""
    changed_afterwards: bool = False
    """``True`` when the teacher changed the edit: the note stays, marked as changed."""

    @property
    def is_minor(self) -> bool:
        """Whether this change explains itself: spelling or punctuation only.

        A hunk without a note counts when it touches no word character (a comma added or
        removed), because there is nothing a teacher would still have to be told about it.
        """
        if self.note is not None:
            return self.note.category in MINOR_CATEGORIES
        return not _WORD_CHAR_RE.search(self.hunk.original + self.hunk.corrected)

    @property
    def category(self) -> str:
        """Return the note's category, or ``other`` for a hunk without a note."""
        return self.note.category if self.note is not None else "other"

    @property
    def explanation(self) -> str:
        """Return the note's explanation, or ``""`` for a hunk without a note."""
        return self.note.explanation if self.note is not None else ""


@dataclass(frozen=True)
class AlignedUnclear:
    """An unclear expression and, when it could be found, its span in the raw text."""

    number: int
    note: UnclearNote
    start: int | None = None
    end: int | None = None

    @property
    def anchored(self) -> bool:
        """Whether the expression was found in the raw text."""
        return self.start is not None and self.end is not None


@dataclass(frozen=True)
class AlignedReview:
    """Display model: the current hunks with their notes, and the unclear spans."""

    changes: tuple[AlignedChange, ...] = ()
    unclear: tuple[AlignedUnclear, ...] = ()
    orphan_notes: tuple[EditNote, ...] = ()
    """Notes whose edit is not in the text any more (the teacher undid it)."""

    @property
    def change_count(self) -> int:
        """Number of diff hunks, minor ones included."""
        return len(self.changes)

    @property
    def listed_changes(self) -> tuple[AlignedChange, ...]:
        """The changes the overview lists one by one: everything but the minor ones."""
        return tuple(change for change in self.changes if not change.is_minor)

    @property
    def minor_changes(self) -> tuple[AlignedChange, ...]:
        """The spelling and punctuation changes the overview only counts."""
        return tuple(change for change in self.changes if change.is_minor)

    @property
    def listed_count(self) -> int:
        """Number of changes the overview lists (the number a teacher is shown)."""
        return len(self.listed_changes)

    @property
    def minor_count(self) -> int:
        """Number of spelling and punctuation changes."""
        return len(self.minor_changes)

    @property
    def unclear_count(self) -> int:
        """Number of unclear expressions."""
        return len(self.unclear)


def review_summary(aligned: AlignedReview | None, *, separator: str = ", ") -> str:
    """Return a one-line count such as ``"7 changes, 2 unclear"`` (empty without a review).

    The counts are those of the aligned review -- the listed hunks of the local diff, without the
    spelling and punctuation ones the overview only mentions -- so every place that shows them
    (CLI line, tab title, navigation bar, filter buttons) agrees.
    """
    if aligned is None:
        return ""
    changes = aligned.listed_count
    unclear = aligned.unclear_count
    if not changes and not unclear:
        minor = aligned.minor_count
        return f"{minor} minor {'correction' if minor == 1 else 'corrections'}" if minor else ""
    parts = [f"{changes} {'change' if changes == 1 else 'changes'}"]
    if unclear:
        parts.append(f"{unclear} unclear")
    return separator.join(parts)


def align(raw_plain: str, corrected_plain: str, review: CorrectionReview) -> AlignedReview:
    """Match *review*'s notes to the current word diff of *raw_plain* against *corrected_plain*.

    The diff stays the ground truth: a hunk the review does not know is shown as the teacher's own
    edit, and a note without a hunk is reported as an orphan rather than claiming a change that is
    not in the text.  A stored part with the same span and the same replacement is an exact match;
    a part whose span overlaps but whose replacement differs is the same edit changed afterwards,
    so its note stays and is marked.  Both inputs must be plain text.
    """
    hunks = word_diff(raw_plain, corrected_plain)
    parts = [(note, part) for note in review.edits for part in note.parts]
    used: set[int] = set()
    changes: list[AlignedChange] = []
    for hunk in hunks:
        index, changed = _match(parts, used, hunk, raw_plain)
        note = parts[index][0] if index >= 0 else None
        if index >= 0:
            used.add(index)
        changes.append(
            AlignedChange(
                number=len(changes) + 1,
                hunk=hunk,
                note=note,
                position=hunk.raw_start,
                by_teacher=index < 0,
                edit_id=note.id if note is not None else None,
                changed_afterwards=changed,
            )
        )
    matched = {parts[index][0].id for index in used}
    return AlignedReview(
        changes=tuple(changes),
        unclear=_aligned_unclear(review.unclear),
        orphan_notes=tuple(note for note in review.edits if note.id not in matched),
    )


def _match(
    parts: Sequence[tuple[EditNote, EditPart]],
    used: set[int],
    hunk: Hunk,
    raw_plain: str,
) -> tuple[int, bool]:
    """Return the index of the stored part that explains *hunk*, and whether it changed.

    ``(-1, False)`` when no stored part belongs to the hunk.
    """
    exact = [index for index, (_, part) in enumerate(parts) if _is_exact(part, hunk, raw_plain)]
    if exact:
        return _prefer(parts, exact, used, hunk), False
    near = [index for index, (_, part) in enumerate(parts) if _overlaps(part, hunk, raw_plain)]
    if near:
        return _prefer(parts, near, used, hunk), True
    return -1, False


def _is_exact(part: EditPart, hunk: Hunk, raw_plain: str) -> bool:
    """Whether *part* is the same edit as *hunk*: same span and same replacement.

    An insertion counts as the same edit when it carries the same words in the same gap between
    two words: repeated words can move the offset of an insertion by a token without changing
    what the model did.
    """
    if part.raw_start == hunk.raw_start and part.raw_end == hunk.raw_end:
        return part.corrected == hunk.corrected
    return (
        part.raw_start == part.raw_end
        and hunk.raw_start == hunk.raw_end
        and part.corrected == hunk.corrected
        and _same_gap(raw_plain, part.raw_start, hunk.raw_start)
    )


def _prefer(
    parts: Sequence[tuple[EditNote, EditPart]],
    candidates: Sequence[int],
    used: set[int],
    hunk: Hunk,
) -> int:
    """Return the best of *candidates*: unused first, then the most shared tokens, then the first."""
    tokens = _tokens(hunk.original) | _tokens(hunk.corrected)
    best = candidates[0]
    best_key: tuple[bool, int, int] | None = None
    for index in candidates:
        part = parts[index][1]
        overlap = len(tokens & (_tokens(part.original) | _tokens(part.corrected)))
        key = (index not in used, overlap, -index)
        if best_key is None or key > best_key:
            best, best_key = index, key
    return best


def _overlaps(part: EditPart, hunk: Hunk, raw_plain: str) -> bool:
    """Whether *part* and *hunk* cover the same place of the raw text.

    Two insertions belong together when nothing but layout lies between them: repeated words can
    shift an insertion by one token, and the note still explains it.
    """
    if part.raw_start == part.raw_end and hunk.raw_start == hunk.raw_end:
        return _same_gap(raw_plain, part.raw_start, hunk.raw_start)
    if part.raw_start == part.raw_end:
        return hunk.raw_start < part.raw_start < hunk.raw_end
    if hunk.raw_start == hunk.raw_end:
        return part.raw_start < hunk.raw_start < part.raw_end
    return max(part.raw_start, hunk.raw_start) < min(part.raw_end, hunk.raw_end)


def _same_gap(text: str, first: int, second: int) -> bool:
    """Whether only layout lies between two offsets of *text*."""
    low, high = min(first, second), max(first, second)
    return not _WORD.search(text[low:high])


def _tokens(text: str) -> frozenset[str]:
    """Return the lower-cased words of *text*."""
    return frozenset(_WORD.findall(text.casefold()))


def _aligned_unclear(notes: Sequence[UnclearNote]) -> tuple[AlignedUnclear, ...]:
    """Return the unclear notes as display values, anchored where they were found."""
    return tuple(
        AlignedUnclear(
            number=index + 1,
            note=note,
            start=note.raw_start if note.raw_end > note.raw_start else None,
            end=note.raw_end if note.raw_end > note.raw_start else None,
        )
        for index, note in enumerate(notes)
    )
