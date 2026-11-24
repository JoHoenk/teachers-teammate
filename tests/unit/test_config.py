"""Unit tests for teachers_teammate.config."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any
from unittest.mock import patch

import pytest

from teachers_teammate.config import (
    Config,
    OcrConfig,
    broken_settings_copy,
    dump_config_file,
    load_config_file,
    save_settings_file,
)
from teachers_teammate.settings_schema import DEFAULTS
from teachers_teammate.domain.settings_notice import SettingNoticeKind
from teachers_teammate.domain.model_catalog import DEFAULT_WRITING_MODEL, HardwareProfileSetting
from teachers_teammate.exceptions import ConfigFileNotFoundError, ConfigFileParseError
from teachers_teammate.settings_schema import SETTINGS
from teachers_teammate.infrastructure.storage_root import (
    compute_cache_key,
    default_config_path,
    resolve_artifact_dir,
    resolve_config_path,
)
from tests.conftest import make_config

# ── Helpers ────────────────────────────────────────────────────────────────


def _load(path: Path) -> dict:
    """Return the settings values of *path*; the notices have their own tests."""
    return load_config_file(path).values


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
        ("ollama", DEFAULT_WRITING_MODEL),
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5"),
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
        ("ollama", DEFAULT_WRITING_MODEL),
        ("openai", "gpt-4o-mini"),
        ("anthropic", "claude-haiku-4-5"),
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


def test_correction_notes_default_is_empty() -> None:
    """
    Given  a Config created with no correction_notes argument
    When   the correction_notes field is read
    Then   it is an empty string
    """
    cfg = _cfg()
    assert cfg.correction_notes == ""


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
    result = _load(cfg_file)
    assert result.get("language") == "German"
    assert "unknown_key" not in result


def test_load_config_file_reads_recognition_enabled(tmp_path: Path) -> None:
    """
    Given  a TOML file that switches text recognition off
    When   load_config_file() is called
    Then   the key is returned (and recognition is on in DEFAULTS)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text("recognition_enabled = false\n")

    assert _load(cfg_file)["recognition_enabled"] is False
    assert DEFAULTS["recognition_enabled"] is True


@pytest.mark.parametrize("engine", ["langchain", "openai", "gpt"])
def test_load_config_file_replaces_online_ocr_engine(tmp_path: Path, engine: str) -> None:
    """
    Given  a settings file from an older version naming an online OCR engine
    When   load_config_file() is called
    Then   the engine falls back to the default and a notice says it was replaced
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(f'ocr_engine = "{engine}"\nocr_provider = "openai"\n')
    loaded = load_config_file(cfg_file)

    assert loaded.values["ocr_engine"] == DEFAULTS["ocr_engine"]
    assert "ocr_provider" not in loaded.values
    assert [(n.kind, n.key, n.online) for n in loaded.notices] == [
        (SettingNoticeKind.ENGINE_REPLACED, "ocr_engine", True)
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('ocr_engine = "langchain"', ("langchain", "ollama", True)),
        ('ocr_engine = "openai"', ("openai", "ollama", True)),
        ('ocr_engine = "Tesseract"', ("Tesseract", "ppocr", False)),
        ('ocr_engine = "paddleocr"', ("paddleocr", "ppocr", False)),
        ('ocr_engine = "ollama"', None),
        ('ocr_engine = "ppocr"', None),
        ('language = "German"', None),
    ],
)
def test_load_config_file_names_the_engine_it_replaced(
    tmp_path: Path, line: str, expected: tuple[str, str, bool] | None
) -> None:
    """
    Given  a settings file with an online, a removed local, a current or no OCR engine
    When   load_config_file() is called
    Then   its notice names the old engine, its replacement and whether it was online
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(line + "\n")

    notices = load_config_file(cfg_file).notices

    assert (
        None if not notices else (notices[0].detail, notices[0].replacement, notices[0].online)
    ) == expected


@pytest.mark.parametrize("old", ["tesseract", "paddleocr", " Tesseract "])
def test_a_removed_local_engine_becomes_the_built_in_reader(tmp_path: Path, old: str) -> None:
    """
    Given  a settings file naming Tesseract or PaddleOCR and an Ollama model
    When   load_config_file() is called
    Then   the engine is the built-in reader, the model is kept, and a notice says it was removed
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(f'ocr_engine = "{old}"\nocr_model = "my-model:7b"\n')

    loaded = load_config_file(cfg_file)

    assert loaded.values["ocr_engine"] == "ppocr"
    assert loaded.values["ocr_model"] == "my-model:7b"
    assert [(n.kind, n.online) for n in loaded.notices] == [
        (SettingNoticeKind.ENGINE_REPLACED, False)
    ]


@pytest.mark.parametrize("method", ["clahe", "adaptive_threshold", "grayscale", "anything"])
def test_a_method_of_the_old_preparation_reads_as_automatic(tmp_path: Path, method: str) -> None:
    """
    Given  a settings file saved before the automatic preparation, naming a method that is gone
    When   load_config_file() is called and an OcrConfig is built from the result
    Then   the method is "auto", whatever engine the file names
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(f'ocr_engine = "ppocr"\npreprocess_method = "{method}"\n')

    values = _load(cfg_file)

    assert values["preprocess_method"] == "auto"
    assert (
        OcrConfig.from_settings({"ocr_engine": "ollama", "preprocess_method": method})
    ).preprocess_method == "auto"


def test_original_page_is_kept_and_nothing_is_invented(tmp_path: Path) -> None:
    """
    Given  a settings file naming "none", and one naming no method at all
    When   load_config_file() is called
    Then   "none" is kept, and the second gets no value invented
    """
    kept = tmp_path / "kept.toml"
    kept.write_text('preprocess_method = "NONE"\n')
    unset = tmp_path / "unset.toml"
    unset.write_text('ocr_engine = "ppocr"\n')

    assert _load(kept)["preprocess_method"] == "none"
    assert "preprocess_method" not in _load(unset)


def test_the_default_preparation_is_automatic() -> None:
    """
    Given  the defaults, an OcrConfig and an empty settings dict
    When   they are read
    Then   the method is "auto" everywhere
    """
    assert DEFAULTS["preprocess_method"] == "auto"
    assert OcrConfig().preprocess_method == "auto"
    assert OcrConfig.from_settings({}).preprocess_method == "auto"


def test_the_switches_of_the_old_preparation_in_a_settings_file_are_ignored(
    tmp_path: Path,
) -> None:
    """
    Given  a settings file with the five old switch keys
    When   load_config_file() is called
    Then   none of them is in the result (they are unknown keys, ignored without a word)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(
        "preprocess_dewarp = false\npreprocess_deskew = false\npreprocess_border_crop = true\n"
        "preprocess_denoise = false\npreprocess_gamma = true\n"
    )

    assert _load(cfg_file) == {}


def test_load_config_file_resets_model_of_replaced_online_engine(tmp_path: Path) -> None:
    """
    Given  a settings file naming an online engine together with its online model
    When   load_config_file() is called
    Then   the model is reset too, so the replacement engine is not asked for 'gpt-4o'
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = "langchain"\nocr_model = "gpt-4o"\n')

    assert _load(cfg_file)["ocr_model"] == DEFAULTS["ocr_model"]


def test_load_config_file_keeps_model_of_local_engine(tmp_path: Path) -> None:
    """
    Given  a settings file naming Ollama and a model
    When   load_config_file() is called
    Then   the model is kept
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = "ollama"\nocr_model = "my-model:7b"\n')

    assert _load(cfg_file)["ocr_model"] == "my-model:7b"


def test_load_config_file_keeps_local_ocr_engines(tmp_path: Path) -> None:
    """
    Given  a settings file naming the built-in reader with stray case and spaces
    When   load_config_file() is called
    Then   the engine is kept (case and spaces folded)
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('ocr_engine = " PPOCR "\n')
    assert _load(cfg_file)["ocr_engine"] == "ppocr"


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
    result = _load(cfg_file)
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
        _load(cfg_file)


def test_load_config_file_accepts_known_correction_provider(tmp_path: Path) -> None:
    """
    Given  a TOML file with correction_provider = 'openai'
    When   load_config_file() is called
    Then   correction_provider = 'openai' is in the result
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('correction_provider = "openai"\n')
    result = _load(cfg_file)
    assert result.get("correction_provider") == "openai"


def test_load_config_file_accepts_known_evaluate_provider(tmp_path: Path) -> None:
    """
    Given  a TOML file with evaluate_provider = 'openai'
    When   load_config_file() is called
    Then   evaluate_provider = 'openai' is in the result
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('evaluate_provider = "openai"\n')
    result = _load(cfg_file)
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


def test_the_review_request_keeps_the_makers_temperature_not_step_ones() -> None:
    """
    Given  Configs for models whose step ① has a measured temperature
    When   the temperature of the single review request the app sends today is read
    Then   it is the maker's value, and step ① of the exchange keeps the measured one
    """
    local = {"correction_provider": "ollama"}
    qwen = _cfg(correction_model="qwen3.5:9b", **local)
    gemma = _cfg(correction_model="gemma4:e4b", **local)

    assert (qwen.correction_temperature, qwen.correction_profile.temperature) == (0.7, 0.05)
    assert (gemma.correction_temperature, gemma.correction_profile.temperature) == (1.0, 0.4)


def test_an_online_provider_gets_the_default_temperature() -> None:
    """
    Given  an online provider with a model name that is also a recommended Ollama tag
    When   the proofreading and review temperatures are read
    Then   the Ollama table does not apply: 0.2 and 0.7
    """
    cfg = _cfg(
        correction_provider="openai",
        correction_model="gemma4:e4b",
        evaluate_provider="openai",
        evaluate_model="gemma4:e4b",
    )

    assert cfg.correction_temperature == 0.2
    assert cfg.evaluate_temperature == 0.7


def test_the_review_temperature_defaults_to_0_7() -> None:
    """
    Given  a Config whose review model has no recommended temperature
    When   evaluate_temperature is read
    Then   it is 0.7
    """
    cfg = _cfg(evaluate_provider="ollama", evaluate_model="gpt-oss:20b")

    assert cfg.evaluate_temperature == 0.7


def test_temperature_keys_of_an_old_settings_file_are_ignored(tmp_path: Path) -> None:
    """
    Given  a settings file of an older version that still holds the three temperature keys
    When   load_config_file() reads it
    Then   none of them is passed on: teachers no longer set a temperature
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(
        "ocr_temperature = 0.3\ncorrection_temperature = 0.5\nevaluate_temperature = 0.9\n"
    )

    result = _load(cfg_file)

    assert not {"ocr_temperature", "correction_temperature", "evaluate_temperature"} & set(result)


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
    values = _load(path)
    assert values["hardware_profile"] == "16gb"
    assert type(values["hardware_profile"]) is HardwareProfileSetting


@pytest.mark.use_case("Hardware_Profile_Override")
def test_load_config_file_folds_case_and_spaces_of_the_hardware_profile(tmp_path: Path) -> None:
    """
    Given  an ocr.toml whose hardware_profile is spelled " 16GB "
    When   load_config_file is called
    Then   the profile setting is the 16 GB tier, as the GUI and the service read it too
    """
    path = tmp_path / "ocr.toml"
    path.write_text('hardware_profile = " 16GB "\n', encoding="utf-8")
    assert _load(path)["hardware_profile"] is HardwareProfileSetting.CARD_16GB


@pytest.mark.use_case("Hardware_Profile_Override")
def test_load_config_file_drops_invalid_hardware_profile_with_a_notice(tmp_path: Path) -> None:
    """
    Given  an ocr.toml with an invalid hardware_profile value
    When   load_config_file is called
    Then   the key is dropped and a notice carries the value as it was written (AC11)
    """
    path = tmp_path / "ocr.toml"
    path.write_text('hardware_profile = "supercomputer"\n', encoding="utf-8")
    loaded = load_config_file(path)

    assert "hardware_profile" not in loaded.values
    assert [(n.kind, n.key) for n in loaded.notices] == [
        (SettingNoticeKind.VALUE_IGNORED, "hardware_profile")
    ]
    assert loaded.notices[0].detail == "supercomputer"


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
    values = _load(path)
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

    assert _load(cfg_file)["pdf_text_layer"] == "always"


def test_load_config_file_drops_invalid_pdf_text_layer(tmp_path: Path) -> None:
    """
    Given  a config file with an unknown pdf_text_layer mode
    When   load_config_file() is called
    Then   the key is ignored and a notice carries the value as it was written
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('pdf_text_layer = "off"\n')

    loaded = load_config_file(cfg_file)

    assert "pdf_text_layer" not in loaded.values
    assert [(n.kind, n.key) for n in loaded.notices] == [
        (SettingNoticeKind.VALUE_IGNORED, "pdf_text_layer")
    ]
    assert loaded.notices[0].detail == "off"


def test_ocr_config_from_settings_maps_flat_keys_and_defaults() -> None:
    """
    Given  a flat settings dict with some OCR keys missing
    When   OcrConfig.from_settings() is called
    Then   present keys map onto the fields and missing ones take the defaults
    """
    ocr = OcrConfig.from_settings(
        {"ocr_engine": "ppocr", "preprocess_method": "none", "pdf_text_layer": "NEVER"}
    )

    assert ocr.engine == "ppocr"
    assert ocr.preprocess_method == "none"
    assert ocr.pdf_text_layer == "never"
    assert ocr.pdf_render_dpi == DEFAULTS["pdf_render_dpi"]


def test_load_config_file_ignores_the_removed_proofreading_keys(tmp_path: Path) -> None:
    """
    Given  a settings file from a released version, with the instructions and answer formats
    When   load_config_file() is called
    Then   the four removed keys are not in the result, and the file is not written again
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(
        'correction_prompt = "Fix my students\' homework at {level}."\n'
        'answer_format_review = "Answer in JSON."\n'
        'answer_format_text = "Return the text."\n'
        "answer_format_version = 2\n"
        'correction_notes = "Year 9."\n'
    )
    result = _load(cfg_file)

    assert "correction_prompt" not in result
    assert "answer_format_review" not in result
    assert "answer_format_text" not in result
    assert "answer_format_version" not in result
    assert result["correction_notes"] == "Year 9."
    assert "correction_prompt" not in dump_config_file(result)


def test_load_config_file_keeps_a_custom_evaluation_prompt(tmp_path: Path) -> None:
    """
    Given  a settings file with a teacher's own content-review instructions
    When   load_config_file() is called
    Then   the custom prompt is kept unchanged
    """
    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text('evaluate_prompt = "Report in German."\n')
    result = _load(cfg_file)
    assert result["evaluate_prompt"] == "Report in German."


def test_load_config_file_cuts_notes_longer_than_the_limit(tmp_path: Path) -> None:
    """
    Given  a settings file whose notes for the AI are longer than the limit
    When   load_config_file() is called
    Then   the notes are cut to the limit and a notice says so
    """
    from teachers_teammate.domain.correction_prompts import NOTES_LIMIT  # noqa: PLC0415

    cfg_file = tmp_path / "ocr.toml"
    cfg_file.write_text(f'correction_notes = "{"x" * (NOTES_LIMIT + 20)}"\n')
    loaded = load_config_file(cfg_file)

    assert loaded.values["correction_notes"] == "x" * NOTES_LIMIT
    assert [(n.kind, n.key) for n in loaded.notices] == [
        (SettingNoticeKind.NOTES_CUT, "correction_notes")
    ]


def test_load_config_file_gives_unknown_correction_settings_their_defaults(
    tmp_path: Path,
) -> None:
    """
    Given  a settings file with a mistyped level and output mode
    When   load_config_file() is called
    Then   both fall back to the defaults, and valid values (any case) are kept
    """
    bad = tmp_path / "bad.toml"
    bad.write_text('correction_level = "D9"\ncorrection_output = "texxt"\n')
    good = tmp_path / "good.toml"
    good.write_text('correction_level = "c1"\ncorrection_output = "TEXT"\n')

    assert _load(bad)["correction_level"] == DEFAULTS["correction_level"]
    assert _load(bad)["correction_output"] == DEFAULTS["correction_output"]
    assert _load(good)["correction_level"] == "C1"
    assert _load(good)["correction_output"] == "text"


# ── The settings file round-trips ──────────────────────────────────────────

#: Values a TOML basic string cannot hold as they are.
HOSTILE_TEXT = [
    "",
    "   ",
    "plain",
    "two\nlines",
    "two\nlines\n",
    "\nleading newline",
    'a quote " inside',
    'ends with a quote"',
    'has """ triple',
    "a back\\slash",
    "a carriage\rreturn",
    "a carriage return\r\npair",
    "control \x07 here",
    "nul \x00 here",
    "delete \x7f here",
    "tab\there",
    "emoji 😀 on two\nlines",
]


@pytest.mark.parametrize("text", HOSTILE_TEXT)
def test_dump_config_file_round_trips_hostile_text(text: str, tmp_path: Path) -> None:
    """
    Given  a value with characters a TOML basic string cannot hold as they are
    When   dump_config_file() writes it and it is read back with tomllib
    Then   the value is unchanged
    """
    path = tmp_path / "ocr.toml"
    path.write_text(dump_config_file({"evaluate_prompt": text}), encoding="utf-8")

    with path.open("rb") as handle:
        assert tomllib.load(handle)["evaluate_prompt"] == text


def test_dump_config_file_keeps_a_multiline_prompt_readable() -> None:
    """
    Given  a prompt of several lines that a block string can carry
    When   dump_config_file() writes it
    Then   it uses the block form and adds no newline of its own
    """
    text = "First line.\nSecond line."

    written = dump_config_file({"evaluate_prompt": text})

    assert '"""' in written
    assert tomllib.loads(written)["evaluate_prompt"] == text


def test_dump_config_file_writes_switches_inside_a_list() -> None:
    """
    Given  a list that holds a switch, a whole number and a fraction
    When   dump_config_file() serialises it
    Then   the file reads back as the same list
    """
    written = dump_config_file({"k": [True, "a", 2, 1.5]})

    assert tomllib.loads(written)["k"] == [True, "a", 2, 1.5]


@pytest.mark.parametrize("value", [Path("/tmp"), {"a": 1}, b"bytes", object()])
def test_dump_config_file_refuses_a_value_the_file_cannot_hold(value: object) -> None:
    """
    Given  a value of a type no key of the schema produces
    When   dump_config_file() serialises it
    Then   it raises TypeError naming the key
    """
    with pytest.raises(TypeError, match="input"):
        dump_config_file({"input": value})


def test_every_schema_key_survives_a_save_and_load(tmp_path: Path) -> None:
    """
    Given  a settings file holding a value for every key of the schema
    When   it is loaded and written again, twice
    Then   the file stops changing and no key was lost
    """
    values = {setting.key: setting.default for setting in SETTINGS if setting.default is not None}
    path = tmp_path / "ocr.toml"

    save_settings_file(path, values)
    save_settings_file(path, dict(load_config_file(path).values))
    settled = path.read_text(encoding="utf-8")
    save_settings_file(path, dict(load_config_file(path).values))

    assert path.read_text(encoding="utf-8") == settled
    assert set(load_config_file(path).values) == set(values)


def test_a_prompt_keeps_its_text_across_saves(tmp_path: Path) -> None:
    """
    Given  a settings file with a prompt of the teacher's own
    When   it is saved and read back three times
    Then   the prompt is the same text every time, so the content review's fingerprint holds
    """
    path = tmp_path / "ocr.toml"
    prompt = "Review this text in two sentences."
    save_settings_file(path, {"evaluate_prompt": prompt})

    seen = set()
    for _ in range(3):
        values = load_config_file(path).values
        seen.add(values["evaluate_prompt"])
        save_settings_file(path, dict(values))

    assert seen == {prompt}
    assert make_config(tmp_path, evaluate_prompt=prompt).evaluate_prompt == prompt


def test_a_prompt_that_grew_a_newline_per_save_reads_as_it_was(tmp_path: Path) -> None:
    """
    Given  a settings file whose prompt gained a newline on every save
    When   load_config_file() reads it
    Then   the trailing whitespace is gone
    """
    path = tmp_path / "ocr.toml"
    path.write_text('evaluate_prompt = """\nReview this text.\n\n"""\n', encoding="utf-8")

    assert load_config_file(path).values["evaluate_prompt"] == "Review this text."


def test_a_settings_file_that_is_not_utf8_is_a_parse_error(tmp_path: Path) -> None:
    """
    Given  a settings file an older Windows editor saved in a code page rather than UTF-8
    When   load_config_file() reads it
    Then   the failure is a ConfigFileParseError, so a caller keeps the file instead of writing
           over it
    """
    path = tmp_path / "ocr.toml"
    path.write_bytes('language = "Français"\n'.encode("cp1252"))

    with pytest.raises(ConfigFileParseError, match="not UTF-8"):
        load_config_file(path)


def test_broken_settings_copy_keeps_the_first_copy(tmp_path: Path) -> None:
    """
    Given  a settings file that cannot be read
    When   a copy is asked for twice
    Then   both copies are kept, and the file itself is unchanged
    """
    path = tmp_path / "ocr.toml"
    path.write_text("not toml at all", encoding="utf-8")

    first = broken_settings_copy(path)
    second = broken_settings_copy(path)

    assert first is not None and second is not None
    assert (first.name, second.name) == ("ocr.toml.broken", "ocr.toml.broken.1")
    assert path.read_text(encoding="utf-8") == "not toml at all"


def test_broken_settings_copy_of_a_missing_file_is_none(tmp_path: Path) -> None:
    """
    Given  no settings file
    When   a copy is asked for
    Then   there is nothing to copy
    """
    assert broken_settings_copy(tmp_path / "ocr.toml") is None


def test_save_settings_file_leaves_no_temporary_file(tmp_path: Path) -> None:
    """
    Given  settings written in one step
    When   the folder is listed afterwards
    Then   only the settings file is there
    """
    save_settings_file(tmp_path / "ocr.toml", {"language": "German"})

    assert [path.name for path in tmp_path.iterdir()] == ["ocr.toml"]
