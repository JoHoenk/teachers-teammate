"""The two page models the automatic image preparation can use: turn and flatten.

Both are PaddlePaddle's ONNX exports, run by OpenCV's DNN module through the same
:class:`~teachers_teammate.infrastructure.onnx.runner.OnnxNet` seam the built-in reader uses.
The resize and normalisation are the values in each model's own ``inference.yml``; the model
files come from the :class:`~teachers_teammate.infrastructure.onnx.store.ModelStore` and are never
downloaded here (only on request: Add-ons, the fix button, ``--download-page-models``).

* **doc_ori** (PP-LCNet_x1_0_doc_ori) names how far a page is turned: 0, 90, 180 or 270°.
* **UVDoc** takes a photo of a page and returns the page flattened, at the size it was given.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ...domain.local_models import DOC_ORI, PAGE_MODELS_NOT_DOWNLOADED, UVDOC
from ..onnx.runner import CvDnnNet, OnnxNet
from ..onnx.store import ModelStore
from .identity import available_page_steps

_ORI_SHORT_SIDE = 256
_ORI_CROP = 224
_ORI_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_ORI_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_logger = logging.getLogger(__name__)

ORIENTATIONS = (0, 90, 180, 270)
"""The classes of doc_ori, in the order of its output: the degrees a page is turned clockwise."""

FLATTEN_MAX_SIDE = 2048
"""The page goes to UVDoc scaled down to this long side (never up); the answer has that size."""

NetFactory = Callable[[Path], OnnxNet]


def orientation_blob(image: Any) -> np.ndarray:
    """Return *image* (BGR) as doc_ori's input: short side 256, centre 224 x 224, normalised RGB."""
    height, width = image.shape[:2]
    scale = _ORI_SHORT_SIDE / min(height, width)
    resized = cv2.resize(
        image,
        (max(_ORI_CROP, round(width * scale)), max(_ORI_CROP, round(height * scale))),
        interpolation=cv2.INTER_LINEAR,
    )
    top = (resized.shape[0] - _ORI_CROP) // 2
    left = (resized.shape[1] - _ORI_CROP) // 2
    crop = resized[top : top + _ORI_CROP, left : left + _ORI_CROP, ::-1].astype(np.float32)
    normalised = (crop / 255.0 - _ORI_MEAN) / _ORI_STD
    return np.ascontiguousarray(normalised.transpose(2, 0, 1)[None], dtype=np.float32)


class PageModels:
    """The two page models of one run, loaded on first use.

    Whether a model is there is decided once, when this object is made: a download that
    finishes during a run takes effect on the next one.
    """

    def __init__(self, store: ModelStore, net_factory: NetFactory = CvDnnNet) -> None:
        self._store = store
        self._net_factory = net_factory
        steps = available_page_steps(store)
        self.can_turn = "turn" in steps
        self.can_flatten = "flatten" in steps
        self._orientation: OnnxNet | None = None
        self._unwarp: OnnxNet | None = None
        self._noted = False

    def note_missing(self) -> None:
        """Say once per run (info log, not once per page) that the model steps are skipped."""
        if self._noted:
            return
        self._noted = True
        _logger.info(
            "Turning and flattening pages is skipped for this run: %s", PAGE_MODELS_NOT_DOWNLOADED
        )

    def orientation(self, image: Any) -> tuple[int, float]:
        """Return ``(degrees the page is turned clockwise, confidence)`` for *image* (BGR)."""
        if self._orientation is None:
            self._orientation = self._net_factory(self._store.file(DOC_ORI, "inference.onnx"))
        scores = np.asarray(self._orientation.run(orientation_blob(image))).reshape(-1)
        if scores.size != len(ORIENTATIONS):
            raise ValueError(f"the orientation model answered {scores.size} classes")
        best = int(np.argmax(scores))
        return ORIENTATIONS[best], float(scores[best])

    def flatten(self, image: Any) -> Any:
        """Return *image* (BGR) as a flat page, at most :data:`FLATTEN_MAX_SIDE` on the long side."""
        if self._unwarp is None:
            self._unwarp = self._net_factory(self._store.file(UVDOC, "inference.onnx"))
        height, width = image.shape[:2]
        shrink = min(1.0, FLATTEN_MAX_SIDE / max(height, width))
        if shrink < 1.0:
            image = cv2.resize(
                image,
                (max(1, round(width * shrink)), max(1, round(height * shrink))),
                interpolation=cv2.INTER_AREA,
            )
        blob = cv2.dnn.blobFromImage(image, 1 / 255.0, swapRB=True)
        out = np.asarray(self._unwarp.run(blob))
        if out.ndim != 4 or out.shape[1] != 3:
            raise ValueError(f"the flatten model answered the shape {out.shape}")
        rgb = out[0].transpose(1, 2, 0)
        return np.ascontiguousarray((rgb[:, :, ::-1] * 255.0).clip(0, 255).astype(np.uint8))
