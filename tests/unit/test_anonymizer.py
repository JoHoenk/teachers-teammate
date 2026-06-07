"""Unit tests for teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer.

spacy and its NLP models are mocked throughout so these tests run fully
offline without any ML dependencies.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.infrastructure.anonymizer import (
    AnonymizerConfig,
    DEFAULT_PATTERNS,
    SpacyAnonymizer,
    _spacy_model_for,
)


# ── _spacy_model_for ───────────────────────────────────────────────────────


def test_spacy_model_for_known_languages() -> None:
    """
    Given  known language names (English, german, French)
    When   _spacy_model_for() is called
    Then   each resolves to the correct spaCy model slug
    """
    assert _spacy_model_for("English") == "en_core_web_sm"
    assert _spacy_model_for("german") == "de_core_news_sm"
    assert _spacy_model_for("French") == "fr_core_news_sm"


def test_spacy_model_for_unknown_falls_back() -> None:
    """
    Given  an unknown language name not in the mapping
    When   _spacy_model_for() is called
    Then   the multilingual fallback model 'xx_ent_wiki_sm' is returned
    """
    assert _spacy_model_for("Klingon") == "xx_ent_wiki_sm"


# ── AnonymizerConfig ───────────────────────────────────────────────────────


def test_anonymizer_config_defaults() -> None:
    """
    Given  an AnonymizerConfig created with no arguments
    When   its fields are accessed
    Then   secondary_model is None, primary_model is None, and patterns equal DEFAULT_PATTERNS
    """
    cfg = AnonymizerConfig()
    assert cfg.secondary_model is None
    assert cfg.primary_model is None
    assert cfg.patterns == tuple(DEFAULT_PATTERNS)


def test_anonymizer_config_custom() -> None:
    """
    Given  an AnonymizerConfig with a secondary model and empty patterns
    When   its fields are accessed
    Then   the values are stored correctly and primary_model defaults to None
    """
    cfg = AnonymizerConfig(secondary_model="xx_ent_wiki_sm", patterns=())
    assert cfg.secondary_model == "xx_ent_wiki_sm"
    assert cfg.primary_model is None
    assert cfg.patterns == ()


def test_anonymizer_config_with_primary_model() -> None:
    """
    Given  an AnonymizerConfig constructed with a specific primary_model
    When   primary_model is accessed
    Then   the configured value is stored
    """
    cfg = AnonymizerConfig(primary_model="xx_ent_wiki_sm")
    assert cfg.primary_model == "xx_ent_wiki_sm"


def test_anonymizer_config_is_hashable() -> None:
    """
    Given  two equal AnonymizerConfig instances
    When   they are used as dict keys
    Then   they hash to the same bucket
    """
    a = AnonymizerConfig()
    b = AnonymizerConfig()
    d = {a: 1}
    assert d[b] == 1


def test_anonymizer_config_with_list_patterns_is_not_hashable() -> None:
    """
    Given  an AnonymizerConfig whose patterns field holds a list instead of a tuple
    When   it is used as a dict key (e.g. as a cache key in anonymize_preview)
    Then   a TypeError is raised — regression guard for the privacy-preview 'unhashable type list' bug
    """
    cfg = AnonymizerConfig.__new__(AnonymizerConfig)
    # bypass frozen enforcement to inject a list (simulates the buggy _main_window code path)
    object.__setattr__(cfg, "secondary_model", None)
    object.__setattr__(cfg, "primary_model", None)
    object.__setattr__(cfg, "patterns", list(DEFAULT_PATTERNS))

    with pytest.raises(TypeError, match="unhashable type"):
        _ = {cfg: 1}


def test_anonymizer_config_with_tuple_patterns_is_hashable() -> None:
    """
    Given  an AnonymizerConfig built from DEFAULT_PATTERNS wrapped in tuple()
    When   it is used as a dict key
    Then   no TypeError is raised — verifies the fix applied in _main_window privacy-preview path
    """
    cfg = AnonymizerConfig(patterns=tuple(DEFAULT_PATTERNS))
    d = {cfg: 1}
    assert d[cfg] == 1


# ── SpacyAnonymizer helpers ────────────────────────────────────────────────


def _make_anonymizer(
    ner_entities: list[tuple[int, int, str, str]] | None = None,
    secondary_ner_entities: list[tuple[int, int, str, str]] | None = None,
    config: AnonymizerConfig | None = None,
) -> SpacyAnonymizer:
    """Build a SpacyAnonymizer with mocked spacy pipelines.

    Bypasses ``__init__`` entirely via ``__new__``.  *ner_entities* and
    *secondary_ner_entities* are lists of *(start_char, end_char, text, label)*
    tuples returned by the respective mock NLP objects.
    """
    ner_entities = ner_entities or []
    anon = SpacyAnonymizer.__new__(SpacyAnonymizer)
    anon._config = config if config is not None else AnonymizerConfig()
    anon._nlp = _mock_nlp(ner_entities)
    anon._nlp_secondary = (
        _mock_nlp(secondary_ner_entities) if secondary_ner_entities is not None else None
    )
    return anon


def _mock_nlp(entities: list[tuple[int, int, str, str]]) -> MagicMock:
    mock_ents = []
    for start, end, text, label in entities:
        ent = MagicMock()
        ent.start_char = start
        ent.end_char = end
        ent.text = text
        ent.label_ = label
        mock_ents.append(ent)
    mock_doc = MagicMock()
    mock_doc.ents = mock_ents
    return MagicMock(return_value=mock_doc)


# ── anonymize: person names ────────────────────────────────────────────────


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_anonymize_replaces_person_entity() -> None:
    """
    Given  text with a PERSON entity 'Alice'
    When   anonymize() is called
    Then   'Alice' is replaced with '[PERSON_1]' and mapping contains the reverse
    """
    anon = _make_anonymizer([(0, 5, "Alice", "PERSON")])
    result, mapping = anon.anonymize("Alice went to the shop.")
    assert "[PERSON_1]" in result
    assert "Alice" not in result
    assert mapping["[PERSON_1]"] == "Alice"


def test_anonymize_per_label_also_replaced() -> None:
    """
    Given  a 'PER' label entity (used by German/multilingual models)
    When   anonymize() is called
    Then   the entity is replaced the same way as 'PERSON'
    """
    anon = _make_anonymizer([(0, 4, "Hans", "PER")])
    result, mapping = anon.anonymize("Hans schrieb den Brief.")
    assert "[PERSON_1]" in result
    assert mapping["[PERSON_1]"] == "Hans"


def test_anonymize_same_name_gets_same_placeholder() -> None:
    """
    Given  'Alice' appears twice in the text as PERSON entities
    When   anonymize() is called
    Then   both occurrences map to '[PERSON_1]' (deduplication)
    """
    anon = _make_anonymizer(
        [
            (0, 5, "Alice", "PERSON"),
            (16, 21, "Alice", "PERSON"),
        ]
    )
    result, mapping = anon.anonymize("Alice said hello. Alice smiled.")
    assert result.count("[PERSON_1]") == 2
    assert len([k for k in mapping if k.startswith("[PERSON_")]) == 1


def test_anonymize_two_different_names_get_different_placeholders() -> None:
    """
    Given  'Alice' and 'Bob' both appear as PERSON entities
    When   anonymize() is called
    Then   each name gets its own placeholder
    """
    anon = _make_anonymizer(
        [
            (0, 5, "Alice", "PERSON"),
            (15, 18, "Bob", "PERSON"),
        ]
    )
    result, mapping = anon.anonymize("Alice greeted Bob.")
    assert "[PERSON_1]" in result
    assert "[PERSON_2]" in result
    assert len(mapping) == 2


# ── anonymize: secondary NER model ────────────────────────────────────────


def test_anonymize_secondary_model_adds_non_overlapping_span() -> None:
    """
    Given  primary NER finds 'Alice' and secondary NER finds 'Müller' (non-overlapping)
    When   anonymize() is called
    Then   both names are replaced with distinct PERSON placeholders
    """
    text = "Alice and Müller were present."
    anon = _make_anonymizer(
        ner_entities=[(0, 5, "Alice", "PERSON")],
        secondary_ner_entities=[(10, 16, "Müller", "PER")],
    )
    result, mapping = anon.anonymize(text)
    assert "Alice" not in result
    assert "Müller" not in result
    assert len([k for k in mapping if k.startswith("[PERSON_")]) == 2


def test_anonymize_secondary_model_skips_overlapping_span() -> None:
    """
    Given  primary and secondary NER both tag the same character range
    When   anonymize() is called
    Then   only one placeholder is produced for that span (no duplicate)
    """
    text = "Alice was here."
    anon = _make_anonymizer(
        ner_entities=[(0, 5, "Alice", "PERSON")],
        secondary_ner_entities=[(0, 5, "Alice", "PERSON")],
    )
    result, mapping = anon.anonymize(text)
    assert result.count("[PERSON_1]") == 1
    assert len([k for k in mapping if k.startswith("[PERSON_")]) == 1


def test_anonymize_no_secondary_model_when_none() -> None:
    """
    Given  an anonymizer with _nlp_secondary=None
    When   anonymize() is called
    Then   only the primary model's entities are anonymized
    """
    text = "Alice was here."
    anon = _make_anonymizer(ner_entities=[(0, 5, "Alice", "PERSON")])
    assert anon._nlp_secondary is None
    result, mapping = anon.anonymize(text)
    assert "[PERSON_1]" in result
    assert len(mapping) == 1


# ── anonymize: regex patterns ──────────────────────────────────────────────


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_anonymize_replaces_email() -> None:
    """
    Given  text containing an email address
    When   anonymize() is called (no NER entities)
    Then   the email is replaced with '[EMAIL_1]'
    """
    anon = _make_anonymizer()
    result, mapping = anon.anonymize("Contact us at hello@example.com for info.")
    assert "[EMAIL_1]" in result
    assert "hello@example.com" not in result
    assert mapping["[EMAIL_1]"] == "hello@example.com"


def test_anonymize_replaces_iban() -> None:
    """
    Given  text containing a German IBAN
    When   anonymize() is called
    Then   the IBAN is replaced with '[IBAN_1]'
    """
    anon = _make_anonymizer()
    text = "Bitte überweisen Sie auf DE89370400440532013000."
    result, mapping = anon.anonymize(text)
    assert "[IBAN_1]" in result
    assert "DE89370400440532013000" not in result


def test_anonymize_custom_patterns_used() -> None:
    """
    Given  an AnonymizerConfig with a custom pattern (student ID) and no defaults
    When   anonymize() is called
    Then   only the custom pattern is applied
    """
    config = AnonymizerConfig(patterns=(("STUID", r"\bSTU-\d{6}\b"),))
    anon = _make_anonymizer(config=config)
    result, mapping = anon.anonymize("Student STU-123456 submitted.")
    assert "[STUID_1]" in result
    assert "[EMAIL_" not in result  # default EMAIL pattern not active


def test_anonymize_empty_patterns_skips_regex() -> None:
    """
    Given  an AnonymizerConfig with an empty patterns tuple
    When   anonymize() is called on text with an email address
    Then   the email is not replaced
    """
    config = AnonymizerConfig(patterns=())
    anon = _make_anonymizer(config=config)
    result, mapping = anon.anonymize("Contact hello@example.com")
    assert "hello@example.com" in result
    assert mapping == {}


def test_anonymize_returns_empty_map_for_clean_text() -> None:
    """
    Given  text with no PII
    When   anonymize() is called
    Then   the text is unchanged and the map is empty
    """
    anon = _make_anonymizer()
    result, mapping = anon.anonymize("The sky is blue today.")
    assert result == "The sky is blue today."
    assert mapping == {}


# ── restore ────────────────────────────────────────────────────────────────


def test_restore_replaces_placeholders() -> None:
    """
    Given  a mapping and text with placeholders
    When   restore() is called
    Then   placeholders are replaced with the original values
    """
    anon = _make_anonymizer()
    mapping = {"[PERSON_1]": "Alice", "[EMAIL_1]": "alice@example.com"}
    text = "[PERSON_1] wrote to [EMAIL_1] yesterday."
    result = anon.restore(text, mapping)
    assert result == "Alice wrote to alice@example.com yesterday."


def test_restore_is_noop_for_empty_map() -> None:
    """
    Given  an empty mapping
    When   restore() is called
    Then   text is returned unchanged
    """
    anon = _make_anonymizer()
    text = "No placeholders here."
    assert anon.restore(text, {}) == text


@pytest.mark.use_case("PII_Anonymization_Before_Correction")
def test_anonymize_restore_roundtrip() -> None:
    """
    Given  text anonymized with anonymize()
    When   restore() is called with the returned mapping
    Then   the original text is recovered
    """
    # "alice@test.com" starts at index 24 in the string below
    anon = _make_anonymizer([(24, 38, "alice@test.com", "PERSON")])
    original = "Please send the file to alice@test.com as soon as possible."
    anonymized, mapping = anon.anonymize(original)
    assert anonymized != original
    restored = anon.restore(anonymized, mapping)
    assert restored == original


# ── ImportError / OSError handling ────────────────────────────────────────


def test_anonymizer_raises_import_error_when_spacy_missing() -> None:
    """
    Given  spacy is not installed
    When   SpacyAnonymizer is instantiated
    Then   ImportError is raised with a helpful message
    """
    with patch.dict("sys.modules", {"spacy": None}):
        with pytest.raises(ImportError):
            SpacyAnonymizer("English")


def test_anonymizer_raises_os_error_when_model_missing() -> None:
    """
    Given  spacy is installed but the primary model is not downloaded
    When   SpacyAnonymizer is instantiated
    Then   OSError is raised
    """
    mock_spacy = MagicMock()
    mock_spacy.load.side_effect = OSError("model not found")

    with patch.dict(sys.modules, {"spacy": mock_spacy}):
        with pytest.raises(OSError, match="model not found"):
            SpacyAnonymizer("English")


def test_anonymizer_raises_os_error_when_secondary_model_missing() -> None:
    """
    Given  spacy is installed, the primary model loads fine, but the secondary model is absent
    When   SpacyAnonymizer is instantiated with a secondary_model configured
    Then   OSError is raised for the secondary model
    """
    mock_spacy = MagicMock()

    def _load(name: str) -> MagicMock:
        if name == "xx_ent_wiki_sm":
            raise OSError("secondary model not found")
        return MagicMock()

    mock_spacy.load.side_effect = _load

    with patch.dict(sys.modules, {"spacy": mock_spacy}):
        with pytest.raises(OSError, match="secondary model not found"):
            SpacyAnonymizer("English", AnonymizerConfig(secondary_model="xx_ent_wiki_sm"))


def test_anonymizer_uses_configured_primary_model() -> None:
    """
    Given  an AnonymizerConfig with primary_model set to "xx_ent_wiki_sm"
    When   SpacyAnonymizer is instantiated for "English"
    Then   spacy.load is called with "xx_ent_wiki_sm" rather than "en_core_web_sm"
    """
    mock_spacy = MagicMock()

    with patch.dict(sys.modules, {"spacy": mock_spacy}):
        SpacyAnonymizer("English", AnonymizerConfig(primary_model="xx_ent_wiki_sm"))

    mock_spacy.load.assert_called_once_with("xx_ent_wiki_sm")


def test_anonymizer_falls_back_to_language_model_when_primary_none() -> None:
    """
    Given  an AnonymizerConfig with primary_model=None
    When   SpacyAnonymizer is instantiated for "English"
    Then   spacy.load is called with the language-derived model "en_core_web_sm"
    """
    mock_spacy = MagicMock()

    with patch.dict(sys.modules, {"spacy": mock_spacy}):
        SpacyAnonymizer("English", AnonymizerConfig(primary_model=None))

    mock_spacy.load.assert_called_once_with("en_core_web_sm")


def test_anonymizer_raises_oserror_when_configured_primary_model_missing() -> None:
    """
    Given  an AnonymizerConfig with a specific primary_model that is not downloaded
    When   SpacyAnonymizer is instantiated
    Then   OSError is raised (the configured model, not the language-derived one, is attempted)
    """
    mock_spacy = MagicMock()
    mock_spacy.load.side_effect = OSError("configured model not found")

    with patch.dict(sys.modules, {"spacy": mock_spacy}):
        with pytest.raises(OSError, match="configured model not found"):
            SpacyAnonymizer("English", AnonymizerConfig(primary_model="custom_model_sm"))
