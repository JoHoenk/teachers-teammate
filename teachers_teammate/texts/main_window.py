"""What the main window says: its input and output rows, its steps, its panel and results.

The toolbar's buttons, the documents and results rows, the step switches, the panel's own
buttons, the results table with its right-click menu, the export entries and the System
Monitor's gauges read their wording from here — the main window's own module, next to the rest
of the window's wording.
"""

from __future__ import annotations

from ..exceptions import RECOGNITION_OFF_MESSAGE
from .settings import WHERE_ADDONS, WHERE_AI_MODELS

# ── Main window: the documents and results rows ───────────────────────────────

DOCUMENTS_LABEL = "Documents:"
DOCUMENTS_PLACEHOLDER = "No input selected"
DOCUMENTS_FOLDER_BUTTON = "Folder…"
DOCUMENTS_FOLDER_TOOLTIP = (
    "Choose a folder — all supported files inside will be processed, including subfolders."
)
DOCUMENTS_FILES_BUTTON = "Files…"
DOCUMENTS_FILES_TOOLTIP = "Choose one or more individual files (PDF, images, or plain text)."
RESULTS_LABEL = "Save to:"
RESULTS_PLACEHOLDER = "Choose a folder for the results"
RESULTS_TOOLTIP = (
    "Folder where Word documents and text files will be saved.\n"
    "Required as soon as a Word document or a text file is switched on."
)
RESULTS_BUTTON = "Choose…"
RESULTS_BUTTON_TOOLTIP = "Choose the folder."
TEXT_FILE_TOOLTIP = "Save the extracted text of each input file as a text file (name.txt)."
TEXT_MODE_TOOLTIP = (
    "as written — the text as it was read (name.txt).\n"
    "anonymized — names and other personal data replaced by placeholders\n"
    "(name_anonymized.txt). Runs on this computer and needs the privacy add-on (spaCy).\n"
    "both — one file of each."
)


def files_more(count: int) -> str:
    """Return the last line of the list of selected files: how many more there are."""
    return f"… and {count} more"


def files_from_folder(count: int, folder: object) -> str:
    """Return ``3 files from C:\\…`` for a selection of individual documents."""
    return f"{count} file{'s' if count != 1 else ''} from {folder}"


# ── Main window: the steps ────────────────────────────────────────────────────

RECOGNIZE_TEXT = "Recognize text"
RECOGNIZE_TEXT_TOOLTIP = (
    "Read the text from scans (photos and scanned pages).\n"
    "Text files and PDFs that already contain their text are used as they are.\n"
    "Switched off, only that existing text is used and scans are skipped."
)
RECOGNIZE_NOT_NEEDED = "Not needed here: the text of these documents is already available."


def scans_skipped_note(count: int) -> str:
    """Return the note under a switched-off "Recognize text" when *count* scans are queued."""
    noun = "scan" if count == 1 else "scans"
    return f"{count} {noun} will be skipped because text recognition is off."


#: The tooltip of a "skipped" cell in the queue (the same sentence the pipeline reports).
RECOGNITION_OFF_REASON = RECOGNITION_OFF_MESSAGE
NOTHING_TO_READ_LINE = "Nothing to read: no document has text to use without recognition"
TURN_ON_RECOGNITION = "Turn on"
PROOFREAD = "Proofread"
REVIEW_CONTENT = "Review content"
RULES_BUTTON = "Rules…"
OPTIONS_BUTTON = "Options…"
GUARD_TITLE = "{step} can't be turned on"
RUN_BLOCKED_TITLE = "The run can't start"
REVIEW_NEEDS_PROOFREADING = (
    "Content review reads the proofread text, so it runs only together with proofreading."
)

# ── Main window: the panel's buttons, the results table, the export, the stats ─
#
# The panel names a step and this module words its buttons; the results table's marks and
# its right-click menu; what Export writes; what the System Monitor reads out.

LEVEL_LABEL = "Level:"
EXPLANATION_LANGUAGE_LABEL = "Explanation language:"
RULES_TOOLTIP = "Set which types of personal information to hide and how."
PROOFREAD_TOOLTIP = (
    "Use an AI to fix spelling and grammar in the extracted text.\n"
    f"Which AI: see {WHERE_AI_MODELS}."
)
PROOFREAD_OPTIONS_TOOLTIP = (
    "Change the explanations (their language, or corrections only) and add notes\n"
    "for the AI. The instructions themselves are fixed; the dialog shows them."
)
REVIEW_OPTIONS_TOOLTIP = "Change the instructions used to generate the content review report."


def collapsed(heading: str) -> str:
    """Return the button of a folded section, e.g. ``▸ What the AI is asked``."""
    return f"\u25b8 {heading}"


def expanded(heading: str) -> str:
    """Return the button of an open section, e.g. ``▾ What the AI is asked``."""
    return f"\u25be {heading}"


RESULTS_WORD_CHECKBOX = "Word document"
RESULTS_WORD_TOOLTIP = (
    "Generate a Word document for each input file containing the image,\n"
    "extracted text, and (if enabled) the proofread text."
)
RESULTS_WORD_FORMAT_TOOLTIP = (
    "Table \u2014 side-by-side columns: Image | Extracted text | Proofread text.\n"
    "Comments \u2014 two columns with tracked changes shown as Word comments."
)
RESULTS_TEXT_CHECKBOX = "Text file"
RESULTS_TEXT_HINT = (
    "Text files need an output folder outside the input folder; otherwise the next "
    "run would read them as new documents."
)

#: What a results cell shows instead of words: a button, and the marks that carry a note.
OPEN_BUTTON = "Open"
WARNING_MARK = "\u26a0"
INFO_MARK = "\u24d8"
ERROR_MARK = "\u2717"
STEP_INFO_MARK = "i"

#: The results table's right-click menu.
MENU_CHECK = "Check"
MENU_RERUN_RECOGNITION = "Re-run Text Recognition"
MENU_RERUN_PROOFREADING = "Re-run Proofreading"
MENU_RUN_REVIEW = "Run Content Review"
MENU_WHAT_IS_SENT = "What is sent for this file\u2026"

RUN_SELECTED = "Run Selected"
READINESS_TOOLTIP = (
    "Whether a run can start with the current settings. It never blocks Run: "
    "the requirements are checked again when you press Run."
)

#: Export writes one file per document; the toolbar button and both menus say the same.
EXPORT_BUTTON = "Export"
EXPORT_TOOLTIP = (
    "Save results as Word documents or text files, optionally anonymized "
    "(also available via right-click)"
)
EXPORT_LABELS = {
    "docx": "Word Document\u2026",
    "text": "Text File\u2026",
    "text_anonymized": "Anonymized Text File\u2026",
}

#: The System Monitor's gauge: a row's label and the reading beside it.
STATS_GROUP = "System Stats"
STATS_CPU = "CPU"
STATS_RAM = "RAM"
STATS_GPU = "GPU"
STATS_VRAM = "VRAM"
STATS_NEEDS_PSUTIL = "Install psutil for live system stats."
STATS_NO_READING = "\u2013"


def percent(value: float) -> str:
    """Return a load as the gauge prints it, e.g. ``37 %``."""
    return f"{value:.0f} %"


def memory_use(used_gb: float, total_gb: float) -> str:
    """Return a memory reading, e.g. ``3.4 / 16.0 GB``."""
    return f"{used_gb:.1f} / {total_gb:.1f} GB"


#: The three choices of the "Text file" row: which text files a run writes.
TEXT_MODE_AS_WRITTEN = "as written"
TEXT_MODE_ANONYMIZED = "anonymized"
TEXT_MODE_BOTH = "both"

#: The title above the chart of how long each file took, and the key to its two kinds of bar.
CHART_TITLE = "Processing time per file"
CHART_LEGEND_OCR = "OCR"
CHART_LEGEND_PROOFREADING = "Proofreading"

# ── The readiness line above Run ──────────────────────────────────────────────

READINESS_SELECT_DOCUMENTS = "Select documents to start"
READINESS_NO_DOCUMENTS = "No documents found there"
READINESS_SELECT_ROWS = "Select the documents to run in the queue"
READINESS_CHECKING = "Checking…"


def readiness_ready(selected: int, files: int) -> str:
    """Return the line that says a run can start: all *files*, or *selected* of them."""
    documents = f"{files} document{'s' if files != 1 else ''}"
    count = documents if selected == files else f"{selected} of {documents}"
    return f"✓ Ready · {count}"


def readiness_warning(line: str) -> str:
    """Return *line*, the fix of an unmet requirement, as the readiness line shows it."""
    return f"⚠ {line}"


def readiness_downloading(model: str) -> str:
    """Return the line the readiness line shows while *model* is downloaded."""
    return f"Downloading {model}"


# ── The lines an export writes to the log ─────────────────────────────────────


def docx_saved(path: object) -> str:
    """Return the log line for a Word document written to *path*."""
    return f"DOCX saved: {path}\n"


def text_saved(path: object) -> str:
    """Return the log line for a text file written to *path*."""
    return f"Text saved: {path}\n"


# ── The four steps' headers: a numbered title and what the step is for ────────

STEP_DOCUMENTS_TITLE = "1  Documents"
STEP_DOCUMENTS_TOOLTIP = (
    "Choose the documents you want to process.\n"
    "You can pick individual files or a whole folder.\n"
    "Subfolders are included automatically."
)
STEP_LANGUAGE_TITLE = "2  Language"
STEP_LANGUAGE_TOOLTIP = (
    "Select the language the documents are written in.\n"
    "This helps the app recognise text accurately and\n"
    "proofread in the correct language."
)
STEP_WHAT_TITLE = "3  What should happen"
STEP_WHAT_TOOLTIP = (
    "The text is always read from the scans. The other steps are optional.\n"
    "Removing personal info runs on this computer, before anything is sent\n"
    f"(it needs name detection, see {WHERE_ADDONS}). Proofreading and\n"
    f"content review use the AI models set under {WHERE_AI_MODELS}."
)
STEP_RESULTS_TITLE = "4  Results"
STEP_RESULTS_TOOLTIP = "Where the results are saved, and which files a run creates there."

# ── The lines the window writes to its log ────────────────────────────────────

LOG_READER_DOWNLOADED = "The built-in reader is downloaded.\n"
LOG_PAGE_MODELS_DOWNLOADED = "The page models are downloaded.\n"


def log_settings_loaded(path: object) -> str:
    """Return the log line for the settings file that was read from *path*."""
    return f"Loaded settings from {path}\n"


def log_settings_not_loaded(path: object, reason: BaseException) -> str:
    """Return the log line for a settings file at *path* that could not be read."""
    return f"WARNING: Could not load {path}: {reason}\n"


def log_notice(body: str) -> str:
    """Return the log line for a notice about a saved setting."""
    return f"NOTICE: {body}\n"


def log_settings_saved(path: object) -> str:
    """Return the log line for the settings file written to *path*."""
    return f"Settings saved to {path}\n"


def log_settings_backup(path: object) -> str:
    """Return the log line naming the copy of a settings file that could not be read."""
    return f"A copy of the unreadable settings file is at {path}\n"


def log_settings_backup_failed(path: object, reason: BaseException) -> str:
    """Return the log line for a settings file that could not be copied either."""
    return f"WARNING: Could not keep a copy of {path}: {reason}\n"


def log_settings_save_refused(path: object) -> str:
    """Return the log line for a save refused because the file could not be read."""
    return f"The settings were not saved over {path}, which could not be read\n"


def export_would_overwrite(source_name: str, out_name: str) -> str:
    """Return why an export is refused: *out_name* is also the file of another document."""
    return f"{source_name}: {out_name} would overwrite another file of this export."


def guard_title(step: str) -> str:
    """Return the title of the notice that says *step* cannot be turned on."""
    return GUARD_TITLE.format(step=step)
