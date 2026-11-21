"""Shared constants and small pure helpers for the GUI."""

from __future__ import annotations

from ..domain.language import LANGUAGES
from ..domain.name_models import FALLBACK_SPACY_MODEL, SPACY_MODELS

_COLOUR_ERROR = "#c0392b"
_COLOUR_WARNING = "#e67e22"
_COLOUR_OK = "#27ae60"
_COLOUR_CACHED = "#1e8449"
_COLOUR_INFO = "#cccccc"
_COLOUR_OCR_BAR = "#3498db"
_COLOUR_CORRECTION_BAR = "#e67e22"
_COLOUR_EVAL_BAR = "#8e44ad"

# Common languages offered in the dropdown (user may type custom values).  The list itself
# lives in the domain so the CLI and the system-locale mapping use exactly the same names.
_LANGUAGES = LANGUAGES

# spaCy NER models offered for download, as (display label, package name) pairs.
# Shared by the addon installer and downloads dialogs.  Built from the one table in
# `infrastructure/spacy_models.py`, so a language that leaves it leaves this list too.
_SPACY_MODEL_CHOICES: list[tuple[str, str]] = [
    (f"{language.title()} ({model})", model) for language, model in SPACY_MODELS.items()
] + [(f"Multilingual ({FALLBACK_SPACY_MODEL})", FALLBACK_SPACY_MODEL)]
