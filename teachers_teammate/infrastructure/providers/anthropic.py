"""Anthropic provider: create a ``ChatAnthropic`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER_INFO: dict = {
    "needs_api_key": True,
    "env_key": "ANTHROPIC_API_KEY",
    "needs_base_url": False,
    "can_list_models": False,
    "default_model": "claude-3-haiku-20240307",
    "models": [
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-3-5-sonnet-20241022",
        "claude-3-opus-20240229",
        "claude-3-haiku-20240307",
    ],
}


def list_models(**_kwargs: object) -> list[str]:
    """Return the static Anthropic model list (no public listing API available)."""
    return list(PROVIDER_INFO["models"])


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatAnthropic`` instance for *model*.

    The API key is read from the ``$ANTHROPIC_API_KEY`` environment variable
    by LangChain automatically.
    """
    with import_guard("langchain-anthropic"):
        from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    return ChatAnthropic(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
