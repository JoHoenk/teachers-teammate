"""The app's own corrector, as the tools build and measure it.

A teacher's run builds a :class:`~teachers_teammate.infrastructure.correction.NativeOllamaCorrector`
and wraps it in :class:`~teachers_teammate.infrastructure.correction.ChunkingCorrector`
(``stage_builder._chunked``).  The comparison and the protocol fingerprint build the same chain
here, so what they measure and hash is the request a teacher's run sends.  This module needs no
server and imports nothing from :mod:`.measure`, :mod:`.report` or the command line.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from teachers_teammate.infrastructure.ollama_utils import PROOFREADING_NUM_CTX
from teachers_teammate.interfaces import Corrector

from .answer_mode import AnswerMode

if TYPE_CHECKING:
    from teachers_teammate.domain.context_budget import ContextBudget
    from teachers_teammate.domain.correction_review import CorrectionOutcome
    from teachers_teammate.domain.formatted_text import TextFormat
    from teachers_teammate.domain.model_catalog import RequestProfile
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient
    from teachers_teammate.infrastructure.proofreading.port import (
        ModelPort,
        PortAnswer,
        StepRequest,
    )


CorrectorFactory = Callable[[str, float], "Corrector"]
"""``(model, temperature) -> corrector``; replaced in tests."""


def app_corrector_factory(
    client: OllamaClient,
    *,
    timeout: float,
    mode: AnswerMode,
    num_ctx: int = PROOFREADING_NUM_CTX,
    profile: RequestProfile | None = None,
) -> CorrectorFactory:
    """Return a factory building the app's own corrector on *client*, asking in *mode*.

    The corrector is the one a teacher's run uses: a ``NativeOllamaCorrector`` wrapped in
    ``ChunkingCorrector``.  That is what ``stage_builder._chunked`` builds for every Ollama
    corrector, whose ``max_text_chars`` is never ``None``, so a text longer than one request is
    proofread in pieces here too.  ``test_app_corrector.py`` compares the two chains, so a
    change of that wrapping is found.

    Args:
        client: The Ollama client the requests go through (a recording one in a measured run).
        timeout: Seconds one request may take.
        mode: ``review`` (the app's JSON answer with explanations) or ``text``.
        num_ctx: The context window the corrector asks for; the app's proofreading window by
            default.
        profile: The request profile the corrector asks with (the compact-JSON rule and the
            temperature); the default for a model the catalog does not know.

    Raises:
        ValueError: if *mode* is not one the app's corrector answers in (``two-step`` asks it in
            text mode, which nothing runs yet).
    """
    if not mode.asks_corrector:
        raise ValueError(
            f"the app's corrector cannot be asked in {mode.value!r}: "
            "two-step asks the corrector in text mode, which nothing runs yet"
        )
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DEFAULT_REQUEST_PROFILE,
    )
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        ChunkingCorrector,
        CorrectionSettings,
        NativeOllamaCorrector,
    )

    chosen = DEFAULT_REQUEST_PROFILE if profile is None else profile

    def build(model: str, temperature: float) -> Corrector:
        return ChunkingCorrector(
            NativeOllamaCorrector(
                model,
                client,
                timeout=timeout,
                temperature=temperature,
                settings=CorrectionSettings(output_mode=mode, num_ctx=num_ctx),
                profile=chosen,
            )
        )

    return build


def exchange_corrector_factory(
    client: OllamaClient,
    *,
    timeout: float,
    num_ctx: int = PROOFREADING_NUM_CTX,
    profile: RequestProfile | None = None,
    steps: frozenset[str] | None = None,
) -> CorrectorFactory:
    """Return a factory building the exchange's corrector on *client*.

    The corrector is the app's :class:`~teachers_teammate.infrastructure.proofreading.corrector.
    ExchangeCorrector`, wrapped in ``ChunkingCorrector`` as ``stage_builder._chunked`` wraps
    every corrector with a bounded request window.  The temperature and the thinking of each step come from *profile*; the
    temperature the factory is asked for is not used, because ``--temperature`` is already the
    profile's step ①.

    Args:
        client: The Ollama client the requests go through (a recording one in a measured run).
        timeout: Seconds one request may take.
        num_ctx: The context window the port asks for.
        profile: The request profile; the default for a model the catalog does not know.
        steps: The steps to send, or ``None`` for all of them.  ``{"correct"}`` asks step ① alone
            (the ``correct-only`` mode): the other steps fail at the port without a request.
    """
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DEFAULT_REQUEST_PROFILE,
    )
    from teachers_teammate.infrastructure.correction import ChunkingCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        build_exchange_corrector,
    )
    from teachers_teammate.infrastructure.proofreading.port import OllamaPort  # noqa: PLC0415

    chosen = DEFAULT_REQUEST_PROFILE if profile is None else profile

    def build(model: str, temperature: float) -> Corrector:  # noqa: ARG001  # see the docstring
        if steps is None:
            return ChunkingCorrector(
                build_exchange_corrector(
                    client, model, timeout=timeout, num_ctx=num_ctx, profile=chosen
                )
            )
        port = OnlyStepsPort(OllamaPort(client, model, timeout=timeout, num_ctx=num_ctx), steps)
        return ChunkingCorrector(
            CorrectOnlyCorrector(
                build_exchange_corrector(
                    client,
                    model,
                    timeout=timeout,
                    num_ctx=num_ctx,
                    profile=chosen,
                    port=port,
                )
            )
        )

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


class CorrectOnlyCorrector(Corrector):
    """The exchange's corrector as a run of step ① alone sees it: text, no review, no warnings.

    What the skipped steps leave behind (unexplained edits, a warning that explanations are
    missing) is not part of the answer to "what does step ① correct", so it is dropped; a file
    whose step ① failed keeps its failure and its warning.
    """

    def __init__(self, inner: Corrector) -> None:
        self._inner = inner

    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the corrected text of step ① with nothing from the steps that did not run."""
        from teachers_teammate.domain.correction_review import (  # noqa: PLC0415
            CorrectionOutcome,
        )

        outcome = self._inner.correct(raw_text, language, text_format=text_format)
        if outcome.failed:
            return outcome
        return CorrectionOutcome(text=outcome.text)

    def max_text_chars(self, language: str, *, text_format: TextFormat = "plain") -> int | None:
        """Return the budget of the exchange, so the pieces are the exchange's pieces."""
        return self._inner.max_text_chars(language, text_format=text_format)


def innermost(corrector: Corrector) -> Corrector:
    """Return the corrector that handles one piece: look through the chunking wrapper.

    Only that wrapper is looked through, so a corrector of another kind (a scripted one in a
    test) is returned as it is.
    """
    from teachers_teammate.infrastructure.correction import ChunkingCorrector  # noqa: PLC0415

    while isinstance(corrector, ChunkingCorrector):
        corrector = corrector.inner
    return corrector


def pieces_of(corrector: Corrector, source: str, language: str) -> int:
    """Return how many pieces the app splits *source* into: one when it fits one request.

    Asks the app's own split (:meth:`~teachers_teammate.infrastructure.correction.
    ChunkingCorrector.split`), so the count cannot drift from what the corrector does, and counts
    the non-blank pieces.  A corrector that does not chunk sends the text whole.  For a text that
    failed, the pieces after the failed one were not sent; the figure is what the app would have
    done with a text that went through.
    """
    from teachers_teammate.infrastructure.correction import ChunkingCorrector  # noqa: PLC0415

    if not isinstance(corrector, ChunkingCorrector):
        return 1
    chunks = corrector.split(source, language)
    if chunks is None:
        return 1
    return max(1, len([chunk for chunk in chunks if chunk.text.strip()]))
