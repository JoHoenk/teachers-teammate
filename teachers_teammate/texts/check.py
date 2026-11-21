"""What the Check tab says around its text.

The tab's own wording — the switch, the banners, the card and its buttons — lives in
``gui/_user_text.py`` next to the rest of the window's labels.  What is here is the wording the
tab's widgets hand to a sink themselves: the tooltips of the two editable views, the ⋯ button,
the source dialog's button, the side panel's tabs and its filter row.
"""

from __future__ import annotations

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
FILTER_LEFT_OUT = "Left out"
FILTER_LEFT_OUT_TIP = "Show the passages the model left out of the correction"
"""The boxes above the list: what is left, one per category, and the two kinds without one."""

BADGE_UNCLEAR = "unclear"
BADGE_LEFT_OUT = "left out"
BADGE_OWN = "your change"
BADGE_TAKEN_OUT = "taken out"
"""The badge of an item the list has no category for: what kind of thing it is."""


def filter_label(label: str, count: int) -> str:
    """Return a filter chip's text with its count, e.g. ``"Grammar 4"``."""
    return f"{label} {count}"
