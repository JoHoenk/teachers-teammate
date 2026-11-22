"""The LLM providers: what each one is, what it needs, and the models it offers.

One place answers "which providers are there, has this one its package and its key, which
models does it offer, and where does it process the text".  The model lists are cached here,
per provider and address, so the pickers of the whole process see one answer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import importlib
import importlib.util
import os
import time

from .._model_defaults import default_model_for_task
from ..domain.data_location import PROVIDER_OLLAMA, StageLocation
from ..domain.model_listing import ModelListing
from ..domain.providers import ProviderInfo
from ..infrastructure.llm_factory import (
    check_provider_connection,
    get_provider_data_region,
    get_provider_info,
    list_provider_models_checked,
    list_providers,
    provider_package,
    provider_package_required,
)
from .data_flow import llm_location, text_goes_online


def importable(module: str) -> bool:
    """Return whether *module* can be imported (its package is installed).

    The import caches are refreshed first: a package installed while the app runs (Add-ons) is
    otherwise reported missing until the next start.
    """
    importlib.invalidate_caches()
    return importlib.util.find_spec(module) is not None


class ProviderService:
    """Answers what each LLM provider is, needs and offers, and caches the model lists."""

    _MODEL_CACHE_TTL_S: float = 300.0

    def __init__(self, *, importable_module: Callable[[str], bool] | None = None) -> None:
        self._importable = importable_module or importable
        self._model_cache: dict[str, tuple[float, ModelListing]] = {}
        # Told when an Ollama model list is forgotten, so what was derived from it is forgotten
        # too; the recommender that derives it is built after this service.
        self._forget_classifications: Callable[[str], None] = lambda _url: None

    def watch_classifications(self, forget: Callable[[str], None]) -> None:
        """Let *forget* hear when an Ollama model list is forgotten.

        Called once, while the services are built: the model classifications are derived from
        the list, so they are stale the moment the list is.
        """
        self._forget_classifications = forget

    # ── the providers ─────────────────────────────────────────────────────────

    def list_providers(self) -> list[str]:
        """Return available LLM providers."""
        return list_providers()

    def get_provider_info(self, provider: str) -> ProviderInfo | None:
        """Return what *provider* declares, or ``None`` when the name is no provider."""
        return get_provider_info(provider)

    def display_name(self, provider: str) -> str:
        """Return the name a teacher reads for *provider* (its id when it declares none)."""
        info = self.get_provider_info(provider)
        return (info.display_name if info is not None else "") or provider

    def env_key(self, provider: str) -> str:
        """Return the environment variable *provider* reads its key from (``""`` = none)."""
        info = self.get_provider_info(provider)
        return info.env_key if info is not None else ""

    def provider_needs_key(self, provider: str) -> bool:
        """Return whether *provider* cannot answer without an API key."""
        info = self.get_provider_info(provider)
        return info is not None and info.needs_api_key

    def key_page(self, env_key: str) -> str:
        """Return the page where a key for *env_key* is created (``""`` when nobody says)."""
        for provider in list_providers():
            if self.env_key(provider) == env_key:
                info = self.get_provider_info(provider)
                if info is not None and info.key_url:
                    return info.key_url
        return ""

    def provider_key_missing(self, provider: str) -> bool:
        """Return whether *provider* needs an API key that is not set in the environment.

        Without it the service cannot be asked for its models, so the GUI says "no key"
        instead of starting a request that is certain to fail.
        """
        env_key = self.env_key(provider)
        return self.provider_needs_key(provider) and not os.environ.get(env_key)

    def default_model_for_task(self, provider: str, task: str) -> str:
        """Resolve default model for provider and task."""
        return default_model_for_task(provider, task)

    def effective_model(self, provider: str, model: str, task: str) -> str:
        """Return the model a run uses for *task*: an empty *model* means the provider's default."""
        return model.strip() or self.default_model_for_task(provider, task)

    def get_provider_data_region(self, provider: str) -> str:
        """Return where *provider* processes the data: ``"eu"``, ``"non_eu"`` or ``"unknown"``."""
        return get_provider_data_region(provider)

    def describe_llm_location(self, provider: str, model: str, ollama_url: str) -> StageLocation:
        """Return where proofreading / content review with *provider* and *model* runs."""
        return llm_location(provider, model, ollama_url, self.get_provider_info)

    def sends_text_online(self, settings: Mapping[str, object]) -> bool:
        """Return whether a run with *settings* sends text to an online service.

        Only the steps that run count (proofreading on, and the content review on top of it),
        each with the model it really uses; the same steps and the same classification as the
        data-flow box (:meth:`~teachers_teammate.application.data_flow.describe_data_flow`),
        but from settings, so a page can ask before anything is saved.
        """
        return text_goes_online(settings, self.describe_llm_location, self.effective_model)

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        """Return ``{provider: (importable module, pip name)}`` for every provider package.

        Providers that share one package (Mistral global and EU) appear once, under the
        first of them, so an install list does not offer the same package twice.
        """
        packages: dict[str, tuple[str, str]] = {}
        seen: set[str] = set()
        for provider in list_providers():
            package = provider_package(provider)
            if package is None or package[1] in seen:
                continue
            seen.add(package[1])
            packages[provider] = package
        return packages

    def missing_provider_package(self, provider: str) -> str | None:
        """Return the pip name of the package *provider* needs but is not installed, else ``None``."""
        package = provider_package(provider)
        if package is None or not provider_package_required(provider):
            return None
        if self._importable(package[0]):
            return None
        return package[1]

    def check_connection(
        self,
        provider: str,
        *,
        url: str,
        model: str = "",
    ) -> tuple[bool, bool, str]:
        """Check whether an LLM *provider* answers, and whether *model* is there.

        Returns ``(connected, model_ok, message)``.  For the services that are not Ollama the
        tuple is ``(ok, ok, message)``: the model-level distinction does not apply to them.
        """
        if provider == PROVIDER_OLLAMA:
            from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

            return OllamaClient(url).check_connection(model)
        # Unknown providers are not blocked — assume OK by default.
        if provider not in list_providers():
            return True, True, "✓ OK"
        # Delegate to the provider module's own health check (env-key presence for API
        # providers).  The provider declares its own env_key, so no provider→env-var mapping
        # is duplicated here.
        ok, message = check_provider_connection(provider)
        return ok, ok, message

    # ── the model lists ───────────────────────────────────────────────────────

    @staticmethod
    def _model_cache_key(provider: str, base_url: str = "") -> str:
        """Return the cache key for *provider*'s model list.

        Ollama URLs are normalised so ``"http://host:11434/"`` and ``"http://host:11434"`` — and
        an omitted URL, which means the default server — share one entry; otherwise an
        invalidation could miss the entry that was cached under the other spelling.
        """
        if provider == PROVIDER_OLLAMA:
            from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

            return f"ollama:{normalize_ollama_url(base_url)}"
        return f"{provider}:{base_url}" if base_url else provider

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        """Return available models for a provider (result is TTL-cached per instance)."""
        return self.list_provider_models_checked(provider, base_url=base_url).models

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        """Return the models of a provider and whether the list is live (TTL-cached).

        Only a live or by-design built-in answer is remembered; a failed one (no key, key
        refused, no connection) is asked again next time, so a key entered a moment later
        is noticed.
        """
        cached = self.get_cached_listing(provider, base_url=base_url)
        if cached is not None:
            return cached
        kwargs: dict[str, str] = {"base_url": base_url} if base_url else {}
        listing = list_provider_models_checked(provider, **kwargs)
        self.remember_listing(provider, listing, base_url=base_url)
        return listing

    def remember_listing(self, provider: str, listing: ModelListing, *, base_url: str = "") -> None:
        """Cache *listing* when it is :attr:`ModelListing.cacheable`.

        An empty list means "unreachable" (or nothing downloaded yet): never cache it, or a
        server that was started a moment later would keep being reported as down.
        """
        if listing.cacheable:
            self._model_cache[self._model_cache_key(provider, base_url)] = (
                time.monotonic(),
                listing,
            )

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None:
        """Return the cached listing for *provider* if still within TTL, else ``None``."""
        entry = self._model_cache.get(self._model_cache_key(provider, base_url))
        if entry and (time.monotonic() - entry[0]) < self._MODEL_CACHE_TTL_S:
            return entry[1]
        return None

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        """Return cached model list for *provider* if still within TTL, else ``None``."""
        listing = self.get_cached_listing(provider, base_url=base_url)
        return listing.models if listing is not None else None

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        """Evict the cached model list for *provider* (forces re-fetch on next call).

        For Ollama this also forgets the model classifications of that server.
        """
        self._model_cache.pop(self._model_cache_key(provider, base_url), None)
        if provider == PROVIDER_OLLAMA:
            self._forget_classifications(base_url)

    def forget_ollama_model_list(self, normalized_url: str) -> None:
        """Drop only the cached Ollama model list (the recommender forgets the rest)."""
        self._model_cache.pop(self._model_cache_key(PROVIDER_OLLAMA, normalized_url), None)
