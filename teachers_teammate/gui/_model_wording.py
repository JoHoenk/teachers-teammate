"""User-facing wording for hardware profiles and model recommendations.

Qt-free on purpose: every text a teacher sees about "My computer", model
suitability and download warnings lives here, so the wording can be reviewed,
unit-tested and — later — translated in one place.  Widgets import these
helpers; they never build such sentences themselves.

All texts assume no technical background: no "VRAM", no "quantisation", no
parameter counts.
"""

from __future__ import annotations

from datetime import date

from ..domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelUses,
    Suitability,
    card_budget_gb,
    memory_for_models_gb,
    system_reserve_gb,
)
from ._user_text import WHERE_AI_SETUP

# ── "My computer" ─────────────────────────────────────────────────────────────

PROFILE_TEXT: dict[str, str] = {
    "auto": "Detect automatically (recommended)",
    "cpu": "Basic computer or small graphics card (under 8 GB)",
    "8gb": "Graphics card with about 8 GB memory",
    "16gb": "Graphics card with 16 GB memory or more",
}

#: Graphics memory (GB) that computers of a profile are assumed to have.
_TIER_MEMORY_GB: dict[str, int] = {"8gb": 8, "16gb": 16}

CHECKING_COMPUTER = "Checking your computer…"
NO_SUGGESTION_FOR_COMPUTER = "No suggestion available for this computer."
#: Shown when the check itself failed, so it is not mistaken for "nothing to suggest".
CHECK_COMPUTER_FAILED = (
    "Your computer could not be checked (details are in the log), so suggestions assume "
    "a graphics card with about 8 GB. If you know what your computer has, choose it in the list."
)
READ_ONLY_PROFILE_TOOLTIP = f"You can change this under {WHERE_AI_SETUP}."


def profile_text(value: str) -> str:
    """Return the UI text for a hardware profile or profile setting."""
    return PROFILE_TEXT.get(value, value)


def read_only_profile_text(profile: str) -> str:
    """Return the line shown where the computer type cannot be edited."""
    return f"Suggestions are for: {profile_text(profile)}"


def auto_profile_text(profile: str) -> str:
    """Return the label of the "automatic" choice once a profile was resolved."""
    return f"{PROFILE_TEXT['auto']} — {profile_text(profile)}"


#: Shown once under "My computer" when the computer cannot run a local model at all.
TOO_LITTLE_MEMORY = (
    "This computer has too little memory for AI models on this computer. Use the built-in "
    "reader for reading, or Ollama on another computer."
)


def _computer_memory_sentence(detection: HardwareDetection) -> str:
    """Return `` Computer memory: N GB.`` when the memory is known (it rates big models)."""
    if detection.system_memory_gb is None:
        return ""
    return f" Computer memory: {detection.system_memory_gb:.0f} GB."


def _too_little_memory(detection: HardwareDetection) -> bool:
    """Return whether this computer cannot hold a model next to its system's reserve.

    Without a graphics card, the reserve alone (the larger of 8 GB and 20 %) is all the main
    memory there is, so *every* local model is "Won't fit".  Said once, in plain words.
    """
    memory = detection.system_memory_gb
    if memory is None:
        return False
    card = detection.card_gb
    if card is not None and card > 0:
        return False
    return memory <= system_reserve_gb(memory)


def detection_status_text(detection: HardwareDetection) -> str:
    """Return the status line shown below the "My computer" choice."""
    if _too_little_memory(detection):
        return f"{_detection_summary(detection)} {TOO_LITTLE_MEMORY}"
    return _detection_summary(detection) + _computer_memory_sentence(detection)


def _detection_summary(detection: HardwareDetection) -> str:
    memory = detection.memory_gb
    if detection.reason is DetectionReason.GPU_DETECTED and memory is not None:
        return f"Found: {detection.gpu_name} with {memory:.0f} GB graphics memory."
    if detection.reason is DetectionReason.APPLE_UNIFIED_MEMORY and memory is not None:
        return f"Found: Apple computer with {memory:.0f} GB shared memory."
    if detection.reason is DetectionReason.NO_GPU_FOUND:
        return (
            "No graphics card found. Smaller models are suggested — they are slower but "
            "work everywhere. If you do have a graphics card, choose it in the list."
        )
    if detection.reason is DetectionReason.GPU_MEMORY_UNKNOWN:
        return (
            f"Found: {detection.gpu_name}. Its memory size could not be read, "
            "so suggestions assume about 8 GB."
        )
    if detection.reason is DetectionReason.REMOTE_SERVER:
        return (
            f"Ollama runs on another computer ({detection.host}). Suggestions assume "
            "a graphics card with about 8 GB. If you know what that computer has, "
            "choose it in the list."
        )
    return "Chosen by you."


# ── suitability ───────────────────────────────────────────────────────────────

_SUITABILITY_LABELS: dict[Suitability, str] = {
    Suitability.BEST: "★ Best choice",
    Suitability.GOOD: "Good choice",
    Suitability.WORKS: "Works",
    Suitability.SLOW: "Works, but slower",
    Suitability.UNKNOWN: "No suggestion available",
}
_VERY_SLOW_LABEL = "Works, but very slowly"

#: WRONG_KIND depends on the task: a writing model cannot read scans, and a
#: reading model is not meant for proofreading or review.
_WRONG_KIND_LABELS: dict[ModelCategory, str] = {
    "ocr": "Cannot read scans",
    "general": "Reading model only",
}


#: Compact labels for the narrow Suitability column of the downloads list; the
#: full wording (``suitability_text``) is kept for combo boxes and tooltips.
_COMPACT_LABELS: dict[Suitability, str] = {
    Suitability.BEST: "★ Best",
    Suitability.GOOD: "Good",
    Suitability.WORKS: "Works",
    Suitability.SLOW: "Slower",
    Suitability.UNKNOWN: "No suggestion",
}
_COMPACT_VERY_SLOW_LABEL = "Very slow"
_COMPACT_WRONG_KIND_LABELS: dict[ModelCategory, str] = {
    "ocr": "Can't read scans",
    "general": "Reading only",
}


def suitability_text(
    value: Suitability,
    task: ModelCategory,
    *,
    very_slow: bool = False,
) -> str:
    """Return the short label for a suitability value of a *task* model."""
    if value is Suitability.TOO_LARGE:
        return "Does not fit this computer"
    if value is Suitability.WRONG_KIND:
        return _WRONG_KIND_LABELS[task]
    if value is Suitability.SLOW and very_slow:
        return _VERY_SLOW_LABEL
    return _SUITABILITY_LABELS[value]


def compact_suitability_text(
    value: Suitability,
    task: ModelCategory,
    *,
    very_slow: bool = False,
) -> str:
    """Return the compact label for the downloads list (the tooltip has the details)."""
    if value is Suitability.TOO_LARGE:
        return "Won't fit"
    if value is Suitability.WRONG_KIND:
        return _COMPACT_WRONG_KIND_LABELS[task]
    if value is Suitability.SLOW and very_slow:
        return _COMPACT_VERY_SLOW_LABEL
    return _COMPACT_LABELS[value]


def abilities_text(uses: ModelUses | None) -> str:
    """Return what a model that is not in the catalog can be used for."""
    if uses is None:
        return _COMPACT_LABELS[Suitability.UNKNOWN]
    if "ocr" in uses:
        return "Reads & writes"
    return "Writes only"


def memory_text(memory_gb: float) -> str:
    """Return the memory column text (e.g. ``"~7 GB"``)."""
    return f"~{memory_gb:g} GB"


def _gb(value: float) -> str:
    """Return *value* in whole or half gigabytes, rounded down so a claim never overstates."""
    return f"{int(value * 2) / 2:g} GB"


def too_large_text(
    memory_gb: float | None,
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> str:
    """Explain why a model does not fit this computer (tooltip, hint and confirmation).

    With the computer's memory known, the numbers are given: what the model needs, what the
    computer can offer it (graphics memory plus main memory) and what stays free for the system.
    A model that does not fit would only run by swapping to disk, which makes it unusable.
    """
    need = f"This model needs about {memory_gb:g} GB of memory" if memory_gb is not None else ""
    card = card_gb if card_gb is not None else card_budget_gb(profile)
    if system_memory_gb is not None and memory_gb is not None:
        offer = memory_for_models_gb(profile, system_memory_gb, card_gb=card)
        assert offer is not None  # the main memory is known here
        parts = "main memory" if card <= 0 else "graphics memory and main memory"
        return (
            f"{need}. This computer can offer about {_gb(offer)} ({parts}); "
            f"{_gb(system_reserve_gb(system_memory_gb))} of its {system_memory_gb:.0f} GB main "
            "memory stay free for the system. The model does not fit, so it would not run "
            "properly."
        )
    if card <= 0:
        return "This model is too large to run quickly without a graphics card."
    tier = _TIER_MEMORY_GB[profile]
    if memory_gb is None:
        return f"This model may need more memory than the {tier} GB of computers of this type."
    return (
        f"This model needs about {memory_gb:g} GB of graphics memory; "
        f"computers of this type have about {tier} GB."
    )


def slow_text(
    memory_gb: float | None,
    profile: HardwareProfile,
    *,
    card_gb: float | None = None,
    very_slow: bool = False,
) -> str:
    """Explain why a model runs slower: part of it uses the computer's memory (tooltip, hint).

    Whether the text says "without a graphics card" is decided by *card_gb*, not by the
    profile: a computer typed "cpu" whose small card is known still has one.
    """
    need = f"This model needs about {memory_gb:g} GB" if memory_gb is not None else "This model"
    how = (
        "it works, but very slowly: a page can take several minutes"
        if very_slow
        else "it works, but slower"
    )
    if (card_gb if card_gb is not None else card_budget_gb(profile)) <= 0:
        return (
            f"{need}. Without a large graphics card it runs on the processor using your "
            f"computer's memory: {how}."
        )
    return (
        f"{need}, more than the graphics card holds. Part of it runs on the processor using "
        f"your computer's memory: {how}."
    )


def too_large_tooltip(
    memory_gb: float | None,
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> str:
    """Return the tooltip for a TOO_LARGE model row."""
    return too_large_text(memory_gb, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)


def too_large_confirmation(
    tag: str,
    memory_gb: float | None,
    profile: HardwareProfile,
    *,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
) -> str:
    """Return one paragraph of the "download anyway?" confirmation."""
    return (
        f"{tag}: "
        f"{too_large_text(memory_gb, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)}"
    )


# ── hint line under a model box ───────────────────────────────────────────────

HINT_NOT_DOWNLOADED = "This model is not downloaded yet."
HINT_NOT_DOWNLOADED_WITH_LINK = f'{HINT_NOT_DOWNLOADED} <a href="download">Download…</a>'
HINT_NO_SUGGESTION = "No suggestion available for this model."

SAME_MODEL_TIP = (
    "Tip: using the same writing model for proofreading and review is faster, "
    "because the model does not have to be reloaded for every document. Untick "
    '"Use a different model for content review" to use one model for both.'
)

#: Title of the collapsible section holding settings a teacher rarely needs.
EXPERT_SETTINGS = "Expert settings"
TEMPERATURE_TOOLTIP = (
    "How much the model varies its wording from one run to the next.\n"
    "0.0 gives the same result for the same text; higher values allow more variation."
)

# ── while the model list is being fetched ─────────────────────────────────────

ASKING_FOR_MODELS = "Asking {service} which models are installed…"
CHECKING_MODELS = "Checking {count} models for your computer…"
SAVED_MODEL_NOT_OFFERED = "Your saved model {model} is not offered by {service}."

# ── a chosen model the service stops serving ──────────────────────────────────

RETIRING_MODEL = "{model} will be retired on {when} — {service} suggests {replacement}."
RETIRING_MODEL_NO_REPLACEMENT = "{model} will be retired on {when} — {service} names no successor."
#: The link that takes the suggested model instead of the retiring one.
USE_SUGGESTION = "Use it"


def asking_for_models_text(service: str) -> str:
    """Return the status line shown while *service* is being asked for its models."""
    return ASKING_FOR_MODELS.format(service=service)


def checking_models_text(count: int) -> str:
    """Return the status line shown while the fetched models are rated for this computer."""
    return CHECKING_MODELS.format(count=count)


def models_from_text(count: int, service: str) -> str:
    """Return the finished status line: how many models *service* offered."""
    noun = "model" if count == 1 else "models"
    return f"{count} {noun} from {service}"


def saved_model_not_offered_text(model: str, service: str) -> str:
    """Return the note shown when the saved model is missing from *service*'s live list."""
    return SAVED_MODEL_NOT_OFFERED.format(model=model, service=service)


def retiring_model_text(
    model: str, retires_on: date | None, service: str, replacement: str = ""
) -> str:
    """Return the note shown when the chosen model is about to be taken out of service.

    The date is said in full (2026-11-30) because *when* is what a teacher acts on; a model
    with no known end says "soon" rather than a date that would be a lie.
    """
    when = retires_on.isoformat() if retires_on is not None else "soon"
    if replacement:
        return RETIRING_MODEL.format(
            model=model, when=when, service=service, replacement=replacement
        )
    return RETIRING_MODEL_NO_REPLACEMENT.format(model=model, when=when, service=service)


def model_hint(
    value: Suitability,
    profile: HardwareProfile,
    *,
    task: ModelCategory,
    memory_gb: float | None,
    best_for_task: str,
    system_memory_gb: float | None = None,
    card_gb: float | None = None,
    very_slow: bool = False,
) -> str:
    """Return the hint line for a *task* model rated *value* on *profile*.

    *best_for_task* is the model suggested instead when *value* is WRONG_KIND:
    a reading model for ``"ocr"``, a writing model for ``"general"``.
    """
    if value is Suitability.BEST:
        return "★ Best choice for your computer."
    if value is Suitability.GOOD:
        return "Good choice for your computer."
    if value is Suitability.WORKS:
        return "Works on your computer."
    if value is Suitability.SLOW:
        return slow_text(memory_gb, profile, card_gb=card_gb, very_slow=very_slow)
    if value is Suitability.TOO_LARGE:
        text = too_large_text(
            memory_gb, profile, system_memory_gb=system_memory_gb, card_gb=card_gb
        )
        return f"⚠ {text}"
    if value is Suitability.WRONG_KIND:
        if task == "general":
            return (
                "⚠ This is a reading model: it turns scans into text but is not meant "
                f"for proofreading or review. Choose a writing model, for example {best_for_task}."
            )
        return (
            f"⚠ This model cannot read scans. Choose a reading model, for example {best_for_task}."
        )
    return HINT_NO_SUGGESTION


def is_warning(value: Suitability) -> bool:
    """Return whether *value* should be shown as a warning."""
    return value in (Suitability.TOO_LARGE, Suitability.WRONG_KIND)


# ── Add-ons page ──────────────────────────────────────────────────────────

DOWNLOADS_INTRO = (
    "Teacher's Teammate needs two AI models: a reading model that turns your "
    "scans into text, and a writing model that proofreads and reviews the text."
)
BEST_ALREADY_DOWNLOADED = "The best models for your computer are already downloaded."
MODEL_ALREADY_LISTED = "That model is already in the list."
ADD_MODEL_NOTE = "Added models are downloaded when you click Install / Download Selected."
GROUP_READING = "Reading models — turn scans into text"
GROUP_WRITING = "Writing models — proofread and review the text"
GROUP_OTHER = "Other models (added by you)"
