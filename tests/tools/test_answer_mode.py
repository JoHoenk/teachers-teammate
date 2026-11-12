"""Tests of the answer modes: the values, the parser, and the row that carries one."""

from __future__ import annotations

import json

import pytest

from tools.model_eval.answer_mode import CORRECTOR_MODES, DEFAULT_MODE, AnswerMode, parse_mode
from tools.model_eval.results import ResultRow


def _row(**overrides) -> ResultRow:
    """Return a result row of an error text, with sensible defaults."""
    values = {
        "id": "de/de-01",
        "model": "gemma4:e4b",
        "temperature": 0.2,
        "language": "de",
        "kind": "errors",
        "origin": "Falko-MERLIN test",
        "seconds": 12.5,
        "failed": False,
        "fallback": False,
        "has_review": True,
        "source": "Der Hund laufen schnell.",
        "output": "Der Hund läuft schnell.",
        "reference": "Der Hund läuft schnell.",
    }
    values.update(overrides)
    return ResultRow(**values)


def test_the_modes_are_the_three_ways_to_ask() -> None:
    """
    Given  the answer modes of the tools
    When   they are read
    Then   review and text are the ones the app's corrector answers in, two-step is not, and
           review is the default
    """
    assert [mode.value for mode in AnswerMode] == ["review", "text", "two-step"]
    assert CORRECTOR_MODES == (AnswerMode.REVIEW, AnswerMode.TEXT)
    assert DEFAULT_MODE is AnswerMode.REVIEW
    assert AnswerMode.REVIEW.asks_corrector
    assert AnswerMode.TEXT.asks_corrector
    assert not AnswerMode.TWO_STEP.asks_corrector


@pytest.mark.parametrize("value", ["Review", "two_step", "chat", ""])
def test_parse_mode_refuses_a_value_that_is_not_a_mode(value: str) -> None:
    """
    Given  a value that is not one of the modes
    When   it is parsed
    Then   a ValueError names the allowed values
    """
    with pytest.raises(ValueError, match="use one of: review, text, two-step"):
        parse_mode(value)


def test_a_row_round_trip_keeps_the_mode() -> None:
    """
    Given  a result row asked in text mode
    When   it is written and read back
    Then   the stored line still says "text" and the row carries the enum again
    """
    stored = _row(mode=AnswerMode.TEXT).to_dict()

    assert json.loads(json.dumps(stored))["mode"] == "text"
    assert ResultRow.from_dict(stored).mode is AnswerMode.TEXT


def test_a_row_with_an_unknown_mode_is_refused() -> None:
    """
    Given  a stored row whose mode is not one of the modes
    When   it is read
    Then   a ValueError names the value and the allowed ones
    """
    stored = _row().to_dict()
    stored["mode"] = "chat"

    with pytest.raises(ValueError, match="unknown answer mode 'chat'"):
        ResultRow.from_dict(stored)
