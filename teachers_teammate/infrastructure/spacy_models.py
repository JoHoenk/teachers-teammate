"""spaCy model names and pins per language — without importing spaCy.

The tables live in :mod:`teachers_teammate.domain.name_models`; this module adds what needs the
import system: the wheel URL of a pinned model, and which of them are installed.
"""

from __future__ import annotations

import importlib
import importlib.util
from typing import TYPE_CHECKING

from ..domain.name_models import FALLBACK_SPACY_MODEL as FALLBACK_SPACY_MODEL  # noqa: PLC0414
from ..domain.name_models import SPACY_MODEL_DOWNLOAD_URL, SPACY_MODEL_PINS
from ..domain.name_models import SPACY_MODELS as SPACY_MODELS  # noqa: PLC0414
from ..domain.name_models import spacy_model_for as spacy_model_for  # noqa: PLC0414

if TYPE_CHECKING:
    from ..domain.anonymization import AnonymizerConfig
    from ..interfaces import Anonymizer


def spacy_model_url(model: str) -> str:
    """Return the wheel URL of *model* at its pinned version.

    Raises:
        KeyError: if *model* has no pin — a model we do not offer cannot be downloaded.
    """
    version = SPACY_MODEL_PINS[model]
    filename = f"{model}-{version}-py3-none-any.whl"
    return f"{SPACY_MODEL_DOWNLOAD_URL}/{model}-{version}/{filename}"


def _importable(module: str) -> bool:
    """Return whether *module* can be found, without importing it."""
    try:
        return importlib.util.find_spec(module) is not None
    except ImportError, ValueError:  # a broken or half-removed package
        return False


def installed_spacy_models() -> list[str]:
    """Return the sorted names of the spaCy models of this file that are installed.

    Asked of Python's import system, which re-reads a folder that changed (and everything after
    ``importlib.invalidate_caches()``, which the add-on code calls after an install).  spaCy's own
    list (``spacy.util.get_installed_models``) is no use: it comes from a snapshot taken when spaCy
    is first imported, so a model downloaded while the app runs would not be in it.  Nothing is
    imported.  Empty when spaCy is not installed, so callers can treat a missing privacy add-on
    the same as a missing model.
    """
    if not _importable("spacy"):
        return []
    return sorted(model for model in SPACY_MODEL_PINS if _importable(model))


class SpacyModels:
    """The spaCy models name detection loads, asked through this module.

    Each method looks its function up by name when it is called, so a test that patches one of
    them changes what this object answers — and a model downloaded while the app runs is seen
    without a restart (:func:`installed_spacy_models` asks the import system).
    """

    def installed(self) -> list[str]:
        """Return the sorted names of the installed spaCy models, or ``[]`` without spaCy."""
        return installed_spacy_models()

    def anonymizer(self, language: str, config: AnonymizerConfig | None = None) -> Anonymizer:
        """Return an anonymizer for *language*, loaded from the models *config* names.

        Raises:
            ImportError: spaCy is not installed.
            OSError: a model the settings name is not downloaded.
        """
        from .anonymizer import SpacyAnonymizer  # noqa: PLC0415  # spaCy is an add-on

        return SpacyAnonymizer(language, config)
