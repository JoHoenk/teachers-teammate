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
from ..domain.context_budget import (
    EVALUATION_ANSWER_TOKENS,
    EVALUATION_NUM_CTX_CAP,
    ContextBudget,
)
from ..domain.formatted_text import TextFormat
from ..domain.notation import with_notation_hint
from ..interfaces import Evaluator
from ._llm_invoke import invoke_langchain_chain, invoke_ollama_chat, tidy_answer

TOO_LONG_FOR_REVIEW_WARNING = "The text is too long for a content review with a local model."
"""Warning of a content review whose text does not fit the window (its own sentence: the
proofreading warning is about a partly corrected text, which a review does not produce)."""


def _tidy_review(answer: str) -> str:
    """Drop reasoning blocks, control tokens and a wrapping code fence from a review."""
    from .ocr_formats import clean_llm_answer  # noqa: PLC0415

    return clean_llm_answer(answer)


class LangChainEvaluator(Evaluator):
    """Runs the evaluation prompt against corrected text."""

    def __init__(self, llm: BaseChatModel, prompt: str = "") -> None:
        self._llm = llm
        self._prompt = prompt or DEFAULTS["evaluate_prompt"]

    def evaluate(
        self, corrected_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> tuple[str, str | None]:
        result = invoke_langchain_chain(
            self._llm,
            system=with_notation_hint(self._prompt, text_format, task="evaluation"),
            human="Language: {language}\n\nCorrected text:\n\n{text}",
            variables={"text": corrected_text, "language": language},
            fallback="",
            failure_prefix="Evaluation failed",
        )
        return tidy_answer(result, _tidy_review)


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
        num_ctx: int = EVALUATION_NUM_CTX_CAP,
        think: bool | str = False,
    ) -> None:
        self._think = think
        self._model = model
        self._client = client
        self._prompt = prompt or DEFAULTS["evaluate_prompt"]
        self._timeout = timeout
        self._temperature = temperature
        self._num_ctx = num_ctx

    def _message(self, corrected_text: str, language: str, text_format: TextFormat) -> str:
        """Return the review request for this text."""
        system = with_notation_hint(self._prompt, text_format, task="evaluation")
        return f"{system}\n\nLanguage: {language}\n\nCorrected text:\n\n{corrected_text}"

    def fits(self, text: str, language: str, *, text_format: TextFormat = "plain") -> bool:
        """Return whether *text* fits one review request in this window.

        The same message and the same allowance :meth:`evaluate` sends, so a text this says
        fits is not refused with :exc:`~teachers_teammate.exceptions.ResponseTruncatedError`.
        A content review cannot be split into pieces, so a text that does not fit is not
        reviewed at all.
        """
        message = self._message(text, language, text_format)
        return ContextBudget(self._num_ctx).request_fits(
            len(message), answer_tokens=EVALUATION_ANSWER_TOKENS
        )

    def evaluate(
        self, corrected_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> tuple[str, str | None]:
        result = invoke_ollama_chat(
            self._client,
            model=self._model,
            prompt=self._message(corrected_text, language, text_format),
            timeout=self._timeout,
            temperature=self._temperature,
            think=self._think,
            fallback="",
            failure="Evaluation failed ({exc}).",
            num_ctx=self._num_ctx,
            # A review's answer is short and fixed, so the text gets the rest of the window:
            # a content review cannot be split into pieces the way proofreading can.
            answer_tokens=EVALUATION_ANSWER_TOKENS,
            truncated_warning=TOO_LONG_FOR_REVIEW_WARNING,
        )
        return tidy_answer(result, _tidy_review)
