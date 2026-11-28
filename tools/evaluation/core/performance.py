"""What one test text cost: requests, tokens, seconds, memory, and what else was on the server.

Pure data and arithmetic, no I/O.  The numbers come from Ollama's own answer (token counts and
durations in nanoseconds), from its list of loaded models (``/api/ps``) and from a sampler on the
graphics card.  They are stored on every answer, so a report can compute speeds later: a run
file keeps every measured fact, including the ones no report reads yet.

Reading the figures.  Tokens per second says how fast this machine runs this model, which depends
on how much of the model sits on the card; it does not say how long a teacher waits, because models
write very different amounts for the same text.  That is what *seconds per 100 words of student
text* is for.  A text counts as **shared** when another model was loaded on the server at the time:
it competes for the card, so its timing says little about the model.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from teachers_teammate.domain.model_tags import normalize_tag
from teachers_teammate.domain.performance_estimate import percentile

from .formats import extra_fields

if TYPE_CHECKING:
    from teachers_teammate.infrastructure.ollama_request import ChatStats

_NS = 1e9
"""Nanoseconds per second: Ollama reports its durations in nanoseconds."""


class Measured(Protocol):
    """What :func:`step_one_share` needs of a row: its performance, or ``None``.

    Read-only, so that a frozen row satisfies it: nothing here writes a performance back.
    """

    @property
    def performance(self) -> Performance | None:
        """The performance of the row, or ``None`` when it was not recorded."""


@dataclass(frozen=True)
class RequestStats:
    """One request to the server, as Ollama and the clock saw it."""

    model: str
    prompt_tokens: int
    """Tokens of the request."""

    answer_tokens: int
    """Tokens of the answer."""

    load_s: float
    """Seconds the server spent loading the model; near 0 when it was in memory already."""

    prompt_eval_s: float
    """Seconds the server spent reading the request."""

    generation_s: float
    """Seconds the server spent writing the answer."""

    total_s: float
    """Seconds the server spent on the request, by its own count."""

    wall_s: float
    """Seconds the tool waited for the answer, by its own clock."""

    done_reason: str = ""
    failed: bool = False
    """The request raised instead of answering (a timeout, a refused window)."""

    step: str = ""
    """The step of the exchange the request belongs to (``correct``, ``explain``, ``unclear``);
    empty for a request of the one-request review or of text mode, and in rows written before the
    tag existed."""

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = (
        "model",
        "prompt_tokens",
        "answer_tokens",
        "load_s",
        "prompt_eval_s",
        "generation_s",
        "total_s",
        "wall_s",
        "done_reason",
        "failed",
        "step",
    )

    @classmethod
    def from_stats(
        cls, model: str, stats: ChatStats, wall_s: float, step: str = ""
    ) -> RequestStats:
        """Build the record of a finished request from Ollama's statistics."""
        return cls(
            model=model,
            prompt_tokens=stats.prompt_eval_count,
            answer_tokens=stats.eval_count,
            load_s=stats.load_duration / _NS,
            prompt_eval_s=stats.prompt_eval_duration / _NS,
            generation_s=stats.eval_duration / _NS,
            total_s=stats.total_duration / _NS,
            wall_s=wall_s,
            done_reason=stats.done_reason,
            step=step,
        )

    @classmethod
    def failure(cls, model: str, wall_s: float, step: str = "") -> RequestStats:
        """Build the record of a request that raised: only the waiting time is known."""
        return cls(model, 0, 0, 0.0, 0.0, 0.0, 0.0, wall_s, failed=True, step=step)

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record (a request without a step tag does not list one)."""
        record = asdict(self)
        del record["extra"]
        if not record["step"]:
            del record["step"]
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: dict) -> RequestStats:
        """Build a request from :meth:`to_dict` output; an unknown field is kept, not refused."""
        return cls(
            model=str(record["model"]),
            prompt_tokens=int(record.get("prompt_tokens", 0)),
            answer_tokens=int(record.get("answer_tokens", 0)),
            load_s=float(record.get("load_s", 0.0)),
            prompt_eval_s=float(record.get("prompt_eval_s", 0.0)),
            generation_s=float(record.get("generation_s", 0.0)),
            total_s=float(record.get("total_s", 0.0)),
            wall_s=float(record.get("wall_s", 0.0)),
            done_reason=str(record.get("done_reason", "")),
            failed=bool(record.get("failed", False)),
            step=str(record.get("step", "")),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class LoadedModel:
    """A model the server had in memory, with how much of it sat on the graphics card."""

    name: str
    size_bytes: int
    """Memory the model takes in total, on the card and in main memory together."""

    size_vram_bytes: int
    """The part of it on the graphics card."""

    context_length: int = 0

    extra: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)
    """Fields a newer writer added; kept and written back."""

    _KNOWN: ClassVar[tuple[str, ...]] = ("name", "size_bytes", "size_vram_bytes", "context_length")

    @property
    def on_card_share(self) -> float:
        """Return the share (0 to 1) of the model that sits on the card."""
        return self.size_vram_bytes / self.size_bytes if self.size_bytes else 0.0

    def to_dict(self) -> dict:
        """Return a JSON-serialisable record."""
        record = asdict(self)
        del record["extra"]
        return {**self.extra, **record}

    @classmethod
    def from_dict(cls, record: dict) -> LoadedModel:
        """Build a loaded model from :meth:`to_dict` output; an unknown field is kept."""
        return cls(
            name=str(record["name"]),
            size_bytes=int(record.get("size_bytes", 0)),
            size_vram_bytes=int(record.get("size_vram_bytes", 0)),
            context_length=int(record.get("context_length", 0)),
            extra=extra_fields(record, cls._KNOWN),
        )


@dataclass(frozen=True)
class Performance:
    """What one test text cost on the server, as one value a report can carry."""

    requests: tuple[RequestStats, ...] = ()
    """Every request made for the text: the answer, a repair, a fallback."""

    loaded: tuple[LoadedModel, ...] = ()
    """What the server had loaded when the text was done."""

    shared: bool = False
    """Another model was loaded before or after the text: its timing is not comparable."""

    vram_peak_mb: int | None = None
    """Peak graphics memory in use during the text, from the sampler; ``None`` without one."""

    vram_baseline_mb: int | None = None
    """Graphics memory in use before the model was loaded (the desktop, other programs)."""

    seed: int | None = None
    """The seed sent with the requests; ``None`` when none was sent."""

    repeat: int = 1
    """Number of the repeat this text belongs to (1 for a single run)."""

    @property
    def request_count(self) -> int:
        """Return how many requests the text took (more than one: a repair or a fallback)."""
        return len(self.requests)

    @property
    def prompt_tokens(self) -> int:
        """Return the tokens of all requests."""
        return sum(r.prompt_tokens for r in self.requests)

    @property
    def answer_tokens(self) -> int:
        """Return the tokens of all answers."""
        return sum(r.answer_tokens for r in self.requests)

    @property
    def generation_s(self) -> float:
        """Return the seconds the server spent writing answers."""
        return sum(r.generation_s for r in self.requests)

    @property
    def load_s(self) -> float:
        """Return the seconds the server spent loading the model."""
        return sum(r.load_s for r in self.requests)

    @property
    def vram_used_mb(self) -> int | None:
        """Return the memory the run added on the card: peak minus baseline, or ``None``."""
        if self.vram_peak_mb is None or self.vram_baseline_mb is None:
            return None
        return max(0, self.vram_peak_mb - self.vram_baseline_mb)

    def on_card_share(self, model: str) -> float | None:
        """Return the share of *model* on the card, or ``None`` when it was not loaded."""
        for item in self.loaded:
            if normalize_tag(item.name) == normalize_tag(model):
                return item.on_card_share
        return None

    def to_dict(self) -> dict:
        """Return a JSON-serialisable form (a request without a step tag does not list one)."""
        return {
            "requests": [request.to_dict() for request in self.requests],
            "loaded": [model.to_dict() for model in self.loaded],
            "shared": self.shared,
            "vram_peak_mb": self.vram_peak_mb,
            "vram_baseline_mb": self.vram_baseline_mb,
            "seed": self.seed,
            "repeat": self.repeat,
        }

    @classmethod
    def from_dict(cls, record: dict) -> Performance:
        """Build the record from :meth:`to_dict` output."""
        return cls(
            requests=tuple(RequestStats.from_dict(r) for r in record.get("requests", ())),
            loaded=tuple(LoadedModel.from_dict(m) for m in record.get("loaded", ())),
            shared=bool(record.get("shared", False)),
            vram_peak_mb=record.get("vram_peak_mb"),
            vram_baseline_mb=record.get("vram_baseline_mb"),
            seed=record.get("seed"),
            repeat=int(record.get("repeat", 1)),
        )


def per_100_words(seconds: float, words: int) -> float:
    """Return *seconds* scaled to 100 words of student text (0 when there are no words)."""
    return seconds / words * 100 if words else 0.0


def step_one_share(rows: Iterable[Measured]) -> float | None:
    """Return the share of the waiting time that went to step ①, or ``None`` without step tags.

    A correct-only run shows 100 %, because step ① is the whole exchange; a review or text run has
    no steps and shows ``None``, as does a row written before the tag existed.
    """
    first = other = 0.0
    for row in rows:
        if row.performance is None:
            continue
        for request in row.performance.requests:
            if request.step == "correct":
                first += request.wall_s
            elif request.step:
                other += request.wall_s
    return first / (first + other) if first else None


@dataclass(frozen=True)
class Spread:
    """The 10th percentile, median and 90th percentile of a set of figures.

    A range rather than one figure, because the middle 80 % of one model's answers on one test set
    are about three times apart: how many errors a text has decides most of it.  The ends are left
    out so that one runaway answer does not widen the range for good.
    """

    low: float
    typical: float
    high: float

    @classmethod
    def of(cls, values: Sequence[float]) -> Spread | None:
        """Return the spread of *values*, or ``None`` when there are none."""
        if not values:
            return None
        ordered = sorted(values)
        return cls(
            low=percentile(ordered, 10),
            typical=median(ordered),
            high=percentile(ordered, 90),
        )

    def rounded(self) -> tuple[int, int, int]:
        """Return the three figures as whole numbers, the way the catalog stores them."""
        return round(self.low), round(self.typical), round(self.high)


def performance_of(answer: Any) -> Performance:
    """Return the performance block of an answer, as the aggregate type a report reads.

    An answer stores its requests, loaded models and memory numbers flat, where they were
    measured; a report wants them as one value, so this is the one place they are gathered.
    """
    return Performance(
        requests=tuple(answer.requests),
        loaded=tuple(answer.loaded),
        shared=bool(answer.shared),
        vram_peak_mb=answer.vram_peak_mb,
        vram_baseline_mb=answer.vram_baseline_mb,
    )


__all__ = [
    "LoadedModel",
    "Measured",
    "Performance",
    "RequestStats",
    "Spread",
    "per_100_words",
    "performance_of",
    "step_one_share",
]
