"""Ollama provider: create a ``ChatOllama`` BaseChatModel."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from teachers_teammate.domain.data_location import DataRegion
from teachers_teammate.domain.ollama_discovery import DEFAULT_SERVER_URL
from teachers_teammate.domain.providers import AddonPackage, ProviderInfo
from teachers_teammate.exceptions import ProviderNotAvailableError

# Metadata consumed by the GUI (and any other caller of llm_factory.get_provider_info).
# ``models`` is intentionally empty: Ollama's model list is fetched live via the API.
PROVIDER = ProviderInfo(
    name="ollama",
    display_name="Ollama",
    # Runs on the server at base_url (usually this computer), not at a vendor.
    self_hosted=True,
    # Cloud models run at Ollama, which does not declare where; local ones never leave.
    data_region=DataRegion.UNKNOWN,
    needs_base_url=True,
    can_list_models=True,
    # The app talks to Ollama directly; the LangChain connector is an optional extra.
    package=AddonPackage(module="langchain_ollama", pip="langchain-ollama", minimum=">=0.2"),
    package_optional=True,
)


def list_models(*, base_url: str = DEFAULT_SERVER_URL, **_kwargs: object) -> list[str]:
    """Return model names from the Ollama server, or ``[]`` when unreachable."""
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    return OllamaClient(base_url).list_models()


def check_connection(*, base_url: str = DEFAULT_SERVER_URL, **_kwargs: object) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the Ollama server at *base_url*."""
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    _connected, _model_ok, message = OllamaClient(base_url).check_connection()
    return _connected, message


def create(
    model: str, *, base_url: str = DEFAULT_SERVER_URL, temperature: float = 0.7, **_kwargs: object
) -> BaseChatModel:
    """Return a ``ChatOllama`` instance for *model*.

    Args:
        model:       Ollama model name (e.g. ``"hf.co/unsloth/gpt-oss-20b-GGUF:UD-Q4_K_XL"``).
        base_url:    Ollama server base URL.
        temperature: Sampling temperature (0.0 = deterministic).
    """
    try:
        # optional dep; lazy import to avoid startup ImportError when not installed
        from langchain_ollama import ChatOllama  # noqa: PLC0415

        from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
            normalize_ollama_url,
        )
    except ImportError as exc:
        raise ProviderNotAvailableError(
            "langchain-ollama is not installed. Run: pip install langchain-ollama"
        ) from exc
    return ChatOllama(model=model, base_url=normalize_ollama_url(base_url), temperature=temperature)
