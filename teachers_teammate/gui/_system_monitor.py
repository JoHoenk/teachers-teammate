"""System Monitor window: the per-file chart of a run and the live CPU / RAM / GPU gauges."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtWidgets import QSplitter, QVBoxLayout, QWidget

from ._chart_widget import ChartWidget
from ._stats_widget import SystemStatsWidget

GEOMETRY_KEY = "gui/monitor_geometry"
_QSETTINGS_ORG = "TeachersTeammate"
_QSETTINGS_APP = "TeachersTeammate"


class SystemMonitorWindow(QWidget):
    """Own window with the run chart above the live stats.

    The main window builds it at start-up and keeps it hidden, so the chart collects the
    data of every run even while nobody looks.  The stats poll only while the window is
    shown (see :class:`SystemStatsWidget`).

    It is a window of its own that can stay open next to the main window while a batch runs.
    The main window does not own it (no ``parent``): an owned window stays on top of its
    owner on Windows and could never be placed behind it.  The main window closes it when it
    quits, like the Benchmark window.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("System Monitor")
        self.chart = ChartWidget()
        self.stats = SystemStatsWidget()
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.chart)
        splitter.addWidget(self.stats)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(splitter)
        self.resize(560, 520)
        geometry = QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).value(GEOMETRY_KEY)
        if geometry is not None:
            self.restoreGeometry(geometry)

    def hideEvent(self, event: QHideEvent) -> None:  # Qt override
        """Remember where the window was; closing it only hides it."""
        QSettings(_QSETTINGS_ORG, _QSETTINGS_APP).setValue(GEOMETRY_KEY, self.saveGeometry())
        super().hideEvent(event)

    def show_and_raise(self) -> None:
        """Show the window, or bring it to the front when it is already open."""
        self.show()
        self.raise_()
        self.activateWindow()
