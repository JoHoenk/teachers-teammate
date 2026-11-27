"""The paired bootstrap of two groups, and the rule that turns it into a verdict.

One repeat of a run is noisy, so a difference between two models is only believed when the interval
of a **paired** bootstrap over the texts excludes zero on the right **and** the gap is at least
:data:`MIN_EFFECT`: the first says the gap is not the sampler's, the second that it is worth a
reader's attention.  The pairing is what makes the interval tight: the same texts are drawn for both
models, so a text that is hard for one is hard for the other and the difference is measured within a
text, not across texts.

The draws are seeded, so every machine gets the same numbers from the same files.  This module is
pure arithmetic over counts: it reads no file and knows no model.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import random

from teachers_teammate.domain.proofreading.edit_agreement import EditScore

from ..core.constants import TextKind
from . import groups, pooled, summary

BOOTSTRAP_DRAWS = 2000
"""How many draws the paired bootstrap makes."""

BOOTSTRAP_SEED = 20261111
"""The seed of the bootstrap, so every machine gets the same answer."""

MIN_EFFECT = 0.01
"""The smallest difference worth calling a difference: one point of F0.5."""

TIME_TOLERANCE = 0.05
"""How much slower a model may be per 100 words and still count as not slower."""

Counts = tuple[int, int, int, int, int, int, int, int]
"""One text's counts, pooled over the repeats.

``tp, fp, found, fn, alone, alone_of, cover, cover_of``.
"""

Measure = Callable[[Sequence[Counts]], float | None]
"""A number a comparison reads, from the counts of the drawn texts."""


class Verdict(Enum):
    """What one measure says about the first group against the second."""

    BETTER = "better"
    TIE = "tie"
    WORSE = "worse"


@dataclass(frozen=True)
class Interval:
    """One measure's difference between two groups, over the drawn texts."""

    delta: float
    """The difference measured on every text, not on the draws."""
    low: float
    """The 2.5 % point of the drawn differences."""
    high: float
    """The 97.5 % point of the drawn differences."""
    p_not_better: float
    """The share of draws at or below zero."""
    p_not_worse: float
    """The share of draws at or above zero."""

    def negated(self) -> Interval:
        """Return the same difference the other way round, without a second bootstrap."""
        return Interval(
            delta=-self.delta,
            low=-self.high,
            high=-self.low,
            p_not_better=self.p_not_worse,
            p_not_worse=self.p_not_better,
        )

    def verdict(self, minimum: float = MIN_EFFECT) -> Verdict:
        """Return what the interval says: better, worse, or a tie by the minimum effect."""
        if self.low > 0 and self.delta >= minimum:
            return Verdict.BETTER
        if self.high < 0 and self.delta <= -minimum:
            return Verdict.WORSE
        return Verdict.TIE


def _score_of(counts: Sequence[Counts]) -> EditScore:
    """Return the pooled counts of the drawn texts as one :class:`EditScore`."""
    return EditScore(
        true_positives=sum(found[0] for found in counts),
        false_positives=sum(found[1] for found in counts),
        found=sum(found[2] for found in counts),
        false_negatives=sum(found[3] for found in counts),
    )


def _f_beta_of(counts: Sequence[Counts]) -> float | None:
    """Return the F0.5 of the drawn texts, or ``None`` when nothing was scored."""
    return pooled.f_beta(_score_of(counts))


def _precision_of(counts: Sequence[Counts]) -> float | None:
    """Return the precision of the drawn texts, or ``None`` when no change was made."""
    return pooled.precision_recall(_score_of(counts))[0]


def _recall_of(counts: Sequence[Counts]) -> float | None:
    """Return the recall of the drawn texts, or ``None`` when the reference has no change."""
    return pooled.precision_recall(_score_of(counts))[1]


def _left_alone_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the sentences the reference leaves alone that the model left alone."""
    alone = sum(found[4] for found in counts)
    alone_of = sum(found[5] for found in counts)
    return alone / alone_of if alone_of else None


def _coverage_of(counts: Sequence[Counts]) -> float | None:
    """Return the share of the sentences the model covered."""
    cover = sum(found[6] for found in counts)
    cover_of = sum(found[7] for found in counts)
    return cover / cover_of if cover_of else None


MEASURES: dict[str, Measure] = {
    "F0.5": _f_beta_of,
    "recall": _recall_of,
    "precision": _precision_of,
    "left alone": _left_alone_of,
    "coverage": _coverage_of,
}
"""The numbers a comparison prints, each with its own interval."""


def paired_bootstrap(
    a: groups.RunGroup,
    b: groups.RunGroup,
    measures: Mapping[str, Measure] | None = None,
    *,
    deciding: tuple[str, ...] = groups.DECIDING_LANGUAGES,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> dict[str, Interval]:
    """Return the interval of each measure of *a* minus *b*, over the drawn texts.

    Per text and model the counts are pooled over the repeats; per language as many texts are drawn
    as it has, with replacement, the same draw for both models, and the mean over the deciding
    languages is taken.  One set of draws serves every measure, so the intervals agree with each
    other.  The seed is fixed, so every machine gets the same numbers.

    A measure counts only where it can be measured in every deciding language, in a draw as in the
    point estimate: a draw in which one language has no sentence to measure it by is left out of
    that measure, so the interval and ``delta`` average over the same languages.

    Each language draws from its own stream, so the interval of the deciding mean does not depend on
    which other languages the report holds: a French text added to a set of German ones leaves the
    German draws as they were.

    Args:
        a: The first model's runs.
        b: The second model's runs.
        measures: The numbers to measure, by name; :data:`MEASURES` when not given.
        deciding: The languages whose mean decides.
        draws: How many draws to make.
        seed: The seed of the draws.

    Returns:
        One interval per measure that could be measured; a measure that cannot be measured in every
        deciding language in any draw is left out.
    """
    return _bootstrap(a, b, measures, deciding=deciding, draws=draws, seed=seed)[0]


def verdict_of(
    a: groups.RunGroup, b: groups.RunGroup, *, corrections: bool = False
) -> Verdict | None:
    """Return whether *a* is better than *b* beyond the noise, or ``None`` when nothing can judge.

    The interval of the deciding mean F0.5 decides, and two guards may overrule it: a group that is
    behind on F0.5 but ahead on GLEU or on the page band by more than :data:`MIN_EFFECT` is a tie
    and not a loss, because the F-score alone can prefer a model that fixes fewer errors and makes
    fewer false alarms.  A group without a page text has no band to guard, so that guard does not
    stop the verdict.

    The interval is drawn over the deciding languages, each from its own stream, so a language that
    does not decide cannot move it.

    ``None`` for a pair the bootstrap cannot judge - another machine, another window, other texts -
    and for one whose F0.5 could not be drawn at all.
    """
    if not groups.comparable(a, b, corrections=corrections):
        return None
    interval = paired_bootstrap(a, b).get("F0.5")
    if interval is None:
        return None
    verdict = interval.verdict()
    if verdict is Verdict.TIE:
        return Verdict.TIE
    mine, theirs = a.summaries(), b.summaries()
    if verdict is Verdict.BETTER:
        return Verdict.BETTER if _guards_hold(mine, theirs) else Verdict.TIE
    return Verdict.WORSE if _guards_hold(theirs, mine) else Verdict.TIE


def _guards_hold(
    one: Sequence[Sequence[summary.ModelSummary]],
    other: Sequence[Sequence[summary.ModelSummary]],
) -> bool:
    """Whether *one* is not behind *other* on GLEU and on the page band by more than the minimum."""
    return _not_behind(one, other, groups.deciding_mean_gleu) and _page_guard_holds(one, other)


def _not_behind(
    one: Sequence[Sequence[summary.ModelSummary]],
    other: Sequence[Sequence[summary.ModelSummary]],
    measure: Callable[[Sequence[summary.ModelSummary]], float | None],
) -> bool:
    """Whether *one*'s mean of *measure* is not below *other*'s by more than the minimum effect.

    ``False`` when a repeat has no value: the guard cannot be read, so it does not overrule the
    interval.
    """
    mine = groups.ranges(one, measure)
    theirs = groups.ranges(other, measure)
    if mine is None or theirs is None:
        return False
    return mine.mean >= theirs.mean - MIN_EFFECT


def _page_guard_holds(
    one: Sequence[Sequence[summary.ModelSummary]],
    other: Sequence[Sequence[summary.ModelSummary]],
) -> bool:
    """Whether the page band lets the interval stand; a group without a page text has no band."""
    if not _has_page(one) or not _has_page(other):
        return True
    return _not_behind(one, other, groups.band_mean_f_beta)


def _has_page(parts: Sequence[Sequence[summary.ModelSummary]]) -> bool:
    """Whether any repeat of a group has a page-band score."""
    return any(
        row.band == groups.PAGE_BAND and row.f_beta is not None for part in parts for row in part
    )


def _bootstrap(
    a: groups.RunGroup,
    b: groups.RunGroup,
    measures: Mapping[str, Measure] | None = None,
    *,
    deciding: tuple[str, ...],
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[dict[str, Interval], dict[str, float]]:
    """Return the intervals of the deciding mean and ``P(Δ ≤ 0)`` of F0.5 per language.

    Each language draws from its own stream, seeded by the seed and the language's code, so the
    draws of one language never move another's.  The per-language chance costs no second bootstrap:
    it is the same draws the mean is taken over.
    """
    measures = MEASURES if measures is None else measures
    left = per_text(a, deciding)
    right = per_text(b, deciding)
    languages = [code for code in deciding if code in left and code in right]
    if not languages:
        return {}, {}
    deltas: dict[str, dict[str, list[float | None]]] = {
        name: {code: [] for code in languages} for name in measures
    }
    for code in languages:
        rng = random.Random(f"{seed}/{code}")  # a reproducible draw, not a security decision
        items = sorted(left[code])
        for _ in range(draws):
            draw = [items[rng.randrange(len(items))] for _ in items]
            first = [left[code][item] for item in draw]
            second = [right[code][item] for item in draw]
            for name, measure in measures.items():
                one = measure(first)
                other = measure(second)
                deltas[name][code].append(None if one is None or other is None else one - other)
    intervals: dict[str, Interval] = {}
    for name, measure in measures.items():
        found = deltas[name]
        values: list[float] = []
        for index in range(draws):
            drawn = [found[code][index] for code in languages]
            if any(value is None for value in drawn):
                continue
            values.append(sum(value for value in drawn if value is not None) / len(languages))
        if not values:
            continue
        delta = _measured_difference(left, right, languages, measure)
        if delta is None:
            continue
        ordered = sorted(values)
        intervals[name] = Interval(
            delta=delta,
            low=_quantile(ordered, 0.025),
            high=_quantile(ordered, 0.975),
            p_not_better=sum(1 for value in values if value <= 0) / len(values),
            p_not_worse=sum(1 for value in values if value >= 0) / len(values),
        )
    chances = {
        code: sum(1 for value in deltas["F0.5"][code] if value is not None and value <= 0)
        / len(deltas["F0.5"][code])
        for code in languages
        if "F0.5" in deltas and deltas["F0.5"][code]
    }
    return intervals, chances


def _measured_difference(
    left: Mapping[str, Mapping[str, Counts]],
    right: Mapping[str, Mapping[str, Counts]],
    languages: Sequence[str],
    measure: Measure,
) -> float | None:
    """Return the mean over *languages* of *measure* of *left* minus *right*, or ``None``.

    ``None`` when the measure is undefined in any of *languages*, as in a draw.
    """
    deltas = []
    for code in languages:
        one = measure(list(left[code].values()))
        other = measure(list(right[code].values()))
        if one is None or other is None:
            return None
        deltas.append(one - other)
    return sum(deltas) / len(deltas) if deltas else None


def _quantile(ordered: Sequence[float], share: float) -> float:
    """Return the *share* point of *ordered*, which is sorted, by nearest rank."""
    return ordered[round(share * (len(ordered) - 1))]


def bootstrap(
    a: groups.RunGroup,
    b: groups.RunGroup,
    *,
    deciding: tuple[str, ...] = groups.DECIDING_LANGUAGES,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = BOOTSTRAP_SEED,
) -> float:
    """Return ``P(Δ ≤ 0)`` for the mean F0.5 of *a* minus *b* over the deciding languages.

    The share of the draws in which *a* is not better, which the per-language figures print; the
    verdict reads the interval of :func:`paired_bootstrap` instead.  One language alone gives that
    language's own bootstrap.
    """
    found = paired_bootstrap(a, b, {"F0.5": _f_beta_of}, deciding=deciding, draws=draws, seed=seed)
    return found["F0.5"].p_not_better if "F0.5" in found else 1.0


def per_text(group: groups.RunGroup, languages: Sequence[str]) -> dict[str, dict[str, Counts]]:
    """Return ``{language: {item: counts}}`` of *languages*, the counts pooled over the repeats.

    Error texts only: a ``correct`` text has no errors to compare, and the language rows are built
    from the error texts too.
    """
    pooled_counts: dict[str, dict[str, list[int]]] = {}
    for items in group.items:
        for item in items:
            if item.language not in languages or item.kind != TextKind.ERRORS:
                continue
            counts = pooled_counts.setdefault(item.language, {}).setdefault(item.item, [0] * 8)
            counts[0] += item.true_positives
            counts[1] += item.false_positives
            counts[2] += item.found
            counts[3] += item.false_negatives
            counts[4] += int(item.scores.get("alone", 0))
            counts[5] += int(item.scores.get("alone_of", 0))
            counts[6] += int(item.scores.get("cover", 0))
            counts[7] += int(item.scores.get("cover_of", 0))
    return {
        code: {
            item: (found[0], found[1], found[2], found[3], found[4], found[5], found[6], found[7])
            for item, found in by_item.items()
        }
        for code, by_item in pooled_counts.items()
    }


__all__ = [
    "BOOTSTRAP_DRAWS",
    "BOOTSTRAP_SEED",
    "MEASURES",
    "MIN_EFFECT",
    "TIME_TOLERANCE",
    "Counts",
    "Interval",
    "Measure",
    "Verdict",
    "bootstrap",
    "paired_bootstrap",
    "per_text",
    "verdict_of",
]
