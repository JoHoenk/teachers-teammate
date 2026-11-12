"""Tests of gate 1 beyond the request: the saved-answers file and continuing a run."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION
from tools.model_eval import gate1_answers, gate_one, protocol


def _header(**overrides) -> gate1_answers.Header:
    """Return the header of an answers file made with the current protocol."""
    values = {
        "date": "2026-11-12T10:00:00Z",
        "eval_version": protocol.current_stamp().eval_version,
        "fingerprint": protocol.fingerprint_sha256(),
        "answer_format_version": ANSWER_FORMAT_VERSION,
        "num_ctx": 8192,
        "temperature": None,
        "tool_commit": "abc1234",
        "ollama_version": "0.40.15",
        "compact_json": "catalog",
    }
    values.update(overrides)
    return gate1_answers.Header(**values)


def _answer_json(**profile) -> dict:
    """Return the JSON line of one saved answer with the given request profile."""
    return {
        "tag": "m",
        "digest": "sha256:1",
        "fixture": "plain DE",
        "text_sha256": "0" * 64,
        "round": 1,
        "run": 1,
        "request_failed": False,
        "too_long": False,
        "done_reason": "stop",
        "prompt_eval_count": 1,
        "eval_count": 1,
        "seconds": 1.0,
        "answer": "{}",
        "verdict": "",
        "request_profile": profile,
    }


def test_a_header_keeps_the_compact_json_choice_as_a_word() -> None:
    """
    Given  a header made for ``--compact-json off``
    When   it is written and read back
    Then   the choice is the word "off", not a boolean
    """
    header = _header(compact_json="off")

    assert gate1_answers.Header.from_json(header.to_json()).compact_json == "off"


def test_a_header_of_the_older_layout_is_read_as_the_word_it_held() -> None:
    """
    Given  a header of the older layout, which kept the choice inside ``request_profile``
    When   it is read
    Then   the choice is that word; a header without any is "catalog"
    """
    data = _header().to_json()
    del data["compact_json"]

    assert (
        gate1_answers.Header.from_json(
            {**data, "request_profile": {"compact_json": "on"}}
        ).compact_json
        == "on"
    )
    assert gate1_answers.Header.from_json(data).compact_json == "catalog"


def test_a_header_with_an_unknown_choice_is_refused() -> None:
    """
    Given  a header whose compact_json is not catalog, on or off
    When   it is read
    Then   it is refused, so a word cannot be mistaken for anything else
    """
    data = {**_header().to_json(), "compact_json": "maybe"}

    with pytest.raises(ValueError, match="compact_json"):
        gate1_answers.Header.from_json(data)


def test_an_answer_with_a_word_where_a_boolean_belongs_is_refused() -> None:
    """
    Given  an answer whose request profile holds the header's word "off" as compact_json
    When   it is read
    Then   it is refused: bool("off") is True, so it would silently read as the compact rule
    """
    with pytest.raises(ValueError, match="compact_json"):
        gate1_answers.Answer.from_json(_answer_json(compact_json="off", temperature=0.2))

    answer = gate1_answers.Answer.from_json(_answer_json(compact_json=False, temperature=0.2))
    assert answer.compact_json is False


def test_the_answers_of_the_current_request_can_be_continued() -> None:
    """
    Given  a header made with the current protocol and the same settings
    When   the continue check runs
    Then   there is no problem
    """
    assert (
        gate_one.continue_problem(_header(), num_ctx=None, temperature=None, compact_json="catalog")
        is None
    )


@pytest.mark.parametrize(
    ("overrides", "asked", "reason"),
    [
        ({"fingerprint": "f" * 64}, {}, "fingerprint"),
        ({"answer_format_version": 0}, {}, "answer format"),
        ({}, {"num_ctx": 16384}, "num_ctx"),
        ({"temperature": 0.2}, {"temperature": 0.7}, "temperature"),
        ({"compact_json": "on"}, {"compact_json": "off"}, "compact-json"),
    ],
)
def test_answers_of_another_request_are_not_continued(overrides, asked, reason) -> None:
    """
    Given  a header that differs from this invocation in one thing that decides the request
    When   the continue check runs
    Then   it names that thing and refuses
    """
    settings = {"num_ctx": None, "temperature": None, "compact_json": "catalog", **asked}

    problem = gate_one.continue_problem(_header(**overrides), **settings)

    assert problem is not None and reason in problem
