"""Running an ONNX model.

The reader depends on :class:`OnnxNet` only (one method), so a different runtime — ONNX
Runtime, for instance — is one new class and one dependency, not a rewrite.  OpenCV's DNN module
is the default: it is already part of the app, so the reader adds no package.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np


class OnnxNet(Protocol):
    """A loaded model that maps one input array to one output array."""

    def run(self, blob: np.ndarray) -> np.ndarray:
        """Run the model on *blob* (NCHW float32) and return its first output."""
        ...


class CvDnnNet:
    """A model run by OpenCV's DNN module on the processor (all cores, no setting)."""

    def __init__(self, path: Path) -> None:
        # From bytes, not from the path: OpenCV's path functions fail on non-ASCII Windows paths.
        self._net = cv2.dnn.readNetFromONNX(np.fromfile(path, dtype=np.uint8))

    def run(self, blob: np.ndarray) -> np.ndarray:
        """Run the model on *blob* and return its output."""
        self._net.setInput(blob)
        return self._net.forward()
