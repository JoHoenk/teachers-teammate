"""Hardware detection and Ollama model recommendations.

Owns every cache behind the "which model suits this computer" feature so that
nobody else has to remember to invalidate them:

* the **local hardware probe** (``nvidia-smi`` / ``rocm-smi`` / Apple memory) runs
  at most once per process — it describes *this* computer and does not depend on
  the Ollama URL, so editing the URL never re-probes;
* the **model classification** cache (the uses — reading and/or writing — per
  tag, per normalised Ollama URL) only stores *definite* answers, so a model that
  could not be inspected (not pulled yet, server hiccup) is asked again next time;
* :meth:`on_ollama_models_changed` is the one call to make after a pull
  finished: it evicts the model list *and* the classifications for that server.

All methods are thread-safe; the slow ones (probe, classification over the
network) are meant to run in a background thread — see ``cached_only`` for the
variants that are safe to call from the GUI thread.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import dataclasses
import logging
import threading
from urllib.parse import urlparse

from ..domain.model_catalog import (
    OLLAMA_CATALOG,
    DetectionReason,
    GpuInfo,
    GpuUsage,
    HardwareDetection,
    HardwareProfile,
    HardwareProfileSetting,
    LocalHardware,
    ModelCategory,
    ModelRating,
    ModelUses,
    OllamaModelSpec,
    SystemUsage,
    active_profile,
    best_model,
    catalog_uses,
    choose_profile,
    concrete_profile,
    find_spec,
    normalize_tag,
    rate_model,
    uses_from_capabilities,
)
from ..domain.ollama_address import normalize_ollama_url
from ..domain.stages import Stage
from ..exceptions import OllamaUnreachableError
from ..settings_schema import DEFAULTS
from .context_choice import ContextChoice, context_choice
from .ports import HardwareProbe, OllamaServer

_logger = logging.getLogger(__name__)

#: How long the classification waits for one ``show()`` answer.  The names it asks
#: about are already installed, so a healthy server answers at once; a short limit
#: keeps a hung server from blocking a dialog (or closing the app) for long.
_CLASSIFY_TIMEOUT_S = 3.0


@dataclasses.dataclass(frozen=True)
class _LocalProbe:
    """Raw result of one probe of this computer."""

    gpus: tuple[GpuInfo, ...]
    apple_unified_memory_gb: float | None
    system_memory_gb: float | None = None

    def as_local_hardware(self) -> LocalHardware:
        return LocalHardware(
            gpus=tuple(
                (gpu.name, (gpu.vram_mb / 1024) if gpu.vram_mb is not None else None)
                for gpu in self.gpus
            ),
            apple_unified_memory_gb=self.apple_unified_memory_gb,
            system_memory_gb=self.system_memory_gb,
        )


class ModelRecommendationService:
    """Hardware profile detection, model classification and model ratings."""

    def __init__(
        self,
        *,
        effective_model: Callable[[str, str, str], str],
        invalidate_model_list: Callable[[str], None] | None = None,
        invalidate_speed_facts: Callable[[str], None] | None = None,
        machine_factor: Callable[[str, ModelCategory], float | None] | None = None,
        hardware: HardwareProbe,
        ollama: OllamaServer,
    ) -> None:
        """Create the service.

        Args:
            effective_model: ``(provider, model, task)`` → the model a run would
                use; :meth:`context_choice` needs it to promise the window a run
                really uses.
            invalidate_model_list: Evicts the cached list of installed Ollama
                models for a (normalised) server URL.  Supplied by the owner of
                that list; called by :meth:`on_ollama_models_changed`.
            invalidate_speed_facts: Forgets the stored server facts of a URL: a
                pull changes the digest a measurement is keyed by.  Called by
                :meth:`on_ollama_models_changed`, which is what a pull triggers.
            machine_factor: ``(ollama_url, category)`` → how much slower this
                computer is than the reference machine, for the speed a rating
                of :meth:`rate_ollama_models` may carry.
            hardware: What this computer is: its graphics cards and its memory.  The
                probe runs once per process, however often the URL changes.
            ollama: The server the models live on: whether it is this computer, and what a
                model the catalogue does not know can do.
        """
        self._effective_model = effective_model
        self._ollama = ollama
        self._invalidate_model_list = invalidate_model_list or (lambda _url: None)
        self._invalidate_speed_facts = invalidate_speed_facts or (lambda _url: None)
        self._machine_factor_of = machine_factor or (lambda _url, _category: None)
        self._hardware = hardware
        self._probe: _LocalProbe | None = None
        self._probe_lock = threading.Lock()
        self._system_memory: tuple[float | None] | None = None  # read once, a 1-tuple when read
        self._cache_lock = threading.RLock()
        # normalised URL -> {tag -> definite category}; unknown results are never stored.
        self._classification: dict[str, dict[str, ModelUses]] = {}

    # ── this computer ──────────────────────────────────────────────────────

    def _ensure_probe(self, *, refresh: bool = False) -> _LocalProbe:
        # Serialised on its own lock: a probe can take seconds and must not block
        # classification lookups; concurrent callers wait for the one probe.
        with self._probe_lock:
            if self._probe is None or refresh:
                self._probe = _LocalProbe(
                    gpus=tuple(self._hardware.gpus()),
                    apple_unified_memory_gb=self._hardware.apple_unified_memory_gb(),
                    system_memory_gb=self._hardware.system_memory_gb(),
                )
                _logger.info("Probed this computer: %s", self._probe)
            return self._probe

    def local_hardware(self, *, refresh: bool = False) -> LocalHardware:
        """Return the raw hardware of this computer (probed once per process)."""
        return self._ensure_probe(refresh=refresh).as_local_hardware()

    def available_memory_gb(self) -> float | None:
        """Return the main memory free right now in GB, or ``None`` when it cannot be read.

        Read when the "My computer" line is drawn, never stored: it changes all the time and no
        model suggestion depends on it.  It is a quick query, but the GUI still asks for it on a
        background task, like every other probe of this computer.
        """
        return self._hardware.available_memory_gb()

    def read_gpu_usage(self) -> GpuUsage | None:
        """Return how busy the first graphics card is right now, or ``None`` when unreadable."""
        return self._hardware.gpu_usage()

    def read_system_usage(self) -> SystemUsage | None:
        """Return the processor load and main memory in use right now, or ``None`` when unreadable.

        Quick enough for the GUI thread; the processor share is measured since the previous call.
        """
        return self._hardware.system_usage()

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        """Return the hardware detection for the machine that runs Ollama.

        A remote *ollama_url* never probes this computer.  A local one uses the
        process-wide probe, so calling this for every keystroke in the URL field
        costs nothing; ``refresh=True`` probes again ("Detect again").
        """
        url = normalize_ollama_url(ollama_url)
        if not self._ollama.is_local(url):
            return choose_profile(
                gpus=(),
                apple_unified_memory_gb=None,
                ollama_is_remote=True,
                host=urlparse(url).hostname or url,
            )
        local = self.local_hardware(refresh=refresh)
        detection = choose_profile(
            gpus=local.gpus,
            apple_unified_memory_gb=local.apple_unified_memory_gb,
            ollama_is_remote=False,
            system_memory_gb=local.system_memory_gb,
        )
        _logger.info("Hardware profile for %s: %s", url, detection)
        return detection

    def resolve_hardware_profile(
        self, setting: HardwareProfileSetting, ollama_url: str
    ) -> HardwareDetection:
        """Resolve the ``hardware_profile`` *setting* into a concrete detection.

        A profile chosen by hand is returned as a MANUAL choice without any detection.
        ``"auto"`` — and, defensively, any unknown value — runs detection for *ollama_url*.
        """
        profile = concrete_profile(setting)
        if profile is not None:
            return HardwareDetection(
                profile=profile,
                reason=DetectionReason.MANUAL,
                system_memory_gb=self._local_system_memory_gb(ollama_url),
            )
        return self.detect_hardware(ollama_url)

    def cached_hardware_profile(
        self, setting: HardwareProfileSetting, ollama_url: str
    ) -> HardwareDetection | None:
        """Resolve *setting* without ever probing this computer.

        A concrete profile resolves as :meth:`resolve_hardware_profile` does (it reads only the
        system memory, which is quick).  ``"auto"`` with a remote URL resolves as usual (no
        probe).  ``"auto"`` with a local URL returns ``None`` while this process has not probed
        yet: the probe runs ``nvidia-smi`` and the ROCm tools, which must never happen on the GUI
        thread, so the caller asks a background task instead.
        """
        if concrete_profile(setting) is not None:
            return self.resolve_hardware_profile(setting, ollama_url)
        url = normalize_ollama_url(ollama_url)
        if not self._ollama.is_local(url):
            return self.detect_hardware(url)
        if self._probe is None:
            return None
        return self.detect_hardware(url)

    def _local_system_memory_gb(self, ollama_url: str) -> float | None:
        """Return this computer's memory when Ollama runs here, else ``None`` (it is unknown).

        A chosen profile needs no hardware probe, but a model that does not fit on the
        card is still rated by the memory the computer has, so that is read separately and
        once: it is a quick query, unlike the graphics probe.
        """
        if not self._ollama.is_local(ollama_url):
            return None
        if self._hardware.apple_unified_memory_gb() is not None:
            return None  # the graphics share is carved out of this memory: no second helping
        if self._system_memory is None:
            self._system_memory = (self._hardware.system_memory_gb(),)
        return self._system_memory[0]

    # ── catalog ────────────────────────────────────────────────────────────

    def ollama_catalog(self) -> tuple[OllamaModelSpec, ...]:
        """Return the curated Ollama model catalog."""
        return OLLAMA_CATALOG

    def first_run_model_settings(
        self,
        profile: HardwareProfile,
        *,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
    ) -> dict[str, str]:
        """Return the first-start model settings (BEST picks) for this computer."""
        general = best_model("general", profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
        return {
            "ocr_model": best_model(
                "ocr", profile, system_memory_gb=system_memory_gb, card_gb=card_gb
            ),
            "correction_model": general,
            "evaluate_model": general,
        }

    def context_choice(
        self, settings: Mapping[str, object], card_gb: float | None
    ) -> ContextChoice:
        """Return what the context setting changes on this computer, and what ``"auto"`` gives.

        The same rule the run uses (:func:`~teachers_teammate.domain.context_budget.run_windows`),
        so the Settings page cannot promise a window the run does not use.  *card_gb* comes from
        a detection the caller already has: this does no I/O, so it is safe on the GUI thread.
        """
        proofreading_provider = str(
            settings.get("correction_provider") or DEFAULTS["correction_provider"]
        )
        review_provider = str(settings.get("evaluate_provider") or DEFAULTS["evaluate_provider"])
        return context_choice(
            proofreading_provider=proofreading_provider,
            proofreading_model=self._effective_model(
                proofreading_provider,
                str(settings.get("correction_model") or ""),
                Stage.CORRECTION,
            ),
            review_provider=review_provider,
            review_model=self._effective_model(
                review_provider, str(settings.get("evaluate_model") or ""), Stage.EVALUATION
            ),
            card_gb=card_gb,
        )

    # ── classification & ratings ───────────────────────────────────────────

    def classify_ollama_models(
        self, tags: Sequence[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        """Return the uses (reading and/or writing) of each Ollama model tag.

        The catalog decides first; only unknown tags are asked to the server via
        ``show().capabilities``: a model that can read images may be used for
        reading *and* writing, a text-only model for writing only.  Only definite
        answers are cached.  After the first connection failure the remaining
        tags are not asked, so an unreachable server costs one timeout, not one
        per model.

        Args:
            tags: Model tags to classify.
            ollama_url: Server the tags live on.
            cached_only: Never contact the server — unknown tags map to ``None``.
                Use this from the GUI thread.
        """
        url = normalize_ollama_url(ollama_url)
        result: dict[str, ModelUses | None] = {}
        unknown: list[str] = []
        with self._cache_lock:
            cache = self._classification.setdefault(url, {})
            for tag in tags:
                cached = cache.get(tag)
                if cached is not None:
                    result[tag] = cached
                    continue
                spec = find_spec(tag)
                if spec is not None:
                    cache[tag] = catalog_uses(spec)
                    result[tag] = cache[tag]
                else:
                    unknown.append(tag)

        if not unknown:
            return result
        if cached_only:
            result.update(dict.fromkeys(unknown, None))
            return result

        reachable = True
        for tag in unknown:
            uses: ModelUses | None = None
            if reachable:
                try:
                    capabilities = self._ollama.model_capabilities(
                        url, normalize_tag(tag), timeout=_CLASSIFY_TIMEOUT_S
                    )
                except OllamaUnreachableError:
                    reachable = False
                else:
                    if capabilities is not None:
                        uses = uses_from_capabilities(capabilities)
            if uses is not None:
                with self._cache_lock:
                    self._classification.setdefault(url, {})[tag] = uses
            result[tag] = uses
        return result

    def rate_ollama_models(
        self,
        tags: Sequence[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
        machine_factor: float | None = None,
    ) -> list[ModelRating]:
        """Return a :class:`ModelRating` per tag — the single rating path for widgets.

        Pass ``cached_only=True`` from the GUI thread (after a background thread
        has classified the tags); the default may contact the server for tags
        that are not catalogued.  *system_memory_gb* is the memory of the computer that
        runs the models (``HardwareDetection.system_memory_gb``): with it, a model too big
        for the graphics card but within reach of that memory is rated slow, not too large.
        *card_gb* is the graphics memory models can use there (``HardwareDetection.card_gb``);
        ``None`` assumes the size the profile stands for.  *machine_factor* is how much slower
        this computer is than the reference machine, for the speed a model is expected to
        reach; ``None`` asks the owner of the measurements, and a caller that gets nothing
        leaves every rating without one.
        """
        factor = (
            machine_factor
            if machine_factor is not None
            else self._machine_factor_of(ollama_url, task)
        )
        classification = self.classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        return [
            rate_model(
                tag,
                task,
                profile,
                detected_uses=classification.get(tag),
                system_memory_gb=system_memory_gb,
                card_gb=card_gb,
                machine_factor=factor,
            )
            for tag in tags
        ]

    def rate_for(
        self,
        tags: Sequence[str],
        task: ModelCategory,
        detection: HardwareDetection | None,
        setting: HardwareProfileSetting,
        ollama_url: str,
        *,
        cached_only: bool = False,
    ) -> list[ModelRating]:
        """Return a rating per tag on the computer *detection* describes, as *setting* reads it.

        The profile, the computer's memory and its card come from *detection*; a profile the
        teacher chose by hand decides over it.
        """
        return self.rate_ollama_models(
            tags,
            task,
            active_profile(detection, setting),
            ollama_url,
            cached_only=cached_only,
            system_memory_gb=detection.system_memory_gb if detection is not None else None,
            card_gb=detection.card_gb if detection is not None else None,
        )

    # ── invalidation ───────────────────────────────────────────────────────

    def invalidate_classification(self, ollama_url: str) -> None:
        """Forget the classifications for *ollama_url* only."""
        with self._cache_lock:
            self._classification.pop(normalize_ollama_url(ollama_url), None)

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        """Evict everything derived from the installed-model list of *ollama_url*.

        Call this after a pull finished (or a model was removed): the model list,
        the classifications and the stored server facts are stale from that moment
        on — a pull changes the digest a measurement is keyed by.
        """
        url = normalize_ollama_url(ollama_url)
        self._invalidate_model_list(url)
        self.invalidate_classification(url)
        self._invalidate_speed_facts(url)
