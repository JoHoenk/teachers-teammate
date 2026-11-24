"""What the command line says: its arguments' help, and the lines it prints while it works.

The console has words of its own — a help text explains a flag to someone reading a terminal,
and the line that counts the megabytes of a download is written for one.  What both surfaces
say alike — an error, a warning, what a privacy level means — comes from ``dialogs.py`` and
``privacy.py``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..domain.correction_prompts import NOTES_LIMIT
from ..domain.data_location import DataFlow
from ..domain.local_models import (
    HUGGING_FACE_HOST,
    PAGE_MODELS_SIZE_LABEL,
    READER_DOWNLOAD_COMMAND,
    READER_SIZE_LABEL,
)
from ..domain.ollama_discovery import OLLAMA_DOWNLOAD_URL
from ..domain.requirements import IssueKind, RequirementIssue
from . import app, privacy
from .app import NAME
from .privacy import level_text
from .settings import WHERE_ADDONS, WHERE_ONLINE_SERVICES

DESCRIPTION = (
    f"{NAME} — batch OCR pipeline that extracts text from PDFs/images "
    "and builds DOCX documents with grammar/spelling correction proposals."
)


def version_notice(version: str) -> str:
    """Return the four lines ``--version`` prints: what this is, whose it is, and its license."""
    return "\n".join(
        (
            f"{NAME} {version}",
            app.COPYRIGHT,
            f"License {app.LICENSE_ID}: <{app.LICENSE_URL}>.",
            app.NO_WARRANTY,
        )
    )


HELP_CONFIG = (
    "Path to a TOML config file. Defaults to ocr.toml in the app's settings folder "
    "(the file the app window saves) when it exists.  CLI arguments override file values."
)

# The argument groups: input / output, the OCR parameters, the correction, the evaluation.
HELP_INPUT = "Input folder containing PDF, JPG, JPEG, PNG, or TXT files."
HELP_OUTPUT = "Output folder for generated .docx files (cache state is stored separately)."
HELP_RECURSIVE = "Scan subfolders recursively."
HELP_NO_RECURSIVE = "Do not scan subfolders recursively."
HELP_DEBUG = "Keep preprocessed temp files after the run (useful for inspecting OCR inputs)."
HELP_NO_DEBUG = "Do not keep preprocessing temp files after the run."
HELP_KEEP_AWAKE = "Keep the computer from going to sleep during the run (default)."
HELP_ALLOW_SLEEP = "Let the computer go to sleep during the run (by default it is kept awake)."

HELP_OCR_MODEL = "Ollama OCR vision model."
HELP_OCR_ENGINE = (
    "OCR engine: ollama (AI vision model through Ollama, default) or ppocr (the "
    "built-in reader: no server, best on printed pages; download it first with "
    "--download-reader). Online services are not offered: reading a page needs the "
    "whole page, so personal information cannot be hidden first."
)
HELP_DOWNLOAD_READER = (
    f"Download the built-in reader's model files once ({READER_SIZE_LABEL}, from "
    f"{HUGGING_FACE_HOST}) and exit."
)
HELP_DOWNLOAD_PAGE_MODELS = (
    "Download the page models of the automatic image preparation once "
    f"({PAGE_MODELS_SIZE_LABEL}, from {HUGGING_FACE_HOST}) and exit. They turn sideways "
    "pages and flatten curved ones; without them those two steps are skipped."
)
HELP_LANGUAGE = "Language passed to the OCR vision model."
HELP_PREPROCESS_METHOD = (
    "Image preparation: auto (default; looks at each page and straightens, turns and "
    "evens out photographed pages, clean scans pass unchanged) or none (the original "
    "page, with the photo's rotation tag applied)."
)
HELP_PDF_RENDER_DPI = (
    "Resolution for rendering PDF pages to images before OCR. "
    "72 = screen quality, 150 = draft OCR, 300 = recommended (default), "
    "600 = high quality. Has no effect on image inputs (JPG, PNG)."
)
HELP_RECOGNITION = "Read scans (images, PDFs without a text layer) with the OCR engine (default)."
HELP_NO_RECOGNITION = (
    "Do not read scans: use only text that already exists (TXT files, PDFs with a "
    "text layer) and skip images and scanned PDFs. No OCR engine or model is needed."
)
HELP_PDF_TEXT_LAYER = (
    "Use a PDF's embedded text instead of OCR. "
    "auto = only when every page is clean, text-only (no scans, no handwriting, "
    "annotations or form fields) (default); always = whenever every page has any text; "
    "never = always OCR. Has no effect on image or TXT inputs."
)
HELP_OLLAMA_URL = "Ollama base URL."
HELP_OCR_TIMEOUT = "Per-image OCR timeout in seconds (Ollama only)."

HELP_CORRECTION = "Enable the LLM correction step (default)."
HELP_NO_CORRECTION = "Skip the LLM correction step (DOCX is still created without column 2)."
HELP_ANONYMIZATION = (
    "Anonymize PII (person names, emails, phone numbers) in OCR text before "
    "sending to the correction LLM. Requires spacy and a language model: "
    "pip install 'teachers_teammate[privacy]' && python -m spacy download en_core_web_sm"
)
HELP_NO_ANONYMIZATION = "Disable PII anonymization before correction (default)."
HELP_CORRECTION_MODEL_HEAD = "Correction LLM model name. Auto-selected per provider when omitted:"
HELP_CORRECTION_NOTES = (
    "Notes for the AI about this class or this text, sent with every proofreading "
    f"request (at most {NOTES_LIMIT} characters). The instructions themselves are "
    "fixed by the app."
)
HELP_CORRECTION_LEVEL = "CEFR level the correction aims at (default: %(default)s)."
HELP_EXPLANATION_LANGUAGE = (
    "Language of the change explanations and the unclear table "
    "(preset name such as 'german'). Default: the language of the text."
)
HELP_CORRECTIONS_ONLY = (
    "Corrected text only: no explanations, no unclear list (one request instead of three)."
)
HELP_EXPLANATIONS = (
    "Explain the changes and list unclear expressions (default; overrides a config "
    'file that sets correction_output = "text").'
)
HELP_PROOFREADING_CONTEXT = (
    "Context window for the content review when it runs on another Ollama model than "
    "proofreading (proofreading uses 8192): 'auto' grows it with the size of the "
    "graphics card (8192, 16384 or 32768, whichever still fits), a fixed value pins it "
    "— useful for a remote server whose card the app cannot see."
)

HELP_EVALUATION = "Enable the evaluation step."
HELP_NO_EVALUATION = "Skip the evaluation step (default)."
HELP_EVALUATE_MODEL = "Evaluation model name; provider default is used when omitted."
HELP_EVALUATE_PROMPT = "System prompt used for evaluating corrected text."

HELP_PREVIEW_ONLY = (
    "Preprocess all input files and save the resulting images to the "
    "temp cache folder, then exit. No OCR, correction, or DOCX generation "
    "is performed."
)
HELP_IGNORE_MEMORY_CHECK = (
    "Run even when the models do not fit in the memory that is free now. "
    "Without this flag such a run is refused, because the computer would have to "
    "use its disk as memory and become extremely slow."
)
HELP_DOCX = "Enable DOCX generation (disabled by default; only cache state is written otherwise)."
HELP_NO_DOCX = "Skip DOCX generation; only cache state is written."
HELP_TEXT = "Also save the extracted text of each file as <name>.txt."
HELP_TEXT_ANONYMIZED = (
    "Also save the extracted text with personal data replaced by placeholders "
    "as <name>_anonymized.txt (needs spaCy)."
)
HELP_DOCX_FORMAT = (
    "DOCX output layout: 'table' = three columns (image | OCR | correction); "
    "'comments' = two columns (image | OCR) with diff corrections as Word comments."
)


def provider_help(task: str, providers: Sequence[str]) -> str:
    """Return the help of the provider argument of *task* (a ``Stage``), naming *providers*."""
    if not providers:
        return f"LLM provider for {task}."
    return f"LLM provider for {task}: {', '.join(providers)}."


def correction_model_help(defaults: Sequence[tuple[str, str]]) -> str:
    """Return the help of ``--correction-model``, naming *defaults* as ``(provider, model)``."""
    listed = ", ".join(f"{model} ({provider})" for provider, model in defaults)
    return f"{HELP_CORRECTION_MODEL_HEAD} {listed}."


# What the command line prints while it works.
DATA_FLOW_TITLE = "Where your documents are processed:"
LEAVES_COMPUTER = "  ! Data leaves this computer. Check that this is allowed for your documents."
IGNORE_MEMORY_CHECK_HINT = "Use --ignore-memory-check to run anyway."


#: What the download progress line calls the two downloads.
READER = "the built-in reader"
PAGE_MODELS = "the page models"
READER_READY = "The built-in reader is ready."
PAGE_MODELS_READY = "The page models are ready."


def data_flow_lines(flow: DataFlow) -> list[str]:
    """Return the lines that tell a teacher where the documents of a run are processed."""
    lines = [DATA_FLOW_TITLE]
    for step in flow.stages:
        marker = "!" if step.leaves_computer else "-"
        lines.append(f"  {marker} {app.stage_title(step.stage)}: {privacy.stage_badge(step)}")
    lines.append(f"  => {level_text(flow.level)}")
    if flow.leaves_computer:
        lines.append(LEAVES_COMPUTER)
    return lines


def downloading(what: str, done: int, total: int) -> str:
    """Return the progress line of a download: *what* is downloading, in megabytes of *total*."""
    return f"\rDownloading {what}: {done // 1_000_000} of {total // 1_000_000} MB"


def unknown_provider(label: str, value: str, valid: Iterable[str]) -> str:
    """Return the warning about a provider value the app does not know; it is ignored."""
    return f"Unknown {label} '{value}' — ignored. Valid providers: {', '.join(sorted(valid))}."


def not_an_integer(value: str) -> str:
    """Return what ``--pdf-render-dpi`` says about a value that is no number."""
    return f"'{value}' is not an integer"


def dpi_out_of_range(dpi: int, low: int, high: int) -> str:
    """Return what ``--pdf-render-dpi`` says about a *dpi* outside *low*..*high*."""
    return f"DPI must be between {low} and {high}, got {dpi}"


def timeout_out_of_range(seconds: int, low: int, high: int) -> str:
    """Return what ``--ocr-timeout`` says about *seconds* outside *low*..*high*."""
    return f"the timeout must be between {low} and {high} seconds, got {seconds}"


_INSTALL_IN_APP = f"Install it in the app: {WHERE_ADDONS}"

#: What the command line says for an issue whose hint does not depend on its data.
_FIXED_HINTS: dict[IssueKind, str] = {
    IssueKind.READER_MODEL_MISSING: f"Download it with: {READER_DOWNLOAD_COMMAND}",
    IssueKind.NOTHING_TO_READ: "Run again with --recognition, or queue files that have text.",
    IssueKind.OLLAMA_NOT_RUNNING: "Start Ollama, then run again.",
    IssueKind.OLLAMA_NOT_FOUND: f"Install Ollama from {OLLAMA_DOWNLOAD_URL}, then run again.",
    IssueKind.OLLAMA_UNREACHABLE: (
        "Check that the server answers at the address of --ollama-url, then run again."
    ),
    IssueKind.ADDON_RESTART_NEEDED: "Run the command again: the installation finishes at the start.",
    IssueKind.OUTPUT_IN_INPUT_FOLDER: (
        "Choose an output folder with -o/--output that is not the input folder."
    ),
    IssueKind.PACKAGE_MISSING: _INSTALL_IN_APP,
    IssueKind.NAME_DETECTION_MISSING: _INSTALL_IN_APP,
    IssueKind.PRIVACY_ADDON_MISSING: _INSTALL_IN_APP,
    IssueKind.ADDONS_OUTDATED: _INSTALL_IN_APP,
}


def cli_issue_hint(issue: RequirementIssue, *, env_key: str = "") -> str:
    """Return what the command line suggests for *issue*, or ``""`` when nothing helps.

    *env_key* is the environment variable the key of ``issue.provider`` is read from.
    """
    kind = issue.kind
    if kind in (IssueKind.MODEL_MISSING, IssueKind.NO_OLLAMA_MODEL):
        if not issue.models:
            return "Download a model in Ollama with ollama pull, then run again."
        pulls = "; ".join(f"ollama pull {model}" for model in issue.models)
        return f"Download in Ollama: {pulls}"
    if kind is IssueKind.KEY_MISSING:
        if env_key:
            return (
                f"Set {env_key} in the environment, or save the key under {WHERE_ONLINE_SERVICES}."
            )
        return f"Save the key under {WHERE_ONLINE_SERVICES}."
    return _FIXED_HINTS.get(kind, "")
