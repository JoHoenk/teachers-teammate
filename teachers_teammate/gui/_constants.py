"""Shared constants and small pure helpers for the GUI."""

from __future__ import annotations

_COLOUR_ERROR = "#c0392b"
_COLOUR_WARNING = "#e67e22"
_COLOUR_OK = "#27ae60"
_COLOUR_CACHED = "#1e8449"
_COLOUR_INFO = "#cccccc"
_COLOUR_OCR_BAR = "#3498db"
_COLOUR_CORRECTION_BAR = "#e67e22"
_COLOUR_EVAL_BAR = "#8e44ad"

# Common languages offered in the dropdown (user may type custom values)
_LANGUAGES = [
    "English",
    "German",
    "French",
    "Spanish",
    "Italian",
    "Portuguese",
    "Dutch",
]

# spaCy NER models offered for download, as (display label, package name) pairs.
# Shared by the addon installer and downloads dialogs.
_SPACY_MODEL_CHOICES: list[tuple[str, str]] = [
    ("English (en_core_web_sm)", "en_core_web_sm"),
    ("German (de_core_news_sm)", "de_core_news_sm"),
    ("French (fr_core_news_sm)", "fr_core_news_sm"),
    ("Spanish (es_core_news_sm)", "es_core_news_sm"),
    ("Italian (it_core_news_sm)", "it_core_news_sm"),
    ("Portuguese (pt_core_news_sm)", "pt_core_news_sm"),
    ("Dutch (nl_core_news_sm)", "nl_core_news_sm"),
    ("Multilingual (xx_ent_wiki_sm)", "xx_ent_wiki_sm"),
]
