"""The numbers a report is made of: one summary per configuration, language and band.

The tables in :mod:`.tables` print them, and :mod:`.groups` and :mod:`.stats` read them.  They are
computed here, below the tables, so that neither of those has to import a renderer: the numbers are
the same whichever module asks for them.

A summary is about the **error** texts of a language: the ``correct`` ones have no edits to score,
and pooling them in would make a language's precision depend on how many correct texts the recipe
drew.  Reliability — failures, fallbacks, marks, time — is about every text the model was given,
the ``correct`` ones included, because that is what the run paid for.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from teachers_teammate.domain.proofreading.edit_agreement import DEFAULT_BETA, EditScore

from ..core.constants import TextKind
from ..core.languages import ordered
from ..metrics import text_loss
from ..metrics.gleu import SOURCE_PREFIX, add, from_scores, gleu
from . import pooled
from .pooled import ItemFacts
from .score import LENIENT_TRUE_POSITIVES

BAND_ORDER: tuple[str, ...] = ("short", "page", "pages")
"""The bands of the recipe, in the order of the table; a band it does not know comes last."""

WORDS_PER_PAGE = 300
"""The page the per-page figures are counted for: a page of a student's text, not a band."""

_HALF_KEYS: tuple[str, ...] = ("tp_h1", "fn_h1", "tp_h2", "fn_h2")
"""The exact matches and the misses of each half of a text."""


@dataclass(frozen=True)
class ModelSummary:
    """The per-model, per-language and per-band part of the table."""

    model: str
    temperature: float
    mode: str
    language: str
    band: str
    """The band of the texts, or ``""`` for the row over all texts of the language."""
    score: EditScore
    """The pooled counts the precision, the recall and the F-score come from."""
    precision: float | None
    """Share of the model's changes the reference agrees with; ``None`` when it made none."""
    recall: float | None
    """Share of the reference's changes the model found; ``None`` when there are none."""
    located: int
    """Reference changes the model touched at all, with the same or another replacement."""
    ref_edits: int
    """Reference changes of the scored texts."""
    f_beta: float | None
    """The weighted mean of the two; ``None`` when nothing was scored."""
    mean_text_f_beta: float | None
    """The F-score of each text, averaged over the texts; ``None`` when nothing was scored.

    The pooled figures above weight a long text more than a short one, which is what a language's
    score should do; this one weights every text alike, so a few long texts cannot carry a model.
    """
    mean_text_precision: float | None
    """The precision of each text, averaged over the texts that made a change."""
    mean_text_recall: float | None
    """The recall of each text, averaged over the texts whose reference has a change."""
    words: int
    """Words and marks of the scored error texts, as the manifest counts them."""
    texts: int
    """The scored error texts; the ``correct`` ones of the language are not among them."""
    gleu: float | None
    """GLEU of the model's texts, from the summed counts; ``None`` when none was scored."""
    gleu_source: float | None
    """GLEU of the unchanged source, the floor the model has to beat."""
    alone: int
    """Sentences the reference leaves alone that the model leaves alone too."""
    alone_of: int
    """Sentences the reference leaves alone, plus the correct sentences of a ``correct`` text."""
    cover: int
    """Sentences some reference change touches that some model change touches too."""
    cover_of: int
    """Sentences some reference change touches."""
    recall_h1: float | None
    """Share of the reference's changes in the first half of the texts that the model found."""
    recall_h2: float | None
    """The same for the second half, which shows a model that stops early or drifts."""
    failed: int
    fallback: int
    cut_off: int
    """Fallbacks whose answer the window cut off (``done_reason: "length"``)."""
    lost_text: int
    """Texts whose answer kept much less of the text than the reference has."""
    added_text: int
    """Texts whose answer added much more than the reference has."""
    loss_checked: int
    """Texts whose words were measured; 0 for a run written before they were stored."""
    added_marks: int
    rows: int
    explained: int
    """Changes that carry an explanation, over the texts whose review could be read."""
    needed: int
    """Changes that need one, over the same texts."""
    orphans_per_text: float
    seconds_per_text: float
    label: str = ""
    """The model plus the axes that vary within the selection, so two prompt variants are two rows."""
    explanations_unknown: int = 0
    """Texts left out of :attr:`explained` and :attr:`needed`: their review could not be read."""
    lenient_tp: int = 0
    """The model's changes that touch a reference change or are an accepted alternative."""
    located_fixed: int = 0
    """Reference changes fixed exactly, counted over the rows that store ``located`` only.

    :attr:`noticed_gap` subtracts it from ``located``: both must come from the same rows, or a
    report that pools older rows (no ``located``) with newer ones gets a negative gap.
    """

    @property
    def recall_located(self) -> float | None:
        """Share of the reference's changes the model touched at all: "found the spot".

        The second recall of the report.  The gap to :attr:`recall` is what the model noticed and
        fixed differently, which the F-score counts as a miss; ``None`` for a run written before
        the count was stored.
        """
        return share_of(self.located, self.ref_edits)

    def lenient_f_beta(self, beta: float = DEFAULT_BETA) -> float | None:
        """Return the F-score that counts a change at a reference change's place as right.

        Precision counts the model's changes that touch a reference change, recall the reference
        changes the model touched (:attr:`located`).  A check beside the exact F-score, which
        counts a valid alternative the annotator did not write as wrong and as missed; ``None``
        when nothing was scored.
        """
        made = self.score.true_positives + self.score.false_positives
        precision = share_of(self.lenient_tp, made)
        recall = self.recall_located
        if precision is None or recall is None:
            return None
        weighted = beta**2 * precision + recall
        return (1 + beta**2) * precision * recall / weighted if weighted else 0.0

    @property
    def noticed_gap(self) -> float | None:
        """Share of the reference's changes the model noticed but did not fix as the reference did."""
        if not self.ref_edits:
            return None
        return (self.located - self.located_fixed) / self.ref_edits

    @property
    def fixed_per_page(self) -> float | None:
        """Mistakes fixed per page of 300 words, from the pooled counts."""
        return _per_page(self.score.true_positives, self.words)

    @property
    def wrong_per_page(self) -> float | None:
        """Wrong or unneeded changes per page of 300 words, from the pooled counts."""
        return _per_page(self.score.false_positives, self.words)

    @property
    def missed_per_page(self) -> float | None:
        """Mistakes missed per page of 300 words, from the pooled counts."""
        return _per_page(self.score.false_negatives, self.words)


def summarise(items: Sequence[ItemFacts], beta: float = DEFAULT_BETA) -> list[ModelSummary]:
    """Return one summary per configuration, language and band that has scored texts.

    A configuration is one compare key, so two prompt variants or two test sets are two rows and
    never one average.  Every language gets a row over all its texts (band ``""``) and one row per
    band its texts were drawn for.

    Args:
        items: The (already scored) texts.
        beta: Weight of the F-score; precision counts twice as much at ``0.5``.
    """
    summaries: list[ModelSummary] = []
    for configuration, group in pooled.by_configuration(items):
        for language in ordered({row.language for row in group}):
            part = [row for row in group if row.language == language]
            errors = [row for row in part if row.kind == TextKind.ERRORS]
            if not errors:
                continue
            correct = [row for row in part if row.kind == TextKind.CORRECT]
            for band in ("", *_bands(errors)):
                rows = errors if not band else [row for row in errors if row.band == band]
                summaries.append(
                    _summary(
                        configuration,
                        language,
                        band,
                        rows,
                        correct if not band else [],
                        beta,
                    )
                )
    return summaries


def _bands(rows: Sequence[ItemFacts]) -> list[str]:
    """Return the bands the texts of *rows* were drawn for, in the order of the table."""
    present = {row.band for row in rows if row.band}
    return [band for band in BAND_ORDER if band in present] + sorted(present - set(BAND_ORDER))


def _summary(
    configuration: pooled.Configuration,
    language: str,
    band: str,
    rows: Sequence[ItemFacts],
    correct: Sequence[ItemFacts],
    beta: float,
) -> ModelSummary:
    """Return the summary of one group of texts, the correct ones of the language included."""
    score = pooled.pooled(rows)
    precision, recall = pooled.precision_recall(score)
    model_gleu, source_gleu = _gleu_pair(rows)
    # Reliability (failed, fallback, marks, texts, time) is about every text the model was given,
    # the ``correct`` ones of the language included; the scores above are about the error texts.
    sent = [*rows, *correct]
    # A review this tool cannot read leaves its text out of the explanation figures: the number is
    # unknown, and counting it as zero would report a model that explained nothing.
    known = [row for row in rows if row.explanations_known]
    tp_h1, fn_h1, tp_h2, fn_h2 = (sum(_count(row, key) for row in rows) for key in _HALF_KEYS)
    return ModelSummary(
        model=configuration.model,
        temperature=configuration.temperature,
        mode=configuration.mode,
        label=configuration.label,
        language=language,
        band=band,
        score=score,
        precision=precision,
        recall=recall,
        located=sum(_count(row, "located") for row in rows),
        ref_edits=sum(_count(row, "ref_edits") for row in rows),
        f_beta=pooled.f_beta(score, beta),
        mean_text_f_beta=_mean_over_texts(rows, lambda row: _text_f_beta(row, beta)),
        mean_text_precision=_mean_over_texts(rows, _text_precision),
        mean_text_recall=_mean_over_texts(rows, _text_recall),
        words=sum(row.words for row in rows),
        texts=len(rows),
        gleu=model_gleu,
        gleu_source=source_gleu,
        alone=sum(_count(row, "alone") for row in rows) + sum(row.unchanged for row in correct),
        alone_of=sum(_count(row, "alone_of") for row in rows)
        + sum(row.sentences_total for row in correct),
        cover=sum(_count(row, "cover") for row in rows),
        cover_of=sum(_count(row, "cover_of") for row in rows),
        recall_h1=share_of(tp_h1, tp_h1 + fn_h1),
        recall_h2=share_of(tp_h2, tp_h2 + fn_h2),
        failed=sum(row.failed for row in sent),
        fallback=sum(row.fallback for row in sent),
        cut_off=sum(1 for row in sent if row.fallback and _cut_off(row)),
        lost_text=sum(1 for row in sent if text_loss.is_lost(row.scores)),
        added_text=sum(1 for row in sent if text_loss.is_added(row.scores)),
        loss_checked=sum(1 for row in sent if text_loss.is_lost(row.scores) is not None),
        added_marks=sum(row.marks_added for row in sent),
        rows=len(sent),
        explained=sum(row.explained for row in known),
        needed=sum(row.needed for row in known),
        explanations_unknown=len(rows) - len(known),
        orphans_per_text=sum(row.orphans for row in rows) / len(rows),
        seconds_per_text=sum(row.seconds for row in sent) / len(sent),
        located_fixed=sum(row.true_positives for row in rows if "located" in row.scores),
        lenient_tp=sum(_count(row, LENIENT_TRUE_POSITIVES) for row in rows),
    )


def _count(row: ItemFacts, key: str) -> int:
    """Return one stored count of *row*, or 0 when the row was written before it was stored."""
    return int(row.scores.get(key, 0))


def _text_precision(row: ItemFacts) -> float | None:
    """Return one text's precision, or ``None`` when the model made no change in it."""
    made = row.true_positives + row.false_positives
    return row.true_positives / made if made else None


def _text_recall(row: ItemFacts) -> float | None:
    """Return one text's recall, or ``None`` when its reference has no change."""
    expected = row.found + row.false_negatives
    return row.found / expected if expected else None


def _text_f_beta(row: ItemFacts, beta: float) -> float | None:
    """Return one text's F-score, or ``None`` when nothing was scored in it."""
    score = EditScore(
        true_positives=row.true_positives,
        false_positives=row.false_positives,
        found=row.found,
        false_negatives=row.false_negatives,
    )
    return pooled.f_beta(score, beta)


def _mean_over_texts(rows: Sequence[ItemFacts], measure) -> float | None:
    """Return the mean of *measure* over the texts that have a value, or ``None`` when none has.

    A text without a value is skipped rather than counted as 0: a text the model left untouched has
    no precision, and counting it as 0.00 would drag the mean down as if it had failed.
    """
    values = [value for row in rows if (value := measure(row)) is not None]
    return sum(values) / len(values) if values else None


def pooled_per_page(
    parts: Sequence[Sequence[ModelSummary]], count: str, languages: Sequence[str]
) -> float | None:
    """Return one count of the score per page of 300 words, pooled over repeats and languages.

    The one rule of the per-page figures: band rows are skipped, only *languages* count, and the
    counts and words of every repeat are summed.  *count* names a field of ``ModelSummary.score``.
    """
    total = words = 0
    for part in parts:
        for row in part:
            if row.band or row.language not in languages:
                continue
            total += getattr(row.score, count)
            words += row.words
    return _per_page(total, words)


def _per_page(count: int, words: int) -> float | None:
    """Return *count* per page of 300 words, or ``None`` when the texts have no words."""
    return count / words * WORDS_PER_PAGE if words else None


def _gleu_pair(rows: Sequence[ItemFacts]) -> tuple[float | None, float | None]:
    """Return the GLEU of the model and of the source over *rows*, from the summed counts.

    ``None`` for both when a row carries no counts, as a row written before they were stored does:
    a GLEU over some of the texts would not be the language's GLEU.
    """
    model_counts = []
    source_counts = []
    for row in rows:
        try:
            counts = from_scores(row.scores)
            model_counts.append(counts)
            source_counts.append(from_scores(row.scores, SOURCE_PREFIX, ref_len=counts.ref_len))
        except KeyError:
            return None, None
    return gleu(add(model_counts)), gleu(add(source_counts))


def _cut_off(row: ItemFacts) -> bool:
    """Whether one of the requests of *row* ended because the window cut the answer off."""
    performance = row.performance
    return performance is not None and any(
        request.done_reason == "length" for request in performance.requests
    )


def share_of(part: int, whole: int) -> float | None:
    """Return ``part / whole``, or ``None`` when there is nothing to divide."""
    return part / whole if whole else None


__all__ = [
    "BAND_ORDER",
    "WORDS_PER_PAGE",
    "ModelSummary",
    "pooled_per_page",
    "share_of",
    "summarise",
]
