"""What one test text cost: requests, tokens, seconds, memory, and what else was on the server.

Pure data and arithmetic, no I/O.  The numbers come from Ollama's own answer (token counts and
durations in nanoseconds, :class:`~teachers_teammate.infrastructure.ollama_utils.ChatStats`), from
its list of loaded models (``/api/ps``) and from a sampler on the graphics card
(:mod:`.measure`).  They are stored on every result row, so a report can compute speeds later.

Reading the figures.  Tokens per second says how fast this machine runs this model, which depends
on how much of the model sits on the card; it does not say how long a teacher waits, because
models write very different amounts for the same text.  That is what *seconds per 100 words of
student text* is for.  A text counts as **shared** when another model was loaded on the server at
the time: it competes for the card, so its timing says little about the model.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from teachers_teammate.domain.model_catalog import normalize_tag

if TYPE_CHECKING:
    from teachers_teammate.infrastructure.ollama_utils import ChatStats

_NS = 1e9
"""Nanoseconds per second: Ollama reports its durations in nanoseconds."""


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

    @classmethod
    def from_stats(cls, model: str, stats: ChatStats, wall_s: float) -> RequestStats:
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
        )

    @classmethod
    def failure(cls, model: str, wall_s: float) -> RequestStats:
        """Build the record of a request that raised: only the waiting time is known."""
        return cls(model, 0, 0, 0.0, 0.0, 0.0, 0.0, wall_s, failed=True)


@dataclass(frozen=True)
class LoadedModel:
    """A model the server had in memory, with how much of it sat on the graphics card."""

    name: str
    size_bytes: int
    """Memory the model takes in total, on the card and in main memory together."""

    size_vram_bytes: int
    """The part of it on the graphics card."""

    context_length: int = 0

    @property
    def on_card_share(self) -> float:
        """Return the share (0 to 1) of the model that sits on the card."""
        return self.size_vram_bytes / self.size_bytes if self.size_bytes else 0.0


@dataclass(frozen=True)
class Performance:
    """What one test text cost on the server."""

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
        """Return a JSON-serialisable form."""
        return asdict(self)

    @classmethod
    def from_dict(cls, record: dict) -> Performance:
        """Build the record from :meth:`to_dict` output."""
        return cls(
            requests=tuple(RequestStats(**r) for r in record.get("requests", ())),
            loaded=tuple(LoadedModel(**m) for m in record.get("loaded", ())),
            shared=bool(record.get("shared", False)),
            vram_peak_mb=record.get("vram_peak_mb"),
            vram_baseline_mb=record.get("vram_baseline_mb"),
            seed=record.get("seed"),
            repeat=int(record.get("repeat", 1)),
        )


def per_100_words(seconds: float, words: int) -> float:
    """Return *seconds* scaled to 100 words of student text (0 when there are no words)."""
    return seconds / words * 100 if words else 0.0
