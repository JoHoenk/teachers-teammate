"""What the Check tab says around its text.

The tab's own wording — the switch, the banners, the card and its buttons — lives in
``texts/review.py`` next to the rest of the window's labels.  What is here is the wording the
tab's widgets hand to a sink themselves: the tooltips of the two editable views, the ⋯ button,
the source dialog's button, the side panel's tabs and its filter row.
"""

from __future__ import annotations

from ..domain.ollama_discovery import WaitDetail, WaitKind

MORE = "\u22ef"
"""The label of the ⋯ button: everything else that can be done with this document."""

MORE_TIP = "More for this document"

SOURCE_CLOSE = "Close"

FORMAT_BOLD = "Bold"
FORMAT_ITALIC = "Italic"
FORMAT_UNDERLINE = "Underline"
FORMAT_STRIKE = "Crossed out"
FORMAT_INSERT = "Inserted above the line"
"""The formatting an editable view offers in its context menu, with the key that applies it."""

READ_TIP = (
    "The text as it was read. Fix a reading mistake here; the corrections of this "
    "document are removed when you do."
)
"""Says what typing into *What was read* costs, next to the banner that says it again."""

SCAN_PREPARED = "Prepared"
SCAN_PREPARED_TIP = "Show the pages as they were fixed for reading"
SCAN_ORIGINAL = "Original"
SCAN_ORIGINAL_TIP = "Show the pages as scanned, before they were prepared"
SCAN_SHOWING_ORIGINAL = "Showing the original pages"
"""The scan's two-part switch, their tooltips and the caption while *Original* is on."""

SCAN_ZOOM_IN = "+"
SCAN_ZOOM_IN_TIP = "Zoom in (Ctrl + mouse wheel)"
SCAN_ZOOM_OUT = "\u2212"
SCAN_ZOOM_OUT_TIP = "Zoom out (Ctrl + mouse wheel)"
SCAN_FIT_PAGE = "Fit page"
SCAN_FIT_PAGE_TIP = "Show a whole page in the pane"
SCAN_FIT_WIDTH = "Fit width"
SCAN_FIT_WIDTH_TIP = "Make the page as wide as the pane; scroll down to read it"
"""The scan's zoom buttons and their tooltips; the checked one says which fit is on."""

TEXT_ZOOM_IN = "T+"
TEXT_ZOOM_IN_TIP = "Larger text (Ctrl + mouse wheel)"
TEXT_ZOOM_OUT = "T\u2212"
TEXT_ZOOM_OUT_TIP = "Smaller text (Ctrl + mouse wheel)"
"""The header's buttons that change the size of the texts, and their tooltips."""

TAB_DOCUMENTS = "Documents"
TAB_DOCUMENTS_TIP = "Documents (Ctrl+1)"
TAB_CHECK = "Check"
TAB_CHECK_TIP = "Check (Ctrl+2)"
"""The window's two tabs and the tooltips that name their shortcuts."""


def tab_documents_progress(done: int, total: int) -> str:
    """The Documents tab while a batch runs: how many documents are through."""
    return f"{TAB_DOCUMENTS} \u00b7 {done} of {total}"


def tab_check_named(name: str) -> str:
    """The Check tab with the name of the document it shows."""
    return f"{TAB_CHECK} \u2014 {name}"


def tab_check_tip_named(name: str) -> str:
    """The Check tab's tooltip: the document's full name above the shortcut."""
    return f"{name}\n{TAB_CHECK_TIP}"


NEXT_STEP = "Check the text against the scan and fix reading mistakes, then proofread."
NEXT_STEP_SET_UP = "Set up proofreading…"
NEXT_STEP_SET_UP_TIP = "Choose the AI that proofreads, in Settings"
NEXT_STEP_READ_BADLY = "Read badly? Change how pages are read…"
NEXT_STEP_READ_BADLY_TIP = "Open Settings where the reading of the pages is chosen"
"""The strip above a transcription that has not been proofread: what to do next, and how to
read the pages differently when the reading itself is poor."""

RUN_PROOFREADING = "Proofreading with {model}…"
RUN_PROOFREADING_PLAIN = "Proofreading…"
RUN_PREPARING = "Preparing the pages…"
RUN_READING = "Reading the text…"
RUN_WAITING = "Waiting for its turn in this run…"
RUN_TIME_LEFT_SEPARATOR = " · "
"""The strip in the header while the open document is in a run: what is being done to it."""

RUN_WAITING_FOR_MODEL = "Waiting for Ollama: it is still working with {other}"
RUN_LOADING_MODEL = "Loading {model} into memory…"
RUN_QUEUED_FOR_MODEL = (
    "The first file waits until Ollama has finished with {other} and loaded {model}"
)
"""Why a request to Ollama has no answer yet: another model is generating, this one loads, or
another one is in the way before the run starts."""

PROGRESS_PREPROCESSING = "preprocessing…"
PROGRESS_OCR = "OCR…"
PROGRESS_CORRECTING = "correcting…"
"""The step named after a file's name in the progress line of a run."""

TAB_CORRECTIONS = "Corrections"
TAB_CONTENT = "Content"
"""The side panel's two tabs: the list of corrections, and the content review."""

FILTER_NOT_LOOKED_AT = "Not looked at"
FILTER_NOT_LOOKED_AT_TIP = "Narrow the list to the corrections you have not looked at yet"
FILTER_CATEGORY_TIP = "Show the {category} corrections"
FILTER_UNCLEAR = "Unclear"
FILTER_UNCLEAR_TIP = "Show the passages whose meaning is unclear"
FILTER_LEFT_OUT = "Not proofread"
FILTER_LEFT_OUT_TIP = "Show the passages the AI did not proofread"
"""The boxes above the list: what is left, one per category, and the two kinds without one."""

BADGE_UNCLEAR = "unclear"
BADGE_LEFT_OUT = "not proofread"
BADGE_OWN = "your change"
BADGE_TAKEN_OUT = "taken out"
"""The badge of an item the list has no category for: what kind of thing it is."""

# ── The legend of the marks ──────────────────────────────────────────────

LEGEND_TIP = "What the marks mean"
"""The info button beside *Next*: its accessible name, and what its legend is."""

LEGEND_CORRECTION = "Correction by the AI"
LEGEND_OWN = "Your change"
LEGEND_UNCLEAR = "Unclear or not proofread: read it yourself"
LEGEND_TAKEN_OUT = "Taken out: the student's words are back"
LEGEND_PALER = "Paler: a spelling or punctuation change"
LEGEND_UNDERLINE = (
    "Underlined solid for a change, dotted for something unclear or not proofread, dashed for "
    "what you took out"
)
LEGEND_STRIKES = "With corrections"
LEGEND_STRIKE_MODEL = "removed by the AI"
LEGEND_STRIKE_STUDENT = "crossed out by the student"
LEGEND_LIST = "In the list: \u2022 is there to look at, a grey row you have read"
"""The legend of the tab's marks, one line per colour and per mark of the text and the list."""


def filter_label(label: str, count: int) -> str:
    """Return a filter chip's text with its count, e.g. ``"Grammar 4"``."""
    return f"{label} {count}"


# ── The strip of a document's page: queue buttons and Copy ────────────────

PREVIOUS_DOCUMENT = "Previous document in the queue"
NEXT_DOCUMENT = "Next document in the queue"
PREVIOUS_ARROW = "\u25c0"
NEXT_ARROW = "\u25b6"
COPY = "Copy"
COPY_TOOLTIP = "Copy the text of this document, or save it as a text file"
COPIED = "Copied"
COPY_RAW = "Copy what was read"
COPY_RAW_ANONYMIZED = "Copy what was read, names hidden"
COPY_CORRECTED = "Copy corrected text"
COPY_CORRECTED_ANONYMIZED = "Copy corrected text, names hidden"
SAVE_RAW = "Save what was read as text file\u2026"
SAVE_ANONYMIZED = "Save it with names hidden\u2026"


def run_proofreading(model: str) -> str:
    """Return the run strip's line for proofreading with *model*."""
    return RUN_PROOFREADING.format(model=model)


def run_waiting_for_model(other: str) -> str:
    """Return why a request waits: Ollama still works with the model *other*."""
    return RUN_WAITING_FOR_MODEL.format(other=other)


def run_loading_model(model: str) -> str:
    """Return why a request waits: *model* is loaded into memory."""
    return RUN_LOADING_MODEL.format(model=model)


def run_queued_for_model(other: str, model: str) -> str:
    """Return why the first file waits: Ollama finishes with *other*, then loads *model*."""
    return RUN_QUEUED_FOR_MODEL.format(other=other, model=model)


def filter_category_tip(category: str) -> str:
    """Return the tooltip of the filter chip of the correction *category*."""
    return FILTER_CATEGORY_TIP.format(category=category)


def wait_detail_text(detail: WaitDetail) -> str:
    """Return what to say about a request to Ollama that has no answer yet."""
    if detail.kind is WaitKind.BUSY:
        return run_waiting_for_model(detail.other)
    if detail.kind is WaitKind.LOADING:
        return run_loading_model(detail.model)
    if detail.kind is WaitKind.QUEUED:
        return run_queued_for_model(detail.other, detail.model)
    return ""
