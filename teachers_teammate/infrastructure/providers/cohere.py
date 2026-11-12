"""Cohere provider: create a ``ChatCohere`` BaseChatModel.

Installation
------------
pip install langchain-cohere

Authentication
--------------
Set the ``COHERE_API_KEY`` environment variable to your Cohere API key:

    export COHERE_API_KEY=<your-key>
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER_INFO: dict = {
    "display_name": "Cohere",
    "data_region": "non_eu",
    "vendor_country": "Canada",
    "needs_api_key": True,
    # The vendor's page where a key is created; the Settings window links to it.
    "key_url": "https://dashboard.cohere.com/api-keys",
    "env_key": "COHERE_API_KEY",
    "needs_base_url": False,
    "can_list_models": False,
    "package": {"module": "langchain_cohere", "pip": "langchain-cohere"},
    "default_model": "command-r-plus",
    "models": [
        "command-r-plus",
        "command-r",
        "command",
        "command-light",
        "command-nightly",
    ],
}


def list_models(**_kwargs: object) -> list[str]:
    """Return the static Cohere model list."""
    return list(PROVIDER_INFO["models"])


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatCohere`` instance for *model*.

    The API key is read from the ``$COHERE_API_KEY`` environment variable
    by LangChain automatically.

    Args:
        model:       Cohere model name (e.g. ``"command-r-plus"``).
        temperature: Sampling temperature (0.0 = deterministic).
    """
    with import_guard("langchain-cohere"):
        from langchain_cohere import ChatCohere  # noqa: PLC0415
    return ChatCohere(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
