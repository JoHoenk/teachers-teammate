"""Google Gemini provider: create a ``ChatGoogleGenerativeAI`` BaseChatModel.

Installation
------------
pip install langchain-google-genai

Authentication
--------------
Set the ``GOOGLE_API_KEY`` environment variable to your Google AI Studio key:

    export GOOGLE_API_KEY=<your-key>

Alternatively, set ``GOOGLE_APPLICATION_CREDENTIALS`` for Vertex AI service-
account authentication — the LangChain wrapper handles both automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.infrastructure.providers._helpers import api_key_check, import_guard

PROVIDER_INFO: dict = {
    "needs_api_key": True,  # key is read from $GOOGLE_API_KEY by LangChain
    "env_key": "GOOGLE_API_KEY",
    "needs_base_url": False,
    "can_list_models": True,
    "default_model": "gemini-2.0-flash",
    "models": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ],
}


def list_models(**_kwargs: object) -> list[str]:
    """Return Gemini model names from the Google API, or fall back to the static list."""
    try:
        import google.generativeai as genai  # noqa: PLC0415  # pylint: disable=no-name-in-module  # google-generativeai uses a namespace package

        result = [
            m.name.removeprefix("models/")
            for m in genai.list_models()
            if "generateContent" in m.supported_generation_methods and "gemini" in m.name
        ]
        return sorted(result) if result else list(PROVIDER_INFO["models"])
    except Exception:  # noqa: BLE001  # google-generativeai may raise auth or network errors; return static fallback list
        return list(PROVIDER_INFO["models"])


def create(model: str, *, temperature: float = 0.7, **_kwargs: object) -> BaseChatModel:
    """Return a ``ChatGoogleGenerativeAI`` instance for *model*.

    The API key is read from the ``$GOOGLE_API_KEY`` environment variable
    by LangChain automatically.

    Args:
        model:       Gemini model name (e.g. ``"gemini-2.0-flash"``).
        temperature: Sampling temperature (0.0 = deterministic).
    """
    with import_guard("langchain-google-genai"):
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415
    return ChatGoogleGenerativeAI(model=model, temperature=temperature)


check_connection = api_key_check(PROVIDER_INFO["env_key"])
