"""What a reference correction leaves alone, measured against a model's answer.

The edit metrics themselves — the changes of a word diff, their agreement with a reference and the
explanations that go with them — are the app's, in
:mod:`teachers_teammate.domain.proofreading.edit_agreement`.  What stays here is what only a
corpus with a reference can ask:

* **Left alone** — how many already-correct sentences came back unchanged, which catches a model
  that rewrites text nobody asked it to touch.
* **Untouched spans** — which sentences no change of the reference touches at all.

Pure: no I/O, no provider and no Qt imports.
"""

from __future__ import annotations

from collections.abc import Sequence

from teachers_teammate.domain.proofreading.edit_agreement import ordered_edits, word_positions
from teachers_teammate.domain.proofreading.sentences import Span
from teachers_teammate.domain.proofreading.word_diff import tokenize


def unchanged_sentences(sentences: tuple[str, ...] | list[str], output: str) -> int:
    """Return how many of the already-correct *sentences* came back unchanged in *output*.

    Args:
        sentences: The sentences that were correct before proofreading.
        output: The text the model produced.

    Returns:
        How many of them still appear in *output*, word for word.
    """
    return sum(1 for sentence in sentences if sentence in output)


def untouched_spans(source: str, other: str, spans: Sequence[Span]) -> tuple[bool, ...]:
    """Return, for every token span of *source*, whether the word diff to *other* leaves it alone.

    The rule is deliberately conservative, because it decides which sentences count as "the
    reference left this one alone": a sentence only counts when nothing could have changed it.  A
    replacement or a deletion touches a span when they share a word.  An insertion touches a span
    when it lies inside it **or at either edge** — the opposite of
    :func:`~teachers_teammate.domain.proofreading.edit_agreement.spans_touch`, because a word
    inserted between two sentences may belong to either of them.

    Args:
        source: The text the spans are positions in.
        other: The text to compare with, a reference correction or a model's answer.
        spans: ``[start, end)`` token indices into ``tokenize(source)``, whitespace tokens
            included, the positions :func:`ordered_edits` and the manifest's edits use.

    Returns:
        One flag per span, in the order given: ``True`` when no change of the diff touches it.
    """
    positions = word_positions(tokenize(source))
    changes = [(positions[start], positions[end]) for start, end, _ in ordered_edits(source, other)]
    return tuple(
        not any(
            _touches_or_borders((positions[start], positions[end]), change) for change in changes
        )
        for start, end in spans
    )


def _touches_or_borders(span: Span, change: Span) -> bool:
    """Return whether *change* shares words with *span*, or is an insertion at its edge."""
    start, end = span
    change_start, change_end = change
    if change_start == change_end:
        return start <= change_start <= end
    return max(start, change_start) < min(end, change_end)
