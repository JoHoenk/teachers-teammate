"""The exchange, seen as the corrector the pipeline and the evaluation tools already run.

The pipeline asks a :class:`~teachers_teammate.interfaces.Corrector` for a
:class:`~teachers_teammate.domain.correction_review.CorrectionOutcome`; the exchange answers with
an :class:`~.exchanges.base.ExchangeResult`.  :class:`ExchangeCorrector` is the one adapter
between the two, so the stage builder, ``ChunkingCorrector`` (which splits a long text by
:meth:`ExchangeCorrector.max_text_chars`) and the evaluation tools do not know there is a new
shape underneath.  The review it returns is the sentence-based one the readers still use
(:func:`~teachers_teammate.domain.proofreading.legacy_review.sentence_review`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...domain.correction_policy import DEFAULT_LEVEL
from ...domain.correction_review import CorrectionOutcome
from ...domain.model_catalog import DEFAULT_REQUEST_PROFILE, RequestProfile
from ...domain.proofreading.legacy_review import sentence_review
from ...interfaces import Corrector
from ..ocr_formats import plain_text_of
from .exchanges import Exchange, ProofreadContext, build_exchange
from .port import ModelPort, OllamaPort

if TYPE_CHECKING:
    from ...domain.formatted_text import TextFormat
    from ..ollama_utils import OllamaClient

_FAILED = "Proofreading failed; keeping the original text."
"""Shown when an exchange failed the file without saying why."""


class ExchangeCorrector(Corrector):
    """A :class:`~teachers_teammate.interfaces.Corrector` that proofreads through an exchange."""

    def __init__(
        self,
        exchange: Exchange,
        *,
        level: str = DEFAULT_LEVEL,
        explanation_language: str = "",
        teacher_notes: str = "",
        profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    ) -> None:
        self._exchange = exchange
        self._level = level
        self._explanation_language = explanation_language
        self._teacher_notes = teacher_notes
        self._profile = profile

    @property
    def exchange(self) -> Exchange:
        """Return the exchange this corrector runs."""
        return self._exchange

    def _context(self, language: str, text_format: TextFormat) -> ProofreadContext:
        """Return what the exchange needs besides the text."""
        return ProofreadContext(
            language=language,
            explanation_language=self._explanation_language,
            level=self._level,
            teacher_notes=self._teacher_notes,
            text_format=text_format,
            profile=self._profile,
        )

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofread text, its review and what the teacher should know.

        A failed file keeps *raw_text* and says why; nothing half-corrected is returned.
        """
        result = self._exchange.proofread(raw_text, self._context(language, text_format))
        warning = "\n".join(result.warnings) or None
        if result.failed:
            return CorrectionOutcome(
                text=raw_text, review=None, warning=warning or _FAILED, failed=True
            )
        review = (
            sentence_review(plain_text_of(raw_text, text_format), result.review)
            if result.review is not None
            else None
        )
        return CorrectionOutcome(text=result.text, review=review, warning=warning)

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return how many characters one request of the exchange can carry."""
        return self._exchange.max_text_chars(self._context(language, text_format))


def build_exchange_corrector(  # noqa: PLR0913  # a request is many independent facts, keyword-only
    client: OllamaClient,
    model: str,
    *,
    timeout: float,
    num_ctx: int,
    profile: RequestProfile = DEFAULT_REQUEST_PROFILE,
    level: str = DEFAULT_LEVEL,
    explanation_language: str = "",
    teacher_notes: str = "",
    cut_off_tries: int = 1,
    clause_check: Callable[[str, str], bool] | None = None,
    port: ModelPort | None = None,
) -> ExchangeCorrector:
    """Return the corrector of the exchange *profile* names, on an Ollama server.

    The port is the app's own :class:`~.port.OllamaPort` on *client*, so a client that records
    its requests (the evaluation tools') records the exchange's too.  A test passes *port*.
    """
    the_port = port or OllamaPort(client, model, timeout=timeout, num_ctx=num_ctx)
    exchange = build_exchange(
        profile.exchange, the_port, cut_off_tries=cut_off_tries, clause_check=clause_check
    )
    return ExchangeCorrector(
        exchange,
        level=level,
        explanation_language=explanation_language,
        teacher_notes=teacher_notes,
        profile=profile,
    )
