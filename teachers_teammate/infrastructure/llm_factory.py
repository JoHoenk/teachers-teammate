"""LLM factory: discover and invoke provider modules to build a BaseChatModel.

Provider modules live in :mod:`teachers_teammate.infrastructure.providers` — one
file per integration.  Each module exposes a single
``create(model, **kwargs) -> BaseChatModel`` function.  The *provider* string
maps directly to the module name:

    ``"ollama"``    → :mod:`teachers_teammate.infrastructure.providers.ollama`
    ``"openai"``    → :mod:`teachers_teammate.infrastructure.providers.openai`
    ``"anthropic"`` → :mod:`teachers_teammate.infrastructure.providers.anthropic`

Adding a new provider means dropping a new file into
``teachers_teammate/infrastructure/providers/`` — no changes to this module are
required.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from .._model_defaults import default_model_for_task as default_model_for_task  # noqa: PLC0414
from ..domain.model_listing import ModelListing
from ..exceptions import ProviderNotAvailableError
from ..interfaces import LLMProviderModule

_logger = logging.getLogger(__name__)

# Providers are discovered at runtime from the
# teachers_teammate/infrastructure/providers/ directory. Drop a new <name>.py
# file there and it is immediately available — no changes to this module required.


def _providers_dir() -> Path:
    return Path(__file__).parent / "providers"


def list_providers() -> list[str]:
    """Return sorted names of all available providers (one .py file each).

    Modules whose name starts with ``_`` (``__init__``, ``_helpers``, …) are
    private support modules, not providers, and are excluded.
    """
    return sorted(p.stem for p in _providers_dir().glob("*.py") if not p.stem.startswith("_"))


def get_provider_info(provider: str) -> dict:
    """Return the ``PROVIDER_INFO`` metadata dict for *provider*, or ``{}``.

    The module is imported lazily; heavy LangChain dependencies inside each
    provider's ``create()`` function are **not** triggered here.
    """
    try:
        mod = importlib.import_module(f".providers.{provider}", package=__package__)
        return getattr(mod, "PROVIDER_INFO", {})
    except ImportError:
        return {}


def _load_provider(provider: str) -> LLMProviderModule:
    """Import and return ``teachers_teammate.infrastructure.providers.<provider>``, or raise."""
    available = list_providers()
    if provider not in available:
        raise ValueError(
            f"Unknown correction provider '{provider}'. Available: {', '.join(available)}."
        )
    mod = importlib.import_module(f".providers.{provider}", package=__package__)
    return cast(LLMProviderModule, mod)


def list_provider_models(provider: str, **kwargs: object) -> list[str]:
    """Return available model names for *provider*.

    Calls the provider module's ``list_models(**kwargs)`` when it exists,
    otherwise falls back to the ``models`` list in ``PROVIDER_INFO``.

    Args:
        provider: Provider name — must match a module in :mod:`teachers_teammate.infrastructure.providers`.
        **kwargs: Forwarded to the provider's ``list_models()`` (e.g. ``base_url`` for Ollama).

    Returns:
        List of model name strings; empty list on import error.
    """
    try:
        mod = importlib.import_module(f".providers.{provider}", package=__package__)
        fn = getattr(mod, "list_models", None)
        if fn is not None:
            return fn(**kwargs)
        info = getattr(mod, "PROVIDER_INFO", {})
        return list(info.get("models", []))
    except Exception as exc:  # noqa: BLE001  # provider list_models() may raise anything; return static fallback list
        _logger.warning("Could not list models for provider '%s': %s", provider, exc)
        return []


def list_provider_models_checked(provider: str, **kwargs: object) -> ModelListing:
    """Return the models of *provider* together with where the list came from.

    A provider that can tell live from built-in answers (``list_models_checked``) says so
    itself.  For the others, the metadata decides: a service that cannot be asked
    (``can_list_models`` false) has a built-in list by design, one that can be asked is
    live, and an empty live answer simply means "nothing to list yet".
    """
    try:
        mod = importlib.import_module(f".providers.{provider}", package=__package__)
        checked = getattr(mod, "list_models_checked", None)
        if checked is not None:
            return checked(**kwargs)
    except Exception as exc:  # noqa: BLE001  # provider list_models_checked() may raise anything; report as a failed listing
        _logger.warning("Could not list models for provider '%s': %s", provider, exc)
        return ModelListing(_builtin_models(provider), "failed", "the model list could not be read")
    models = list_provider_models(provider, **kwargs)
    can_list = bool(get_provider_info(provider).get("can_list_models"))
    return ModelListing(models, "live" if can_list else "builtin")


def _builtin_models(provider: str) -> list[str]:
    """Return the models *provider* lists in its metadata (empty when that cannot be read)."""
    try:
        return list(get_provider_info(provider).get("models", []))
    except Exception:  # noqa: BLE001  # a broken provider module must not turn into a second failure
        return []


def provider_package(provider: str) -> tuple[str, str] | None:
    """Return ``(importable module, pip name)`` of the package *provider* uses, or ``None``."""
    package = get_provider_info(provider).get("package")
    if not isinstance(package, dict):
        return None
    return str(package["module"]), str(package["pip"])


def provider_package_required(provider: str) -> bool:
    """Whether *provider* cannot work without its package (Ollama's connector is optional)."""
    return not get_provider_info(provider).get("package_optional", False)


def check_provider_connection(provider: str, **kwargs: object) -> tuple[bool, str]:
    """Return ``(ok, message)`` for a lightweight health check of *provider*.

    Calls the provider module's ``check_connection(**kwargs)`` when it exists,
    otherwise returns ``(True, "no health check available")``.

    Args:
        provider: Provider name — must match a module in :mod:`teachers_teammate.infrastructure.providers`.
        **kwargs: Forwarded to the provider's ``check_connection()`` (e.g. ``base_url``).

    Returns:
        Tuple of ``(ok, human-readable status message)``.
    """
    try:
        mod = importlib.import_module(f".providers.{provider}", package=__package__)
        fn = getattr(mod, "check_connection", None)
        if fn is not None:
            return fn(**kwargs)
        return True, "no health check available"
    except Exception as exc:  # noqa: BLE001  # provider health check may raise any provider/network error; report as failed
        return False, f"✗ Could not check provider '{provider}': {exc}"


def build_llm(
    provider: str,
    model: str,
    *,
    base_url: str = "http://127.0.0.1:11434",
    temperature: float = 0.7,
) -> BaseChatModel:
    """Instantiate and return a LangChain chat model via the named provider module.

    Args:
        provider:    Provider name — must match a module in :mod:`teachers_teammate.infrastructure.providers`.
        model:       Model name forwarded to the provider.
        base_url:    Base URL for providers that require one (e.g. Ollama).
                     Ignored for providers whose ``PROVIDER_INFO["needs_base_url"]`` is ``False``.
        temperature: Sampling temperature forwarded to the model constructor (0.0 = deterministic).

    Returns:
        A :class:`~langchain_core.language_models.BaseChatModel` ready to invoke.

    Raises:
        RuntimeError: On unknown provider or missing dependency.
    """
    module = _load_provider(provider.lower())
    info = getattr(module, "PROVIDER_INFO", {})
    try:
        if info.get("needs_base_url", False):
            return module.create(model, base_url=base_url, temperature=temperature)
        return module.create(model, temperature=temperature)
    except SystemExit as exc:
        raise ProviderNotAvailableError(
            f"Provider '{provider}' could not be loaded — "
            "check that all required packages are installed."
        ) from exc
