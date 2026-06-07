"""OpenAI provider: create a ``ChatOpenAI`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER_INFO: dict = {
    "needs_api_key": True,
    "env_key": "OPENAI_API_KEY",
    "needs_base_url": False,
    "can_list_models": True,
    "default_model": "gpt-4o-mini",
    "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo"],
}

_CHAT_PREFIXES = ("gpt-", "o1", "o3", "o4", "chatgpt-")


def list_models(**_kwargs: object) -> list[str]:
    """Return chat-capable model IDs from the OpenAI API, or fall back to the static list."""
    try:
        # optional dep; lazy import to avoid startup ImportError when not installed
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI()
        all_ids = [m.id for m in client.models.list()]
        chat_ids = sorted(m for m in all_ids if any(m.startswith(p) for p in _CHAT_PREFIXES))
        return chat_ids if chat_ids else list(PROVIDER_INFO["models"])
    except Exception:  # noqa: BLE001  # OpenAI SDK may raise auth or network errors; return static fallback list
        return list(PROVIDER_INFO["models"])


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatOpenAI`` instance for *model*.

    The API key is read from the ``$OPENAI_API_KEY`` environment variable
    by LangChain automatically.
    """
    with import_guard("langchain-openai"):
        from langchain_openai import ChatOpenAI  # noqa: PLC0415
    return ChatOpenAI(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
