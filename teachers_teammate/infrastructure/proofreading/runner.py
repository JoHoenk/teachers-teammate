"""One place that sends a step request: retries, repair, window check, timeout.

Every step of every exchange goes through :class:`StepRunner`, so the rules that make a request
robust exist once instead of once per step and once per connection:

* A cut-off answer is asked again, up to the runner's ``cut_off_tries``.  A cut-off is usually a
  runaway (a model that starts listing correct fragments with an empty change), not a text that
  is too long, and a second try very likely answers.
* An answer that does not match the schema gets **one** repair request quoting the validation
  error and the previous answer.  A repair that cannot fit the context window is skipped before
  it is sent, which saves a round trip; a repair answer that is cut off is not retried, because
  the repair request is the longest of all and a retry would not make it shorter.
* A transport error ends the step: the next request would meet the same unreachable server.

Every request leaves a :class:`~teachers_teammate.domain.proofreading.result.StepReport`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, cast

from ...domain.context_budget import answers_with_the_same_text
from ...domain.proofreading.result import FailureKind, StepReport
from ..correction_schema import repair_message
from .port import ModelPort, PortAnswer, StepRequest

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class StepOutcome[T]:
    """What a step produced: the answer, how it ended, and the reports of its requests."""

    value: T | None = None
    """The answer, converted to the domain; ``None`` on any failure."""
    failure: FailureKind = ""
    error: str = ""
    """For ``transport``, the complete warning to show; otherwise the reason, for the model."""
    reports: tuple[StepReport, ...] = ()


class StepRunner:
    """Sends the requests of one step, with the retry and repair rules of the app."""

    def __init__(self, port: ModelPort, *, cut_off_tries: int = 1) -> None:
        self._port = port
        self._cut_off_tries = max(1, cut_off_tries)

    def run[T](
        self, req: StepRequest, parse: Callable[[str], tuple[T | None, str]]
    ) -> StepOutcome[T]:
        """Send *req* and return its answer, repairing and retrying as the rules say.

        *parse* validates the answer text and returns ``(value, error)``; it is not called when
        the provider already validated the answer itself.
        """
        reports: list[StepReport] = []
        answer = self._ask(req, reports)
        for _ in range(self._cut_off_tries - 1):
            if answer.failure != "truncated":
                break
            answer = self._ask(req, reports)
        if answer.failure == "truncated":
            return self._failed(answer, reports)
        if answer.failure == "transport":
            return self._failed(answer, reports)
        value, error = self._value(answer, parse)
        if value is None and req.repairable and answer.raw.strip():
            if not self._repair_fits(req, answer.raw, error):
                return StepOutcome(failure="invalid", error=error, reports=tuple(reports))
            answer = self._ask(req, reports, repair=(answer.raw, error))
            if answer.failure == "truncated":
                return self._failed(answer, reports)
            if answer.failure == "transport":
                return self._failed(answer, reports)
            value, error = self._value(answer, parse)
        if value is None:
            return StepOutcome(failure="invalid", error=error, reports=tuple(reports))
        return StepOutcome(value=value, reports=tuple(reports))

    @staticmethod
    def _value[T](
        answer: PortAnswer, parse: Callable[[str], tuple[T | None, str]]
    ) -> tuple[T | None, str]:
        """Return the answer as the domain value, or the reason it is not one.

        A provider that validates the answer itself says why it did not match; that reason is
        the one to quote back to the model.  The raw text of such an answer need not be the
        object the schema describes (a tool-calling provider returns a list of tool arguments),
        so parsing it again would only give a misleading error.
        """
        if answer.parsed is not None:
            return cast("T", answer.parsed), ""
        if answer.failure == "invalid" and answer.error:
            return None, answer.error
        return parse(answer.raw)

    def _ask(
        self, req: StepRequest, reports: list[StepReport], *, repair: tuple[str, str] | None = None
    ) -> PortAnswer:
        """Send one request of *req*, with the repair message when *repair* is given."""
        request = req
        if repair is not None:
            request = replace(req, user=f"{req.user}\n\n{repair_message(*repair)}")
        if request.schema is None:
            answer = self._port.ask_text(request)
        else:
            answer = self._port.ask_structured(request)
        reports.append(
            StepReport(
                step=request.step,
                prompt_tokens=answer.prompt_tokens,
                answer_tokens=answer.answer_tokens,
                seconds=answer.seconds,
                failure=answer.failure,
            )
        )
        return answer

    def _repair_fits(self, req: StepRequest, previous: str, error: str) -> bool:
        """Return whether the repair request for *previous* and its *error* fits the window.

        ``True`` where the window is unknown or not enforced (a provider with a window of its
        own).  The repair request repeats the text and adds the previous answer, so it is the
        longest request of the step; its answer is a full answer again, so it is checked with the
        same allowance as the request itself
        (:func:`~teachers_teammate.domain.context_budget.answers_with_the_same_text`).
        """
        budget = self._port.window()
        if budget is None:
            return True
        repair = f"{req.user}\n\n{repair_message(previous, error)}"
        return budget.request_fits(
            len(req.system) + len(repair),
            answer_tokens=answers_with_the_same_text(budget.num_ctx),
        )

    @staticmethod
    def _failed[T](answer: PortAnswer, reports: list[StepReport]) -> StepOutcome[T]:
        """Return the outcome of a step whose last request failed."""
        return StepOutcome(failure=answer.failure, error=answer.error, reports=tuple(reports))
