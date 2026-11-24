"""Which spaCy model recognises people's names in which language — a lookup table.

Which model belongs to which language is data, so both the anonymizer and the resolver of the
"extra detection model" setting can ask for it without loading spaCy (which may not even be
installed).

The version of each model is pinned next to its name, so the download builds its URL from here
instead of asking spaCy's compatibility table at run time: the model a teacher gets is the one
this file names, whatever spaCy release happens to be installed.  A unit test holds each pin's
major.minor to the locked spaCy's.

Two languages have no model: Spanish (none listed; `es_core_news_sm` is GPL-3.0, which the
license policy accepts) and Italian (`it_core_news_sm` is CC BY-NC-SA, non-commercial, which it
refuses).  They fall back to the multilingual model like any other language without one.
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
