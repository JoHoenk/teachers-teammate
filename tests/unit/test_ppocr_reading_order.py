"""Tests for the built-in reader's reading order."""

from __future__ import annotations

import random

from teachers_teammate.infrastructure.ppocr.detect import RotatedBox
from teachers_teammate.infrastructure.ppocr.reading_order import reading_order


def _box(x: float, y: float, w: float = 80, h: float = 24) -> RotatedBox:
    return RotatedBox((x, y), (w, h), 0.0)


def test_shuffled_boxes_become_rows_left_to_right() -> None:
    """
    Given  boxes of three lines with two words each, shuffled
    When   the reading order is made
    Then   there are three rows, top to bottom, each left to right
    """
    boxes = [_box(100 + 150 * col, 50 + 60 * row) for row in range(3) for col in range(2)]
    random.Random(3).shuffle(boxes)

    rows = reading_order(boxes)

    assert [[b.centre for b in r] for r in rows] == [
        [(100, 50 + 60 * i), (250, 50 + 60 * i)] for i in range(3)
    ]


def test_a_box_slightly_lower_stays_in_its_row() -> None:
    """
    Given  a second word that sits 6 pixels lower than the first
    When   the reading order is made
    Then   both words are one row
    """
    rows = reading_order([_box(100, 50), _box(250, 56)])

    assert len(rows) == 1 and len(rows[0]) == 2


def test_no_boxes_give_no_rows() -> None:
    """
    Given  no boxes
    When   the reading order is made
    Then   the result is empty
    """
    assert not reading_order([])
