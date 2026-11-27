"""What a report computes from counts: one F-beta, pooled over the texts of a group.

:class:`~teachers_teammate.domain.proofreading.edit_agreement.EditScore` knows the metrics of one text; a report needs them over many
texts, and it needs one answer to "nothing was scored here".  This module is that arithmetic, pure
and shared by every table, so no two tables can drift apart.  It also names what a report needs from
one scored text (:class:`ItemFacts`), and what a table that shows the text itself needs
(:class:`TextItem`).

A share whose denominator is zero is ``None`` and not ``0.0``: a language whose reference has no
edit and whose model made none has no score, and counting it as 0.00 would drag a mean down as if
the model had failed.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from teachers_teammate.domain.proofreading.edit_agreement import DEFAULT_BETA, EditScore

from ..core.axes import AXIS_BY_NAME, axis_label, compare_key, compare_key_sha256, varying_axes
from ..core.modes import Exchange, setup_label, steps_of
from ..core.performance import Performance
from ..core.records import Protocol as RunProtocol
from ..core.records import RunHeader, TestRecord


class CountsLike(Protocol):
    """What :func:`pooled` needs from one scored text: its four counts."""

    @property
    def true_positives(self) -> int:
        """Changes the text made that the reference makes too, or that the key accepts."""

    @property
    def false_positives(self) -> int:
        """Changes the text made that neither the reference nor the key accepts."""

    @property
    def found(self) -> int:
        """Reference changes the text found."""

    @property
    def false_negatives(self) -> int:
        """Reference changes the text did not find."""


@runtime_checkable
class ItemFacts(Protocol):
    """What a report needs from one scored text."""

    @property
    def header(self) -> RunHeader:
        """The run's header: what it was measured under, which is what a row is grouped by."""

    @property
    def model(self) -> str:
        """The model tag the text was sent to."""

    @property
    def temperature(self) -> float:
        """The temperature it was asked at."""

    @property
    def mode(self) -> str:
        """How it was asked, as :func:`mode_label` writes it."""

    @property
    def language(self) -> str:
        """The language code of the text."""

    @property
    def item(self) -> str:
        """The record's id of the text (``de-01``)."""

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
        """Changes the text made that the reference makes too, or that the key accepts."""

    @property
    def false_positives(self) -> int:
        """Changes the text made that neither the reference nor the key accepts."""

    @property
    def found(self) -> int:
        """Reference changes the text found."""

    @property
    def false_negatives(self) -> int:
        """Reference changes the text did not find."""

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
    def explanations_known(self) -> bool:
        """Whether the review could be read, so whether the explanation counts mean anything."""

    @property
    def orphans(self) -> int:
        """Notes that explain no change."""

    @property
    def category_checked(self) -> int:
        """Changes whose error category the reference labels."""

    @property
    def category_agreed(self) -> int:
        """Changes whose error category the model named as the reference does."""

    @property
    def unclear(self) -> int:
        """Expressions the model said a reader cannot follow."""

    @property
    def accepted(self) -> int:
        """Changes the key accepts although the reference does not make them."""

    @property
    def seconds(self) -> float:
        """Seconds the text took."""

    @property
    def words(self) -> int:
        """Words and marks of the source, as the manifest counts them."""

    @property
    def performance(self) -> Performance | None:
        """What the text cost on the server, or ``None`` when nothing was measured."""


@runtime_checkable
class TextItem(ItemFacts, Protocol):
    """A scored text whose own text is at hand: what a table that shows the text needs."""

    @property
    def output(self) -> str:
        """The text the model produced, as the scorer read it: without the marks it added."""

    @property
    def record(self) -> TestRecord:
        """The record the answer was asked about: its source, its reference and its labels."""


def mode_of(exchange: str, steps: Sequence[str]) -> str:
    """Return the readable label of an exchange and its steps.

    The label is :func:`~..core.modes.setup_label`'s, so a table and a run file name a run the same
    way.  The steps are in the compare key, so two runs of one exchange with different steps are
    two rows and not one.
    """
    return setup_label((Exchange(exchange), steps_of(steps)))


def mode_label(protocol: RunProtocol) -> str:
    """Return the readable label of a run's mode, from its protocol (see :func:`mode_of`)."""
    return setup_label((protocol.exchange, protocol.steps))


def pooled(items: Iterable[CountsLike]) -> EditScore:
    """Sum the counts of the items into one :class:`~teachers_teammate.domain.proofreading.edit_agreement.EditScore`."""
    true_positives = false_positives = found = false_negatives = 0
    for item in items:
        true_positives += item.true_positives
        false_positives += item.false_positives
        found += item.found
        false_negatives += item.false_negatives
    return EditScore(
        true_positives=true_positives,
        false_positives=false_positives,
        found=found,
        false_negatives=false_negatives,
    )


def f_beta(score: EditScore, beta: float = DEFAULT_BETA) -> float | None:
    """Return ``EditScore.f_beta``, or ``None`` when nothing was scored."""
    if not score.true_positives + score.false_positives + score.found + score.false_negatives:
        return None
    return score.f_beta(beta)


def precision_recall(score: EditScore) -> tuple[float | None, float | None]:
    """Return ``(precision, recall)``, ``None`` for the share whose denominator is 0."""
    made = score.true_positives + score.false_positives
    expected = score.found + score.false_negatives
    return (
        score.true_positives / made if made else None,
        score.found / expected if expected else None,
    )


DECIDING_LANGUAGES: tuple[str, ...] = ("de", "en")
"""The languages whose mean decides; French is reported only."""


def mean_f_beta(by_language: Mapping[str, EditScore], beta: float = DEFAULT_BETA) -> float | None:
    """Return the mean over the languages whose f_beta is not ``None``; ``None`` when none is."""
    scores = [value for score in by_language.values() if (value := f_beta(score, beta)) is not None]
    return sum(scores) / len(scores) if scores else None


def mean_gleu(by_language: Mapping[str, float | None]) -> float | None:
    """Return the mean GLEU over the languages that have one; ``None`` when none has.

    A language whose texts carry no GLEU counts is left out, as a language without a score is left
    out of :func:`mean_f_beta`: counting it as 0.00 would drag the mean down.
    """
    values = [value for value in by_language.values() if value is not None]
    return sum(values) / len(values) if values else None


_COLUMN_AXES: frozenset[str] = frozenset({"model", "temperature", "exchange", "steps"})
"""The axes a table prints in a column of their own, so a row's label does not repeat them."""


def _default_of(name: str) -> Any:
    """Return the value an axis has when the run left it at its default, for a label."""
    axis = AXIS_BY_NAME.get(name)
    return None if axis is None else axis.default


@dataclass(frozen=True)
class Configuration:
    """One row of a report: the texts of one compare key, and how to name them."""

    key: str
    """The hash of the compare key, which is what makes two rows two rows."""

    model: str
    temperature: float
    mode: str
    label: str
    """The model plus the axes that vary within the selection, for a reader of the table."""


def configuration_of(header: RunHeader, varying: Sequence[str]) -> Configuration:
    """Return the row a run's texts belong to, labelled by the axes that vary in the selection."""
    values = compare_key(header)
    named = [
        f"{name}={axis_label(name, values.get(name, _default_of(name)))}"
        for name in varying
        if name not in _COLUMN_AXES
    ]
    label = header.protocol.model + (f" ({', '.join(named)})" if named else "")
    return Configuration(
        key=compare_key_sha256(header),
        model=header.protocol.model,
        temperature=header.protocol.temperature,
        mode=mode_label(header.protocol),
        label=label,
    )


def by_configuration(
    items: Iterable[ItemFacts],
) -> Iterator[tuple[Configuration, list[ItemFacts]]]:
    """Group *items* by compare key, keeping the groups sorted by their label.

    A row is one compare key, so two prompt variants, two test sets or two samplings are two rows
    and never one average.  The label names the model and the axes that vary within the selection,
    so a reader can tell the rows apart without opening a run file.
    """
    rows = list(items)
    varying = varying_axes([row.header for row in rows])
    groups: dict[str, list[ItemFacts]] = {}
    for row in rows:
        groups.setdefault(compare_key_sha256(row.header), []).append(row)
    found = [(configuration_of(group[0].header, varying), group) for group in groups.values()]
    yield from sorted(found, key=lambda pair: (pair[0].label, pair[0].temperature, pair[0].mode))


__all__ = [
    "DECIDING_LANGUAGES",
    "Configuration",
    "CountsLike",
    "ItemFacts",
    "TextItem",
    "by_configuration",
    "configuration_of",
    "f_beta",
    "mean_f_beta",
    "mean_gleu",
    "mode_label",
    "mode_of",
    "pooled",
    "precision_recall",
]
