"""Fit and page time per tier: the two criteria that come before quality, read from the records.

The fit is criterion 1 of ``evaluations/README.md``: the need measured at the app's window against
the card the tier may use (``FULL_SPEED_SHARE`` of it).  A computer without a card is judged by the
rule the app applies there — the model's size in the catalog against ``CPU_COMFORT_GB``, which is
the size a processor still runs at a usable speed, not a memory limit.  The page time is criterion
2: a typical page of student text through the whole exchange, read in the app's ``SpeedBand``.

Both come from the records.  The need and the writing rate are the store's
``model-fit/sizes.jsonl``; the page time is the full exchange where a run measured one as the app
asks (:func:`asked_as_the_app`), else the time the writing rate gives through the app's own
estimate, marked as computed.  A page time
belongs to one machine — the machine is a column, so two machines never meet in one figure.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import logging

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.domain.hardware_profile import (
    CPU_COMFORT_GB,
    HardwareProfile,
    choose_profile,
    comfortable_model_gb,
)
from teachers_teammate.domain.model_catalog import find_spec, request_profile
from teachers_teammate.domain.performance_estimate import (
    PROOFREADING_STEPS,
    TYPICAL_PAGE_WORDS,
    ModelSpeed,
    SpeedBand,
    band,
    proofreading_time,
)
from teachers_teammate.domain.proofreading.result import DEFAULT_EXCHANGE_COST

from ...core.machines import Machine
from ...core.modes import ALL_STEPS, DEFAULT_EXCHANGE
from ...core.sizes import GB, FitRecord, current_build, need_bytes, spilled, tokens_per_second
from .performance import PerformanceSummary

_logger = logging.getLogger(__name__)

BYTES_PER_GIB = 2**30
"""A card's total comes as bytes; the app's thresholds are the gigabytes a driver reports."""

TIER_ORDER: tuple[HardwareProfile, ...] = (
    HardwareProfile.CPU,
    HardwareProfile.CARD_8GB,
    HardwareProfile.CARD_16GB,
)
"""The tiers from weakest to strongest, in the order the table prints them."""

_TIER_TITLE: dict[HardwareProfile, str] = {
    HardwareProfile.CPU: "no card (the processor)",
    HardwareProfile.CARD_8GB: "about 8 GB",
    HardwareProfile.CARD_16GB: "16 GB or more",
}
"""How a tier is written in the table's heading."""


@dataclass(frozen=True)
class TierRow:
    """One model on one tier, on one machine and device, as the records measure it."""

    tier: HardwareProfile
    machine: str
    device: str
    """``gpu`` or ``cpu``: the two are different measurements of the same machine."""
    model: str
    need_gb: float | None
    """The need at the app's window here; ``None`` when this machine did not measure it."""
    partly_in_memory: bool
    """Whether the measured step ran partly in main memory: the need is a lower bound then."""
    fits: bool | None
    """Whether the app runs the model on this tier; ``None`` when it cannot tell."""
    seconds_per_page: float | None
    """A page of :data:`~teachers_teammate.domain.performance_estimate.TYPICAL_PAGE_WORDS` words, or
    ``None`` when nothing measured the model here."""
    measured: bool
    """Whether a full exchange measured the page time, or the writing rate gave it."""

    @property
    def band(self) -> SpeedBand | None:
        """Return the band of the page time, ``None`` without one."""
        return None if self.seconds_per_page is None else band(self.seconds_per_page)


def limit_gb(tier: HardwareProfile) -> float:
    """Return the largest need *tier* runs well with (the app's rule, ``comfortable_model_gb``)."""
    return comfortable_model_gb(tier)


def card_tier(machine: Machine | None, records: Iterable[FitRecord]) -> HardwareProfile | None:
    """Return the tier of *machine*'s card, or ``None`` when no figure says how large it is.

    The card's size is the registry's figure; a machine the registry does not know falls back to
    the total the memory agent read off the card.
    """
    gib = machine.vram_mb / 1024 if machine is not None and machine.vram_mb else None
    if gib is None:
        totals = [record.card.total_bytes for record in records if record.card is not None]
        gib = max(totals) / BYTES_PER_GIB if totals else None
    if gib is None:
        return None
    return choose_profile(
        gpus=[("", gib)], apple_unified_memory_gb=None, ollama_is_remote=False
    ).profile


def summarise_tiers(
    sizes: Iterable[FitRecord],
    performance: Iterable[PerformanceSummary],
    machines: Mapping[str, Machine],
) -> list[TierRow]:
    """Return one row per model, tier, machine and device that the records say something about.

    The fit is measured where the tier is: a need measured on a 16 GB card says nothing about the
    8 GB one, so each machine contributes its own rows.  A model that a full exchange measured on
    the machine gets that page time; every other model gets the time its measured writing rate
    gives, marked as computed.  Where a machine measured a model more than once, the slowest figure
    stands: a page that can take this long will.
    """
    measured = _page_times(performance)
    blocks = _blocks(sizes, measured, machines)
    return sorted(
        (row for block in blocks for row in block),
        key=_row_order,
    )


def asked_as_the_app(summary: PerformanceSummary) -> bool:
    """Return whether a run is the page the app would time.

    All three steps of the app's exchange, at the temperature, prompt, sampling and window the app
    sends the model, with the card to itself.  A temperature sweep, a prompt variant or a run that
    shared the card measured something else, and its page time says nothing about the app's.
    """
    profile = request_profile(summary.model)
    return (
        summary.isolated
        and set(summary.steps) == set(ALL_STEPS)
        and summary.exchange == DEFAULT_EXCHANGE
        and summary.temperature == profile.temperature
        and (summary.step_one_prompt or "standard") == (profile.correct.prompt or "standard")
        and summary.sampling == profile.correct.options
        and summary.num_ctx == BASE_NUM_CTX
    )


def _page_times(
    performance: Iterable[PerformanceSummary],
) -> dict[tuple[str, str], float]:
    """Return the slowest page time per machine and model of the runs the app would make."""
    times: dict[tuple[str, str], float] = {}
    for summary in performance:
        if not asked_as_the_app(summary) or summary.seconds_per_100_words is None:
            continue
        key = (summary.machine, summary.model)
        page = summary.seconds_per_100_words * TYPICAL_PAGE_WORDS / 100
        times[key] = max(page, times.get(key, 0.0))
    return times


def _blocks(
    sizes: Iterable[FitRecord],
    measured: Mapping[tuple[str, str], float],
    machines: Mapping[str, Machine],
) -> list[list[TierRow]]:
    """Return the rows of every tier, machine and device the records hold, one list per block."""
    grouped: dict[tuple[str, str], list[FitRecord]] = {}
    for record in sizes:
        grouped.setdefault((record.machine, record.device), []).append(record)
    for machine, _model in measured:
        grouped.setdefault((machine, _run_device(machine, machines)), [])
    blocks: list[list[TierRow]] = []
    for (machine, device), records in sorted(grouped.items()):
        tier = HardwareProfile.CPU if device == "cpu" else card_tier(machines.get(machine), records)
        if tier is None or (device == "gpu" and tier is HardwareProfile.CPU):
            _logger.warning(
                "no tier for %s (%s): its card is unknown or smaller than the 8 GB tier; left out",
                machine,
                device,
            )
            continue
        on_this_device = measured if device == _run_device(machine, machines) else {}
        blocks.append(_rows_of(tier, machine, device, records, on_this_device))
    return blocks


def _run_device(machine: str, machines: Mapping[str, Machine]) -> str:
    """Return the device a run on *machine* used: its card, or its processor when it has none."""
    known = machines.get(machine)
    if known is not None and not known.vram_mb:
        return "cpu"
    return "gpu"


def _rows_of(
    tier: HardwareProfile,
    machine: str,
    device: str,
    records: Sequence[FitRecord],
    measured: Mapping[tuple[str, str], float],
) -> list[TierRow]:
    """Return the rows of one tier, machine and device: the measured needs and page times."""
    taken = _at_window(records)
    models = sorted(set(taken) | {model for host, model in measured if host == machine})
    rows: list[TierRow] = []
    for model in models:
        record = taken.get(model)
        need = None if record is None else need_bytes(record) / GB
        page = measured.get((machine, model))
        rows.append(
            TierRow(
                tier=tier,
                machine=machine,
                device=device,
                model=model,
                need_gb=need,
                partly_in_memory=record is not None and spilled(record),
                fits=_fits(tier, model, need),
                seconds_per_page=page if page is not None else _computed_page(record),
                measured=page is not None,
            )
        )
    return rows


def _fits(tier: HardwareProfile, model: str, need_gb: float | None) -> bool | None:
    """Return whether the app runs *model* on *tier*, ``None`` when it cannot tell.

    A card is judged by what was measured on a card of that size.  A processor is judged by the
    model's size in the catalog, as the app does: the limit there is a speed, not the memory the
    processor needs, which is larger and includes its own buffers.
    """
    if tier is not HardwareProfile.CPU:
        return None if need_gb is None else need_gb <= limit_gb(tier)
    spec = find_spec(model)
    return None if spec is None else spec.memory_gb <= CPU_COMFORT_GB


def _at_window(records: Sequence[FitRecord]) -> dict[str, FitRecord]:
    """Return the measurement at the app's window per model, from the records of one device."""
    per_model: dict[str, list[FitRecord]] = {}
    for record in records:
        per_model.setdefault(record.tag, []).append(record)
    found: dict[str, FitRecord] = {}
    for model, group in per_model.items():
        at_window = [item for item in current_build(group) if item.num_ctx == BASE_NUM_CTX]
        if at_window:
            found[model] = at_window[-1]
    return found


def _computed_page(record: FitRecord | None) -> float | None:
    """Return the page time the measured writing rate of *record* gives, or ``None``.

    The fit measurement's request is one copy of a text and its answer, so its rate is the model's
    own; the exchange's reading and writing are assumed to run at that same rate.
    """
    rate = None if record is None else tokens_per_second(record)
    if rate is None:
        return None
    speed = ModelSpeed(read_tps=rate, write_tps=rate)
    page = proofreading_time(speed, TYPICAL_PAGE_WORDS, DEFAULT_EXCHANGE_COST, PROOFREADING_STEPS)
    return page.typical_s


def _row_order(row: TierRow) -> tuple:
    """Return the sort key of a row: its tier, then the models that fit and are quickest."""
    return (
        TIER_ORDER.index(row.tier),
        row.machine,
        row.device,
        row.fits is not True,
        row.seconds_per_page if row.seconds_per_page is not None else float("inf"),
        row.need_gb if row.need_gb is not None else float("inf"),
        row.model,
    )


def _figure(value: float | None, suffix: str, decimals: int = 0) -> str:
    """Return *value* with *suffix*, or a dash when there is none."""
    return "—" if value is None else f"{value:.{decimals}f} {suffix}"


def _fit_cell(row: TierRow) -> str:
    """Return the app's verdict for the row, with the figure it rests on."""
    if row.fits is None:
        return "—"
    verdict = "yes" if row.fits else "**no**"
    limit = limit_gb(row.tier)
    lower_bound = ", at least" if row.partly_in_memory else ""
    if row.tier is HardwareProfile.CPU:
        spec = find_spec(row.model)
        size = None if spec is None else spec.memory_gb
        return f"{verdict} (catalog {_figure(size, 'GB', 1)} of {limit:.1f} GB)"
    return f"{verdict} ({_figure(row.need_gb, 'GB', 1)}{lower_bound} of {limit:.1f} GB)"


def _block_title(row: TierRow) -> str:
    """Return the heading of a block: the tier, the machine and what the tier allows."""
    if row.tier is HardwareProfile.CPU:
        allowed = f"the app takes a model of at most {limit_gb(row.tier):.1f} GB (catalog size)"
    else:
        allowed = f"a model fits at {limit_gb(row.tier):.1f} GB or less"
    return f"### {_TIER_TITLE[row.tier]} — `{row.machine}` ({row.device}), {allowed}"


def tier_table(rows: Sequence[TierRow]) -> str:
    """Return the fit and speed table as markdown, one block per tier and machine.

    A dash marks a figure that could not be computed: nothing was measured on this machine, or the
    answer was too short to read a rate from.
    """
    if not rows:
        return "No size was measured: nothing to read for a tier."
    lines: list[str] = []
    current: tuple[HardwareProfile, str, str] | None = None
    for row in rows:
        block = (row.tier, row.machine, row.device)
        if block != current:
            current = block
            lines += [
                "",
                _block_title(row),
                "",
                f"| Model | Need at {BASE_NUM_CTX} | Fits | Page time | Band | From |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        lines.append(
            f"| `{row.model}` | {_figure(row.need_gb, 'GB', 1)} | {_fit_cell(row)} "
            f"| {_figure(row.seconds_per_page, 's')} "
            f"| {'—' if row.band is None else row.band} "
            f"| {'run' if row.measured else 'computed'} |"
        )
    lines += [
        "",
        "Rows: what the tier runs first, then the shortest page.  The need is the memory the "
        f"device held at the app's window ({BASE_NUM_CTX} tokens); on a card the tier runs the "
        'model when that stays within the share of the card the app gives it, and "at least" '
        "marks a model that ran partly in main memory, whose need is only a lower bound.  A "
        "computer without a card has no card to fill: there the app compares the model's size in "
        f"the catalog with {CPU_COMFORT_GB:.1f} GB, the size a processor still runs at a usable "
        "speed, so the need column is larger than the figure the verdict rests on.  The page is "
        f"{TYPICAL_PAGE_WORDS} words through the whole exchange; `run` is a full-exchange run on "
        "that machine as the app asks, `computed` the time the measured writing rate gives.  A "
        "page time belongs to one machine: two machines never share a row.",
    ]
    return "\n".join(lines)


__all__ = [
    "BYTES_PER_GIB",
    "TIER_ORDER",
    "TierRow",
    "asked_as_the_app",
    "card_tier",
    "limit_gb",
    "summarise_tiers",
    "tier_table",
]
