"""What the Add-ons page says: the reading engine, its downloads and an install.

The Add-ons page, the installer dialog and the readiness line read their wording here: which
reading engine is which, whether the built-in reader and the page models are downloaded, the
status lines of an install, and the sentence for one that failed.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR
from ..domain.local_models import PAGE_MODELS_SIZE_LABEL, READER_SIZE_LABEL
from ..exceptions import (
    AddonInstallError,
    AddonUpdateError,
    InstallFailure,
)

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
READER_DOWNLOADED = "✓ Downloaded"
INCLUDED = "✓ Included"
"""What a row says when the app carries the files itself: nothing to download."""

PAGE_MODELS_LABEL = f"Page models — {PAGE_MODELS_SIZE_LABEL}"
PAGE_MODELS_HINT = "Used by Automatic image preparation for any reading engine."
PAGE_MODELS_MISSING_LINE = (
    f"ⓘ Turning pages and flattening curved pages need the page models ({PAGE_MODELS_SIZE_LABEL})."
)
PAGE_MODELS_DOWNLOAD_BUTTON = "Download…"
PAGE_MODELS_DOWNLOADED = "✓ Downloaded"

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
