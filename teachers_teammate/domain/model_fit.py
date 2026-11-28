"""How well a model suits a computer: memory fit, speed, rank and the recommended picks."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from .hardware_profile import (
    CPU_COMFORT_GB,
    FULL_SPEED_SHARE,
    HardwareProfile,
    card_budget_gb,
    card_share,
    is_very_slow,
    system_reserve_gb,
)
from .model_catalog import (
    DEFAULT_OCR_MODEL,
    OLLAMA_CATALOG,
    ModelCategory,
    ModelUses,
    OllamaModelSpec,
    find_spec,
)
from .model_tags import normalize_tag
from .performance_estimate import SpeedBand, SpeedSource, expected_band


class ModelRole(StrEnum):
    """Which of a writing tier's two picks a model is."""

    QUALITY = "quality"
    """The tier's best model: the most accurate one that runs on it."""

    SPEED = "speed"
    """The tier's faster alternative, for a little less accuracy."""


#: The writing picks per profile, one model per role.  Chosen by hand by the rule in
#: ``evaluations/README.md``: the speed model is at most SPEED_ROLE_MAX_F05_GAP behind the
#: quality model and at least SPEED_ROLE_FASTER_SHARE quicker.  gemma4:12b is the most accurate
#: model; on an 8 GB card a page still takes about a minute.  gemma4:e2b ties with gemma4:e4b in
#: quality at half its time.  Without a card it is the quality model and there is no speed
#: model: nothing that runs there is faster.
WRITING_PICKS: dict[HardwareProfile, dict[ModelRole, str]] = {
    HardwareProfile.CPU: {ModelRole.QUALITY: "gemma4:e2b"},
    HardwareProfile.CARD_8GB: {ModelRole.QUALITY: "gemma4:12b", ModelRole.SPEED: "gemma4:e2b"},
    HardwareProfile.CARD_16GB: {ModelRole.QUALITY: "gemma4:12b", ModelRole.SPEED: "gemma4:e2b"},
}


#: Ordered recommendations per category and profile: the first entry is ``BEST``,
#: the others are ``GOOD``.  Every list must be non-empty and every tag must
#: exist in :data:`OLLAMA_CATALOG` with the matching category (tests enforce this).
#: The writing lists are :data:`WRITING_PICKS` in role order; on a given computer
#: :func:`writing_picks` decides which of them hold.
RECOMMENDED: dict[ModelCategory, dict[HardwareProfile, tuple[str, ...]]] = {
    "ocr": {
        HardwareProfile.CPU: ("glm-ocr:q8_0",),
        # 16 GB reading list intentionally equals the 8 GB list until a larger
        # vision model has been validated on a teacher's handwriting.
        HardwareProfile.CARD_8GB: (
            DEFAULT_OCR_MODEL,
            "qwen3-vl:8b",
            "deepseek-ocr:latest",
            "qwen3.5:9b",
            "glm-ocr:q8_0",
        ),
        HardwareProfile.CARD_16GB: (
            DEFAULT_OCR_MODEL,
            "qwen3-vl:8b",
            "deepseek-ocr:latest",
            "qwen3.5:9b",
            "glm-ocr:q8_0",
        ),
    },
    "general": {
        profile: tuple(picks[role] for role in ModelRole if role in picks)
        for profile, picks in WRITING_PICKS.items()
    },
}


#: How much worse a speed model may be than its tier's quality model, in F0.5: a tenth of a point.
#: A model further behind trades more accuracy for its speed than a teacher can be asked to accept
#: to save a minute a page.
SPEED_ROLE_MAX_F05_GAP = 0.10


#: How much faster than the quality model a speed model has to be, as a share of the quality model's
#: page time: a quarter.  A smaller gain is no second role — the tier has one model then.
SPEED_ROLE_FASTER_SHARE = 0.25


def _pick_holds(
    tag: str, profile: HardwareProfile, system_memory_gb: float | None, card_gb: float | None
) -> bool:
    """Return whether the pick *tag* runs well enough on this computer to keep its role.

    It must not be too large for the main memory (checked only when that is known), and a model
    that spills off the card must not spill so far that it runs very slowly.
    """
    spec = find_spec(tag)
    if spec is None:
        return True
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    if (
        system_memory_gb is not None
        and _memory_fit(spec, card, system_memory_gb) is Suitability.TOO_LARGE
    ):
        return False
    return not (card > 0 and is_very_slow(spec.memory_gb, card))


def writing_picks(
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> dict[ModelRole, str]:
    """Return the writing picks that hold on this computer, by role.

    A tier's quality model that does not hold here hands its role to the speed model, and the
    computer then has no speed model.  Empty when no pick holds.
    """
    listed = WRITING_PICKS[profile]
    quality = listed[ModelRole.QUALITY]
    speed = listed.get(ModelRole.SPEED)
    if speed is not None and not _pick_holds(speed, profile, system_memory_gb, card_gb):
        speed = None
    if not _pick_holds(quality, profile, system_memory_gb, card_gb):
        return {ModelRole.QUALITY: speed} if speed is not None else {}
    if speed is None:
        return {ModelRole.QUALITY: quality}
    return {ModelRole.QUALITY: quality, ModelRole.SPEED: speed}


def role_of(
    tag: str,
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> ModelRole | None:
    """Return the writing role *tag* plays on this computer, or ``None`` when it plays none."""
    wanted = normalize_tag(tag)
    picks = writing_picks(profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
    return next((role for role, named in picks.items() if normalize_tag(named) == wanted), None)


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


#: Free disk space (GB) kept beyond the downloads, so the computer keeps room to work.
DISK_MARGIN_GB = 2.0


class DiskVerdict(StrEnum):
    """Whether the downloads fit on the disk Ollama keeps its models on."""

    ENOUGH = "enough"
    NOT_ENOUGH = "not_enough"
    #: The free space could not be read, or the downloads are not known.
    UNKNOWN = "unknown"


def disk_verdict(needed_gb: float, free_gb: float | None) -> DiskVerdict:
    """Return whether *needed_gb* of downloads fit in *free_gb* of disk space.

    A warning only: Ollama's own settings can move the models folder, and the app cannot see
    that, so a "not enough" is a reason to look, not a refusal.  The margin keeps the computer
    from filling up to the last byte.
    """
    if free_gb is None or needed_gb <= 0:
        return DiskVerdict.UNKNOWN
    return DiskVerdict.ENOUGH if free_gb >= needed_gb + DISK_MARGIN_GB else DiskVerdict.NOT_ENOUGH


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


def best_model(
    category: ModelCategory,
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> str:
    """Return the BEST model tag for *category* on *profile*.

    For writing, the quality model of :func:`writing_picks`.  For reading, the first model of
    the profile's list, unless the computer's main memory is known and that model is too large
    for it: then the first one of the list that fits.  When none fits, the first of the list,
    as without a figure.
    """
    recommended = RECOMMENDED[category][profile]
    if category == "general":
        picks = writing_picks(profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
        return picks.get(ModelRole.QUALITY, recommended[0])
    if system_memory_gb is not None:
        card = card_gb if card_gb is not None else card_budget_gb(profile)
        for tag in recommended:
            spec = find_spec(tag)
            if spec is None:
                continue
            if _memory_fit(spec, card, system_memory_gb) is not Suitability.TOO_LARGE:
                return tag
    return recommended[0]


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
    3. Writing: the quality model of :func:`writing_picks` → ``BEST``, its speed model →
       ``GOOD``; a tier pick that does not hold here is rated by its fit like any other.
       Reading: ``RECOMMENDED["ocr"][profile][0]`` → ``BEST``, elsewhere in that list →
       ``GOOD``, unless it is too large for a known main memory.
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
    if category == "general":
        role = role_of(tag, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
        if role is None:
            return fit
        return Suitability.BEST if role is ModelRole.QUALITY else Suitability.GOOD
    recommended = RECOMMENDED[category][profile]
    if normalized in recommended:
        # A recommendation holds for the computer type; it does not hold where the model
        # cannot work.  Without a figure for the main memory the type's list is all there is.
        if fit is Suitability.TOO_LARGE and system_memory_gb is not None:
            return Suitability.TOO_LARGE
        best = best_model(category, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
        return Suitability.BEST if normalized == normalize_tag(best) else Suitability.GOOD
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
    #: The speed band this model is expected to reach here; ``None`` when it was not worked out.
    speed: SpeedBand | None = None
    #: Where :attr:`speed` comes from; ``None`` when there is no band.
    speed_source: SpeedSource | None = None
    #: The writing role the model plays on this computer (see :func:`writing_picks`); ``None``
    #: for a model that plays none, and for every reading model.
    role: ModelRole | None = None


#: Ratings that get no expected speed.  A model that spills to the processor is slower by an
#: amount the machine factor cannot predict, one that does not fit is not a download to
#: consider, and one of the wrong kind is not used for this task at all.
_NO_EXPECTED_SPEED: tuple[Suitability, ...] = (
    Suitability.SLOW,
    Suitability.TOO_LARGE,
    Suitability.WRONG_KIND,
)


def rate_model(
    tag: str,
    task: ModelCategory,
    profile: HardwareProfile,
    *,
    detected_uses: ModelUses | None = None,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
    machine_factor: float | None = None,
) -> ModelRating:
    """Rate *tag* for *task* on *profile* — the single rating path for all callers.

    *machine_factor* is how much slower this computer is than the reference machine
    (:class:`~.performance_estimate.MachineFactors`); with it, a model that was never measured
    here gets the speed band it is expected to reach, so a teacher can judge a download.
    """
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
    speed, speed_source = _expected_speed(spec, rated, machine_factor)
    return ModelRating(
        tag=tag,
        task=task,
        model_uses=model_uses,
        suitability=rated,
        memory_gb=spec.memory_gb if spec is not None else None,
        note=spec.note if spec is not None else "",
        structured_output=spec.structured_output if spec is not None else None,
        very_slow=very_slow,
        speed=speed,
        speed_source=speed_source,
        role=(
            role_of(tag, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
            if task == "general"
            else None
        ),
    )


def _expected_speed(
    spec: OllamaModelSpec | None,
    rated: Suitability,
    machine_factor: float | None,
) -> tuple[SpeedBand | None, SpeedSource | None]:
    """Return the band *spec* is expected to reach here, and where it comes from.

    ``(None, None)`` when there is nothing to say: no catalog entry, no reference speed, no
    measured model on this server, or a rating that makes the figure meaningless.
    """
    if spec is None or machine_factor is None or rated in _NO_EXPECTED_SPEED:
        return None, None
    speed = expected_band(spec, machine_factor)
    if speed is None:
        return None, None
    return speed, SpeedSource.EXPECTED


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
