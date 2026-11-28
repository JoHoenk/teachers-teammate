"""What a step of the proofreading exchange cost, how it ended, and what the file got.

A step is one kind of request (correct, explain, unclear, review); it may take several requests
(a cut-off answer is asked again, an invalid one is repaired).  Every request leaves a
:class:`StepReport`, so a run can say what each step cost and where it failed, and the
evaluation tools can compare two models on the same steps.

:class:`CorrectionOutcome` is what a :class:`~teachers_teammate.interfaces.Corrector` returns:
the corrected text, the review the exchange made, and what the teacher should know.

Pure: no I/O, no provider imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from .review import CorrectionReview

StepName = Literal["correct", "explain", "unclear", "review"]
"""The steps a request can belong to: ① correct, ② explain, ③ unclear, and R's review."""


class ExchangeKind(StrEnum):
    """Which exchange proofreads a text."""

    TEXT_FIRST = "text-first"
    """The three steps: ① the corrected text, ② the explanations, ③ the unclear wording."""


_STORED_EXCHANGE_IDS: dict[ExchangeKind, str] = {ExchangeKind.TEXT_FIRST: "T"}
"""The id in a stored cache key, for an exchange whose id is not its enum value."""


def stored_exchange_id(kind: ExchangeKind) -> str:
    """Return the id *kind* has in a stored cache key.

    The cache key carries ``"T"`` for the text-first exchange; the enum's value is the word the
    evaluation tools use.  Any other exchange is keyed by its value.
    """
    return _STORED_EXCHANGE_IDS.get(kind, kind.value)


FailureKind = Literal["", "truncated", "invalid", "transport"]
"""How a request ended.

``""`` — it answered.  ``truncated`` — the context window cut the answer off.  ``invalid`` — the
model answered, but not in the shape the step asked for (or the provider cannot produce that
shape at all).  ``transport`` — the request never reached the model, or its answer never came
back.
"""


@dataclass(frozen=True)
class AnswerCurve:
    """How long one step's answer is: a part per text plus a part per word.

    Not a rate per 100 words: an answer restates its finding and its notes whatever the text's
    length, so the same per-100 figure is twice too high for a short text and too low for a long
    one.  :attr:`low` and :attr:`high` are the spread of real answers against the curve, so one
    runaway answer in the middle of it does not widen the range.
    """

    fixed: float
    """Answer tokens an answer writes whatever the text's length."""
    per_word: float
    """Answer tokens one more word of student text adds."""
    low: float = 1.0
    """The 10th percentile of what a model answered against this curve, as a factor."""
    high: float = 1.0
    """The 90th percentile of what a model answered against this curve, as a factor."""

    def tokens(self, words: int) -> float:
        """Return the answer tokens one text of *words* words takes, typically."""
        return self.fixed + self.per_word * words


@dataclass(frozen=True)
class ExchangeCost:
    """What an exchange costs, per step: requests per page, answer length and text copies."""

    requests_per_page: dict[StepName, float] = field(default_factory=dict)
    """Requests one page costs per step."""
    answers: dict[StepName, AnswerCurve] = field(default_factory=dict)
    """How long the answer of one step is, per text."""
    text_copies: dict[StepName, int] = field(default_factory=dict)
    """How many times one request of a step carries the text.

    Step ① sends the text once, step ② sends the student's text and the corrected one (two
    copies, so it reads twice as much), step ③ sends the corrected text once.  The reading time
    of a page is the sum over the steps that run.
    """


DEFAULT_EXCHANGE_COST = ExchangeCost(
    requests_per_page={"correct": 1.0, "explain": 1.0, "unclear": 1.0},
    answers={
        "correct": AnswerCurve(fixed=8, per_word=1.28, low=0.86, high=1.13),
        "explain": AnswerCurve(fixed=352, per_word=2.99, low=0.54, high=1.47),
        "unclear": AnswerCurve(fixed=90, per_word=0.53, low=0.34, high=1.98),
    },
    text_copies={"correct": 1, "explain": 2, "unclear": 1},
)
"""What the text-first exchange costs, until this computer's own runs replace it.

One request per step and page, and per step the answer length fitted over the runs of the
reference machine: a part per text and a part per word, with the 10th to 90th percentile of what
the model answered against the curve as the range.
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


@dataclass(frozen=True)
class CorrectionOutcome:
    """Result of one proofreading call: the text, the optional review and a warning."""

    text: str
    review: CorrectionReview | None = None
    warning: str | None = None
    failed: bool = False
    """``True`` when the model answered unusably (or a tool's one-request corrector failed) and
    *text* is the unchanged source: such an outcome is reported but never stored as a finished
    correction.  A request that never reached the model is not an outcome but a
    :exc:`~teachers_teammate.exceptions.StageError`."""
    left_out: int = 0
    """How many stretches of the text the model left out and the app put back.

    Such a file is corrected and stored, with a warning and one item per stretch in the review;
    the evaluation tools count it as a drop, because the model's own answer was.
    """
    fell_back: bool = False
    """``True`` when the answer lacks part of what was asked.

    Either a review came back but a report of step ② or ③ failed, so the explanations or the
    unclear list are missing for part of the text, or step ① left text out and the app put it
    back (:attr:`left_out`).  The file is corrected and stored; only the speed sample is dropped,
    because it would undercount the answer length of a complete answer.
    """
