"""The exchange, seen as the corrector the pipeline and the evaluation tools already run.

The pipeline asks a :class:`~teachers_teammate.interfaces.Corrector` for a
:class:`~teachers_teammate.domain.proofreading.result.CorrectionOutcome`; the exchange answers
with an :class:`~.exchanges.base.ExchangeResult`.  :class:`ExchangeCorrector` is the one adapter
between the two, so the stage builder, ``ChunkingCorrector`` (which splits a long text by
:meth:`ExchangeCorrector.max_text_chars`) and the evaluation tools do not know there is a new
shape underneath.  The review it returns is the exchange's own, unchanged: the edit-based review
whose offsets are in the plain text of the raw text.

The three functions beside it answer what a run sends without sending anything:
:func:`exchange_identity` is the cache-key part, :func:`exchange_prompts` is what the dialog and
*What is sent* show, and :func:`exchange_cost` is what the time estimate is worked out from.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
from typing import TYPE_CHECKING

from ...domain.correction_policy import DEFAULT_LEVEL
from ...domain.correction_prompts import PROMPT_VERSION
from ...domain.model_catalog import DEFAULT_REQUEST_PROFILE, RequestProfile
from ...domain.proofreading.result import (
    CorrectionOutcome,
    ExchangeKind,
    StepName,
    stored_exchange_id,
)
from ...exceptions import StageError, StageErrorKind
from ...interfaces import Corrector
from .exchanges import Exchange, ExchangeCost, ProofreadContext, build_exchange
from .exchanges.t import EXPLAIN_FAILED, UNCLEAR_FAILED
from .port import ModelPort, OllamaPort

if TYPE_CHECKING:
    from ...domain.formatted_text import TextFormat
    from ..ollama_utils import OllamaClient

_FAILED = "Proofreading failed; keeping the original text."
"""Shown when an exchange failed without saying why."""

_NO_ANSWER = "Proofreading failed; the model did not answer."
"""Shown when a request never reached the model and the transport said nothing more."""

_PARTIAL_WARNINGS: frozenset[str] = frozenset({EXPLAIN_FAILED, UNCLEAR_FAILED})
"""The warnings of the steps whose failure costs part of the answer instead of the file."""


@dataclass(frozen=True)
class ProofreadingSettings:
    """What the teacher set for this run, besides the model and the connection."""

    level: str = DEFAULT_LEVEL
    """The CEFR level of the student."""
    explanation_language: str = ""
    """The language of the explanations; empty means the language of the text."""
    teacher_notes: str = ""
    """What the teacher wants the model to know about this class or this text."""
    corrections_only: bool = False
    """Stop after step ①: the corrected text alone, without explanations."""


class ExchangeCorrector(Corrector):
    """A :class:`~teachers_teammate.interfaces.Corrector` that proofreads through an exchange."""

    def __init__(
        self,
        exchange: Exchange,
        *,
        settings: ProofreadingSettings | None = None,
        profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    ) -> None:
        self._exchange = exchange
        self._settings = settings or ProofreadingSettings()
        self._profile = profile

    @property
    def exchange(self) -> Exchange:
        """Return the exchange this corrector runs."""
        return self._exchange

    def _context(self, language: str, text_format: TextFormat) -> ProofreadContext:
        """Return what the exchange needs besides the text."""
        return ProofreadContext(
            language=language,
            explanation_language=self._settings.explanation_language,
            level=self._settings.level,
            teacher_notes=self._settings.teacher_notes,
            text_format=text_format,
            profile=self._profile,
            corrections_only=self._settings.corrections_only,
        )

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofread text, its review and what the teacher should know.

        An answer that cannot be used (the model added or dropped text, or answered twice with a
        remark around it) is an outcome with ``failed`` set: the source text and the reason.

        Raises:
            :exc:`~teachers_teammate.exceptions.StageError`: when a request never reached the
                model, so there was no answer to judge.
        """
        result = self._exchange.proofread(raw_text, self._context(language, text_format))
        warning = "\n".join(result.warnings) or None
        if result.failed:
            if any(report.failure == "transport" for report in result.reports):
                raise StageError(
                    warning or _NO_ANSWER, kind=StageErrorKind.PROVIDER, stage="correction"
                )
            return CorrectionOutcome(
                text=raw_text, review=None, warning=warning or _FAILED, failed=True
            )
        return CorrectionOutcome(
            text=result.text,
            review=result.review,
            warning=warning,
            left_out=result.left_out,
            # A repaired answer is shorter than a complete one, like one whose step ② failed.
            fell_back=_partial(result.warnings) or result.left_out > 0,
        )

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return how many characters one request of the exchange can carry."""
        return self._exchange.max_text_chars(self._context(language, text_format))


def _partial(warnings: tuple[str, ...]) -> bool:
    """Whether step ② or ③ ended without its answer.

    The answer then lacks what that step was asked for, so it is shorter than a complete one:
    the speed sample of such a file would undercount the answer length and is dropped.  The
    exchange's own warnings say so; a failed request that a retry recovered does not count.
    """
    return any(warning in _PARTIAL_WARNINGS for warning in warnings)


class _NullPort:
    """A port that sends nothing: for asking an exchange what it asks and what it costs."""

    def ask_text(self, _req: object) -> object:
        """Refuse: nothing is sent to ask an exchange about itself."""
        raise RuntimeError("this port sends no request")

    def ask_structured(self, _req: object) -> object:
        """Refuse: nothing is sent to ask an exchange about itself."""
        raise RuntimeError("this port sends no request")

    def window(self) -> None:
        """Return ``None``: an exchange asked about itself has no window to check."""
        return None


def _exchange_of(kind: ExchangeKind) -> Exchange:
    """Return the exchange *kind* without a connection, for its prompts and its cost."""
    return build_exchange(kind, _NullPort())  # ty: ignore[invalid-argument-type]


def exchange_prompts(
    profile: RequestProfile, *, corrections_only: bool = False
) -> tuple[tuple[StepName, str], ...]:
    """Return the system prompts of the steps that run, in step order.

    What the *Options…* dialog shows read-only and what *What is sent* lists, taken from the
    exchange itself so neither can drift from the requests.
    """
    return _exchange_of(profile.exchange).prompts(profile, corrections_only=corrections_only)


def exchange_identity(profile: RequestProfile, *, corrections_only: bool) -> str:
    """Return what names this exchange's requests in a cache key.

    The exchange, the version of its fixed prompts, a hash of the system prompts of the steps
    that run (so an edited prompt invalidates the stored results), and the model's request
    profile (which carries every temperature, the thinking and the sampling values).
    """
    prompts = exchange_prompts(profile, corrections_only=corrections_only)
    digest = hashlib.sha256("\n".join(text for _step, text in prompts).encode("utf-8")).hexdigest()
    return "|".join(
        [
            stored_exchange_id(profile.exchange),
            f"prompts-{PROMPT_VERSION}",
            digest[:12],
            profile.fingerprint(),
        ]
    )


def exchange_cost(kind: ExchangeKind) -> ExchangeCost:
    """Return what the exchange *kind* costs per page and per 100 words."""
    return _exchange_of(kind).cost_model()


def build_exchange_corrector(
    port: ModelPort,
    *,
    profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    settings: ProofreadingSettings | None = None,
    cut_off_tries: int = 1,
    clause_check: Callable[[str, str], bool] | None = None,
) -> ExchangeCorrector:
    """Return the corrector of the exchange *profile* names, on *port*.

    The port is the connection: an :class:`~.port.OllamaPort` for a local server, a
    :class:`~.port.LangChainPort` for an online provider, or a recording one in a test.
    """
    exchange = build_exchange(
        profile.exchange, port, cut_off_tries=cut_off_tries, clause_check=clause_check
    )
    return ExchangeCorrector(exchange, settings=settings, profile=profile)


def ollama_exchange_corrector(  # noqa: PLR0913  # a request is many independent facts, keyword-only
    client: OllamaClient,
    model: str,
    *,
    timeout: float,
    num_ctx: int,
    profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    level: str = DEFAULT_LEVEL,
    explanation_language: str = "",
    teacher_notes: str = "",
    corrections_only: bool = False,
    cut_off_tries: int = 1,
    clause_check: Callable[[str, str], bool] | None = None,
    port: ModelPort | None = None,
) -> ExchangeCorrector:
    """Return the corrector of the exchange *profile* names, on an Ollama server.

    The port is the app's own :class:`~.port.OllamaPort` on *client*, so a client that records
    its requests (the evaluation tools') records the exchange's too.  A test passes *port*.
    """
    the_port = port or OllamaPort(client, model, timeout=timeout, num_ctx=num_ctx)
    return build_exchange_corrector(
        the_port,
        profile=profile,
        settings=ProofreadingSettings(
            level=level,
            explanation_language=explanation_language,
            teacher_notes=teacher_notes,
            corrections_only=corrections_only,
        ),
        cut_off_tries=cut_off_tries,
        clause_check=clause_check,
    )
