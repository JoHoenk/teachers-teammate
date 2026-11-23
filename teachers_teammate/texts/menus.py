"""The main window's menu bar: the menus, their entries and what each entry offers.

A menu entry names what it opens; its tooltip says what the teacher gets there.  The entries
of the Settings menu are the pages' own names (:mod:`settings`), so a renamed page renames its
entry too.
"""

from __future__ import annotations

from . import app

FILE = "File"
OPEN_FOLDER = "Open Folder…"
OPEN_FILES = "Open Files…"
QUIT = "Quit"

SETTINGS = "Settings"
ALL_SETTINGS = "All settings…"

TOOLS = "Tools"
BENCHMARK = "Benchmark…"
BENCHMARK_TIP = "Try a document with different text-recognition settings and compare the results."
SYSTEM_MONITOR = "System Monitor"
SYSTEM_MONITOR_TIP = "How busy this computer is, and how long each document took."
SETUP_ASSISTANT = "Setup Assistant…"
SETUP_ASSISTANT_TIP = "Answer a few questions to set up the AI for this computer."

HELP = "Help"
USER_GUIDE = "User Guide…"
OPEN_LOG_FOLDER = "Open Log Folder…"
OPEN_LOG_FOLDER_TIP = "What the app wrote down when something went wrong, for whoever helps you."
LICENSES = "Third-Party Licenses…"
ABOUT = f"About {app.NAME_TYPOGRAPHIC}…"


def entry(title: str) -> str:
    """Return a menu entry that opens a window: the page's name and an ellipsis."""
    return f"{title}…"
