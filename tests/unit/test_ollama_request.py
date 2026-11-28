"""Unit tests for teachers_teammate.infrastructure.ollama_{kind}."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.infrastructure import ollama_request


def test_proofreading_options_hold_one_fixed_context_size() -> None:
    """
    Given  the temperature of a proofreading request
    When   proofreading_options() is called
    Then   the options carry that temperature and the one shared context size
    """
    options = ollama_request.proofreading_options(0.7)

    assert options == {"temperature": 0.7, "num_ctx": BASE_NUM_CTX}
    assert BASE_NUM_CTX == 8192


def test_sampling_values_join_the_proofreading_options_without_replacing_the_fixed_ones() -> None:
    """
    Given  sampling values that try to set the temperature and the window as well
    When   proofreading_options() is called
    Then   the values are sent, and the temperature and the window are the ones the caller gave
    """
    options = ollama_request.proofreading_options(
        0.3, 4096, {"presence_penalty": 0.0, "temperature": 9.0, "num_ctx": 1}
    )

    assert options == {"presence_penalty": 0.0, "temperature": 0.3, "num_ctx": 4096}
