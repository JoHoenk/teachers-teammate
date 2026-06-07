"""Image preprocessing pipeline for OCR using OpenCV.

Two methods are available:

* ``adaptive_threshold`` (default): grayscale → adaptive Gaussian threshold
  (binary output).  Robust to uneven lighting and ink variation.
* ``clahe``: grayscale → CLAHE (Contrast Limited Adaptive Histogram
  Equalization, greyscale output).  Recommended when the downstream OCR engine
  performs its own binarisation (e.g. Tesseract).
* ``grayscale``: grayscale only, no threshold or CLAHE.  Useful when the OCR
  engine performs its own binarisation and CLAHE is too aggressive.
* ``none``: pass the original image to the OCR engine without any modification.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import cv2
from PIL import Image as _PILImage

from ..exceptions import PipelineInputError
from ..interfaces import ImagePreprocessor

_logger = logging.getLogger(__name__)

# ── Implementation ──────────────────────────────────────────────────


class HandwritingPreprocessor(ImagePreprocessor):
    """Applies a handwriting-optimised OpenCV preprocessing pipeline.

    Four *method* options are available:

    * ``adaptive_threshold`` (default): grayscale → adaptive Gaussian threshold.
      Outputs a binary image.  Robust to uneven lighting.
    * ``clahe``: grayscale → CLAHE (Contrast Limited Adaptive Histogram
      Equalization).  Outputs an enhanced greyscale image; recommended when the
      downstream OCR engine has its own binarisation (e.g. Tesseract).
    * ``grayscale``: grayscale only.  No threshold or histogram equalization.
    * ``none``: return the original file unchanged.

    When *save_steps* is ``True`` and *tmp_dir* is set, a JPEG snapshot is
    written to *tmp_dir* after every pipeline step for inspection.
    """

    def __init__(
        self,
        tmp_dir: Path | None = None,
        save_steps: bool = False,
        method: str = "adaptive_threshold",
    ) -> None:
        self._tmp_dir = tmp_dir
        self._save_steps = save_steps
        self._method = method

    def preprocess(self, image_path: Path) -> tuple[Path, list[str]]:
        """Preprocess *image_path* and return ``(output_path, steps_applied)``.

        When *method* is ``"none"``, the original file is returned unchanged.
        Otherwise applies a fixed handwriting-optimised pipeline:
        grayscale -> adaptive Gaussian threshold.
        The preprocessed image is saved to *tmp_dir* (or alongside the source
        file when *tmp_dir* is ``None``).  The caller is responsible for cleanup.
        When *save_steps* was set to ``True``, an additional snapshot is saved
        to *tmp_dir* after every intermediate step.
        """
        if self._method == "none":
            return image_path, []
        # Capture DPI metadata before cv2 strips it.
        _source_dpi: tuple[float, float] | None = None
        try:
            with _PILImage.open(image_path) as _pil:
                _source_dpi = _pil.info.get("dpi")
        except Exception as exc:  # noqa: BLE001  # PIL may raise format-specific errors; log DPI warning and continue
            _logger.warning("Could not read DPI from %s: %s", image_path, exc)
        image: Any = cv2.imread(str(image_path))
        if image is None:
            raise PipelineInputError(f"Cannot read image: {image_path}")

        stem = image_path.stem
        steps: list[str] = []

        # 1. grayscale
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        steps.append("grayscale")
        self._save_step(image, stem, 1, "grayscale")

        if self._method == "grayscale":
            out_path = self._output_path(image_path)
            cv2.imwrite(str(out_path), image)
            if _source_dpi:
                try:
                    with _PILImage.open(out_path) as _pil_out:
                        _pil_out.save(str(out_path), dpi=_source_dpi)
                except Exception as exc:  # noqa: BLE001  # PIL save errors (permissions, disk full, unsupported format) are non-fatal; log and continue
                    _logger.warning("Could not restore DPI metadata on %s: %s", out_path, exc)
            return out_path, steps

        # 2. contrast enhancement / binarisation
        if self._method == "clahe":
            # clipLimit=2.0: caps contrast amplification to avoid noise over-enhancement.
            # tileGridSize=(8,8): tile size for local histogram equalisation at ~300 DPI.
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            image = clahe.apply(image)
            steps.append("CLAHE")
            self._save_step(image, stem, 2, "clahe")
        else:  # adaptive_threshold (default)
            image = cv2.adaptiveThreshold(
                image,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                # blockSize=31: odd neighbourhood, empirically tuned for ~300 DPI A4 handwriting.
                # C=8: constant subtracted from local mean; higher → more aggressive binarisation.
                blockSize=31,
                C=8,
            )
            steps.append("adaptive threshold")
            self._save_step(image, stem, 2, "adaptive_threshold")

        out_path = self._output_path(image_path)
        cv2.imwrite(str(out_path), image)

        # Restore DPI metadata that cv2 strips on write.
        if _source_dpi:
            try:
                with _PILImage.open(out_path) as _pil_out:
                    _pil_out.save(str(out_path), dpi=_source_dpi)
            except Exception as exc:  # noqa: BLE001  # PIL save errors (permissions, disk full, unsupported format) are non-fatal; log and continue
                _logger.warning("Could not restore DPI metadata on %s: %s", out_path, exc)

        return out_path, steps

    # ── private ───────────────────────────────────────────────────────────

    def _save_step(self, image: Any, stem: str, step_num: int, name: str) -> None:
        """Write a snapshot of *image* to *tmp_dir* if step-saving is enabled."""
        if not self._save_steps or self._tmp_dir is None:
            return
        path = self._tmp_dir / f"{stem}_step{step_num}_{name}.jpg"
        cv2.imwrite(str(path), image)

    def _output_path(self, image_path: Path) -> Path:
        # PNG is lossless and compresses binary images far better than JPEG;
        # it also avoids the grey fringe artefacts JPEG introduces on 0/255 edges.
        out_name = f"{image_path.stem}_preprocessed.png"
        if self._tmp_dir is not None:
            return self._tmp_dir / out_name
        return image_path.parent / out_name
