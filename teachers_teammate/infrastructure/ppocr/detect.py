"""Text detection: find the boxes around the lines of a page.

PP-OCR's detector (DB, "differentiable binarisation") returns a probability map the size of
its input.  Everything after the forward pass is plain OpenCV, written to follow the values in
the model's own ``inference.yml`` (``DBPostProcess``) — with one deliberate change: line boxes
are lengthened (see :attr:`DetectorSettings.line_end_pad`).

The post-processing re-implements the published algorithm of PaddleOCR's ``DBPostProcess``
(``ppocr/postprocess/db_postprocess.py``, Apache-2.0; no code is copied).  Paddle grows a box
with ``pyclipper``; here the rotated rectangle is grown directly by the same distance, so the
app needs no extra package.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import cv2
import numpy as np

from ..onnx.runner import OnnxNet

_logger = logging.getLogger(__name__)

MEAN = (0.485, 0.456, 0.406)
STD = (0.229, 0.224, 0.225)
"""The detector's normalisation, applied to the BGR channels in this order, as Paddle does."""
_MEAN_ARRAY = np.array(MEAN, dtype=np.float32)
_STD_ARRAY = np.array(STD, dtype=np.float32)


@dataclass(frozen=True)
class DetectorSettings:
    """The detector's numbers.  The first four come from the pinned ``inference.yml``."""

    thresh: float = 0.2
    box_thresh: float = 0.45
    unclip_ratio: float = 1.4
    max_candidates: int = 3000
    min_side: float = 3.0
    """Boxes thinner than this (map pixels) are noise."""
    max_side: int = 1600
    """The page is scaled down so its long side is at most this (never up).  Provisional."""
    line_end_pad: float = 0.6
    """The box grows by this x its height in length (half at each end).  Paddle's tight boxes
    lose full stops and last letters; lengthened boxes keep them.  Provisional."""


@dataclass(frozen=True)
class RotatedBox:
    """A rotated rectangle in page pixels, as ``cv2.minAreaRect`` describes it."""

    centre: tuple[float, float]
    size: tuple[float, float]
    angle: float

    @classmethod
    def from_points(cls, points: Any) -> RotatedBox:
        """Return the smallest rotated rectangle around *points* (``N x 2``)."""
        (cx, cy), (w, h), angle = cv2.minAreaRect(np.asarray(points, dtype=np.float32))
        return cls((float(cx), float(cy)), (float(w), float(h)), float(angle))

    def corners(self) -> np.ndarray:
        """Return the four corners as a ``4 x 2`` float32 array."""
        return cv2.boxPoints((self.centre, self.size, self.angle)).astype(np.float32)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return ``(left, top, right, bottom)`` of the corners."""
        pts = self.corners()
        return (
            float(pts[:, 0].min()),
            float(pts[:, 1].min()),
            float(pts[:, 0].max()),
            float(pts[:, 1].max()),
        )


def detector_input(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float, float]:
    """Prepare *image* (BGR) for the detector.

    Scales so the long side is at most *max_side* (never up), rounds both sides to multiples of
    32 (at least 32), normalises and returns an NCHW float32 blob.

    Returns:
        ``(blob, x scale, y scale)``: multiply map coordinates by the scales to get page pixels.
    """
    height, width = image.shape[:2]
    shrink = min(1.0, max_side / max(height, width))
    new_w = max(32, round(width * shrink / 32) * 32)
    new_h = max(32, round(height * shrink / 32) * 32)
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    normalised = (resized.astype(np.float32) / 255.0 - _MEAN_ARRAY) / _STD_ARRAY
    blob = np.ascontiguousarray(normalised.transpose(2, 0, 1)[None], dtype=np.float32)
    return blob, width / new_w, height / new_h


def boxes_from_map(
    prob: np.ndarray,
    settings: DetectorSettings,
    scale: tuple[float, float] = (1.0, 1.0),
) -> list[RotatedBox]:
    """Turn the detector's probability map into line boxes.

    ``prob > thresh`` → contours → per contour (at most ``max_candidates``): the smallest
    rotated rectangle; dropped when its short side is below ``min_side`` or the mean
    probability inside the contour is below ``box_thresh``; both sides grow by
    ``2 x area x unclip_ratio / perimeter`` (DB's "unclip") and the long side by
    ``line_end_pad x short side``.  *scale* turns map pixels into page pixels.
    """
    binary = (prob > settings.thresh).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes: list[RotatedBox] = []
    for contour in contours[: settings.max_candidates]:
        (cx, cy), (w, h), angle = cv2.minAreaRect(contour)
        if min(w, h) < settings.min_side:
            continue
        if _mean_inside(prob, contour) < settings.box_thresh:
            continue
        grow = w * h * settings.unclip_ratio / (2 * (w + h))
        w, h = w + 2 * grow, h + 2 * grow
        if w >= h:
            w += settings.line_end_pad * h
        else:
            h += settings.line_end_pad * w
        corners = cv2.boxPoints(((cx, cy), (w, h), angle)) * np.array(scale, dtype=np.float32)
        boxes.append(RotatedBox.from_points(corners))
    return boxes


def _mean_inside(prob: np.ndarray, contour: np.ndarray) -> float:
    """Return the mean probability inside *contour* (looked up in its bounding rectangle)."""
    x, y, w, h = cv2.boundingRect(contour)
    mask = np.zeros((h, w), np.uint8)
    cv2.fillPoly(mask, [contour - np.array([x, y])], 1)
    return float(cv2.mean(prob[y : y + h, x : x + w], mask)[0])


def detect(net: OnnxNet, image: np.ndarray, settings: DetectorSettings) -> list[RotatedBox]:
    """Return the line boxes of *image* (BGR), in page pixels."""
    blob, sx, sy = detector_input(image, settings.max_side)
    output = net.run(blob)
    if output.ndim != 4 or output.shape[0] != 1:
        raise ValueError(f"unexpected detector output shape {tuple(output.shape)}")
    boxes = boxes_from_map(output[0, 0], settings, (sx, sy))
    _logger.debug("detector: %d boxes (map %s)", len(boxes), tuple(output.shape[2:]))
    return boxes
