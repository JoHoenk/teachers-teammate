"""What several passes of one model would add, and what they would cost.

The app runs one pass, so this is information: it says what a second and a third pass would buy if
the passes were merged by a vote.  An edit is a word-diff change of the source; the repeats of one
configuration are the passes, and an edit's *votes* are how many of them made it.

Three merges are shown: the single pass (the mean of the repeats), the edits at least two repeats
made, and the edits every repeat made.  Next to them the flag: if a single pass's edits that not
every repeat made were shown as uncertain, how many of them are false alarms and how many are real
fixes.  The last figure is the time the passes cost, from the share of the exchange that step ①
takes: a second and a third pass repeat only that step.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from teachers_teammate.domain.proofreading.edit_agreement import EditScore, edits
from teachers_teammate.domain.proofreading.word_diff import Edit

from ...core.constants import TextKind
from ...core.performance import step_one_share
from .. import groups

DECIDING_LANGUAGES: tuple[str, ...] = groups.DECIDING_LANGUAGES
"""The languages the figures are pooled over; French is reported only."""

Edits = frozenset[Edit]
"""The changes of one text: ``(start, end, replacement)`` over the source tokens."""

EXCHANGE_MODE = "text-first"
"""The label of the whole exchange, whose step ① share prices a second pass."""

CORRECT_ONLY_MODE = "text-first/correct"
"""The label of step ① alone, which has no share of its own and takes its model's."""


@dataclass(frozen=True)
class PassRow:
    """What the repeats of one configuration would give as one, two or three passes."""

    model: str
    temperature: float
    mode: str
    repeats: int
    single: EditScore
    """The pooled counts of one pass, averaged over the repeats."""
    vote_two: EditScore
    """The pooled counts of the edits at least two of the repeats made."""
    all_repeats: EditScore
    """The pooled counts of the edits every repeat made."""
    flagged_share: float | None
    """Share of the single pass's edits that not every repeat made."""
    flagged_false_alarms: float | None
    """Share of the single pass's false alarms that carry the flag."""
    flagged_true_fixes: float | None
    """Share of the single pass's real fixes that carry the flag."""
    precision_by_votes: tuple[float | None, ...]
    """The precision of an edit made by 1, 2, … repeats; ``None`` where no edit has that count."""
    step_one_share: float | None
    """Share of the exchange's waiting time that step ① took, or ``None`` without step tags."""

    @property
    def passes_time(self) -> float | None:
        """The time of the passes against one pass: ``1 + (k - 1) x step ①'s share``."""
        if self.step_one_share is None:
            return None
        return 1 + (self.repeats - 1) * self.step_one_share


def pass_rows(chosen: Sequence[groups.RunGroup]) -> list[PassRow]:
    """Return one row per group that has at least two repeats, in the order of the groups.

    A group of one repeat has no vote to take, so it is left out: the single pass is all there is.
    The time of the passes needs the share of the exchange that step ① takes.  A correct-only run
    is step ① alone, so its own share is 100 % and says nothing; it takes its model's share from
    the exchange groups among *chosen* and shows none when there are none.
    """
    shares = _exchange_shares(chosen)
    return [_row(group, shares) for group in chosen if len(group.items) >= 2]


def _exchange_shares(chosen: Sequence[groups.RunGroup]) -> dict[str, float]:
    """Return ``{model: share of the exchange's time that step ① takes}`` from the exchange groups.

    The mean over the model's exchange groups, because the share hardly moves with the temperature.
    """
    found: dict[str, list[float]] = {}
    for group in chosen:
        if group.key.mode != EXCHANGE_MODE:
            continue
        share = _step_one_share(group)
        if share is not None:
            found.setdefault(group.key.model, []).append(share)
    return {model: sum(values) / len(values) for model, values in found.items()}


def _row(group: groups.RunGroup, exchange_shares: dict[str, float]) -> PassRow:
    """Return the figures of one group's repeats."""
    passes = _passes(group)
    references = _references(group)
    repeats = len(group.items)
    votes = {
        item: Counter(edit for edits in per_item for edit in edits)
        for item, per_item in passes.items()
    }
    return PassRow(
        model=group.key.model,
        temperature=group.key.temperature,
        mode=group.key.mode,
        repeats=repeats,
        single=_mean_score(
            [_pooled(_one_pass(passes, index), references) for index in range(repeats)]
        ),
        vote_two=_pooled(_merged(votes, minimum=2), references),
        all_repeats=_pooled(_merged(votes, minimum=repeats), references),
        flagged_share=_flagged_share(passes, votes),
        flagged_false_alarms=_flagged_share(passes, votes, references=references, hit=False),
        flagged_true_fixes=_flagged_share(passes, votes, references=references, hit=True),
        precision_by_votes=_precision_by_votes(votes, references, repeats),
        step_one_share=_group_share(group, exchange_shares),
    )


def _passes(group: groups.RunGroup) -> dict[str, list[Edits]]:
    """Return ``{item: [the edits of each repeat]}`` of the error texts of the deciding languages.

    Raises:
        ValueError: if a repeat lacks a text another has (an interrupted run), because a vote needs
            every text in every repeat.
    """
    per_repeat: list[dict[str, Edits]] = []
    for items in group.items:
        made: dict[str, Edits] = {}
        for item in items:
            if item.language not in DECIDING_LANGUAGES or item.kind != TextKind.ERRORS:
                continue
            made[item.item] = edits(item.record.source, item.output)
        per_repeat.append(made)
    expected = set(per_repeat[0])
    for number, made in enumerate(per_repeat, start=1):
        if set(made) != expected:
            raise ValueError(
                f"{group.key.model} at {group.key.temperature:g}: repeat {number} does not hold "
                "the same texts as repeat 1; finish or remove the interrupted run"
            )
    return {item: [made[item] for made in per_repeat] for item in per_repeat[0]}


def _references(group: groups.RunGroup) -> dict[str, Edits]:
    """Return ``{item: the reference's edits}`` of the error texts of the deciding languages."""
    references: dict[str, Edits] = {}
    for items in group.items:
        for item in items:
            if item.language not in DECIDING_LANGUAGES or item.kind != TextKind.ERRORS:
                continue
            references.setdefault(item.item, edits(item.record.source, item.record.reference))
    return references


def _one_pass(passes: dict[str, list[Edits]], index: int) -> dict[str, Edits]:
    """Return the edits of one repeat, by item."""
    return {item: per_item[index] for item, per_item in passes.items()}


def _merged(votes: dict[str, Counter], *, minimum: int) -> dict[str, Edits]:
    """Return the edits of each item that at least *minimum* repeats made."""
    return {
        item: frozenset(edit for edit, count in found.items() if count >= minimum)
        for item, found in votes.items()
    }


def _pooled(edits_by_item: dict[str, Edits], references: dict[str, Edits]) -> EditScore:
    """Return the pooled counts of *edits_by_item* against *references*."""
    true_positives = false_positives = found = false_negatives = 0
    for item, made in edits_by_item.items():
        expected = references.get(item, frozenset())
        hits = len(made & expected)
        true_positives += hits
        false_positives += len(made) - hits
        found += hits
        false_negatives += len(expected) - hits
    return EditScore(
        true_positives=true_positives,
        false_positives=false_positives,
        found=found,
        false_negatives=false_negatives,
    )


def _mean_score(scores: Sequence[EditScore]) -> EditScore:
    """Return the mean of the counts of *scores*, rounded to whole changes."""
    if not scores:
        return EditScore(0, 0, 0, 0)
    count = len(scores)
    return EditScore(
        true_positives=round(sum(score.true_positives for score in scores) / count),
        false_positives=round(sum(score.false_positives for score in scores) / count),
        found=round(sum(score.found for score in scores) / count),
        false_negatives=round(sum(score.false_negatives for score in scores) / count),
    )


def _flagged_share(
    passes: dict[str, list[Edits]],
    votes: dict[str, Counter],
    *,
    references: dict[str, Edits] | None = None,
    hit: bool | None = None,
) -> float | None:
    """Return the share of the single pass's edits that not every repeat made.

    With *references* and *hit*, only the edits that are (or are not) in the reference count, so
    the same rule says how many false alarms and how many real fixes the flag would catch.
    """
    flagged = total = 0
    for item, per_item in passes.items():
        expected = (references or {}).get(item, frozenset())
        for made in per_item:
            for edit in made:
                if hit is not None and (edit in expected) is not hit:
                    continue
                total += 1
                flagged += votes[item][edit] < len(per_item)
    return flagged / total if total else None


def _precision_by_votes(
    votes: dict[str, Counter],
    references: dict[str, Edits],
    repeats: int,
) -> tuple[float | None, ...]:
    """Return the precision of an edit made by 1, 2, … *repeats* repeats."""
    counts = {number: [0, 0] for number in range(1, repeats + 1)}
    for item, found in votes.items():
        expected = references.get(item, frozenset())
        for edit, number in found.items():
            counts[number][0] += 1
            counts[number][1] += edit in expected
    return tuple(
        counts[number][1] / counts[number][0] if counts[number][0] else None
        for number in range(1, repeats + 1)
    )


def _group_share(group: groups.RunGroup, exchange_shares: dict[str, float]) -> float | None:
    """Return the share that prices *group*'s passes: its own, or its model's for correct-only."""
    if group.key.mode == CORRECT_ONLY_MODE:
        return exchange_shares.get(group.key.model)
    return _step_one_share(group)


def _step_one_share(group: groups.RunGroup) -> float | None:
    """Return the share of the waiting time that step ① took, or ``None`` without step tags."""
    return step_one_share([item for items in group.items for item in items])


def passes_table(rows: Sequence[PassRow]) -> str:
    """Return the passes as markdown: one row per configuration."""
    lines = [
        "| Model | T | Mode | Single pass F0.5 (P/R) | Vote (2+ repeats) | All repeats "
        "| Flagged share of edits | False alarms flagged | Real fixes flagged "
        "| Precision by votes | Time of the passes |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| `{row.model}` | {row.temperature:g} | {row.mode} "
            f"| {_score_cell(row.single)} | {_share(row.vote_two.f_beta())} "
            f"| {_score_cell(row.all_repeats)} | {_percent(row.flagged_share)} "
            f"| {_percent(row.flagged_false_alarms)} | {_percent(row.flagged_true_fixes)} "
            f"| {_votes_cell(row.precision_by_votes)} | {_time_cell(row.passes_time)} |"
        )
    lines += [
        "",
        "The single pass is the mean of the repeats; the vote keeps the edits at least two "
        'repeats made, and "all repeats" only those every repeat made.  The flag is what a single '
        "pass would show as uncertain: the edits not every repeat made.  The time of the passes "
        "is one pass plus the repeated step ①, from the share of the exchange that step ① takes; a "
        "correct-only row takes it from the same model's exchange rows (read the exchange folder "
        "too), and shows a dash without them.",
    ]
    return "\n".join(lines)


def _score_cell(score: EditScore) -> str:
    """Return the F0.5 of a merge with its precision and recall in brackets."""
    return f"{score.f_beta():.2f} ({score.precision:.2f}/{score.recall:.2f})"


def _share(value: float | None) -> str:
    """Return a score as two decimals, or a dash when nothing was scored."""
    return "—" if value is None else f"{value:.2f}"


def _percent(value: float | None) -> str:
    """Return a share as a percentage, or a dash when nothing was measured."""
    return "—" if value is None else f"{value:.0%}"


def _votes_cell(values: Sequence[float | None]) -> str:
    """Return the precision of each vote count, separated by dots."""
    return " · ".join("—" if value is None else f"{value:.2f}" for value in values)


def _time_cell(value: float | None) -> str:
    """Return the time of the passes against one pass, or a dash when step ① was not tagged."""
    return "—" if value is None else f"{value:.2f}x"


__all__ = [
    "CORRECT_ONLY_MODE",
    "DECIDING_LANGUAGES",
    "EXCHANGE_MODE",
    "Edits",
    "PassRow",
    "pass_rows",
    "passes_table",
]
