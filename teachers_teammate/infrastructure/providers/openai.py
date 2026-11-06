"""OpenAI provider: create a ``ChatOpenAI`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.infrastructure.providers._helpers import (
    api_key_check,
    checked_listing,
    import_guard,
)

PROVIDER_INFO: dict = {
    "display_name": "OpenAI (ChatGPT)",
    "needs_api_key": True,
    "env_key": "OPENAI_API_KEY",
    "needs_base_url": False,
    "can_list_models": True,
    "package": {"module": "langchain_openai", "pip": "langchain-openai"},
    "default_model": "gpt-4o-mini",
    "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
}

_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def _fetch_chat_ids() -> list[str]:
    # optional dep; lazy import to avoid startup ImportError when not installed
    from openai import OpenAI  # noqa: PLC0415

    all_ids = [m.id for m in OpenAI().models.list()]
    return sorted(m for m in all_ids if any(m.startswith(p) for p in _CHAT_PREFIXES))


def list_models_checked(**_kwargs: object) -> ModelListing:
    """Return chat-capable model IDs from the OpenAI API and whether the list is live."""
    return checked_listing(
        _fetch_chat_ids, PROVIDER_INFO["models"], env_key=PROVIDER_INFO["env_key"], service="OpenAI"
    )


def list_models(**kwargs: object) -> list[str]:
    """Return chat-capable model IDs, or the static list when OpenAI cannot be asked."""
    return list_models_checked(**kwargs).models


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatOpenAI`` instance for *model*.

    The API key is read from the ``$OPENAI_API_KEY`` environment variable
    by LangChain automatically.
    """
    with import_guard("langchain-openai"):
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
    return ChatOpenAI(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
