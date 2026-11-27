"""The app itself: its name, the names of its steps, and the Help, About and license windows.

The name is spelled once here, so the main window, the windows' titles and the About text can
never drift apart.  The step names are shared the same way: the boxes of the main window, the
lines of a run, the CLI and the list of remembered answers all name a step through them.
"""

from __future__ import annotations

from html import escape

from ..domain.data_location import STAGE_ANONYMIZATION
from ..domain.stages import Stage
from .settings import WHERE_GENERAL

#: The app's name, and the titles of the windows that are the app's own.
NAME = "Teacher's Teammate"
#: The name with a typographic apostrophe, for a menu entry.
NAME_TYPOGRAPHIC = NAME.replace("'", "\u2019")
MAIN_WINDOW_TITLE = NAME

GUIDE_TITLE = f"User Guide — {NAME}"
ABOUT_TITLE = f"About {NAME}"
LICENSES_TITLE = f"Licenses — {NAME}"

#: The license the app is under, where a teacher reads it, and who holds the rights.  One place:
#: the About window and the ``--version`` notice name the same license.
COPYRIGHT = "Copyright 2026 Teacher's Teammate contributors"
LICENSE_TITLE = "GNU General Public License, version 3 or later"
#: The same license by its SPDX identifier, for a line that has room for one name only.
LICENSE_ID = "GPL-3.0-or-later"
LICENSE_URL = "https://www.gnu.org/licenses/gpl-3.0.html"
NO_WARRANTY = (
    "This is free software: you are free to change and redistribute it. "
    "There is NO WARRANTY, to the extent permitted by law."
)

#: The two tabs of the Licenses window, and the menu entry that opens it.
LICENSE_TAB = "This App"
THIRD_PARTY_TAB = "Third-Party Licenses"
LICENSES_NAME = "Licenses"

#: The user guide's three tabs, one per page of ``docs/``.
GUIDE_OVERVIEW = "Overview"
GUIDE_USING = "Using the App"
GUIDE_ADVANCED = "Advanced Guide"

#: The marks the window uses where a word would take too much room.
DISMISS_MARK = "✕"

#: The name of each step of a run, as a teacher knows it, in the order the steps run.
STAGE_TITLES: dict[str, str] = {
    Stage.OCR: "Text recognition",
    STAGE_ANONYMIZATION: "Personal info removal",
    Stage.CORRECTION: "Proofreading",
    Stage.EVALUATION: "Content review",
}


def stage_title(stage: str) -> str:
    """Return the name of a step, or *stage* itself when it is none of the four."""
    return STAGE_TITLES.get(stage, stage)


def guide_page_missing(name: str) -> str:
    """Explain that one page of the user guide is not in this installation."""
    return f"Page not found: {name}"


LICENSE_MISSING = (
    "The license of this app is not part of this installation.\n\n"
    "It ships as teachers_teammate/assets/LICENSE.txt."
)

THIRD_PARTY_MISSING = (
    "Third-party license information is not available in this installation.\n\n"
    "Run tools/licenses/inventory.py --compile to generate it."
)

_PYSIDE_TAIL = (
    "PySide6-Essentials, which is part of the base install. "
    "Reinstall with: pip install --force-reinstall teachers-teammate"
)

#: Raised when the GUI cannot start because Qt is not installed.
MISSING_PYSIDE = f"The window needs {_PYSIDE_TAIL}"


UPDATE_DOWNLOAD = "Download"


def update_available(version: str, url: str) -> str:
    """Return the banner that offers a newer version, and how to stop the check.

    The banner is rich text and *version* and *url* come from the release feed: both are escaped.
    """
    return (
        f"Version <b>{escape(version)}</b> is available — "
        f'<a href="{escape(url)}">{UPDATE_DOWNLOAD}</a>'
        f" · turn this check off under {WHERE_GENERAL}"
    )


# ── The About window ──────────────────────────────────────────────────────────

ABOUT_NAME = f"<h2>{NAME}</h2>"


def about_version(version: str) -> str:
    """Return the version line under the app's name."""
    return f"<p>Version <b>{version}</b></p>"


ABOUT_DESCRIPTION = (
    "<p>Batch OCR pipeline for scanned documents and handwritten notes.<br>"
    "Extracts text from PDFs and images, optionally produces<br>"
    "proofread DOCX reports via a local language model.</p>"
)

ABOUT_CREDITS = (
    '<p style="color:#888; font-size:8pt;">'
    "Powered by Ollama &bull; OpenCV &bull; PaddleOCR models &bull; LangChain<br>"
    "pypdfium2 &bull; PySide6 &bull; qdarkstyle<br><br>"
    'Inspired by <a href="https://github.com/imanoop7/Ollama-OCR">'
    "Ollama-OCR</a> by imanoop7."
    "</p>"
)

#: The license block under the version: who holds the rights, the license, that there is no
#: warranty, and where the license is read.
ABOUT_LICENSE = (
    '<p style="font-size:8pt;">'
    f"{COPYRIGHT}<br>"
    f"Licensed under the {LICENSE_TITLE}.<br>"
    f"{NO_WARRANTY}<br>"
    f'<a href="{LICENSE_URL}">{LICENSE_URL}</a><br>'
    f"Help &rarr; {LICENSES_NAME}"
    "</p>"
)
