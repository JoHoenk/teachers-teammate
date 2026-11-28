"""The table a campaign is read from: one row per run and group, against a chosen baseline.

A number on its own says nothing: a CER of 0.18 is good or bad depending on what the other models
read on the same pages.  So every row carries the difference to a baseline run, measured page for
page on the page ids both runs hold — a paired bootstrap, seeded, so the same records always give
the same interval.  A difference whose interval holds zero is no difference.

A row's figures are the run's :class:`~tools.ocr_eval.records.Totals` over the row's pages, and the
difference is weighted the same way: the CER by the characters of each page, the kept mistakes
page by page.  So when both runs read the same pages, the difference is the two CER columns'.

The draws resample writers, not pages: ten pages of one hand are not ten pieces of evidence.  A row
whose pages come from one writer has nothing to resample, and its interval is the difference
itself.

The baseline is what a default would be replaced: ``raw`` for a preparation question, and the
model a campaign would otherwise pick.  Reading the rows:

* a run that wins on average but loses clearly in one group (often photographed handwriting)
  makes a poor single default;
* a gain that appears on the tune set and vanishes on the check set was chance;
* 0.2 points of CER for ten seconds a page is not worth the time;
* a model that reads better but keeps clearly fewer of the student's mistakes hides what
  proofreading is there to find, so its *kept mistakes* column is read next to its CER.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import random

from .records import Header, PageNumbers, RunRecord, Totals
from .runs import Stored

DRAWS = 2000
"""How often a paired bootstrap resamples the writers; 2000 is steady to about a point."""

SEED = 20261128
"""The seed of the draws: the same records give the same interval, run after run."""

LOW = 2.5
HIGH = 97.5
"""The percentiles of the interval: a 95 % interval, two-sided."""


@dataclass(frozen=True)
class Interval:
    """A difference between two runs, and the range the writers put it in."""

    delta: float
    """The mean difference: positive when the run read better than the baseline."""
    low: float
    high: float


def _percentile(ordered: Sequence[float], share: float) -> float:
    """Return the *share* percentile of an ordered list (nearest rank, no interpolation)."""
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round(share / 100 * (len(ordered) - 1))))
    return ordered[index]


def _pages(record: RunRecord, *, group: str, split: str) -> list[PageNumbers]:
    """Return the pages of *record* that a row is made of."""
    return [
        page
        for page in record.pages
        if (not group or page.group == group) and (not split or page.split == split)
    ]


def paired(
    run: Mapping[str, float],
    baseline: Mapping[str, float],
    writers: Mapping[str, str],
    *,
    weights: Mapping[str, float] | None = None,
    higher_is_better: bool = False,
    draws: int = DRAWS,
    seed: int = SEED,
) -> Interval | None:
    """Return the run's difference to the baseline, with the range the writers give.

    A positive difference always means *run* did better than *baseline*: lower is better for CER,
    higher for the kept-mistakes share.  Each page counts by its weight (one when *weights* names
    none), as the rate the column shows is weighted.  The draws take writers with replacement, so
    a writer whose pages all moved one way counts once, not per page.  ``None`` when the two share
    no page: two runs of different pages are not compared.
    """
    shared = sorted(set(run) & set(baseline))
    if not shared:
        return None
    sign = 1.0 if higher_is_better else -1.0
    clusters: dict[str, list[tuple[float, float]]] = {}
    for page in shared:
        weight = 1.0 if weights is None else weights.get(page, 0.0)
        clusters.setdefault(writers.get(page, f"unknown:{page}"), []).append(
            (weight, sign * (run[page] - baseline[page]))
        )
    names = sorted(clusters)
    chosen = random.Random(seed)
    means = sorted(
        _mean([one for _ in names for one in clusters[names[chosen.randrange(len(names))]]])
        for _ in range(draws)
    )
    return Interval(
        delta=_mean([one for name in names for one in clusters[name]]),
        low=_percentile(means, LOW),
        high=_percentile(means, HIGH),
    )


def _mean(pairs: Sequence[tuple[float, float]]) -> float:
    """Return the weighted mean of ``(weight, difference)`` pairs; 0 when they weigh nothing."""
    whole = sum(weight for weight, _value in pairs)
    return sum(weight * value for weight, value in pairs) / whole if whole else 0.0


def _cell(share: float | None) -> str:
    """Return a rate as a percentage, or a dash when the run holds none."""
    return "—" if share is None else f"{share * 100:.1f} %"


def _change(interval: Interval | None) -> str:
    """Return a paired difference as points and its interval, or a dash."""
    if interval is None:
        return "—"
    return f"{interval.delta * 100:+.1f} [{interval.low * 100:+.1f}, {interval.high * 100:+.1f}]"


def rows(
    records: Iterable[Stored],
    *,
    baseline: Stored | None = None,
    group: str = "",
    split: str = "",
) -> list[str]:
    """Return the report's markdown table, one row per run and group.

    *baseline* is the run every other is compared against; without one the column is a dash.
    *group* and *split* narrow the pages a row is made of, not the runs.
    """
    lines = [
        "| Run | Model | Prompt | Preset | Group | Pages | CER | WER | Bag WER "
        "| CER vs baseline | Kept mistakes | Kept vs baseline | Failures | s/page | Memory "
        "| Licence (claimed) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- "
        "| --- | --- |",
    ]
    for one_run in records:
        header = one_run.header
        groups = (
            [group] if group else list(dict.fromkeys(page.group for page in one_run.record.pages))
        )
        for one in groups:
            pages = _pages(one_run.record, group=one, split=split)
            if not pages:
                continue
            theirs = _pages(baseline.record, group=one, split=split) if baseline else []
            cer, kept = _intervals(pages, theirs)
            totals = Totals.of(pages)
            lines.append(
                f"| `{one_run.name}` | `{header.model}` | {header.prompt} | {header.preset} "
                f"| {one} | {totals.pages} | {_cell(totals.cer)} | {_cell(totals.wer)} "
                f"| {_cell(totals.bag_wer)} | {_change(cer)} | {_cell(totals.kept)} "
                f"| {_change(kept)} | {totals.failures} ({totals.failures / totals.pages:.0%}) "
                f"| {totals.seconds / totals.pages:.1f} | {_memory(header)} "
                f"| {header.licence or '—'} |"
            )
    return lines


def _intervals(
    mine: Sequence[PageNumbers], theirs: Sequence[PageNumbers]
) -> tuple[Interval | None, Interval | None]:
    """Return the CER and kept-mistakes intervals of *mine* against *theirs*, the baseline's.

    Both are drawn over the writers, and both say "positive is better", so a reader compares the
    two columns without turning a sign around.
    """
    if not theirs:
        return None, None
    writers = {page.page: page.writer or f"unknown:{page.page}" for page in mine}
    return (
        paired(
            {page.page: page.cer for page in mine},
            {page.page: page.cer for page in theirs},
            writers,
            weights={page.page: float(page.characters) for page in mine},
        ),
        paired(
            {page.page: page.kept for page in mine if page.kept is not None},
            {page.page: page.kept for page in theirs if page.kept is not None},
            writers,
            higher_is_better=True,
        ),
    )


def table(
    records: Sequence[Stored],
    *,
    baseline: Stored | None = None,
    group: str = "",
    split: str = "",
) -> str:
    """Return the report as one markdown text."""
    return "\n".join(rows(records, baseline=baseline, group=group, split=split))


def _memory(header: Header) -> str:
    """Return the model's memory, and whether the run stayed off the card."""
    device = "cpu" if header.cpu_only else "card"
    return f"{header.memory_mb / 1024:.1f} GB ({device})" if header.memory_mb else f"— ({device})"
