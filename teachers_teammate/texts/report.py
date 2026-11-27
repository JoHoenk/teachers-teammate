"""What a run writes to its log while it works: the banner, the progress lines, the closing block.

The command line prints these lines and the log file keeps them.  The banner is a
table of ``label : value`` rows; a progress line under a file starts with an arrow.
"""

from __future__ import annotations

from .app import NAME

LINE = "=" * 64
TITLE = NAME
PREVIEW_TITLE = f"{NAME} — Preview (preprocess only)"

# ── The banner ────────────────────────────────────────────────────────────────

CONFIG_FILE = "Config file"
RECOGNITION = "Recognition"
OCR_ENGINE = "OCR engine"
OCR_MODEL = "OCR model"
PREPROCESS = "Preprocess"
LANGUAGE = "Language"
OLLAMA_URL = "Ollama URL"
INPUT = "Input"
OUTPUT = "Output"
RECURSIVE = "Recursive"
DEBUG = "Debug"
CORRECTION = "Correction"
ANONYMIZATION = "Anonymization"
EVALUATION = "Evaluation"
DOCX_OUTPUT = "DOCX output"
DOCX_FORMAT = "DOCX format"
TEXT_FILES = "Text files"
PREVIEW_DIR = "Preview dir"
KEEP_AWAKE = "Keep awake"

DISABLED = "disabled"
RECOGNITION_DISABLED = "disabled (scans are skipped)"
ANONYMIZATION_ENABLED = "enabled (PII removed after text recognition)"
AWAKE_OFF = "off (the computer may go to sleep)"
AWAKE_YES = "yes"
AWAKE_UNAVAILABLE = "no (not available on this computer)"
AWAKE_REFUSED = "no (the system did not allow it)"


def row(label: str, value: object) -> str:
    """Return one ``label : value`` row of the banner."""
    return f"  {label:<14}: {value}"


def reader_engine(engine: str, model: str) -> str:
    """Return the built-in reader as the banner names it: its *engine* name and *model*."""
    return f"{engine} ({model})"


def provider_model(provider: str, model: str) -> str:
    """Return the service and the model a step uses."""
    return f"{provider} / {model}"


def text_files(*, plain: bool, anonymized: bool) -> str:
    """Return which text files a run writes, or that it writes none."""
    names = [name for name, on in (("plain", plain), ("anonymized", anonymized)) if on]
    return ", ".join(names) or DISABLED


def storage_root(path: object, source: str) -> str:
    """Return the line that says where the app keeps its data."""
    return f"Storage root: {path} ({source})"


# ── The progress lines ────────────────────────────────────────────────────────

_STEP = "       → "

OLLAMA_CHECKED = "Checking Ollama connection... OK"
CORRECTION_LLM_BUILT = "Building correction LLM... OK"
EVALUATION_LLM_BUILT = "Building evaluation LLM... OK"
STOPPED_BY_USER = "\nStopped by user."
EVALUATION_NEEDS_CORRECTION = "WARNING: Evaluation requires correction; disabling evaluation."

CACHED_OCR = f"{_STEP}Using cached OCR output"
PREPROCESSING = f"{_STEP}Preprocessing..."
PREPROCESSING_TEXT_INPUT = f"{_STEP}Preprocessing skipped (text input)"
PREPROCESSING_NO_STEPS = f"{_STEP}Preprocessing applied no steps"
PREPROCESSING_SKIPPED_TEXT = f"{_STEP}skipped preprocessing (text input)"
RUNNING_OCR = f"{_STEP}Running OCR..."
USED_TRANSCRIPTION = f"{_STEP}Your transcription used (pages not read again)"
USED_PDF_TEXT = f"{_STEP}Embedded PDF text used (OCR skipped)"
USED_TEXT_INPUT = f"{_STEP}Text input loaded (OCR skipped)"
KEPT_PROOFREADING = f"{_STEP}Proofreading kept: you worked on it (made with other settings)"
CACHED_CORRECTION = f"{_STEP}Using cached correction output"
RUNNING_CORRECTION = f"{_STEP}Running correction..."
CACHED_EVALUATION = f"{_STEP}Using cached evaluation output"
RUNNING_EVALUATION = f"{_STEP}Running evaluation..."


def no_supported_files(supported: str) -> str:
    """Return the line for an input with no file of a type the app reads."""
    return f"No supported files found ({supported}). Nothing to do."


def files_to_process(count: int) -> str:
    """Return the line that opens a run: how many files it works on."""
    return f"Found {count} file(s) to process.\n"


def files_to_preprocess(count: int) -> str:
    """Return the line that opens a preview run: how many files it prepares."""
    return f"Found {count} file(s) to preprocess.\n"


def processing(index: int, total: int, name: str) -> str:
    """Return the line that opens the work on one file."""
    return f"[{index}/{total}] Processing: {name}"


def stopped_with_files_left(count: int) -> str:
    """Return the line for a stop that left *count* files unstarted."""
    return f"\nStopped by user: {count} file(s) not started."


def saved(message: str) -> str:
    """Return the line for a file whose result was saved, with what the run said about it."""
    return f"{_STEP}Saved: {message}"


def step(text: str) -> str:
    """Return *text* as a progress line under a file."""
    return f"{_STEP}{text}"


def file_skipped(reason: str) -> str:
    """Return the line for a file that was skipped, and why."""
    return f"{_STEP}Skipped: {reason}"


def prepared_page(name: str, steps: str) -> str:
    """Return the line for a page image a preview wrote, with the steps that made it."""
    return f"{_STEP}{name}  ({steps})"


def preprocessing_done(steps: str, pages: int) -> str:
    """Return the line for a finished image preparation: its *steps* and the page count."""
    return f"{_STEP}Preprocessing done: {steps} ({pages} page(s))"


def ocr_page(page: int, total: int) -> str:
    """Return the line for the page the reader is on."""
    return f"{_STEP}OCR page {page}/{total}..."


def ocr_done(pages: int) -> str:
    """Return the line for a finished text recognition."""
    return f"{_STEP}OCR done ({pages} page(s))"


def pdf_text_layer_note(note: str) -> str:
    """Return the line for a PDF whose text layer is not used, and why."""
    return f"{_STEP}PDF {note} — using OCR"


def anonymized_before(stage: str) -> str:
    """Return the line for names replaced before the step *stage*."""
    return f"{_STEP}PII anonymized before {stage}"


def personal_info_removed(count: int) -> str:
    """Return the line for the names and other personal data that were replaced."""
    return f"{_STEP}Personal info removed ({count} items)"


def proofreading_in_parts(name: str, parts: int) -> str:
    """Return the line for a text that is proofread in several pieces."""
    return f"{_STEP}Proofreading {name} in {parts} parts..."


def debug_files_kept(folder: object) -> str:
    """Return the line that says where the preprocessing files stay after a debug run."""
    return f"{_STEP}DEBUG: preprocessing files kept in {folder}"


# ── The closing block ─────────────────────────────────────────────────────────

FAILED_FILES = "Failed files:"
SKIPPED_FILES = "Skipped files (see the lines above for why):"
UNFINISHED_FILES = "Unfinished files (stopped by user; what was done is kept):"


def done(
    *, succeeded: int, failed: int, skipped: int = 0, stopped: int = 0, not_started: int = 0
) -> str:
    """Return the count line that closes a run; a count of nothing is left out where it can be."""
    counts = [f"{succeeded} succeeded", f"{failed} failed"]
    if skipped:
        counts.append(f"{skipped} skipped")
    if stopped:
        counts.append(f"{stopped} stopped")
    if not_started:
        counts.append(f"{not_started} not started")
    return f"Done: {', '.join(counts)}."


def file_item(name: str) -> str:
    """Return one file of a list in the closing block."""
    return f"  - {name}"


def preview_done(prepared: int, failed: int) -> str:
    """Return the count line that closes a preview run."""
    return f"Done: {prepared} preprocessed, {failed} failed."


def images_saved(folder: object) -> str:
    """Return the line that says where a preview run put its images."""
    return f"Images saved to: {folder}"
