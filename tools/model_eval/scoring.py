"""What the reports compute from counts: one F-beta, pooled over the texts of a group.

The domain's :class:`~tools.model_eval.quality.EditScore` knows the metrics of
one text; a report needs them over many texts, and it needs one answer to "nothing was scored
here".  This module is that arithmetic, pure and shared by the local report and the index, so the
two cannot drift apart.  It also names what a report needs from one scored text
(:class:`ScoredItem`), which is what lets the same table be built from a local cache and from a
committed run.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Protocol, runtime_checkable

from .answer_mode import AnswerMode
from .performance import Performance
from .quality import DEFAULT_BETA, EditScore


class CountsLike(Protocol):
    """What :func:`pooled` needs from one scored text: its three counts."""

    @property
    def true_positives(self) -> int:
        """Changes the text made that the reference makes too."""

    @property
    def false_positives(self) -> int:
        """Changes the text made that the reference does not make."""

    @property
    def false_negatives(self) -> int:
        """Changes the reference makes that the text does not make."""


@runtime_checkable
class ScoredItem(Protocol):
    """What a report needs from one scored text, whether it comes from a cache or a run file."""

    @property
    def model(self) -> str:
        """The model tag the text was sent to."""

    @property
    def temperature(self) -> float:
        """The temperature it was asked at."""

    @property
    def mode(self) -> AnswerMode:
        """How it was asked."""

    @property
    def language(self) -> str:
        """The language code of the text."""

    @property
    def item(self) -> str:
        """The manifest's id of the text (``de-01``)."""

    @property
    def kind(self) -> str:
        """``errors`` (a text with mistakes) or ``correct`` (sentences that must stay untouched)."""

    @property
    def band(self) -> str:
        """The band the recipe drew the text for (``short``, ``page``, ``pages``), or ``""``."""

    @property
    def scores(self) -> Mapping[str, float]:
        """The stored numbers of the text: GLEU, recall by half, left alone and coverage."""

    @property
    def true_positives(self) -> int:
        """Changes the text made that the reference makes too."""

    @property
    def false_positives(self) -> int:
        """Changes the text made that the reference does not make."""

    @property
    def false_negatives(self) -> int:
        """Changes the reference makes that the text does not make."""

    @property
    def unchanged(self) -> int:
        """Already-correct sentences that came back untouched."""

    @property
    def sentences_total(self) -> int:
        """The correct sentences of a ``correct`` text, else 0."""

    @property
    def failed(self) -> bool:
        """The text could not be proofread at all."""

    @property
    def fallback(self) -> bool:
        """The answer fell back to corrections only, so the explanations are missing."""

    @property
    def marks_added(self) -> bool:
        """The model added Markdown marks the source does not have."""

    @property
    def explained(self) -> int:
        """Changes that carry an explanation."""

    @property
    def needed(self) -> int:
        """Changes that need one."""

    @property
    def orphans(self) -> int:
        """Notes that explain no change."""

    @property
    def seconds(self) -> float:
        """Seconds the text took."""

    @property
    def words(self) -> int:
        """Words and marks of the source, as the manifest counts them."""

    @property
    def performance(self) -> Performance | None:
        """What the text cost on the server, or ``None`` when nothing was measured."""


def pooled(items: Iterable[CountsLike]) -> EditScore:
    """Sum tp, fp and fn of the items into one EditScore (the domain's)."""
    true_positives = false_positives = false_negatives = 0
    for item in items:
        true_positives += item.true_positives
        false_positives += item.false_positives
        false_negatives += item.false_negatives
    return EditScore(
        true_positives=true_positives,
        false_positives=false_positives,
        false_negatives=false_negatives,
    )


def f_beta(score: EditScore, beta: float = DEFAULT_BETA) -> float | None:
    """Return ``EditScore.f_beta``, or ``None`` when tp + fp + fn == 0 (nothing was scored).

    A language whose reference has no edit and whose model made none has no score: counting it as
    0.00 would drag the mean down as if the model had failed.
    """
    if not score.true_positives + score.false_positives + score.false_negatives:
        return None
    return score.f_beta(beta)


def precision_recall(score: EditScore) -> tuple[float | None, float | None]:
    """Return ``(precision, recall)``, ``None`` for the share whose denominator is 0."""
    made = score.true_positives + score.false_positives
    expected = score.true_positives + score.false_negatives
    return (
        score.true_positives / made if made else None,
        score.true_positives / expected if expected else None,
    )


DECIDING_LANGUAGES: tuple[str, ...] = ("de", "en")
"""The languages whose mean decides; French is reported only."""


def mean_f_beta(by_language: Mapping[str, EditScore], beta: float = DEFAULT_BETA) -> float | None:
    """Return the mean over the languages whose f_beta is not ``None``; ``None`` when none is."""
    scores = [value for score in by_language.values() if (value := f_beta(score, beta)) is not None]
    return sum(scores) / len(scores) if scores else None


def mean_gleu(by_language: Mapping[str, float | None]) -> float | None:
    """Return the mean GLEU over the languages that have one; ``None`` when none has.

    A language whose texts carry no GLEU counts is left out, as a language without a score is
    left out of :func:`mean_f_beta`: counting it as 0.00 would drag the mean down.
    """
    values = [value for value in by_language.values() if value is not None]
    return sum(values) / len(values) if values else None


def by_configuration(
    items: Iterable[ScoredItem],
) -> Iterator[tuple[tuple[str, float, AnswerMode], list[ScoredItem]]]:
    """Group *items* by ``(model, temperature, mode)``, keeping the groups sorted."""
    groups: dict[tuple[str, float, AnswerMode], list[ScoredItem]] = {}
    for item in items:
        groups.setdefault((item.model, item.temperature, item.mode), []).append(item)
    yield from sorted(groups.items())


__all__ = [
    "DECIDING_LANGUAGES",
    "CountsLike",
    "ScoredItem",
    "by_configuration",
    "f_beta",
    "mean_f_beta",
    "mean_gleu",
    "pooled",
    "precision_recall",
]
