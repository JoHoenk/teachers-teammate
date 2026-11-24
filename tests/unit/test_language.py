"""Unit tests for the extra detection model default and the system-locale mapping."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path

import pytest

from teachers_teammate.settings_schema import DEFAULTS
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.language import LANGUAGES, language_from_locale
from teachers_teammate.domain.name_models import (
    FALLBACK_SPACY_MODEL,
    SPACY_MODELS,
    spacy_model_for,
)
from teachers_teammate.infrastructure.anonymizer import resolve_anonymizer_config
from teachers_teammate.infrastructure.spacy_models import SPACY_MODEL_PINS, spacy_model_url
from teachers_teammate.infrastructure.system_locale import system_language, system_locale_code
from teachers_teammate.settings_schema import SECONDARY_MODEL_AUTO, SECONDARY_MODEL_NONE
from tests.conftest import make_config

# ── language_from_locale ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("de_DE", "German"),
        ("de_DE.UTF-8", "German"),
        ("de-DE", "German"),
        ("de", "German"),
        ("en_US", "English"),
        ("fr_FR@euro", "French"),
        ("pt_BR", "Portuguese"),
    ],
)
def test_locale_maps_to_language_name(code: str, expected: str) -> None:
    """
    Given  a system locale code
    When   language_from_locale() is called
    Then   it returns the language name used in the document-language list
    """
    assert language_from_locale(code) == expected


@pytest.mark.parametrize("code", ["", None, "C", "POSIX", "tlh_KL"])
def test_locale_unknown_gives_none(code: str | None) -> None:
    """
    Given  an empty, unset or unsupported locale
    When   language_from_locale() is called
    Then   it returns None
    """
    assert language_from_locale(code) is None


def test_every_mapped_language_is_offered() -> None:
    """
    Given  the language list the pickers show
    When   a locale is mapped to a language name
    Then   that name is one of them (so the name exists in the UI)
    """
    for code in ("en_GB", "de_AT", "fr_BE", "es_ES", "it_IT", "pt_PT", "nl_NL"):
        assert language_from_locale(code) in LANGUAGES


# ── system locale ──────────────────────────────────────────────────────────


def test_system_locale_falls_back_to_environment() -> None:
    """
    Given  locale.getlocale() reports nothing
    When   system_locale_code() is asked
    Then   it uses LANG, and system_language() names that language
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("locale.getlocale", lambda: (None, None))
        patch.setenv("LANG", "de_DE.UTF-8")
        patch.delenv("LC_ALL", raising=False)
        patch.delenv("LC_MESSAGES", raising=False)

        assert system_locale_code() == "de_DE.UTF-8"
        assert system_language() == "German"


def test_system_language_none_when_nothing_is_set() -> None:
    """
    Given  neither locale.getlocale() nor LANG/LC_ALL report a locale
    When   system_language() is asked
    Then   it returns None instead of guessing
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("locale.getlocale", lambda: (None, None))
        for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
            patch.delenv(name, raising=False)

        assert system_language() is None


# ── resolving the extra detection model ────────────────────────────────────


def test_auto_uses_the_system_language_model() -> None:
    """
    Given  the "auto:system" default, a German document and an English system
    When   the anonymizer config is resolved
    Then   the English model is added as the extra detection model
    """
    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO),
        document_language="German",
        system_language="English",
        installed_models=["de_core_news_sm", "en_core_web_sm"],
    )

    assert resolved.secondary_model == "en_core_web_sm"


def test_auto_drops_the_extra_model_for_the_same_language() -> None:
    """
    Given  "auto:system" and a system language whose model is already the primary one
    When   the anonymizer config is resolved
    Then   no extra model is used (a second pass with the same model adds nothing)
    """
    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO),
        document_language="German",
        system_language="German",
        installed_models=["de_core_news_sm"],
    )

    assert resolved.secondary_model is None


def test_auto_missing_model_warns_and_skips() -> None:
    """
    Given  "auto:system" but the system language's model is not downloaded
    When   the anonymizer config is resolved
    Then   a warning names the model and no extra model is used
    """
    warnings: list[str] = []

    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO),
        document_language="German",
        system_language="English",
        installed_models=["de_core_news_sm"],
        warn=warnings.append,
    )

    assert resolved.secondary_model is None
    assert len(warnings) == 1
    assert "en_core_web_sm" in warnings[0]


def test_auto_without_system_language_warns_and_skips() -> None:
    """
    Given  "auto:system" and a system language that could not be determined
    When   the anonymizer config is resolved
    Then   a warning says so and no extra model is used
    """
    warnings: list[str] = []

    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO),
        document_language="German",
        system_language=None,
        warn=warnings.append,
    )

    assert resolved.secondary_model is None
    assert warnings and "could not be determined" in warnings[0]


def test_explicit_model_is_kept_even_when_missing() -> None:
    """
    Given  an explicitly chosen extra model that is not installed
    When   the anonymizer config is resolved
    Then   it is kept as it is (the anonymizer then fails as before)
    """
    warnings: list[str] = []

    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model="xx_ent_wiki_sm"),
        document_language="German",
        system_language="English",
        installed_models=["de_core_news_sm"],
        warn=warnings.append,
    )

    assert resolved.secondary_model == "xx_ent_wiki_sm"
    assert warnings == []


@pytest.mark.parametrize("configured", [None, SECONDARY_MODEL_NONE, ""])
def test_explicit_none_means_no_extra_model(configured: str | None) -> None:
    """
    Given  a config that switches the extra pass off (None, "none" or "")
    When   the anonymizer config is resolved
    Then   no extra model is used
    """
    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=configured),
        document_language="German",
        system_language="English",
        installed_models=["en_core_web_sm"],
    )

    assert resolved.secondary_model is None


def test_resolution_keeps_primary_and_patterns() -> None:
    """
    Given  a config with an explicit primary model and patterns
    When   the extra model is resolved
    Then   the primary model and the patterns are unchanged
    """
    config = AnonymizerConfig(
        primary_model="xx_ent_wiki_sm",
        secondary_model=SECONDARY_MODEL_AUTO,
        patterns=(("EMAIL", r"\S+@\S+"),),
    )

    resolved = resolve_anonymizer_config(
        config, document_language="German", system_language="English", installed_models=()
    )

    assert resolved.primary_model == "xx_ent_wiki_sm"
    assert resolved.patterns == (("EMAIL", r"\S+@\S+"),)


# ── config compatibility ───────────────────────────────────────────────────


def test_default_extra_model_is_auto_system() -> None:
    """
    Given  the shipped defaults
    When   they are read
    Then   the extra detection model follows the system language
    """
    assert DEFAULTS["anonymizer_secondary_model"] == SECONDARY_MODEL_AUTO
    assert AnonymizerConfig().secondary_model is None


def test_old_config_with_none_still_means_no_extra_model(tmp_path: Path) -> None:
    """
    Given  a settings value saved by an older version (None) and a config file holding "none"
    When   a Config is built from it
    Then   no extra model is used, so an earlier choice is honoured
    """
    from teachers_teammate.config import load_config_file  # noqa: PLC0415

    config = make_config(tmp_path, anonymizer_secondary_model=None)
    assert config.anonymizer_secondary_model is None

    path = tmp_path / "ocr.toml"
    path.write_text('anonymizer_secondary_model = "none"\n', encoding="utf-8")
    assert load_config_file(path).values["anonymizer_secondary_model"] == SECONDARY_MODEL_NONE


def test_replacing_a_config_field_keeps_the_resolved_value() -> None:
    """
    Given  a resolved anonymizer config
    When   another field is replaced
    Then   the resolved extra model stays as it was resolved
    """
    resolved = resolve_anonymizer_config(
        AnonymizerConfig(secondary_model=SECONDARY_MODEL_AUTO),
        document_language="German",
        system_language="English",
        installed_models=["en_core_web_sm"],
    )

    assert replace(resolved, primary_model="de_core_news_sm").secondary_model == "en_core_web_sm"


def test_spacy_model_for_unknown_language_falls_back() -> None:
    """
    Given  a language spaCy has no model for
    When   spacy_model_for() is asked
    Then   it returns the multilingual model
    """
    assert spacy_model_for("Klingon") == "xx_ent_wiki_sm"


def test_every_offered_spacy_model_has_a_pin() -> None:
    """
    Given  the models the app offers
    When   their pins are looked up
    Then   each has one, and the fallback is among them
    """
    offered = {*SPACY_MODELS.values(), FALLBACK_SPACY_MODEL}

    assert offered <= set(SPACY_MODEL_PINS)


def _addon_lock() -> Path:
    """Return the add-on lock from the Bazel runfiles, or from the source checkout."""
    root = os.environ.get("TEST_SRCDIR", "")
    relative = os.environ.get("ADDON_LOCK", "")
    if root and relative:
        return Path(root) / relative
    return Path(__file__).resolve().parents[2] / "requirements_addons_lock.txt"


def test_every_spacy_pin_matches_the_locked_spacy() -> None:
    """
    Given  the pinned model versions and the add-on lock
    When   the locked spaCy version is read
    Then   every pin's major.minor is the same — a spaCy bump is a model bump
    """
    locked = next(
        line.split("==")[1].strip().rstrip("\\")
        for line in _addon_lock().read_text(encoding="utf-8").splitlines()
        if line.startswith("spacy==")
    )

    assert {version.rsplit(".", 1)[0] for version in SPACY_MODEL_PINS.values()} == {
        locked.rsplit(".", 1)[0]
    }


def test_a_spacy_model_url_names_the_pinned_wheel() -> None:
    """
    Given  a model the app offers
    When   its download URL is built
    Then   it is the GitHub release of the pinned version, with the wheel name spaCy publishes
    """
    assert spacy_model_url("en_core_web_sm") == (
        "https://github.com/explosion/spacy-models/releases/download/"
        "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
    )


def test_a_model_without_a_pin_cannot_be_downloaded() -> None:
    """
    Given  a model name the app does not offer
    When   its download URL is built
    Then   it raises instead of guessing a version
    """
    with pytest.raises(KeyError):
        spacy_model_url("es_core_news_sm")


# ── Windows and macOS locale names ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("Dutch_Netherlands.1252", "Dutch"),
        ("English_United States", "English"),
        ("German_Germany", "German"),
        ("Portuguese_Brazil", "Portuguese"),
    ],
)
def test_windows_locale_names_map_to_language_name(code: str, expected: str) -> None:
    """
    Given  a Windows locale name that spells the language out
    When   language_from_locale() is called
    Then   it returns the language name used in the document-language list
    """
    assert language_from_locale(code) == expected


def test_system_language_understands_a_windows_locale() -> None:
    """
    Given  locale.getlocale() answers like Windows does ("Dutch_Netherlands", "1252")
    When   system_language() is asked
    Then   it names the language instead of giving None
    """
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("locale.getlocale", lambda: ("Dutch_Netherlands", "1252"))

        assert system_language() == "Dutch"
