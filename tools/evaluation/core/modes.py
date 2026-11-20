"""How a model is asked, in the one vocabulary the tools and the app share.

The old tools said ``mode: exchange`` and meant the app's ``ExchangeKind`` ``"T"``, while every file
name said ``T`` and meant the temperature.  One word, two meanings, and the exchange itself was
never named.  Here an answer is described by two axes instead:

* **``exchange``** — how the text travels: ``text-first`` (the app's ``"T"``: the corrected text
  first, then the explanations), ``one-request-text`` (the corrected text in one JSON answer),
  ``one-request-review`` (corrections and explanations in one JSON answer), and the POC's
  ``sentence-walk`` and ``sentence-list``.
* **``steps``** — which steps of the exchange ran: a subset of ``correct``, ``explain``,
  ``unclear``.  ``correct-only`` is ``text-first`` with ``["correct"]``.

The app's ``ExchangeKind`` is renamed to the same words in its own small PR.  Until then
:class:`AnswerMode` — the old vocabulary, moved here unchanged — is what a stored v1 header says,
and :func:`exchange_and_steps` is the one place it is translated, so no other module knows both.
"""

from __future__ import annotations

from enum import StrEnum


class Exchange(StrEnum):
    """How a text travels to the model and back."""

    TEXT_FIRST = "text-first"
    """The app's exchange: ① the corrected text, ② the explanations, ③ the unclear check."""

    ONE_REQUEST_TEXT = "one-request-text"
    """The corrected text only, in one JSON answer."""

    ONE_REQUEST_REVIEW = "one-request-review"
    """Corrections and explanations together, in one JSON answer."""

    SENTENCE_WALK = "sentence-walk"
    """The POC's ``R-walk``: sentence by sentence, each answer feeding the next."""

    SENTENCE_LIST = "sentence-list"
    """The POC's ``R-list``: every sentence in one request, answered as a list."""


class Step(StrEnum):
    """One step of the exchange."""

    CORRECT = "correct"
    """① the corrected text."""

    EXPLAIN = "explain"
    """② the explanation of every change."""

    UNCLEAR = "unclear"
    """③ the wording a reader cannot follow."""


ALL_STEPS: tuple[Step, ...] = (Step.CORRECT, Step.EXPLAIN, Step.UNCLEAR)
"""Every step of the exchange, in the order it runs: the default of a ``text-first`` run."""

DEFAULT_EXCHANGE = Exchange.TEXT_FIRST
"""The exchange a run uses when nothing says otherwise."""

DEFAULT_STEPS: tuple[Step, ...] = ALL_STEPS
"""The steps a run asks when nothing says otherwise."""

ONE_REQUEST_OUTPUT: dict[Exchange, str] = {
    Exchange.ONE_REQUEST_REVIEW: "review",
    Exchange.ONE_REQUEST_TEXT: "text",
}
"""The answer format each one-request exchange writes: the old ``AnswerMode`` value of its answer."""

Setup = tuple[Exchange, tuple[Step, ...]]
"""What a run asks: the exchange, and the steps of it that run."""


def setup_label(setup: Setup) -> str:
    """Return *setup* as one string: ``text-first``, ``text-first/correct``.

    An exchange that asks its own steps is named by the exchange alone, so the label of a run is
    the shortest one that still tells two runs apart.  ``text-first/correct`` is what the old
    vocabulary called ``correct-only``.
    """
    exchange, steps = setup
    if not steps or tuple(steps) == default_steps(exchange):
        return str(exchange)
    return f"{exchange}/{'/'.join(step.value for step in steps)}"


class AnswerMode(StrEnum):
    """The old vocabulary of a v1 header's ``mode``; kept so a stored run can be read and converted.

    A new run does not record a mode: it records an :class:`Exchange` and its :class:`Step`s, which
    say the same thing without the ambiguity of ``T``.
    """

    REVIEW = "review"
    """The app's JSON answer: corrections with explanations."""

    TEXT = "text"
    """The corrected text only."""

    EXCHANGE = "exchange"
    """The proofreading exchange: ① the corrected text, ② the explanations, ③ the unclear check."""

    CORRECT_ONLY = "correct-only"
    """Step ① of the exchange alone: the same request, the same text, none of the time of ② and ③."""


def parse_mode(value: str) -> AnswerMode:
    """Return the mode *value* names.

    Raises:
        ValueError: if *value* is not a mode, naming the allowed values.
    """
    try:
        return AnswerMode(value)
    except ValueError:
        allowed = ", ".join(mode.value for mode in AnswerMode)
        raise ValueError(f"unknown answer mode {value!r}; use one of: {allowed}") from None


def default_steps(exchange: Exchange) -> tuple[Step, ...]:
    """Return the steps an exchange runs when nothing says otherwise.

    A one-request answer carries what its one request asked for: the corrected text alone, or the
    corrections with their explanations and the unclear expressions.  The app's exchange runs all
    three steps.  The POC's sentence exchanges answer with corrections and explanations.
    """
    match exchange:
        case Exchange.ONE_REQUEST_TEXT:
            return (Step.CORRECT,)
        case Exchange.SENTENCE_WALK | Exchange.SENTENCE_LIST:
            return (Step.CORRECT, Step.EXPLAIN)
        case _:
            return ALL_STEPS


def asks_review(exchange: Exchange) -> bool:
    """Whether *exchange* answers with the corrections and their explanations in one request.

    Only that exchange has explanations to mark and an answer length worth recording: a text answer
    is the corrected text itself, so its length is one constant for every model.
    """
    return exchange is Exchange.ONE_REQUEST_REVIEW


def exchange_and_steps(
    mode: AnswerMode, *, unclear_check: bool = True
) -> tuple[Exchange, tuple[Step, ...]]:
    """Return the exchange and the steps the old ``AnswerMode`` stands for.

    This is the one translation between the two vocabularies, so a reader of an old run and a
    writer of a new one cannot disagree about what ``exchange`` meant.  The steps are what the
    exchange's one answer holds: a review answer carries corrections with explanations and the
    unclear expressions, a text answer carries the corrected text only.

    Args:
        mode: The answer mode an old header records.
        unclear_check: Whether the run asked for the unclear step; only ``text-first`` has one to
            switch off, because only the app applies that check.

    Returns:
        The exchange and the steps, in the order the steps run.

    Raises:
        ValueError: for a mode this table does not know, so a new mode cannot be mapped by guess.
    """
    match mode:
        case AnswerMode.REVIEW:
            return Exchange.ONE_REQUEST_REVIEW, ALL_STEPS
        case AnswerMode.TEXT:
            return Exchange.ONE_REQUEST_TEXT, (Step.CORRECT,)
        case AnswerMode.EXCHANGE:
            return Exchange.TEXT_FIRST, ALL_STEPS if unclear_check else (Step.CORRECT, Step.EXPLAIN)
        case AnswerMode.CORRECT_ONLY:
            return Exchange.TEXT_FIRST, (Step.CORRECT,)
    raise ValueError(f"no exchange for the answer mode {mode!r}")


def exchange_of(value: object) -> Exchange:
    """Return *value* as an exchange, refusing one this table does not know.

    Raises:
        ValueError: if *value* is not an exchange, naming the allowed ones.
    """
    try:
        return Exchange(value)
    except ValueError:
        allowed = ", ".join(exchange.value for exchange in Exchange)
        raise ValueError(f"unknown exchange {value!r}; use one of: {allowed}") from None


def steps_of(values: object) -> tuple[Step, ...]:
    """Return *values* as steps, refusing one this table does not know.

    Raises:
        ValueError: if a value is not a step, naming the allowed ones.
    """
    if not isinstance(values, (list, tuple)):
        raise ValueError(f"steps must be a list, not {type(values).__name__}")
    found: list[Step] = []
    for value in values:
        try:
            found.append(Step(value))
        except ValueError:
            allowed = ", ".join(step.value for step in Step)
            raise ValueError(f"unknown step {value!r}; use one of: {allowed}") from None
    return tuple(found)


__all__ = [
    "ALL_STEPS",
    "DEFAULT_EXCHANGE",
    "DEFAULT_STEPS",
    "ONE_REQUEST_OUTPUT",
    "AnswerMode",
    "Exchange",
    "Setup",
    "Step",
    "asks_review",
    "default_steps",
    "exchange_and_steps",
    "exchange_of",
    "parse_mode",
    "setup_label",
    "steps_of",
]
