"""What the Add-ons page says: the reading engine, its downloads and an install.

The Add-ons page, the installer dialog and the readiness line read their wording here: which
reading engine is which, whether the built-in reader and the page models are downloaded, the
status lines of an install, and the sentence for one that failed.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR
from ..domain.local_models import (
    HUGGING_FACE_HOST,
    PAGE_MODELS_NOT_DOWNLOADED,
    PAGE_MODELS_SIZE_LABEL,
    READER_SIZE_LABEL,
)
from ..exceptions import (
    AddonInstallError,
    AddonUpdateError,
    InstallFailure,
)
from .settings import WHERE_ADDONS

#: The Add-ons page's tabs, in the order it shows them.
TAB_LOCAL_MODELS = "AI models for this computer"
TAB_ONLINE_SERVICES = "Online AI services"
TAB_NAME_DETECTION = "Name detection"

#: How the status line names the two downloads that come from this app.
READER_PROGRESS_STATUS = "Built-in reader"
PAGE_MODELS_PROGRESS_STATUS = "Page models"

# ── The engines, the built-in reader and the page models ──────────────────────


def provider_package_label(services: Sequence[str], pip: str, *, connector: bool) -> str:
    """Return the Add-ons box label of a provider package: who needs it, and its pip name."""
    kind = " LangChain connector" if connector else ""
    return f"{' and '.join(services)}{kind} — {pip}"


#: The engine choice, by engine id.
OCR_ENGINE_LABELS = {
    ENGINE_OLLAMA: "Ollama (AI model)",
    ENGINE_PPOCR: "Built-in reader (PP-OCR)",
}
OCR_ENGINE_HINTS = {
    ENGINE_OLLAMA: "Best for handwriting. Fast with a graphics card.",
    ENGINE_PPOCR: (
        "Works on any computer without a server. Best for printed pages; reads handwriting "
        "less well."
    ),
}
READER_DOWNLOAD_LABEL = f"Download built-in reader ({READER_SIZE_LABEL})"
READER_NOT_DOWNLOADED = "Not downloaded yet."
DOWNLOADED_MARK = "✓ Downloaded"
READER_DOWNLOADED = DOWNLOADED_MARK
INCLUDED = "✓ Included"
"""What a row says when the app carries the files itself: nothing to download."""

PAGE_MODELS_SKIPPED_NOTICE = (
    "Turning and flattening pages is skipped: "
    f"{PAGE_MODELS_NOT_DOWNLOADED} (--download-page-models)"
)
"""The one line a run prints when the automatic preparation has no page models."""

PAGE_MODELS_LABEL = f"Page models — {PAGE_MODELS_SIZE_LABEL}"
PAGE_MODELS_HINT = "Used by Automatic image preparation for any reading engine."
PAGE_MODELS_MISSING_LINE = (
    f"ⓘ Turning pages and flattening curved pages need the page models ({PAGE_MODELS_SIZE_LABEL})."
)
PAGE_MODELS_DOWNLOAD_BUTTON = "Download…"
PAGE_MODELS_DOWNLOADED = DOWNLOADED_MARK

# ── Model files on disk and a download ────────────────────────────────────────

#: The built-in reader's model files, when they are not the ones this version of the app expects.
READER_MODEL_MISMATCH = "The built-in reader's model files don't match this version of the app."


def reader_model_mismatch(reason: str | None = None) -> str:
    """Return the same sentence, naming *reason* when the reader failed as it loaded."""
    return READER_MODEL_MISMATCH if reason is None else f"{READER_MODEL_MISMATCH} ({reason})"


def models_not_downloaded(repo: str) -> str:
    """Return what the app says about the model files of *repo* while they are missing."""
    return f"The model files of {repo} are not downloaded yet."


def download_damaged(name: str) -> str:
    """Return what a download says about a file whose checksum does not match."""
    return (
        f"The download of {name} was damaged on the way (the checksum does not match). "
        "Please try again."
    )


def download_too_large(name: str) -> str:
    """Return what a download says when the answer is bigger than the file it should be."""
    return (
        f"The download of {name} sent more data than the file holds, so it was stopped. "
        "Please try again."
    )


def model_pull_failed(reason: str) -> str:
    """Return what a download of a model from the AI server says when it breaks off."""
    return f"Download failed: {reason}"


# ── Provider packages ─────────────────────────────────────────────────────────


def package_not_installed(package: str, *, installed_app: bool) -> str:
    """Return the install hint of a provider package that is missing.

    The installed app points at the Add-ons page; a source checkout at ``pip``.
    """
    if installed_app:
        return f"{package} is not installed. Install it under {WHERE_ADDONS}."
    return f"{package} is not installed. Run: pip install {package}"


def provider_not_loaded(provider: str) -> str:
    """Return what the app says about a provider whose own package cannot be loaded."""
    return (
        f"Provider '{provider}' could not be loaded — "
        "check that all required packages are installed."
    )


def unknown_provider_error(provider: str, available: str) -> str:
    """Return what the app says about a provider name no module of the app has."""
    return f"Unknown correction provider '{provider}'. Available: {available}."


# ── Name detection: spaCy ─────────────────────────────────────────────────────

#: What the app says about anonymization without spaCy: it is one of the add-ons.
SPACY_REQUIRED = (
    "Removing personal info needs spaCy, which the Add-ons page installs (and the name "
    "detection model for the language of the text)"
)


def spacy_not_loadable(reason: str) -> str:
    """Return what the app says about a spaCy that is installed but cannot be imported."""
    return f"spacy is installed but cannot be loaded: {reason}"


# ── The Add-ons page and an install ───────────────────────────────────────────

#: After an app update the add-ons wait for one click: the readiness line, the Add-ons page.
ADDONS_OUTDATED_LINE = "Add-ons need an update after the app update"
UPDATE_ADDONS = "Update add-ons"
ADDONS_OUTDATED_NOTE = (
    "The app was updated, and so were some of the packages it shares with its add-ons. The "
    "add-ons stay switched off until they are installed again: this downloads them once. "
    "Everything else works meanwhile."
)
ADDONS_UPDATED = "The add-ons are up to date."
#: The add-ons are installed into the folder of the running program, which a source checkout
#: does not have: updating them there is refused.
ADDONS_UPDATE_NEEDS_INSTALL = "Add-ons are updated only in the installed app"
#: An install could not replace a file the running app has loaded (Windows): a restart
#: finishes it.  The Add-ons page says it, and the readiness line names the step it blocks.
ADDONS_RESTART_NEEDED = "spaCy is installed. Restart the app to finish."
ADDONS_RESTART_NEEDED_GENERIC = "The add-ons are installed. Restart the app to finish."

#: The Add-ons page's one status line, and the controls around it.
ADDONS_INSTALL = "Install / Download Selected"
ADDONS_INSTALLING = "Installing…"
ADDONS_ABORT = "Abort"
ADDONS_ABORT_TOOLTIP = "Stop the current download after the current chunk."
ADDONS_SHOW_DETAILS = "Show details"
ADDONS_HIDE_DETAILS = "Hide details"
ADDONS_COPY_LOG = "Copy"
ADDONS_COPIED = "Copied."
ADDONS_DETECTING = "Detecting installed packages…"
ADDONS_DETECTED = "Detection complete."
ADDONS_DETECT_FAILED = "Detection failed: {reason}"
ADDONS_DETECT_UNEXPECTED = "unexpected result"
ADDONS_STATUS_STEP = "{verb} {item} (step {number} of {total})…"
ADDONS_STATUS_STEP_PERCENT = "{verb} {item} (step {number} of {total}), {percent} %"
ADDONS_STATUS_ELAPSED = "{line} {elapsed}"
ADDONS_STATUS_INSTALLED = "✓ {count} installed"
ADDONS_STATUS_FAILED = "✗ {item} failed: {reason}"
ADDONS_ABORTED = "Aborted."
ADDONS_VERB_INSTALL = "Installing"
ADDONS_VERB_DOWNLOAD = "Downloading"
ADDONS_ITEM_ADDONS = "the add-ons"
ADDONS_ITEM_READER = "the built-in reader"
ADDONS_ITEM_PAGE_MODELS = "the page models"
ADDONS_ITEM_SPACY = "spaCy"
ADDONS_ITEM_SPACY_MODEL = "the {language} model"
ADDONS_ITEM_MULTILINGUAL = "multilingual"


def format_elapsed(seconds: float) -> str:
    """Return *seconds* as a clock a teacher can watch: ``"0:07"``, ``"1:42"``."""
    total = max(0, int(seconds))
    return f"{total // 60}:{total % 60:02d}"


def addons_status_step(verb: str, item: str, number: int, total: int) -> str:
    """Return the status line of a running install step: ``"Installing spaCy (step 1 of 2)…"``."""
    return ADDONS_STATUS_STEP.format(verb=verb, item=item, number=number, total=total)


def addons_status_percent(verb: str, item: str, number: int, total: int, percent: int) -> str:
    """Return the status line of a download that has come *percent* of the way."""
    return ADDONS_STATUS_STEP_PERCENT.format(
        verb=verb, item=item, number=number, total=total, percent=percent
    )


def addons_status_elapsed(line: str, seconds: float) -> str:
    """Return *line* with the time the step has been running, so the screen visibly moves."""
    return ADDONS_STATUS_ELAPSED.format(line=line, elapsed=format_elapsed(seconds))


def addons_installed(count: int) -> str:
    """Return the status line after a round that installed *count* items."""
    return ADDONS_STATUS_INSTALLED.format(count=count)


ADDONS_FAILURE: dict[InstallFailure, str] = {
    InstallFailure.DISK_FULL: "There is not enough space on the disk.",
    InstallFailure.FILE_IN_USE: "A file in the add-on folder is in use. Close the app and try again.",
    InstallFailure.OFFLINE: "There is no internet connection.",
    InstallFailure.NO_VERSION: (
        "No version of this add-on fits this version of the app. Look for an app update."
    ),
    InstallFailure.MERGE_FAILED: (
        "The add-on could not be put in the add-on folder. Check the free space and try again."
    ),
    InstallFailure.INSTALL_FAILED: "The installation failed.",
    InstallFailure.DOWNLOAD_FAILED: "The model could not be downloaded.",
}


def addon_error_text(error: BaseException) -> str:
    """Return the sentence for a failed add-on install; any other error says what it says."""
    if isinstance(error, AddonUpdateError):
        return "; ".join(
            f"{requirement}: {addon_error_text(failure)}" for requirement, failure in error.errors
        )
    if isinstance(error, AddonInstallError):
        return ADDONS_FAILURE[error.reason]
    return str(error)


def addons_failed(item: str, reason: str) -> str:
    """Return the status line of a step that failed, naming the item and the reason."""
    return ADDONS_STATUS_FAILED.format(item=item, reason=reason)


def addons_detect_failed(reason: str = "") -> str:
    """Return the status line of a detection that could not run."""
    return ADDONS_DETECT_FAILED.format(reason=reason or ADDONS_DETECT_UNEXPECTED)


def addons_spacy_model(language: str) -> str:
    """Return the name of a name-detection model by its language: ``"the German model"``."""
    return ADDONS_ITEM_SPACY_MODEL.format(language=language)


# ── The installer and the download dialogs ────────────────────────────────────

INSTALL_PRIVACY_TITLE = "Install Privacy Add-on"

#: The buttons of the installer and the download dialogs.
BUTTON_INSTALL = "Install"
BUTTON_CLOSE = "Close"
BUTTON_RETRY = "Retry"
BUTTON_DOWNLOAD = "Download"
BUTTON_CANCEL = "Cancel"
BUTTON_INSTALLED = "Installed ✓"
BUTTON_DONE = "Done ✓"
BUTTON_DOWNLOADED = "Downloaded ✓"
BUTTON_DOWNLOADING = "Downloading…"

#: The hint under an AI service whose Python package is missing.
PACKAGE_BUTTON = "Install…"
PACKAGE_BUTTON_TOOLTIP = "Opens Add-ons, where this package can be installed with one click."
PACKAGE_PIP_HINT = "Or run:  pip install {pip}"

INSTALL_LOG_PLACEHOLDER = "Installation log will appear here…"
DOWNLOAD_LOG_PLACEHOLDER = "Download log will appear here…"
SAVED_LOG_PLACEHOLDER = "Install / download log will appear here…"
STARTING_DOWNLOAD = "Starting download…"
DOWNLOAD_CANCELLED = "The download was cancelled."
INSTALL_COMPLETE_LOG = "\n✓ Installation complete."
MODEL_DOWNLOADED_LOG = "\n✓ Model downloaded."

READER_DOWNLOAD_TITLE = "Download the built-in reader"
PAGE_MODELS_DOWNLOAD_TITLE = "Download the page models"

SPACY_DOWNLOAD_TITLE = "Download spaCy Model"
SPACY_DOWNLOAD_INTRO = (
    "Select a spaCy NER model to download. The model will be used for "
    "anonymizing person names in OCR output.<br><br>"
    "The <i>Multilingual</i> model works as a secondary pass across all "
    "supported languages and is a good default choice."
)

#: The Add-ons page's tabs and the fields of the models tab.
READING_MODELS_INTRO = (
    "The built-in reader reads pages on this computer, without a server. "
    f"{OCR_ENGINE_HINTS[ENGINE_PPOCR]} Its model files, and the page models of the "
    "automatic image preparation, come from PaddlePaddle (Apache-2.0)."
)
READER_ITEM_LABEL = f"Built-in reader (PP-OCR) — {READER_SIZE_LABEL}"
NAME_DETECTION_GROUP = "Name detection"
NAME_DETECTION_MODELS_GROUP = "Name detection models"
NAME_DETECTION_INTRO = (
    "spaCy is the library that finds names and other personal information. "
    "Install it first, then download a name detection model below."
)
SPACY_ITEM_LABEL = "spaCy  (spacy)"
PROVIDERS_GROUP = "Online AI services"


def privacy_install_intro() -> str:
    """Return what the privacy add-on's installer says before it starts."""
    return (
        "This installs <b>spaCy</b>, the NLP library used for PII anonymization.<br><br>"
        "After installation, download a name detection model for your language under "
        f"<i>{WHERE_ADDONS}</i> before using anonymization."
    )


def reader_intro() -> str:
    """Return what the download dialog of the built-in reader says above the folder."""
    return (
        "The built-in reader reads pages on this computer, without a server. Its model "
        f"files ({READER_SIZE_LABEL}, Apache-2.0, from PaddlePaddle) are downloaded once "
        f"from {HUGGING_FACE_HOST} and kept in:"
    )


def page_models_intro() -> str:
    """Return what the download dialog of the page models says above the folder."""
    return (
        "The automatic image preparation turns sideways pages and flattens curved ones with two "
        f"small models ({PAGE_MODELS_SIZE_LABEL}, Apache-2.0, from PaddlePaddle). They are "
        f"downloaded once from {HUGGING_FACE_HOST} and kept in:"
    )


def saved_in(path: str) -> str:
    """Return the line under a download that names where the files are kept."""
    return f"Saved in: {path}"


def megabytes(done: int, total: int) -> str:
    """Return a download's progress in megabytes: ``"1.2 / 3.4 MB"``."""
    return f"{done / 1_000_000:.1f} / {total / 1_000_000:.1f} MB"


# ── The lines an install writes to the log ────────────────────────────────────

LOG_UPDATING = "Installing the add-ons again…"
LOG_UPDATED = "✓ Add-ons updated."
LOG_PIP_DONE = "✓ pip install complete."
LOG_SPACY_DONE = "✓ spaCy model downloaded."
LOG_READER_START = "Downloading the built-in reader…"
LOG_READER_DONE = "✓ Built-in reader downloaded."
LOG_PAGE_MODELS_START = "Downloading the page models…"
LOG_PAGE_MODELS_DONE = "✓ Page models downloaded."
LOG_OLLAMA_DONE = "✓ Ollama model pulled."


def log_pip_install(packages: list[str]) -> str:
    """Return the log line that names the *packages* pip is asked to install."""
    return f"pip install {' '.join(packages)}"


def log_spacy_start(model: str) -> str:
    """Return the log line for the start of a spaCy *model* download."""
    return f"Downloading spaCy model: {model}"


def log_ollama_start(model: str) -> str:
    """Return the log line for the start of an Ollama *model* pull."""
    return f"Pulling Ollama model: {model}"


# ── The name detection's extra model, the provider check ──────────────────────

INSTALLED_MARK = "✓ Installed"
NO_HEALTH_CHECK = "no health check available"
MODEL_LIST_UNREADABLE = "the model list could not be read"


def provider_check_failed(provider: str, reason: BaseException) -> str:
    """Return the line for a *provider* whose health check raised."""
    return f"✗ Could not check provider '{provider}': {reason}"


def extra_model_language_unknown(document_language: str) -> str:
    """Return the warning for a computer whose language is not known: no extra model is used."""
    return (
        "WARNING: the language of this computer could not be determined, so no "
        f"extra detection model is used (the model for {document_language} is "
        "enough). Choose a model in the personal-info settings."
    )


def extra_model_missing(
    model: str, system_language: str, primary: str, document_language: str
) -> str:
    """Return the warning for the extra detection *model* that is not downloaded."""
    return (
        f"WARNING: the extra detection model '{model}' for the language of this "
        f"computer ({system_language}) is not downloaded, so only '{primary}' is "
        f"used. Download it under Add-ons in the app, or with `python -m spacy "
        f"download {model}`, to catch names the {document_language} model misses."
    )


def package_pip_hint(pip: str) -> str:
    """Return the line that offers the pip command for the package *pip*."""
    return PACKAGE_PIP_HINT.format(pip=pip)
