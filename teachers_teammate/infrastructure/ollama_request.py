"""What an Ollama request carries, and what the server reports about the answer."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from ..domain.context_budget import BASE_NUM_CTX, ContextBudget, answers_with_the_same_text
from ..exceptions import OCRError, ResponseTruncatedError


def ensure_prompt_fits(
    system: str,
    prompt: str,
    num_ctx: int,
    *,
    answer_tokens: int | None = None,
) -> None:
    """Raise :exc:`~teachers_teammate.exceptions.ResponseTruncatedError` if a request is too long.

    Ollama silently drops the middle of a prompt that is longer than ``num_ctx`` and still reports
    ``done_reason="stop"``, so a prompt overflow cannot be seen in the answer.  (A *long answer*
    can: it ends with ``done_reason="length"``.)  The client library cannot send
    ``truncate=false`` either, so the size is checked here, by estimate, before the request goes
    out.  The rule itself lives in :class:`~teachers_teammate.domain.context_budget.ContextBudget`.

    Args:
        answer_tokens: Tokens reserved for the answer.  ``None`` — the answer repeats (part of)
            the text it was given, so the request may take at most half of the window.
    """
    budget = ContextBudget(num_ctx)
    allowance = answers_with_the_same_text(num_ctx) if answer_tokens is None else answer_tokens
    if not budget.request_fits(len(system) + len(prompt), answer_tokens=allowance):
        raise ResponseTruncatedError()


def proofreading_options(
    temperature: float,
    num_ctx: int = BASE_NUM_CTX,
    sampling: Mapping[str, float] | None = None,
) -> dict:
    """Return the Ollama options of a proofreading or content-review request.

    The one place that decides what such a request sends, so the model check
    (``tools/verify_model_catalog.py``) exercises the same options as the app.  *sampling* is the
    step's extra sampling values (:attr:`~teachers_teammate.domain.request_profile.StepProfile.
    options`); without it the request carries nothing but the temperature and the window, and
    the model's own tag decides the rest.
    """
    return {**(sampling or {}), "temperature": temperature, "num_ctx": num_ctx}


def rejects_think(exc: OCRError) -> bool:
    """Return whether *exc* is Ollama refusing the ``think`` flag (a 400 that names it)."""
    return exc.status_code == 400 and "think" in str(exc).lower()


def _chat_messages(prompt: str, images: list[str] | None, system: str) -> list[dict]:
    """Return the messages of a chat: the system message when there is one, then the user's."""
    message: dict = {"role": "user", "content": prompt}
    if images:
        message["images"] = images
    return [{"role": "system", "content": system}, message] if system else [message]


def chat_request(
    model: str,
    prompt: str,
    *,
    images: list[str] | None,
    options: dict | None,
    response_format: dict | str | None,
    system: str,
) -> dict:
    """Return the body of one chat request, in the shape the Ollama client takes."""
    request: dict = {
        "model": model,
        "messages": _chat_messages(prompt, images, system),
        "options": options,
    }
    if response_format is not None:
        request["format"] = response_format
    return request


def open_chat_stream(client: Any, request: dict, think: bool | str | None) -> Iterator:
    """Open a streaming chat for *request*; ``think=None`` leaves the argument out."""
    kwargs: dict = {**request, "stream": True}
    if think is not None:
        kwargs["think"] = think
    return client.chat(**kwargs)


@dataclass(frozen=True)
class ChatStats:
    """What Ollama reported about the answer it just finished (defaults when it said nothing).

    Kept so a caller can tell a complete answer from one the context window cut off, and so
    the token counts of a real run are visible in the log and in the model check.
    """

    done_reason: str = ""
    """``"stop"`` for a complete answer, ``"length"`` when the context window was full."""
    prompt_eval_count: int = 0
    """Tokens of the request (system instructions, answer format and text)."""
    eval_count: int = 0
    """Tokens of the answer."""
    total_duration: int = 0
    """Nanoseconds the server spent on the whole request (loading the model included)."""
    load_duration: int = 0
    """Nanoseconds spent loading the model; near zero when it was already in memory."""
    prompt_eval_duration: int = 0
    """Nanoseconds spent reading the request."""
    eval_duration: int = 0
    """Nanoseconds spent writing the answer: ``eval_count`` tokens over this is the speed."""
    waited: bool = False
    """The request waited behind another model of the server, so ``load_duration`` counts that
    wait as well and is no measurement of loading."""

    @property
    def truncated(self) -> bool:
        """Whether the model stopped because it ran out of context, not because it was done."""
        return self.done_reason == "length"


def chunk_stats(chunk: object) -> ChatStats:
    """Return the statistics of one stream chunk (empty ones for a chunk that reports none)."""
    done_reason = getattr(chunk, "done_reason", "")

    def count(name: str) -> int:
        value = getattr(chunk, name, 0)
        return value if isinstance(value, int) else 0

    return ChatStats(
        done_reason=done_reason if isinstance(done_reason, str) else "",
        prompt_eval_count=count("prompt_eval_count"),
        eval_count=count("eval_count"),
        total_duration=count("total_duration"),
        load_duration=count("load_duration"),
        prompt_eval_duration=count("prompt_eval_duration"),
        eval_duration=count("eval_duration"),
    )
