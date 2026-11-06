"""Code shared by the Mistral providers (global and EU).

Both providers talk to the same API with the same key; only the base URL differs, and
the base URL decides *where the text is processed*.  The URL is therefore always passed
explicitly (never left to ``MISTRAL_BASE_URL``), so a provider's name in the app always
matches where its requests really go.

LangChain has no "list models" call, so the model query is a plain HTTP request here.
The standard library is enough for that, which keeps the Mistral SDK (and any extra
HTTP dependency) out of the install.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any
import urllib.error
import urllib.request

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.infrastructure.providers._helpers import import_guard

ENV_KEY = "MISTRAL_API_KEY"

#: Global endpoint: Mistral makes no promise about where requests are processed.
GLOBAL_BASE_URL = "https://api.mistral.ai/v1"

#: EU endpoint: processed in EU/EFTA data centres (billed at a surcharge by Mistral).
EU_BASE_URL = "https://api.eu.mistral.ai/v1"

DEFAULT_MODEL = "mistral-small-latest"

#: Used when the live query is unavailable (no key, offline, auth error).
FALLBACK_MODELS: list[str] = [
    "mistral-large-latest",
    "mistral-medium-latest",
    "mistral-small-latest",
    "open-mistral-nemo",
]

#: Fixed seed so the same text and settings give the same correction when comparing runs.
RANDOM_SEED = 42

_LIST_TIMEOUT_S = 10


def _is_chat_model(entry: dict[str, Any]) -> bool:
    """Return whether an API model entry can be used for chat (not embeddings, moderation…)."""
    if entry.get("archived"):
        return False
    capabilities = entry.get("capabilities")
    if isinstance(capabilities, dict) and "completion_chat" in capabilities:
        return bool(capabilities["completion_chat"])
    return True


def list_chat_models_checked(base_url: str, fallback: list[str]) -> ModelListing:
    """Return the chat-capable model IDs served at *base_url*, saying whether they are live.

    Asking the endpoint the provider will use means the EU provider only lists models the
    EU endpoint actually serves.  Any problem gives the *fallback* list with
    ``source="failed"`` and the reason, never a list that looks like the service's answer.
    """
    api_key = os.environ.get(ENV_KEY, "").strip()
    if not api_key:
        return ModelListing(list(fallback), "failed", "no Mistral key is set")
    request = urllib.request.Request(
        f"{base_url}/models",
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_LIST_TIMEOUT_S) as response:
            entries = json.load(response).get("data", [])
        ids = sorted({str(e["id"]) for e in entries if "id" in e and _is_chat_model(e)})
    except urllib.error.HTTPError as exc:  # an OSError too, but its status says why
        if exc.code in (401, 403):
            problem = "Mistral did not accept the key"
        else:
            problem = f"Mistral answered with error {exc.code}"
        return ModelListing(list(fallback), "failed", problem)
    except OSError:  # URLError, timeout: no connection
        return ModelListing(list(fallback), "failed", "no connection to Mistral")
    except (ValueError, AttributeError, TypeError, KeyError):
        return ModelListing(list(fallback), "failed", "Mistral answered unexpectedly")
    if not ids:
        return ModelListing(list(fallback), "failed", "Mistral returned no chat models")
    return ModelListing(ids, "live")


def create_chat_model(model: str, temperature: float, base_url: str) -> BaseChatModel:
    """Return a ``ChatMistralAI`` for *model* talking to *base_url*.

    The API key is read from ``$MISTRAL_API_KEY`` by LangChain.  Retries (5) and the
    request timeout (120 s) are LangChain's defaults.
    """
    with import_guard("langchain-mistralai"):
        from langchain_mistralai import ChatMistralAI  # noqa: PLC0415
    return ChatMistralAI(
        model=model,
        temperature=temperature,
        endpoint=base_url,
        random_seed=RANDOM_SEED,
    )
