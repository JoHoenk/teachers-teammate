"""Reading and writing image files with OpenCV, safe for non-ASCII paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..exceptions import PipelineInputError


def read_image(path: Path, flags: int = cv2.IMREAD_COLOR) -> Any:
    """Load *path* as a BGR array (or as *flags* ask), or ``None`` if it cannot be read.

    ``cv2.imread`` takes the path as a narrow string, which on Windows is decoded
    with the ANSI code page, so names like ``märz_page0.png`` are never found.
    Reading the bytes in Python and decoding them in memory avoids that.  With the default
    flags OpenCV applies the file's EXIF orientation.
    """
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)


def write_image(path: Path, image: Any) -> None:
    """Encode *image* by the extension of *path* and write it (Unicode-safe, see above).

    Raises:
        PipelineInputError: if encoding or writing fails.
    """
    ok, encoded = cv2.imencode(path.suffix or ".png", image)
    if not ok:
        raise PipelineInputError(f"Cannot encode image: {path}")
    try:
        encoded.tofile(path)
    except OSError as exc:
        raise PipelineInputError(f"Cannot write image: {path}: {exc}") from exc
