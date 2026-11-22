"""The object names the widgets carry, so a script can find them without reaching inside.

A Qt object name is how a widget is looked up from outside the app
(``window.findChild(QAbstractButton, handles.RUN_BUTTON)``).  The app walkthrough sets no
attribute and calls no private method: it clicks what a teacher clicks, and this is the list of
what that is.  A name lives here, is set where the widget is built, and is looked up by the
script; a widget that is renamed or removed fails the walkthrough's first step, naming it.

Windows the main window does not own (Benchmark, System Monitor) are found in
``QApplication.topLevelWidgets()`` rather than under the main window.
"""

from __future__ import annotations

# ── main window ──────────────────────────────────────────────────────────────

RUN_BUTTON = "runButton"
STOP_BUTTON = "stopButton"
EXPORT_BUTTON = "exportButton"
MAIN_TABS = "mainTabs"
RESULTS_TABLE = "resultsTable"
OPEN_DOCUMENT = "openDocument"
"""Each row's Open button; the property below names the row it belongs to."""
SOURCE_ID_PROPERTY = "sourceId"
INPUT_FOLDER_BUTTON = "inputFolderButton"
INPUT_PATH = "inputPath"
OUTPUT_PATH = "outputPath"
CHECK_PAGE = "checkPage"
READ_VIEW = "readView"

# ── menu entries ─────────────────────────────────────────────────────────────

ALL_SETTINGS = "allSettingsAction"
BENCHMARK = "benchmarkAction"
SYSTEM_MONITOR = "systemMonitorAction"
SETUP_ASSISTANT = "setupAssistantAction"
USER_GUIDE = "userGuideAction"
LICENSES = "licensesAction"
ABOUT = "aboutAction"

# ── the windows of their own ─────────────────────────────────────────────────

BENCHMARK_WINDOW = "benchmarkWindow"
MONITOR_WINDOW = "monitorWindow"
