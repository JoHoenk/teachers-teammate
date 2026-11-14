"""The automatic image preparation: one list of steps that decides, page by page, what to do.

Teachers see one choice, "Automatic".  The steps look at the page and act only where it needs
them, so a clean scan passes unchanged and a phone photo is cut out, turned, evened out and
straightened:

====== ========= =========================================================================
 #     name      acts when
====== ========= =========================================================================
 1     measure   always; never "acts" — it only measures what the later steps decide on
 2     page      a page outline with four straight edges, a page's shape and a page's
                 contrast against the background: perspective warp to the page
 3     flatten   no ``page``, and the image is a photo (or its outline is curved): UVDoc
 4     turn      doc_ori says 90 / 180 / 270° with confidence ≥ 0.9
 5     size      the long side is above A4 at 300 DPI
 6     light     the paper is unevenly lit (shadows): each channel divided by its background
 7     deskew    a tilt between 0.3° and 10°
 8     grayscale Ollama reads gray pages; the built-in reader wants colour
====== ========= =========================================================================

There is no denoise and no binarising.  Every threshold is a named constant below; change one,
or a step, and bump :data:`AUTO_VERSION` — stored pages are then read again.  A step that needs
a page model and does not have it leaves the page alone (reason "page models not downloaded").
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import cv2
import numpy as np

from ...domain.local_models import PAGE_MODELS_NOT_DOWNLOADED
from ..onnx.store import ModelStore
from .identity import AUTO_VERSION as AUTO_VERSION  # noqa: PLC0414  # re-exported
from .identity import auto_output
from .page_models import NetFactory, PageModels
from .steps import Step, StepContext, StepResult

_logger = logging.getLogger(__name__)

# ── thresholds ────────────────────────────────────────────────────────────────

MEASURE_LONG_SIDE = 1000
"""The outline is looked for on a copy scaled down to this long side."""
PAGE_MIN_AREA = 0.25
"""The outline must cover at least this share of the image to be taken for a page."""
PAGE_MAX_AREA = 0.97
"""An outline this large is the picture's own border: there is nothing to cut away."""
PAGE_ASPECT = 2**0.5
"""A4 (and most sheets): long side / short side."""
PAGE_ASPECT_TOLERANCE = 0.15
"""How far the outline's aspect may be from :data:`PAGE_ASPECT` (share), either orientation."""
PAGE_EDGE_TOLERANCE = 0.015
"""How far an edge may bulge (share of the page's long side) and still count as straight."""
PAGE_MIN_CONTRAST = 25.0
"""The page must be at least this much brighter (0 to 255) than the ring around it.  A framed table
on a scan has the same white inside and out and is not a page."""
TURN_MIN_CONFIDENCE = 0.9
"""doc_ori's answer is acted on from this confidence."""
SIZE_LONG_SIDE = 3508
"""A4 at 300 DPI: a larger page is scaled down to this long side."""
LIGHT_UNEVENNESS = 0.12
"""Spread of the paper's brightness (see :func:`unevenness`) from which ``light`` acts."""
LIGHT_SCALE = 4
"""The background is worked out on a copy this many times smaller."""
LIGHT_DILATE = 7
LIGHT_MEDIAN = 21
SKEW_MIN = 0.3
SKEW_MAX = 10.0
"""A tilt is corrected from :data:`SKEW_MIN` up to :data:`SKEW_MAX` degrees; a page turned more
than that is left alone."""
SKEW_SEARCH = 12.0
"""The tilt is searched over ±this many degrees: wider than :data:`SKEW_MAX`, so a page tilted
too far is seen as such and not as the nearest allowed angle."""
SKEW_LONG_SIDE = 800

_TURN_ROTATIONS = {
    90: cv2.ROTATE_90_COUNTERCLOCKWISE,
    180: cv2.ROTATE_180,
    270: cv2.ROTATE_90_CLOCKWISE,
}
"""doc_ori names how far the page is turned clockwise; the page is turned back by this."""


# ── measuring ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PageOutline:
    """The biggest outline found in a picture, in full-size pixel coordinates."""

    corners: np.ndarray | None
    """Top-left, top-right, bottom-right, bottom-left; ``None`` if the outline has no 4 corners."""
    aspect_ok: bool
    contrast_ok: bool
    straight: bool

    @property
    def is_page(self) -> bool:
        """Return whether the outline is a flat page that can be cut out."""
        return self.corners is not None and self.aspect_ok and self.contrast_ok and self.straight

    @property
    def is_curved(self) -> bool:
        """Return whether it is a page's outline with bulging edges (a curved page)."""
        return (
            self.corners is not None and self.aspect_ok and self.contrast_ok and not self.straight
        )

    @property
    def is_page_like_but_broken(self) -> bool:
        """Return whether a region stands out like a page but is not shaped like one."""
        return self.contrast_ok and (self.corners is None or not self.aspect_ok)


def to_gray(image: Any) -> Any:
    """Return *image* as one gray channel (a gray image is returned as it is)."""
    if len(image.shape) == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image


def _ordered(pts: np.ndarray) -> np.ndarray:
    """Return the four corners as top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]],
        dtype=np.float32,
    )


def _side_lengths(corners: np.ndarray) -> tuple[float, float]:
    """Return the page's ``(width, height)``: the longer of each pair of opposite sides."""
    tl, tr, br, bl = corners
    width = max(float(np.linalg.norm(br - bl)), float(np.linalg.norm(tr - tl)))
    height = max(float(np.linalg.norm(tr - br)), float(np.linalg.norm(tl - bl)))
    return width, height


def find_outline(gray: Any) -> PageOutline | None:
    """Return the biggest outline in *gray* and what it is, or ``None`` if there is none.

    The outline is the largest contour of the picture's edges that covers at least
    :data:`PAGE_MIN_AREA` of it and not nearly all of it.
    """
    full_h, full_w = gray.shape[:2]
    scale = min(1.0, MEASURE_LONG_SIDE / max(full_h, full_w))
    small = (
        cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else gray
    )
    h, w = small.shape[:2]
    edges = cv2.Canny(cv2.GaussianBlur(small, (5, 5), 0), 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)  # ty: ignore[no-matching-overload]
    share = cv2.contourArea(largest) / (h * w)
    if share < PAGE_MIN_AREA or share > PAGE_MAX_AREA:
        return None

    approx = cv2.approxPolyDP(largest, 0.02 * cv2.arcLength(largest, True), True)
    hull = cv2.convexHull(largest)
    contrast_ok = _contrast(small, hull) >= PAGE_MIN_CONTRAST
    if len(approx) != 4 or not cv2.isContourConvex(approx):
        return PageOutline(None, aspect_ok=False, contrast_ok=contrast_ok, straight=False)

    quad = _ordered(approx.reshape(4, 2).astype(np.float32))
    width, height = _side_lengths(quad)
    if min(width, height) < 10:
        return PageOutline(None, aspect_ok=False, contrast_ok=contrast_ok, straight=False)
    aspect = max(width, height) / min(width, height)
    aspect_ok = abs(aspect - PAGE_ASPECT) / PAGE_ASPECT <= PAGE_ASPECT_TOLERANCE
    contrast_ok = _contrast(small, quad.astype(np.int32)) >= PAGE_MIN_CONTRAST
    return PageOutline(
        corners=quad / scale,
        aspect_ok=aspect_ok,
        contrast_ok=contrast_ok,
        straight=_edges_straight(largest, quad, max(width, height)),
    )


def _contrast(gray: Any, polygon: np.ndarray) -> float:
    """Return how much brighter the inside of *polygon* is than the ring just outside it."""
    h, w = gray.shape[:2]
    mask = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask, np.asarray(polygon, dtype=np.int32).reshape(-1, 2), 1)
    grow = max(3, round(0.03 * max(h, w)))
    ring = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_RECT, (2 * grow + 1,) * 2)) - mask
    if not ring.any() or not mask.any():
        return 0.0
    return float(gray[mask > 0].mean() - gray[ring > 0].mean())


def _edges_straight(contour: np.ndarray, quad: np.ndarray, long_side: float) -> bool:
    """Return whether *contour* stays within :data:`PAGE_EDGE_TOLERANCE` of the quad's sides."""
    polygon = quad.reshape(-1, 1, 2).astype(np.float32)
    points = contour.reshape(-1, 2)
    step = max(1, len(points) // 400)
    gaps = [
        abs(cv2.pointPolygonTest(polygon, (float(x), float(y)), True)) for x, y in points[::step]
    ]
    return float(np.percentile(gaps, 95)) <= PAGE_EDGE_TOLERANCE * long_side


def background_map(channel: Any) -> Any:
    """Return the brightness of the paper under the writing, one channel, at the channel's size.

    A small copy is dilated (the dark strokes of the writing vanish), median-filtered (the
    remaining unevenness is the light) and scaled back up.
    """
    h, w = channel.shape[:2]
    small = cv2.resize(
        channel, (max(1, w // LIGHT_SCALE), max(1, h // LIGHT_SCALE)), interpolation=cv2.INTER_AREA
    )
    small = cv2.dilate(small, np.ones((LIGHT_DILATE, LIGHT_DILATE), np.uint8))
    small = cv2.medianBlur(small, LIGHT_MEDIAN)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def unevenness(background: Any) -> float:
    """Return how unevenly the paper is lit: the spread of its brightness, 0 (even) to 1."""
    low, high = np.percentile(background, (5, 95))
    return 0.0 if high <= 0 else float((high - low) / high)


@dataclass(frozen=True)
class Measure:
    """Measure what the later steps decide on; never changes the image (never "acts").

    Leaves in ``ctx.measurements``: ``long_side``, ``outline`` (:class:`PageOutline` or
    ``None``), ``unevenness`` (of the middle of the picture, so a desk around the page does not
    count) and ``photo`` (the file carries camera tags, a page-like region has a broken shape,
    or the light is uneven: scans and rendered PDF pages have none of these).
    """

    name: str = "measure"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Measure *image* and return it unchanged."""
        gray = to_gray(image)
        h, w = gray.shape[:2]
        outline = find_outline(gray)
        middle = gray[h // 10 : h - h // 10, w // 10 : w - w // 10]
        uneven = unevenness(background_map(middle)) if min(middle.shape[:2]) >= 40 else 0.0
        photo = bool(
            ctx.camera_tags
            or (outline is not None and outline.is_page_like_but_broken)
            or uneven >= LIGHT_UNEVENNESS
        )
        ctx.measurements.update(
            long_side=max(h, w), outline=outline, unevenness=uneven, photo=photo
        )
        return StepResult(
            image,
            acted=False,
            reason=(
                f"long side {max(h, w)}, outline "
                f"{'none' if outline is None else 'page' if outline.is_page else 'other'}, "
                f"unevenness {uneven:.2f}, {'photo' if photo else 'scan'}"
            ),
        )


# ── the steps that change the page ────────────────────────────────────────────


@dataclass(frozen=True)
class PageCorners:
    """Cut the page out of a photo with a perspective warp, when its outline is a flat page."""

    name: str = "page"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Return the page cut out, or *image* unchanged."""
        outline: PageOutline | None = ctx.measurements.get("outline")
        ctx.measurements["page_acted"] = False
        if outline is None or not outline.is_page or outline.corners is None:
            return StepResult(image, acted=False, reason="no flat page outline")
        width, height = (round(v) for v in _side_lengths(outline.corners))
        if width < 10 or height < 10:
            return StepResult(image, acted=False, reason="outline too small")
        target = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32
        )
        matrix = cv2.getPerspectiveTransform(outline.corners.astype(np.float32), target)
        ctx.measurements["page_acted"] = True
        return StepResult(
            cv2.warpPerspective(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE),
            acted=True,
            reason=f"page {width}x{height}",
        )


@dataclass
class Flatten:
    """Flatten a photographed or curved page with UVDoc, where the corner cut-out did not act."""

    models: PageModels
    name: str = "flatten"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:
        """Return the flattened page, or *image* unchanged."""
        outline: PageOutline | None = ctx.measurements.get("outline")
        curved = outline is not None and outline.is_curved
        if ctx.measurements.get("page_acted") or not (ctx.measurements.get("photo") or curved):
            return StepResult(image, acted=False, reason="not a photo that needs flattening")
        if not self.models.can_flatten:
            self.models.note_missing()
            return StepResult(image, acted=False, reason=PAGE_MODELS_NOT_DOWNLOADED)
        try:
            return StepResult(self.models.flatten(image), acted=True, reason="flattened")
        except Exception as exc:  # noqa: BLE001  # a model that fails must not fail the page
            _logger.warning("Flattening the page failed, the page is used as it is: %s", exc)
            return StepResult(image, acted=False, reason=f"the flatten model failed: {exc}")


@dataclass
class Turn:
    """Turn a sideways or upside-down page upright, when doc_ori is sure."""

    models: PageModels
    name: str = "turn"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:  # noqa: ARG002
        """Return the page turned upright, or *image* unchanged."""
        if not self.models.can_turn:
            self.models.note_missing()
            return StepResult(image, acted=False, reason=PAGE_MODELS_NOT_DOWNLOADED)
        try:
            degrees, confidence = self.models.orientation(image)
        except Exception as exc:  # noqa: BLE001  # a model that fails must not fail the page
            _logger.warning("Finding the page's turn failed, the page is used as it is: %s", exc)
            return StepResult(image, acted=False, reason=f"the turn model failed: {exc}")
        if degrees == 0 or confidence < TURN_MIN_CONFIDENCE:
            return StepResult(image, acted=False, reason=f"{degrees}° at {confidence:.2f}")
        return StepResult(
            cv2.rotate(image, _TURN_ROTATIONS[degrees]),
            acted=True,
            reason=f"turned back from {degrees}° ({confidence:.2f})",
        )


@dataclass(frozen=True)
class SizeCap:
    """Scale a page larger than A4 at 300 DPI down to it (never up)."""

    name: str = "size"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:  # noqa: ARG002
        """Return the page at most :data:`SIZE_LONG_SIDE` pixels on the long side."""
        h, w = image.shape[:2]
        if max(h, w) <= SIZE_LONG_SIDE:
            return StepResult(image, acted=False, reason=f"long side {max(h, w)}")
        factor = SIZE_LONG_SIDE / max(h, w)
        scaled = cv2.resize(
            image,
            (max(1, round(w * factor)), max(1, round(h * factor))),
            interpolation=cv2.INTER_AREA,
        )
        return StepResult(scaled, acted=True, reason=f"{max(h, w)} → {SIZE_LONG_SIDE}")


@dataclass(frozen=True)
class EvenLight:
    """Even out shadows: each channel divided by the paper's brightness under the writing."""

    name: str = "light"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:  # noqa: ARG002
        """Return the page lit evenly, or *image* unchanged when the light is even already."""
        h, w = image.shape[:2]
        if min(h, w) < 4 * LIGHT_MEDIAN:
            return StepResult(image, acted=False, reason="too small to measure the light")
        uneven = unevenness(background_map(to_gray(image)))
        if uneven < LIGHT_UNEVENNESS:
            return StepResult(image, acted=False, reason=f"unevenness {uneven:.2f}")
        channels = [image] if len(image.shape) == 2 else cv2.split(image)
        evened = []
        for channel in channels:
            background = np.maximum(background_map(channel), 1).astype(np.float32)
            evened.append(np.clip(channel.astype(np.float32) * 255.0 / background, 0, 255))
        result = evened[0] if len(evened) == 1 else cv2.merge(evened)
        return StepResult(result.astype(np.uint8), acted=True, reason=f"unevenness {uneven:.2f}")


def _ink_mask(gray: Any) -> Any:
    """Return a boolean mask of the ink: pixels darker than their surroundings.

    A local threshold, so plain paper counts as background whatever its tone; a light blur first
    suppresses sensor grain.  Large dark areas (a scanner bed) are left out.
    """
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    ink = (
        cv2.adaptiveThreshold(blurred, 1, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 31, 10)
        > 0
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
    opened = cv2.morphologyEx((gray < 128).astype(np.uint8), cv2.MORPH_OPEN, kernel)
    return ink & ~(cv2.dilate(opened, kernel, iterations=2) > 0)


def estimate_skew_angle(gray: Any) -> float:
    """Return the tilt of the writing in degrees (to rotate the page by), 0 if there is none.

    A projection-profile search over ±:data:`SKEW_SEARCH` degrees: the ink mask of a downscaled
    copy is rotated by each candidate angle and scored by the variance of its row sums — lines
    aligned with the rows give a sharp profile, a residual tilt smears the ink.  Coarse steps of
    0.5°, then 0.05° around the best.  The answer must clearly beat 0°, so a blank or upright
    page is left alone.
    """
    h, w = gray.shape[:2]
    scale = min(1.0, SKEW_LONG_SIDE / max(h, w))
    small = (
        cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else gray
    )
    ink = _ink_mask(small).astype(np.float32)
    if ink.mean() < 0.002:
        return 0.0  # essentially blank
    sh, sw = small.shape[:2]
    centre = (sw / 2, sh / 2)

    def score(angle: float) -> float:
        matrix = cv2.getRotationMatrix2D(centre, angle, 1.0)
        rotated = cv2.warpAffine(ink, matrix, (sw, sh), flags=cv2.INTER_LINEAR, borderValue=0)
        return float(np.var(rotated.sum(axis=1)))

    coarse = sorted((float(a) for a in np.arange(-SKEW_SEARCH, SKEW_SEARCH + 0.01, 0.5)), key=abs)
    best = max(coarse, key=score)  # ty: ignore[no-matching-overload]
    fine = sorted((float(a) for a in np.arange(best - 0.5, best + 0.51, 0.05)), key=abs)
    angle = float(max(fine, key=score))  # ty: ignore[no-matching-overload]
    if score(angle) < 1.10 * score(0.0):
        return 0.0  # no clear improvement over leaving the page as it is
    return angle


@dataclass(frozen=True)
class Deskew:
    """Straighten a slightly tilted page (0.3° to 10°); a page tilted more is left alone."""

    name: str = "deskew"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:  # noqa: ARG002
        """Return the straightened page, or *image* unchanged."""
        angle = estimate_skew_angle(to_gray(image))
        if abs(angle) < SKEW_MIN:
            return StepResult(image, acted=False, reason=f"tilt {angle:.1f}°")
        if abs(angle) > SKEW_MAX:
            return StepResult(image, acted=False, reason=f"tilt {angle:.1f}° is too much")
        h, w = image.shape[:2]
        matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        turned = cv2.warpAffine(
            image, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        )
        return StepResult(turned, acted=True, reason=f"tilt {angle:.1f}°")


@dataclass(frozen=True)
class Output:
    """Hand the engine what it reads best: Ollama a gray page, the built-in reader colour."""

    gray: bool
    name: str = "grayscale"

    def apply(self, image: Any, ctx: StepContext) -> StepResult:  # noqa: ARG002
        """Return the gray page when the engine wants one; colour is kept as it is."""
        if not self.gray or len(image.shape) == 2:
            return StepResult(image, acted=False, reason="colour kept" if not self.gray else "")
        return StepResult(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), acted=True)


def auto_steps(engine: str, store: ModelStore, net_factory: NetFactory | None = None) -> list[Step]:
    """Return the automatic preparation for *engine*, with the page models in *store*.

    The model steps are always in the list; without their files they leave the page alone.
    """
    models = PageModels(store, net_factory) if net_factory else PageModels(store)
    return [
        Measure(),
        PageCorners(),
        Flatten(models),
        Turn(models),
        SizeCap(),
        EvenLight(),
        Deskew(),
        Output(gray=auto_output(engine) == "gray"),
    ]
