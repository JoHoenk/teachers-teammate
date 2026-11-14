"""The pictures of the automatic-preparation tests, drawn with OpenCV, and their golden digests.

Every picture is drawn with OpenCV's own primitives and no random numbers, so nothing depends on
a font file or a random-number stream.  The digests in :data:`GOLDEN` belong to the pinned OpenCV
build on Linux (see ``test_preprocessing_auto_golden.py``).
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

PAGE_W, PAGE_H = 990, 1400
"""An A4-ish page at about 120 DPI: wide enough for text the detectors see."""


def text_page(
    lines: int = 12, *, paper: int = 245, ink: tuple[int, int, int] = (60, 40, 30)
) -> Any:
    """Return a colour page of *paper* brightness with *lines* lines of Hershey text."""
    page = np.full((PAGE_H, PAGE_W, 3), paper, np.uint8)
    for i in range(lines):
        cv2.putText(
            page,
            f"Line {i} Hello world test",
            (80, 150 + i * 90),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.6,
            ink,
            3,
        )
    return page


def dense_text_page() -> Any:
    """Return a page of smaller text, many lines: what a turn model sees on a real page."""
    page = np.full((PAGE_H, PAGE_W, 3), 245, np.uint8)
    for i in range(30):
        cv2.putText(
            page,
            f"Row {i} the quick brown fox jumps over the lazy dog",
            (60, 90 + i * 42),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (50, 40, 30),
            2,
        )
    return page


def photo_of_page(page: Any | None = None) -> Any:
    """Return *page* photographed at an angle on a dark desk (1300 x 1700)."""
    page = text_page() if page is None else page
    h, w = page.shape[:2]
    source = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], dtype=np.float32)
    target = np.array([[200, 150], [1050, 100], [1100, 1450], [120, 1500]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, target)
    desk = np.full((1700, 1300, 3), (40, 60, 90), np.uint8)
    return cv2.warpPerspective(
        page, matrix, (1300, 1700), dst=desk, borderMode=cv2.BORDER_TRANSPARENT
    )


def curved_photo() -> Any:
    """Return :func:`photo_of_page` bent by a sine wave: no straight edges, no flat page."""
    photo = photo_of_page()
    h, w = photo.shape[:2]
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    return cv2.remap(
        photo, xs + 25 * np.sin(ys / h * np.pi * 2), ys, cv2.INTER_LINEAR, borderValue=(40, 60, 90)
    )


def shadow_page(low: float = 0.45) -> Any:
    """Return a text page lit from the right: brightness falls to *low* at the left edge."""
    gradient = np.linspace(low, 1.0, PAGE_W)[None, :, None]
    return (text_page().astype(np.float32) * gradient).astype(np.uint8)


def tilted_page(angle: float) -> Any:
    """Return a text page rotated by *angle* degrees, the corners filled with paper."""
    matrix = cv2.getRotationMatrix2D((PAGE_W / 2, PAGE_H / 2), angle, 1.0)
    return cv2.warpAffine(text_page(), matrix, (PAGE_W, PAGE_H), borderValue=(245, 245, 245))


def framed_table_scan() -> Any:
    """Return a clean white scan with a big framed box and a line of text in it."""
    scan = np.full((1700, 1200, 3), 250, np.uint8)
    cv2.rectangle(scan, (100, 200), (1100, 1000), (30, 30, 30), 4)
    cv2.putText(scan, "hello there", (150, 400), cv2.FONT_HERSHEY_SIMPLEX, 2, (0, 0, 0), 4)
    return scan


def pencil_photo() -> Any:
    """Return a faint pencil text on grey paper with a shadow: the hard case for ``light``."""
    page = text_page(paper=215, ink=(150, 150, 150))
    gradient = np.linspace(0.5, 1.0, PAGE_W)[None, :, None]
    return (page.astype(np.float32) * gradient).astype(np.uint8)


#: Digests of the automatic preparation's output (see test_preprocessing_auto_golden.py).
#: Regenerate with the snippet in that file when AUTO_VERSION is bumped on purpose.
GOLDEN: dict[str, str] = {
    "photo": "350b9e33384b502c",
    "shadow": "8a7c9a516680cea8",
    "tilt": "d7fbb37a423549a0",
    "sample_png": "b4be19f93047d26b",
    "sample_pdf": "ec1937b5a46243eb",
}
