"""What the Setup Assistant says on its pages.

Every sentence the assistant shows a teacher is defined here and used by reference from
``gui/setup/_pages.py``.  The pages are grouped as the assistant shows them: Welcome, Your
classes, the AI on this computer, Proofreading, Privacy, the summary and the last page.
"""

from __future__ import annotations

from ..domain.model_catalog import Suitability
from .settings import WHERE_SETUP

# ── Welcome ───────────────────────────────────────────────────────────────────

WELCOME_TITLE = "Welcome to Teacher's Teammate"
WELCOME_TEXT = (
    "The app reads your students' handwritten work, can proofread it and review its content, "
    "and writes the results into Word documents.\n\n"
    "This assistant sets it up for this computer. It takes about five minutes; the AI models "
    "then download in the background (a few gigabytes)."
)
WELCOME_POINTS = (
    "• Pages are always read on this computer — never online.\n"
    "• You decide whether proofreading may use an online service."
)
WELCOME_FOOTNOTE = (
    f"You can run it again under {WHERE_SETUP}, and change everything under Settings."
)

# ── Your classes ──────────────────────────────────────────────────────────────

CLASSES_TITLE = "Your classes"
CLASSES_LANGUAGE = "Language your students write in:"
CLASSES_LANGUAGE_NOTE = (
    "Proofreading and name detection follow it. You can change it for every batch."
)
CLASSES_QUESTION = "What do they hand in?"
CLASSES_HANDWRITTEN = (
    "Handwritten or scanned pages (photos, scans, PDFs), also mixed with typed work"
)
CLASSES_HANDWRITTEN_NOTE = "The text is read by an AI on this computer."
CLASSES_TYPED = "Only typed texts (text files, PDFs that contain their text)"
CLASSES_TYPED_NOTE = (
    "Nothing has to be read, so no reading model is downloaded.\n"
    "Word files can't be read yet: save them as PDF."
)

# ── Local AI ──────────────────────────────────────────────────────────────────

LOCAL_AI_TITLE = "The AI on this computer"
LOCAL_AI_INTRO = (
    "Teacher's Teammate uses Ollama, a free program that runs AI models on your own computer."
)
LOCAL_AI_QUESTION = "Where does Ollama run?"
LOCAL_AI_HERE = "On this computer"
LOCAL_AI_SERVER = "On another PC in my network"
LOCAL_AI_FIND = "Find…"
LOCAL_AI_FIND_TOOLTIP = "Look for an Ollama server on this computer or on your network."
LOCAL_AI_THIS_PC_HEADING = "Your computer"
LOCAL_AI_THIS_PC_TEXT = (
    "Running the AI here needs a capable PC — best a graphics card with 8 GB or more. "
    "The suggestions below follow what this PC can do."
)
LOCAL_AI_OTHER_PC_HEADING = "The other PC"
LOCAL_AI_OTHER_PC_TEXT = (
    "Running the AI there needs a capable PC — best a graphics card with 8 GB or more. "
    "The suggestions below follow what that PC can do."
)

# ── Proofreading ──────────────────────────────────────────────────────────────

PROOFREADING_TITLE = "Proofreading"
PROOFREADING_INTRO = (
    "The app can proofread the recognised text and explain each mistake. Where should that AI run?"
)
PROOFREADING_LOCAL = "With Ollama — free; the text is not sent to an online service"
PROOFREADING_ONLINE = (
    "At an online service — faster on a weak computer; the text is sent over the internet"
)
PROOFREADING_OFF = "Not now — only read the text"
MODEL_INSTALLED = "installed"
PROOFREADING_MODEL = "Model:"
PROOFREADING_USE_RECOMMENDED = "Use the recommended model"
PROOFREADING_MODEL_TOOLTIP = "The Ollama model that proofreads."
PROOFREADING_ONLINE_NOTE = "You need an account with the service, and it may charge for use."
PROOFREADING_REVIEW_KEPT = "Content review keeps its own model (Settings → AI models)."
PROOFREADING_INSTALL_NOW = "Install now"
PROOFREADING_INSTALL_TOOLTIP = "Download the package this service needs, then check the key."
SUGGESTION = "Suggested: {model} · {fit} · {size}"
SUGGESTION_DOWNLOADED = "Suggested: {model} · {fit} · already downloaded"
SUGGESTION_CHECKING = "Checking this computer…"
SUGGESTION_UNKNOWN = "The model for this computer is chosen when you press Set up."
FIT_WORDS: dict[Suitability, str] = {
    Suitability.BEST: "fits this computer well",
    Suitability.GOOD: "fits this computer well",
    Suitability.WORKS: "works on this computer",
    Suitability.SLOW: "runs slowly here: it doesn't fit the graphics card",
}
FIT_VERY_SLOW = "runs very slowly here; an online service is much faster"

# ── Privacy ───────────────────────────────────────────────────────────────────

PRIVACY_TITLE = "Names and personal information"
PRIVACY_SENT = "Proofreading at {service} sends your students' text over the internet."
PRIVACY_EXPLAIN = (
    "Names and other personal details are replaced on this computer before anything is sent."
)
PRIVACY_NEEDS_DETECTION = (
    "Needs name detection for {language} ({model}), which is added to the downloads (small)."
)
PRIVACY_HAS_DETECTION = "Name detection for {language} ({model}) is installed."
PRIVACY_MULTILINGUAL = (
    "There is no name detection of its own for {language}: the multilingual one is used. "
    "It finds fewer names."
)
PRIVACY_EXTRA = "Also find names in texts of another language:"
PRIVACY_EXTRA_NOTE = (
    "For a class that writes in two languages. The app runs one extra language next to the "
    "main one; its detection is added to the downloads (small)."
)
PRIVACY_EXTRA_INSTALLED = "installed"
#: What each name-detection model is for, as a teacher names it.
NAME_MODEL_LANGUAGES: dict[str, str] = {
    "en_core_web_sm": "English",
    "de_core_news_sm": "German",
    "fr_core_news_sm": "French",
    "pt_core_news_sm": "Portuguese",
    "nl_core_news_sm": "Dutch",
    "xx_ent_wiki_sm": "Other languages (multilingual)",
}


def name_model_label(model: str) -> str:
    """Return the language a name-detection model is for, or the model's own name."""
    return NAME_MODEL_LANGUAGES.get(model, model)


PRIVACY_ALREADY_ON = "Already on."
PRIVACY_SENT_NETWORK = (
    "Proofreading on {host} sends your students' text to that PC in your network."
)
PRIVACY_STAYS = "Your students' texts stay on this computer."
PRIVACY_STAYS_NOTE = "Nothing is sent anywhere, so there are no names to remove before sending."
PRIVACY_HIDE_HERE = "Hide names in this app"
PRIVACY_HIDE_HERE_NOTE = (
    "The texts on screen show placeholders such as [PERSON_1] instead of the names. "
    "The stored texts do not change, and you can switch it off with the 👁 button."
)
PRIVACY_SCHOOL_NOTE = (
    "Check with your school that pupils' texts may be sent to this service. Before the first "
    "run that sends text, the app shows what is sent and asks you once more."
)
PRIVACY_SCHOOL_NOTE_NETWORK = (
    "Check with your school that pupils' texts may be sent to that PC. Before the first "
    "run that sends text, the app shows what is sent and asks you once more."
)

# ── Summary ───────────────────────────────────────────────────────────────────

SUMMARY_TITLE = "Ready to set up"
SUMMARY_WORKING = "Working out what is needed…"
SUMMARY_FAILED = (
    "The setup plan could not be worked out. You can set everything up under Settings → AI models."
)
SUMMARY_INSTALLING = "A package is still being installed. Set up is possible once it has finished."
SUMMARY_LATER = "You can change all of this later under Settings → AI models."
SUMMARY_NO_DOWNLOADS = "Nothing to download."
SUMMARY_DOWNLOADS_TOTAL = "Downloads: about {needed:.1f} GB · {free:.0f} GB free on this computer"
SUMMARY_DOWNLOADS_NEEDED = "Downloads: about {needed:.1f} GB"
SUMMARY_OLLAMA_STOPPED = (
    "⚠ Ollama isn't running yet, so its models can't be downloaded now. "
    "The main window offers them as soon as it runs."
)
SUMMARY_DISK_SHORT = (
    "Only {free:.0f} GB is free where Ollama keeps its models, and the downloads need about "
    "{needed:.1f} GB. Free some space, or untick a download."
)

# ── Done ──────────────────────────────────────────────────────────────────────

DONE_TITLE = "All set"
DONE_TITLE_RUNNING = "Setting up"
DONE_TITLE_PROBLEMS = "Set up — some downloads failed"
DONE_NOTHING = "Nothing to download — everything is in place."
DONE_SPEED_WAITING = "The speed test starts when the proofreading model has arrived."
DONE_OVERALL = "{done} of {total} downloads"
DONE_PURPOSE_READER = "Reading model"
DONE_PURPOSE_READING = "Reading handwriting"
DONE_PURPOSE_PAGE_MODELS = "Page models"
DONE_PURPOSE_PROOFREADING = "Proofreading"
DONE_PURPOSE_NAMES = "Name detection"
DONE_PURPOSE_ADDON = "Add-on"
DONE_NAME_READER = "built-in reader"
DONE_NAME_PAGE_MODELS = "turn and flatten pages"
DONE_TEXT = "The downloads run in the background; the main window shows how far they are."
DONE_DOWNLOADS_TITLE = "Downloads"
DONE_SPEED_TITLE = "Speed on this computer"
DONE_OLLAMA_GOES_ON = (
    "Ollama downloads go on after you close this window; the main window shows them."
)
DONE_WAITING = "waiting"
DONE_RUNNING = "↓ downloading…"
DONE_DONE = "✓ done"
DONE_FAILED = "✗ {message}"
DONE_PROGRESS = "↓ {done:.1f} of {total:.1f} GB"
DONE_PROGRESS_MB = "↓ {done:.0f} of {total:.0f} MB"
READER_NAME = "ppocr"  # the name of the reader's download row (``PlannedDownload.name``)
DONE_SELECT = "Select documents…"
DONE_SELECT_TOOLTIP = "Close this window and choose the folder with your students' work."
