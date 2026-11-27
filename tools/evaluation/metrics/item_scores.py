"""The numbers of one item: GLEU, recall by half, left alone, coverage, located and lost text.

One pure function computes everything a run stores about a text, and the report recomputes it from
the stored texts through the same function, so the run and the report cannot drift apart.  The
numbers are counts, not shares: a language's figures are pooled over its texts, and a share
computed per text and then averaged would not be the same number.

``located`` counts the reference's changes the model touched at all, ``ref_edits`` how many the
reference makes.  The two give a second recall — "found the spot" — next to the exact one: a model
that noticed an error and fixed it differently is a miss in the F-score, and this is what tells
that apart from an error it did not see.

The unit of the halves is the text, not the pieces a long text was sent in, so text mode, review
mode and every budget compare alike.

The words of the answer against the reference's are measured for every text, a ``correct`` one
included: a model that drops a sentence is a defect of the answer, not of the scoring, and a
``correct`` text has no other number to carry it.
"""

from __future__ import annotations

from collections.abc import Sequence
from collections.abc import Set as AbstractSet

from teachers_teammate.domain.proofreading.edit_agreement import (
    EditMatch,
    match_edits,
    word_positions,
)
from teachers_teammate.domain.proofreading.sentences import Span
from teachers_teammate.domain.proofreading.word_diff import Edit, tokenize

from . import text_loss
from .gleu import SOURCE_PREFIX, gleu_counts, to_scores
from .quality import untouched_spans


def item_scores(
    source: str,
    corrected: str,
    reference: str,
    *,
    sentence_spans: Sequence[Span] = (),
    unchanged_sentences: Sequence[int] = (),
    made: AbstractSet[Edit] | None = None,
    expected: AbstractSet[Edit] | None = None,
) -> dict[str, int]:
    """Return every number a run stores about one item, as integer keys.

    Args:
        source: The text before proofreading, in plain text.
        corrected: What the model produced, without the marks it added.
        reference: The reference correction; empty for a ``correct`` text, whose yardstick is
            then the source itself.
        sentence_spans: The token span of every sentence of the corpus's segmentation, whitespace
            tokens included; empty when the set has none.
        unchanged_sentences: Indices into *sentence_spans* of the sentences the reference leaves
            alone.
        made: The changes of *source* to *corrected*, when the caller has already computed them.
        expected: The changes of *source* to *reference*, when the caller has already computed them.

    Returns:
        The words of the answer against the reference's, the GLEU counts of the model and of the
        source, the recall by half, the changes the model located, and — when the text has spans —
        left alone and coverage.  A ``correct`` text gets the words only, because it has nothing
        else to score.
    """
    scores = text_loss.measure(source, corrected, reference)
    if not reference.strip():
        return scores
    scores |= to_scores(gleu_counts(source, corrected, reference))
    scores |= to_scores(
        gleu_counts(source, source, reference), SOURCE_PREFIX, reference_length=False
    )
    matches = match_edits(source, corrected, reference, made=made, expected=expected)
    scores |= _recall_by_half(source, matches)
    scores |= _located(matches)
    if sentence_spans:
        scores |= _alone_and_cover(
            source, corrected, reference, sentence_spans, unchanged_sentences
        )
    return scores


def _recall_by_half(source: str, matches: Sequence[EditMatch]) -> dict[str, int]:
    """Return the exact matches and the misses of each half of the text.

    A reference change belongs to the first half when its start lies before the middle word of
    the text, else to the second.  A model that stops early or drifts shows it here, which a
    single recall figure hides.
    """
    positions = word_positions(tokenize(source))
    middle = positions[-1] / 2
    scores = {"tp_h1": 0, "fn_h1": 0, "tp_h2": 0, "fn_h2": 0}
    for match in matches:
        half = 1 if positions[match.edit[0]] < middle else 2
        scores[f"tp_h{half}"] += 1 if match.exact else 0
        scores[f"fn_h{half}"] += 0 if match.exact else 1
    return scores


def _located(matches: Sequence[EditMatch]) -> dict[str, int]:
    """Return how many of the reference's changes the model touched, and how many there are.

    A change counts as located when the model changed at least some of the same words, with the
    same replacement or another one.  It is the second recall of the report: the gap to the exact
    count is "noticed it, fixed it differently".
    """
    return {
        "located": sum(1 for match in matches if match.located),
        "ref_edits": len(matches),
    }


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
