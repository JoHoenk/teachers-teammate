"""Ollama model catalog and hardware-based recommendations (pure data + logic).

This module is the single source of truth for:

* which Ollama models the app knows about (:data:`OLLAMA_CATALOG`), grouped into
  *reading models* (OCR / vision: turn scans into text) and *writing models*
  (general purpose: proofreading and content review);
* which model is the best choice for a given computer type
  (:data:`RECOMMENDED`);
* how suitable a model is for a task on a given computer type
  (:func:`suitability` → :class:`Suitability`);
* how the computer type (hardware profile) is derived from detection results
  (:func:`choose_profile`).

It is pure data and decision logic: no I/O, no Qt, no network — the
infrastructure layer gathers inputs, the application service caches them, and
widgets only render the results.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

HardwareProfile = Literal["cpu", "8gb", "16gb"]
HardwareProfileSetting = Literal["auto", "cpu", "8gb", "16gb"]
ModelCategory = Literal["ocr", "general"]
#: The tasks a model may be used for: a catalog model has exactly one, a model the
#: Ollama server reports as able to read images has both.
ModelUses = frozenset[ModelCategory]

#: The three hardware tiers the GUI distinguishes, ordered from weakest to strongest.
HARDWARE_PROFILES: tuple[HardwareProfile, ...] = ("cpu", "8gb", "16gb")

#: Values accepted for the ``hardware_profile`` setting (auto-detect plus the tiers).
PROFILE_SETTINGS: tuple[HardwareProfileSetting, ...] = ("auto", *HARDWARE_PROFILES)

# Memory (GB) a model may need to count as "fits". For cpu this is a speed limit,
# not a hard limit: larger models still run, just slowly.
MEMORY_BUDGET_GB: dict[HardwareProfile, float] = {"cpu": 4.0, "8gb": 7.5, "16gb": 15.5}

#: Static default models (config DEFAULTS, CLI, provider metadata).  They apply
#: regardless of hardware; the per-profile picks in :data:`RECOMMENDED` only drive
#: the hardware-aware GUI recommendations.
DEFAULT_OCR_MODEL = "hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0"
DEFAULT_WRITING_MODEL = "gpt-oss:20b"

# ── hardware detection thresholds ─────────────────────────────────────────────

#: GPUs reporting >= this many GB count as "16 GB or more" (16 GB cards report ~15.5+ GiB).
GPU_16GB_THRESHOLD_GB = 15.0
#: GPUs reporting >= this many GB count as "about 8 GB" (8 GB cards report 7.6-8.0 GiB).
GPU_8GB_THRESHOLD_GB = 7.0
#: Share of unified memory macOS leaves available for GPU workloads by default.
APPLE_GPU_MEMORY_SHARE = 0.66


@dataclass(frozen=True)
class OllamaModelSpec:
    """One catalogued Ollama model with approximate resource needs."""

    #: Normalised model tag, always including the ``:tag`` suffix.
    tag: str
    #: "ocr" (reading model) or "general" (writing model).  The Downloads dialog
    #: lists the model in this group.
    category: ModelCategory
    #: Approximate memory (GB) while running (weights + ~8k context).
    memory_gb: float
    #: Approximate download size (GB), shown before the model is pulled.
    download_gb: float
    #: Plain-language one-liner shown as tooltip.
    note: str
    #: Further tasks the model is verified for besides ``category`` (a writing
    #: model that can also read images).
    also_uses: ModelUses = frozenset()


# Download sizes (decimal GB) were checked against the Ollama registry manifests
# with ``tools/verify_model_catalog.py`` on 2026-10-12 (the Hugging Face entry
# against the repository file list).  ``memory_gb`` is an estimate (weights + ~8k
# context) that cannot be read from a manifest.
OLLAMA_CATALOG: tuple[OllamaModelSpec, ...] = (
    OllamaModelSpec(
        tag=DEFAULT_OCR_MODEL,
        category="ocr",
        memory_gb=6.5,
        download_gb=5.5,
        note="Built for full pages with tables and handwriting. Keeps the page layout.",
    ),
    OllamaModelSpec(
        tag="glm-ocr:q8_0",
        category="ocr",
        memory_gb=2.5,
        download_gb=1.6,
        note="Small and fast. Good for printed and mixed-language pages.",
    ),
    OllamaModelSpec(
        tag="qwen3-vl:8b",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.1,
        note="Best overall for handwriting.",
    ),
    OllamaModelSpec(
        tag="deepseek-ocr:latest",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.7,
        note="Strong on dense, structured pages.",
    ),
    OllamaModelSpec(
        tag="qwen2.5vl:7b",
        category="ocr",
        memory_gb=7.0,
        download_gb=6.0,
        note="Older version of qwen3-vl.",
    ),
    OllamaModelSpec(
        tag="qwen3:4b",
        category="general",
        memory_gb=3.5,
        download_gb=2.5,
        note="Small writing model for computers without a graphics card.",
    ),
    OllamaModelSpec(
        tag="qwen3.5:9b",
        category="general",
        memory_gb=7.0,
        download_gb=6.6,
        note="Best writing model for 8 GB. Can also read scanned pages.",
        also_uses=frozenset({"ocr"}),
    ),
    OllamaModelSpec(
        tag="mistral:latest",
        category="general",
        memory_gb=5.5,
        download_gb=4.4,
        note="Reliable all-rounder.",
    ),
    OllamaModelSpec(
        tag="mistral-nemo:12b",
        category="general",
        memory_gb=8.5,
        download_gb=7.1,
        note="Good with long texts.",
    ),
    OllamaModelSpec(
        tag="gemma4:latest",
        category="general",
        memory_gb=7.5,
        download_gb=6.6,
        note="Compact writing model.",
    ),
    OllamaModelSpec(
        tag="gemma4:12b",
        category="general",
        memory_gb=9.5,
        download_gb=8.0,
        note="Larger writing model.",
    ),
    OllamaModelSpec(
        tag=DEFAULT_WRITING_MODEL,
        category="general",
        memory_gb=14.0,
        download_gb=13.8,
        note="Best writing model for 16 GB.",
    ),
)

#: Ordered recommendations per category and profile: the first entry is ``BEST``,
#: the others are ``GOOD``.  Every list must be non-empty and every tag must
#: exist in :data:`OLLAMA_CATALOG` with the matching category (tests enforce this).
RECOMMENDED: dict[ModelCategory, dict[HardwareProfile, tuple[str, ...]]] = {
    "ocr": {
        "cpu": ("glm-ocr:q8_0",),
        # 16 GB reading list intentionally equals the 8 GB list until a larger
        # vision model has been validated with the Benchmark app (plan §10).
        "8gb": (
            DEFAULT_OCR_MODEL,
            "qwen3-vl:8b",
            "deepseek-ocr:latest",
            "qwen3.5:9b",
            "glm-ocr:q8_0",
        ),
        "16gb": (
            DEFAULT_OCR_MODEL,
            "qwen3-vl:8b",
            "deepseek-ocr:latest",
            "qwen3.5:9b",
            "glm-ocr:q8_0",
        ),
    },
    "general": {
        "cpu": ("qwen3:4b",),
        "8gb": ("qwen3.5:9b", "mistral:latest"),
        "16gb": ("gpt-oss:20b", "qwen3.5:9b", "mistral-nemo:12b", "gemma4:12b"),
    },
}


class Suitability(StrEnum):
    """How well a model fits a task on a given hardware profile."""

    BEST = "best"
    GOOD = "good"
    WORKS = "works"
    TOO_LARGE = "too_large"
    WRONG_KIND = "wrong_kind"
    UNKNOWN = "unknown"


def normalize_tag(tag: str) -> str:
    """Return *tag* in normalised form: stripped, with ``:latest`` appended when needed.

    ``"deepseek-ocr"`` → ``"deepseek-ocr:latest"``; tags that already carry a
    ``:tag`` suffix are returned unchanged (apart from surrounding whitespace).
    """
    cleaned = (tag or "").strip()
    if not cleaned:
        return cleaned
    if ":" in cleaned:
        return cleaned
    return f"{cleaned}:latest"


def find_spec(tag: str) -> OllamaModelSpec | None:
    """Return the catalog entry for *tag* (normalised), or ``None`` when unknown."""
    wanted = normalize_tag(tag)
    for spec in OLLAMA_CATALOG:
        if spec.tag == wanted:
            return spec
    return None


def catalog_uses(spec: OllamaModelSpec) -> ModelUses:
    """Return the uses of a catalogued model: its curated category plus ``also_uses``."""
    return frozenset({spec.category}) | spec.also_uses


def uses_from_capabilities(capabilities: Collection[str]) -> ModelUses:
    """Return the uses of a model that is not in the catalog, from its capabilities.

    A model that can read images ("vision") may be used for reading *and* writing;
    most current general models are multimodal, so treating them as reading-only
    would hide them from the writing steps.  A text-only model is writing-only.
    """
    if "vision" in capabilities:
        return frozenset({"ocr", "general"})
    return frozenset({"general"})


def best_model(category: ModelCategory, profile: HardwareProfile) -> str:
    """Return the BEST model tag for *category* on *profile*."""
    return RECOMMENDED[category][profile][0]


def suitability(
    tag: str,
    category: ModelCategory,
    profile: HardwareProfile,
    *,
    detected_uses: ModelUses | None = None,
) -> Suitability:
    """Rate how well *tag* suits *category* on *profile*.

    Rules, applied in order:

    1. Uses unknown (not in the catalog and *detected_uses* is ``None``)
       → :attr:`Suitability.UNKNOWN`.
    2. *category* not among the model's uses → :attr:`Suitability.WRONG_KIND`.
       A catalogued reading model is therefore never offered for writing: the
       dedicated OCR models do not follow a proofreading prompt well.  A catalogued
       writing model is a reading model too only when it lists ``"ocr"`` in
       ``also_uses``.
    3. Tag is ``RECOMMENDED[category][profile][0]`` → ``BEST``; elsewhere in that
       list → ``GOOD``.
    4. Not catalogued (custom model) → ``WORKS`` (its size is unknown, so it is
       never called too large).
    5. ``memory_gb`` above the profile budget → ``TOO_LARGE``, otherwise ``WORKS``.

    Args:
        tag: Model tag (normalisation is applied internally).
        category: Task category: ``"ocr"`` for reading, ``"general"`` for writing.
        profile: Hardware profile of the computer that runs the model.
        detected_uses: Uses reported by the Ollama server for models that are not
            in the catalog (see :func:`uses_from_capabilities`).  ``None`` means
            "unknown".
    """
    spec = find_spec(tag)
    uses = catalog_uses(spec) if spec is not None else detected_uses
    if uses is None:
        return Suitability.UNKNOWN
    if category not in uses:
        return Suitability.WRONG_KIND

    normalized = normalize_tag(tag)
    recommended = RECOMMENDED[category][profile]
    if normalized == recommended[0]:
        return Suitability.BEST
    if normalized in recommended:
        return Suitability.GOOD

    if spec is None:
        # Custom model: size unknown — never called too large.
        return Suitability.WORKS

    if spec.memory_gb > MEMORY_BUDGET_GB[profile]:
        return Suitability.TOO_LARGE
    return Suitability.WORKS


#: Sort rank of suitability values (BEST first, WRONG_KIND last).
_SUITABILITY_RANK: dict[Suitability, int] = {
    Suitability.BEST: 0,
    Suitability.GOOD: 1,
    Suitability.WORKS: 2,
    Suitability.TOO_LARGE: 3,
    Suitability.UNKNOWN: 4,
    Suitability.WRONG_KIND: 5,
}


def suitability_rank(value: Suitability) -> int:
    """Return the sort rank of *value* (BEST = 0 … WRONG_KIND = 5)."""
    return _SUITABILITY_RANK[value]


@dataclass(frozen=True)
class ModelRating:
    """Everything a widget needs to render one model for one task and profile."""

    tag: str
    #: Task the rating is for ("ocr" = reading, "general" = writing).
    task: ModelCategory
    #: What the model may be used for: catalog category, detected uses, or ``None``.
    model_uses: ModelUses | None
    suitability: Suitability
    #: Approximate memory need (GB); ``None`` for models that are not catalogued.
    memory_gb: float | None
    #: Plain-language catalog note; empty for custom models.
    note: str


def rate_model(
    tag: str,
    task: ModelCategory,
    profile: HardwareProfile,
    *,
    detected_uses: ModelUses | None = None,
) -> ModelRating:
    """Rate *tag* for *task* on *profile* — the single rating path for all callers."""
    spec = find_spec(tag)
    model_uses = catalog_uses(spec) if spec is not None else detected_uses
    return ModelRating(
        tag=tag,
        task=task,
        model_uses=model_uses,
        suitability=suitability(tag, task, profile, detected_uses=detected_uses),
        memory_gb=spec.memory_gb if spec is not None else None,
        note=spec.note if spec is not None else "",
    )


def models_for(
    category: ModelCategory, profile: HardwareProfile
) -> list[tuple[OllamaModelSpec, Suitability]]:
    """Return all catalog models of *category* with their suitability for *profile*.

    Sorted by suitability (BEST → GOOD → WORKS → TOO_LARGE), then by tag name.
    """
    result = [
        (spec, suitability(spec.tag, category, profile))
        for spec in OLLAMA_CATALOG
        if category in catalog_uses(spec)
    ]
    result.sort(key=lambda pair: (_SUITABILITY_RANK[pair[1]], pair[0].tag))
    return result


# ── hardware profile detection ────────────────────────────────────────────────


class DetectionReason(StrEnum):
    """Why a hardware profile was chosen — drives the GUI status text."""

    MANUAL = "manual"
    GPU_DETECTED = "gpu_detected"
    APPLE_UNIFIED_MEMORY = "apple_unified_memory"
    NO_GPU_FOUND = "no_gpu_found"
    GPU_MEMORY_UNKNOWN = "gpu_memory_unknown"
    REMOTE_SERVER = "remote_server"


@dataclass(frozen=True)
class HardwareDetection:
    """The resolved hardware profile plus everything needed to explain it."""

    profile: HardwareProfile
    reason: DetectionReason
    #: Name of the GPU that decided the profile, when one was found.
    gpu_name: str | None = None
    #: What the user's computer *has*, in GB: dedicated graphics memory of the
    #: deciding GPU, or the total shared memory on Apple Silicon.  The profile is
    #: derived from this value (Apple scales it by :data:`APPLE_GPU_MEMORY_SHARE`
    #: first), but it is never replaced by the scaled figure.
    memory_gb: float | None = None
    #: Host name of the Ollama server for REMOTE_SERVER, otherwise ``None``.
    host: str | None = None


@dataclass(frozen=True)
class LocalHardware:
    """Raw detection results for *this* computer (independent of any Ollama URL)."""

    #: ``(name, memory_gb_or_None)`` per detected GPU.
    gpus: tuple[tuple[str, float | None], ...] = ()
    #: Total unified memory (GB) on Apple Silicon, ``None`` elsewhere.
    apple_unified_memory_gb: float | None = None


def _profile_for_memory_gb(memory_gb: float) -> HardwareProfile:
    """Map a known GPU memory size to a profile using the plan thresholds."""
    if memory_gb >= GPU_16GB_THRESHOLD_GB:
        return "16gb"
    if memory_gb >= GPU_8GB_THRESHOLD_GB:
        return "8gb"
    return "cpu"


def choose_profile(
    *,
    gpus: Sequence[tuple[str, float | None]],
    apple_unified_memory_gb: float | None,
    ollama_is_remote: bool,
    host: str | None = None,
) -> HardwareDetection:
    """Derive the hardware profile from raw detection results.

    Rules (first match wins):

    1. Ollama runs on another computer → ``8gb`` / :attr:`DetectionReason.REMOTE_SERVER`
       (the local GPU is irrelevant; 8 GB is the most common home-server setup).
    2. At least one GPU with known memory → the **largest** single GPU decides:
       ≥ 15 GB → ``16gb``, ≥ 7 GB → ``8gb``, otherwise ``cpu``.
    3. GPU(s) found, but no memory value → ``8gb`` / ``GPU_MEMORY_UNKNOWN``.
    4. Apple Silicon: effective GPU share = 0.66 x RAM, same thresholds; the
       reported memory is the total RAM.
    5. Nothing found → ``cpu`` / :attr:`DetectionReason.NO_GPU_FOUND`.

    Args:
        gpus: ``(name, memory_gb_or_None)`` per detected GPU.
        apple_unified_memory_gb: Total unified memory (GB) on Apple Silicon, or ``None``.
        ollama_is_remote: True when the configured Ollama URL is not this machine.
        host: Host name of the remote Ollama server (only used for REMOTE_SERVER).
    """
    if ollama_is_remote:
        return HardwareDetection(profile="8gb", reason=DetectionReason.REMOTE_SERVER, host=host)

    known = [(name, mem) for name, mem in gpus if mem is not None]
    if known:
        name, mem = max(known, key=lambda pair: pair[1])
        return HardwareDetection(
            profile=_profile_for_memory_gb(mem),
            reason=DetectionReason.GPU_DETECTED,
            gpu_name=name,
            memory_gb=mem,
        )

    if gpus:
        return HardwareDetection(
            profile="8gb",
            reason=DetectionReason.GPU_MEMORY_UNKNOWN,
            gpu_name=gpus[0][0],
            memory_gb=None,
        )

    if apple_unified_memory_gb is not None:
        # The profile uses the share macOS gives the GPU, but the detection
        # reports the memory the computer actually has ("16 GB", not "10.6 GB").
        effective = APPLE_GPU_MEMORY_SHARE * apple_unified_memory_gb
        return HardwareDetection(
            profile=_profile_for_memory_gb(effective),
            reason=DetectionReason.APPLE_UNIFIED_MEMORY,
            memory_gb=apple_unified_memory_gb,
        )

    return HardwareDetection(profile="cpu", reason=DetectionReason.NO_GPU_FOUND)
