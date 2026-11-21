"""The Settings window's page names and the "where to change it" phrases.

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

WHERE_RESULTS = "Results, in the main window"
WHERE_STEPS = "What should happen, in the main window"
WHERE_SETUP = "Tools → Setup Assistant…"

OPEN_ADDONS = "Open Add-ons…"


def license_tooltip(license_text: str | None) -> str:
    """Return the tooltip of an add-on's row: its license, or nothing when it is unknown."""
    return f"License: {license_text}" if license_text else ""
