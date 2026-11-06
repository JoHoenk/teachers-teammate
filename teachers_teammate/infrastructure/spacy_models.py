"""spaCy model names per language — without importing spaCy.

Which model belongs to which language is a lookup table, so both the anonymizer and the
resolver of the "extra detection model" setting can ask for it without loading spaCy (which
may not even be installed).
"""

from __future__ import annotations

#: Language name (lower case) to the spaCy model that recognises people's names in it.
SPACY_MODELS: dict[str, str] = {
    "english": "en_core_web_sm",
    "german": "de_core_news_sm",
    "french": "fr_core_news_sm",
    "spanish": "es_core_news_sm",
    "italian": "it_core_news_sm",
    "portuguese": "pt_core_news_sm",
    "dutch": "nl_core_news_sm",
}

#: Model used for a language that has no model of its own.
FALLBACK_SPACY_MODEL = "xx_ent_wiki_sm"


def spacy_model_for(language: str) -> str:
    """Return the spaCy model for *language*, falling back to the multilingual model."""
    return SPACY_MODELS.get(language.strip().lower(), FALLBACK_SPACY_MODEL)


def installed_spacy_models() -> list[str]:
    """Return the sorted names of the installed spaCy model packages.

    Empty when spaCy (or its model registry) is unavailable, so callers can treat a missing
    privacy add-on the same as a missing model.
    """
    try:
        import spacy.util  # noqa: PLC0415

        return sorted(spacy.util.get_installed_models())
    except Exception:  # noqa: BLE001  # spaCy may be absent or its model registry may fail
        return []
