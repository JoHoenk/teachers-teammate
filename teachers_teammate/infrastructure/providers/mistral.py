"""Mistral AI provider (global endpoint): create a ``ChatMistralAI`` BaseChatModel.

Mistral does not say where requests to this endpoint are processed.  To keep processing in
the EU use the ``mistral_eu`` provider, which uses the same key and models.

Installation
------------
pip install langchain-mistralai

Authentication
--------------
Set the ``MISTRAL_API_KEY`` environment variable to your Mistral API key:

    export MISTRAL_API_KEY=<your-key>
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.infrastructure.providers import _mistral_common as common
from teachers_teammate.infrastructure.providers._helpers import api_key_check

PROVIDER_INFO: dict = {
    "display_name": "Mistral",
    # The global endpoint does not say where requests are processed; use "mistral_eu".
    "data_region": "unknown",
    "vendor_country": "France",
    "needs_api_key": True,
    "env_key": common.ENV_KEY,
    "needs_base_url": False,
    "can_list_models": True,
    "package": {"module": "langchain_mistralai", "pip": "langchain-mistralai"},
    "default_model": common.DEFAULT_MODEL,
    "models": list(common.FALLBACK_MODELS),
}


def list_models_checked(**_kwargs: object) -> ModelListing:
    """Return the chat models this endpoint serves and whether the list is live."""
    return common.list_chat_models_checked(common.GLOBAL_BASE_URL, PROVIDER_INFO["models"])


def list_models(**kwargs: object) -> list[str]:
    """Return chat model IDs, or the static list when the endpoint cannot be asked."""
    return list_models_checked(**kwargs).models


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatMistralAI`` instance for *model* using the global endpoint.

    Args:
        model:       Mistral model name (e.g. ``"mistral-small-latest"``).
        temperature: Sampling temperature (0.0 = deterministic).
    """
    return common.create_chat_model(model, temperature, common.GLOBAL_BASE_URL)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
