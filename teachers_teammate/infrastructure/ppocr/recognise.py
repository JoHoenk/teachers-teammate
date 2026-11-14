"""Text recognition: read one cropped line.

PP-OCR's recogniser (CTC) takes a strip of height 48 and returns, per horizontal step, the
probability of each character class.  The classes are ``["<blank>", *character_dict, " "]``,
the character list being part of the model's own ``inference.yml``.
"""

from __future__ import annotations

from collections.abc import Sequence
import math
from pathlib import Path

import cv2
import numpy as np
import yaml

from .detect import RotatedBox

REC_HEIGHT = 48
REC_MIN_WIDTH = 320
REC_MAX_WIDTH = 3200

_TURN_RATIO = 1.5


def crop_line(image: np.ndarray, box: RotatedBox) -> np.ndarray:
    """Cut *box* out of *image* as an upright strip.

    The corners are ordered top-left, top-right, bottom-right, bottom-left and the box is
    warped to a rectangle (the approach of PaddleOCR's ``get_rotate_crop_image``, Apache-2.0;
    no code is copied).  A strip at least 1.5 x taller than wide (writing in a margin) is
    turned 90° so the text runs left to right.
    """
    top_left, top_right, bottom_right, bottom_left = _ordered_corners(box.corners())
    width = max(1, round(max(_dist(top_right, top_left), _dist(bottom_right, bottom_left))))
    height = max(1, round(max(_dist(bottom_left, top_left), _dist(bottom_right, top_right))))
    source = np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    strip = cv2.warpPerspective(
        image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE, flags=cv2.INTER_CUBIC
    )
    if height >= _TURN_RATIO * width:
        strip = cv2.rotate(strip, cv2.ROTATE_90_COUNTERCLOCKWISE)
    return strip


def _ordered_corners(pts: np.ndarray) -> np.ndarray:
    """Return the four corners as top-left, top-right, bottom-right, bottom-left.

    The two left-most corners are the left side and the two right-most the right side; each
    side is then ordered by y.  Ordering by ``x + y`` and ``y - x`` instead gives one corner
    two roles for a box at 45° (a diamond), which makes the warp singular.
    """
    by_x = pts[np.argsort(pts[:, 0], kind="stable")]
    top_left, bottom_left = by_x[:2][np.argsort(by_x[:2, 1], kind="stable")]
    top_right, bottom_right = by_x[2:][np.argsort(by_x[2:, 1], kind="stable")]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def _dist(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b))


def recogniser_input(line: np.ndarray) -> np.ndarray:
    """Prepare a cropped line (BGR) for the recogniser.

    Height 48, width ``ceil(48 x w / h)`` capped at 3200, ``(x / 255 - 0.5) / 0.5``, padded
    with zeros on the right to at least 320; NCHW float32.
    """
    height, width = line.shape[:2]
    new_w = min(REC_MAX_WIDTH, max(1, math.ceil(REC_HEIGHT * width / max(1, height))))
    resized = cv2.resize(line, (new_w, REC_HEIGHT), interpolation=cv2.INTER_LINEAR)
    normalised = (resized.astype(np.float32) / 255.0 - 0.5) / 0.5
    canvas = np.zeros((REC_HEIGHT, max(REC_MIN_WIDTH, new_w), 3), np.float32)
    canvas[:, :new_w] = normalised
    return np.ascontiguousarray(canvas.transpose(2, 0, 1)[None], dtype=np.float32)


def load_characters(yml_path: Path) -> list[str]:
    """Return the recogniser's classes: ``["<blank>", *character_dict, " "]``."""
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with yml_path.open("rb") as handle:
        config = yaml.load(handle, Loader=loader)
    return ["<blank>", *(str(c) for c in config["PostProcess"]["character_dict"]), " "]


def ctc_decode(logits: np.ndarray, characters: Sequence[str]) -> tuple[str, float]:
    """Decode a recogniser output of shape ``[1, T, C]``.

    Takes the best class per step, merges repeats and drops the blank (class 0).  Returns the
    text and the mean probability of the kept steps (0.0 for an empty line).
    """
    steps = logits[0]
    ids = steps.argmax(axis=1)
    keep = ids != 0
    keep[1:] &= ids[1:] != ids[:-1]
    if not keep.any():
        return "", 0.0
    best = steps.max(axis=1)
    text = "".join(characters[i] for i in ids[keep])
    return text, float(best[keep].mean())
