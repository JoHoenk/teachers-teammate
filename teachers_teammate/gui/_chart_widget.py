"""Stacked horizontal bar chart: OCR time vs correction time per file."""

from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis,
    QBarSet,
    QChart,
    QChartView,
    QHorizontalStackedBarSeries,
    QValueAxis,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ._constants import _COLOUR_CORRECTION_BAR, _COLOUR_OCR_BAR


class ChartWidget(QChartView):
    """Stacked horizontal bar chart: OCR time (blue) + Correction time (orange).

    Uses PySide6-Charts with a dark theme, smooth animations, and hover tooltips.
    Public API is identical to the previous matplotlib version so MainWindow is
    unchanged: :meth:`update_data` and :meth:`clear_data`.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        chart = QChart()
        chart.setTheme(QChart.ChartTheme.ChartThemeDark)
        chart.setAnimationOptions(QChart.AnimationOption.SeriesAnimations)
        chart.setTitle("Processing time per file")
        legend = chart.legend()
        assert legend is not None
        legend.setVisible(True)
        legend.setAlignment(Qt.AlignmentFlag.AlignBottom)
        super().__init__(chart, parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(200)

    def _build_series(
        self,
        ocr_times: list[float],
        correction_times: list[float],
    ) -> QHorizontalStackedBarSeries:
        ocr_set = QBarSet("OCR")
        ocr_set.setColor(QColor(_COLOUR_OCR_BAR))
        corr_set = QBarSet("Correction")
        corr_set.setColor(QColor(_COLOUR_CORRECTION_BAR))
        for o, c in zip(ocr_times, correction_times, strict=False):
            ocr_set.append(o)
            corr_set.append(c)
        series = QHorizontalStackedBarSeries()
        series.append(ocr_set)
        series.append(corr_set)
        series.hovered.connect(self._on_hovered)
        return series

    @staticmethod
    def _on_hovered(status: bool, index: int, barset: QBarSet) -> None:
        """Show a tooltip with the bar value when the mouse hovers over it."""
        if status:
            QToolTip.showText(QCursor.pos(), f"{barset.label()}: {barset.at(index):.2f} s")

    def update_data(
        self,
        names: list[str],
        ocr_times: list[float],
        correction_times: list[float],
    ) -> None:
        """Rebuild chart with updated timing data."""
        chart = self.chart()
        assert chart is not None
        chart.removeAllSeries()
        for axis in chart.axes():
            chart.removeAxis(axis)
        if not names:
            return

        series = self._build_series(ocr_times, correction_times)
        chart.addSeries(series)

        y_axis = QBarCategoryAxis()
        y_axis.append(names)
        chart.addAxis(y_axis, Qt.AlignmentFlag.AlignLeft)
        series.attachAxis(y_axis)

        total_times = [o + c for o, c in zip(ocr_times, correction_times, strict=False)]
        x_axis = QValueAxis()
        x_axis.setTitleText("seconds")
        x_axis.setLabelFormat("%.1f")
        x_axis.setTickCount(6)
        x_axis.setRange(0, max(total_times) * 1.2 if total_times else 1.0)
        chart.addAxis(x_axis, Qt.AlignmentFlag.AlignBottom)
        series.attachAxis(x_axis)

    def clear_data(self) -> None:
        chart = self.chart()
        assert chart is not None
        chart.removeAllSeries()
        for axis in chart.axes():
            chart.removeAxis(axis)
