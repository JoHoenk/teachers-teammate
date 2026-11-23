"""OpenAI provider: create a ``ChatOpenAI`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.data_location import DataRegion
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.infrastructure.providers._helpers import (
    api_key_check,
    checked_listing,
    import_guard,
)

PROVIDER = ProviderInfo(
    name="openai",
    display_name="OpenAI (ChatGPT)",
    data_region=DataRegion.NON_EU,
    vendor_country="USA",
    needs_api_key=True,
    # The vendor's page where a key is created; the Settings window links to it.
    key_url="https://platform.openai.com/api-keys",
    env_key="OPENAI_API_KEY",
    can_list_models=True,
    strict_json_schema=True,
    package=AddonPackage(module="langchain_openai", pip="langchain-openai", version_spec=">=0.2"),
    models=("gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"),
)

_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def _fetch_chat_ids(api_key: str) -> list[str]:
    # optional dep; lazy import to avoid startup ImportError when not installed
    from openai import OpenAI  # noqa: PLC0415

    all_ids = [m.id for m in OpenAI(api_key=api_key).models.list()]
    return sorted(m for m in all_ids if any(m.startswith(p) for p in _CHAT_PREFIXES))


def list_models_checked(*, api_key: str | None = None, **_kwargs: object) -> ModelListing:
    """Return chat-capable model IDs from the OpenAI API and whether the list is live."""
    return checked_listing(
        _fetch_chat_ids,
        PROVIDER.models,
        env_key=PROVIDER.env_key,
        service="OpenAI",
        api_key=api_key,
    )


def list_models(*, api_key: str | None = None, **_kwargs: object) -> list[str]:
    """Return chat-capable model IDs, or the static list when OpenAI cannot be asked."""
    return list_models_checked(api_key=api_key).models


def create(
    model: str,
    *,
    temperature: float = 0.7,
    api_key: str | None = None,
    **_kwargs: object,
) -> BaseChatModel:
    """Return a ``ChatOpenAI`` instance for *model*.

    The key comes in as *api_key*; with none, LangChain reads ``$OPENAI_API_KEY`` itself.
    """
    with import_guard("langchain-openai"):
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
    return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)


check_connection = api_key_check(PROVIDER.env_key)
