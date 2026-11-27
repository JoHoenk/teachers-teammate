"""The content review: one request about corrected text, over a connection to a model.

:class:`PortEvaluator` asks through a :class:`~.proofreading.port.ModelPort`, the same port
proofreading sends its steps over.  A local Ollama server and an online provider therefore get
exactly the same messages, and both report a cut-off answer the same way.  It implements
:class:`~teachers_teammate.interfaces.Evaluator`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..domain.formatted_text import TextFormat
    from .proofreading.port import ModelPort

from ..domain.context_budget import EVALUATION_ANSWER_TOKENS
from ..domain.evaluation import EvaluationOutcome
from ..domain.proofreading.result import FailureKind
from ..domain.review_prompts import review_message, review_system
from ..interfaces import Evaluator
from ..settings_schema import DEFAULTS
from ..texts.review import TOO_LONG_FOR_REVIEW_WARNING, evaluation_failed
from .proofreading.port import StepRequest


def _tidy_review(answer: str) -> str:
    """Drop reasoning blocks, control tokens and a wrapping code fence from a review."""
    from .ocr_formats import clean_llm_answer  # noqa: PLC0415

    return clean_llm_answer(answer)


class PortEvaluator(Evaluator):
    """Asks the review prompt about corrected text, over the run's connection to a model."""

    def __init__(
        self,
        port: ModelPort,
        prompt: str = "",
        *,
        temperature: float = 0.0,
        think: bool | str = False,
    ) -> None:
        self._port = port
        self._prompt = prompt or DEFAULTS["evaluate_prompt"]
        self._temperature = temperature
        self._think = think

    def evaluate(
        self, corrected_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> EvaluationOutcome:
        answer = self._port.ask_text(
            StepRequest(
                step="review",
                system=review_system(self._prompt, text_format),
                user=review_message(language, corrected_text),
                temperature=self._temperature,
                think=self._think,
                # A review's answer is short and fixed, so the text gets the rest of the window:
                # a content review cannot be split into pieces the way proofreading can.
                answer_factor=0.0,
                answer_tokens=EVALUATION_ANSWER_TOKENS,
                failed=evaluation_failed,
            )
        )
        if answer.failure:
            # A review is optional: one that could not be written is a warning on the file, and
            # the proofreading it was asked about is stored all the same.
            return EvaluationOutcome(warning=_warning_of(answer.failure, answer.error))
        return EvaluationOutcome(text=_tidy_review(answer.raw))


def _warning_of(failure: FailureKind, error: str) -> str:
    """Return what a teacher is told about a review that did not come back whole.

    ``error`` already carries the sentence for a failed request (the port words it as a review);
    a cut-off answer says why it cannot be reviewed instead.
    """
    if failure == "truncated":
        return TOO_LONG_FOR_REVIEW_WARNING
    return error
