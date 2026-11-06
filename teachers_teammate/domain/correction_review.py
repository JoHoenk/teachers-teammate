"""Structured proofreading review: notes per change, unclear expressions, splicing and alignment.

The corrector answers with *sentences* — the sentences it changed or that contain an unclear
expression — each carrying its own list of :class:`ChangeNote`.  This module turns that answer
into (a) the full corrected text, by splicing the corrected sentences back into the source, and
(b) a display model that attaches every note to a hunk of the local word diff.

It is pure: no I/O, no Qt, no provider imports.  ``infrastructure/correction_schema.py`` holds
the Pydantic mirror of these dataclasses; ``application/correction_review.py`` is the entry
point the GUI uses.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
import difflib
import re
from typing import Any, Literal, cast

REVIEW_VERSION = 1
"""Version written by :meth:`CorrectionReview.to_dict`; ``from_dict`` rejects any other."""

CHANGE_CATEGORIES: tuple[str, ...] = (
    "grammar",
    "spelling",
    "punctuation",
    "word_choice",
    "word_order",
    "other",
)

#: Two strings count as the same anchor above this similarity (see :func:`splice`).
_FUZZY_RATIO = 0.9

#: One sentence-like piece of text: up to a sentence end followed by a space, or a line end.
_SEGMENT_RE = re.compile(
    r"[^\n]+?(?:[.!?\u2026]+[\"'\u201c\u201d\u2018\u2019\u00bb)\]]*(?=\s|$)|$)", re.M
)

#: A fuzzy anchor may span this many consecutive sentence-like pieces (a model may merge or
#: split sentences).
_MAX_FUZZY_SEGMENTS = 4

#: Token pattern shared with the GUI's diff, so hunks and spans are measured in the same units.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]|\s+", flags=re.UNICODE)

#: Markdown marks that a stored text may carry but an anchor string should not.
_MARK_RE = re.compile(r"</?(?:sup|sub|u)>|~~|\*\*|__")
_WORD_CHAR_RE = re.compile(r"\w", flags=re.UNICODE)

MINOR_CATEGORIES: frozenset[str] = frozenset({"spelling", "punctuation"})
"""Categories whose edits explain themselves: a typo, a comma.  They are marked in the text but
not listed one by one in the overview of the changes."""


@dataclass(frozen=True)
class ChangeNote:
    """One separate edit inside a sentence, with the explanation shown to the teacher."""

    original: str
    """Exact words from the student's sentence; empty for an insertion."""
    corrected: str
    """Replacement words; empty for a deletion."""
    category: str
    """One of :data:`CHANGE_CATEGORIES` (free-form; unknown values are kept as given)."""
    explanation: str
    """One short sentence saying why the edit was made."""


@dataclass(frozen=True)
class SentenceNote:
    """A changed sentence: its original wording, its corrected wording and the edits."""

    original: str
    """The sentence exactly as written; the anchor :func:`splice` looks for."""
    corrected: str
    """Corrected sentence, without any ``[unclear]`` marker."""
    changes: tuple[ChangeNote, ...] = ()


@dataclass(frozen=True)
class UnclearNote:
    """An unclear or ambiguous expression, even a grammatically correct one."""

    expression: str
    """Exact words from the text; the anchor used to underline them."""
    why: str
    recommendation: str
    is_suggestion: bool = False
    """``True`` when the intended meaning could not be determined: the fix is a suggestion."""


@dataclass(frozen=True)
class CorrectionReview:
    """Everything the model explained about one document."""

    sentences: tuple[SentenceNote, ...] = ()
    unclear: tuple[UnclearNote, ...] = ()
    unplaced: tuple[int, ...] = ()
    """Indices into :attr:`sentences` that :func:`splice` could not place in the text."""

    @property
    def change_count(self) -> int:
        """Number of individual edits the model explained."""
        return sum(len(sentence.changes) for sentence in self.sentences)

    @property
    def unclear_count(self) -> int:
        """Number of unclear or ambiguous expressions."""
        return len(self.unclear)

    def placed_sentences(self) -> tuple[SentenceNote, ...]:
        """Return the sentences that could be placed in the text, in order."""
        skipped = set(self.unplaced)
        return tuple(s for i, s in enumerate(self.sentences) if i not in skipped)

    def unplaced_sentences(self) -> tuple[SentenceNote, ...]:
        """Return the sentences whose wording could not be found in the text."""
        return tuple(s for i, s in enumerate(self.sentences) if i in set(self.unplaced))

    def map_text(self, fn: Callable[[str], str]) -> CorrectionReview:
        """Return a copy with *fn* applied to every text field (used to restore PII)."""
        return CorrectionReview(
            sentences=tuple(
                SentenceNote(
                    original=fn(sentence.original),
                    corrected=fn(sentence.corrected),
                    changes=tuple(
                        ChangeNote(
                            original=fn(change.original),
                            corrected=fn(change.corrected),
                            category=change.category,
                            explanation=fn(change.explanation),
                        )
                        for change in sentence.changes
                    ),
                )
                for sentence in self.sentences
            ),
            unclear=tuple(
                UnclearNote(
                    expression=fn(note.expression),
                    why=fn(note.why),
                    recommendation=fn(note.recommendation),
                    is_suggestion=note.is_suggestion,
                )
                for note in self.unclear
            ),
            unplaced=self.unplaced,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable record, tagged with :data:`REVIEW_VERSION`."""
        return {
            "version": REVIEW_VERSION,
            "sentences": [
                {
                    "original": sentence.original,
                    "corrected": sentence.corrected,
                    "changes": [
                        {
                            "original": change.original,
                            "corrected": change.corrected,
                            "category": change.category,
                            "explanation": change.explanation,
                        }
                        for change in sentence.changes
                    ],
                }
                for sentence in self.sentences
            ],
            "unclear": [
                {
                    "expression": note.expression,
                    "why": note.why,
                    "recommendation": note.recommendation,
                    "is_suggestion": note.is_suggestion,
                }
                for note in self.unclear
            ],
            "unplaced": list(self.unplaced),
        }

    @classmethod
    def from_dict(cls, data: object) -> CorrectionReview | None:
        """Return the review stored in *data*, or ``None`` when it is not one of ours.

        An unknown :data:`REVIEW_VERSION` (an older record, or a record from a newer version)
        yields ``None``: the caller then simply shows no review.
        """
        if not isinstance(data, dict):
            return None
        record = cast("dict[str, Any]", data)
        if record.get("version") != REVIEW_VERSION:
            return None
        try:
            return cls(
                sentences=tuple(
                    SentenceNote(
                        original=str(raw_sentence["original"]),
                        corrected=str(raw_sentence["corrected"]),
                        changes=tuple(
                            ChangeNote(
                                original=str(raw_change.get("original", "")),
                                corrected=str(raw_change.get("corrected", "")),
                                category=str(raw_change.get("category", "other")),
                                explanation=str(raw_change.get("explanation", "")),
                            )
                            for raw_change in raw_sentence.get("changes", [])
                        ),
                    )
                    for raw_sentence in record.get("sentences", [])
                ),
                unclear=tuple(
                    UnclearNote(
                        expression=str(raw_note["expression"]),
                        why=str(raw_note.get("why", "")),
                        recommendation=str(raw_note.get("recommendation", "")),
                        is_suggestion=bool(raw_note.get("is_suggestion", False)),
                    )
                    for raw_note in record.get("unclear", [])
                ),
                unplaced=tuple(int(i) for i in record.get("unplaced", [])),
            )
        except (KeyError, TypeError, ValueError, AttributeError):
            return None


@dataclass(frozen=True)
class CorrectionOutcome:
    """Result of one proofreading call: the text, the optional review and a warning."""

    text: str
    review: CorrectionReview | None = None
    warning: str | None = None
    failed: bool = False
    """``True`` when the provider call failed and *text* is the unchanged source: such an
    outcome is reported but never stored as a finished correction."""


# ── Splicing ──────────────────────────────────────────────────────────────────


def splice(source: str, sentences: Sequence[SentenceNote]) -> tuple[str, tuple[int, ...]]:
    """Rewrite *source* with *sentences*; return ``(text, unplaced_indices)``.

    The full text keeps the student's line breaks, paragraphs and Markdown marks — joining
    the model's sentences would lose them.  Each sentence's ``original`` is looked up from a
    cursor that only moves forward, so a sentence repeated in the text is replaced where it
    occurs next.  Three lookups are tried in turn: exact, whitespace-normalised (an OCR line
    break inside a sentence), then fuzzy within ``2 x len(original)`` characters.  Text
    between two anchors is copied verbatim; a sentence that cannot be found is not applied and
    its index is returned in the second element.
    """
    normalised_source, source_map = _normalised_with_map(source)
    parts: list[str] = []
    cursor = 0
    unplaced: list[int] = []
    for index, sentence in enumerate(sentences):
        span = _find_anchor(source, normalised_source, source_map, cursor, sentence.original)
        if span is None:
            unplaced.append(index)
            continue
        start, end = span
        parts.append(source[cursor:start])
        parts.append(sentence.corrected)
        cursor = end
    parts.append(source[cursor:])
    return "".join(parts), tuple(unplaced)


def _normalised_with_map(text: str) -> tuple[str, tuple[int, ...]]:
    """Return *text* with whitespace runs collapsed to one space, plus per-char source offsets."""
    chars: list[str] = []
    positions: list[int] = []
    pending_space = False
    for position, char in enumerate(text):
        if char.isspace():
            pending_space = bool(chars)
            continue
        if pending_space:
            chars.append(" ")
            positions.append(position)
            pending_space = False
        chars.append(char)
        positions.append(position)
    return "".join(chars), tuple(positions)


def _normalise(text: str) -> str:
    """Return *text* with every whitespace run collapsed to a single space and trimmed."""
    return " ".join(text.split())


def _find_whole(text: str, needle: str, start: int) -> int:
    """Return the first index of *needle* in *text* from *start* that is not inside a word.

    An expression such as ``"it"`` or ``"he"`` is also a part of ``"without"`` and ``"then"``;
    where *needle* begins (ends) with a letter or digit, the character before (after) the match
    must not be one.  ``-1`` when there is no such match.
    """
    check_before = needle[:1].isalnum()
    check_after = needle[-1:].isalnum()
    index = text.find(needle, start)
    while index >= 0:
        end = index + len(needle)
        inside_before = check_before and index > 0 and text[index - 1].isalnum()
        inside_after = check_after and end < len(text) and text[end].isalnum()
        if not (inside_before or inside_after):
            return index
        index = text.find(needle, index + 1)
    return -1


def _find_anchor(
    source: str,
    normalised_source: str,
    source_map: tuple[int, ...],
    cursor: int,
    original: str,
) -> tuple[int, int] | None:
    """Return the ``(start, end)`` of *original* in *source* at or after *cursor*, or ``None``."""
    if not original.strip():
        return None
    exact = _find_whole(source, original, cursor)
    if exact >= 0:
        return exact, exact + len(original)

    needle = _normalise(original)
    if needle:
        from_index = bisect_left(source_map, cursor)
        found = _find_whole(normalised_source, needle, from_index)
        if found >= 0:
            last = found + len(needle) - 1
            return source_map[found], source_map[last] + 1

    return _fuzzy_anchor(source, cursor, original)


def _fuzzy_anchor(source: str, cursor: int, original: str) -> tuple[int, int] | None:
    """Return the span of the sentence-like piece(s) of *source* most similar to *original*.

    Candidates are whole runs of up to :data:`_MAX_FUZZY_SEGMENTS` consecutive sentence-like
    pieces after *cursor*, so the returned span is always a complete piece of the text: replacing
    it can never leave half of the old sentence behind.  The best candidate with a
    ``SequenceMatcher`` ratio of at least :data:`_FUZZY_RATIO` wins; the earliest one on a tie.
    """
    target = original.strip()
    if len(target) < 4:
        return None
    spans: list[tuple[int, int]] = []
    for match in _SEGMENT_RE.finditer(source, cursor):
        piece = match.group()
        stripped = piece.strip()
        if stripped:
            start = match.start() + len(piece) - len(piece.lstrip())
            spans.append((start, start + len(stripped)))
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(target)
    longest = len(target) * (2 / _FUZZY_RATIO - 1)
    best: tuple[int, int] | None = None
    best_ratio = _FUZZY_RATIO
    for first in range(len(spans)):
        for last in range(first, min(first + _MAX_FUZZY_SEGMENTS, len(spans))):
            start, end = spans[first][0], spans[last][1]
            if end - start > longest:
                break
            matcher.set_seq1(source[start:end])
            if matcher.real_quick_ratio() < best_ratio or matcher.quick_ratio() < best_ratio:
                continue
            ratio = matcher.ratio()
            if ratio >= best_ratio and (best is None or ratio > best_ratio):
                best, best_ratio = (start, end), ratio
    return best


# ── Alignment ─────────────────────────────────────────────────────────────────

HunkKind = Literal["replace", "insert", "delete"]


@dataclass(frozen=True)
class Hunk:
    """One contiguous difference of the local word diff."""

    original: str
    """Text of the hunk in the raw text (empty for an insertion)."""
    corrected: str
    """Text of the hunk in the corrected text (empty for a deletion)."""
    kind: HunkKind

    @property
    def summary(self) -> str:
        """Return the hunk as ``old → new``, using the GUI's wording for an empty side."""
        original = " ".join(self.original.split())
        corrected = " ".join(self.corrected.split())
        if self.kind == "delete":
            return f"{original} → (deleted)"
        if self.kind == "insert":
            return f"(inserted) → {corrected}"
        return f"{original} → {corrected}"


@dataclass(frozen=True)
class AlignedChange:
    """A diff hunk together with the note that explains it (``None`` when unexplained)."""

    number: int
    hunk: Hunk
    note: ChangeNote | None
    sentence: int
    """Index of the sentence the note belongs to (``-1`` when the hunk has no note)."""
    position: int = 0
    """Character offset of the hunk in the raw text; orders notes against unclear spans."""
    by_teacher: bool = False
    """``True`` for a hunk without a note that is not part of the model's version of the text
    (the teacher's own edit); ``False`` for a model change that came without an explanation."""

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
    """Display model: hunks with notes, unclear spans, and what could not be matched."""

    changes: tuple[AlignedChange, ...] = ()
    unclear: tuple[AlignedUnclear, ...] = ()
    orphan_notes: tuple[ChangeNote, ...] = ()
    """Notes whose edit is not in the text (the model claimed it, or the teacher reverted it)."""
    unplaced: tuple[SentenceNote, ...] = ()
    """Sentences :func:`splice` could not place in the text."""
    sentence_texts: tuple[str, ...] = ()
    """The corrected wording of every placed sentence as it stands in the corrected text now
    (the model's wording when the sentence can no longer be found, e.g. after an edit)."""

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

    The counts are those of the aligned review -- the listed hunks of the local diff, without
    the spelling and punctuation ones the overview only mentions -- so every place
    that shows them (CLI line, tab title, navigation bar, filter buttons) agrees.  *separator*
    lets the GUI use its own spacing without a second implementation.
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


def tokenize(text: str) -> list[str]:
    """Split *text* into diff tokens, preserving whitespace and punctuation."""
    return _TOKEN_RE.findall(text)


DiffOpcode = tuple[str, int, int, int, int]


def diff_opcodes(raw_tokens: Sequence[str], corrected_tokens: Sequence[str]) -> list[DiffOpcode]:
    """Return the ``SequenceMatcher`` opcodes of the word diff, layout changes counted as equal.

    A difference in whitespace alone (an OCR line break the model joined into a space) is not
    a change a teacher can see or needs explained, so it is reported as ``equal``.  The GUI's
    diff and :func:`align` both number the hunks from this list, so their numbers agree.
    """
    matcher = difflib.SequenceMatcher(None, raw_tokens, corrected_tokens, autojunk=False)
    opcodes: list[DiffOpcode] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        changed = "".join(raw_tokens[i1:i2]) + "".join(corrected_tokens[j1:j2])
        layout_only = tag != "equal" and not changed.strip()
        opcodes.append(("equal" if layout_only else tag, i1, i2, j1, j2))
    return opcodes


def strip_marks(text: str) -> str:
    """Remove the Markdown notation marks from an anchor string."""
    return _MARK_RE.sub("", text)


def align(raw_plain: str, corrected_plain: str, review: CorrectionReview) -> AlignedReview:
    """Match *review*'s notes to the local word diff of *raw_plain* against *corrected_plain*.

    The diff stays the ground truth: a hunk without a note is shown as a change without an
    explanation (or as the teacher's own edit), and a note without a hunk is reported as an
    orphan rather than claiming a change that is not in the text.  One note may explain several
    hunks (``his friend -> her friends`` is two hunks of the word diff).  Both inputs must be
    plain text, and anchors are stripped of notation marks before searching.
    """
    placed = review.placed_sentences()
    raw_spans = _span_of_each(raw_plain, [strip_marks(s.original) for s in placed])
    corrected_spans = _span_of_each(corrected_plain, [strip_marks(s.corrected) for s in placed])
    sentence_texts = tuple(
        corrected_plain[span[0] : span[1]] if span is not None else strip_marks(sentence.corrected)
        for sentence, span in zip(placed, corrected_spans, strict=True)
    )

    notes = [
        _NoteRef(sentence=i, note=change, tokens=change_tokens(change))
        for i, s in enumerate(placed)
        for change in s.changes
    ]
    # A sentence that could be found in neither text has no place a hunk could belong to; its
    # notes are the only ones a hunk outside every sentence may still match.
    unlocated = frozenset(
        index
        for index, (raw_span, corrected_span) in enumerate(
            zip(raw_spans, corrected_spans, strict=True)
        )
        if raw_span is None and corrected_span is None
    )
    used: set[int] = set()
    changes: list[AlignedChange] = []
    for hunk, raw_start, corrected_start in _hunks(raw_plain, corrected_plain):
        sentence = _owning_sentence(raw_spans, corrected_spans, raw_start, corrected_start)
        chosen = _best_note(notes, used, sentence, hunk, unlocated)
        if chosen >= 0:
            used.add(chosen)
        changes.append(
            AlignedChange(
                number=len(changes) + 1,
                hunk=hunk,
                note=notes[chosen].note if chosen >= 0 else None,
                sentence=notes[chosen].sentence if chosen >= 0 else sentence,
                position=raw_start,
                by_teacher=chosen < 0 and not _in_model_version(placed, sentence, hunk),
            )
        )

    orphans = tuple(ref.note for index, ref in enumerate(notes) if index not in used)
    return AlignedReview(
        changes=tuple(changes),
        unclear=_aligned_unclear(raw_plain, review.unclear),
        orphan_notes=orphans,
        unplaced=review.unplaced_sentences(),
        sentence_texts=sentence_texts,
    )


@dataclass(frozen=True)
class _NoteRef:
    """A note together with the index of its sentence and the tokens it changes."""

    sentence: int
    note: ChangeNote
    tokens: frozenset[str]


def text_tokens(text: str) -> frozenset[str]:
    """Return the lower-cased tokens of *text*, punctuation included, without whitespace.

    Punctuation counts: a comma inserted by the model is as much a change as a new word.
    """
    return frozenset(token.lower() for token in tokenize(strip_marks(text)) if token.strip())


def change_tokens(note: ChangeNote) -> frozenset[str]:
    """Return the tokens that *note* actually changes (what only one of its sides has).

    ``Tom -> Tom,`` changes ``,`` alone, so the shared ``Tom`` does not tie the note to every
    other place the word occurs.  A pure reordering changes no token; it then keeps them all.
    """
    before = Counter(_token_list(note.original))
    after = Counter(_token_list(note.corrected))
    changed = frozenset(((before - after) + (after - before)).keys())
    return changed or text_tokens(note.original) | text_tokens(note.corrected)


def _token_list(text: str) -> list[str]:
    """Return the lower-cased tokens of *text* in order (see :func:`text_tokens`)."""
    return [token.lower() for token in tokenize(strip_marks(text)) if token.strip()]


def _hunks(raw: str, corrected: str) -> Iterator[tuple[Hunk, int, int]]:
    """Yield ``(hunk, raw_offset, corrected_offset)`` for every difference between the texts."""
    raw_tokens = tokenize(raw)
    corrected_tokens = tokenize(corrected)
    raw_offsets = _token_offsets(raw_tokens)
    corrected_offsets = _token_offsets(corrected_tokens)
    for tag, i1, i2, j1, j2 in diff_opcodes(raw_tokens, corrected_tokens):
        if tag == "equal":
            continue
        old = "".join(raw_tokens[i1:i2])
        new = "".join(corrected_tokens[j1:j2])
        if old.strip() and new.strip():
            kind: HunkKind = "replace"
        elif old.strip():
            kind = "delete"
        else:
            kind = "insert"
        yield Hunk(original=old, corrected=new, kind=kind), raw_offsets[i1], corrected_offsets[j1]


def _token_offsets(tokens: Sequence[str]) -> list[int]:
    """Return the character offset of every token, plus the end of the last one (index -1)."""
    offsets: list[int] = []
    position = 0
    for token in tokens:
        offsets.append(position)
        position += len(token)
    offsets.append(position)
    return offsets


def _span_of_each(
    text: str, anchors: Sequence[str], *, ordered: bool = True
) -> list[tuple[int, int] | None]:
    """Locate every anchor in *text*; ``None`` where it cannot be found.

    With *ordered* the anchors are expected in text order (each search starts where the last
    match ended), which is how the sentences of a review arrive.  Without it every anchor is
    searched from the start, and two anchors never share a match: the unclear expressions are
    listed by the model in whatever order it found them.
    """
    normalised_text, text_map = _normalised_with_map(text)
    cursor = 0
    taken: set[int] = set()
    spans: list[tuple[int, int] | None] = []
    for anchor in anchors:
        span = None
        if anchor.strip():
            span = _find_anchor(text, normalised_text, text_map, cursor if ordered else 0, anchor)
            while span is not None and not ordered and span[0] in taken:
                span = _find_anchor(text, normalised_text, text_map, span[1], anchor)
        if span is not None:
            if ordered:
                cursor = span[1]
            else:
                taken.add(span[0])
        spans.append(span)
    return spans


def _owning_sentence(
    raw_spans: Sequence[tuple[int, int] | None],
    corrected_spans: Sequence[tuple[int, int] | None],
    raw_start: int,
    corrected_start: int,
) -> int:
    """Return the index of the sentence a hunk belongs to, or ``-1`` when none contains it."""
    for index, span in enumerate(raw_spans):
        if span is not None and span[0] <= raw_start < span[1]:
            return index
    for index, span in enumerate(corrected_spans):
        if span is not None and span[0] <= corrected_start < span[1]:
            return index
    return -1


def _in_model_version(placed: Sequence[SentenceNote], sentence: int, hunk: Hunk) -> bool:
    """Whether *hunk* is part of the model's corrected wording of its sentence.

    A hunk inside a changed sentence whose new words the model also wrote is the model's change
    (merely without an explanation); anything else is the teacher's own edit.
    """
    if sentence < 0:
        return False
    return text_tokens(hunk.corrected) <= text_tokens(placed[sentence].corrected)


def _best_note(
    notes: Sequence[_NoteRef],
    used: set[int],
    sentence: int,
    hunk: Hunk,
    unlocated: frozenset[int] = frozenset(),
) -> int:
    """Return the index of the note that best explains *hunk*, or ``-1``.

    A note explains a hunk when it changes the hunk's tokens.  Notes of the hunk's own sentence
    are preferred; the notes of other sentences are only considered when none of them fits, so
    a sentence the model split differently still finds its explanation.  A hunk outside every
    sentence of the review (the teacher's own edit in an untouched sentence) may only use the
    notes of *unlocated* sentences -- the ones whose wording could not be found in the text --
    never another sentence's note.  Among the fitting notes, the one covering most of the hunk
    wins, and a note not yet used breaks ties -- a note may serve several hunks, but two
    identical edits get their own notes first.
    """
    hunk_tokens = text_tokens(hunk.original) | text_tokens(hunk.corrected)
    if not hunk_tokens:
        return -1
    for local in (True, False) if sentence >= 0 else (False,):
        best = -1
        best_key: tuple[float, bool, int, int] | None = None
        for index, ref in enumerate(notes):
            if local and ref.sentence != sentence:
                continue
            if sentence < 0 and ref.sentence not in unlocated:
                continue
            overlap = len(hunk_tokens & ref.tokens)
            if not overlap:
                continue
            key = (overlap / len(hunk_tokens), index not in used, overlap, -index)
            if best_key is None or key > best_key:
                best, best_key = index, key
        if best >= 0:
            return best
    return -1


def _aligned_unclear(raw_plain: str, notes: Sequence[UnclearNote]) -> tuple[AlignedUnclear, ...]:
    """Anchor every unclear note in *raw_plain*; unanchored notes are still returned."""
    spans = _span_of_each(
        raw_plain, [strip_marks(note.expression) for note in notes], ordered=False
    )
    aligned: list[AlignedUnclear] = []
    for index, (note, span) in enumerate(zip(notes, spans, strict=True)):
        aligned.append(
            AlignedUnclear(
                number=index + 1,
                note=note,
                start=span[0] if span else None,
                end=span[1] if span else None,
            )
        )
    return tuple(aligned)
