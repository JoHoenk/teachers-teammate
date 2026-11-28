"""Personal-info removal: the spaCy models it loads, and the caches behind them.

One instance per process.  A run, a privacy preview and a text export share the anonymizer
cache, the cache of anonymized texts and the list of installed models, so the same text costs
one spaCy pass per session and a download is seen by every window.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading
from typing import TYPE_CHECKING

from ..config import Config
from ..domain.anonymization import (
    AnonymizerConfig,
    AnonymizerNotice,
    config_hiding,
    config_keeping,
    resolve_anonymizer_config,
)
from ..domain.name_models import FALLBACK_SPACY_MODEL, SPACY_MODELS, spacy_model_for
from ..infrastructure.anonymizer import AnonymizationResultCache, CachedAnonymizer
from .ports import NameModels

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from ..interfaces import Anonymizer


class AnonymizationService:
    """What personal-info removal loads and the caches it answers from."""

    def __init__(self, *, names: NameModels) -> None:
        # The spaCy models name detection loads: which are installed, and loading one.
        self._names = names
        self._anonymizer_cache: dict[tuple, Anonymizer] = {}
        # Anonymized texts of this session, so a run, a preview and an export analyse the same
        # text once.  In memory only: nothing that was hidden is written to disk.
        self._anonymization_cache = AnonymizationResultCache()
        # A spaCy pipeline is not safe to share between threads (a privacy preview and a
        # background text export may run at once), so building and running are serialized.
        self._anonymizer_lock = threading.RLock()
        # Listing the installed spaCy models imports spaCy; the answer only changes when the
        # user downloads one, so it is remembered until forget_installed_models().
        self._installed_models: list[str] | None = None
        # Warnings already shown: resolving the settings happens on every preview.
        self._warned: set[str] = set()

    # ── The anonymizer ────────────────────────────────────────────────────

    def warm_up_anonymizer(self, language: str, config: AnonymizerConfig | None = None) -> None:
        """Load (and cache) the anonymizer for *language* so a missing model shows up early.

        Raises:
            ImportError: spacy is not installed.
            OSError: a required spacy language model is not downloaded.
        """
        with self._anonymizer_lock:
            self._anonymizer(language, config)

    def _anonymizer(
        self, language: str, config: AnonymizerConfig | None
    ) -> tuple[Anonymizer, AnonymizerConfig]:
        """Return the cached anonymizer and its resolved settings; callers hold the lock."""
        anon_config = self.resolve_anonymizer_config(language, config)
        cache_key = (language, anon_config)
        cached = self._anonymizer_cache.get(cache_key)
        if cached is None:
            cached = self._names.anonymizer(language, anon_config)
            self._anonymizer_cache[cache_key] = cached
        return cached, anon_config

    def build_cached_anonymizer(self, language: str, config: AnonymizerConfig) -> Anonymizer:
        """Return an anonymizer backed by this service's model and result caches.

        Passed into a run as ``build_anonymizer``, so a pipeline, a privacy preview and a text
        export share one result: the same text costs one spaCy pass per session and settings.

        Args:
            language: Document language — selects the primary spaCy model.
            config: The anonymizer settings; ``"auto"`` models are resolved here.
        """
        with self._anonymizer_lock:
            inner, anon_config = self._anonymizer(language, config)
        return CachedAnonymizer(
            inner,
            self._anonymization_cache,
            (language, anon_config),
            self._anonymizer_lock,
        )

    def anonymize_text(
        self,
        text: str,
        language: str,
        config: AnonymizerConfig | None = None,
    ) -> tuple[str, dict[str, str]]:
        """Anonymize *text* using NER + configured regex patterns.

        What the privacy preview, the Copy menu and a saved text export all use, so they can
        never hide different things in the same document.

        Args:
            text: Raw text to anonymize.
            language: Document language — selects the primary spaCy model.
            config: :class:`AnonymizerConfig` controlling the optional secondary
                NER model and regex patterns.  ``None`` uses the default config.

        Raises:
            ImportError: spacy is not installed.
            OSError: a required spaCy language model is not downloaded.
        """
        with self._anonymizer_lock:
            inner, anon_config = self._anonymizer(language, config)
            cached = CachedAnonymizer(
                inner,
                self._anonymization_cache,
                (language, anon_config),
                self._anonymizer_lock,
            )
            return cached.anonymize(text)

    def with_hiding(
        self, config: AnonymizerConfig, literal: str, *, hide: bool
    ) -> AnonymizerConfig:
        """Return *config* with *literal* hidden from now on, or with *literal* left as written.

        *Hide this* and *Don't hide this* of the Check tab change no text and no plan: they
        write one rule of the anonymizer's settings, so the next send obeys them.
        """
        return config_hiding(config, literal) if hide else config_keeping(config, literal)

    def clear_anonymizer_cache(self) -> None:
        """Evict the cached anonymizers and with them every anonymized text."""
        with self._anonymizer_lock:
            self._anonymizer_cache.clear()
            self._anonymization_cache.clear()
        self.forget_installed_models()

    # ── The spaCy models ──────────────────────────────────────────────────

    def spacy_model_for_language(self, language: str) -> str:
        """Return the spaCy model name for *language*, falling back to the multilingual model."""
        return spacy_model_for(language)

    def list_installed_spacy_models(self) -> list[str]:
        """Return sorted names of installed spaCy model packages, or ``[]`` if spaCy is absent."""
        return self._names.installed()

    def _remembered_installed_models(self) -> list[str]:
        """Return the installed spaCy models, asking spaCy only once until it is forgotten."""
        if self._installed_models is None:
            self._installed_models = self.list_installed_spacy_models()
        return list(self._installed_models)

    def _warn_once(self, notice: AnonymizerNotice) -> None:
        """Log *notice* unless the same settings were reported before.

        One diagnostic line, said once: the sentence a teacher reads for it is worded in
        ``texts/addons.py`` by the run, which has a reporter.
        """
        key = repr(notice)
        if key not in self._warned:
            self._warned.add(key)
            _logger.warning("Extra detection model not used: %s", notice)

    def anonymizer_available(self, language: str, config: AnonymizerConfig | None = None) -> bool:
        """Return whether personal information can be hidden for *language* right now.

        The models are resolved the way a run resolves them, so a chosen primary model counts:
        the answer is "yes" exactly when a run would find every model it loads.  Cheap enough
        for the GUI to ask whenever it builds a panel: spaCy must be importable and the models
        downloaded.  The GUI uses this to disable "Hide personal information in texts" instead
        of failing when it is used.
        """
        from importlib.util import find_spec as find_module_spec  # noqa: PLC0415

        if find_module_spec("spacy") is None:
            return False
        installed = self._remembered_installed_models()
        return all(model in installed for model in self.anonymizer_models(language, config))

    def extra_name_models(self, language: str) -> list[str]:
        """Return the spaCy models that can be the extra language next to *language*'s own.

        The model of the document language itself is left out: a second pass with it would only
        repeat the first.
        """
        own = spacy_model_for(language)
        models = dict.fromkeys([*SPACY_MODELS.values(), FALLBACK_SPACY_MODEL])
        return [model for model in models if model != own]

    def system_spacy_model(self) -> str | None:
        """Return the spaCy model for the language this computer is set to, if it is known."""
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        language = system_language()
        return spacy_model_for(language) if language else None

    def resolve_anonymizer_config(
        self, language: str, config: AnonymizerConfig | None = None
    ) -> AnonymizerConfig:
        """Return *config* with the spaCy models that will actually be loaded.

        ``"auto:system"`` becomes the model of the system language — unless that is the same
        model as the primary pass or the model is not downloaded (then a warning says so and
        no extra pass runs).  An explicitly chosen model is kept as it is: if it is missing,
        building the anonymizer fails as before.
        """
        anon_config = config if config is not None else AnonymizerConfig()
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        resolved, notice = resolve_anonymizer_config(
            anon_config,
            document_language=language,
            system_language=system_language(),
            installed_models=self._remembered_installed_models(),
        )
        if notice is not None:
            self._warn_once(notice)
        return resolved

    def anonymizer_models(self, language: str, config: AnonymizerConfig | None = None) -> list[str]:
        """Return the spaCy models personal-info removal loads for *language* and *config*.

        Resolved the same way a run resolves them, so a chosen primary model counts and an
        automatic extra model that is not downloaded is left out.  No warning: this answers
        "would it work?", and the run itself warns when a model is missing.
        """
        from ..infrastructure.system_locale import system_language  # noqa: PLC0415

        anon_config = config if config is not None else AnonymizerConfig()
        resolved, _notice = resolve_anonymizer_config(
            anon_config,
            document_language=language,
            system_language=system_language(),
            installed_models=self._remembered_installed_models(),
        )
        models = [resolved.primary_model or spacy_model_for(language)]
        if resolved.secondary_model:
            models.append(resolved.secondary_model)
        return models

    def describe_anonymizer_models(self, config: Config) -> list[str]:
        """Return the spaCy models personal-info removal loads for *config*, or ``[]`` when off.

        Resolved the same way a run resolves them, so the status box names the models a run
        would really use.  A missing automatic model is not reported here: the box only
        says what runs, and the run itself warns.
        """
        if not config.anonymization_enabled:
            return []
        return self.anonymizer_models(config.language, config.anonymizer_config)

    def reread_installed_models(self) -> None:
        """Ask spaCy again which models are installed, without forgetting what was warned."""
        self._installed_models = None

    def forget_installed_models(self) -> None:
        """Ask spaCy again which models are installed (after a download or an install).

        Also lets a warning be printed again: what it said may have changed with the install.
        """
        self.reread_installed_models()
        self._warned.clear()

    def download_spacy_model(
        self,
        model: str,
        target: Path,
        progress_cb: Callable[[str], None],
    ) -> None:
        """Download a spaCy model into *target* using spaCy's own download mechanism."""
        from ..infrastructure.addon_pip import download_spacy_model_subprocess  # noqa: PLC0415

        download_spacy_model_subprocess(model, target, progress_cb)
