"""Unit tests for the rules a whole run follows: its windows and its Ollama models."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.domain.context_budget import BASE_NUM_CTX
from teachers_teammate.run_rules import (
    ollama_models,
    resolve_run_windows,
    uses_ollama,
    window_of_model,
)
from tests.conftest import make_config


def _ollama_ocr(tmp_path: Path, **overrides: object):
    """Return a config whose scans are read by a model on an Ollama server."""
    settings = {"ocr_engine": "ollama", "ocr_model": "qwen3-vl:8b", "correction_enabled": False}
    settings.update(overrides)
    return make_config(tmp_path, **settings)


@pytest.mark.unit
def test_an_ollama_reader_alone_needs_the_server(tmp_path: Path) -> None:
    """
    Given  OCR on an Ollama model and no other stage on Ollama
    When   the run asks whether it talks to a server
    Then   it does, and the model it needs is the reader's
    """
    config = _ollama_ocr(tmp_path)

    assert uses_ollama(config) is True
    assert ollama_models(config) == ("qwen3-vl:8b",)


@pytest.mark.unit
def test_the_built_in_reader_with_an_online_provider_needs_no_server(tmp_path: Path) -> None:
    """
    Given  the built-in reader and an online proofreading
    When   the run asks whether it talks to a server
    Then   it does not, and no model is listed
    """
    config = make_config(tmp_path, correction_enabled=True, correction_provider="openai")

    assert uses_ollama(config) is False
    assert ollama_models(config) == ()


@pytest.mark.unit
def test_a_stage_that_is_switched_off_does_not_count(tmp_path: Path) -> None:
    """
    Given  proofreading and evaluation set to Ollama models but both switched off
    When   the run asks whether it talks to a server
    Then   it does not, because no switched-off stage sends anything
    """
    config = make_config(
        tmp_path,
        correction_enabled=False,
        correction_provider="ollama",
        correction_model="qwen3:8b",
        evaluation_enabled=False,
        evaluate_provider="ollama",
        evaluate_model="qwen3:14b",
    )

    assert uses_ollama(config) is False
    assert ollama_models(config) == ()


@pytest.mark.unit
def test_a_model_used_by_two_stages_is_listed_once(tmp_path: Path) -> None:
    """
    Given  one Ollama model chosen for reading, proofreading and evaluation
    When   the run lists the models it needs
    Then   the model appears once, so the server is asked about it once
    """
    config = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="qwen3-vl:8b",
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3-vl:8b",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="qwen3-vl:8b",
    )

    assert ollama_models(config) == ("qwen3-vl:8b",)


@pytest.mark.unit
def test_each_stage_of_a_server_run_sends_its_own_model(tmp_path: Path) -> None:
    """
    Given  a reader, a proofreading and an evaluation on three different Ollama models
    When   the run lists the models it needs
    Then   they are listed in the order the stages run
    """
    config = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="qwen3-vl:8b",
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3:14b",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="qwen3:30b",
    )

    assert uses_ollama(config) is True
    assert ollama_models(config) == ("qwen3-vl:8b", "qwen3:14b", "qwen3:30b")


@pytest.mark.unit
def test_a_reader_without_a_model_asks_for_nothing(tmp_path: Path) -> None:
    """
    Given  an Ollama reader whose model field is empty
    When   the run lists the models it needs
    Then   the empty tag is left out, so the server is not asked about it
    """
    config = _ollama_ocr(tmp_path, ocr_model="")

    assert ollama_models(config) == ()


@pytest.mark.unit
def test_a_model_reads_scans_with_the_base_window(tmp_path: Path) -> None:
    """
    Given  a run whose only Ollama model reads scans
    When   the window is asked for that model
    Then   it is the base window: OCR requests set none
    """
    config = _ollama_ocr(tmp_path)
    windows = resolve_run_windows(config, None)

    assert window_of_model(config, windows, "qwen3-vl:8b") == BASE_NUM_CTX


@pytest.mark.unit
def test_the_model_a_stage_writes_with_takes_its_window(tmp_path: Path) -> None:
    """
    Given  a proofreading and an evaluation on Ollama, sized by the context setting
    When   the window is asked for each model
    Then   each model takes the window of the stage that writes with it
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3:14b",
        evaluation_enabled=True,
        evaluate_provider="ollama",
        evaluate_model="qwen3:30b",
    )
    windows = resolve_run_windows(config, None)

    assert window_of_model(config, windows, "qwen3:14b") == windows.proofreading
    assert window_of_model(config, windows, "qwen3:30b") == windows.evaluation
