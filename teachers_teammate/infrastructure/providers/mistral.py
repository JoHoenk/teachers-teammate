"""Mistral AI provider (global endpoint): create a ``ChatMistralAI`` BaseChatModel.

Mistral does not say where requests to this endpoint are processed.  To keep processing in
the EU use the ``mistral_eu`` provider, which uses the same key and models.

Installation
------------
pip install langchain-mistralai

Authentication
--------------
The key is passed in as ``api_key``; with none, LangChain reads the ``MISTRAL_API_KEY``
environment variable, so exporting it works as well:

    export MISTRAL_API_KEY=<your-key>
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.data_location import DataRegion
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.infrastructure.providers import _mistral_common as common
from teachers_teammate.infrastructure.providers._helpers import api_key_check

PROVIDER = ProviderInfo(
    name="mistral",
    display_name="Mistral",
    # The global endpoint does not say where requests are processed; use "mistral_eu".
    data_region=DataRegion.UNKNOWN,
    vendor_country="France",
    needs_api_key=True,
    # The vendor's page where a key is created; the Settings window links to it.
    key_url="https://console.mistral.ai/api-keys",
    env_key=common.ENV_KEY,
    can_list_models=True,
    package=AddonPackage(
        module="langchain_mistralai", pip="langchain-mistralai", version_spec=">=1.1"
    ),
    models=tuple(common.FALLBACK_MODELS),
)


def list_models_checked(*, api_key: str | None = None, **_kwargs: object) -> ModelListing:
    """Return the chat models this endpoint serves and whether the list is live."""
    return common.list_chat_models_checked(common.GLOBAL_BASE_URL, PROVIDER.models, api_key=api_key)


def list_models(*, api_key: str | None = None, **_kwargs: object) -> list[str]:
    """Return chat model IDs, or the static list when the endpoint cannot be asked."""
    return list_models_checked(api_key=api_key).models


def create(
    model: str,
    *,
    temperature: float,
    api_key: str | None = None,
    **_kwargs: object,
) -> BaseChatModel:
    """Return a ``ChatMistralAI`` instance for *model* using the global endpoint.

    Args:
        model:       Mistral model name (e.g. ``"mistral-small-latest"``).
        temperature: Sampling temperature (0.0 = deterministic).
        api_key:     The key the app holds; with none, LangChain reads ``$MISTRAL_API_KEY``.
    """
    return common.create_chat_model(model, temperature, common.GLOBAL_BASE_URL, api_key)


check_connection = api_key_check(PROVIDER.env_key)
