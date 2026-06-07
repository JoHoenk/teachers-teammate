"""Mistral AI provider: create a ``ChatMistralAI`` BaseChatModel.

Installation
------------
pip install langchain-mistralai

Authentication
--------------
Set the ``MISTRAL_API_KEY`` environment variable to your Mistral API key:

    export MISTRAL_API_KEY=<your-key>
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER_INFO: dict = {
    "needs_api_key": True,
    "env_key": "MISTRAL_API_KEY",
    "needs_base_url": False,
    "can_list_models": True,
    "default_model": "mistral-small-latest",
    "models": [
        "mistral-large-latest",
        "mistral-medium-latest",
        "mistral-small-latest",
        "open-mistral-nemo",
        "pixtral-12b-2409",
    ],
}


def list_models(**_kwargs: object) -> list[str]:
    """Return available Mistral model IDs from the API, or fall back to the static list."""
    try:
        # optional dep; imported lazily to avoid startup ImportError when not installed
        from mistralai import Mistral  # noqa: PLC0415

        client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", ""))
        result = client.models.list()
        ids = sorted(m.id for m in result.data)
        return ids if ids else list(PROVIDER_INFO["models"])
    except Exception:  # noqa: BLE001  # Mistral SDK may raise auth or network errors; return static fallback list
        return list(PROVIDER_INFO["models"])


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatMistralAI`` instance for *model*.

    The API key is read from the ``$MISTRAL_API_KEY`` environment variable
    by LangChain automatically.

    Args:
        model:       Mistral model name (e.g. ``"mistral-small-latest"``).
        temperature: Sampling temperature (0.0 = deterministic).
    """
    with import_guard("langchain-mistralai"):
        from langchain_mistralai import ChatMistralAI  # noqa: PLC0415
    return ChatMistralAI(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
