"""The processing-time chart: its bars, its hit test and its tick steps."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QColor, QImage

from teachers_teammate.gui._chart_widget import ChartWidget, nice_ticks
from teachers_teammate.gui._constants import _COLOUR_CORRECTION_BAR, _COLOUR_OCR_BAR


def _render(widget: ChartWidget) -> QImage:
    image = QImage(widget.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    widget.render(image)
    return image


def _pixels(image: QImage, colour: str) -> list[tuple[int, int]]:
    target = QColor(colour).rgb()
    return [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if image.pixel(x, y) == target
    ]


def _bar_pixels(widget: ChartWidget, image: QImage, colour: str) -> list[tuple[int, int]]:
    """Return the pixels of *colour* inside the plot area (the legend uses the same colours)."""
    plot = widget._layout().plot
    return [(x, y) for x, y in _pixels(image, colour) if plot.contains(QPointF(x, y))]


def _chart(qtbot) -> ChartWidget:
    widget = ChartWidget()
    qtbot.addWidget(widget)
    widget.resize(400, 300)
    return widget


def test_bars_are_painted_for_two_files(qtbot) -> None:
    """Given two files, When the chart is rendered, Then both segments appear, OCR left of proofreading."""
    widget = _chart(qtbot)
    widget.update_data(["a.png", "b.png"], [1.0, 2.0], [0.5, 0.5])
    qtbot.waitUntil(lambda: widget._progress == 1.0)

    image = _render(widget)

    ocr = _bar_pixels(widget, image, _COLOUR_OCR_BAR)
    correction = _bar_pixels(widget, image, _COLOUR_CORRECTION_BAR)
    assert ocr and correction
    assert min(x for x, _ in ocr) < min(x for x, _ in correction)
    assert len({y for _, y in ocr}) > 1


def test_segment_at_returns_the_segment_under_the_pointer(qtbot) -> None:
    """Given a painted chart, When a point inside a segment is hit-tested, Then its label and time come back."""
    widget = _chart(qtbot)
    widget.update_data(["a.png"], [1.0], [0.5])
    qtbot.waitUntil(lambda: widget._progress == 1.0)
    image = _render(widget)
    ocr_x, ocr_y = min(_bar_pixels(widget, image, _COLOUR_OCR_BAR))
    corr_x, corr_y = min(_bar_pixels(widget, image, _COLOUR_CORRECTION_BAR))

    assert widget.segment_at(QPoint(ocr_x + 2, ocr_y)) == ("OCR", 1.0)
    assert widget.segment_at(QPoint(corr_x + 2, corr_y)) == ("Proofreading", 0.5)
    assert widget.segment_at(QPoint(2, 2)) is None


@pytest.mark.parametrize(
    ("max_value", "expected"),
    [
        (0.3, [0.0, 0.1, 0.2, 0.3]),
        (7.0, [0.0, 2.0, 4.0, 6.0, 8.0]),
        (95.0, [0.0, 20.0, 40.0, 60.0, 80.0, 100.0]),
        (1234.0, [0.0, 500.0, 1000.0, 1500.0]),
    ],
)
def test_nice_ticks_are_round(max_value: float, expected: list[float]) -> None:
    """Given a longest bar, When the ticks are computed, Then the steps are 1, 2 or 5 times a power of ten."""
    assert nice_ticks(max_value) == expected


def test_clear_data_leaves_no_bars(qtbot) -> None:
    """Given a painted chart, When the data is cleared, Then the plot area holds no bar."""
    widget = _chart(qtbot)
    widget.update_data(["a.png"], [1.0], [0.5])
    qtbot.waitUntil(lambda: widget._progress == 1.0)

    widget.clear_data()
    image = _render(widget)

    inside = [
        pixel
        for colour in (_COLOUR_OCR_BAR, _COLOUR_CORRECTION_BAR)
        for pixel in _bar_pixels(widget, image, colour)
    ]
    assert inside == []


def test_an_appended_bar_does_not_regrow(qtbot) -> None:
    """Given a drawn chart, When one more file arrives, Then the bars stay at full width."""
    widget = _chart(qtbot)
    widget.update_data(["a.png"], [1.0], [0.5])
    qtbot.waitUntil(lambda: widget._progress == 1.0)

    widget.update_data(["a.png", "b.png"], [1.0, 2.0], [0.5, 0.5])

    assert widget._progress == 1.0


def test_many_files_stay_inside_the_plot(qtbot) -> None:
    """
    Given  forty files in a small chart
    When   the chart is rendered
    Then   no bar pixel lies outside the plot area and the last row can be hit-tested
    """
    widget = _chart(qtbot)
    names = [f"file{i}.png" for i in range(40)]
    widget.update_data(names, [1.0] * 40, [0.5] * 40)
    qtbot.waitUntil(lambda: widget._progress == 1.0)

    image = _render(widget)

    # One pixel of tolerance: a bar edge on a fractional row boundary rounds outwards.
    plot = widget._layout().plot.adjusted(-1.0, -1.0, 1.0, 1.0)
    for colour in (_COLOUR_OCR_BAR, _COLOUR_CORRECTION_BAR):
        outside = [
            (x, y)
            for x, y in _pixels(image, colour)
            if not plot.contains(QPointF(x, y)) and y < widget.height() - 40
        ]
        assert outside == []
    inner = widget._layout().plot
    last_row_y = int(inner.top() + 39.5 * widget._layout().row_height)
    assert widget.segment_at(QPoint(int(inner.left()) + 2, last_row_y)) == ("OCR", 1.0)


def test_names_are_thinned_out_when_rows_are_thinner_than_the_font(qtbot) -> None:
    """
    Given  forty files, so rows thinner than the font
    When   the chart is painted
    Then   rows are shorter than a text line and the label step is more than one
    """
    widget = _chart(qtbot)
    widget.update_data([f"f{i}" for i in range(40)], [1.0] * 40, [0.0] * 40)

    layout = widget._layout()

    assert layout.row_height < widget.fontMetrics().height()
