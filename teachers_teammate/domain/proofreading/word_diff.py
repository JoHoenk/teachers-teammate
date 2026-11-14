"""The one word diff of the app.

Every place that compares two texts — the GUI's preview, the DOCX builder, ``align``, the
edits of a review and the evaluation tools — takes its hunks from here, so two views of the
same correction can never disagree about where a change begins and ends.

The engine sits behind :func:`diff_opcodes`: ``difflib``'s ``SequenceMatcher`` today, with
``autojunk=False`` because a text of a few hundred words is small enough that the heuristic
would only cost accuracy.  A change of tokenisation or engine is a new evaluation protocol
version, because it moves the boundaries a run is measured on.

Pure: no I/O, no Qt, no provider imports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import difflib
import re
from typing import Literal, cast

HunkKind = Literal["replace", "insert", "delete"]

#: Token pattern of the word diff: words, single punctuation marks and whitespace runs.
_TOKEN_RE = re.compile(r"\w+|[^\w\s]|\s+", flags=re.UNICODE)

DiffOpcode = tuple[str, int, int, int, int]
"""``(tag, i1, i2, j1, j2)``: the tokens ``raw[i1:i2]`` become ``corrected[j1:j2]``."""


def tokenize(text: str) -> list[str]:
    """Split *text* into diff tokens, preserving whitespace and punctuation."""
    return _TOKEN_RE.findall(text)


def diff_opcodes(
    raw_tokens: Sequence[object], corrected_tokens: Sequence[object]
) -> list[DiffOpcode]:
    """Return the opcodes of the word diff, layout changes counted as equal.

    A difference in whitespace alone (an OCR line break the model joined into a space) is not
    a change a teacher can see or needs explained, so it is reported as ``equal``.  The rule
    only applies to text tokens: a caller that diffs lines or styled words passes tuples,
    which have no layout of their own.
    """
    matcher = difflib.SequenceMatcher(None, raw_tokens, corrected_tokens, autojunk=False)
    opcodes: list[DiffOpcode] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        layout_only = tag != "equal" and _layout_only(raw_tokens[i1:i2], corrected_tokens[j1:j2])
        opcodes.append(("equal" if layout_only else tag, i1, i2, j1, j2))
    return opcodes


def _layout_only(old: Sequence[object], new: Sequence[object]) -> bool:
    """Whether a difference is whitespace alone (only text tokens have layout)."""
    if not all(isinstance(token, str) for token in (*old, *new)):
        return False
    return not "".join(cast("Sequence[str]", (*old, *new))).strip()


def token_offsets(tokens: Sequence[str]) -> list[int]:
    """Return the character offset of every token, plus the end of the last one (index -1)."""
    offsets: list[int] = []
    position = 0
    for token in tokens:
        offsets.append(position)
        position += len(token)
    offsets.append(position)
    return offsets


def similarity(a: str, b: str) -> float:
    """Return how similar two strings are, 0.0 to 1.0 (``difflib``'s ratio).

    Not a diff: the fuzzy anchor of a quote and the "is this still the same sentence" check
    of an answer ask for one number, and they ask for it here so ``difflib`` stays in one
    module.
    """
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


class Similarity:
    """Similarity of many candidates to one target, with ``difflib``'s cheap pre-checks.

    The fuzzy anchor compares a quote against every run of sentence-like pieces of a text;
    ``real_quick_ratio`` and ``quick_ratio`` skip the expensive comparison of the candidates
    that cannot reach the threshold anyway.
    """

    def __init__(self, target: str) -> None:
        self._matcher = difflib.SequenceMatcher(None, autojunk=False)
        self._matcher.set_seq2(target)

    def ratio(self, candidate: str, *, at_least: float = 0.0) -> float:
        """Return the ratio of *candidate* to the target; ``0.0`` below *at_least*."""
        self._matcher.set_seq1(candidate)
        if self._matcher.real_quick_ratio() < at_least or self._matcher.quick_ratio() < at_least:
            return 0.0
        return self._matcher.ratio()


@dataclass(frozen=True)
class Hunk:
    """One contiguous difference of the word diff, with its span in both texts."""

    raw_start: int
    raw_end: int
    """Half-open span in the raw text; ``raw_start == raw_end`` for an insertion."""
    corrected_start: int
    corrected_end: int
    """Half-open span in the corrected text; equal ends for a deletion."""
    original: str
    """Text of the hunk in the raw text (empty for an insertion)."""
    corrected: str
    """Text of the hunk in the corrected text (empty for a deletion)."""
    kind: HunkKind
    shift_after: int | None = None
    """How far the corrected text runs ahead of the raw text right after this hunk.

    The layout whitespace trimmed off the two sides of a hunk is equal text, and the two sides
    can lose different amounts of it, so this is not always ``corrected_end - raw_end``.  ``None``
    (a hunk built by hand) means that difference.
    """

    @property
    def offset_after(self) -> int:
        """Return how far the corrected text runs ahead of the raw text after this hunk."""
        if self.shift_after is not None:
            return self.shift_after
        return self.corrected_end - self.raw_end

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


def word_diff(raw: str, corrected: str) -> tuple[Hunk, ...]:
    """Return the hunks of the word diff from *raw* to *corrected*, in text order.

    Both texts must be plain (see ``TextFormatter.plain_text``): the offsets are character
    offsets into exactly these strings, which is what a stored review is anchored on.
    """
    raw_tokens = tokenize(raw)
    corrected_tokens = tokenize(corrected)
    raw_at = token_offsets(raw_tokens)
    corrected_at = token_offsets(corrected_tokens)
    hunks: list[Hunk] = []
    for tag, i1, i2, j1, j2 in diff_opcodes(raw_tokens, corrected_tokens):
        if tag == "equal":
            continue
        old, new, raw_trim, corrected_trim = _trim_layout(
            raw_tokens[i1:i2], corrected_tokens[j1:j2]
        )
        if old.strip() and new.strip():
            kind: HunkKind = "replace"
        elif old.strip():
            kind = "delete"
        else:
            kind = "insert"
        hunks.append(
            Hunk(
                raw_start=raw_at[i1] + raw_trim[0],
                raw_end=raw_at[i2] - raw_trim[1],
                corrected_start=corrected_at[j1] + corrected_trim[0],
                corrected_end=corrected_at[j2] - corrected_trim[1],
                original=old,
                corrected=new,
                kind=kind,
                shift_after=corrected_at[j2] - raw_at[i2],
            )
        )
    return tuple(hunks)


def _trim_layout(
    old: Sequence[str], new: Sequence[str]
) -> tuple[str, str, tuple[int, int], tuple[int, int]]:
    """Return both sides of a hunk without the layout whitespace on their edges.

    A hunk can start in the gap between two sentences (``"\\n" "Weil"`` becomes ``" " "Da"``):
    the whitespace is layout, the change is the first word of the next sentence, and which
    sentence the hunk belongs to must not depend on it.  A difference in whitespace alone is
    layout too (``diff_opcodes`` reports it as equal), so trimming each side on its own never
    loses a change.  The counts are the characters cut off the front and the back of each side,
    so the spans stay exact.
    """
    old_tokens, new_tokens = list(old), list(new)
    raw_trim = [0, 0]
    corrected_trim = [0, 0]
    while old_tokens and old_tokens[0].isspace():
        raw_trim[0] += len(old_tokens.pop(0))
    while new_tokens and new_tokens[0].isspace():
        corrected_trim[0] += len(new_tokens.pop(0))
    while old_tokens and old_tokens[-1].isspace():
        raw_trim[1] += len(old_tokens.pop())
    while new_tokens and new_tokens[-1].isspace():
        corrected_trim[1] += len(new_tokens.pop())
    return (
        "".join(old_tokens),
        "".join(new_tokens),
        (raw_trim[0], raw_trim[1]),
        (
            corrected_trim[0],
            corrected_trim[1],
        ),
    )
