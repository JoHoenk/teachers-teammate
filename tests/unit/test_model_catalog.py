"""Unit tests for the Ollama model catalog (``domain/model_catalog.py``)."""

from __future__ import annotations

import pytest
from teachers_teammate._model_defaults import _DEFAULT_MODELS, default_model_for_task
from teachers_teammate.domain.stages import Stage
from teachers_teammate.settings_schema import DEFAULTS

from teachers_teammate.domain.hardware_profile import HARDWARE_PROFILES
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DEFAULT_WRITING_MODEL,
    OLLAMA_CATALOG,
    find_spec,
    licence_note,
)
from teachers_teammate.domain.model_fit import RECOMMENDED
from teachers_teammate.domain.model_tags import normalize_tag


def test_catalog_tags_are_unique_and_normalised() -> None:
    """
    Given  the Ollama model catalog
    When   tags are inspected
    Then   every tag is unique and already normalised (name:tag)
    """
    tags = [spec.tag for spec in OLLAMA_CATALOG]
    assert len(tags) == len(set(tags))
    for tag in tags:
        assert tag == normalize_tag(tag)


def test_every_catalog_entry_names_its_licence_and_where_to_read_it() -> None:
    """
    Given  the Ollama model catalog
    When   the licence of every entry is inspected
    Then   each names its terms and links to them, so the app can say what a teacher agrees to
    """
    for spec in OLLAMA_CATALOG:
        assert spec.licence is not None, spec.tag
        assert spec.licence.name, spec.tag
        assert spec.licence.url.startswith("https://"), spec.tag


def test_only_a_licence_that_restricts_use_gets_a_sentence() -> None:
    """
    Given  the default reading model (modified OpenRAIL-M) and a model under Apache-2.0
    When   the sentence for a model's row is asked for
    Then   the restrictive one has one, the permissive one and an unknown tag have none
    """
    assert "check the licence" in licence_note(DEFAULT_OCR_MODEL)
    assert licence_note(DEFAULT_WRITING_MODEL) == ""
    assert licence_note("a-model-the-app-does-not-know:latest") == ""


def test_catalog_memory_and_download_sizes_are_plausible() -> None:
    """
    Given  the Ollama model catalog
    When   memory_gb and download_gb are inspected
    Then   memory_gb is at least 70 % of download_gb for every entry: a download can hold parts
           that are not loaded for text (gemma4's image-reading projector is about 1 GB)
    """
    for spec in OLLAMA_CATALOG:
        assert spec.download_gb > 0
        assert spec.memory_gb >= 0.7 * spec.download_gb, spec.tag


@pytest.mark.use_case("Model_Category_Guidance")
def test_default_ocr_model_is_a_catalogued_reading_model() -> None:
    """
    Given  the static DEFAULTS and per-task model defaults
    When   the Ollama OCR default is inspected
    Then   it is a catalogued reading model (regression: gpt-oss cannot read images)
           and no online provider has an OCR default
    """
    assert DEFAULTS["ocr_model"] == DEFAULT_OCR_MODEL
    spec = find_spec(DEFAULTS["ocr_model"])
    assert spec is not None
    assert spec.category == "ocr"
    assert Stage.OCR not in _DEFAULT_MODELS  # no online provider ever reads pages


def test_default_writing_models_equal_the_default_writing_model() -> None:
    """
    Given  the static DEFAULTS, per-task model defaults, and provider metadata
    When   the Ollama writing-model defaults are inspected
    Then   all equal DEFAULT_WRITING_MODEL, a catalogued writing model
    """
    expected = DEFAULT_WRITING_MODEL
    spec = find_spec(expected)
    assert spec is not None
    assert spec.category == "general"
    assert DEFAULTS["correction_model"] == expected
    assert DEFAULTS["evaluate_model"] == expected
    assert _DEFAULT_MODELS[Stage.CORRECTION]["ollama"] == expected
    assert _DEFAULT_MODELS[Stage.EVALUATION]["ollama"] == expected


def test_an_unknown_provider_or_task_has_no_default_model() -> None:
    """
    Given  the per-task model defaults
    When   a default model is asked for a name no provider declares, or for text recognition
    Then   it raises instead of answering with another service's model
    """
    with pytest.raises(ValueError, match="provider"):
        default_model_for_task("no_such_provider", Stage.CORRECTION)
    with pytest.raises(ValueError, match="task"):
        default_model_for_task("ollama", Stage.OCR)


@pytest.mark.use_case("Model_Category_Guidance")
def test_catalog_notes_do_not_claim_unverified_capabilities() -> None:
    """
    Given  the catalog
    When   the notes of writing models are read
    Then   none claims image reading unless it is catalogued as a reading model too
    """
    for spec in OLLAMA_CATALOG:
        if spec.category == "general" and "ocr" not in spec.also_uses:
            assert "image" not in spec.note.lower(), spec.tag


@pytest.mark.use_case("Model_Category_Guidance")
def test_writing_models_offered_first_pass_the_structured_output_check() -> None:
    """
    Given  the default writing model and the RECOMMENDED[general] lists
    When   their catalog flags are inspected
    Then   the model the app picks for proofreading and every entry of every general list are
           marked as answering the review schema — the rule that an unverified model must not be
           one the app proposes, be it as the quality or as the speed model
    """
    default = find_spec(DEFAULT_WRITING_MODEL)
    assert default is not None
    assert default.structured_output, f"{DEFAULT_WRITING_MODEL} must support explanations"

    for profile in HARDWARE_PROFILES:
        for tag in RECOMMENDED["general"][profile]:
            spec = find_spec(tag)
            assert spec is not None
            assert spec.structured_output, f"{tag} is offered for {profile}"
