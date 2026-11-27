"""The Benchmark window: choosing a document, running a configuration, comparing two runs.

The window is the same pipeline on one file with other settings; its wording stays apart from
the main window's, so a teacher reads it as the one experiment it is.
"""

from __future__ import annotations

from .app import BENCHMARK_WINDOW_TITLE

WINDOW_TITLE = BENCHMARK_WINDOW_TITLE

DOCUMENT_GROUP = "Document"
NO_DOCUMENT = "No document selected."
CHOOSE_DOCUMENT = "Choose document…"

CONFIGURATION_GROUP = "OCR configuration"
RUN_AND_STORE = "Run && store"
STOP = "Stop"

COMPARE_TAB = "Compare"
PROGRESS_TAB = "Progress"

#: The compare view: the two runs' panes, the diff, and the line above them.
PANE_RUN_A = "Run A"
PANE_RUN_B = "Run B"
DIFF_LABEL = "Diff (A → B)"
SELECT_TWO_RUNS = "Select run A and run B to compare."


def similarity(percent: float, words_a: int, chars_a: int, words_b: int, chars_b: int) -> str:
    """Return the line above the diff: how alike the two runs are, and how much each wrote."""
    return (
        f"Similarity: {percent:.1f}%   ·   "
        f"A: {words_a} words / {chars_a} chars   ·   "
        f"B: {words_b} words / {chars_b} chars"
    )


#: The list of stored runs and its buttons.
STORED_RUNS_LABEL = "Stored runs"
SET_AS_A = "Set as A"
SET_AS_B = "Set as B"
DELETE = "Delete"
CLEAR_ALL = "Clear all runs for this document"


# ── The lines the window writes to its progress pane ──────────────────────────

PROGRESS_STOP_REQUESTED = "Stop requested…"
PROGRESS_RUN_STORED = "Run stored."
PROGRESS_RUN_STOPPED = "Stopped."


def progress_run_started(name: str) -> str:
    """Return the line that says an OCR run began on the document *name*."""
    return f"Starting OCR run for {name}…"
