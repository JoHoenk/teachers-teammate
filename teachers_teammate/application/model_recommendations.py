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

from collections.abc import Callable, Sequence
import dataclasses
import threading
from typing import cast
from urllib.parse import urlparse

from ..domain.model_catalog import (
    HARDWARE_PROFILES,
    OLLAMA_CATALOG,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    LocalHardware,
    ModelCategory,
    ModelRating,
    ModelUses,
    OllamaModelSpec,
    best_model,
    catalog_uses,
    choose_profile,
    find_spec,
    normalize_tag,
    rate_model,
    uses_from_capabilities,
)
from ..exceptions import OllamaConnectionError
from ..infrastructure.gpu_detector import GpuInfo

#: How long the classification waits for one ``show()`` answer.  The names it asks
#: about are already installed, so a healthy server answers at once; a short limit
#: keeps a hung server from blocking a dialog (or closing the app) for long.
_CLASSIFY_TIMEOUT_S = 3.0


@dataclasses.dataclass(frozen=True)
class _LocalProbe:
    """Raw result of one probe of this computer."""

    gpus: tuple[GpuInfo, ...]
    apple_unified_memory_gb: float | None

    def as_local_hardware(self) -> LocalHardware:
        return LocalHardware(
            gpus=tuple(
                (gpu.name, (gpu.vram_mb / 1024) if gpu.vram_mb is not None else None)
                for gpu in self.gpus
            ),
            apple_unified_memory_gb=self.apple_unified_memory_gb,
        )


class ModelRecommendationService:
    """Hardware profile detection, model classification and model ratings."""

    def __init__(self, *, invalidate_model_list: Callable[[str], None] | None = None) -> None:
        """Create the service.

        Args:
            invalidate_model_list: Evicts the cached list of installed Ollama
                models for a (normalised) server URL.  Supplied by the owner of
                that list; called by :meth:`on_ollama_models_changed`.
        """
        self._invalidate_model_list = invalidate_model_list or (lambda _url: None)
        self._probe: _LocalProbe | None = None
        self._probe_lock = threading.Lock()
        self._cache_lock = threading.RLock()
        # normalised URL -> {tag -> definite category}; unknown results are never stored.
        self._classification: dict[str, dict[str, ModelUses]] = {}

    # ── this computer ──────────────────────────────────────────────────────

    def _ensure_probe(self, *, refresh: bool = False) -> _LocalProbe:
        # Serialised on its own lock: a probe can take seconds and must not block
        # classification lookups; concurrent callers wait for the one probe.
        with self._probe_lock:
            if self._probe is None or refresh:
                # Module attribute lookups at call time keep this patchable.
                from ..infrastructure import gpu_detector  # noqa: PLC0415

                self._probe = _LocalProbe(
                    gpus=tuple(gpu_detector.detect_gpus()),
                    apple_unified_memory_gb=gpu_detector.detect_apple_unified_memory_gb(),
                )
            return self._probe

    def local_hardware(self, *, refresh: bool = False) -> LocalHardware:
        """Return the raw hardware of this computer (probed once per process)."""
        return self._ensure_probe(refresh=refresh).as_local_hardware()

    def detected_gpus(self, *, refresh: bool = False) -> list[GpuInfo]:
        """Return the GPUs of this computer (probed once per process)."""
        return list(self._ensure_probe(refresh=refresh).gpus)

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        """Return the hardware detection for the machine that runs Ollama.

        A remote *ollama_url* never probes this computer.  A local one uses the
        process-wide probe, so calling this for every keystroke in the URL field
        costs nothing; ``refresh=True`` probes again ("Detect again").
        """
        from ..infrastructure.ollama_utils import (  # noqa: PLC0415
            is_local_ollama_url,
            normalize_ollama_url,
        )

        url = normalize_ollama_url(ollama_url)
        if not is_local_ollama_url(url):
            return choose_profile(
                gpus=(),
                apple_unified_memory_gb=None,
                ollama_is_remote=True,
                host=urlparse(url).hostname or url,
            )
        local = self.local_hardware(refresh=refresh)
        return choose_profile(
            gpus=local.gpus,
            apple_unified_memory_gb=local.apple_unified_memory_gb,
            ollama_is_remote=False,
        )

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        """Resolve the ``hardware_profile`` *setting* into a concrete detection.

        A concrete profile ("cpu"/"8gb"/"16gb") is returned as a MANUAL choice
        without any detection.  ``"auto"`` — and, defensively, any unknown value —
        runs detection for *ollama_url*.
        """
        if setting in HARDWARE_PROFILES:
            return HardwareDetection(
                profile=cast(HardwareProfile, setting), reason=DetectionReason.MANUAL
            )
        return self.detect_hardware(ollama_url)

    # ── catalog ────────────────────────────────────────────────────────────

    def ollama_catalog(self) -> tuple[OllamaModelSpec, ...]:
        """Return the curated Ollama model catalog."""
        return OLLAMA_CATALOG

    def best_ollama_model(self, category: ModelCategory, profile: HardwareProfile) -> str:
        """Return the BEST Ollama model tag for *category* on *profile*."""
        return best_model(category, profile)

    def first_run_model_settings(self, profile: HardwareProfile) -> dict[str, str]:
        """Return the first-start model settings (BEST picks) for *profile*."""
        general = best_model("general", profile)
        return {
            "ocr_model": best_model("ocr", profile),
            "correction_model": general,
            "evaluate_model": general,
        }

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
        from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

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

        from ..infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

        client = OllamaClient(url, timeout=_CLASSIFY_TIMEOUT_S)
        reachable = True
        for tag in unknown:
            uses: ModelUses | None = None
            if reachable:
                try:
                    capabilities = client.model_capabilities(normalize_tag(tag))
                except OllamaConnectionError:
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
    ) -> list[ModelRating]:
        """Return a :class:`ModelRating` per tag — the single rating path for widgets.

        Pass ``cached_only=True`` from the GUI thread (after a background thread
        has classified the tags); the default may contact the server for tags
        that are not catalogued.
        """
        classification = self.classify_ollama_models(tags, ollama_url, cached_only=cached_only)
        return [
            rate_model(tag, task, profile, detected_uses=classification.get(tag)) for tag in tags
        ]

    # ── invalidation ───────────────────────────────────────────────────────

    def invalidate_classification(self, ollama_url: str) -> None:
        """Forget the classifications for *ollama_url* only."""
        from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

        with self._cache_lock:
            self._classification.pop(normalize_ollama_url(ollama_url), None)

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        """Evict everything derived from the installed-model list of *ollama_url*.

        Call this after a pull finished (or a model was removed): the model list
        and the classifications are stale from that moment on.
        """
        from ..infrastructure.ollama_utils import normalize_ollama_url  # noqa: PLC0415

        url = normalize_ollama_url(ollama_url)
        self._invalidate_model_list(url)
        self.invalidate_classification(url)
