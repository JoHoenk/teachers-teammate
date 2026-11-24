"""Shared LLM-invocation helpers for the correction and evaluation stages.

Both stages build a ``(system, human)`` chat prompt and invoke either a
LangChain chain or a native Ollama chat call.  Each returns an :class:`InvokeAnswer`: the
answer, and how the request ended when it did not come back whole — the caller decides whether
that is a failure of its stage.  These two helpers capture that shared shape so the
``Corrector`` / ``Evaluator`` implementations do not repeat the prompt-build and error-handling
boilerplate.

A cut-off answer is read in one place for every provider (:func:`answer_was_cut_off`): the fact
is reported in the response metadata, under one of three names, and an answer whose metadata
says nothing counts as whole.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Literal, cast

from ..domain.context_budget import BASE_NUM_CTX
from ..exceptions import TRUNCATED_MESSAGE, RunStoppedError
from ..texts.review import ANSWER_NOT_STRUCTURED, correction_failed

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient


@dataclass(frozen=True)
class InvokeAnswer:
    """What one request to a model returned: the text, and how it ended."""

    text: str = ""
    warning: str | None = None
    """Why the request did not answer as asked; ``None`` when it did."""
    truncated: bool = False
    """Whether the output limit cut the answer off: its end is missing, so it must not replace
    the text it was asked about."""


@dataclass(frozen=True)
class ChatRequest:
    """One chat request to a model: what it says, and the window it says it in."""

    model: str
    prompt: str
    temperature: float
    timeout: float
    #: Sent as a system message; empty sends none.
    system: str = ""
    #: The context window this request asks Ollama for (resolved once per run).
    num_ctx: int = BASE_NUM_CTX
    #: Reasoning on, off, or at the level the server names.
    think: bool | str = False
    #: A JSON schema the answer is constrained to; ``None`` leaves it free.
    format: dict | None = None
    #: How many tokens the answer may take; ``None`` — as many as the request, which is the
    #: proofreading shape.  A content review passes a short fixed allowance.
    answer_tokens: int | None = None


#: The finish reasons that say the provider stopped at its output limit rather than because it
#: was done.
_CUT_OFF_REASONS = frozenset({"length", "max_tokens", "MAX_TOKENS"})


def answer_was_cut_off(message: object) -> bool:
    """Return whether the output limit cut *message* off.

    Each provider names the same fact in its own field and its own word: OpenAI and Mistral
    ``finish_reason == "length"``, Anthropic ``stop_reason == "max_tokens"``, Gemini
    ``finish_reason == "MAX_TOKENS"``.  Metadata that says nothing counts as a whole answer, so
    a provider the app cannot read is never reported as cut off.
    """
    metadata = getattr(message, "response_metadata", None) or {}
    if not isinstance(metadata, dict):
        return False
    reasons = (metadata.get("finish_reason"), metadata.get("stop_reason"))
    return any(str(reason) in _CUT_OFF_REASONS for reason in reasons if reason)


def invoke_langchain_chain(
    llm: BaseChatModel,
    *,
    system: str,
    human: str,
    variables: dict[str, str],
    fallback: str,
    failure: Callable[[Exception], str],
) -> InvokeAnswer:
    """Build a ``(system, human)`` chain and invoke it.

    The message the provider returned is kept instead of being parsed to its text: the finish
    reason lives in the message, and a parser drops it.  On any provider error, returns
    *fallback* with the warning *failure* words for it.
    """
    from langchain_core.prompts import ChatPromptTemplate  # noqa: PLC0415

    template = ChatPromptTemplate.from_messages([("system", system), ("human", human)])
    try:
        message = (template | llm).invoke(variables)
    except Exception as exc:  # noqa: BLE001  # LangChain raises provider-specific errors; degrade with a warning
        return InvokeAnswer(fallback, failure(exc))
    return InvokeAnswer(_message_text(message), truncated=answer_was_cut_off(message))


FailureKind = Literal["", "transport", "unsupported", "invalid", "truncated"]
"""``transport``: the provider call failed.  ``unsupported``: the model or provider cannot
produce a schema-bound answer at all.  ``invalid``: it answered, but not with the schema.
``truncated``: it answered, but the context window cut the answer off — asking again would cut
it off again, so this is a failure, not something to repair."""


@dataclass(frozen=True)
class StructuredResult[T]:
    """Outcome of one schema-bound request."""

    parsed: T | None = None
    """The validated answer; ``None`` on any failure."""
    raw: str = ""
    """What the model said, for the repair request (empty when it said nothing usable)."""
    error: str = ""
    """For ``transport``, the complete warning to show; otherwise the reason, for the model."""
    failure: FailureKind = ""


def _message_text(message: object) -> str:
    """Return the text of a LangChain message; the arguments of its tool calls if it has none."""
    content = getattr(message, "content", message)
    if isinstance(content, list):  # providers such as Anthropic answer with content blocks
        content = "".join(
            str(cast("dict[str, Any]", part).get("text", ""))
            if isinstance(part, dict)
            else str(part)
            for part in content
        )
    text = str(content or "")
    if text.strip():
        return text
    calls = getattr(message, "tool_calls", None) or []
    return json.dumps([call.get("args", {}) for call in calls]) if calls else ""


#: HTTP statuses of a request the provider understood but will not serve in this shape.
_REJECTED_STATUSES = frozenset({400, 422})


def _rejected_request(exc: BaseException) -> bool:
    """Return whether *exc* is the provider rejecting the request itself (HTTP 400 / 422).

    Network failures, timeouts, authentication and rate limits carry other statuses (or none)
    and stay transport errors; the provider SDKs expose the status as ``status_code`` or
    ``code``, and an ``httpx`` error (Mistral) only on its ``response``.
    """
    status = (
        getattr(exc, "status_code", None)
        or getattr(exc, "code", None)
        or getattr(getattr(exc, "response", None), "status_code", None)
    )
    return isinstance(status, int) and status in _REJECTED_STATUSES


def invoke_langchain_structured[T](
    llm: BaseChatModel,
    *,
    system: str,
    human: str,
    variables: dict[str, str],
    schema: type[T],
    strict: bool = False,
) -> StructuredResult[T]:
    """Invoke *llm* constrained to *schema*.

    ``include_raw=True`` makes the integration return ``{"raw", "parsed", "parsing_error"}``
    instead of raising, so a bad answer arrives here as ``parsed=None`` together with its raw
    text -- which is what the repair retry needs to quote back to the model.  A provider that
    cannot bind a schema at all is reported as ``unsupported`` so the caller can fall back to a
    plain answer instead of treating it like a network error.
    """
    from langchain_core.prompts import ChatPromptTemplate  # noqa: PLC0415

    try:
        if strict:
            structured = llm.with_structured_output(
                schema, method="json_schema", strict=True, include_raw=True
            )
        else:
            structured = llm.with_structured_output(schema, include_raw=True)
    except Exception as exc:  # noqa: BLE001  # NotImplementedError, TypeError, ValueError, ... all mean "cannot bind a schema"
        return StructuredResult(error=str(exc), failure="unsupported")
    template = ChatPromptTemplate.from_messages([("system", system), ("human", human)])
    try:
        answer = (template | structured).invoke(variables)
    except Exception as exc:  # noqa: BLE001  # LangChain raises provider-specific errors; degrade with a warning
        if _rejected_request(exc):
            # The provider took the schema when it was bound but refuses it when it is sent
            # (e.g. OpenAI's json_schema on gpt-3.5-turbo): a plain answer may still work.
            return StructuredResult(error=str(exc), failure="unsupported")
        return StructuredResult(error=correction_failed(exc), failure="transport")
    raw = answer.get("raw") if isinstance(answer, dict) else answer
    parsed = answer.get("parsed") if isinstance(answer, dict) else answer
    if parsed is None:
        error = answer.get("parsing_error") if isinstance(answer, dict) else None
        return StructuredResult(
            raw=_message_text(raw),
            error=str(error or ANSWER_NOT_STRUCTURED),
            failure="invalid",
        )
    return StructuredResult(parsed=cast("T", parsed), raw=_message_text(raw))


def invoke_ollama_structured[T](
    client: OllamaClient,
    request: ChatRequest,
    *,
    parse: Callable[[str], tuple[T | None, str]],
) -> StructuredResult[T]:
    """Invoke a native Ollama chat constrained to the schema of *request*.

    *parse* validates the answer text (``CorrectionAnswer.model_validate_json``) and returns
    ``(value, error)``, so the caller decides what a valid answer means.  An answer the context
    window cut off is reported as ``truncated``: it cannot be repaired, because the repair answer
    would be cut off as well.
    """
    from ..exceptions import ResponseTruncatedError  # noqa: PLC0415
    from .ollama_utils import proofreading_options  # noqa: PLC0415

    try:
        text = client.chat(
            request.model,
            request.prompt,
            options=proofreading_options(request.temperature, request.num_ctx),
            think=request.think,
            timeout=request.timeout,
            format=request.format,
            system=request.system,
        )
    except ResponseTruncatedError:
        return StructuredResult(error=TRUNCATED_MESSAGE, failure="truncated")
    except RunStoppedError:
        raise  # a stop is not a failed request: the caller leaves the stage
    except Exception as exc:  # noqa: BLE001  # client may raise any transport error; degrade with a warning
        if _rejected_request(exc):
            # An older server or a model that refuses ``format``: a plain answer may still work.
            return StructuredResult(error=str(exc), failure="unsupported")
        return StructuredResult(error=correction_failed(exc), failure="transport")
    parsed, error = parse(text)
    if parsed is None:
        return StructuredResult(raw=text, error=error, failure="invalid")
    return StructuredResult(parsed=parsed, raw=text)
