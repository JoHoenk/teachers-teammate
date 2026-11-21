"""Unit tests for teachers_teammate.application.setup_plan.SetupService."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from teachers_teammate.application.composition import build_app_services
from teachers_teammate.application.ollama_server import OllamaStatus
from teachers_teammate.application.setup_plan import (
    HandIn,
    ProofreadingPlace,
    SetupAnswers,
)
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
    ModelRating,
    Suitability,
)
from teachers_teammate.domain.ollama_discovery import OllamaState

_INSTALL = "teachers_teammate.infrastructure.ollama_install"


@pytest.mark.unit
def test_resolve_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  a config file exists at a known path
    When   resolve_config_path() is called with that explicit path as a string
    Then   it returns the resolved Path without error
    """
    cfg = tmp_path / "ocr.toml"
    cfg.write_text("[settings]\n", encoding="utf-8")
    setup = build_app_services().setup

    result = setup.resolve_config_path(str(cfg))

    assert result == cfg


@pytest.mark.unit
def test_first_start_settings_tells_a_first_start_from_a_saved_setup(tmp_path: Path) -> None:
    """
    Given  no settings file, one with only what the installer writes, one a teacher saved, one
           whose only value is unusable, and one that cannot be read
    When   first_start_settings() is asked about each
    Then   the first two are a first start (carrying the installer's choice), the others are not
    """
    setup = build_app_services().setup
    installer = tmp_path / "installer.toml"
    installer.write_text("check_for_updates = false\n", encoding="utf-8")
    saved = tmp_path / "saved.toml"
    saved.write_text('check_for_updates = false\nlanguage = "German"\n', encoding="utf-8")
    unusable = tmp_path / "unusable.toml"
    unusable.write_text('hardware_profile = "nonsense"\n', encoding="utf-8")
    broken = tmp_path / "broken.toml"
    broken.write_text("this is = = not toml", encoding="utf-8")

    assert setup.first_start_settings(None) == {}
    assert setup.first_start_settings(tmp_path / "missing.toml") == {}
    assert setup.first_start_settings(installer) == {"check_for_updates": False}
    assert setup.first_start_settings(saved) is None
    assert setup.first_start_settings(unusable) is None
    assert setup.first_start_settings(broken) is None


@pytest.mark.unit
def test_default_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  the composed services and a patched storage root
    When   default_config_path() is called
    Then   it delegates to storage_root.default_config_path and returns a Path
    """
    expected = tmp_path / "ocr.toml"
    setup = build_app_services().setup

    with patch(
        "teachers_teammate.infrastructure.storage_root.default_config_path",
        return_value=expected,
    ):
        result = setup.default_config_path()

    assert result == expected


@pytest.mark.unit
def test_the_setup_plan_comes_from_the_facts_and_the_ratings() -> None:
    """
    Given  a computer with a running Ollama, no models and 100 GB of free disk space
    When   plan_setup is asked for handwritten pages and local proofreading
    Then   the facts are gathered once and the setup plan names the recommended models
    """

    setup = build_app_services().setup
    answers = SetupAnswers(
        language="German",
        hand_in=HandIn.HANDWRITTEN,
        proofreading=ProofreadingPlace.LOCAL,
        ollama_url="http://127.0.0.1:11434",
        hardware_profile="auto",
        local_ai_shown=True,
    )
    hardware = HardwareDetection(profile="8gb", reason=DetectionReason.MANUAL)
    recommended = {"ocr": "qwen2.5vl:7b", "general": "gemma4:e4b"}
    asked_cached_only: list[bool] = []

    def rate(tags, task, profile, url, **kwargs):
        asked_cached_only.append(kwargs["cached_only"])
        return [
            ModelRating(
                tag=tag,
                task=task,
                model_uses=None,
                suitability=Suitability.BEST,
                memory_gb=None,
                note="",
            )
            for tag in tags
        ]

    with (
        patch.object(
            setup._ollama, "ollama_status", return_value=OllamaStatus(OllamaState.RUNNING)
        ),
        patch.object(setup._providers, "list_provider_models", return_value=[]),
        patch.object(setup._recommendations, "resolve_hardware_profile", return_value=hardware),
        patch.object(setup._anonymization, "anonymizer_available", return_value=False),
        patch.object(setup._addons, "is_module_importable", return_value=True),
        patch.object(
            setup._anonymization, "spacy_model_for_language", return_value="de_core_news_sm"
        ),
        patch.object(setup._providers, "missing_provider_package", return_value=None),
        patch.object(setup._reader, "page_models_installed", return_value=False),
        patch.object(setup._recommendations, "rate_ollama_models", side_effect=rate),
        patch.object(
            setup._recommendations,
            "best_ollama_model",
            side_effect=lambda task, profile, **kwargs: recommended[task],
        ),
        patch(f"{_INSTALL}.free_disk_gb", return_value=100.0),
    ):
        plan = setup.plan_setup(answers, dict(DEFAULTS))

    assert plan.settings["ocr_model"] == "qwen2.5vl:7b"
    assert plan.settings["correction_model"] == "gemma4:e4b"
    assert plan.panel["recognition_enabled"] is True
    assert [download.name for download in plan.downloads] == [
        "page_models",
        "qwen2.5vl:7b",
        "gemma4:e4b",
    ]
    # Background thread: an installed model outside the catalog is classified by the server.
    assert asked_cached_only
    assert not any(asked_cached_only)


@pytest.mark.unit
def test_the_plan_and_the_model_choices_are_made_from_one_set_of_facts() -> None:
    """
    Given  a computer with a running Ollama that holds one model
    When   plan_with_model_choices is asked for local proofreading
    Then   the facts are gathered once, the setup plan is the one plan_setup gives, and the
           choices carry its proofreading model as the suggestion and list the installed one
    """

    setup = build_app_services().setup
    answers = SetupAnswers(
        language="German",
        hand_in=HandIn.TYPED,
        proofreading=ProofreadingPlace.LOCAL,
        ollama_url="http://127.0.0.1:11434",
        hardware_profile="auto",
        local_ai_shown=True,
    )
    hardware = HardwareDetection(profile="8gb", reason=DetectionReason.MANUAL)

    def rate(tags, task, profile, url, **kwargs):
        return [
            ModelRating(
                tag=tag,
                task=task,
                model_uses=None,
                suitability=Suitability.BEST,
                memory_gb=None,
                note="",
            )
            for tag in tags
        ]

    with (
        patch.object(
            setup._ollama, "ollama_status", return_value=OllamaStatus(OllamaState.RUNNING)
        ),
        patch.object(
            setup._providers, "list_provider_models", return_value=["mymodel:7b"]
        ) as listing,
        patch.object(setup._recommendations, "resolve_hardware_profile", return_value=hardware),
        patch.object(setup._anonymization, "anonymizer_available", return_value=False),
        patch.object(setup._addons, "is_module_importable", return_value=True),
        patch.object(
            setup._anonymization, "spacy_model_for_language", return_value="de_core_news_sm"
        ),
        patch.object(setup._providers, "missing_provider_package", return_value=None),
        patch.object(setup._reader, "page_models_installed", return_value=False),
        patch.object(setup._recommendations, "rate_ollama_models", side_effect=rate),
        patch.object(setup._recommendations, "best_ollama_model", return_value="gemma4:e4b"),
        patch(f"{_INSTALL}.free_disk_gb", return_value=100.0),
    ):
        plan, choices = setup.plan_with_model_choices(answers, dict(DEFAULTS))
        again = setup.plan_setup(answers, dict(DEFAULTS))

    assert plan == again
    assert choices.suggested == plan.settings["correction_model"] == "gemma4:e4b"
    assert "mymodel:7b" in {choice.tag for choice in choices.choices}
    assert listing.call_count == 2  # once for the combined call, once for plan_setup
