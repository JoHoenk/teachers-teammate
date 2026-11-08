"""The one seam between the writing-model checks and a real Ollama server.

Everything above this module talks to a :class:`ChatPort`, so the rule that decides a model's
verdict can be tested with scripted answers instead of against a live server.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from teachers_teammate.infrastructure.ollama_utils import ChatStats


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
    ) -> tuple[str, ChatStats]:
        """Send one chat request and return ``(text, stats)``; a cut-off answer is returned."""

    def fits(self, system: str, prompt: str, *, answer_tokens: int | None = None) -> bool:
        """Return whether this request fits the context window the port will send it at."""


class OllamaChatPort:
    """Adapt :class:`~teachers_teammate.infrastructure.ollama_utils.OllamaClient` to ``ChatPort``.

    The size check is not repeated here: :meth:`fits` delegates to the app's
    :class:`~teachers_teammate.domain.context_budget.ContextBudget`, so the tool and the app
    cannot disagree about what fits.
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
    ) -> tuple[str, ChatStats]:
        """Send one chat request through the app's client."""
        return self._client.chat_with_stats(
            model,
            prompt,
            options=options,
            timeout=timeout,
            format=format,
            system=system,
        )

    def fits(self, system: str, prompt: str, *, answer_tokens: int | None = None) -> bool:
        """Return whether the request fits the window, with the app's own rule."""
        from teachers_teammate.domain.context_budget import (  # noqa: PLC0415
            ContextBudget,
            answers_with_the_same_text,
        )

        allowance = (
            answers_with_the_same_text(self._num_ctx) if answer_tokens is None else answer_tokens
        )
        return ContextBudget(self._num_ctx).request_fits(
            len(system) + len(prompt), answer_tokens=allowance
        )
