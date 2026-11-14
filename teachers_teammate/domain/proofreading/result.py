"""What a step of the proofreading exchange cost and how it ended.

A step is one kind of request (correct, explain, unclear, review); it may take several requests
(a cut-off answer is asked again, an invalid one is repaired).  Every request leaves a
:class:`StepReport`, so a run can say what each step cost and where it failed, and the
evaluation tools can compare two models on the same steps.

Pure: no I/O, no provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

StepName = Literal["correct", "explain", "unclear", "review"]
"""The steps a request can belong to: ① correct, ② explain, ③ unclear, and R's review."""

ExchangeKind = Literal["T", "R-walk", "R-list"]
"""Which exchange proofreads a text.

``T`` — the three steps (correct, explain, unclear).  ``R-walk`` / ``R-list`` — the prepared
one-request exchange, which walks every sentence or lists only the changed ones; it is built only
when a tuning stage keeps it for a model.
"""

FailureKind = Literal["", "truncated", "invalid", "transport"]
"""How a request ended.

``""`` — it answered.  ``truncated`` — the context window cut the answer off.  ``invalid`` — the
model answered, but not in the shape the step asked for (or the provider cannot produce that
shape at all).  ``transport`` — the request never reached the model, or its answer never came
back.
"""


@dataclass(frozen=True)
class StepReport:
    """One request of one step: what it cost, how it ended, and what it counted."""

    step: StepName
    prompt_tokens: int | None = None
    """Tokens of the request, when the server reported them."""
    answer_tokens: int | None = None
    """Tokens of the answer, when the server reported them."""
    seconds: float = 0.0
    """Wall-clock time of the request, including the wait for the server."""
    failure: FailureKind = ""
    counters: dict[str, int] = field(default_factory=dict)
    """What the step counted while reading this answer (unknown ids, dropped quotes, ...)."""
