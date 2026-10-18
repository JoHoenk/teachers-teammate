"""Shared LLM-invocation helpers for the correction and evaluation stages.

Both stages build a ``(system, human)`` chat prompt and invoke either a
LangChain chain or a native Ollama chat call, returning ``(result, None)`` on
success or ``(fallback, warning)`` on any error.  These two helpers capture that
shared shape so the ``Corrector`` / ``Evaluator`` implementations do not repeat
the prompt-build and error-handling boilerplate.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient


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


def invoke_ollama_chat(
    client: OllamaClient,
    *,
    model: str,
    prompt: str,
    timeout: float,
    temperature: float,
    fallback: str,
    failure_prefix: str,
    failure_suffix: str = ".",
) -> tuple[str, str | None]:
    """Invoke a native Ollama chat completion and return ``(text, warning)``.

    On any transport error, returns ``(fallback, "<failure_prefix> (<exc>)<failure_suffix>")``.
    """
    try:
        return (
            client.chat(model, prompt, options={"temperature": temperature}, timeout=timeout),
            None,
        )
    except Exception as exc:  # noqa: BLE001  # client may raise any transport error; degrade with a warning
        return fallback, f"{failure_prefix} ({exc}){failure_suffix}"


def tidy_answer(
    result: tuple[str, str | None], postprocess: Callable[[str], str]
) -> tuple[str, str | None]:
    """Apply *postprocess* to the text of a successful ``(text, warning)`` result.

    A failed call returns its fallback with a warning; that fallback is the caller's own
    text and stays exactly as it was.
    """
    text, warning = result
    return (text, warning) if warning is not None else (postprocess(text), None)
