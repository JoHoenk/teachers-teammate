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

#: Share of a graphics card's memory that models can use: the rest holds the operating
#: system's own windows and the driver's buffers (measured on an 8 GB card, where a model, its
#: context and the driver's buffers used 6.9 of 8.2 GB).
CARD_USABLE_SHARE = 0.85

#: Memory (GB) of the graphics card a hardware profile stands for when the real size is unknown
#: (a profile chosen by hand, a graphics card whose memory could not be read, a remote server).
NOMINAL_CARD_GB: dict[HardwareProfile, float] = {"cpu": 0.0, "8gb": 8.0, "16gb": 16.0}

#: Largest model (GB) that is comfortable on the processor alone, without a graphics card.
CPU_COMFORT_GB = 4.0

#: Main memory kept free for the operating system and the app: the larger of this many GB and
#: ``SYSTEM_RESERVE_SHARE`` of the total.  Swapping to disk is never counted as memory.
SYSTEM_RESERVE_MIN_GB = 8.0
SYSTEM_RESERVE_SHARE = 0.20

#: Share of a model that sits on the graphics card, from which on it runs at full speed, and
#: below which it is "very slow" (measured on an 8 GB card: a share of 0.97 gave 43 tokens per
#: second, 0.73 gave 13, 0.66 gave 6.6; a page takes under a minute at the top and about seven
#: minutes at the bottom).  Between the two it is "slower".
FULL_SPEED_SHARE = 0.9
SLOWER_SHARE = 0.7

#: Free main memory (GB) a run keeps beyond what the model needs from it, so the computer stays
#: responsive.  Checked against the memory that is free *now*, not the total.
RUN_MEMORY_MARGIN_GB = 2.0

#: Static default models (config DEFAULTS, CLI, provider metadata).  They apply
#: regardless of hardware; the per-profile picks in :data:`RECOMMENDED` only drive
#: the hardware-aware GUI recommendations.
DEFAULT_OCR_MODEL = "hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0"
#: The default writing model must be one that passes the structured-output check:
#: proofreading explains every change, which needs an answer the model can be constrained to.
DEFAULT_WRITING_MODEL = "gemma4:e4b"

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
    #: "ocr" (reading model) or "general" (writing model).  Settings → Add-ons
    #: lists the model in this group.
    category: ModelCategory
    #: Memory (GB) the model needs in total while running at the app's proofreading window of
    #: 8192 tokens: weights, context and working buffers, on the card and in main memory
    #: together.  Measured where a model was run (see ``docs/development.md``), otherwise estimated.
    memory_gb: float
    #: Approximate download size (GB), shown before the model is pulled.
    download_gb: float
    #: Plain-language one-liner shown as tooltip.
    note: str
    #: Further tasks the model is verified for besides ``category`` (a writing
    #: model that can also read images).
    also_uses: ModelUses = frozenset()
    #: Whether the model was checked to answer the correction review's JSON schema reliably
    #: (``tools/verify_model_catalog.py --structured-output``).  It only decides the
    #: ``explanations ✓`` badge; it never switches the proofreading mode.
    structured_output: bool = False
    #: Extra memory (GB) per additional 8192 tokens of context window, measured with
    #: ``ollama ps`` at 8192 and 16384 tokens (see ``docs/development.md``).  ``None`` — not
    #: measured, or a model the user added — means the window stays at 8192 tokens.  It cannot
    #: be derived from ``memory_gb``: how fast a context cache grows depends on the model's
    #: attention design (sliding-window or linear-attention layers grow much more slowly).
    context_gb_per_8k: float | None = None


# Download sizes (decimal GB) were checked against the Ollama registry manifests
# with ``tools/verify_model_catalog.py`` on 2026-10-12 (the Hugging Face entry
# against the repository file list).  ``memory_gb`` cannot be read from a manifest.
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
        memory_gb=4.0,
        download_gb=2.5,
        note="Small writing model for computers without a graphics card.",
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="qwen3.5:9b",
        category="general",
        memory_gb=7.2,
        download_gb=6.6,
        note="Writing model for 8 GB and 16 GB graphics cards. Can also read scanned pages.",
        also_uses=frozenset({"ocr"}),
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="gemma4:e4b",
        category="general",
        memory_gb=5.0,
        download_gb=6.6,
        note="Fast writing model that fits an 8 GB graphics card.",
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="gemma4:12b",
        category="general",
        memory_gb=9.5,
        download_gb=8.0,
        note="Best writing model for a 16 GB graphics card.",
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="mistral:latest",
        category="general",
        memory_gb=5.5,
        download_gb=4.4,
        note="General-purpose writing model.",
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="mistral-nemo:12b",
        category="general",
        memory_gb=8.5,
        download_gb=7.1,
        note="General-purpose writing model.",
        structured_output=True,
    ),
    OllamaModelSpec(
        tag="gpt-oss:20b",
        category="general",
        memory_gb=14.0,
        download_gb=13.8,
        note="Large reasoning model; its answers do not fit the proofreading format.",
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
        "8gb": ("gemma4:e4b", "qwen3.5:9b"),
        "16gb": ("gemma4:12b", "qwen3.5:9b"),
    },
}


class Suitability(StrEnum):
    """How well a model fits a task on a given hardware profile."""

    BEST = "best"
    GOOD = "good"
    WORKS = "works"
    #: Too big for the graphics card, but fits with the help of the computer's memory.
    SLOW = "slow"
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


def system_reserve_gb(system_memory_gb: float) -> float:
    """Return the main memory (GB) kept free for the system: the larger of 8 GB and 20 %."""
    return max(SYSTEM_RESERVE_MIN_GB, SYSTEM_RESERVE_SHARE * system_memory_gb)


def card_budget_gb(profile: HardwareProfile) -> float:
    """Return the graphics memory (GB) models can use when the real size is not known.

    The size the profile stands for (:data:`NOMINAL_CARD_GB`), times
    :data:`CARD_USABLE_SHARE`.  A computer typed ``"cpu"`` has no card this figure could
    stand for, so it is 0 — a real card of such a computer arrives as a known ``card_gb``.
    """
    return CARD_USABLE_SHARE * NOMINAL_CARD_GB[profile]


def memory_for_models_gb(
    profile: HardwareProfile,
    system_memory_gb: float | None,
    card_gb: float | None = None,
) -> float | None:
    """Return the memory (GB) a model may need in total on this computer, or ``None``.

    The usable graphics memory plus the main memory that stays after the system's reserve.
    ``None`` when the main memory is unknown (a server on another computer).
    """
    if system_memory_gb is None:
        return None
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    return card + max(0.0, system_memory_gb - system_reserve_gb(system_memory_gb))


def ram_needed_gb(
    memory_gb: float,
    profile: HardwareProfile,
    card_gb: float | None = None,
    *,
    unified: bool = False,
) -> float:
    """Return the main memory (GB) a model of *memory_gb* takes on this computer.

    What does not fit on the graphics card; all of it when there is no card, and on Apple
    Silicon, where the graphics share is carved out of the same memory.
    """
    if unified:
        return memory_gb
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    return max(0.0, memory_gb - card)


def card_share(memory_gb: float, card_gb: float) -> float:
    """Return the share (0 to 1) of a model of *memory_gb* that fits on a card of *card_gb*."""
    return min(1.0, card_gb / memory_gb) if memory_gb > 0 else 1.0


def is_very_slow(memory_gb: float, card_gb: float) -> bool:
    """Return whether a model that does not fit the card runs very slowly (see below 0.7)."""
    return card_share(memory_gb, card_gb) < SLOWER_SHARE


def _memory_fit(
    spec: OllamaModelSpec,
    card: float,
    system_memory_gb: float | None,
) -> Suitability:
    """Return ``WORKS``, ``SLOW`` or ``TOO_LARGE`` for *spec* by the computer's memory alone."""
    spill = spec.memory_gb - card
    # What the processor must hold has to fit in the main memory left after the reserve.
    holds = system_memory_gb is not None and spill <= system_memory_gb - system_reserve_gb(
        system_memory_gb
    )
    # The card decides how fast a model is, not the profile: a computer typed "cpu" whose
    # card is known runs a model on that card just like an "8gb" computer does.
    if card > 0:
        comfortable = card_share(spec.memory_gb, card) >= FULL_SPEED_SHARE
    else:
        comfortable = spec.memory_gb <= CPU_COMFORT_GB
    if comfortable and (holds or system_memory_gb is None or card > 0):
        return Suitability.WORKS
    return Suitability.SLOW if holds else Suitability.TOO_LARGE


def suitability(
    tag: str,
    category: ModelCategory,
    profile: HardwareProfile,
    *,
    detected_uses: ModelUses | None = None,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
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
    5. At least :data:`FULL_SPEED_SHARE` of the model fits on the graphics card (for a
       computer without a card: the model is at most :data:`CPU_COMFORT_GB`) → ``WORKS``.
    6. The part that does not fit (the *spill*) runs on the processor in main memory.  When it
       fits into the main memory that remains after the system's reserve
       (:func:`system_reserve_gb`) → :attr:`Suitability.SLOW`; swapping to disk is never counted.
       Otherwise, and whenever the main memory is unknown (a server on another computer),
       ``TOO_LARGE``: the model does not work on this computer.

    Args:
        tag: Model tag (normalisation is applied internally).
        category: Task category: ``"ocr"`` for reading, ``"general"`` for writing.
        profile: Hardware profile of the computer that runs the model.
        detected_uses: Uses reported by the Ollama server for models that are not
            in the catalog (see :func:`uses_from_capabilities`).  ``None`` means
            "unknown".
        system_memory_gb: Total main memory (RAM) of the computer, or ``None`` when unknown
            (a server on another computer) or already part of the card's memory (Apple Silicon,
            where the graphics share is carved out of the same memory); without it nothing
            is ``SLOW``.
        card_gb: Graphics memory the models can use, when it is known (the real card size times
            :data:`CARD_USABLE_SHARE`); ``None`` takes the profile's nominal size.
    """
    spec = find_spec(tag)
    uses = catalog_uses(spec) if spec is not None else detected_uses
    if uses is None:
        return Suitability.UNKNOWN
    if category not in uses:
        return Suitability.WRONG_KIND

    normalized = normalize_tag(tag)
    if spec is None:
        # Custom model: its size is unknown, so it is never called too large.
        recommended = RECOMMENDED[category][profile]
        if normalized == recommended[0]:
            return Suitability.BEST
        return Suitability.GOOD if normalized in recommended else Suitability.WORKS

    card = card_gb if card_gb is not None else card_budget_gb(profile)
    fit = _memory_fit(spec, card, system_memory_gb)
    recommended = RECOMMENDED[category][profile]
    if normalized in recommended:
        # A recommendation holds for the computer type; it does not hold where the model
        # cannot work.  Without a figure for the main memory the type's list is all there is.
        if fit is Suitability.TOO_LARGE and system_memory_gb is not None:
            return Suitability.TOO_LARGE
        return Suitability.BEST if normalized == recommended[0] else Suitability.GOOD
    return fit


#: Sort rank of suitability values (BEST first, WRONG_KIND last).
_SUITABILITY_RANK: dict[Suitability, int] = {
    Suitability.BEST: 0,
    Suitability.GOOD: 1,
    Suitability.WORKS: 2,
    Suitability.SLOW: 3,
    Suitability.TOO_LARGE: 4,
    Suitability.UNKNOWN: 5,
    Suitability.WRONG_KIND: 6,
}


def suitability_rank(value: Suitability) -> int:
    """Return the sort rank of *value* (BEST = 0 … WRONG_KIND = 6)."""
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
    #: ``True`` when the model is verified to answer the review's JSON schema,
    #: ``False`` when it is known not to, ``None`` when the model is not catalogued.
    structured_output: bool | None = None
    #: For a ``SLOW`` model: whether so little of it fits on the graphics card that it runs very
    #: slowly (rather than somewhat slower).
    very_slow: bool = False


def rate_model(
    tag: str,
    task: ModelCategory,
    profile: HardwareProfile,
    *,
    detected_uses: ModelUses | None = None,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> ModelRating:
    """Rate *tag* for *task* on *profile* — the single rating path for all callers."""
    spec = find_spec(tag)
    model_uses = catalog_uses(spec) if spec is not None else detected_uses
    rated = suitability(
        tag,
        task,
        profile,
        detected_uses=detected_uses,
        system_memory_gb=system_memory_gb,
        card_gb=card_gb,
    )
    very_slow = False
    if rated is Suitability.SLOW and spec is not None:
        card = card_gb if card_gb is not None else card_budget_gb(profile)
        very_slow = is_very_slow(spec.memory_gb, card)
    return ModelRating(
        tag=tag,
        task=task,
        model_uses=model_uses,
        suitability=rated,
        memory_gb=spec.memory_gb if spec is not None else None,
        note=spec.note if spec is not None else "",
        structured_output=spec.structured_output if spec is not None else None,
        very_slow=very_slow,
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
    #: Memory (RAM) of the computer that runs the models, in GB; ``None`` when unknown
    #: (a server on another computer) or already counted in the profile (Apple Silicon).  Lets
    #: models too big for the graphics card be rated "slower" instead of "too large".
    system_memory_gb: float | None = None

    @property
    def card_gb(self) -> float | None:
        """Graphics memory (GB) models can use on this computer, or ``None`` when not known.

        ``None`` (a profile chosen by hand, a card whose memory could not be read, a remote
        server) makes the rating assume the size the profile stands for.  Apple Silicon uses
        the share of the unified memory macOS gives the graphics side.  A card smaller than
        7 GB gives the profile ``"cpu"``, but the card is still there and still decides how
        fast a model runs, so its size is reported whatever the profile.
        """
        if self.memory_gb is None:
            return None
        if self.reason is DetectionReason.APPLE_UNIFIED_MEMORY:
            return self.memory_gb * APPLE_GPU_MEMORY_SHARE
        if self.reason is DetectionReason.GPU_DETECTED:
            return self.memory_gb * CARD_USABLE_SHARE
        return None


@dataclass(frozen=True)
class LocalHardware:
    """Raw detection results for *this* computer (independent of any Ollama URL)."""

    #: ``(name, memory_gb_or_None)`` per detected GPU.
    gpus: tuple[tuple[str, float | None], ...] = ()
    #: Total unified memory (GB) on Apple Silicon, ``None`` elsewhere.
    apple_unified_memory_gb: float | None = None
    #: Memory (RAM) of this computer in GB, ``None`` when it could not be read.
    system_memory_gb: float | None = None


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
    system_memory_gb: float | None = None,
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
        system_memory_gb: Memory (RAM) of this computer, carried into the result; it says
            nothing about a remote server and is ignored for it.  Apple Silicon ignores it
            too: its graphics share is part of that same memory, so it cannot be offloaded
            into a second time.
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
            system_memory_gb=system_memory_gb,
        )

    if gpus:
        return HardwareDetection(
            profile="8gb",
            reason=DetectionReason.GPU_MEMORY_UNKNOWN,
            gpu_name=gpus[0][0],
            memory_gb=None,
            system_memory_gb=system_memory_gb,
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

    return HardwareDetection(
        profile="cpu", reason=DetectionReason.NO_GPU_FOUND, system_memory_gb=system_memory_gb
    )
