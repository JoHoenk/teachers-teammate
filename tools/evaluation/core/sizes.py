"""The size record: what a model really needs on one machine, as it was measured.

Ollama's ``/api/ps`` is Ollama's own bookkeeping, not a reading of the card: on the 8 GB laptop it
reported about 1 GB for ``gemma4:12b`` while ``nvidia-smi`` showed 6.9 GB.  So a measurement stores
two readings side by side — what Ollama says and what the card really holds — and nothing derived.
The verdict (does this model fit a 16 GB card?) is computed when it is needed, from the stored
bytes, so changing the rule costs no measuring.

The **need** of a measurement is ``max(ollama size, card increase)``: Ollama's figure leaves out the
driver's and the runtime's share, the card's increase cannot exceed the physical card.  Taking the
larger is cautious in both directions.  A step that spills onto the processor is flagged, because
its need is only a lower bound.

A record names the **machine** it was made on, an id of the store's ``machines/``, and the
**device** it was measured on: the card or the processor.  The two are not the same thing — one
machine has both — and a size measured on the card says nothing about the processor.

The records live in the store's ``model-fit/sizes.jsonl``, append-only, numbers only
(:class:`FitStore`).  Everything here is pure.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.domain.hardware_profile import card_share

GB = 1e9
"""Ollama reports bytes; the catalog and the docs speak of GB (10^9), as the download sizes do."""

FORMAT = 3
"""The version of the record format; a record of another version is refused, not guessed at.

Version 2 added the speed of the request (``answer_tokens`` over ``generation_seconds``).  Version 3
replaced ``schema`` with ``format`` and ``server`` with ``machine`` and ``device``.
"""

MIN_SPEED_TOKENS = 20
"""An answer shorter than this says nothing about how fast the model writes."""

SPILL_SHARE = 0.98
"""A card peak this close to its total means the model did not fit: the rest is in main memory."""

SIZES_PATH = Path("model-fit") / "sizes.jsonl"
"""Where the measurements are kept, below the store root."""


class FitRecordError(ValueError):
    """A stored record cannot be read: another format, or a field that is not there."""


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
    """One measurement: one model, one window, one machine and one device.

    Raw bytes only; reports divide by 10⁹, the catalog's unit.  Nothing derived is stored, so a
    change of rule costs no new measurement.
    """

    measured_at: str
    """When the request ran, UTC."""

    machine: str
    """The id of the machine, from the store's ``machines/``."""

    ollama_version: str
    """The server's version; another version is another backend."""

    tool_commit: str
    """The commit that made the record."""

    tag: str
    digest: str
    """The build of the model; a tag pushed again has a new one."""

    num_ctx: int
    """The window the request asked for."""
    answer_tokens: int
    """How many tokens the request's answer had (Ollama's ``eval_count``)."""

    generation_seconds: float
    """How long the model took to write them (Ollama's ``eval_duration``), load excluded."""

    options: dict[str, Any] = field(default_factory=dict)
    """The options of the app's own proofreading request."""

    load_seconds: float = 0.0
    """What a reload costs."""

    ollama: OllamaReading | None = None
    card: CardReading | None = None
    ram: RamReading | None = None
    """``card`` and ``ram`` are ``None`` without the memory agent."""

    device: str = "gpu"
    """``gpu`` or ``cpu``; the two are never pooled."""
    format: int = FORMAT

    def to_json(self) -> dict[str, Any]:
        """Return the record as the JSON object one line of the store holds."""
        return {
            "format": self.format,
            "measured_at": self.measured_at,
            "machine": self.machine,
            "device": self.device,
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
            FitRecordError: the record is of another format, or a field it needs is missing.
        """
        found = data.get("format")
        if found != FORMAT:
            raise FitRecordError(
                f"record format {found!r} is not {FORMAT}: written by another version of the tool"
                + (
                    "; a `schema: 2` line is relabelled to `format: 3` with `machine` and `device`"
                    if found is None and "schema" in data
                    else ""
                )
            )
        try:
            return cls(
                measured_at=str(data["measured_at"]),
                machine=str(data["machine"]),
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
                device=str(data.get("device") or "gpu"),
                format=FORMAT,
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
    """Return whether the request ran on the processor, to read its speed.

    Such a record says how fast the processor runs the model.  It is no size measurement: the
    card's share is missing from the need, so the size lookups never use it.
    """
    return record.device == "cpu"


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


# ── which record counts ──────────────────────────────────────────────────────


def latest(
    records: Iterable[FitRecord], *, tag: str, digest: str, num_ctx: int
) -> dict[str, FitRecord]:
    """Return the newest record per machine for one build at one window.

    Records of the processor are left out: they have no card share, so their size is no fit figure.
    """
    newest: dict[str, FitRecord] = {}
    for record in records:
        if is_cpu_only(record):
            continue
        if record.tag != tag or record.digest != digest or record.num_ctx != num_ctx:
            continue
        known = newest.get(record.machine)
        if known is None or record.measured_at > known.measured_at:
            newest[record.machine] = record
    return newest


def size_for_decision(
    records: Iterable[FitRecord], *, tag: str, digest: str, num_ctx: int
) -> FitRecord | None:
    """Return the record to decide with: the largest need among the machines' latest records.

    Two backends that disagree give the cautious answer, and ``None`` means nothing was measured.
    """
    candidates = latest(records, tag=tag, digest=digest, num_ctx=num_ctx)
    if not candidates:
        return None
    return max(candidates.values(), key=need_bytes)


def current_build(records: Iterable[FitRecord]) -> tuple[FitRecord, ...]:
    """Return the measurements that describe one model's current build, one per window.

    *records* are those of one model on one machine and one device.  The store is append-only, so
    it also holds older builds (another digest, after the tag was re-pointed) and repeated
    measurements (``--again``, another Ollama version).  The build of the newest record counts, and
    of it the newest record per window; mixing builds would give growth figures that belong to no
    model.
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


def growth_per_8k(records: Iterable[FitRecord]) -> float | None:
    """Return the growth (GB) per 8192 tokens of one model on one machine.

    The growth starts at :data:`BASE_NUM_CTX`.  Only steps that did not spill count: a spilled
    step's need is a lower bound, so its growth would be one too.  ``None`` when fewer than two such
    steps were measured.
    """
    records = current_build(records)
    usable = [record for record in records if not spilled(record)]
    if len(usable) < 2:
        return None
    base, largest = usable[0], usable[-1]
    span = (largest.num_ctx - base.num_ctx) / BASE_NUM_CTX
    if span <= 0:
        return None
    return (need_bytes(largest) - need_bytes(base)) / GB / span


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
        machine: str,
        device: str,
        tag: str,
        digest: str,
        num_ctx: int,
        ollama_version: str,
    ) -> bool:
        """Return whether this build was already measured at this window on this device.

        The card and the processor are two measurements of the same build, not one.
        """
        return any(
            record.device == device
            and record.machine == machine
            and record.tag == tag
            and record.digest == digest
            and record.num_ctx == num_ctx
            and record.ollama_version == ollama_version
            for record in self.records()
        )


__all__ = [
    "FORMAT",
    "GB",
    "MIN_SPEED_TOKENS",
    "SIZES_PATH",
    "SPILL_SHARE",
    "CardReading",
    "FitRecord",
    "FitRecordError",
    "FitStore",
    "OllamaReading",
    "RamReading",
    "card_increase_bytes",
    "current_build",
    "growth_per_8k",
    "is_cpu_only",
    "latest",
    "need_bytes",
    "ollama_card_share",
    "ollama_off_by",
    "on_card_share",
    "ram_drop_bytes",
    "size_for_decision",
    "spilled",
    "tokens_per_second",
]
