"""Stacked horizontal bar chart: OCR time vs proofreading time per file.

Drawn with ``QPainter`` rather than QtCharts: the chart is a side display, and QtCharts lives
in ``PySide6-Addons`` (161 MB installed) which nothing else in the app needs. The public API
is unchanged — :meth:`ChartWidget.update_data` and :meth:`ChartWidget.clear_data` — so the
main window and the System Monitor stay as they are.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFontMetrics, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizePolicy, QToolTip, QWidget

from ..texts import main_window as txt
from ._constants import _COLOUR_CORRECTION_BAR, _COLOUR_OCR_BAR

_LEGEND: tuple[tuple[str, str], ...] = (
    (txt.CHART_LEGEND_OCR, _COLOUR_OCR_BAR),
    (txt.CHART_LEGEND_PROOFREADING, _COLOUR_CORRECTION_BAR),
)
_GROW_MS = 300
_MARGIN = 8
_ROW_GAP = 4
_MIN_ROW = 3.0
_MAX_ROW = 34.0
_MIN_NAME_WIDTH = 60.0
_MAX_NAME_WIDTH = 180.0
_NAME_SHARE = 0.28


def nice_ticks(max_value: float, count: int = 6) -> list[float]:
    """Return round tick values from 0 up to at least *max_value*.

    The step is 1, 2 or 5 times a power of ten, so the labels read 0.5, 1, 2, 5, 10, 20, …
    rather than 0.37, 0.74, …  *count* is the number of intervals aimed for.
    """
    if max_value <= 0 or count < 2:
        return [0.0]
    raw = max_value / (count - 1)
    magnitude = 10.0 ** math.floor(math.log10(raw))
    step = 10.0 * magnitude
    for factor in (1.0, 2.0, 5.0, 10.0):
        if factor * magnitude >= raw:
            step = factor * magnitude
            break
    ticks = [0.0]
    value = step
    while value < max_value - step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    ticks.append(round(value, 10))
    return ticks


@dataclass(frozen=True)
class _Layout:
    """Where the parts of the chart sit, shared by painting and hit-testing."""

    plot: QRectF
    row_height: float
    axis_max: float
    ticks: list[float]


class ChartWidget(QWidget):
    """Stacked horizontal bar chart: OCR time (blue) + proofreading time (orange).

    Bars grow in when new data arrives, the file names are elided when the panel is narrow,
    and hovering a segment shows its time.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._names: list[str] = []
        self._ocr: list[float] = []
        self._correction: list[float] = []
        self._progress = 1.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(_GROW_MS)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._on_grow)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(200)
        self.setMouseTracking(True)

    # ── Public API ────────────────────────────────────────────────────────────

    def update_data(
        self,
        names: list[str],
        ocr_times: list[float],
        correction_times: list[float],
    ) -> None:
        """Replace the chart's data; a bar that was only appended does not re-grow."""
        appended = len(names) == len(self._names) + 1 and names[: len(self._names)] == self._names
        self._names = list(names)
        self._ocr = list(ocr_times)
        self._correction = list(correction_times)
        if appended:
            self._animation.stop()
            self._progress = 1.0
            self.update()
            return
        self._animation.stop()
        self._animation.start()

    def clear_data(self) -> None:
        """Forget the data; only the frame, the title and the legend remain."""
        self._animation.stop()
        self._names = []
        self._ocr = []
        self._correction = []
        self._progress = 1.0
        self.update()

    def segment_at(self, pos: QPoint) -> tuple[str, float] | None:
        """Return the bar segment under *pos* as ``(label, seconds)``, or ``None``."""
        layout = self._layout()
        point = QPointF(pos)
        if not layout.plot.contains(point):
            return None
        index = int((point.y() - layout.plot.top()) // layout.row_height)
        if not 0 <= index < len(self._names):
            return None
        scale = layout.plot.width() / layout.axis_max
        offset = point.x() - layout.plot.left()
        ocr_width = self._ocr[index] * scale
        if offset <= ocr_width:
            return ("OCR", self._ocr[index])
        if offset <= ocr_width + self._correction[index] * scale:
            return ("Proofreading", self._correction[index])
        return None

    # ── Qt overrides ──────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: ARG002  # Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        layout = self._layout()
        self._paint_title(painter, layout)
        self._paint_axis(painter, layout)
        self._paint_bars(painter, layout)
        self._paint_legend(painter, layout)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # Qt override
        segment = self.segment_at(event.position().toPoint())
        if segment is None:
            QToolTip.hideText()
        else:
            label, seconds = segment
            QToolTip.showText(event.globalPosition().toPoint(), f"{label}: {seconds:.2f} s", self)
        super().mouseMoveEvent(event)

    # ── Geometry ──────────────────────────────────────────────────────────────

    def _layout(self) -> _Layout:
        metrics = QFontMetrics(self.font())
        name_width = min(max(_MIN_NAME_WIDTH, self.width() * _NAME_SHARE), _MAX_NAME_WIDTH)
        left = _MARGIN + name_width
        top = _MARGIN + metrics.height() + 6
        bottom = self.height() - _MARGIN - metrics.height() - 6 - (metrics.height() + 6)
        plot = QRectF(
            left,
            top,
            max(10.0, self.width() - left - _MARGIN),
            max(10.0, bottom - top),
        )
        rows = max(1, len(self._names))
        # Rows shrink to fit the plot, so many files never draw over the axis or the legend.
        row_height = min(_MAX_ROW, max(_MIN_ROW, plot.height() / rows))
        totals = [o + c for o, c in zip(self._ocr, self._correction, strict=False)]
        ticks = nice_ticks(max(totals) if totals else 0.0)
        return _Layout(plot, row_height, ticks[-1] or 1.0, ticks)

    # ── Painting ──────────────────────────────────────────────────────────────

    def _paint_title(self, painter: QPainter, layout: _Layout) -> None:
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self.palette().text().color())
        painter.drawText(
            QRectF(_MARGIN, _MARGIN, self.width() - 2 * _MARGIN, layout.plot.top() - _MARGIN),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            txt.CHART_TITLE,
        )
        font.setBold(False)
        painter.setFont(font)

    def _paint_axis(self, painter: QPainter, layout: _Layout) -> None:
        metrics = QFontMetrics(self.font())
        scale = layout.plot.width() / layout.axis_max
        painter.setPen(QPen(self.palette().mid().color(), 1))
        for tick in layout.ticks:
            x = layout.plot.left() + tick * scale
            painter.drawLine(QPointF(x, layout.plot.top()), QPointF(x, layout.plot.bottom()))
            painter.drawText(
                QRectF(x - 30.0, layout.plot.bottom() + 2.0, 60.0, metrics.height()),
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                f"{tick:.1f}",
            )

    def _paint_bars(self, painter: QPainter, layout: _Layout) -> None:
        metrics = QFontMetrics(self.font())
        scale = layout.plot.width() / layout.axis_max
        name_width = layout.plot.left() - _MARGIN - 6.0
        # Rows thinner than the font get a name on every k-th row only, so labels never overlap.
        label_every = max(1, math.ceil(metrics.height() / layout.row_height))
        for index, name in enumerate(self._names):
            top = layout.plot.top() + index * layout.row_height
            height = max(2.0, layout.row_height - min(_ROW_GAP, layout.row_height * 0.25))
            x = layout.plot.left()
            for value, colour in (
                (self._ocr[index], _COLOUR_OCR_BAR),
                (self._correction[index], _COLOUR_CORRECTION_BAR),
            ):
                width = value * scale * self._progress
                if width <= 0:
                    continue
                painter.fillRect(QRectF(x, top, width, height), QColor(colour))
                x += width
            if index % label_every:
                continue
            label = metrics.elidedText(
                name, Qt.TextElideMode.ElideMiddle, int(max(0.0, name_width))
            )
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(_MARGIN, top, name_width, height),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                label,
            )

    def _paint_legend(self, painter: QPainter, layout: _Layout) -> None:
        metrics = QFontMetrics(self.font())
        box = float(metrics.height())
        gap = 6.0
        widths = [box + gap + metrics.horizontalAdvance(text) for text, _ in _LEGEND]
        total = sum(widths) + gap * (len(_LEGEND) - 1)
        x = layout.plot.center().x() - total / 2.0
        y = self.height() - _MARGIN - box
        for (text, colour), width in zip(_LEGEND, widths, strict=True):
            painter.fillRect(QRectF(x, y, box, box), QColor(colour))
            painter.setPen(self.palette().text().color())
            painter.drawText(
                QRectF(x + box + gap, y, width - box - gap, box),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
            x += width + gap

    # ── Animation ─────────────────────────────────────────────────────────────

    def _on_grow(self, value: object) -> None:
        self._progress = float(value)  # ty: ignore[invalid-argument-type]
        self.update()
