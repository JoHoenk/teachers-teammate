"""The object names the widgets carry, so a script can find them without reaching inside.

A Qt object name is how a widget is looked up from outside the app
(``window.findChild(QAbstractButton, handles.RUN_BUTTON)``).  The app walkthrough sets no
attribute and calls no private method: it clicks what a teacher clicks, and this is the list of
what that is.  A name lives here, is set where the widget is built, and is looked up by the
script; a widget that is renamed or removed fails the walkthrough's first step, naming it.

Windows the main window does not own (System Monitor) are found in
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
OUTPUT_FOLDER_BUTTON = "outputFolderButton"
CHECK_PAGE = "checkPage"
READ_VIEW = "readView"
FINAL_VIEW = "finalView"
"""Where the teacher types a correction when the text was proofread: the read view is read-only."""
FINAL_VIEW_BUTTON = "finalViewButton"
"""The switch that puts the corrected text in front."""
ITEM_LIST = "itemList"
"""The list of the proofreading's items, in text order."""
CHECKED_BUTTON = "checkedButton"
"""Marks the chosen item as checked, or takes that back."""

# ── menu entries ─────────────────────────────────────────────────────────────

ALL_SETTINGS = "allSettingsAction"
SYSTEM_MONITOR = "systemMonitorAction"
COMPARE_MODELS = "compareModelsAction"
SETUP_ASSISTANT = "setupAssistantAction"
USER_GUIDE = "userGuideAction"
OPEN_LOG_FOLDER = "openLogFolderAction"
LICENSES = "licensesAction"
ABOUT = "aboutAction"

# ── the Settings window ──────────────────────────────────────────────────────

ADDONS_UPDATE_BANNER = "addonsUpdateBanner"
"""The line that asks for the add-ons to be installed again; hidden while they are current."""
ADDONS_UPDATE_BUTTON = "addonsUpdateButton"
"""The button on that line; it waits while the round that installs them runs."""

# ── the windows of their own ─────────────────────────────────────────────────

MONITOR_WINDOW = "monitorWindow"

COMPARE_WINDOW = "compareWindow"
COMPARE_DOCUMENT = "compareDocument"
COMPARE_MODELS_LIST = "compareModelsList"
"""The models to tick, each with what the catalog says of it, its time and its state."""
COMPARE_RUN_BUTTON = "compareRunButton"
COMPARE_AGAIN_BUTTON = "compareAgainButton"
"""Asks the chosen model again and keeps both answers."""
COMPARE_STOP_BUTTON = "compareStopButton"
COMPARE_USE_BUTTON = "compareUseButton"
"""Makes the chosen model the one that proofreads from now on."""
COMPARE_CHANGES_TABLE = "compareChangesTable"
"""The *Changes* tab's table: one row per place a model changed, one column per model."""
COMPARE_MARK_RIGHT = "compareMarkRight"
COMPARE_MARK_WRONG = "compareMarkWrong"
"""The two verdicts a teacher gives one option of the chosen change."""

CHECK_COMPARE_BUTTON = "checkCompareButton"
"""The Check tab's *Compare models…*, which opens the window on the open document."""
