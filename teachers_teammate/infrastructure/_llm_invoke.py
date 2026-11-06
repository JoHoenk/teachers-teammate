"""Shared LLM-invocation helpers for the correction and evaluation stages.

Both stages build a ``(system, human)`` chat prompt and invoke either a
LangChain chain or a native Ollama chat call, returning ``(result, None)`` on
success or ``(fallback, warning)`` on any error.  These two helpers capture that
shared shape so the ``Corrector`` / ``Evaluator`` implementations do not repeat
the prompt-build and error-handling boilerplate.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient

#: Warning used when a correction call fails at the transport level.
_CORRECTION_FAILURE = "Correction failed ({exc}); keeping original text."


def invoke_langchain_chain(
    llm: BaseChatModel,
    *,
    system: str,
    human: str,
    variables: dict[str, str],
    fallback: str,
    failure_prefix: str,
    failure_suffix: str = ".",
) -> tuple[str, str | None]:
    """Build a ``(system, human)`` chain, invoke it, and return ``(text, warning)``.

    On any provider error, returns ``(fallback, "<failure_prefix> (<exc>)<failure_suffix>")``.
    """
    from langchain_core.output_parsers import StrOutputParser  # noqa: PLC0415
    from langchain_core.prompts import ChatPromptTemplate  # noqa: PLC0415

    template = ChatPromptTemplate.from_messages([("system", system), ("human", human)])
    chain = template | llm | StrOutputParser()
    try:
        return chain.invoke(variables), None
    except Exception as exc:  # noqa: BLE001  # LangChain raises provider-specific errors; degrade with a warning
        return fallback, f"{failure_prefix} ({exc}){failure_suffix}"


def _failure_warning(template: str, exc: Exception) -> str:
    """Return the warning text of *template*, with ``{exc}`` replaced by *exc*."""
    return template.replace("{exc}", str(exc))


def invoke_ollama_chat(  # noqa: PLR0913  # one keyword-only call shape shared by every Ollama caller
    client: OllamaClient,
    *,
    model: str,
    prompt: str,
    timeout: float,
    temperature: float,
    fallback: str,
    failure: str,
    system: str = "",
    format: dict | None = None,  # pylint: disable=redefined-builtin  # the ollama API's name
) -> tuple[str, str | None]:
    """Invoke a native Ollama chat completion and return ``(text, warning)``.

    On any transport error, returns ``(fallback, failure)`` with the ``{exc}`` placeholder in
    *failure* replaced by the exception.  *system* is sent as a system message; *format* passes
    a JSON schema to Ollama, which then constrains the answer to it.
    """
    try:
        return (
            client.chat(
                model,
                prompt,
                options={"temperature": temperature},
                timeout=timeout,
                format=format,
                system=system,
            ),
            None,
        )
    except Exception as exc:  # noqa: BLE001  # client may raise any transport error; degrade with a warning
        return fallback, _failure_warning(failure, exc)


def tidy_answer(
    result: tuple[str, str | None], postprocess: Callable[[str], str]
) -> tuple[str, str | None]:
    """Apply *postprocess* to the text of a successful ``(text, warning)`` result.

    A failed call returns its fallback with a warning; that fallback is the caller's own
    text and stays exactly as it was.
    """
    text, warning = result
    return (text, warning) if warning is not None else (postprocess(text), None)


FailureKind = Literal["", "transport", "unsupported", "invalid"]
"""``transport``: the provider call failed.  ``unsupported``: the model or provider cannot
produce a schema-bound answer at all.  ``invalid``: it answered, but not with the schema."""


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
    ``code``.
    """
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
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
        return StructuredResult(
            error=_failure_warning(_CORRECTION_FAILURE, exc), failure="transport"
        )
    raw = answer.get("raw") if isinstance(answer, dict) else answer
    parsed = answer.get("parsed") if isinstance(answer, dict) else answer
    if parsed is None:
        error = answer.get("parsing_error") if isinstance(answer, dict) else None
        return StructuredResult(
            raw=_message_text(raw),
            error=str(error or "the answer did not match the required structure"),
            failure="invalid",
        )
    return StructuredResult(parsed=cast("T", parsed), raw=_message_text(raw))


def invoke_ollama_structured[T](
    client: OllamaClient,
    *,
    model: str,
    prompt: str,
    schema: dict,
    timeout: float,
    temperature: float,
    parse: Callable[[str], tuple[T | None, str]],
    system: str = "",
) -> StructuredResult[T]:
    """Invoke a native Ollama chat constrained to *schema*.

    *parse* validates the answer text (``CorrectionAnswer.model_validate_json``) and returns
    ``(value, error)``, so the caller decides what a valid answer means.
    """
    try:
        text = client.chat(
            model,
            prompt,
            options={"temperature": temperature},
            timeout=timeout,
            format=schema,
            system=system,
        )
    except Exception as exc:  # noqa: BLE001  # client may raise any transport error; degrade with a warning
        if _rejected_request(exc):
            # An older server or a model that refuses ``format``: a plain answer may still work.
            return StructuredResult(error=str(exc), failure="unsupported")
        return StructuredResult(
            error=_failure_warning(_CORRECTION_FAILURE, exc), failure="transport"
        )
    parsed, error = parse(text)
    if parsed is None:
        return StructuredResult(raw=text, error=error, failure="invalid")
    return StructuredResult(parsed=parsed, raw=text)
