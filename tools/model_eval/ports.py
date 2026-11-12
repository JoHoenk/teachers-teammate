"""The seams between the writing-model checks and a real Ollama server.

Everything above this module talks to a :class:`ChatPort`, so the rule that decides a model's
verdict can be tested with scripted answers instead of against a live server.
"""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from teachers_teammate.infrastructure.ollama_utils import ChatStats

    from .performance import LoadedModel, RequestStats


class ServerUnavailableError(RuntimeError):
    """The Ollama server cannot be reached, so nothing about a model can be judged.

    Raised instead of recording a failed request: a run that met an outage would otherwise write
    "the model failed" for every text it sent, and gate 1 would fail the model for good.
    """


@runtime_checkable
class ChatPort(Protocol):
    """A chat endpoint that also reports how the answer ended."""

    def chat_with_stats(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        options: dict | None = None,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Send one chat request and return ``(text, stats)``; a cut-off answer is returned.

        *timeout* bounds the wait for the first token; *total_timeout*, when given, bounds the
        whole answer and raises ``OCRDeadlineError`` when it is still running.
        """


class OllamaChatPort:
    """Adapt :class:`~teachers_teammate.infrastructure.ollama_utils.OllamaClient` to ``ChatPort``.

    The size of a request is not checked here: the structured-output check asks the app's own
    corrector how much text one review request carries
    (:func:`~.structured_check.review_budget`), so the tool and the app cannot disagree.
    """

    def __init__(self, url: str, *, num_ctx: int | None = None) -> None:
        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            PROOFREADING_NUM_CTX,
            OllamaClient,
        )

        self._client = OllamaClient(url)
        self._num_ctx = PROOFREADING_NUM_CTX if num_ctx is None else num_ctx

    @property
    def num_ctx(self) -> int:
        """Return the context window this port sends its requests at."""
        return self._num_ctx

    @property
    def client(self):
        """Return the wrapped :class:`OllamaClient` (for ``model_capabilities`` and the like)."""
        return self._client

    def chat_with_stats(
        self,
        model: str,
        prompt: str,
        *,
        system: str = "",
        options: dict | None = None,
        timeout: float = 120.0,
        format: dict | str | None = None,  # pylint: disable=redefined-builtin
        total_timeout: float | None = None,
    ) -> tuple[str, ChatStats]:
        """Send one chat request through the app's client."""
        return self._client.chat_with_stats(
            model,
            prompt,
            options=options,
            timeout=timeout,
            format=format,
            system=system,
            total_timeout=total_timeout,
        )


# ── the seams of a measured run ───────────────────────────────────────────────


class ProbePort(Protocol):
    """What a run needs from the server: its version, a model's details, what is loaded."""

    def version(self) -> str:
        """Return the server's version."""

    def model_info(self, tag: str) -> dict:
        """Return digest, quantisation, parameter size and family of an installed *tag*."""

    def loaded(self) -> tuple[LoadedModel, ...]:
        """Return the models the server holds in memory right now."""

    def unload(self, name: str) -> None:
        """Ask the server to drop *name* from memory."""

    def unload_all(
        self, *, wait_s: float = 60.0, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        """Unload every model and wait until the server reports none."""


class RequestRecorder(Protocol):
    """What :class:`MeasuredRun` needs from the client: the seed and the recorded requests."""

    @property
    def seed(self) -> int | None:
        """The seed sent with every request, if any."""

    def drain(self) -> tuple[RequestStats, ...]:
        """Return the requests recorded so far and forget them."""


class RecordingPort(RequestRecorder, Protocol):
    """What a run needs from the client: the seed and the recorded requests.

    The factory seam receives the same object and knows the rest of it: the app's client, which
    the corrector sends its requests through.
    """
