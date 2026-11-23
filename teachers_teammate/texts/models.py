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
    HardwareProfileSetting,
    ModelCategory,
    ModelUses,
    Suitability,
    card_budget_gb,
    memory_for_models_gb,
    system_reserve_gb,
)
from .app import NAME
from .review import REMOVE_PERSONAL_INFO
from .settings import WHERE_ADDONS, WHERE_AI_SETUP

# ── "My computer" ─────────────────────────────────────────────────────────────

PROFILE_TEXT: dict[HardwareProfileSetting, str] = {
    HardwareProfileSetting.AUTO: "Detect automatically (recommended)",
    HardwareProfileSetting.CPU: "Basic computer or small graphics card (under 8 GB)",
    HardwareProfileSetting.CARD_8GB: "Graphics card with about 8 GB memory",
    HardwareProfileSetting.CARD_16GB: "Graphics card with 16 GB memory or more",
}

#: Graphics memory (GB) that computers of a profile are assumed to have.
_TIER_MEMORY_GB: dict[HardwareProfile, int] = {
    HardwareProfile.CARD_8GB: 8,
    HardwareProfile.CARD_16GB: 16,
}

#: How a profile is named in a sentence ("models for … are suggested").
_TIER_PHRASE: dict[HardwareProfile, str] = {
    HardwareProfile.CPU: "a basic computer",
    HardwareProfile.CARD_8GB: "a graphics card with about 8 GB",
    HardwareProfile.CARD_16GB: "a graphics card with 16 GB or more",
}

CHECKING_COMPUTER = "Checking your computer…"
NO_SUGGESTION_FOR_COMPUTER = "No suggestion available for this computer."
#: Shown when the check itself failed, so it is not mistaken for "nothing to suggest".
CHECK_COMPUTER_FAILED = (
    "Your computer could not be checked (details are in the log), so suggestions assume "
    "a graphics card with about 8 GB. If you know what your computer has, choose it in the list."
)
READ_ONLY_PROFILE_TOOLTIP = f"You can change this under {WHERE_AI_SETUP}."


def profile_text(value: HardwareProfile | HardwareProfileSetting) -> str:
    """Return the UI text for a hardware profile or profile setting."""
    return PROFILE_TEXT[HardwareProfileSetting(value.value)]


def read_only_profile_text(profile: HardwareProfile | HardwareProfileSetting) -> str:
    """Return the line shown where the computer type cannot be edited."""
    return f"Models are suggested for: {profile_text(profile)}"


#: Shown once under "My computer" when the computer cannot run a local model at all.
TOO_LITTLE_MEMORY = (
    "This computer has too little memory for AI models on this computer. Use the built-in "
    "reader for reading, or Ollama on another computer."
)


def _computer_memory_line(
    detection: HardwareDetection, available_memory_gb: float | None
) -> list[str]:
    """Return ``Computer memory: N GB, M GB free right now`` when the memory is known.

    The free figure is read when the line is drawn, so it is passed in rather than stored in
    the detection: it changes all the time and no suggestion depends on it.
    """
    if detection.system_memory_gb is None:
        return []
    text = f"Computer memory: {detection.system_memory_gb:.0f} GB"
    if available_memory_gb is not None:
        text += f", {available_memory_gb:.0f} GB free right now"
    return [text]


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


def detection_lines(
    detection: HardwareDetection, *, available_memory_gb: float | None = None
) -> list[str]:
    """Return the lines of the status shown below the "My computer" choice.

    A found graphics card gives the card, the computer's memory and what the suggestions follow,
    one line each.  *available_memory_gb* is the main memory free right now, read when the lines
    are drawn; it is left out when unknown, and it is never part of the detection itself.
    """
    if _too_little_memory(detection):
        return [*_detection_summary(detection), TOO_LITTLE_MEMORY]
    return [
        *_detection_summary(detection),
        *_other_cards_line(detection),
        *_computer_memory_line(detection, available_memory_gb),
        *_suggestion_line(detection),
    ]


def detection_status_text(
    detection: HardwareDetection, *, available_memory_gb: float | None = None
) -> str:
    """Return the status below the "My computer" choice as lines of text."""
    return "\n".join(detection_lines(detection, available_memory_gb=available_memory_gb))


def _suggestion_line(detection: HardwareDetection) -> list[str]:
    """Return what the suggestions follow, for a graphics card that was found."""
    if detection.reason is not DetectionReason.GPU_DETECTED or detection.memory_gb is None:
        return []
    phrase = _TIER_PHRASE.get(detection.profile)
    return [f"Models are suggested for {phrase}."] if phrase else []


def _other_cards_line(detection: HardwareDetection) -> list[str]:
    """Return ``Also found: …`` when more than one graphics card was found."""
    if not detection.other_gpu_names:
        return []
    return [f"Also found: {', '.join(detection.other_gpu_names)}"]


def _detection_summary(detection: HardwareDetection) -> list[str]:
    memory = detection.memory_gb
    if detection.reason is DetectionReason.GPU_DETECTED and memory is not None:
        return [f"Graphics card: {detection.gpu_name} with {memory:.0f} GB graphics memory"]
    if detection.reason is DetectionReason.APPLE_UNIFIED_MEMORY and memory is not None:
        return [f"Apple computer with {memory:.0f} GB shared memory"]
    if detection.reason is DetectionReason.NO_GPU_FOUND:
        return [
            "No graphics card found. Smaller models are suggested — they are slower but "
            "work everywhere. If you do have a graphics card, choose it in the list."
        ]
    if detection.reason is DetectionReason.GPU_MEMORY_UNKNOWN:
        return [
            f"Graphics card: {detection.gpu_name}. Its memory size could not be read, "
            "so suggestions assume about 8 GB."
        ]
    if detection.reason is DetectionReason.REMOTE_SERVER:
        return [
            f"Ollama runs on another computer ({detection.host}). Suggestions assume "
            "a graphics card with about 8 GB. If you know what that computer has, "
            "choose it in the list."
        ]
    return ["Chosen by you."]


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
        if offer is not None:  # the main memory is known here, so the offer is a number
            parts = "main memory" if card <= 0 else "graphics memory and main memory"
            return (
                f"{need}. This computer can offer about {_gb(offer)} ({parts}); "
                f"{_gb(system_reserve_gb(system_memory_gb))} of its {system_memory_gb:.0f} GB "
                "main memory stay free for the system. The model does not fit, so it would not "
                "run properly."
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


def model_count(count: int) -> str:
    """Return how many models there are: ``"1 model"``, ``"3 models"``."""
    return f"{count} {'model' if count == 1 else 'models'}"


def models_from_text(count: int, service: str) -> str:
    """Return the finished status line: how many models *service* offered."""
    return f"{model_count(count)} from {service}"


# ── The status line under a model field ──────────────────────────────────────

STATUS_LOADING = "● loading…"
STATUS_KEY_MISSING = "● key not set"
STATUS_ERROR = "● -"
STATUS_RETRY = "Try again"
SERVICE_NOT_ASKED = "the service could not be asked"


def listing_builtin(count: int) -> str:
    """Return the status of a model list that is the app's own by design."""
    return f"● {model_count(count)} · built-in list"


def listing_failed(count: int, problem: str) -> str:
    """Return the status of a model list that fell back to the app's own after a failed query."""
    what = f"built-in list of {model_count(count)}" if count else "no models to show"
    return f"● {what} — {problem or SERVICE_NOT_ASKED}"


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
    f"{NAME} needs two AI models: a reading model that turns your "
    "scans into text, and a writing model that proofreads and reviews the text."
)
BEST_ALREADY_DOWNLOADED = "The best models for your computer are already downloaded."
MODEL_ALREADY_LISTED = "That model is already in the list."
#: The Add-ons page's *AI models for this computer* tab.
OLLAMA_MODELS_GROUP = "AI models for this computer (Ollama)"
SELECT_BEST_MODELS = "Select the best models for my computer"
ADD_MODEL_LABEL = "Add a model by name:"
ADD_MODEL_PLACEHOLDER = "e.g. llama3.2:latest"
ADD_BUTTON = "Add"

ADD_MODEL_NOTE = "Added models are downloaded when you click Install / Download Selected."
GROUP_READING = "Reading models — turn scans into text"
GROUP_WRITING = "Writing models — proofread and review the text"
GROUP_OTHER = "Other models (added by you)"

# ── Model choice guidance ────────────────────────────────────────────────

MY_COMPUTER_LABEL = "My computer:"
DETECT_AGAIN = "Detect again"
USE_BEST_CHOICE = "Use best choice"
DOWNLOAD_BEST_CHOICE = "Download best choice…"

# ── Reading scans: the recognition settings ──────────────────────────────

MODEL_LABEL = "Model:"
RECOGNITION_ENGINE_TIP = (
    "Pages are always read on a computer you control. Online services are not offered: "
    "they would receive the whole page, including names and grades."
)
PDF_LABEL = "PDF:"
RECOGNITION_METHOD_LABEL = "Recognition method:"
IMAGE_PREPARATION_LABEL = "Image preparation:"
PDF_RESOLUTION_LABEL = "PDF resolution:"
PDF_TEXT_LAYER_LABEL = "PDF text layer:"
TEMPERATURE_LABEL = "Temperature:"
DPI_SUFFIX = " DPI"
AI_SERVICE_LABEL = "AI service:"
AI_MODEL_LABEL = "AI model:"
PREVIEW_BUTTON = "Preview…"
PDF_DPI_TIP = (
    "Resolution for rendering PDF pages to images before OCR.\n"
    "72 = screen quality, 150 = draft, 300 = recommended, 600 = high quality.\n"
    "Has no effect on image inputs (JPG, PNG)."
)
PDF_TEXT_LAYER_TIP = (
    "Use the text embedded in a PDF instead of recognising it.\n\n"
    "auto — only when every page is clean, text-only (no scans, no\n"
    "  handwriting, annotations or form fields); otherwise the pages\n"
    "  are recognised.\n"
    "always — whenever every page contains any text.\n"
    "never — always recognise the rendered pages.\n"
    "Has no effect on image or text files."
)


def preview_title(method: str) -> str:
    """Return the title of the window that shows one prepared page."""
    return f"Preprocessing Preview — {method}"


PREVIEW_ZOOM_HINT = "Ctrl + scroll wheel to zoom"


# ── Settings → AI models: the two groups of the page ─────────────────────

READING_HANDWRITING_GROUP = "Reading handwriting"
READING_HANDWRITING_NOTE = "Always on this computer: pages never go online."
PROOFREADING_GROUP = "Proofreading and content review"
REVIEW_OVERRIDE_LABEL = "Use a different model for content review"
REVIEW_OVERRIDE_TOOLTIP = (
    "Off: the content review uses the proofreading model.\n"
    "On: choose another service or model for the review only."
)
REVIEW_CONTEXT_LABEL = "Context window for the content review:"
REVIEW_CONTEXT_TOOLTIP = (
    "How much text the content review may read in one request.\n"
    "Automatic grows the window with the size of the graphics card;\n"
    "a larger window lets longer texts through but needs more memory."
)

#: What the page says about names in the text, by the service the teacher chose.
NAMES_AS_WRITTEN = "Names are sent as written."
NAMES_PROTECTED = "Names are replaced before the text is sent."
NAMES_NEED_DETECTION = (
    "Names are sent as written. Replacing them needs name detection for {language}."
)
NAMES_TURNED_ON = (
    f'"{REMOVE_PERSONAL_INFO}" will be turned on when you press OK, so names are replaced '
    "before the text is sent."
)
BUTTON_TURN_ON = f'Turn on "{REMOVE_PERSONAL_INFO}"'
BUTTON_KEEP_OFF = "Keep it off"
#: The button next to the line that misses name detection: it leads to where it is installed.
BUTTON_INSTALL_DETECTION = "Install…"

#: How one row of the service list marks what still has to happen before it can be used.
STATUS_NOT_INSTALLED = "not installed"
STATUS_KEY_NEEDED = "key needed"

# ── Settings → This computer: the local AI server, the model downloads ───

PERFORMANCE_GROUP = "Power and speed of this computer"
DOWNLOAD_MODEL = "Download a model…"
DOWNLOAD_MODEL_TOOLTIP = "Opens Add-ons, where AI models for this computer are downloaded."


# ── The Mistral model list: what its entries say ──────────────────────────────

#: The labels the list puts to the right of a model's name.
SUGGESTED_LABEL = "★ suggested"
PINNED_LABEL = "pinned version"
OWN_LABEL = "your model"


def current_version_note(model_id: str) -> str:
    """Return the note that names the version a model's name stands for today."""
    return f"Currently {model_id}."


def first_start_log(reading_model: str, writing_model: str, profile: HardwareProfile) -> str:
    """Return the log line that says which models the first start chose for this computer."""
    return (
        f"First start: chose reading model {reading_model} and writing model "
        f"{writing_model} for your computer ({profile_text(profile)}). "
        f"You can download them under {WHERE_ADDONS}.\n"
    )


# ── The privacy line under the AI models page's service list ──────────────────

THIS_LANGUAGE = "this language"
LINE_PROTECTED = "✓ " + NAMES_PROTECTED
LINE_TURNED_ON = "✓ " + NAMES_TURNED_ON
LINE_NAMES_SENT = "● " + NAMES_AS_WRITTEN


def line_needs_detection(language: str) -> str:
    """Return the line for names that cannot be replaced: no detection for *language*."""
    return "● " + NAMES_NEED_DETECTION.format(language=language or THIS_LANGUAGE)
