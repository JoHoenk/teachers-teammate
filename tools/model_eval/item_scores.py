"""The numbers of one item: GLEU, recall by half, left alone and coverage.

One pure function computes everything a run stores about a text, and the report recomputes it from
the stored texts through the same function, so the run and the report cannot drift apart.  The
numbers are counts, not shares: a language's figures are pooled over its texts, and a share
computed per text and then averaged would not be the same number.

The unit of the halves is the text, not the pieces a long text was sent in, so text mode, review
mode and every budget compare alike.
"""

from __future__ import annotations

from collections.abc import Sequence

from teachers_teammate.domain.correction_review import tokenize

from .gleu import SOURCE_PREFIX, gleu_counts, to_scores
from .quality import (
    Span,
    match_edits,
    untouched_spans,
    word_positions,
)


def item_scores(
    source: str,
    corrected: str,
    reference: str,
    *,
    sentence_spans: Sequence[Span] = (),
    unchanged_sentences: Sequence[int] = (),
) -> dict[str, int]:
    """Return every number a run stores about one item, as integer keys.

    Args:
        source: The text before proofreading, in plain text.
        corrected: What the model produced, without the marks it added.
        reference: The reference correction; empty for a ``correct`` text, which has no numbers.
        sentence_spans: The token span of every sentence of the corpus's segmentation, whitespace
            tokens included; empty when the set has none.
        unchanged_sentences: Indices into *sentence_spans* of the sentences the reference leaves
            alone.

    Returns:
        The GLEU counts of the model and of the source, the recall by half, and — when the text
        has spans — left alone and coverage.  Empty for a ``correct`` text.
    """
    if not reference.strip():
        return {}
    scores = to_scores(gleu_counts(source, corrected, reference))
    scores |= to_scores(
        gleu_counts(source, source, reference), SOURCE_PREFIX, reference_length=False
    )
    scores |= _recall_by_half(source, corrected, reference)
    if sentence_spans:
        scores |= _alone_and_cover(
            source, corrected, reference, sentence_spans, unchanged_sentences
        )
    return scores


def _recall_by_half(source: str, corrected: str, reference: str) -> dict[str, int]:
    """Return the exact matches and the misses of each half of the text.

    A reference change belongs to the first half when its start lies before the middle word of
    the text, else to the second.  A model that stops early or drifts shows it here, which a
    single recall figure hides.
    """
    positions = word_positions(tokenize(source))
    middle = positions[-1] / 2
    scores = {"tp_h1": 0, "fn_h1": 0, "tp_h2": 0, "fn_h2": 0}
    for match in match_edits(source, corrected, reference):
        half = 1 if positions[match.edit[0]] < middle else 2
        scores[f"tp_h{half}"] += 1 if match.exact else 0
        scores[f"fn_h{half}"] += 0 if match.exact else 1
    return scores


def _alone_and_cover(
    source: str,
    corrected: str,
    reference: str,
    sentence_spans: Sequence[Span],
    unchanged_sentences: Sequence[int],
) -> dict[str, int]:
    """Return how many sentences were left alone and how many the model looked at.

    Left alone is measured on the sentences the reference leaves alone, coverage on the sentences
    some reference change touches; both use the same rule on the model's side, so a sentence
    counts as left alone exactly when no change of the model could belong to it.  Coverage is
    "looked at", not "fixed": a different replacement still covers the sentence.
    """
    reference_alone = untouched_spans(source, reference, sentence_spans)
    model_alone = untouched_spans(source, corrected, sentence_spans)
    return {
        "alone": sum(1 for index in unchanged_sentences if model_alone[index]),
        "alone_of": len(unchanged_sentences),
        "cover": sum(
            1 for index, alone in enumerate(reference_alone) if not alone and not model_alone[index]
        ),
        "cover_of": sum(1 for alone in reference_alone if not alone),
    }
