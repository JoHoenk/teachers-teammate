"""Google Gemini provider: create a ``ChatGoogleGenerativeAI`` BaseChatModel.

Installation
------------
pip install langchain-google-genai

Authentication
--------------
The key is passed in as ``api_key``; with none, LangChain reads the ``GOOGLE_API_KEY``
environment variable, so exporting it works as well:

    export GOOGLE_API_KEY=<your-key>

Alternatively, set ``GOOGLE_APPLICATION_CREDENTIALS`` for Vertex AI service-
account authentication — the LangChain wrapper handles both automatically.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
import urllib.parse
import urllib.request

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

_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_LIST_TIMEOUT_S = 10.0
_MAX_PAGES = 5  # 1000 models a page: far more than Google offers; bounds a misbehaving answer

PROVIDER = ProviderInfo(
    name="google",
    display_name="Google (Gemini)",
    data_region=DataRegion.NON_EU,
    vendor_country="USA",
    needs_api_key=True,  # key is read from $GOOGLE_API_KEY by LangChain
    # The vendor's page where a key is created; the Settings window links to it.
    key_url="https://aistudio.google.com/apikey",
    env_key="GOOGLE_API_KEY",
    can_list_models=True,
    package=AddonPackage(
        module="langchain_google_genai", pip="langchain-google-genai", version_spec=">=2.0"
    ),
    models=(
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.0-flash",
        "gemini-1.5-pro",
        "gemini-1.5-flash",
    ),
)


def _fetch_gemini_names(api_key: str) -> list[str]:
    """Ask Google for the Gemini models that can generate text (all pages, sorted).

    A plain HTTP request: the LangChain package has no "list models" call, and the SDK it
    installs differs from version to version (``google-generativeai``, then
    ``google-ai-generativelanguage``, now ``google-genai``), so none of them can be relied on.
    The key travels in a header, never in the URL.
    """
    names: set[str] = set()
    page_token = ""
    for _ in range(_MAX_PAGES):
        query = {"pageSize": "1000", **({"pageToken": page_token} if page_token else {})}
        request = urllib.request.Request(
            f"{_MODELS_URL}?{urllib.parse.urlencode(query)}",
            headers={"x-goog-api-key": api_key, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=_LIST_TIMEOUT_S) as response:
            page = json.load(response)
        for entry in page.get("models", []):
            name = str(entry["name"]).removeprefix("models/")
            if "gemini" in name and "generateContent" in entry.get(
                "supportedGenerationMethods", []
            ):
                names.add(name)
        page_token = str(page.get("nextPageToken") or "")
        if not page_token:
            break
    return sorted(names)


def list_models_checked(*, api_key: str | None = None, **_kwargs: object) -> ModelListing:
    """Return Gemini model names from the Google API and whether the list is live."""
    return checked_listing(
        _fetch_gemini_names,
        PROVIDER.models,
        env_key=PROVIDER.env_key,
        service="Google",
        api_key=api_key,
    )


def list_models(*, api_key: str | None = None, **_kwargs: object) -> list[str]:
    """Return Gemini model names, or the static list when Google cannot be asked."""
    return list_models_checked(api_key=api_key).models


def create(
    model: str,
    *,
    temperature: float = 0.7,
    api_key: str | None = None,
    **_kwargs: object,
) -> BaseChatModel:
    """Return a ``ChatGoogleGenerativeAI`` instance for *model*.

    The key comes in as *api_key*; with none, LangChain reads ``$GOOGLE_API_KEY`` itself.

    Args:
        model:       Gemini model name (e.g. ``"gemini-2.0-flash"``).
        temperature: Sampling temperature (0.0 = deterministic).
    """
    with import_guard("langchain-google-genai"):
        from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: PLC0415
    return ChatGoogleGenerativeAI(model=model, temperature=temperature, google_api_key=api_key)


check_connection = api_key_check(PROVIDER.env_key)
