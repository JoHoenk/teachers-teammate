"""Policy for deciding whether a PDF's embedded text can replace OCR.

Pure business rules, no I/O: the input provider measures each page
(:class:`PageTextStats`) and asks :func:`use_text_layer` whether the document's own
text should be used instead of rendering and recognising the pages.

A text layer is only trusted when it is *complete*: scanner software often embeds
poor OCR of handwriting, and a typed worksheet filled in on a tablet has text for the
printed questions only while the answers are images or ink.  Taking such a layer
would silently drop the handwriting, so ``auto`` requires every page to look
text-only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal, cast

PdfTextLayerMode = Literal["auto", "always", "never"]

PDF_TEXT_LAYER_MODES: tuple[PdfTextLayerMode, ...] = ("auto", "always", "never")

MIN_CHARS_PER_PAGE = 50
"""Fewer extracted characters than this and a page is treated as having no real text."""

MIN_PRINTABLE_RATIO = 0.85
"""Minimum share of letters/digits/punctuation (vs. replacement or control chars)."""

MAX_IMAGE_COVERAGE = 0.05
"""A page with images covering more than this share of its area is treated as scanned.

Deliberately strict: a photographed answer pasted into a worksheet covers only a small part
of the page and must not be mistaken for a decorative logo.  Wrongly falling back to OCR
only costs time; wrongly trusting the text layer silently drops student work."""

MIN_FREEHAND_PATHS = 1
"""Curved or diagonal vector paths on a page from which it is treated as hand-drawn.

Note-taking apps store pen strokes as vector paths, which neither the text layer nor the
image coverage sees, and even a short answer ("42", a tick) is only a few strokes.  Ruled
lines and table borders are axis-aligned and do not count; a diagram, a rounded checkbox or
a logo made of curves does, which merely falls back to (safe) OCR."""


@dataclass(frozen=True)
class PageTextStats:
    """Measurements of one PDF page's text layer and non-text content."""

    char_count: int
    printable_ratio: float
    image_coverage: float
    has_content_annotations: bool = False
    """Annotations that add content the text layer lacks (ink, text boxes, form fields …)."""
    freehand_path_count: int = 0


def parse_pdf_text_layer(value: object) -> PdfTextLayerMode | None:
    """Return *value* as a :data:`PdfTextLayerMode` (case/whitespace folded), else ``None``."""
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    if lowered in PDF_TEXT_LAYER_MODES:
        return cast(PdfTextLayerMode, lowered)
    return None


def normalize_pdf_text_layer(value: object) -> PdfTextLayerMode:
    """Return *value* as a valid :data:`PdfTextLayerMode`, falling back to ``"auto"``."""
    return parse_pdf_text_layer(value) or "auto"


def page_has_usable_text(stats: PageTextStats) -> bool:
    """Return True when *stats* describe a clean, text-only page."""
    return (
        stats.char_count >= MIN_CHARS_PER_PAGE
        and stats.printable_ratio >= MIN_PRINTABLE_RATIO
        and stats.image_coverage <= MAX_IMAGE_COVERAGE
        and not stats.has_content_annotations
        and stats.freehand_path_count < MIN_FREEHAND_PATHS
    )


def use_text_layer(mode: PdfTextLayerMode, pages: Iterable[PageTextStats]) -> bool:
    """Return True when the document's embedded text should replace OCR.

    Args:
        mode: ``"never"`` always OCRs; ``"always"`` trusts the text layer when every page
            has some text; ``"auto"`` additionally requires every page to pass
            :func:`page_has_usable_text`.
        pages: One entry per page, in order.  Consumed lazily and only up to the first
            page that rules the text layer out, so a generator can measure on demand.

    A document without pages never uses a text layer.
    """
    if mode == "never":
        return False
    any_page = False
    for page in pages:
        any_page = True
        if mode == "always":
            usable = page.char_count > 0
        else:
            usable = page_has_usable_text(page)
        if not usable:
            return False
    return any_page
