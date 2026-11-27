"""The models a comparison can ask about one text, and what this computer makes of each.

:meth:`ComparisonCandidates.candidates` reads the model lists, the ratings and the measured times
the app already holds, so the window can show them at once on the GUI thread.  The lists expire,
so :meth:`ComparisonCandidates.refresh` asks for them again, off that thread, whenever the window
opens on a document.  A model counts as done when it answered the text already: a stored answer of
its settings, or the document's own proofreading.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses

from ..config import Config
from ..domain.data_location import PROVIDER_OLLAMA
from ..domain.model_catalog import Suitability, suitability_rank
from ..domain.performance_estimate import TYPICAL_PAGE_WORDS, TimeRange
from .model_comparison import Candidate, CompareSource
from .ports import CompareResults, ModelAdvice, PageTimes, ProviderCatalogue

__all__ = ["ComparisonCandidates"]


class ComparisonCandidates:
    """The installed and the online writing models, the best fitting first."""

    def __init__(
        self,
        *,
        store: CompareResults,
        key_of: Callable[[Config], str],
        advice: ModelAdvice,
        providers: ProviderCatalogue,
        speeds: PageTimes,
    ) -> None:
        self._store = store
        self._key = key_of
        self._advice = advice
        self._providers = providers
        self._speeds = speeds

    def candidates(self, config: Config, source: CompareSource) -> tuple[Candidate, ...]:
        """Return the models that can answer about *source*, the best fitting first."""
        found = [*self._local(config, source), *self._online(config, source)]
        return tuple(
            sorted(found, key=lambda item: (suitability_rank(item.suitability), item.model))
        )

    def refresh(self, config: Config) -> None:
        """Ask again for the model lists :meth:`candidates` reads, where they have expired.

        Contacts the Ollama server of *config*, which also says what each of its models can do,
        and the online providers whose key and package are there; a list that is still fresh
        costs no request.  Never call it on the GUI thread.
        """
        url = config.ollama_url
        installed = self._providers.list_provider_models(PROVIDER_OLLAMA, base_url=url)
        if installed:
            self._advice.classify_ollama_models(installed, url)
        for provider in self._online_providers():
            self._providers.list_provider_models(provider)

    def _local(self, config: Config, source: CompareSource) -> list[Candidate]:
        """Return the installed Ollama models that can write, with their ratings and times."""
        url = config.ollama_url
        installed = list(self._providers.get_cached_models(PROVIDER_OLLAMA, base_url=url) or ())
        if not installed:
            return []
        detection = self._advice.cached_hardware_profile(config.hardware_profile, url)
        ratings = self._advice.rate_for(
            installed, "general", detection, config.hardware_profile, url, cached_only=True
        )
        # A model the app never classified may still write; hiding it would hide a model the
        # teacher downloaded for this.  Only a model it knows reads alone is left out.
        writing = [rating for rating in ratings if rating.suitability is not Suitability.WRONG_KIND]
        times = self._speeds.page_times(
            [rating.tag for rating in writing], "general", url, hardware=detection
        )
        return [
            dataclasses.replace(
                self._candidate(config, source, PROVIDER_OLLAMA, rating.tag),
                suitability=rating.suitability,
                very_slow=rating.very_slow,
                estimated=_for_words(times.get(rating.tag), source.words),
            )
            for rating in writing
        ]

    def _online(self, config: Config, source: CompareSource) -> list[Candidate]:
        """Return the online models the app can ask: its key is there and its list is held."""
        return [
            self._candidate(config, source, provider, model)
            for provider in self._online_providers()
            for model in self._providers.get_cached_models(provider) or ()
        ]

    def _online_providers(self) -> list[str]:
        """Return the online providers the app can ask: their key and their package are there."""
        return [
            provider
            for provider in self._providers.list_providers()
            if provider != PROVIDER_OLLAMA
            and not self._providers.provider_key_missing(provider)
            and not self._providers.missing_provider_package(provider)
        ]

    def _candidate(
        self,
        config: Config,
        source: CompareSource,
        provider: str,
        model: str,
    ) -> Candidate:
        """Return *model* of *provider* as an unrated row: its key, where it answers, if it is done.

        A service elsewhere stays unrated: it has no memory of this computer to fit.
        """
        key = self._key(
            dataclasses.replace(config, correction_provider=provider, correction_model=model)
        )
        own = source.own(key)
        return Candidate(
            provider=provider,
            model=model,
            key=key,
            location=self._providers.describe_llm_location(provider, model, config.ollama_url),
            suitability=Suitability.UNKNOWN,
            done=own or self._store.load(source.text_sha, key) is not None,
            own=own,
        )


def _for_words(per_page: TimeRange | None, words: int) -> TimeRange | None:
    """Return *per_page* scaled to a text of *words* words, or ``None`` without a measurement."""
    if per_page is None:
        return None
    return per_page.scaled(words / TYPICAL_PAGE_WORDS if words else 0.0)
