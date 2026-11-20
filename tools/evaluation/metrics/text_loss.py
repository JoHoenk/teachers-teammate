"""How much of a text an answer keeps, and how much it adds, against the reference.

A model can silently drop a sentence or a whole paragraph: the answer is well-formed, the
exchange sees no added framing, and the row is stored as if the teacher had received the whole
text.  The numbers here catch that, and they are pure, so the run and the report compute them the
same way.

Two rules, because one alone misses a case:

* **The length ratio** compares the words of the answer with the words of the reference (the
  source for a ``correct`` text).  It catches a text that came back much shorter or much longer,
  and it is the only rule the app can use, because the app has no reference.
* **The unmatched run** looks for a stretch of words the model deleted or inserted that no
  reference change overlaps.  It catches a dropped sentence in a text that was otherwise
  corrected heavily, where the ratio stays near 1.

A run the reference makes too is not a loss: the reference is the yardstick, so a text whose
reference cuts 45 words is a text, not a defect.
"""

from __future__ import annotations

from collections.abc import Mapping

from teachers_teammate.domain.proofreading.word_diff import tokenize

from ..core.text import word_count
from .quality import ordered_edits, spans_touch, word_positions

LOST_RATIO = 0.80
"""Below this share of the reference's words an answer has lost text."""

ADDED_RATIO = 1.25
"""Above this share of the reference's words an answer has added text."""

RUN_WORDS = 20
"""How many words a deleted or inserted run needs to count on its own, ratio or not."""

WORDS_OUT = "words_out"
"""The words of the model's answer."""

WORDS_REF = "words_ref"
"""The words of the reference, or of the source for a ``correct`` text."""

CUT_RUN = "cut_run"
"""The longest deleted run of the answer that no reference change overlaps, in words and marks."""

ADDED_RUN = "added_run"
"""The longest inserted run of the answer that no reference change overlaps, in words and marks."""

KEYS: tuple[str, ...] = (WORDS_OUT, WORDS_REF, CUT_RUN, ADDED_RUN)
"""The numbers :func:`measure` stores, in the order a reader wants them."""

LEFT_OUT = "left_out"
"""How many stretches the model left out and the app put back, stored only when there were any.

The stored output has them again, so the numbers above see a whole text; the model's own answer
did not, so a row with a count here is lost text all the same (:func:`is_lost`)."""


def measure(source: str, corrected: str, reference: str) -> dict[str, int]:
    """Return the four numbers of one answer: its words, the reference's, and its longest runs.

    Args:
        source: The text before proofreading, in plain text.
        corrected: What the model produced, without the marks it added.
        reference: The reference correction; empty for a ``correct`` text, whose yardstick is
            then the source itself.

    Returns:
        ``words_out`` and ``words_ref``, counts of words and marks as
        :func:`~..core.text.word_count` counts them, and ``cut_run`` and ``added_run``, the longest
        unmatched runs in words and marks.
    """
    yardstick = reference if reference.strip() else source
    positions = word_positions(tokenize(source))
    reference_spans = [
        (positions[start], positions[end]) for start, end, _ in ordered_edits(source, yardstick)
    ]
    cut_run = added_run = 0
    for start, end, replacement in ordered_edits(source, corrected):
        span = (positions[start], positions[end])
        if any(spans_touch(span, other) for other in reference_spans):
            continue
        removed = positions[end] - positions[start]
        inserted = sum(1 for token in replacement if token.strip())
        cut_run = max(cut_run, removed - inserted)
        added_run = max(added_run, inserted - removed)
    return {
        WORDS_OUT: word_count(corrected),
        WORDS_REF: word_count(yardstick),
        CUT_RUN: cut_run,
        ADDED_RUN: added_run,
    }


def is_lost(scores: Mapping[str, float]) -> bool | None:
    """Whether an answer lost text, or ``None`` when the row was written before it was measured.

    A row of an older run carries none of :data:`KEYS`; it is not a row that lost nothing, so the
    caller can say "not measured" instead of counting it as clean.  A row whose answer the app
    had to repair lost text whatever its stored output measures.
    """
    if scores.get(LEFT_OUT, 0) > 0:
        return True
    if not all(key in scores for key in KEYS):
        return None
    return scores[WORDS_OUT] < LOST_RATIO * scores[WORDS_REF] or scores[CUT_RUN] >= RUN_WORDS


def is_added(scores: Mapping[str, float]) -> bool | None:
    """Whether an answer added text, or ``None`` when the row was written before it was measured."""
    if not all(key in scores for key in KEYS):
        return None
    return scores[WORDS_OUT] > ADDED_RATIO * scores[WORDS_REF] or scores[ADDED_RUN] >= RUN_WORDS


__all__ = [
    "ADDED_RATIO",
    "ADDED_RUN",
    "CUT_RUN",
    "KEYS",
    "LEFT_OUT",
    "LOST_RATIO",
    "RUN_WORDS",
    "WORDS_OUT",
    "WORDS_REF",
    "is_added",
    "is_lost",
    "measure",
]
