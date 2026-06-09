"""Image preprocessing pipeline for OCR using OpenCV.

Four *method* options control the final contrast/binarisation step:

* ``adaptive_threshold`` (default): grayscale -> adaptive Gaussian threshold
  (binary output).  Robust to uneven lighting and ink variation.
* ``clahe``: grayscale -> CLAHE (Contrast Limited Adaptive Histogram
  Equalization, greyscale output).  Recommended when the downstream OCR engine
  performs its own binarisation (e.g. Tesseract).
* ``grayscale``: grayscale only, no threshold or CLAHE.  Useful when the OCR
  engine performs its own binarisation and CLAHE is too aggressive.
* ``none``: pass the original image to the OCR engine without any modification.

Optional boolean pre-steps (applied before the main method, in this order):
dewarp -> deskew -> border_crop -> denoise -> gamma.
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

    * ``adaptive_threshold`` (default): grayscale -> adaptive Gaussian threshold.
      Outputs a binary image.  Robust to uneven lighting.
    * ``clahe``: grayscale -> CLAHE (Contrast Limited Adaptive Histogram
      Equalization).  Outputs an enhanced greyscale image; recommended when the
      downstream OCR engine has its own binarisation (e.g. Tesseract).
    * ``grayscale``: grayscale only.  No threshold or histogram equalization.
    * ``none``: return the original file unchanged.

    Optional boolean pre-steps are applied after grayscale conversion, before
    the main method, in this fixed order: dewarp -> deskew -> border_crop ->
    denoise -> gamma.

    When *save_steps* is ``True`` and *tmp_dir* is set, a JPEG snapshot is
    written to *tmp_dir* after every pipeline step for inspection.
    """

    def __init__(
        self,
        tmp_dir: Path | None = None,
        save_steps: bool = False,
        method: str = "adaptive_threshold",
        dewarp: bool = False,
        deskew: bool = False,
        border_crop: bool = False,
        denoise: bool = False,
        gamma: bool = False,
    ) -> None:
        self._tmp_dir = tmp_dir
        self._save_steps = save_steps
        self._method = method
        self._dewarp = dewarp
        self._deskew = deskew
        self._border_crop = border_crop
        self._denoise = denoise
        self._gamma = gamma

    def preprocess(self, image_path: Path) -> tuple[Path, list[str]]:
        """Preprocess *image_path* and return ``(output_path, steps_applied)``.

        When *method* is ``"none"``, the original file is returned unchanged.
        Otherwise applies a fixed handwriting-optimised pipeline:
        grayscale -> [optional pre-steps] -> contrast/binarisation method.
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
        step_num = 1

        # 1. grayscale
        if len(image.shape) == 3:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        steps.append("grayscale")
        self._save_step(image, stem, step_num, "grayscale")
        step_num += 1

        # 2. optional pre-steps (applied in fixed order)
        for enabled, fn, name in [
            (self._dewarp, self._dewarp_image, "dewarp"),
            (self._deskew, self._deskew_image, "deskew"),
            (self._border_crop, self._border_crop_image, "border_crop"),
            (self._denoise, self._denoise_image, "denoise"),
            (self._gamma, self._gamma_image, "gamma"),
        ]:
            if enabled:
                image = fn(image)
                steps.append(name)
                self._save_step(image, stem, step_num, name)
                step_num += 1

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

        # 3. contrast enhancement / binarisation
        if self._method == "clahe":
            # clipLimit=2.0: caps contrast amplification to avoid noise over-enhancement.
            # tileGridSize=(8,8): tile size for local histogram equalisation at ~300 DPI.
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            image = clahe.apply(image)
            steps.append("CLAHE")
            self._save_step(image, stem, step_num, "clahe")
        else:  # adaptive_threshold (default)
            image = cv2.adaptiveThreshold(
                image,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY,
                # blockSize=31: odd neighbourhood, empirically tuned for ~300 DPI A4 handwriting.
                # C=8: constant subtracted from local mean; higher -> more aggressive binarisation.
                blockSize=31,
                C=8,
            )
            steps.append("adaptive threshold")
            self._save_step(image, stem, step_num, "adaptive_threshold")

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

    # ── pre-step methods ──────────────────────────────────────────────────────

    def _dewarp_image(self, gray: Any) -> Any:
        """Correct perspective distortion by finding the largest quadrilateral contour."""
        import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

        h, w = gray.shape[:2]
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return gray
        largest = max(contours, key=cv2.contourArea)  # ty: ignore[no-matching-overload]
        if cv2.contourArea(largest) < 0.25 * h * w:
            return gray  # no dominant page boundary detected
        peri = cv2.arcLength(largest, True)
        approx = cv2.approxPolyDP(largest, 0.02 * peri, True)
        if len(approx) != 4:
            return gray  # not a clean quadrilateral
        pts = approx.reshape(4, 2).astype("float32")
        # Order corners: TL (min sum), BR (max sum), TR (min diff), BL (max diff)
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1)
        ordered = np.array(
            [pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]],
            dtype="float32",
        )
        tl, tr, br, bl = ordered
        width = max(int(np.linalg.norm(br - bl)), int(np.linalg.norm(tr - tl)))
        height = max(int(np.linalg.norm(tr - br)), int(np.linalg.norm(tl - bl)))
        if width < 10 or height < 10:
            return gray
        dst = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype="float32",
        )
        M = cv2.getPerspectiveTransform(ordered, dst)
        return cv2.warpPerspective(gray, M, (width, height))

    def _deskew_image(self, gray: Any) -> Any:
        """Correct page rotation up to +/-45 degrees using minimum-area bounding rectangle.

        The pinned ``opencv-python`` (4.13) returns the ``minAreaRect`` angle in the
        legacy ``[-90, 0)`` range; this normalisation folds it into the smallest-magnitude
        correction so an already-upright page (raw ``-90``) is left untouched.
        """
        import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(binary > 0))
        if len(coords) < 10:
            return gray
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        if abs(angle) < 0.5:
            return gray  # skip sub-pixel corrections
        h, w = gray.shape[:2]
        M = cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0)
        return cv2.warpAffine(
            gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )

    def _border_crop_image(self, gray: Any) -> Any:
        """Crop dark scanner borders by finding the bounding box of non-background pixels."""
        import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

        _, binary = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
        content_mask = binary < 200
        rows = np.any(content_mask, axis=1)
        cols = np.any(content_mask, axis=0)
        if not rows.any() or not cols.any():
            return gray  # all background -- nothing to crop
        rmin = int(np.where(rows)[0][0])
        rmax = int(np.where(rows)[0][-1])
        cmin = int(np.where(cols)[0][0])
        cmax = int(np.where(cols)[0][-1])
        margin = 10
        h, w = gray.shape[:2]
        rmin = max(0, rmin - margin)
        rmax = min(h - 1, rmax + margin)
        cmin = max(0, cmin - margin)
        cmax = min(w - 1, cmax + margin)
        return gray[rmin : rmax + 1, cmin : cmax + 1]

    def _denoise_image(self, gray: Any) -> Any:
        """Remove scanner noise using non-local means denoising."""
        return cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    def _gamma_image(self, gray: Any) -> Any:
        """Brighten dark/underexposed scans with gamma correction (gamma=0.5) via LUT."""
        import numpy as np  # noqa: PLC0415  # pylint: disable=import-error

        lut = (np.arange(256, dtype="float32") / 255.0) ** 0.5 * 255.0
        return lut.astype(np.uint8)[gray]

    # ── private ───────────────────────────────────────────────────────────────

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
