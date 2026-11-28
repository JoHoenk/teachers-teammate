"""What an exchange is: proofreading a text, independent of the connection that carries it.

An exchange decides *what* is asked and what an answer means; a
:class:`~.port.ModelPort` decides *how* it is sent.  The two vary independently, so the same
exchange runs on a local Ollama server and on an online provider, and a second exchange (the
prepared one-request review) needs no new connection.

The result is one domain value: the corrected text in the source notation, the review, the
warnings and the reports of every request.  Nothing above this layer knows which exchange ran.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from ....domain.formatted_text import TextFormat
from ....domain.proofreading.result import ExchangeCost, ExchangeKind, StepName, StepReport
from ....domain.proofreading.review import CorrectionReview
from ....domain.request_profile import RequestProfile
from ..port import ModelPort


@dataclass(frozen=True)
class ProofreadContext:
    """Everything an exchange needs besides the text: the settings of the run."""

    language: str
    """The language *name* of the text, as the teacher set it."""
    explanation_language: str = ""
    """The language of the explanations; empty means the language of the text."""
    level: str = ""
    """The CEFR level of the student."""
    teacher_notes: str = ""
    """What the teacher wants the model to know about this class or this text."""
    text_format: TextFormat = "plain"
    """The notation of the text: Markdown is shielded before the request and restored after it."""
    profile: RequestProfile = field(default_factory=RequestProfile)
    """How this model is asked: the exchange, its steps and their settings."""
    corrections_only: bool = False
    """Stop after step ①: the corrected text alone, without explanations and unclear wording.

    The teacher's *Corrections only (faster)* choice.  It is a property of the run, not of the
    exchange: the same exchange answers with one request instead of three.
    """


@dataclass(frozen=True)
class ExchangeResult:
    """What proofreading a text produced."""

    text: str
    """The corrected text, in the notation of the source."""
    review: CorrectionReview | None = None
    """The explanations and the unclear expressions, or ``None`` when there are none."""
    warnings: tuple[str, ...] = ()
    """What the teacher should know about this file, in the order it happened."""
    failed: bool = False
    """``True``: keep the source text and store nothing — a half-corrected text is never stored."""
    reports: tuple[StepReport, ...] = ()
    """One entry per request: step, tokens, seconds, failure, counters."""
    left_out: int = 0
    """How many stretches of the text the model left out and the exchange put back."""


class Exchange(Protocol):
    """One way of proofreading a text."""

    kind: ExchangeKind
    """Which exchange this is; the profile names it."""

    def proofread(self, source: str, ctx: ProofreadContext) -> ExchangeResult:
        """Return the corrected text and its review for *source*."""
        ...

    def max_text_chars(self, ctx: ProofreadContext) -> int | None:
        """Return how many characters of text one request can carry, ``None`` for no limit."""
        ...

    def prompts(
        self, profile: RequestProfile, *, corrections_only: bool = False
    ) -> tuple[tuple[StepName, str], ...]:
        """Return the system prompts of the steps that run for *profile*, in step order.

        The one place that knows what an exchange asks, so the cache key, the dialog and
        *What is sent* cannot drift from the requests.
        """
        ...

    def cost_model(self) -> ExchangeCost:
        """Return what this exchange costs per page and per 100 words."""
        ...


ExchangeFactory = Callable[..., Exchange]
"""Builds an exchange from a port and its options; see :func:`build_exchange`."""

_EXCHANGES: dict[ExchangeKind, ExchangeFactory] = {}

EXCHANGES: Mapping[ExchangeKind, ExchangeFactory] = MappingProxyType(_EXCHANGES)
"""The exchanges the app can run, by the name a profile uses.

:mod:`teachers_teammate.infrastructure.proofreading.exchanges` registers them through
:func:`register_exchange`, so a new exchange is one module and one call here.  The table is
read-only: no caller can drop an exchange a profile points at.
"""


def register_exchange(kind: ExchangeKind, factory: ExchangeFactory) -> None:
    """Make *factory* the exchange *kind* names."""
    _EXCHANGES[kind] = factory


def build_exchange(
    kind: ExchangeKind,
    port: ModelPort,
    *,
    cut_off_tries: int = 1,
    clause_check: Callable[[str, str], bool] | None = None,
) -> Exchange:
    """Return the exchange *kind* on *port*.

    Raises:
        ValueError: *kind* names no exchange this version of the app can run.
    """
    factory = EXCHANGES.get(kind)
    if factory is None:
        known = ", ".join(sorted(EXCHANGES)) or "none"
        raise ValueError(f"unknown exchange {kind!r} (known: {known})")
    return factory(port, cut_off_tries=cut_off_tries, clause_check=clause_check)
