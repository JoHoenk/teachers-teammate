"""LLM factory: discover and invoke provider modules to build a BaseChatModel.

Provider modules live in :mod:`teachers_teammate.infrastructure.providers` — one
file per integration.  Each module declares its facts in ``PROVIDER`` (a
:class:`~teachers_teammate.domain.providers.ProviderInfo`) and exposes a
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
import os
from pathlib import Path
import re
from types import ModuleType
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from ..domain.data_location import DataRegion
from ..domain.model_listing import ModelListing
from ..domain.providers import ProviderInfo
from ..exceptions import ProviderNotAvailableError
from ..interfaces import LLMProviderModule
from ..messages import provider_not_loaded, unknown_provider_error
from ..texts.addons import (
    MODEL_LIST_UNREADABLE,
    NO_HEALTH_CHECK,
    provider_check_failed,
)

_logger = logging.getLogger(__name__)

#: What a provider name may look like: it is the file name in the providers folder.
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]*")

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


def get_provider_info(provider: str) -> ProviderInfo | None:
    """Return the :class:`ProviderInfo` *provider*'s module declares, or ``None``.

    The module is imported lazily; the heavy LangChain dependency inside each
    provider's ``create()`` function is **not** triggered here.
    """
    module = _load_provider(provider)
    return getattr(module, "PROVIDER", None) if module is not None else None


def get_provider_data_region(provider: str, **settings: object) -> str:
    """Return where *provider* processes data: ``"eu"``, ``"non_eu"`` or ``"unknown"``.

    A provider declares the region in its ``PROVIDER.data_region``, or as a
    ``data_region(**settings)`` function when the answer depends on the settings (a custom
    base URL, a regional endpoint).  The function wins, so a provider that offers several
    regions reports the one the current settings use.

    Anything missing, unrecognised or broken is ``"unknown"``: a school can only rely on
    what a vendor actually declares, so "not declared" must never read as "EU".
    """
    try:
        return DataRegion(_declared_region(_load_provider(provider), settings)).value
    except ValueError:
        return DataRegion.UNKNOWN.value


def _declared_region(module: ModuleType | None, settings: dict[str, object]) -> str:
    """Return what *module* declares as its region: its helper's answer, or its metadata."""
    helper = getattr(module, "data_region", None)
    if callable(helper):
        try:
            return str(helper(**settings))
        except Exception:  # noqa: BLE001  # a provider helper may raise anything
            return DataRegion.UNKNOWN.value
    info = getattr(module, "PROVIDER", None)
    return str(getattr(info, "data_region", DataRegion.UNKNOWN))


def _load_provider(provider: str) -> ModuleType | None:
    """Import the module of *provider*, or return ``None`` when there is none.

    The one place a provider is imported.  Only a plain provider name gets here: a name with a
    dot, a slash or a leading underscore would import something that is no provider.  A
    provider whose package is not installed gives ``None`` instead of an error.
    """
    if not _PROVIDER_NAME.fullmatch(provider):
        return None
    try:
        return importlib.import_module(f".providers.{provider}", package=__package__)
    except ImportError:
        _logger.debug("provider '%s' is not installed", provider)
        return None


def _provider_module(provider: str) -> LLMProviderModule:
    """Return the module of *provider*.

    Raises:
        ValueError: *provider* is none of the listed providers.
        ProviderNotAvailableError: the provider is listed, but its module cannot be imported
            (a package it needs is not installed).
    """
    available = list_providers()
    if provider not in available:
        raise ValueError(unknown_provider_error(provider, ", ".join(available)))
    module = _load_provider(provider)
    if module is None:
        raise ProviderNotAvailableError(provider_not_loaded(provider))
    return cast(LLMProviderModule, module)


def list_provider_models(provider: str, **kwargs: object) -> list[str]:
    """Return available model names for *provider*.

    Calls the provider module's ``list_models(**kwargs)`` when it exists,
    otherwise falls back to the ``models`` list of its ``PROVIDER``.

    Args:
        provider: Provider name — must match a module in :mod:`teachers_teammate.infrastructure.providers`.
        **kwargs: Forwarded to the provider's ``list_models()`` (e.g. ``base_url`` for Ollama).

    Returns:
        List of model name strings; empty list on import error.
    """
    mod = _load_provider(provider)
    if mod is None:
        return []
    try:
        fn = getattr(mod, "list_models", None)
        if fn is not None:
            return fn(**kwargs)
        info = getattr(mod, "PROVIDER", None)
        return list(info.models) if info is not None else []
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
    mod = _load_provider(provider)
    if mod is None:
        return ModelListing([], "failed", MODEL_LIST_UNREADABLE)
    try:
        checked = getattr(mod, "list_models_checked", None)
        if checked is not None:
            return checked(**kwargs)
    except Exception as exc:  # noqa: BLE001  # provider list_models_checked() may raise anything; report as a failed listing
        _logger.warning("Could not list models for provider '%s': %s", provider, exc)
        return ModelListing(_builtin_models(provider), "failed", MODEL_LIST_UNREADABLE)
    models = list_provider_models(provider, **kwargs)
    info = get_provider_info(provider)
    return ModelListing(models, "live" if info is not None and info.can_list_models else "builtin")


def _builtin_models(provider: str) -> list[str]:
    """Return the models *provider* lists in its metadata (empty when that cannot be read)."""
    info = get_provider_info(provider)
    return list(info.models) if info is not None else []


def provider_package(provider: str) -> tuple[str, str] | None:
    """Return ``(importable module, pip name)`` of the package *provider* uses, or ``None``."""
    info = get_provider_info(provider)
    if info is None or info.package is None:
        return None
    return info.package.module, info.package.pip


def provider_package_required(provider: str) -> bool:
    """Whether *provider* cannot work without its package (Ollama's connector is optional)."""
    info = get_provider_info(provider)
    return info is None or not info.package_optional


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
    mod = _load_provider(provider)
    if mod is None:
        return False, provider_check_failed(provider, ValueError("no such provider"))
    try:
        fn = getattr(mod, "check_connection", None)
        if fn is not None:
            return fn(**kwargs)
        return True, NO_HEALTH_CHECK
    except Exception as exc:  # noqa: BLE001  # provider health check may raise any provider/network error; report as failed
        return False, provider_check_failed(provider, exc)


def build_llm(
    provider: str,
    model: str,
    *,
    base_url: str,
    temperature: float = 0.7,
    api_key: str | None = None,
) -> BaseChatModel:
    """Instantiate and return a LangChain chat model via the named provider module.

    Args:
        provider:    Provider name — must match a module in :mod:`teachers_teammate.infrastructure.providers`.
        model:       Model name forwarded to the provider.
        base_url:    Base URL for providers that require one (e.g. Ollama).
                     Ignored for providers whose ``PROVIDER.needs_base_url`` is ``False``.
        temperature: Sampling temperature forwarded to the model constructor (0.0 = deterministic).
        api_key:     The key the model sends to its service.  ``None`` uses the environment's
                     (never written); ``""`` means the app has none.

    Returns:
        A :class:`~langchain_core.language_models.BaseChatModel` ready to invoke.

    Raises:
        RuntimeError: On unknown provider or missing dependency.
    """
    module = _provider_module(provider.lower())
    key = _api_key(module, api_key)
    try:
        if module.PROVIDER.needs_base_url:
            return module.create(model, base_url=base_url, temperature=temperature, api_key=key)
        return module.create(model, temperature=temperature, api_key=key)
    except SystemExit as exc:
        raise ProviderNotAvailableError(provider_not_loaded(provider)) from exc


def _api_key(module: LLMProviderModule, api_key: str | None) -> str | None:
    """Return the key to build with: the one given, else the environment's (never written)."""
    if api_key is not None:
        return api_key
    env_key = module.PROVIDER.env_key
    if not isinstance(env_key, str) or not env_key:
        return None
    return os.environ.get(env_key, "").strip() or None
