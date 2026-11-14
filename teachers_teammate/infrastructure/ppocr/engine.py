"""The built-in reader as an :class:`~teachers_teammate.interfaces.OCRProcessor`.

Five steps per page: load the image, find the line boxes, order them into rows, cut and read
each box, join the words of a row with a space and the rows with a newline.  The models run on
the processor through OpenCV; no server, nothing leaves the computer.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading
import time

import numpy as np

from ...domain.local_models import PPOCR_SMALL_DET, PPOCR_SMALL_REC, READER_NOT_DOWNLOADED
from ...exceptions import ModelMissingError, OCRError, RunStoppedError, stop_requested
from ...interfaces import OCRProcessor
from ..image_io import read_image
from ..onnx.runner import CvDnnNet, OnnxNet
from ..onnx.store import ModelStore
from .detect import DetectorSettings, detect
from .reading_order import reading_order
from .recognise import crop_line, ctc_decode, load_characters, recogniser_input

_logger = logging.getLogger(__name__)

NetFactory = Callable[[Path], OnnxNet]

_MODEL_MISMATCH = "The built-in reader's model files don't match this version of the app."


class PpOcrProcessor(OCRProcessor):
    """The built-in reader: PP-OCRv6 small text detection and recognition, run by OpenCV."""

    def __init__(
        self,
        store: ModelStore,
        *,
        stop_event: threading.Event | None = None,
        net_factory: NetFactory = CvDnnNet,
        settings: DetectorSettings | None = None,
    ) -> None:
        self._store = store
        self._stop_event = stop_event
        self._net_factory = net_factory
        self._settings = settings or DetectorSettings()
        self._detector: OnnxNet | None = None
        self._recogniser: OnnxNet | None = None
        self._characters: list[str] = []

    def process_image(self, image_path: Path, language: str = "English") -> str:  # noqa: ARG002
        """Read the page.  *language* is not used: one model reads 50 languages.

        Raises:
            ModelMissingError: if the models are not downloaded.
            RunStoppedError: if "Stop now" was pressed while reading.
            OCRError: if the image cannot be read or the models misbehave.
        """
        detector, recogniser = self._load()
        image = read_image(image_path)
        if image is None:
            raise OCRError(f"Cannot read image: {image_path}")
        try:
            started = time.perf_counter()
            boxes = detect(detector, image, self._settings)
            detected = time.perf_counter()
            rows = reading_order(boxes)
            lines: list[str] = []
            for row in rows:
                words: list[str] = []
                for box in row:
                    if stop_requested(self._stop_event):
                        raise RunStoppedError
                    text, _ = self._read(recogniser, crop_line(image, box))
                    if text.strip():
                        words.append(text.strip())
                if words:
                    lines.append(" ".join(words))
        except (RunStoppedError, OCRError):
            raise
        except Exception as exc:  # cv2.error and shape errors from the models
            raise OCRError(str(exc)) from exc
        _logger.debug(
            "built-in reader: %d boxes, %d lines, detection %.2fs, recognition %.2fs",
            len(boxes),
            len(lines),
            detected - started,
            time.perf_counter() - detected,
        )
        return "\n".join(lines)

    def _read(self, recogniser: OnnxNet, line: np.ndarray) -> tuple[str, float]:
        output = recogniser.run(recogniser_input(line))
        if output.ndim != 3 or output.shape[2] != len(self._characters):
            raise OCRError(_MODEL_MISMATCH)
        return ctc_decode(output, self._characters)

    def _load(self) -> tuple[OnnxNet, OnnxNet]:
        if self._detector is None or self._recogniser is None:
            try:
                det_path = self._store.file(PPOCR_SMALL_DET, "inference.onnx")
                rec_path = self._store.file(PPOCR_SMALL_REC, "inference.onnx")
                yml_path = self._store.file(PPOCR_SMALL_REC, "inference.yml")
            except ModelMissingError as exc:
                raise ModelMissingError(READER_NOT_DOWNLOADED) from exc
            try:
                self._characters = load_characters(yml_path)
                self._detector = self._net_factory(det_path)
                self._recogniser = self._net_factory(rec_path)
            except Exception as exc:  # cv2.error, yaml errors, KeyError of a changed file
                self._detector = self._recogniser = None
                raise OCRError(f"{_MODEL_MISMATCH} ({exc})") from exc
        return self._detector, self._recogniser
