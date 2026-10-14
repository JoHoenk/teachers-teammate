"""User-facing wording for hardware profiles and model recommendations.

Qt-free on purpose: every text a teacher sees about "My computer", model
suitability and download warnings lives here, so the wording can be reviewed,
unit-tested and — later — translated in one place.  Widgets import these
helpers; they never build such sentences themselves.

All texts assume no technical background: no "VRAM", no "quantisation", no
parameter counts.
"""

from __future__ import annotations

from ..domain.model_catalog import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelUses,
    Suitability,
)

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
READ_ONLY_PROFILE_TOOLTIP = (
    "You can change this under Advanced Settings → Connections & API Keys, or in Downloads."
)


def profile_text(value: str) -> str:
    """Return the UI text for a hardware profile or profile setting."""
    return PROFILE_TEXT.get(value, value)


def read_only_profile_text(profile: str) -> str:
    """Return the line shown where the computer type cannot be edited."""
    return f"Suggestions are for: {profile_text(profile)}"


def auto_profile_text(profile: str) -> str:
    """Return the label of the "automatic" choice once a profile was resolved."""
    return f"{PROFILE_TEXT['auto']} — {profile_text(profile)}"


def detection_status_text(detection: HardwareDetection) -> str:
    """Return the status line shown below the "My computer" choice."""
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
    Suitability.UNKNOWN: "No suggestion available",
}

#: WRONG_KIND depends on the task: a writing model cannot read scans, and a
#: reading model is not meant for proofreading or review.
_WRONG_KIND_LABELS: dict[ModelCategory, str] = {
    "ocr": "Cannot read scans",
    "general": "Reading model only",
}


def suitability_text(value: Suitability, profile: HardwareProfile, task: ModelCategory) -> str:
    """Return the short label for a suitability value of a *task* model on *profile*."""
    if value is Suitability.TOO_LARGE:
        if profile == "cpu":
            return "Too slow for this computer"
        return "Too large for this computer"
    if value is Suitability.WRONG_KIND:
        return _WRONG_KIND_LABELS[task]
    return _SUITABILITY_LABELS[value]


def abilities_text(uses: ModelUses | None) -> str:
    """Return what a model that is not in the catalog can be used for."""
    if uses is None:
        return _SUITABILITY_LABELS[Suitability.UNKNOWN]
    if "ocr" in uses:
        return "Reads scans and writes"
    return "Writes only"


def memory_text(memory_gb: float) -> str:
    """Return the memory column text (e.g. ``"~7 GB"``)."""
    return f"~{memory_gb:g} GB"


def too_large_text(memory_gb: float | None, profile: HardwareProfile) -> str:
    """Explain why a model is too large for *profile* (tooltip and hint)."""
    if profile == "cpu":
        return "This model is too large to run quickly without a graphics card."
    tier = _TIER_MEMORY_GB[profile]
    if memory_gb is None:
        return f"This model may need more memory than the {tier} GB of computers of this type."
    return (
        f"This model needs about {memory_gb:g} GB of graphics memory; "
        f"computers of this type have about {tier} GB."
    )


def too_large_tooltip(memory_gb: float | None, profile: HardwareProfile) -> str:
    """Return the tooltip for a TOO_LARGE model row."""
    return f"{too_large_text(memory_gb, profile)} It will still work, but it can be very slow."


def too_large_confirmation(tag: str, memory_gb: float | None, profile: HardwareProfile) -> str:
    """Return one paragraph of the "download anyway?" confirmation."""
    return (
        f"{tag}: {too_large_text(memory_gb, profile)} "
        "It will still work, but reading or proofreading can become very slow."
    )


# ── hint line under a model box ───────────────────────────────────────────────

HINT_NOT_DOWNLOADED = "This model is not downloaded yet."
HINT_NOT_DOWNLOADED_WITH_LINK = f'{HINT_NOT_DOWNLOADED} <a href="download">Download…</a>'
HINT_NO_SUGGESTION = "No suggestion available for this model."

SAME_MODEL_TIP = (
    "Tip: using the same writing model for proofreading and review is faster, "
    "because the model does not have to be reloaded for every document."
)


def model_hint(
    value: Suitability,
    profile: HardwareProfile,
    *,
    task: ModelCategory,
    memory_gb: float | None,
    best_for_task: str,
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
    if value is Suitability.TOO_LARGE:
        return f"⚠ {too_large_text(memory_gb, profile)} It may be very slow."
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


# ── Downloads dialog ──────────────────────────────────────────────────────────

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
