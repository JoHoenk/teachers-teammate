"""spaCy model names and pins per language — without importing spaCy.

Which model belongs to which language is a lookup table, so both the anonymizer and the
resolver of the "extra detection model" setting can ask for it without loading spaCy (which
may not even be installed).

The version of each model is pinned next to its name, so the download builds its URL from here
instead of asking spaCy's compatibility table at run time: the model a teacher gets is the one
this file names, whatever spaCy release happens to be installed.  A unit test holds each pin's
major.minor to the locked spaCy's.

Two languages have no model: Spanish (`es_core_news_sm` is GPL-3.0) and Italian
(`it_core_news_sm` is CC BY-NC-SA, non-commercial).  Both are refused by the license policy, so
they fall back to the multilingual model like any other language without one.
"""

from __future__ import annotations

#: Language name (lower case) to the spaCy model that recognises people's names in it.
SPACY_MODELS: dict[str, str] = {
    "english": "en_core_web_sm",
    "german": "de_core_news_sm",
    "french": "fr_core_news_sm",
    "portuguese": "pt_core_news_sm",
    "dutch": "nl_core_news_sm",
}

#: Model used for a language that has no model of its own.
FALLBACK_SPACY_MODEL = "xx_ent_wiki_sm"

#: The version each model is pinned to, read from spaCy's compatibility table for the locked
#: spaCy (3.8).  A model's major.minor must equal the locked spaCy's.
SPACY_MODEL_PINS: dict[str, str] = {
    "en_core_web_sm": "3.8.0",
    "de_core_news_sm": "3.8.0",
    "fr_core_news_sm": "3.8.0",
    "pt_core_news_sm": "3.8.0",
    "nl_core_news_sm": "3.8.0",
    "xx_ent_wiki_sm": "3.8.0",
}

#: Where spaCy publishes its model wheels: one GitHub release per model and version.
SPACY_MODEL_DOWNLOAD_URL = "https://github.com/explosion/spacy-models/releases/download"


def spacy_model_for(language: str) -> str:
    """Return the spaCy model for *language*, falling back to the multilingual model."""
    return SPACY_MODELS.get(language.strip().lower(), FALLBACK_SPACY_MODEL)


def spacy_model_url(model: str) -> str:
    """Return the wheel URL of *model* at its pinned version.

    Raises:
        KeyError: if *model* has no pin — a model we do not offer cannot be downloaded.
    """
    version = SPACY_MODEL_PINS[model]
    filename = f"{model}-{version}-py3-none-any.whl"
    return f"{SPACY_MODEL_DOWNLOAD_URL}/{model}-{version}/{filename}"


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
