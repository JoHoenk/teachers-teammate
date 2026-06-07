"""Smoke harness: construct → show → close every GUI window/dialog/widget.

Guards against bootstrap/lifecycle regressions (the kind that produced the
"QPixmap before QGuiApplication" / "QThread destroyed while running" aborts):
each component must build, show, and close without raising and without leaving a
background thread running.  Behaviour is covered by the per-widget tests; this
file only asserts the lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from PySide6.QtWidgets import QWidget

from teachers_teammate.application.service import AnonymizerConfig, ProcessingApplicationService
from teachers_teammate.gui._addon_installer_dialog import AddonInstallerDialog
from teachers_teammate.gui._anonymizer_config_dialog import AnonymizerConfigDialog
from teachers_teammate.gui._chart_widget import ChartWidget
from teachers_teammate.gui._collapsible_section import CollapsibleSection
from teachers_teammate.gui._config_panel import ConfigPanel
from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog
from teachers_teammate.gui._downloads_dialog import DownloadsDialog
from teachers_teammate.gui._help_dialog import AboutDialog, HelpDialog, ThirdPartyLicensesDialog
from teachers_teammate.gui._log_widget import LogWidget
from teachers_teammate.gui._ocr_config_selector import OcrConfigSelector
from teachers_teammate.gui._preview_panel import DiffWidget, PreviewPanel, ZoomableImageView
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._settings_dialog import (
    CorrectionSettingsDialog,
    EvaluationSettingsDialog,
    OCRSettingsDialog,
    SettingsDialog,
)
from teachers_teammate.gui._stats_widget import SystemStatsWidget
from teachers_teammate.gui.benchmark._compare_view import CompareView
from teachers_teammate.gui.benchmark._runs_list import RunsList
from teachers_teammate.gui.benchmark.composition import build_benchmark_window
from teachers_teammate.gui.composition import build_main_window

from ._gui_harness import FakeGuiService, exercise_lifecycle, neutralize_gui_threads

_FAKE = FakeGuiService()

WIDGET_BUILDERS: list[tuple[str, Callable[[], QWidget]]] = [
    ("main_window", lambda: build_main_window()),
    ("benchmark_window", lambda: build_benchmark_window()),
    ("ocr_settings", lambda: OCRSettingsDialog({}, app_service=_FAKE)),
    ("correction_settings", lambda: CorrectionSettingsDialog({}, "", app_service=_FAKE)),
    ("evaluation_settings", lambda: EvaluationSettingsDialog({}, "", app_service=_FAKE)),
    ("settings", lambda: SettingsDialog({}, app_service=_FAKE)),
    # AnonymizerConfigDialog types app_service as the concrete service, so use a real one.
    (
        "anonymizer",
        lambda: AnonymizerConfigDialog(
            "English", AnonymizerConfig(), ProcessingApplicationService()
        ),
    ),
    ("addon_installer", lambda: AddonInstallerDialog("paddle")),
    ("downloads", lambda: DownloadsDialog()),
    ("about", lambda: AboutDialog()),
    ("help", lambda: HelpDialog()),
    ("licenses", lambda: ThirdPartyLicensesDialog()),
    ("dependency_guard", lambda: DependencyGuardDialog("correction", [])),
    ("config_panel", lambda: ConfigPanel()),
    ("results_table", lambda: ResultsTable()),
    ("preview_panel", lambda: PreviewPanel()),
    ("zoomable_image", lambda: ZoomableImageView()),
    ("diff_widget", lambda: DiffWidget()),
    ("log_widget", lambda: LogWidget()),
    ("chart_widget", lambda: ChartWidget()),
    ("system_stats", lambda: SystemStatsWidget()),
    ("collapsible", lambda: CollapsibleSection("Section")),
    ("ocr_config_selector", lambda: OcrConfigSelector(app_service=_FAKE)),
    ("compare_view", lambda: CompareView()),
    ("runs_list", lambda: RunsList()),
]


@pytest.fixture(autouse=True)
def _hermetic_gui(monkeypatch, tmp_path) -> None:
    """Keep the smoke tests offline and storage hermetic."""
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "storage"))
    neutralize_gui_threads(monkeypatch)


@pytest.mark.gui
@pytest.mark.parametrize(("name", "builder"), WIDGET_BUILDERS, ids=[n for n, _ in WIDGET_BUILDERS])
def test_widget_lifecycle(qtbot, name: str, builder: Callable[[], QWidget]) -> None:
    """
    Given  a GUI window/dialog/widget builder
    When   the component is constructed, shown, and closed
    Then   it does so without raising and leaves no background thread running
    """
    _ = name
    widget = builder()
    exercise_lifecycle(qtbot, widget)
