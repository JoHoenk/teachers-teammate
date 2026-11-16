"""One table per model over its temperatures: the figures of a sweep, and the best row.

A sweep is the same model asked at several temperatures (and, with a sampling override, at several
request profiles), and the question is which of them to keep.  The table answers it the way the
scorecard does: quality and cost side by side, the spread of the repeats as a diagnostic, and the
difference against a reference row with its interval, so a reader sees which rows are a tie.

Nothing here decides on its own: the best row is the one with the highest F0.5, and every row that
is not worse than it beyond the noise is printed with it, because a tie goes to the cheaper
configuration and that is a choice, not a number.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from . import gate_two, grouping, summary
from .performance import per_100_words

DECIDING_LANGUAGES: tuple[str, ...] = grouping.DECIDING_LANGUAGES
"""The languages whose mean decides; French is reported only."""


@dataclass(frozen=True)
class SweepRow:
    """One configuration of a sweep: a model at one temperature and one request profile."""

    group: grouping.RunGroup
    f_beta: float | None
    """The mean F0.5 of the deciding languages, over the repeats."""
    de: float | None
    en: float | None
    recall: float | None
    precision: float | None
    alone_de: float | None
    alone_en: float | None
    cover_de: float | None
    cover_en: float | None
    false_alarms_de: float | None
    """Wrong or unneeded changes per error text, in German."""
    false_alarms_en: float | None
    spread_de: float | None
    """The highest minus the lowest F0.5 of the repeats, in German: the noise of the sampler."""
    spread_en: float | None
    lost_text: int
    added_text: int
    loss_checked: int
    seconds_per_100_words: float | None
    answer_tokens_per_text: float | None
    fixed_per_page: float | None
    wrong_per_page: float | None
    missed_per_page: float | None
    delta: gate_two.Interval | None
    """The difference of the deciding mean F0.5 against the reference row, with its interval."""
    comparable: bool = True
    """Whether the reference row was measured alike and holds the same texts, so a Δ exists."""

    @property
    def model(self) -> str:
        """The model tag of the row."""
        return self.group.key.model

    @property
    def temperature(self) -> float:
        """The temperature the row was asked at."""
        return self.group.key.temperature

    @property
    def profile(self) -> str:
        """The sampling values of the request profile, or ``"default"`` when it has none.

        The temperature is left out: it has a column of its own, and it is in every profile.
        """
        return (
            ", ".join(
                f"{name} {value}"
                for name, value in self.group.key.request_profile
                if name != "temperature"
            )
            or "default"
        )


def sweep_rows(
    groups: Sequence[grouping.RunGroup],
    reference: grouping.RunGroup | None = None,
    *,
    corrections: bool = False,
) -> list[SweepRow]:
    """Return one row per group, in the order of the groups.

    Args:
        groups: The configurations to show, one group each.
        reference: The group every row's difference is measured against; the first group of each
            model when ``None``.
        corrections: Whether the two exchange modes count as one, as in ``gate_two.compare``.
    """
    rows: list[SweepRow] = []
    for group in groups:
        against = reference if reference is not None else _first_of_model(groups, group)
        rows.append(_row(group, against, corrections))
    return rows


def _first_of_model(
    groups: Sequence[grouping.RunGroup], group: grouping.RunGroup
) -> grouping.RunGroup:
    """Return the first group of *group*'s model, which is the reference of a sweep by default."""
    return next(other for other in groups if other.key.model == group.key.model)


def _row(group: grouping.RunGroup, reference: grouping.RunGroup, corrections: bool) -> SweepRow:
    """Return the figures of one group, its difference against *reference* included."""
    parts = group.summaries()
    de = _per_repeat(parts, "de", _f_beta)
    en = _per_repeat(parts, "en", _f_beta)
    return SweepRow(
        group=group,
        f_beta=_mean_of_languages(de, en),
        de=_mean(de),
        en=_mean(en),
        recall=_mean_of_languages(
            _per_repeat(parts, "de", _recall), _per_repeat(parts, "en", _recall)
        ),
        precision=_mean_of_languages(
            _per_repeat(parts, "de", _precision), _per_repeat(parts, "en", _precision)
        ),
        alone_de=_mean(_per_repeat(parts, "de", _alone)),
        alone_en=_mean(_per_repeat(parts, "en", _alone)),
        cover_de=_mean(_per_repeat(parts, "de", _coverage)),
        cover_en=_mean(_per_repeat(parts, "en", _coverage)),
        false_alarms_de=_mean(_per_repeat(parts, "de", _false_alarms)),
        false_alarms_en=_mean(_per_repeat(parts, "en", _false_alarms)),
        spread_de=_spread(de),
        spread_en=_spread(en),
        lost_text=sum(row.lost_text for part in parts for row in part if not row.band),
        added_text=sum(row.added_text for part in parts for row in part if not row.band),
        loss_checked=sum(row.loss_checked for part in parts for row in part if not row.band),
        seconds_per_100_words=_seconds_per_100_words(group),
        answer_tokens_per_text=_answer_tokens_per_text(group),
        fixed_per_page=summary.pooled_per_page(parts, "true_positives", DECIDING_LANGUAGES),
        wrong_per_page=summary.pooled_per_page(parts, "false_positives", DECIDING_LANGUAGES),
        missed_per_page=summary.pooled_per_page(parts, "false_negatives", DECIDING_LANGUAGES),
        delta=_difference(group, reference, corrections),
        comparable=group is reference
        or gate_two.comparable(group, reference, corrections=corrections),
    )


def _per_repeat(
    parts: Sequence[Sequence[summary.ModelSummary]], code: str, measure
) -> tuple[float | None, ...]:
    """Return one language's value of *measure* in each repeat."""
    return tuple(
        None if (row := grouping.language_row(part, code)) is None else measure(row)
        for part in parts
    )


def _f_beta(row: summary.ModelSummary) -> float | None:
    """Return the F0.5 of one language row."""
    return row.f_beta


def _recall(row: summary.ModelSummary) -> float | None:
    """Return the recall of one language row."""
    return row.recall


def _precision(row: summary.ModelSummary) -> float | None:
    """Return the precision of one language row."""
    return row.precision


def _alone(row: summary.ModelSummary) -> float | None:
    """Return the share of the sentences the reference leaves alone that the model left alone."""
    return summary.share_of(row.alone, row.alone_of)


def _coverage(row: summary.ModelSummary) -> float | None:
    """Return the share of the touched sentences the model looked at."""
    return summary.share_of(row.cover, row.cover_of)


def _false_alarms(row: summary.ModelSummary) -> float | None:
    """Return the wrong or unneeded changes per error text."""
    return row.score.false_positives / row.texts if row.texts else None


def _mean(values: Iterable[float | None]) -> float | None:
    """Return the mean of the values that are not ``None``, or ``None`` when none is."""
    found = [value for value in values if value is not None]
    return sum(found) / len(found) if found else None


def _mean_of_languages(*per_language: Iterable[float | None]) -> float | None:
    """Return the mean over the languages that have a value, or ``None`` when none has."""
    return _mean(_mean(values) for values in per_language)


def _spread(values: Iterable[float | None]) -> float | None:
    """Return the highest minus the lowest value, or ``None`` when a repeat has none."""
    found = [value for value in values if value is not None]
    return max(found) - min(found) if len(found) > 1 else None


def _seconds_per_100_words(group: grouping.RunGroup) -> float | None:
    """Return the seconds per 100 words of student text, over every text of the group."""
    seconds = sum(item.seconds for items in group.items for item in items)
    words = sum(item.words for items in group.items for item in items)
    return per_100_words(seconds, words) if words else None


def _answer_tokens_per_text(group: grouping.RunGroup) -> float | None:
    """Return the mean answer tokens of one text, or ``None`` when nothing was measured."""
    values = [
        item.performance.answer_tokens
        for items in group.items
        for item in items
        if item.performance is not None
    ]
    return sum(values) / len(values) if values else None


def _difference(
    group: grouping.RunGroup, reference: grouping.RunGroup, corrections: bool
) -> gate_two.Interval | None:
    """Return the interval of the deciding mean F0.5 of *group* minus *reference*, or ``None``.

    ``None`` too for a reference the group cannot be compared with (another machine, window or
    eval version, or other texts): a difference there would be a number without a meaning.
    """
    if group is reference or not gate_two.comparable(group, reference, corrections=corrections):
        return None
    return gate_two.paired_bootstrap(group, reference).get("F0.5")


def best_and_ties(
    rows: Sequence[SweepRow], *, corrections: bool = False
) -> tuple[SweepRow | None, list[SweepRow]]:
    """Return the row with the highest F0.5 and every row that is not worse than it.

    "Not worse" is the comparison's own rule: the interval of the paired bootstrap and the minimum
    effect, or — for a group the comparison cannot judge, such as one with two repeats — a gap
    within the minimum effect.  A tie is printed with the best row because the cheaper of two
    equal configurations is the one to keep, and that is a choice a reader makes.
    """
    scored = [row for row in rows if row.f_beta is not None]
    if not scored:
        return None, []
    best = max(scored, key=lambda row: row.f_beta or 0.0)
    return best, [row for row in scored if row is not best and _not_worse(row, best, corrections)]


def _not_worse(row: SweepRow, best: SweepRow, corrections: bool) -> bool:
    """Whether *row* is not worse than *best* beyond the noise."""
    result = gate_two.compare(row.group, best.group, corrections=corrections)
    if result.comparison is gate_two.Comparison.NOT_COMPARABLE:
        return (
            row.f_beta is not None
            and best.f_beta is not None
            and (row.f_beta >= best.f_beta - gate_two.MIN_EFFECT)
        )
    return result.comparison is not gate_two.Comparison.WORSE


def sweep_table(rows: Sequence[SweepRow], *, reference: str = "", corrections: bool = False) -> str:
    """Return the sweep as markdown: one table per model, then its best row and its ties.

    Args:
        rows: The rows of the sweep, in the order they should be printed.
        reference: What the difference column is measured against, for the note below the tables.
        corrections: Whether the ties are judged on the corrections only (see ``sweep_rows``).
    """
    lines: list[str] = []
    for model in dict.fromkeys(row.model for row in rows):
        part = [row for row in rows if row.model == model]
        lines += [
            f"## `{model}`",
            "",
            "| T | Profile | F0.5 | DE | EN | R | P | Left alone DE/EN | Coverage DE/EN "
            "| False alarms/text DE/EN | Noise DE/EN | Lost / added | s/100w | Answer tokens/text "
            "| Fixed/page | Wrong/page | Missed/page | Δ F0.5 [95 %] |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | "
            "--- | --- | --- | --- | --- |",
        ]
        for row in part:
            lines.append(
                f"| {row.temperature:g} | {row.profile} | {_share(row.f_beta)} "
                f"| {_share(row.de)} | {_share(row.en)} | {_share(row.recall)} "
                f"| {_share(row.precision)} "
                f"| {_pair(row.alone_de, row.alone_en)} | {_pair(row.cover_de, row.cover_en)} "
                f"| {_pair(row.false_alarms_de, row.false_alarms_en, '.1f')} "
                f"| {_pair(row.spread_de, row.spread_en, '.3f')} "
                f"| {_loss(row)} | {_number(row.seconds_per_100_words, '.2f')} "
                f"| {_number(row.answer_tokens_per_text, '.0f')} "
                f"| {_number(row.fixed_per_page, '.1f')} "
                f"| {_number(row.wrong_per_page, '.1f')} "
                f"| {_number(row.missed_per_page, '.1f')} "
                f"| {_interval(row)} |"
            )
        lines += ["", *_best_lines(part, corrections), ""]
    if reference:
        lines += [f"The difference is measured against {reference}.", ""]
    return "\n".join(lines).rstrip() + "\n"


def _best_lines(rows: Sequence[SweepRow], corrections: bool) -> list[str]:
    """Return the best row of one model and the rows that are a tie with it."""
    best, ties = best_and_ties(rows, corrections=corrections)
    if best is None:
        return ["No row was scored: nothing to choose from."]
    lines = [f"Best: {best.temperature:g} ({best.profile}) at F0.5 {_share(best.f_beta)}."]
    if ties:
        named = ", ".join(f"{row.temperature:g} ({row.profile})" for row in ties)
        lines.append(f"Not worse beyond the noise: {named}.")
    else:
        lines.append("No other row is a tie with it.")
    return lines


def _share(value: float | None) -> str:
    """Return a score as two decimals, or a dash when nothing was scored."""
    return "—" if value is None else f"{value:.2f}"


def _number(value: float | None, spec: str) -> str:
    """Return a figure in *spec* format, or a dash when it could not be computed."""
    return "—" if value is None else format(value, spec)


def _pair(first: float | None, second: float | None, spec: str = ".2f") -> str:
    """Return two figures of one row as ``first/second``, or a dash when either is missing."""
    if first is None or second is None:
        return "—"
    return f"{format(first, spec)}/{format(second, spec)}"


def _loss(row: SweepRow) -> str:
    """Return the texts that lost or added text, or a dash when the run was not measured for it."""
    if not row.loss_checked:
        return "—"
    return f"{row.lost_text} / {row.added_text}"


def _interval(row: SweepRow) -> str:
    """Return a row's difference with its interval, a dash for the reference row itself."""
    interval = row.delta
    if not row.comparable:
        return "not comparable"
    if interval is None:
        return "—"
    return f"{interval.delta:+.3f} [{interval.low:+.3f}, {interval.high:+.3f}]"


__all__ = ["SweepRow", "best_and_ties", "sweep_rows", "sweep_table"]
