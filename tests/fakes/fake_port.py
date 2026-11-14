"""A scripted model port, for tests of the runner and of the exchanges.

A test writes the answers it expects the model to give, in order; the port hands them out and
records the requests it was asked for.  Nothing here talks to a model, so a test says exactly
what the model does — answers, is cut off, fails to reach the server, or answers something the
schema does not accept.
"""

from __future__ import annotations

from teachers_teammate.domain.context_budget import ContextBudget
from teachers_teammate.exceptions import TRUNCATED_MESSAGE
from teachers_teammate.infrastructure.proofreading.port import PortAnswer, StepRequest


def answer(raw: str) -> PortAnswer:
    """Return a complete answer whose text is *raw*."""
    return PortAnswer(raw=raw)


def cut_off() -> PortAnswer:
    """Return an answer the context window cut off."""
    return PortAnswer(failure="truncated", error=TRUNCATED_MESSAGE)


def transport(
    error: str = "Correction failed (connection refused); keeping original text.",
) -> PortAnswer:
    """Return a request that never reached the model."""
    return PortAnswer(failure="transport", error=error)


def invalid(raw: str, error: str = "the answer did not match the required structure") -> PortAnswer:
    """Return an answer that does not match the schema, with its text for the repair request."""
    return PortAnswer(raw=raw, failure="invalid", error=error)


class FakePort:
    """A port that answers from a script and records what it was asked.

    The script is consumed in order, whatever the step: a test that expects two requests writes
    two answers.  A request beyond the script fails the test instead of answering something the
    test did not plan for.
    """

    def __init__(self, *script: PortAnswer, window: ContextBudget | None = None) -> None:
        self._script = list(script)
        self._window = window
        self.requests: list[StepRequest] = []

    def ask_text(self, req: StepRequest) -> PortAnswer:
        """Return the next scripted answer."""
        return self._next(req)

    def ask_structured(self, req: StepRequest) -> PortAnswer:
        """Return the next scripted answer."""
        return self._next(req)

    def window(self) -> ContextBudget | None:
        """Return the window the test gave, or ``None`` for a provider that has its own."""
        return self._window

    def _next(self, req: StepRequest) -> PortAnswer:
        """Return the next scripted answer and record *req*."""
        self.requests.append(req)
        if not self._script:
            raise AssertionError(f"unexpected request of step {req.step!r}")
        return self._script.pop(0)
