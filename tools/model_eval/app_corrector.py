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

from .answer_mode import AnswerMode

if TYPE_CHECKING:
    from teachers_teammate.domain.model_catalog import RequestProfile
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient
    from teachers_teammate.interfaces import Corrector


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
    """
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DEFAULT_REQUEST_PROFILE,
    )
    from teachers_teammate.infrastructure.correction import ChunkingCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.proofreading.corrector import (  # noqa: PLC0415
        build_exchange_corrector,
    )

    chosen = DEFAULT_REQUEST_PROFILE if profile is None else profile

    def build(model: str, temperature: float) -> Corrector:  # noqa: ARG001  # see the docstring
        return ChunkingCorrector(
            build_exchange_corrector(
                client, model, timeout=timeout, num_ctx=num_ctx, profile=chosen
            )
        )

    return build


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
