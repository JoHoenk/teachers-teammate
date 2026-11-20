"""What the texts cost: the speed and memory table, and the catalog's answer-length lines.

Quality decides which model to keep; this says what a teacher waits for it.  Only texts that were
**not shared** with another loaded model enter the figures: a second model on the card changes both
speed and memory, so a shared text measures the server and not the model.

The words are the manifest's count, marks included, so a figure here and the same figure in the
index mean the same thing.  ``-`` marks a figure that could not be computed: no comparable text, no
sampler, or a server that reports no durations.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ...core.axes import environment_key, host_of
from ...core.hashing import sha256_json
from ...core.modes import Exchange, asks_review
from ...core.performance import Spread, per_100_words, step_one_share
from ...metrics import text_loss
from ..load import LoadedItem
from ..pooled import mode_label


@dataclass(frozen=True)
class PerformanceSummary:
    """Speed and memory of one model on one machine, over the texts it was run on."""

    model: str
    temperature: float
    mode: str
    exchange: Exchange
    """How the text travelled, which decides whether the answer length is a figure per model."""
    machine: str
    """The machine the run measured: two machines measured the same quality and other speeds."""
    isolated: bool
    """Whether the run had the card to itself, which is what makes a speed figure comparable."""
    texts: int
    """Texts with a performance record."""

    comparable: int
    """Of those, the texts that were not shared with another model on the server."""

    seconds_per_100_words: float | None
    answer_tokens_per_s: float | None
    answer_tokens_spread: Spread | None
    """Answer tokens per 100 words of student text: 10th percentile, median, 90th."""

    requests_per_text: float | None
    step_one_share: float | None
    """Share of the tool's waiting time that step ① of the exchange took, over the comparable
    texts whose requests carry a step tag.  A correct-only run shows 100 %; ``None`` for review and
    text mode, which have no steps, and for rows written before the tag existed."""

    lost_text: int
    """Texts whose answer kept much less of the text than the reference has."""
    added_text: int
    """Texts whose answer added much more than the reference has."""
    loss_checked: int
    """Texts whose words were measured; 0 for a run written before they were stored."""

    vram_used_mb: int | None
    """Highest memory the model added on the card, over the comparable texts."""

    on_card_share: float | None
    """Lowest share of the model on the card, over the comparable texts."""


def summarise_performance(items: Sequence[LoadedItem]) -> list[PerformanceSummary]:
    """Return one performance summary per environment key: model, machine and isolation.

    The repeats of one setup on one machine pool, because a repeat is the same measurement made
    again.  Two machines do not: they measured the same quality and different speeds, and averaging
    them would name neither.  Only texts that were **not shared** with another loaded model enter
    the figures: a second model on the card changes both speed and memory.  Texts written before
    performance was recorded are skipped.
    """
    summaries: list[PerformanceSummary] = []
    for group in _by_environment(items):
        header = group[0].header
        model = header.protocol.model
        measured = [row for row in group if row.performance is not None]
        if not measured:
            continue
        clean = [row for row in measured if row.performance and not row.performance.shared]
        words = sum(row.words for row in clean)
        answer = sum(row.performance.answer_tokens for row in clean if row.performance)
        generation = sum(row.performance.generation_s for row in clean if row.performance)
        used = [
            row.performance.vram_used_mb
            for row in clean
            if row.performance and row.performance.vram_used_mb is not None
        ]
        shares = [
            share
            for row in clean
            if row.performance and (share := row.performance.on_card_share(model)) is not None
        ]
        summaries.append(
            PerformanceSummary(
                model=model,
                temperature=header.protocol.temperature,
                mode=mode_label(header.protocol),
                exchange=header.protocol.exchange,
                machine=host_of(header),
                isolated=header.environment.isolated,
                texts=len(measured),
                comparable=len(clean),
                seconds_per_100_words=(
                    per_100_words(sum(row.seconds for row in clean), words) if words else None
                ),
                answer_tokens_per_s=answer / generation if generation else None,
                answer_tokens_spread=Spread.of(
                    [
                        row.performance.answer_tokens / row.words * 100
                        for row in clean
                        if row.performance and row.words
                    ]
                ),
                requests_per_text=(
                    sum(row.performance.request_count for row in clean if row.performance)
                    / len(clean)
                    if clean
                    else None
                ),
                step_one_share=step_one_share(clean),
                lost_text=sum(1 for row in clean if text_loss.is_lost(row.scores)),
                added_text=sum(1 for row in clean if text_loss.is_added(row.scores)),
                loss_checked=sum(1 for row in clean if text_loss.is_lost(row.scores) is not None),
                vram_used_mb=max(used) if used else None,
                on_card_share=min(shares) if shares else None,
            )
        )
    return summaries


def _by_environment(items: Sequence[LoadedItem]) -> list[list[LoadedItem]]:
    """Return the texts grouped by environment key, the groups sorted by model and machine."""
    groups: dict[str, list[LoadedItem]] = {}
    for row in items:
        groups.setdefault(sha256_json(environment_key(row.header)), []).append(row)
    return sorted(
        groups.values(),
        key=lambda group: (
            group[0].header.protocol.model,
            group[0].header.protocol.temperature,
            mode_label(group[0].header.protocol),
            host_of(group[0].header),
            group[0].header.environment.isolated,
        ),
    )


def _spread_cell(spread: Spread | None) -> str:
    """Return the three answer-length figures of a row, or a dash when there are none."""
    if spread is None:
        return "-"
    low, typical, high = spread.rounded()
    return f"{low} / {typical} / {high}"


def performance_table(summaries: Iterable[PerformanceSummary]) -> str:
    """Return the speed and memory table as markdown.

    ``-`` marks a figure that could not be computed: no comparable text, no sampler, or a server
    that reports no durations.
    """

    def number(value: float | int | None, spec: str) -> str:
        return "-" if value is None else format(value, spec)

    lines = [
        "| Model | T | Mode | Machine | Texts (comparable) | s per 100 words | Answer tokens/s "
        "| Answer tokens per 100 words (10th / median / 90th) | Requests/text "
        "| Step ① share of time | Lost / added | Graphics memory MB | On the card |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in summaries:
        share = "-" if item.on_card_share is None else f"{item.on_card_share:.0%}"
        loss = "—" if not item.loss_checked else f"{item.lost_text} / {item.added_text}"
        lines.append(
            f"| `{item.model}` | {item.temperature:g} | {item.mode} | {_machine_cell(item)} "
            f"| {item.texts} ({item.comparable}) "
            f"| {number(item.seconds_per_100_words, '.1f')} "
            f"| {number(item.answer_tokens_per_s, '.0f')} "
            f"| {_spread_cell(item.answer_tokens_spread)} "
            f"| {number(item.requests_per_text, '.2f')} "
            f"| {'-' if item.step_one_share is None else format(item.step_one_share, '.0%')} "
            f"| {loss} "
            f"| {number(item.vram_used_mb, 'd')} | {share} |"
        )
    lines += [
        "",
        "Only texts that were not shared with another loaded model count.  Answer tokens/s "
        "says how fast the machine runs the model; seconds per 100 words of student text is "
        "what a teacher waits.  Graphics memory is the peak the model added over the idle baseline, "
        'read from the card.  "On the card" is Ollama\'s own estimate and is unreliable for some '
        "models (it reported 0.4 GB for a Gemma 4 model that added 3.4 GB): trust the memory "
        "column.",
    ]
    return "\n".join(lines)


def _machine_cell(item: PerformanceSummary) -> str:
    """Return the machine of a row, and whether it had the card to itself."""
    return f"{item.machine} (isolated)" if item.isolated else item.machine


def catalog_lines(summaries: Iterable[PerformanceSummary]) -> str:
    """Return the ``answer_length=`` lines to paste into the catalog, one per model.

    Only a review exchange has a catalog figure: the catalog's answer length is answer tokens per
    100 words of student text in review mode.  A text answer is the corrected text itself, so its
    length is one constant for every model and not a figure per model.
    """
    lines: list[str] = []
    for item in summaries:
        if not asks_review(item.exchange) or item.answer_tokens_spread is None:
            continue
        low, typical, high = item.answer_tokens_spread.rounded()
        lines.append(
            f"`{item.model}` at {item.temperature:g}: "
            f"answer_length=AnswerLength(low={low}, typical={typical}, high={high})"
        )
    if not lines:
        return "No review-mode text was measured: nothing to put in the catalog."
    lines += [
        "",
        "Answer tokens per 100 words of student text, proofreading in review mode, at the "
        "temperature named.  The catalog's docstring wants the run, the machine and the date.",
    ]
    return "\n".join(lines)


__all__ = [
    "PerformanceSummary",
    "catalog_lines",
    "performance_table",
    "summarise_performance",
]
