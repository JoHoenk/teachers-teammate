"""Unit tests for teachers_teammate.config."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from teachers_teammate.config import (
    DEFAULTS,
    Config,
    OcrConfig,
    dump_config_file,
    load_config_file,
    unavailable_ocr_engine,
)
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


@pytest.mark.parametrize("engine", ["langchain", "openai", "gpt"])
def test_load_config_file_replaces_online_ocr_engine(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], engine: str
) -> None:
    """
    Given  a settings file from an older version naming an online OCR engine
    When   load_config_file() is called
    Then   the engine falls back to the default and a warning explains why
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(f'ocr_engine = "{engine}"\nocr_provider = "openai"\n')
    result = load_config_file(cfg_file)
    assert result["ocr_engine"] == DEFAULTS["ocr_engine"]
    assert "ocr_provider" not in result
    assert "stays on this computer" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('ocr_engine = "langchain"', "langchain"),
        ('ocr_engine = "openai"', "openai"),
        ('ocr_engine = "Tesseract"', ""),
        ('ocr_engine = "ollama"', ""),
        ('language = "German"', ""),
    ],
)
def test_unavailable_ocr_engine_names_what_was_replaced(
    tmp_path: Path, line: str, expected: str
) -> None:
    """
    Given  a settings file with an online, a local or no OCR engine
    When   unavailable_ocr_engine() is asked
    Then   it names the online engine, and is empty otherwise
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(line + "\n")

    assert unavailable_ocr_engine(cfg_file) == expected


def test_unavailable_ocr_engine_is_empty_for_an_unreadable_file(tmp_path: Path) -> None:
    """
    Given  a settings file that is not valid TOML
    When   unavailable_ocr_engine() is asked
    Then   it answers "" instead of raising (loading reports the parse error itself)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text("not = = toml")

    assert unavailable_ocr_engine(cfg_file) == ""


def test_load_config_file_resets_model_of_replaced_online_engine(tmp_path: Path) -> None:
    """
    Given  a settings file naming an online engine together with its online model
    When   load_config_file() is called
    Then   the model is reset too, so the replacement engine is not asked for 'gpt-4o'
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = "langchain"\nocr_model = "gpt-4o"\n')

    assert load_config_file(cfg_file)["ocr_model"] == DEFAULTS["ocr_model"]


def test_load_config_file_keeps_model_of_local_engine(tmp_path: Path) -> None:
    """
    Given  a settings file naming Ollama and a model
    When   load_config_file() is called
    Then   the model is kept
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = "ollama"\nocr_model = "my-model:7b"\n')

    assert load_config_file(cfg_file)["ocr_model"] == "my-model:7b"


def test_load_config_file_keeps_local_ocr_engines(tmp_path: Path) -> None:
    """
    Given  a settings file naming Tesseract
    When   load_config_file() is called
    Then   the engine is kept (case and spaces folded)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = " Tesseract "\n')
    assert load_config_file(cfg_file)["ocr_engine"] == "tesseract"


def test_ocr_config_from_settings_replaces_online_engine() -> None:
    """
    Given  saved GUI settings whose ocr_engine is an online provider
    When   OcrConfig.from_settings() is called
    Then   the engine is the local default, so pages are never uploaded
    """
    ocr = OcrConfig.from_settings({"ocr_engine": "langchain", "ocr_model": "gpt-4o"})
    assert ocr.engine == DEFAULTS["ocr_engine"]
    assert ocr.model == DEFAULTS["ocr_model"]


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


# ── hardware_profile setting ───────────────────────────────────────────────


def test_hardware_profile_default_is_auto() -> None:
    """
    Given  the DEFAULTS mapping
    When   hardware_profile is inspected
    Then   the default is "auto"
    """
    assert DEFAULTS["hardware_profile"] == "auto"


@pytest.mark.use_case("Hardware_Profile_Override")
def test_load_config_file_keeps_valid_hardware_profile(tmp_path: Path) -> None:
    """
    Given  an ocr.toml with a valid hardware_profile value
    When   load_config_file is called
    Then   the value is returned unchanged
    """
    path = tmp_path / "ocr.toml"
    path.write_text('hardware_profile = "16gb"\n', encoding="utf-8")
    values = load_config_file(path)
    assert values["hardware_profile"] == "16gb"


@pytest.mark.use_case("Hardware_Profile_Override")
def test_load_config_file_drops_invalid_hardware_profile_with_warning(
    tmp_path: Path, capsys
) -> None:
    """
    Given  an ocr.toml with an invalid hardware_profile value
    When   load_config_file is called
    Then   the key is dropped and a warning is printed to stderr (AC11)
    """
    path = tmp_path / "ocr.toml"
    path.write_text('hardware_profile = "supercomputer"\n', encoding="utf-8")
    values = load_config_file(path)
    assert "hardware_profile" not in values
    stderr = capsys.readouterr().err
    assert "hardware_profile" in stderr
    assert "auto" in stderr


@pytest.mark.use_case("Hardware_Profile_Override")
def test_dump_config_file_round_trips_hardware_profile(tmp_path: Path) -> None:
    """
    Given  a settings dict containing hardware_profile
    When   it is dumped and loaded back
    Then   hardware_profile survives the round trip
    """
    text = dump_config_file({"hardware_profile": "8gb"})
    path = tmp_path / "ocr.toml"
    path.write_text(text, encoding="utf-8")
    values = load_config_file(path)
    assert values["hardware_profile"] == "8gb"


# ── pdf_text_layer values ──────────────────────────────────────────────────


def test_ocr_config_normalizes_pdf_text_layer_case() -> None:
    """
    Given  an OcrConfig created with a differently-cased, padded text-layer mode
    When   the config is constructed
    Then   the mode is normalized so "Never" does not fall through to the auto rules
    """
    assert OcrConfig(pdf_text_layer=" Never ").pdf_text_layer == "never"  # ty: ignore[invalid-argument-type]


def test_load_config_file_normalizes_pdf_text_layer(tmp_path: Path) -> None:
    """
    Given  a config file with pdf_text_layer = "Always"
    When   load_config_file() is called
    Then   the value is returned lowercased
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('pdf_text_layer = "Always"\n')

    assert load_config_file(cfg_file)["pdf_text_layer"] == "always"


def test_load_config_file_drops_invalid_pdf_text_layer(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a config file with an unknown pdf_text_layer mode
    When   load_config_file() is called
    Then   the key is ignored and a warning is printed to stderr
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('pdf_text_layer = "off"\n')

    assert "pdf_text_layer" not in load_config_file(cfg_file)
    assert "pdf_text_layer must be one of" in capsys.readouterr().err


def test_ocr_config_from_settings_maps_flat_keys_and_defaults() -> None:
    """
    Given  a flat settings dict with some OCR keys missing
    When   OcrConfig.from_settings() is called
    Then   present keys map onto the fields and missing ones take the defaults
    """
    ocr = OcrConfig.from_settings(
        {"ocr_engine": "tesseract", "preprocess_dewarp": False, "pdf_text_layer": "NEVER"}
    )

    assert ocr.engine == "tesseract"
    assert ocr.dewarp is False
    assert ocr.pdf_text_layer == "never"
    assert ocr.pdf_render_dpi == DEFAULTS["pdf_render_dpi"]
