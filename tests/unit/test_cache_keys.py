"""Unit tests for the keys of the stored results, and the facts they are made of."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from teachers_teammate.cache_keys import (
    anonymization_fingerprint,
    correction_key,
    ocr_key,
    preprocess_fingerprint,
    preview_key,
    privacy_key,
    review_key,
    sent_record,
)
from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.local_models import AUTO_VERSION
from teachers_teammate.domain.model_catalog import RequestProfile
from teachers_teammate.domain.pdf_text_layer import input_fingerprint
from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.domain.stages import Stage
from tests.conftest import make_config

_PDF = Path("worksheet.pdf")


@dataclass
class _FakeFacts:
    """Facts of a made-up computer: no model folder, one language, one exchange."""

    steps: tuple[str, ...] = ()
    language: str = "English"
    asked: int = 0

    def page_steps(self) -> tuple[str, ...]:
        self.asked += 1
        return self.steps

    def system_language(self) -> str:
        self.asked += 1
        return self.language

    def exchange_identity(self, profile: RequestProfile, *, corrections_only: bool) -> str:
        self.asked += 1
        return f"exchange|{profile.fingerprint()}|{'text' if corrections_only else 'review'}"


@pytest.mark.unit
def test_the_keys_need_nothing_from_the_computer_but_the_facts(tmp_path: Path) -> None:
    """
    Given  a document path that is not on the disk at all, and the facts of a made-up computer
    When   every key of it is computed
    Then   each key comes out without reading a file, and the facts were asked for what they hold
    """
    config = make_config(
        tmp_path,
        ocr=OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.AUTO),
        correction_enabled=True,
        correction_provider="ollama",
        evaluation_enabled=True,
        anonymization_enabled=True,
    )
    facts = _FakeFacts(steps=("turn",))
    missing = tmp_path / "gone" / "page.png"

    assert ocr_key(config, missing, facts) != ""
    assert preview_key(config, missing, "abc123", facts) != ""
    assert correction_key(config, None, facts) != ""
    assert review_key(config) != ""
    assert privacy_key(config, facts) != ""
    assert sent_record(config, Stage.CORRECTION, facts=facts).privacy != ""
    assert facts.asked > 0


@pytest.mark.unit
def test_the_automatic_fingerprint_names_version_output_and_downloaded_page_models() -> None:
    """
    Given  the automatic preparation for the built-in reader, without and with the page models
    When   the preprocessing fingerprint is taken of each
    Then   it names the method, the version, the output, the model steps, the DPI and the
           text-layer entries, so downloading the models reads the pages again once
    """
    ocr = OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.AUTO, pdf_render_dpi=600)

    without = preprocess_fingerprint(ocr, _PDF, _FakeFacts())
    with_models = preprocess_fingerprint(ocr, _PDF, _FakeFacts(steps=("turn", "flatten")))

    assert without == (
        "auto",
        f"v{AUTO_VERSION}",
        "colour",
        "600",
        *input_fingerprint(".pdf", ocr.pdf_text_layer),
    )
    assert with_models[1:5] == (f"v{AUTO_VERSION}", "colour", "+turn", "+flatten")


@pytest.mark.unit
def test_the_original_page_key_is_the_method_alone() -> None:
    """
    Given  "Original page" (method none)
    When   the preprocessing fingerprint is taken without and with the page models
    Then   both are the method, the DPI and the text-layer entries: the models change nothing
    """
    ocr = OcrConfig(engine="ppocr", preprocess_method=PreprocessMethod.NONE)

    plain = preprocess_fingerprint(ocr, _PDF, _FakeFacts())

    assert plain[0] == "none"
    assert plain == preprocess_fingerprint(ocr, _PDF, _FakeFacts(steps=("turn", "flatten")))


@pytest.mark.unit
def test_the_ollama_key_does_not_follow_the_reader_models(tmp_path: Path) -> None:
    """
    Given  a fixed Ollama configuration
    When   its OCR key is computed before and after the reader's fingerprint changes
    Then   the key is the same: only the built-in reader's key follows its own models
    """
    config = make_config(
        tmp_path,
        language="German",
        ocr=OcrConfig(engine="ollama", model="qwen3-vl:8b", temperature=0.0),
    )
    facts = _FakeFacts()

    key = ocr_key(config, Path("page.png"), facts)
    with patch(
        "teachers_teammate.cache_keys.reader_fingerprint",
        return_value="PP-OCRv6_small@new+new/v2",
    ):
        assert ocr_key(config, Path("page.png"), facts) == key


@pytest.mark.unit
def test_the_reader_key_ignores_language_and_ollama_model_but_not_the_models(
    tmp_path: Path,
) -> None:
    """
    Given  the built-in reader in two languages, with two Ollama models chosen
    When   their OCR keys are computed, before and after the reader's fingerprint changes
    Then   language and model leave the key alone, and another reader fingerprint changes it
    """
    facts = _FakeFacts()
    base = make_config(tmp_path, language="German", ocr_engine="ppocr", ocr_model="a:1")
    other = make_config(tmp_path, language="French", ocr_engine="ppocr", ocr_model="b:2")

    key = ocr_key(base, Path("page.png"), facts)
    assert key == ocr_key(other, Path("page.png"), facts)
    with patch(
        "teachers_teammate.cache_keys.reader_fingerprint",
        return_value="PP-OCRv6_small@new+new/v2",
    ):
        assert ocr_key(base, Path("page.png"), facts) != key


@pytest.mark.unit
def test_a_downloaded_page_model_changes_the_automatic_key_once(tmp_path: Path) -> None:
    """
    Given  a config with the automatic preparation
    When   its OCR key is computed without and with the page models downloaded
    Then   the two keys differ, and the same facts give the same key every time
    """
    config = make_config(tmp_path, ocr=OcrConfig(engine="ollama", preprocess_method=PreprocessMethod.AUTO))

    without = ocr_key(config, _PDF, _FakeFacts())
    with_models = ocr_key(config, _PDF, _FakeFacts(steps=("turn", "flatten")))

    assert without != with_models
    assert with_models == ocr_key(config, _PDF, _FakeFacts(steps=("turn", "flatten")))


@pytest.mark.unit
def test_the_system_language_is_the_only_thing_the_extra_model_leaves_open(
    tmp_path: Path,
) -> None:
    """
    Given  personal-info removal with the extra model set to the system's language
    When   the privacy key is computed for two computers set to different languages
    Then   the two keys differ, because the extra model differs
    """
    config = make_config(
        tmp_path,
        anonymization_enabled=True,
        anonymizer_secondary_model="auto:system",
    )

    german = anonymization_fingerprint(config, _FakeFacts(language="German"))
    english = anonymization_fingerprint(config, _FakeFacts(language="English"))

    assert german != english
    assert german == anonymization_fingerprint(config, _FakeFacts(language="German"))


@pytest.mark.unit
def test_sent_record_names_the_step_and_where_it_ran(tmp_path: Path) -> None:
    """
    Given  a config that proofreads on the local Ollama and reviews on an online provider
    When   the record of what was sent is asked for each stage
    Then   each names its own provider and model, and only the Ollama one carries a host
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="qwen3.5:9b",
        evaluation_enabled=True,
        evaluate_provider="mistral_eu",
        evaluate_model="mistral-large-latest",
    )
    facts = _FakeFacts()

    proofreading = sent_record(config, Stage.CORRECTION, names_hidden=3, facts=facts)
    review = sent_record(config, Stage.EVALUATION, facts=facts)

    assert proofreading.provider == "ollama"
    assert proofreading.model == "qwen3.5:9b"
    assert proofreading.endpoint == "127.0.0.1"
    assert proofreading.names_hidden == 3
    assert proofreading.hiding_on is False
    assert review.provider == "mistral_eu"
    assert review.model == "mistral-large-latest"
    assert review.endpoint == ""
    assert review.names_hidden == 0


@pytest.mark.unit
def test_sent_record_says_whether_hiding_was_on_when_no_name_was_found(tmp_path: Path) -> None:
    """
    Given  a config with personal info removal on, and a text in which nothing was hidden
    When   the record of the proofreading is asked for
    Then   it says hiding was on although it counts no names
    """
    config = make_config(tmp_path, correction_enabled=True, anonymization_enabled=True)

    record = sent_record(config, Stage.CORRECTION, names_hidden=0, facts=_FakeFacts())

    assert record.hiding_on is True
    assert record.names_hidden == 0


@pytest.mark.unit
def test_the_privacy_key_follows_the_anonymizer_settings(tmp_path: Path) -> None:
    """
    Given  two configs that differ only in whether personal info is removed
    When   the privacy key is asked for each
    Then   the keys differ, and the same settings give the same key
    """
    facts = _FakeFacts()
    plain = make_config(tmp_path, anonymization_enabled=False)
    hidden = make_config(tmp_path, anonymization_enabled=True)

    assert privacy_key(plain, facts) != privacy_key(hidden, facts)
    assert privacy_key(hidden, facts) == privacy_key(hidden, facts)
