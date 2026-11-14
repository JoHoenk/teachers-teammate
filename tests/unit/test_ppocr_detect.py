"""Tests for the built-in reader's detection (pure, no model files)."""

from __future__ import annotations

import numpy as np
import pytest

from teachers_teammate.infrastructure.ppocr.detect import (
    DetectorSettings,
    RotatedBox,
    boxes_from_map,
    detect,
    detector_input,
)

_NO_PAD = DetectorSettings(line_end_pad=0.0)


def _map(height: int = 200, width: int = 320) -> np.ndarray:
    return np.zeros((height, width), np.float32)


class _FakeNet:
    def __init__(self, prob: np.ndarray) -> None:
        self.prob = prob
        self.blob_shape: tuple[int, ...] = ()

    def run(self, blob: np.ndarray) -> np.ndarray:
        """Return the prepared map as a ``[1, 1, H, W]`` output."""
        self.blob_shape = blob.shape
        return self.prob[None, None]


def test_two_rectangles_become_two_boxes_at_the_right_places() -> None:
    """
    Given  a probability map with two bright rectangles
    When   boxes are made from it
    Then   there are two boxes whose centres lie on the rectangles
    """
    prob = _map()
    prob[40:60, 30:250] = 0.9
    prob[120:140, 30:200] = 0.9

    boxes = boxes_from_map(prob, _NO_PAD)

    centres = sorted(((round(b.centre[0]), round(b.centre[1])) for b in boxes), key=lambda c: c[1])
    assert len(boxes) == 2
    assert centres[0] == pytest.approx((140, 50), abs=2)
    assert centres[1] == pytest.approx((115, 130), abs=2)


def test_boxes_are_scaled_to_the_page() -> None:
    """
    Given  a map scaled by 2 in x and 3 in y
    When   boxes are made from it
    Then   the box centre is in page pixels
    """
    prob = _map()
    prob[40:60, 30:250] = 0.9

    boxes = boxes_from_map(prob, _NO_PAD, scale=(2.0, 3.0))
    assert len(boxes) == 1
    box = boxes[0]

    assert box.centre == pytest.approx((280, 150), abs=4)


def test_faint_blob_and_thin_line_are_dropped() -> None:
    """
    Given  a blob above the threshold but below box_thresh, and a 2-pixel line
    When   boxes are made from it
    Then   neither is a box
    """
    prob = _map()
    prob[40:60, 30:250] = 0.3
    prob[120:122, 30:250] = 0.9

    assert boxes_from_map(prob, _NO_PAD) == []


def test_line_end_pad_lengthens_the_long_side_only() -> None:
    """
    Given  the same map with and without line_end_pad
    When   boxes are made from it
    Then   the long side grows by pad x height and the short side does not change
    """
    prob = _map()
    prob[40:60, 30:250] = 0.9

    plain = boxes_from_map(prob, _NO_PAD)[0]
    padded = boxes_from_map(prob, DetectorSettings(line_end_pad=0.6))[0]

    plain_long, plain_short = sorted(plain.size, reverse=True)
    padded_long, padded_short = sorted(padded.size, reverse=True)
    assert padded_short == pytest.approx(plain_short, abs=0.5)
    assert padded_long - plain_long == pytest.approx(0.6 * plain_short, abs=1.0)


def test_detector_input_keeps_multiples_of_32_and_never_enlarges() -> None:
    """
    Given  a large and a small image
    When   the detector input is made
    Then   sides are multiples of 32, the large one is at most max_side and the small one is
           not enlarged
    """
    big = np.zeros((2480, 3508, 3), np.uint8)
    small = np.zeros((100, 200, 3), np.uint8)

    blob, sx, sy = detector_input(big, 1600)
    small_blob, small_sx, _ = detector_input(small, 1600)

    assert blob.dtype == np.float32 and blob.shape[:2] == (1, 3)
    assert blob.shape[2] % 32 == 0 and blob.shape[3] % 32 == 0
    assert max(blob.shape[2:]) <= 1600
    assert sx == pytest.approx(3508 / blob.shape[3]) and sy == pytest.approx(2480 / blob.shape[2])
    assert small_blob.shape[2:] == (96, 192)
    assert small_sx == pytest.approx(200 / 192)


def test_detect_runs_the_net_and_returns_page_boxes() -> None:
    """
    Given  a fake net answering with a map of the prepared size
    When   detect is called on a page
    Then   the net got the blob and the box lies at the page position
    """
    page = np.full((320, 640, 3), 255, np.uint8)
    prob = np.zeros((320, 640), np.float32)
    prob[100:130, 100:500] = 0.9
    net = _FakeNet(prob)

    boxes = detect(net, page, _NO_PAD)

    assert net.blob_shape == (1, 3, 320, 640)
    assert len(boxes) == 1
    assert boxes[0].centre == pytest.approx((300, 115), abs=3)


def test_rotated_box_from_points_round_trips() -> None:
    """
    Given  four corner points
    When   a RotatedBox is made from them
    Then   its corners are the same rectangle and bounds cover them
    """
    pts = np.array([[10, 10], [110, 10], [110, 30], [10, 30]], dtype=np.float32)

    box = RotatedBox.from_points(pts)

    assert box.bounds == pytest.approx((10, 10, 110, 30))
    assert sorted(map(tuple, box.corners().round().tolist())) == sorted(map(tuple, pts.tolist()))
