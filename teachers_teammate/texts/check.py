"""What the Check tab says around its text.

The tab's own wording — the switch, the banners, the card and its buttons — lives in
``gui/_user_text.py`` next to the rest of the window's labels.  What is here is the wording the
tab's widgets hand to a sink themselves: the tooltips of the two editable views, the ⋯ button and
the source dialog's button.
"""

from __future__ import annotations

MORE = "\u22ef"
"""The label of the ⋯ button: everything else that can be done with this document."""

MORE_TIP = "More for this document"

SOURCE_CLOSE = "Close"

FORMAT_TIP = (
    "Ctrl+B bold · Ctrl+I italic · Ctrl+U underline · "
    "Ctrl+Shift+X crossed out · Ctrl+Shift+= inserted above the line"
)
"""What the two editable views accept, so the teacher does not have to guess."""

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
