"""The numbers a report is made of: one summary per model, temperature, language and band.

The renderer in :mod:`.report` prints them, and gate 2 (:mod:`.gate_two`) and the grouping of a
screen (:mod:`.grouping`) read them.  They are computed here, below the renderer, so that neither
of those has to import it: the numbers are the same whichever module asks for them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from . import scoring
from .gleu import SOURCE_PREFIX, add, from_scores, gleu
from .languages import ordered
from .quality import DEFAULT_BETA, EditScore
from .scoring import ScoredItem

BAND_ORDER: tuple[str, ...] = ("short", "page", "pages")
"""The bands of the recipe, in the order of the table; a band it does not know comes last."""

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
    f_beta: float | None
    """The weighted mean of the two; ``None`` when nothing was scored."""
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
    added_marks: int
    rows: int
    explained: int
    needed: int
    orphans_per_text: float
    seconds_per_text: float


def summarise(items: Sequence[ScoredItem], beta: float = DEFAULT_BETA) -> list[ModelSummary]:
    """Return one summary per model, temperature, language and band that has scored texts.

    Every language gets a row over all its texts (band ``""``) and one row per band its texts
    were drawn for.  The items may come from a local cache (:class:`~.results.ResultRow`) or from
    a committed run (:class:`~.run_format.StoredItem`); both satisfy
    :class:`~.scoring.ScoredItem`, so the table is the same from either source.

    Args:
        items: The (already re-scored) texts.
        beta: Weight of the F-score; precision counts twice as much at ``0.5``.
    """
    summaries: list[ModelSummary] = []
    for (model, temperature, mode), group in scoring.by_configuration(items):
        for language in ordered({row.language for row in group}):
            part = [row for row in group if row.language == language]
            errors = [row for row in part if row.kind == "errors"]
            if not errors:
                continue
            correct = [row for row in part if row.kind == "correct"]
            for band in ("", *_bands(errors)):
                rows = errors if not band else [row for row in errors if row.band == band]
                summaries.append(
                    _summary(
                        model,
                        temperature,
                        mode,
                        language,
                        band,
                        rows,
                        correct if not band else [],
                        beta,
                    )
                )
    return summaries


def _bands(rows: Sequence[ScoredItem]) -> list[str]:
    """Return the bands the texts of *rows* were drawn for, in the order of the table."""
    present = {row.band for row in rows if row.band}
    return [band for band in BAND_ORDER if band in present] + sorted(present - set(BAND_ORDER))


def _summary(
    model: str,
    temperature: float,
    mode: str,
    language: str,
    band: str,
    rows: Sequence[ScoredItem],
    correct: Sequence[ScoredItem],
    beta: float,
) -> ModelSummary:
    """Return the summary of one group of texts, the correct ones of the language included."""
    score = scoring.pooled(rows)
    precision, recall = scoring.precision_recall(score)
    model_gleu, source_gleu = _gleu_pair(rows)
    # Reliability (failed, fallback, marks, texts, time) is about every text the model was given,
    # the ``correct`` ones of the language included; the scores above are about the error texts.
    sent = [*rows, *correct]
    tp_h1, fn_h1, tp_h2, fn_h2 = (sum(_count(row, key) for row in rows) for key in _HALF_KEYS)
    return ModelSummary(
        model=model,
        temperature=temperature,
        mode=mode,
        language=language,
        band=band,
        score=score,
        precision=precision,
        recall=recall,
        f_beta=scoring.f_beta(score, beta),
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
        added_marks=sum(row.marks_added for row in sent),
        rows=len(sent),
        explained=sum(row.explained for row in rows),
        needed=sum(row.needed for row in rows),
        orphans_per_text=sum(row.orphans for row in rows) / len(rows),
        seconds_per_text=sum(row.seconds for row in sent) / len(sent),
    )


def _count(row: ScoredItem, key: str) -> int:
    """Return one stored count of *row*, or 0 when the row was written before it was stored."""
    return int(row.scores.get(key, 0))


def _gleu_pair(rows: Sequence[ScoredItem]) -> tuple[float | None, float | None]:
    """Return the GLEU of the model and of the source over *rows*, from the summed counts.

    ``None`` for both when a row carries no counts, as a row written before they were stored
    does: a GLEU over some of the texts would not be the language's GLEU.
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


def _cut_off(row: ScoredItem) -> bool:
    """Whether one of the requests of *row* ended because the window cut the answer off."""
    performance = row.performance
    return performance is not None and any(
        request.done_reason == "length" for request in performance.requests
    )


def share_of(part: int, whole: int) -> float | None:
    """Return ``part / whole``, or ``None`` when there is nothing to divide."""
    return part / whole if whole else None
