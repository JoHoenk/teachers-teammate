"""Evaluation implementations for corrected OCR text.

:class:`LangChainEvaluator` — accepts any LangChain ``BaseChatModel``; provider-agnostic.
:class:`NativeOllamaEvaluator` — uses :class:`~.ollama_utils.OllamaClient` directly; no LangChain.

Both implement :class:`~teachers_teammate.interfaces.Evaluator`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from .ollama_utils import OllamaClient

from ..config import DEFAULTS
from ..interfaces import Evaluator
from ._llm_invoke import invoke_langchain_chain, invoke_ollama_chat


class LangChainEvaluator(Evaluator):
    """Runs the evaluation prompt against corrected text."""

    def __init__(self, llm: BaseChatModel, prompt: str = "") -> None:
        self._llm = llm
        self._prompt = prompt or DEFAULTS["evaluate_prompt"]

    def evaluate(self, corrected_text: str, language: str) -> tuple[str, str | None]:
        return invoke_langchain_chain(
            self._llm,
            system=self._prompt,
            human="Language: {language}\n\nCorrected text:\n\n{text}",
            variables={"text": corrected_text, "language": language},
            fallback="",
            failure_prefix="Evaluation failed",
        )


class NativeOllamaEvaluator(Evaluator):
    """Ollama-backed evaluation using the native HTTP API (no LangChain).

    Accepts an :class:`~.ollama_utils.OllamaClient` injected by the stage
    builder — the client is shared across pipeline stages and must not be
    constructed here.
    """

    def __init__(
        self,
        model: str,
        client: OllamaClient,
        prompt: str = "",
        timeout: float = 120.0,
        temperature: float = 0.7,
    ) -> None:
        self._model = model
        self._client = client
        self._prompt = prompt or DEFAULTS["evaluate_prompt"]
        self._timeout = timeout
        self._temperature = temperature

    def evaluate(self, corrected_text: str, language: str) -> tuple[str, str | None]:
        return invoke_ollama_chat(
            self._client,
            model=self._model,
            prompt=f"{self._prompt}\n\nLanguage: {language}\n\nCorrected text:\n\n{corrected_text}",
            timeout=self._timeout,
            temperature=self._temperature,
            fallback="",
            failure_prefix="Evaluation failed",
        )
