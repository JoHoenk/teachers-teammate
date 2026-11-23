"""The installation check: what of the Add-ons page is already on this computer.

The check asks the service once, on a background thread, and answers with the items of the
vocabulary (:mod:`teachers_teammate.domain.install_item`) and, for the local-models tree, the
size of every model the server lists.  The page hands the answer to every tab.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ....application.composition import AppServices
from ....domain.install_item import SPACY_ITEM, InstallItem
from ..._constants import _SPACY_MODEL_CHOICES as _SPACY_MODEL_DOWNLOAD_CHOICES


@dataclass(frozen=True)
class DetectedAddons:
    """What the check found: which items are installed, and how big the listed models are."""

    installed: dict[InstallItem, bool] = field(default_factory=dict)
    model_sizes: dict[str, int] = field(default_factory=dict)
    """Downloaded bytes per Ollama model, for the tree's Size column."""


def detect_installed(svc: AppServices, ollama_url: str, models: list[str]) -> DetectedAddons:
    """Probe every downloadable item and return what is installed (background task)."""
    spacy_models = [model_id for _, model_id in _SPACY_MODEL_DOWNLOAD_CHOICES]
    status = svc.addons.get_installation_status(
        provider_modules={
            name: module for name, (module, _pip) in svc.providers.list_provider_packages().items()
        },
        spacy_models=spacy_models,
        ollama_models=list(models),
        ollama_url=ollama_url,
    )
    status[SPACY_ITEM] = svc.addons.is_module_importable("spacy")
    # Classify models here (the result is cached by the service) so the GUI thread
    # never blocks on show() requests; the dialog reads the cache afterwards.
    svc.recommendations.classify_ollama_models(list(models), ollama_url)
    return DetectedAddons(
        installed=status, model_sizes=svc.ollama.list_ollama_model_sizes(ollama_url)
    )
