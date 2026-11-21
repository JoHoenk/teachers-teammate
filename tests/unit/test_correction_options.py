"""Tests for the proofreading options the GUI shows: the steps and the notation note."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from teachers_teammate.application.correction_options import (
    evaluation_fixed_instructions,
    proofreading_prompts,
)
from tests.conftest import make_config


def test_proofreading_prompts_lists_the_steps_that_run(tmp_path: Path) -> None:
    """
    Given  a config whose model runs all three steps
    When   proofreading_prompts() is called
    Then   it returns one titled prompt per step, in step order
    """
    config = dataclasses.replace(make_config(tmp_path), correction_enabled=True)

    prompts = proofreading_prompts(config)

    assert [title for title, _prompt in prompts] == [
        "Correct the text",
        "Explain the corrections",
        "List unclear expressions",
    ]
    assert all(prompt.strip() for _title, prompt in prompts)


def test_proofreading_prompts_with_corrections_only_lists_step_one(tmp_path: Path) -> None:
    """
    Given  a config that asks for corrections only
    When   proofreading_prompts() is called
    Then   only step ① is listed, because the other two requests are not sent
    """
    config = dataclasses.replace(
        make_config(tmp_path), correction_enabled=True, correction_output="text"
    )

    prompts = proofreading_prompts(config)

    assert [title for title, _prompt in prompts] == ["Correct the text"]


def test_proofreading_prompts_leaves_out_the_unclear_step_of_a_model_without_it(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a model whose request profile turns the unclear check off
    When   proofreading_prompts() is called
    Then   step ③ is not listed, because that request is not sent
    """
    from teachers_teammate import config as config_module  # noqa: PLC0415

    original = config_module.request_profile
    monkeypatch.setattr(
        config_module,
        "request_profile",
        lambda model, provider: dataclasses.replace(original(model, provider), unclear_check=False),
    )
    config = dataclasses.replace(make_config(tmp_path), correction_enabled=True)

    prompts = proofreading_prompts(config)

    assert [title for title, _prompt in prompts] == [
        "Correct the text",
        "Explain the corrections",
    ]


def test_the_notation_hint_is_the_only_fixed_instruction() -> None:
    """
    Given  the two text formats a document can have
    When   evaluation_fixed_instructions() is asked for each
    Then   the Markdown one names the notation, the plain one adds nothing
    """
    assert evaluation_fixed_instructions("plain") == ""
    assert evaluation_fixed_instructions("markdown")
