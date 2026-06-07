"""Unit tests for teachers_teammate.config."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from teachers_teammate.config import Config, OcrConfig, load_config_file
from teachers_teammate.exceptions import ConfigFileNotFoundError, ConfigFileParseError
from teachers_teammate.infrastructure.storage_root import (
    compute_cache_key,
    default_config_path,
    resolve_artifact_dir,
    resolve_config_path,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _cfg(**overrides: Any) -> Config:
    ocr_map = {
        "ocr_engine": "engine",
        "ocr_model": "model",
        "ocr_provider": "provider",
        "preprocess_method": "preprocess_method",
        "ocr_temperature": "temperature",
    }
    ocr_kwargs: dict[str, Any] = {"engine": "ollama", "model": "", "preprocess_method": "none"}
    for flat_key, ocr_key in ocr_map.items():
        if flat_key in overrides:
            ocr_kwargs[ocr_key] = overrides.pop(flat_key)
    base: dict[str, Any] = {
        "input_dir": Path("."),
        "output_dir": Path("."),
        "recursive": False,
        "debug": False,
        "ocr": overrides.pop("ocr", OcrConfig(**ocr_kwargs)),
        "language": "English",
        "ollama_url": "http://127.0.0.1:11434",
        "correction_enabled": False,
        "correction_provider": "openai",
        "correction_model": "",
    }
    base.update(overrides)
    return Config(**base)


# ── resolve_artifact_dir ───────────────────────────────────────────────────


def test_resolve_artifact_dir_is_under_output_dir() -> None:
    """
    Given  a Config with output_dir=/some/output
    When   resolve_artifact_dir(cfg.output_dir) is called
    Then   it returns an artifacts directory derived from the output directory key
    """
    cfg = _cfg(output_dir=Path("/some/output"))
    artifact_dir = resolve_artifact_dir(cfg.output_dir)
    expected_key = compute_cache_key(str(Path("/some/output").resolve()))
    assert artifact_dir.name == expected_key
    assert artifact_dir.parent.name == "artifacts"


# ── effective_correction_model ─────────────────────────────────────────────


@pytest.mark.use_case("Multi_Provider_Stage_Configuration")
@pytest.mark.parametrize(
    ("provider", "expected_default"),
    [
        ("ollama", "gpt-oss:20b"),
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-3-haiku-20240307"),
        ("google", "gemini-2.0-flash"),
        ("mistral", "mistral-small-latest"),
        ("cohere", "command-r-plus"),
    ],
)
def test_effective_correction_model_provider_defaults(provider: str, expected_default: str) -> None:
    """
    Given  a Config with a known correction_provider and no explicit correction_model
    When   effective_correction_model is read
    Then   the built-in default model for that provider is returned
    """
    cfg = _cfg(correction_provider=provider, correction_model="")
    assert cfg.effective_correction_model == expected_default


def test_effective_correction_model_honours_explicit_model() -> None:
    """
    Given  a Config with correction_provider=openai and an explicit correction_model
    When   effective_correction_model is read
    Then   the explicit model name is returned unchanged
    """
    cfg = _cfg(correction_provider="openai", correction_model="gpt-3.5-turbo")
    assert cfg.effective_correction_model == "gpt-3.5-turbo"


def test_effective_correction_model_unknown_provider_falls_back() -> None:
    """
    Given  a Config with an unrecognised correction_provider and no correction_model
    When   effective_correction_model is read
    Then   a non-empty fallback string is returned without raising
    """
    cfg = _cfg(correction_provider="unknown_provider", correction_model="")
    # Should not raise; returns a fallback string
    result = cfg.effective_correction_model
    assert isinstance(result, str)
    assert len(result) > 0


# ── effective_evaluate_model ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("provider", "expected_default"),
    [
        ("ollama", "gpt-oss:20b"),
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-3-haiku-20240307"),
        ("google", "gemini-2.0-flash"),
        ("mistral", "mistral-small-latest"),
        ("cohere", "command-r-plus"),
    ],
)
def test_effective_evaluate_model_provider_defaults(provider: str, expected_default: str) -> None:
    """
    Given  a Config with a known evaluate_provider and no explicit evaluate_model
    When   effective_evaluate_model is read
    Then   the built-in default model for that provider is returned
    """
    cfg = _cfg(evaluate_provider=provider, evaluate_model="")
    assert cfg.effective_evaluate_model == expected_default


def test_effective_evaluate_model_honours_explicit_model() -> None:
    """
    Given  a Config with evaluate_provider=openai and an explicit evaluate_model
    When   effective_evaluate_model is read
    Then   the explicit model name is returned unchanged
    """
    cfg = _cfg(evaluate_provider="openai", evaluate_model="gpt-4.1-mini")
    assert cfg.effective_evaluate_model == "gpt-4.1-mini"


# ── Field defaults ─────────────────────────────────────────────────────────


def test_correction_prompt_default_is_empty() -> None:
    """
    Given  a Config created with no correction_prompt argument
    When   the correction_prompt field is read
    Then   it is an empty string
    """
    cfg = _cfg()
    assert cfg.correction_prompt == ""


def test_docx_format_default_is_table() -> None:
    """
    Given  a Config created with no docx_format argument
    When   the docx_format field is read
    Then   it defaults to "table"
    """
    cfg = _cfg()
    assert cfg.docx_format == "table"


def test_docx_enabled_default_is_false() -> None:
    """
    Given  a Config created with no docx_enabled argument
    When   the docx_enabled field is read
    Then   it defaults to False (DOCX output is opt-in)
    """
    cfg = _cfg()
    assert cfg.docx_enabled is False


def test_ocr_timeout_default() -> None:
    """
    Given  a Config created with no ocr_timeout argument
    When   the ocr_timeout field is read
    Then   it defaults to 180 seconds
    """
    cfg = _cfg()
    assert cfg.ocr_timeout == 180


# ── load_config_file ───────────────────────────────────────────────────────


def test_load_config_file_returns_recognised_keys(tmp_path: Path) -> None:
    """
    Given  a TOML file with 'language = "German"' and an unrelated key
    When   load_config_file() is called
    Then   language is returned and the unrelated key is excluded
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('language = "German"\nunknown_key = "ignored"\n')
    result = load_config_file(cfg_file)
    assert result.get("language") == "German"
    assert "unknown_key" not in result


def test_load_config_file_passes_through_unknown_provider(tmp_path: Path) -> None:
    """
    Given  a TOML file with correction_provider = 'nonexistent_xyz'
    When   load_config_file() is called
    Then   the value is returned as-is (provider validation happens at the CLI layer)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('correction_provider = "nonexistent_xyz"\n')
    result = load_config_file(cfg_file)
    assert result.get("correction_provider") == "nonexistent_xyz"


def test_load_config_file_raises_on_invalid_toml(tmp_path: Path) -> None:
    """
    Given  a file with invalid TOML syntax
    When   load_config_file() is called
    Then   ConfigFileParseError is raised
    """
    cfg_file = tmp_path / "bad.toml"
    cfg_file.write_text("this = [broken toml")
    with pytest.raises(ConfigFileParseError):
        load_config_file(cfg_file)


def test_load_config_file_accepts_known_correction_provider(tmp_path: Path) -> None:
    """
    Given  a TOML file with correction_provider = 'openai'
    When   load_config_file() is called
    Then   correction_provider = 'openai' is in the result
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('correction_provider = "openai"\n')
    result = load_config_file(cfg_file)
    assert result.get("correction_provider") == "openai"


def test_load_config_file_accepts_known_evaluate_provider(tmp_path: Path) -> None:
    """
    Given  a TOML file with evaluate_provider = 'openai'
    When   load_config_file() is called
    Then   evaluate_provider = 'openai' is in the result
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('evaluate_provider = "openai"\n')
    result = load_config_file(cfg_file)
    assert result.get("evaluate_provider") == "openai"


# ── resolve_config_path ────────────────────────────────────────────────────


def test_resolve_config_path_returns_path_when_explicit_file_exists(tmp_path: Path) -> None:
    """
    Given  an explicit path to an existing file
    When   resolve_config_path() is called with that path
    Then   the Path is returned
    """
    f = tmp_path / "my.toml"
    f.write_text("")
    result = resolve_config_path(str(f))
    assert result == f


def test_resolve_config_path_raises_when_explicit_file_missing() -> None:
    """
    Given  an explicit path that does not exist
    When   resolve_config_path() is called
    Then   ConfigFileNotFoundError is raised
    """
    with pytest.raises(ConfigFileNotFoundError):
        resolve_config_path("/no/such/file.toml")


def test_resolve_config_path_returns_none_when_no_default_present(tmp_path: Path) -> None:
    """
    Given  no explicit path and no ocr.toml in the storage root
    When   resolve_config_path(None) is called
    Then   None is returned
    """
    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415

    with patch.object(storage_module, "default_config_path", return_value=tmp_path / "ocr.toml"):
        result = resolve_config_path(None)
    assert result is None


def test_resolve_config_path_returns_default_when_ocr_toml_exists(tmp_path: Path) -> None:
    """
    Given  no explicit path and ocr.toml exists in the storage root
    When   resolve_config_path(None) is called
    Then   a Path pointing to ocr.toml is returned
    """
    import teachers_teammate.infrastructure.storage_root as storage_module  # noqa: PLC0415

    storage_toml = tmp_path / "ocr.toml"
    storage_toml.write_text("")
    with patch.object(storage_module, "default_config_path", return_value=storage_toml):
        result = resolve_config_path(None)
    assert result is not None
    assert result.name == "ocr.toml"


# ── Temperature defaults ───────────────────────────────────────────────────


def test_ocr_temperature_default_is_zero() -> None:
    """
    Given  a Config created without an explicit ocr_temperature
    When   ocr_temperature is read
    Then   it defaults to 0.0 (deterministic — matches the OCR hardcode)
    """
    cfg = _cfg()
    assert cfg.ocr.temperature == 0.0


def test_correction_temperature_default_is_0_7() -> None:
    """
    Given  a Config created without an explicit correction_temperature
    When   correction_temperature is read
    Then   it defaults to 0.7
    """
    cfg = _cfg()
    assert cfg.correction_temperature == 0.7


def test_evaluate_temperature_default_is_0_7() -> None:
    """
    Given  a Config created without an explicit evaluate_temperature
    When   evaluate_temperature is read
    Then   it defaults to 0.7
    """
    cfg = _cfg()
    assert cfg.evaluate_temperature == 0.7


def test_load_config_file_parses_temperature_keys(tmp_path: Path) -> None:
    """
    Given  a TOML file with all three temperature keys
    When   load_config_file() is called
    Then   all three temperature values are present in the result dict
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(
        "ocr_temperature = 0.0\ncorrection_temperature = 0.5\nevaluate_temperature = 0.9\n"
    )
    result = load_config_file(cfg_file)
    assert result.get("ocr_temperature") == 0.0
    assert result.get("correction_temperature") == 0.5
    assert result.get("evaluate_temperature") == 0.9
