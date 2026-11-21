"""The installation check: what of the Add-ons page is already on this computer.

The check asks the service once, on a background thread, and answers in one mapping keyed
like the tabs key their rows (``spacy``, ``provider:<name>``, ``spacy:<model>``,
``reader:…``, ``page_models:…``, ``ollama:<tag>``, plus ``size:<tag>`` for the tree's Size
column).  The page hands that mapping to every tab.
"""

from __future__ import annotations

from ....application.composition import AppServices
from ..._constants import _SPACY_MODEL_CHOICES as _SPACY_MODEL_DOWNLOAD_CHOICES


def detect_installed(svc: AppServices, ollama_url: str, models: list[str]) -> dict[str, object]:
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
    result: dict[str, object] = dict(status)
    result["spacy"] = svc.addons.is_module_importable("spacy")
    # Add model size information so the UI can show a Size column.
    for model, size_bytes in svc.ollama.list_ollama_model_sizes(ollama_url).items():
        result[f"size:{model}"] = size_bytes
    # Classify models here (the result is cached by the service) so the GUI thread
    # never blocks on show() requests; the dialog reads the cache afterwards.
    svc.recommendations.classify_ollama_models(list(models), ollama_url)
    return result
