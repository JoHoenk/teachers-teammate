"""The correctors the tools build and measure.

A teacher's run builds an
:class:`~teachers_teammate.infrastructure.proofreading.corrector.ExchangeCorrector` over the
exchange and wraps it in
:class:`~teachers_teammate.infrastructure.proofreading.chunking.ChunkingCorrector`
(``stage_builder._chunked``).  The evaluation baseline is the one-request review the app used
before (:mod:`.one_request`), and the comparison and the protocol fingerprint build that chain
here, so what they measure and hash is the request a saved run was made with.  This module needs
no server and imports nothing from :mod:`.measure`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.interfaces import Corrector

from ..core.modes import ALL_STEPS, ONE_REQUEST_OUTPUT, Exchange, Setup, Step

if TYPE_CHECKING:
    from teachers_teammate.domain.context_budget import ContextBudget
    from teachers_teammate.domain.formatted_text import TextFormat
    from teachers_teammate.domain.proofreading.result import CorrectionOutcome
    from teachers_teammate.domain.request_profile import RequestProfile
    from teachers_teammate.infrastructure.ollama_client import OllamaClient
    from teachers_teammate.infrastructure.proofreading.port import (
        ModelPort,
        PortAnswer,
        StepRequest,
    )


CorrectorFactory = Callable[[str, float], "Corrector"]
"""``(model, temperature) -> corrector``; replaced in tests."""

ONE_REQUEST_EXCHANGES: tuple[Exchange, ...] = (
    Exchange.ONE_REQUEST_REVIEW,
    Exchange.ONE_REQUEST_TEXT,
)
"""The exchanges the one-request corrector answers, and the only ones it can."""

BUILT_EXCHANGES: tuple[Exchange, ...] = (Exchange.TEXT_FIRST, *ONE_REQUEST_EXCHANGES)
"""The exchanges :func:`app_corrector_factory` builds a corrector for."""


RUNNABLE_SETUPS: tuple[Setup, ...] = (
    (Exchange.ONE_REQUEST_REVIEW, ALL_STEPS),
    (Exchange.ONE_REQUEST_TEXT, (Step.CORRECT,)),
    (Exchange.TEXT_FIRST, ALL_STEPS),
    (Exchange.TEXT_FIRST, (Step.CORRECT,)),
)
"""Every setup ``evaluate run`` can ask in: the exchanges the tools have a corrector for, with the
steps each of them runs.  The POC's sentence exchanges have no corrector here."""


def steps_that_run(steps: tuple[Step, ...], profile: RequestProfile) -> tuple[Step, ...]:
    """Return the steps of *steps* that *profile* lets run.

    A model whose profile switches the unclear check off never sends step ③, so neither the
    record of the run nor the fingerprint may claim it did.
    """
    return tuple(step for step in steps if step is not Step.UNCLEAR or profile.unclear_check)


def app_corrector_factory(
    client: OllamaClient,
    *,
    timeout: float,
    exchange: Exchange,
    steps: tuple[Step, ...],
    num_ctx: int = BASE_NUM_CTX,
    profile: RequestProfile | None = None,
) -> CorrectorFactory:
    """Return a factory building the corrector of *exchange* with *steps* on *client*.

    The exchange decides which corrector: the app's proofreading exchange
    (:class:`~teachers_teammate.infrastructure.proofreading.corrector.ExchangeCorrector`) for
    ``text-first``, the one-request baseline the saved runs were measured with
    (``NativeOllamaCorrector``) for the two one-request exchanges.  Either is wrapped in
    ``ChunkingCorrector``, as ``stage_builder._chunked`` wraps every corrector with a bounded
    request window, so a text longer than one request is proofread in pieces here too.
    ``test_app_corrector.py`` compares the chains, so a change of that wrapping is found.

    Args:
        client: The Ollama client the requests go through (a recording one in a measured run).
        timeout: Seconds one request may take.
        exchange: How the text travels; it decides which corrector is built.
        steps: The steps of the exchange that run.  A step that is not asked for fails at the
            port without a request, so the exchange's own code path, prompt and text budget are
            the ones measured.
        num_ctx: The context window the corrector asks for; the app's proofreading window by
            default.
        profile: The request profile the corrector asks with (the compact-JSON rule, the
            temperature and the sampling values); the default for a model the catalog does not
            know.

    Raises:
        ValueError: if *exchange* is not one the tools have a corrector for.
    """
    if exchange in ONE_REQUEST_EXCHANGES:
        return _one_request_factory(
            client,
            timeout=timeout,
            exchange=exchange,
            num_ctx=num_ctx,
            profile=profile,
        )
    if exchange is Exchange.TEXT_FIRST:
        return _exchange_factory(
            client, timeout=timeout, steps=steps, num_ctx=num_ctx, profile=profile
        )
    raise ValueError(
        f"no corrector for the exchange {exchange.value!r}: "
        f"the tools ask in {', '.join(item.value for item in Exchange if item in BUILT_EXCHANGES)}"
    )


def _one_request_factory(
    client: OllamaClient,
    *,
    timeout: float,
    exchange: Exchange,
    num_ctx: int,
    profile: RequestProfile | None,
) -> CorrectorFactory:
    """Return a factory building the one-request corrector of *exchange* on *client*."""
    from teachers_teammate.domain.request_profile import DEFAULT_REQUEST_PROFILE  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )

    from .one_request.correction import (  # noqa: PLC0415
        CorrectionSettings,
        NativeOllamaCorrector,
    )

    chosen = DEFAULT_REQUEST_PROFILE if profile is None else profile
    output_mode = ONE_REQUEST_OUTPUT[exchange]

    def build(model: str, temperature: float) -> Corrector:
        return ChunkingCorrector(
            NativeOllamaCorrector(
                model,
                client,
                timeout=timeout,
                temperature=temperature,
                settings=CorrectionSettings(output_mode=output_mode, num_ctx=num_ctx),
                profile=chosen,
            )
        )

    return build


def _exchange_factory(
    client: OllamaClient,
    *,
    timeout: float,
    steps: tuple[Step, ...],
    num_ctx: int,
    profile: RequestProfile | None,
) -> CorrectorFactory:
    """Return a factory building the app's exchange corrector for *steps* on *client*.

    The temperature and the thinking of each step come from *profile*; the temperature the
    factory is asked for is not used, because ``--temperature`` is already the profile's step ①.
    """
    from teachers_teammate.domain.request_profile import DEFAULT_REQUEST_PROFILE  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        ollama_exchange_corrector,
    )
    from teachers_teammate.infrastructure.proofreading.port import OllamaPort  # noqa: PLC0415

    chosen = DEFAULT_REQUEST_PROFILE if profile is None else profile
    asked = frozenset(step.value for step in steps)
    every = frozenset(step.value for step in ALL_STEPS)

    def build(model: str, temperature: float) -> Corrector:  # noqa: ARG001  # see the docstring
        port = (
            None
            if asked >= every
            else OnlyStepsPort(OllamaPort(client, model, timeout=timeout, num_ctx=num_ctx), asked)
        )
        inner = ollama_exchange_corrector(
            client, model, timeout=timeout, num_ctx=num_ctx, profile=chosen, port=port
        )
        if Step.EXPLAIN not in steps:
            inner = NoExplanationCorrector(inner)
        return ChunkingCorrector(inner)

    return build


SKIPPED_STEP = "step skipped by the evaluation"
"""The reason the port gives for a step a correct-only run does not send; never reaches a row."""


class OnlyStepsPort:
    """A model port that sends the requests of some steps and fails the others at once.

    Step ① of the exchange needs nothing from the later steps, and the exchange treats a failed
    step ② or ③ as "no explanations, no unclear list" without failing the file.  So a port that
    refuses those two steps gives the exchange's own corrected text for the time of step ① alone:
    the code path, the prompt, the cut-off handling and the text budget are the exchange's, none
    of them is copied here.
    """

    def __init__(self, port: ModelPort, steps: frozenset[str]) -> None:
        self._port = port
        self._steps = steps

    def window(self) -> ContextBudget | None:
        """Return the window of the real port, so a text is split exactly as in the exchange."""
        return self._port.window()

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Send *req* when its step is asked for, else fail it without a request."""
        return self._port.ask_text(req) if req.step in self._steps else self._skipped()

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Send *req* when its step is asked for, else fail it without a request."""
        return self._port.ask_structured(req) if req.step in self._steps else self._skipped()

    @staticmethod
    def _skipped() -> PortAnswer:
        from teachers_teammate.infrastructure.proofreading.port import (  # noqa: PLC0415
            PortAnswer,
        )

        return PortAnswer(failure="transport", error=SKIPPED_STEP)


class NoExplanationCorrector(Corrector):
    """The exchange's corrector as a run that does not ask for explanations sees it.

    What the skipped step ② leaves behind (unexplained edits, a warning that explanations are
    missing) is not part of the answer to "what does step ① correct", so it is dropped; a file
    whose step ① failed keeps its failure and its warning.
    """

    def __init__(self, inner: Corrector) -> None:
        self._inner = inner

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the corrected text with nothing from the steps that did not run."""
        from teachers_teammate.domain.proofreading.result import (  # noqa: PLC0415
            CorrectionOutcome,
        )

        outcome = self._inner.correct(raw_text, language, text_format=text_format)
        if outcome.failed:
            return outcome
        return CorrectionOutcome(text=outcome.text)

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the budget of the exchange, so the pieces are the exchange's pieces."""
        return self._inner.max_text_chars(language, text_format=text_format)

    def pieces(self, text: str, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the pieces of the exchange, so the count is the exchange's count."""
        return self._inner.pieces(text, language, text_format=text_format)


def innermost(corrector: Corrector) -> Corrector:
    """Return the corrector that handles one piece: look through the chunking wrapper.

    Only that wrapper is looked through, so a corrector of another kind (a scripted one in a
    test) is returned as it is.
    """
    from teachers_teammate.infrastructure.proofreading.chunking import (  # noqa: PLC0415
        ChunkingCorrector,
    )

    while isinstance(corrector, ChunkingCorrector):
        corrector = corrector.inner
    return corrector


def pieces_of(corrector: Corrector, source: str, language: str) -> int:
    """Return how many pieces the app splits *source* into: one when it fits one request.

    Asks the corrector itself
    (:meth:`~teachers_teammate.interfaces.Corrector.pieces`), so the count cannot drift from what
    it does.  A corrector that sends any length in one request answers ``None``.  For a text that
    failed, the pieces after the failed one were not sent; the figure is what the app would have
    done with a text that went through.
    """
    return corrector.pieces(source, language) or 1
