"""What a model really needs: measured once, decided later.

Ollama's ``/api/ps`` is Ollama's own bookkeeping, not a reading of the card: on the 8 GB laptop it
reported about 1 GB for ``gemma4:12b`` while ``nvidia-smi`` showed 6.9 GB.  So a measurement stores
two readings side by side — what Ollama says and what the card really holds — and nothing derived.
The verdict (does this model fit a 16 GB card?) is computed when it is needed, from the stored
bytes, so changing the rule costs no measuring.

A measurement costs one model load and one short request, so every step of the ladder is measured:
``8192``, ``16384`` and ``32768`` tokens.  The records live in
``evaluations/model-fit/sizes.jsonl``, append-only, numbers only.

The **need** of a measurement is ``max(ollama size, card increase)``: Ollama's figure leaves out the
driver's and the runtime's share, the card's increase cannot exceed the physical card.  Taking the
larger is cautious in both directions.  A step that spills onto the processor is flagged, because
its need is only a lower bound.

Everything here is pure apart from :class:`FitStore`'s file I/O, so a test can feed recorded
readings; only :func:`measure_tag` needs a server and an agent.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

from teachers_teammate.domain.context_budget import BASE_NUM_CTX, CONTEXT_STEPS
from teachers_teammate.domain.model_catalog import (
    FULL_SPEED_SHARE,
    card_budget_gb,
    card_share,
    request_profile,
)

GB = 1e9
"""Ollama reports bytes; the catalog and the docs speak of GB (10^9), as the download sizes do."""

SCHEMA = 2
"""The version of the record format; a record of another version is refused, not guessed at.

Version 2 added the speed of the request (``answer_tokens`` over ``generation_seconds``)."""

CPU_ONLY_OPTIONS: dict[str, Any] = {"num_gpu": 0}
"""The Ollama option that keeps the whole model on the processor: a CPU-only measurement."""

CARD_USED_TOLERANCE_BYTES = 300_000_000
"""A CPU-only run that raises the card's use by more than this did not stay off the card."""

MIN_SPEED_TOKENS = 20
"""An answer shorter than this says nothing about how fast the model writes."""

MEMORY_TOLERANCE = 0.10
"""How far ``size@8k`` may be from the catalog's ``memory_gb`` before the report flags it."""

LINEARITY_TOLERANCE = 0.20
"""How far the two growth figures may differ before the report calls the growth non-linear."""

SPILL_SHARE = 0.98
"""A card peak this close to its total means the model did not fit: the rest is in main memory."""

DEFAULT_STORE = Path("evaluations/model-fit/sizes.jsonl")
"""Where the measurements are kept, relative to the workspace root."""

DEFAULT_LIMITS = ("8gb", "16gb")
"""The tiers a report shows unless other limits are named."""

PROMPT = (
    "Schreibe den folgenden Text unverändert ab.\n\n"
    "Am Montagmorgen packte Lena ihre Tasche und ging zur Schule. Unterwegs traf sie ihren "
    "Freund Paul, der ihr von seinem Wochenende erzählte. Sie hatten zusammen ein Fußballspiel "
    "gesehen, und Paul war immer noch begeistert davon. In der ersten Stunde schrieb die Klasse "
    "einen Test über die Verben, und danach las die Lehrerin eine kurze Geschichte vor. Am "
    "Nachmittag half Lena ihrer Mutter im Garten, und am Abend schrieb sie ihrer Oma einen "
    "langen Brief über die ganze Woche."
)
"""The one request per step: it loads the model, and its answer (about 170 tokens, a copy of the
text) is long enough to read the speed of the model from."""


class FitRecordError(ValueError):
    """A stored record cannot be read: another schema, or a field that is not there."""


class FitMeasureError(RuntimeError):
    """One step of one model could not be measured; the next model can still be."""


@dataclass(frozen=True)
class OllamaReading:
    """What Ollama's ``/api/ps`` says about the loaded model."""

    context_length: int
    """The window Ollama really loaded; it can be smaller than the one asked for."""

    size_bytes: int
    """All the model needs: weights, context and working buffers, on the card and in main memory."""

    size_vram_bytes: int
    """The part of it Ollama put on the graphics card."""


@dataclass(frozen=True)
class CardReading:
    """What the memory agent read from the graphics card around the load and the request."""

    agent_sha256: str
    source: str
    total_bytes: int
    idle_bytes: int
    """The settled figure before the load: the card's use with nothing of ours on it."""

    peak_bytes: int
    """The highest sample while the window was open."""

    end_bytes: int
    """The sample at close, with the model still loaded."""


@dataclass(frozen=True)
class RamReading:
    """What the agent read from the main memory around the load and the request."""

    total_bytes: int
    idle_available_bytes: int
    min_available_bytes: int


@dataclass(frozen=True)
class FitRecord:
    """One measurement: one model, one window, one server."""

    measured_at: str
    server: str
    ollama_version: str
    tool_commit: str
    tag: str
    digest: str
    num_ctx: int
    answer_tokens: int
    """How many tokens the request's answer had (Ollama's ``eval_count``)."""

    generation_seconds: float
    """How long the model took to write them (Ollama's ``eval_duration``), load excluded."""

    options: dict[str, Any] = field(default_factory=dict)
    load_seconds: float = 0.0
    ollama: OllamaReading | None = None
    card: CardReading | None = None
    ram: RamReading | None = None
    schema: int = SCHEMA

    def to_json(self) -> dict[str, Any]:
        """Return the record as the JSON object one line of the store holds."""
        return {
            "schema": self.schema,
            "measured_at": self.measured_at,
            "server": self.server,
            "ollama_version": self.ollama_version,
            "tool_commit": self.tool_commit,
            "tag": self.tag,
            "digest": self.digest,
            "num_ctx": self.num_ctx,
            "answer_tokens": self.answer_tokens,
            "generation_seconds": self.generation_seconds,
            "options": dict(self.options),
            "load_seconds": self.load_seconds,
            "ollama": None if self.ollama is None else _ollama_json(self.ollama),
            "card": None if self.card is None else _card_json(self.card),
            "ram": None if self.ram is None else _ram_json(self.ram),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> FitRecord:
        """Return the record *data* describes.

        Raises:
            FitRecordError: the record is of another schema, or a field it needs is missing.
        """
        schema = data.get("schema")
        if schema != SCHEMA:
            raise FitRecordError(
                f"record schema {schema!r} is not {SCHEMA}: written by another version of the tool"
            )
        try:
            return cls(
                measured_at=str(data["measured_at"]),
                server=str(data["server"]),
                ollama_version=str(data["ollama_version"]),
                tool_commit=str(data["tool_commit"]),
                tag=str(data["tag"]),
                digest=str(data["digest"]),
                num_ctx=int(data["num_ctx"]),
                answer_tokens=int(data["answer_tokens"]),
                generation_seconds=float(data["generation_seconds"]),
                options=dict(data.get("options") or {}),
                load_seconds=float(data.get("load_seconds") or 0.0),
                ollama=_ollama_from(data.get("ollama")),
                card=_card_from(data.get("card")),
                ram=_ram_from(data.get("ram")),
                schema=SCHEMA,
            )
        except KeyError as missing:
            raise FitRecordError(f"the record has no {missing.args[0]!r}") from missing


def _ollama_json(reading: OllamaReading) -> dict[str, Any]:
    """Return *reading* as the JSON object of a record."""
    return {
        "context_length": reading.context_length,
        "size_bytes": reading.size_bytes,
        "size_vram_bytes": reading.size_vram_bytes,
    }


def _card_json(reading: CardReading) -> dict[str, Any]:
    """Return *reading* as the JSON object of a record."""
    return {
        "agent_sha256": reading.agent_sha256,
        "source": reading.source,
        "total_bytes": reading.total_bytes,
        "idle_bytes": reading.idle_bytes,
        "peak_bytes": reading.peak_bytes,
        "end_bytes": reading.end_bytes,
    }


def _ram_json(reading: RamReading) -> dict[str, Any]:
    """Return *reading* as the JSON object of a record."""
    return {
        "total_bytes": reading.total_bytes,
        "idle_available_bytes": reading.idle_available_bytes,
        "min_available_bytes": reading.min_available_bytes,
    }


def _ollama_from(data: dict[str, Any] | None) -> OllamaReading | None:
    """Return the Ollama reading of a stored record, or ``None``."""
    if not data:
        return None
    return OllamaReading(
        context_length=int(data.get("context_length", 0)),
        size_bytes=int(data.get("size_bytes", 0)),
        size_vram_bytes=int(data.get("size_vram_bytes", 0)),
    )


def _card_from(data: dict[str, Any] | None) -> CardReading | None:
    """Return the card reading of a stored record, or ``None``."""
    if not data:
        return None
    return CardReading(
        agent_sha256=str(data.get("agent_sha256", "")),
        source=str(data.get("source", "")),
        total_bytes=int(data.get("total_bytes", 0)),
        idle_bytes=int(data.get("idle_bytes", 0)),
        peak_bytes=int(data.get("peak_bytes", 0)),
        end_bytes=int(data.get("end_bytes", 0)),
    )


def _ram_from(data: dict[str, Any] | None) -> RamReading | None:
    """Return the main-memory reading of a stored record, or ``None``."""
    if not data:
        return None
    return RamReading(
        total_bytes=int(data.get("total_bytes", 0)),
        idle_available_bytes=int(data.get("idle_available_bytes", 0)),
        min_available_bytes=int(data.get("min_available_bytes", 0)),
    )


# ── what a record says ───────────────────────────────────────────────────────


def card_increase_bytes(record: FitRecord) -> int | None:
    """Return how far the card's use rose above its idle figure, or ``None`` without a reading."""
    if record.card is None:
        return None
    return max(0, record.card.peak_bytes - record.card.idle_bytes)


def ram_drop_bytes(record: FitRecord) -> int | None:
    """Return how far the available main memory fell, or ``None`` without a reading."""
    if record.ram is None:
        return None
    return max(0, record.ram.idle_available_bytes - record.ram.min_available_bytes)


def is_cpu_only(record: FitRecord) -> bool:
    """Return whether the request kept the model off the card (``num_gpu`` 0), to read its speed.

    Such a record says how fast the processor runs the model.  It is no size measurement: the
    card's share is missing from the need, so the size lookups never use it.
    """
    return record.options.get("num_gpu") == 0


def spilled(record: FitRecord) -> bool:
    """Return whether the model did not fit the card, so its need is only a lower bound.

    Two signs: Ollama itself reports part of the model in main memory, or the card's peak sits at
    its total, which means something had to go elsewhere.
    """
    if is_cpu_only(record):
        return False
    if record.ollama is not None and record.ollama.size_vram_bytes < record.ollama.size_bytes:
        return True
    return (
        record.card is not None
        and record.card.total_bytes > 0
        and record.card.peak_bytes >= SPILL_SHARE * record.card.total_bytes
    )


def need_bytes(record: FitRecord) -> int:
    """Return what the model needs: the larger of Ollama's size and the card's increase.

    Ollama's figure leaves out the driver's and the runtime's share; the card's increase cannot
    exceed the physical card.  Taking the larger is cautious in both directions.  Without a card
    reading, Ollama's figure is all there is.
    """
    size = record.ollama.size_bytes if record.ollama is not None else 0
    increase = card_increase_bytes(record)
    return size if increase is None else max(size, increase)


def tokens_per_second(record: FitRecord) -> float | None:
    """Return how fast the model wrote its answer, or ``None`` when the answer was too short."""
    if record.answer_tokens < MIN_SPEED_TOKENS or record.generation_seconds <= 0:
        return None
    return record.answer_tokens / record.generation_seconds


def ollama_card_share(record: FitRecord) -> float | None:
    """Return the share of the model Ollama itself put on the card (what sets the speed).

    This is Ollama's split between card and processor, not a reading of the card: it is the
    figure the app's full-speed rule is about.  ``None`` without an Ollama reading.
    """
    if is_cpu_only(record) or record.ollama is None or record.ollama.size_bytes <= 0:
        return None
    return card_share(record.ollama.size_bytes, record.ollama.size_vram_bytes)


def on_card_share(record: FitRecord) -> float | None:
    """Return the share of the need that sits on the card, or ``None`` without a reading."""
    need = need_bytes(record)
    increase = card_increase_bytes(record)
    if increase is None or need <= 0:
        return None
    return min(1.0, increase / need)


def ollama_off_by(record: FitRecord) -> float | None:
    """Return how far Ollama's size is from the card's increase, relative to the card.

    ``None`` without a card reading, or when the card's increase is zero.  A large figure is the
    point of the whole exercise: it says where Ollama's bookkeeping cannot be trusted.
    """
    increase = card_increase_bytes(record)
    if increase is None or increase <= 0 or record.ollama is None:
        return None
    return abs(record.ollama.size_bytes - increase) / increase


# ── the store ────────────────────────────────────────────────────────────────


class FitStore:
    """The measurements, one JSON object per line, append-only."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def records(self) -> tuple[FitRecord, ...]:
        """Return every stored record, oldest first; an absent file holds none."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ()
        return tuple(
            FitRecord.from_json(json.loads(line)) for line in text.splitlines() if line.strip()
        )

    def append(self, record: FitRecord) -> None:
        """Add *record* as one line, creating the file and its folder when they are not there."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_json(), sort_keys=True) + "\n")

    def has(
        self,
        *,
        server: str,
        tag: str,
        digest: str,
        num_ctx: int,
        ollama_version: str,
        cpu_only: bool = False,
    ) -> bool:
        """Return whether this build was already measured at this window on this server.

        A CPU-only measurement and a normal one are different measurements of the same build.
        """
        return any(
            is_cpu_only(record) == cpu_only
            and record.server == server
            and record.tag == tag
            and record.digest == digest
            and record.num_ctx == num_ctx
            and record.ollama_version == ollama_version
            for record in self.records()
        )


# ── reading the store ────────────────────────────────────────────────────────


def latest(
    records: Iterable[FitRecord], *, tag: str, digest: str, num_ctx: int
) -> dict[str, FitRecord]:
    """Return the newest record per server for one build at one window.

    CPU-only records are left out: they have no card share, so their size is no fit figure.
    """
    newest: dict[str, FitRecord] = {}
    for record in records:
        if is_cpu_only(record):
            continue
        if record.tag != tag or record.digest != digest or record.num_ctx != num_ctx:
            continue
        known = newest.get(record.server)
        if known is None or record.measured_at > known.measured_at:
            newest[record.server] = record
    return newest


def size_for_decision(
    records: Iterable[FitRecord], *, tag: str, digest: str, num_ctx: int
) -> FitRecord | None:
    """Return the record to decide with: the largest need among the servers' latest records.

    Two backends that disagree give the cautious answer, and ``None`` means nothing was measured.
    """
    candidates = latest(records, tag=tag, digest=digest, num_ctx=num_ctx)
    if not candidates:
        return None
    return max(candidates.values(), key=need_bytes)


def current_build(records: Iterable[FitRecord]) -> tuple[FitRecord, ...]:
    """Return the measurements that describe one model's current build, one per window.

    *records* are those of one model on one server.  The store is append-only, so it also holds
    older builds (another digest, after the tag was re-pointed) and repeated measurements
    (``--again``, another Ollama version).  The build of the newest record counts, and of it the
    newest record per window; mixing builds would give growth figures that belong to no model.
    Sorted by window.
    """
    everything = list(records)
    if not everything:
        return ()
    digest = max(everything, key=lambda record: record.measured_at).digest
    newest: dict[int, FitRecord] = {}
    for record in everything:
        if record.digest != digest:
            continue
        known = newest.get(record.num_ctx)
        if known is None or record.measured_at >= known.measured_at:
            newest[record.num_ctx] = record
    return tuple(newest[num_ctx] for num_ctx in sorted(newest))


def growth_per_8k(records: Sequence[FitRecord]) -> float | None:
    """Return the growth (GB) per 8192 tokens of one model on one server.

    Only steps that did not spill count: a spilled step's need is a lower bound, so its growth
    would be one too.  ``None`` when fewer than two such steps were measured.
    """
    usable = [record for record in current_build(records) if not spilled(record)]
    if len(usable) < 2:
        return None
    base, largest = usable[0], usable[-1]
    span = (largest.num_ctx - base.num_ctx) / BASE_NUM_CTX
    if span <= 0:
        return None
    return (need_bytes(largest) - need_bytes(base)) / GB / span


@dataclass(frozen=True)
class GrowthFigures:
    """What the measurements of one model say, and what is wrong with them."""

    size_at_base_gb: float | None
    """The need at :data:`BASE_NUM_CTX`, to compare with the catalog's ``memory_gb``."""

    growth_per_8k_gb: float | None
    """Growth per 8192 tokens from the base window to the largest step measured."""

    growth_per_8k_short_gb: float | None
    """The same from the base window to the next step, to check that growth is linear."""

    memory_off_by: float | None
    """Relative difference between the need at 8k and the catalog, ``None`` without a value."""

    spilled: bool
    """Whether any step ran partly in main memory, which spoils its figure."""

    flags: tuple[str, ...]
    """What a human has to know before entering the figure in the catalog."""


def _record_at(records: Sequence[FitRecord], num_ctx: int) -> FitRecord | None:
    """Return the measurement at *num_ctx*, or ``None`` when it was not taken."""
    return next((record for record in records if record.num_ctx == num_ctx), None)


def _growth_between(base: FitRecord, other: FitRecord) -> float | None:
    """Return the growth per 8192 tokens between two measurements, ``None`` without a span."""
    span = (other.num_ctx - base.num_ctx) / BASE_NUM_CTX
    if span <= 0:
        return None
    return (need_bytes(other) - need_bytes(base)) / GB / span


def growth_figures(
    records: Sequence[FitRecord], catalog_memory_gb: float | None = None
) -> GrowthFigures:
    """Return the growth figures of one model, and what is wrong with them.

    *records* are the measurements of one model on one server, one per context window;
    *catalog_memory_gb* is the catalog's ``memory_gb`` for it, which was measured at
    :data:`BASE_NUM_CTX` too.  A missing base measurement leaves the growth figures ``None``:
    without it there is nothing to grow from.
    """
    records = current_build(records)
    base = _record_at(records, BASE_NUM_CTX)
    larger = sorted(
        (record for record in records if record.num_ctx > BASE_NUM_CTX),
        key=lambda r: r.num_ctx,
    )
    short = larger[0] if larger else None
    largest = larger[-1] if larger else None

    size_at_base = need_bytes(base) / GB if base is not None else None
    growth = _growth_between(base, largest) if base is not None and largest is not None else None
    growth_short = _growth_between(base, short) if base is not None and short is not None else None
    off_by = (
        abs(size_at_base - catalog_memory_gb) / catalog_memory_gb
        if size_at_base is not None and catalog_memory_gb
        else None
    )

    flags: list[str] = []
    if off_by is not None and off_by > MEMORY_TOLERANCE:
        flags.append(
            f"size at {BASE_NUM_CTX:,} tokens is {size_at_base:.2f} GB, the catalog says "
            f"{catalog_memory_gb:.2f} GB ({off_by * 100:.0f} % off)"
        )
    if (
        growth is not None
        and growth_short is not None
        and short is not None
        and largest is not None
    ):
        spread = abs(growth - growth_short) / max(abs(growth), abs(growth_short), 1e-9)
        if spread > LINEARITY_TOLERANCE:
            flags.append(
                f"growth is not linear: {growth_short:.2f} GB per 8k up to "
                f"{short.num_ctx:,} tokens, {growth:.2f} GB per 8k up to {largest.num_ctx:,}"
            )
    spilled_steps = [record for record in records if spilled(record)]
    if spilled_steps:
        windows = ", ".join(f"{record.num_ctx:,}" for record in spilled_steps)
        flags.append(f"partly in main memory at {windows} tokens: the need is a lower bound")

    return GrowthFigures(
        size_at_base_gb=size_at_base,
        growth_per_8k_gb=growth,
        growth_per_8k_short_gb=growth_short,
        memory_off_by=off_by,
        spilled=bool(spilled_steps),
        flags=tuple(flags),
    )


# ── the limit ────────────────────────────────────────────────────────────────


_TIER_BUDGET_GB: dict[str, float] = {
    "8gb": card_budget_gb("8gb"),
    "16gb": card_budget_gb("16gb"),
}
"""The card sizes the app has a rule for; a limit of one of them is the app's own rule."""


@dataclass(frozen=True)
class FitLimit:
    """The largest need a model may have, as the app's own rule for a card size."""

    text: str
    max_size_gb: float | None
    """The largest need allowed in GB, or ``None`` when the check is off."""

    @classmethod
    def parse(cls, text: str) -> FitLimit:
        """Return the limit *text* names: a tier (``8gb``, ``16gb``), a number, or ``off``.

        Raises:
            ValueError: *text* is none of those, or a number that is not positive.
        """
        cleaned = text.strip().lower()
        if cleaned == "off":
            return cls(text=cleaned, max_size_gb=None)
        if cleaned in _TIER_BUDGET_GB:
            return cls(text=cleaned, max_size_gb=_TIER_BUDGET_GB[cleaned] / FULL_SPEED_SHARE)
        try:
            number = float(cleaned)
        except ValueError as error:
            raise ValueError(
                f"{text!r} is not a limit: pass 8gb, 16gb, a number of GB, or off"
            ) from error
        if number <= 0:
            raise ValueError(f"{text!r} is not a limit: a size has to be positive")
        return cls(text=cleaned, max_size_gb=number)

    @property
    def label(self) -> str:
        """Return how the limit is written in a report."""
        if self.max_size_gb is None:
            return "off"
        if self.text in _TIER_BUDGET_GB:
            return self.text
        return f"{self.max_size_gb:g} GB"


def within(record: FitRecord, limit: FitLimit) -> bool:
    """Return whether the model's need is within *limit*."""
    if limit.max_size_gb is None:
        return True
    return need_bytes(record) / GB <= limit.max_size_gb


def tier_windows(records: Sequence[FitRecord], limits: Sequence[FitLimit]) -> dict[str, int | None]:
    """Return the largest window whose need is within each limit, per limit label."""
    windows: dict[str, int | None] = {}
    for limit in limits:
        fitting = [record.num_ctx for record in records if within(record, limit)]
        windows[limit.label] = max(fitting) if fitting else None
    return windows


# ── printing ─────────────────────────────────────────────────────────────────


def _cpu_parts(record: FitRecord) -> list[str]:
    """Return the report pieces of a CPU-only measurement: the speed, the memory, a warning."""
    size = record.ollama.size_bytes / GB if record.ollama is not None else 0.0
    speed = tokens_per_second(record)
    parts = [
        "CPU only",
        "speed n/a" if speed is None else f"{speed:5.1f} tokens/s",
        f"{record.answer_tokens} answer tokens",
        f"Ollama {size:6.2f} GB",
    ]
    drop = ram_drop_bytes(record)
    if drop is not None:
        parts.append(f"main memory -{drop / GB:.2f} GB")
    increase = card_increase_bytes(record)
    if increase is not None and increase > CARD_USED_TOLERANCE_BYTES:
        parts.append(f"CARD USED +{increase / GB:.2f} GB: num_gpu 0 did not keep it off the card")
    return parts


def _cpu_line(label: str, record: FitRecord) -> str:
    """Return the line of a CPU-only measurement: *label*, then its report pieces."""
    return "  ".join([label, *_cpu_parts(record)])


def record_line(record: FitRecord) -> str:
    """Return the one line that reports a measurement just made."""
    if is_cpu_only(record):
        return _cpu_line(f"{record.tag} at {record.num_ctx:>6,} tokens:", record)
    need = need_bytes(record) / GB
    size = record.ollama.size_bytes / GB if record.ollama is not None else 0.0
    increase = card_increase_bytes(record)
    parts = [
        f"{record.tag} at {record.num_ctx:>6,} tokens: needs {need:6.2f} GB",
        f"Ollama says {size:6.2f} GB",
    ]
    if increase is None:
        parts.append("no card reading")
    else:
        off_by = ollama_off_by(record)
        parts.append(f"the card holds {increase / GB:6.2f} GB")
        if off_by is not None:
            parts.append(f"{off_by * 100:5.0f} % apart")
        share = on_card_share(record)
        if share is not None:
            parts.append(f"{share * 100:3.0f} % on the card")
    parts.extend(_speed_parts(record))
    parts.append(f"load {record.load_seconds:.1f} s")
    if spilled(record):
        parts.append("SPILLED")
    return "  ".join(parts)


def _speed_parts(record: FitRecord) -> list[str]:
    """Return the report pieces for Ollama's split and the speed of one measurement."""
    parts: list[str] = []
    split = ollama_card_share(record)
    if split is not None:
        parts.append(f"Ollama puts {split * 100:3.0f} % on the card")
    speed = tokens_per_second(record)
    parts.append("speed n/a" if speed is None else f"{speed:5.1f} tokens/s")
    return parts


def report_lines(
    tag: str,
    records: Sequence[FitRecord],
    *,
    catalog_memory_gb: float | None = None,
    limits: Sequence[FitLimit] = (),
) -> list[str]:
    """Return the block that reports one model's measurements on one server."""
    records = current_build(records)
    if not records:
        return [f"{tag}: no measurement"]
    server = records[0].server
    if all(is_cpu_only(record) for record in records):
        return [f"{tag} on {server}:"] + [
            _cpu_line(f"  {record.num_ctx:>6,} tokens:", record) for record in records
        ]
    lines = [f"{tag} on {server}:"]
    for record in records:
        need = need_bytes(record) / GB
        size = record.ollama.size_bytes / GB if record.ollama is not None else 0.0
        increase = card_increase_bytes(record)
        off_by = ollama_off_by(record)
        drop = ram_drop_bytes(record)
        share = on_card_share(record)
        parts = [
            f"  {record.num_ctx:>6,} tokens: need {need:6.2f} GB",
            f"Ollama {size:6.2f} GB",
            "no card reading" if increase is None else f"card {increase / GB:6.2f} GB",
        ]
        if off_by is not None:
            parts.append(f"{off_by * 100:5.0f} % apart")
        if share is not None:
            parts.append(f"{share * 100:3.0f} % on the card")
        parts.extend(_speed_parts(record))
        if drop is not None:
            parts.append(f"main memory -{drop / GB:.2f} GB")
        if spilled(record):
            parts.append("SPILLED")
        lines.append("  ".join(parts))
    figures = growth_figures(records, catalog_memory_gb)
    if figures.growth_per_8k_short_gb is not None:
        lines.append(f"  growth per 8k (short span): {figures.growth_per_8k_short_gb:.2f} GB")
    if figures.growth_per_8k_gb is not None:
        lines.append(f"  growth per 8k (long span):  {figures.growth_per_8k_gb:.2f} GB")
    lines.extend(f"  ! {flag}" for flag in figures.flags)
    if limits:
        windows = tier_windows(records, limits)
        for limit in limits:
            window = windows.get(limit.label)
            where = "no window fits" if window is None else f"up to {window:,} tokens"
            lines.append(f"  limit {limit.label}: {where}")
    return lines


def compare_lines(tag: str, first: Sequence[FitRecord], second: Sequence[FitRecord]) -> list[str]:
    """Return the block that puts two servers' measurements of one model side by side.

    The agreement rule is the one the catalog's cross-check uses: the need at 8192 tokens within
    :data:`MEMORY_TOLERANCE`, the growth within 20 % or 0.1 GB.  A window where either machine
    spilled is shown but not compared: part of the model sat in main memory there, so the need is
    a lower bound and a gap says nothing about the two backends.
    """
    first, second = current_build(first), current_build(second)
    left = {record.num_ctx: record for record in first}
    right = {record.num_ctx: record for record in second}
    left_server = first[0].server if first else "?"
    right_server = second[0].server if second else "?"
    lines = [f"{tag}: {left_server} vs {right_server}"]
    if any(is_cpu_only(record) for record in (*first, *second)):
        for num_ctx in sorted(set(left) & set(right)):
            speeds = [tokens_per_second(left[num_ctx]), tokens_per_second(right[num_ctx])]
            shown = ["n/a" if speed is None else f"{speed:5.1f}" for speed in speeds]
            lines.append(f"  {num_ctx:>6,} tokens: {shown[0]} vs {shown[1]} tokens/s")
        return lines
    for num_ctx in sorted(set(left) | set(right)):
        one, other = left.get(num_ctx), right.get(num_ctx)
        if one is None or other is None:
            lines.append(f"  {num_ctx:>6,} tokens: measured on one server only")
            continue
        one_need, other_need = need_bytes(one) / GB, need_bytes(other) / GB
        spilled_on = [
            record.server for record in (one, other) if spilled(record)
        ]  # a spilled step's need is only a lower bound: comparing it would report a false split
        if spilled_on:
            lines.append(
                f"  {num_ctx:>6,} tokens: {one_need:6.2f} GB vs {other_need:6.2f} GB "
                f"(not compared: spilled on {' and '.join(spilled_on)}, the need is a lower bound)"
            )
            continue
        off_by = abs(one_need - other_need) / max(one_need, other_need, 1e-9)
        mark = "ok" if off_by <= MEMORY_TOLERANCE else "DISAGREE"
        lines.append(
            f"  {num_ctx:>6,} tokens: {one_need:6.2f} GB vs {other_need:6.2f} GB "
            f"({off_by * 100:4.0f} % apart, {mark})"
        )
    one_growth = growth_per_8k(first)
    other_growth = growth_per_8k(second)
    if one_growth is not None and other_growth is not None:
        spread = abs(one_growth - other_growth)
        mark = "ok" if spread <= max(0.2 * max(one_growth, other_growth), 0.1) else "DISAGREE"
        lines.append(
            f"  growth per 8k: {one_growth:.2f} GB vs {other_growth:.2f} GB "
            f"({spread:.2f} GB apart, {mark})"
        )
    return lines


def catalog_lines(
    tag: str, records: Sequence[FitRecord], catalog_memory_gb: float | None = None
) -> list[str]:
    """Return the figures to enter in the catalog for one model, taking the cautious value.

    *records* may hold the measurements of more than one server: two backends that disagree give
    the larger figure, because the model has to fit on the machine that needs more.
    """
    records = [record for record in records if not is_cpu_only(record)]
    if not records:
        return [f"{tag}: no measurement"]
    grouped: dict[str, list[FitRecord]] = {}
    for record in records:
        grouped.setdefault(record.server, []).append(record)
    by_server = {server: current_build(group) for server, group in grouped.items()}
    bases = [
        base
        for base in (_record_at(group, BASE_NUM_CTX) for group in by_server.values())
        if base is not None
    ]
    growths = [
        growth
        for growth in (growth_per_8k(group) for group in by_server.values())
        if growth is not None
    ]
    memory = max(need_bytes(base) / GB for base in bases) if bases else None
    growth = max(growths) if growths else None
    lines = [f"{tag}:"]
    if memory is None:
        lines.append(f"  memory_gb: not measured at {BASE_NUM_CTX:,} tokens")
    else:
        note = ""
        if catalog_memory_gb is not None and catalog_memory_gb:
            off_by = abs(memory - catalog_memory_gb) / catalog_memory_gb
            note = f"  (the catalog says {catalog_memory_gb:.2f} GB, {off_by * 100:.0f} % off)"
        lines.append(f"  memory_gb: {memory:.2f}{note}")
    if growth is None:
        lines.append("  context_gb_per_8k: not enough steps without a spill")
    else:
        lines.append(f"  context_gb_per_8k: {growth:.2f}")
    return lines


# ── the screen's line ────────────────────────────────────────────────────────


def _allowed_gb(limit: FitLimit) -> float:
    """Return the limit in GB; a limit that is off allows everything."""
    return math.inf if limit.max_size_gb is None else limit.max_size_gb


def fit_source(record: FitRecord) -> str:
    """Return where a record's figure comes from: the card, or Ollama alone."""
    return "card reading" if record.card is not None else "Ollama's size only"


def fit_line(record: FitRecord, limit: FitLimit) -> str:
    """Return the one line a screen prints for a model that fits the limit.

    The bracket says where the figure comes from: the card's increase, with Ollama's own figure
    next to it, or Ollama's figure alone when no agent was asked.
    """
    need = need_bytes(record) / GB
    if record.card is None or record.ollama is None:
        origin = "Ollama's size only"
    else:
        origin = f"Ollama says {record.ollama.size_bytes / GB:.2f}"
    return (
        f"fit: needs {need:.2f} GB at {record.num_ctx:,} tokens "
        f"({record.server}; {origin}), limit {_allowed_gb(limit):.2f} GB ({limit.label})"
    )


def fit_failure(record: FitRecord, limit: FitLimit) -> str:
    """Return why a model does not fit the limit, as the smoke check's reason.

    The date and the source are named because the figure is one measurement of one build on one
    machine: a reader has to be able to find the line it came from.
    """
    need = need_bytes(record) / GB
    return (
        f"needs {need:.2f} GB at {record.num_ctx:,} tokens "
        f"({record.server}, {record.measured_at[:10]}, {fit_source(record)}); "
        f"limit {_allowed_gb(limit):.2f} GB ({limit.label})"
    )


# ── measuring ────────────────────────────────────────────────────────────────


def measure_tag(  # noqa: PLR0913  # one keyword per thing a measurement needs
    *,
    probe: Any,
    client: Any,
    agent: Any,
    store: FitStore,
    server: str,
    tag: str,
    temperature: float,
    agent_info: Any = None,
    steps: Sequence[int] = CONTEXT_STEPS,
    again: bool = False,
    timeout: float = 600.0,
    clock: Callable[[], datetime] | None = None,
    cpu_only: bool = False,
) -> tuple[FitRecord, ...]:
    """Measure *tag* at every window of *steps* and append what is new to *store*.

    Each step starts from an idle server, so the model is loaded at exactly the window asked for
    and nothing else is in memory.  The request is the app's own proofreading request, so the
    window measured is the window the app would use.

    Returns the records this call made.  A step that is already stored is skipped, unless *again*.
    With *cpu_only* the request carries ``num_gpu`` 0, so the model runs on the processor alone
    and the record says how fast that is; the card's reading then shows that it stayed unused.

    Raises:
        FitMeasureError: the request failed, so this model cannot be measured further.
        AgentError: the agent stopped answering; the caller stops, because a record without its
            card reading would be silently weaker.
    """
    from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
        proofreading_options,
    )

    from .measure import same_model, tool_revision  # noqa: PLC0415

    now = clock or (lambda: datetime.now(UTC))
    commit = tool_revision()
    digest = str(probe.model_info(tag).get("digest", ""))
    version = probe.version()
    made: list[FitRecord] = []
    for num_ctx in steps:
        if not again and store.has(
            server=server,
            tag=tag,
            digest=digest,
            num_ctx=num_ctx,
            ollama_version=version,
            cpu_only=cpu_only,
        ):
            continue
        options = {
            **proofreading_options(temperature, num_ctx),
            **(CPU_ONLY_OPTIONS if cpu_only else {}),
        }
        probe.unload_all()
        idle = agent.settle() if agent is not None else None
        window = agent.open_window() if agent is not None else None
        closed = None
        measured = False
        try:
            try:
                _answer, stats = client.chat_with_stats(
                    tag,
                    PROMPT,
                    options=options,
                    think=request_profile(tag).think,
                    timeout=timeout,
                )
            except Exception as error:  # any failure means this model is not measurable
                raise FitMeasureError(f"{tag} at {num_ctx:,} tokens: {error}") from error
            loaded = next((model for model in probe.loaded() if same_model(model.name, tag)), None)
            if loaded is None:
                raise FitMeasureError(f"the server does not report {tag} after the request")
            measured = True
        finally:
            # A window left open would hold a slot on the agent; close it on every way out.  A
            # failed close only matters when the measurement itself worked: then the agent
            # stopped answering, and the caller has to stop (a record without its card reading
            # would be silently weaker).
            if agent is not None and window is not None:
                try:
                    closed = agent.close_window(window)
                except OSError:
                    if measured:
                        raise
        record = FitRecord(
            measured_at=now().strftime("%Y-%m-%dT%H:%M:%SZ"),
            server=server,
            ollama_version=version,
            tool_commit=commit,
            tag=tag,
            digest=digest,
            num_ctx=num_ctx,
            answer_tokens=int(getattr(stats, "eval_count", 0)),
            generation_seconds=getattr(stats, "eval_duration", 0) / 1e9,
            options=options,
            load_seconds=getattr(stats, "load_duration", 0) / 1e9,
            ollama=OllamaReading(
                context_length=loaded.context_length,
                size_bytes=loaded.size_bytes,
                size_vram_bytes=loaded.size_vram_bytes,
            ),
            card=None if closed is None else _card_of(closed, idle, agent_info),
            ram=None if closed is None else _ram_of(closed, agent_info),
        )
        store.append(record)
        made.append(record)
        if loaded.context_length and loaded.context_length < num_ctx:
            break
    return tuple(made)


def _card_of(stats: Any, idle: int | None, info: Any) -> CardReading:
    """Return the card reading of one closed window, with the agent's own facts."""
    return CardReading(
        agent_sha256=str(getattr(info, "agent_sha256", "")),
        source=str(getattr(info, "source", "")),
        total_bytes=int(getattr(info, "card_total_bytes", 0)),
        idle_bytes=idle or 0,
        peak_bytes=stats.card_peak_bytes,
        end_bytes=stats.card_end_bytes,
    )


def _ram_of(stats: Any, info: Any = None) -> RamReading | None:
    """Return the main-memory reading of one closed window, or ``None`` when it was not read."""
    if stats.ram_min_available_bytes is None or stats.ram_start_available_bytes is None:
        return None
    return RamReading(
        total_bytes=int(getattr(info, "ram_total_bytes", None) or 0),
        idle_available_bytes=stats.ram_start_available_bytes,
        min_available_bytes=stats.ram_min_available_bytes,
    )


__all__ = [
    "CARD_USED_TOLERANCE_BYTES",
    "CPU_ONLY_OPTIONS",
    "DEFAULT_LIMITS",
    "DEFAULT_STORE",
    "GB",
    "LINEARITY_TOLERANCE",
    "MEMORY_TOLERANCE",
    "MIN_SPEED_TOKENS",
    "PROMPT",
    "SCHEMA",
    "SPILL_SHARE",
    "CardReading",
    "FitLimit",
    "FitMeasureError",
    "FitRecord",
    "FitRecordError",
    "FitStore",
    "GrowthFigures",
    "OllamaReading",
    "RamReading",
    "card_increase_bytes",
    "catalog_lines",
    "compare_lines",
    "current_build",
    "growth_figures",
    "growth_per_8k",
    "is_cpu_only",
    "latest",
    "measure_tag",
    "need_bytes",
    "ollama_card_share",
    "ollama_off_by",
    "on_card_share",
    "ram_drop_bytes",
    "record_line",
    "report_lines",
    "size_for_decision",
    "spilled",
    "tier_windows",
    "tokens_per_second",
    "within",
]
