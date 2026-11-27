"""The Settings window: its page names, what each page is about, and its own controls.

A sentence that tells a teacher where a setting lives names the page through these
constants, so renaming a page renames every sentence that points at it.
"""

from __future__ import annotations

PAGE_GENERAL = "General"
PAGE_AI_MODELS = "AI models"
PAGE_SCAN_QUALITY = "Scan quality"
PAGE_PRIVACY = "Privacy"
PAGE_AI_SETUP = "This computer"
PAGE_ONLINE_SERVICES = "Online services"
PAGE_ADDONS = "Add-ons"

WHERE_GENERAL = f"Settings → {PAGE_GENERAL}"
WHERE_AI_MODELS = f"Settings → {PAGE_AI_MODELS}"
WHERE_SCAN_QUALITY = f"Settings → {PAGE_SCAN_QUALITY}"
WHERE_PRIVACY = f"Settings → {PAGE_PRIVACY}"
WHERE_AI_SETUP = f"Settings → {PAGE_AI_SETUP}"
WHERE_ONLINE_SERVICES = f"Settings → {PAGE_ONLINE_SERVICES}"
WHERE_ADDONS = f"Settings → {PAGE_ADDONS}"

TAB_READING_MODELS = "Reading models"
WHERE_READING_MODELS = f"{WHERE_ADDONS} → {TAB_READING_MODELS}"

#: What each page is for: the tooltip of its entry in the main window's Settings menu.
PAGE_TIP_GENERAL = (
    "How the app behaves while it works, for example keeping the computer awake during a run."
)
PAGE_TIP_AI_MODELS = "Which AI reads the handwriting, proofreads and reviews."
PAGE_TIP_AI_SETUP = (
    "What the AI models need from this computer: how powerful it is and where the local AI "
    "server is."
)
PAGE_TIP_ONLINE_SERVICES = "The access keys (API keys) of the online AI services you can use."
PAGE_TIP_SCAN_QUALITY = "How scans are cleaned up before the text is read."
PAGE_TIP_PRIVACY = (
    "What may leave this computer, and how names and other personal information are found "
    "and replaced."
)
PAGE_TIP_ADDONS = "Download AI models and install optional parts."

WHERE_RESULTS = "Results, in the main window"
WHERE_STEPS = "What should happen, in the main window"
WHERE_SETUP = "Tools → Setup Assistant…"

OPEN_ADDONS = "Open Add-ons…"

#: What each page is about: the line under its name.
PAGE_INTRO_GENERAL = "How the app behaves while it works, and what it does at start."
PAGE_INTRO_AI_MODELS = (
    "Which AI reads the handwriting, and which AI proofreads and reviews the text. "
    "You set this once."
)
PAGE_INTRO_AI_SETUP = (
    f"What {PAGE_AI_MODELS} needs from this computer: how powerful it is (the model "
    "suggestions follow it), where its local AI server is, and how fast it runs your models. "
    "Set up once; usually nothing to change."
)
PAGE_INTRO_ONLINE_SERVICES = (
    f"Access keys for the online AI services you can choose on {PAGE_AI_MODELS}. Not "
    "needed while everything runs on this computer."
)
PAGE_INTRO_SCAN_QUALITY = (
    "How scans and photos are prepared before the text is read. Automatic suits most "
    "documents; Preview… shows the effect on one of your own."
)
PAGE_INTRO_PRIVACY = (
    "What may leave this computer, and how names and other personal information are found "
    "and replaced."
)
PAGE_INTRO_ADDONS = (
    "Download AI models for this computer and install optional parts. Installs start right "
    "away and take effect immediately."
)

#: The window itself: its title, and the two controls below the pages.
WINDOW_TITLE = "Settings"
RESTORE_DEFAULTS = "Restore defaults…"
ADDONS_CANCEL_NOTE = "Downloads and installs start right away and are not undone by Cancel."

# ── General ───────────────────────────────────────────────────────────────────

WHILE_PROCESSING_GROUP = "While documents are processed"
KEEP_AWAKE_LABEL = "Keep the computer awake"
KEEP_AWAKE_TOOLTIP = (
    "Stops the computer from going to sleep on its own while documents are processed,\n"
    "so a long run is not interrupted. The screen may still turn off."
)
KEEP_AWAKE_NOTE = (
    "The screen may still turn off. Closing the lid, or a nearly empty battery, can still "
    "put the computer to sleep."
)
AT_START_GROUP = "When the app starts"
CHECK_UPDATES_LABEL = "Check for updates when the app starts"
CHECK_UPDATES_TOOLTIP = (
    "Asks GitHub once whether a newer version exists and shows a note if one does.\n"
    "Only the question itself is sent, nothing from your documents. The change takes effect "
    "at the next start."
)
CHECK_UPDATES_NOTE = (
    "This is the only thing the app sends without being asked. Turned off, it never contacts "
    "GitHub; the change takes effect the next time the app starts."
)

LOG_GROUP = "If something goes wrong"
LOG_DETAIL_LABEL = "Log detail"
LOG_DETAIL_NORMAL = "Normal"
LOG_DETAIL_DETAILED = "Detailed"
LOG_DETAIL_TOOLTIP = (
    "How much the app writes into its log file: what it did and anything that went wrong\n"
    "(Normal), or the details a developer needs as well (Detailed)."
)
LOG_DETAIL_NOTE = (
    "The app writes down what it did and what went wrong in a log file on this computer "
    "(Help → Open Log Folder). Normal is enough to ask for help. Detailed adds the details a "
    "developer needs and makes the file grow faster."
)

# ── Scan quality ──────────────────────────────────────────────────────────────

PREPARING_GROUP = "Preparing scans and photos"


def license_tooltip(license_text: str | None) -> str:
    """Return the tooltip of an add-on's row: its license, or nothing when it is unknown."""
    return f"License: {license_text}" if license_text else ""
