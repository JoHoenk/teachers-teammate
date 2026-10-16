"""Input providers: load source files into raw page images or text.

PDFs with a complete embedded text layer are read as text (no OCR needed); all
other PDF pages are rendered via pypdfium2.  Image files (JPG, PNG …) are passed
through as-is.  All temporary renders are written to *tmp_dir*; the caller is
responsible for cleanup.
"""

from __future__ import annotations

from collections.abc import Iterator
import ctypes
import dataclasses
from pathlib import Path
import unicodedata

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from ..domain.formatted_text import join_labelled_pages
from ..domain.pdf_text_layer import (
    MIN_FREEHAND_PATHS,
    PageTextStats,
    PdfTextLayerMode,
    page_has_usable_text,
    unusable_text_reason,
    use_text_layer,
)
from ..interfaces import InputPayload, InputProvider, InputUnit

_UNPRINTABLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})

# Annotations that only mark up what is already on the page.  Every other subtype (ink,
# free text, stamps, shapes, form widgets, sticky notes) can carry content that
# ``get_text_range()`` does not return, so a page with one is not "text-only".
_MARKUP_ONLY_ANNOT_SUBTYPES = frozenset(
    {
        pdfium_c.FPDF_ANNOT_LINK,
        pdfium_c.FPDF_ANNOT_POPUP,
        pdfium_c.FPDF_ANNOT_HIGHLIGHT,
        pdfium_c.FPDF_ANNOT_UNDERLINE,
        pdfium_c.FPDF_ANNOT_SQUIGGLY,
        pdfium_c.FPDF_ANNOT_STRIKEOUT,
    }
)

_MAX_FORM_DEPTH = 15
_AXIS_TOLERANCE = 0.01
"""Points; a line segment moving less than this along one axis counts as axis-aligned."""


def _is_printable(char: str) -> bool:
    """Return False for control, unassigned, private-use and replacement characters."""
    return char != "\ufffd" and unicodedata.category(char) not in _UNPRINTABLE_CATEGORIES


def _clean_text(text: str) -> str:
    """Drop unprintable characters, keeping line breaks and tabs.

    pdfium can emit control codes or U+FFFE (e.g. at hyphenation points).  They would
    otherwise be stored as ``raw_text`` and make the DOCX writer reject the document
    ("All strings must be XML compatible").
    """
    return "".join(char for char in text if char in "\n\t" or _is_printable(char))


def _object_bounds(obj: pdfium.PdfObject) -> tuple[float, float, float, float]:
    """Return *obj*'s (left, bottom, right, top) in the coordinate space of its container."""
    # pypdfium2 4.x calls it get_pos(), 5.x get_bounds().
    get_bounds = getattr(obj, "get_bounds", None) or obj.get_pos  # ty: ignore[unresolved-attribute]
    return get_bounds()


def _image_rects(
    page: pdfium.PdfPage,
    form: pdfium.PdfObject | None = None,
    matrix: pdfium.PdfMatrix | None = None,
    depth: int = 0,
) -> Iterator[tuple[float, float, float, float]]:
    """Yield the page-space rectangle of every image, descending into form XObjects.

    pdfium reports bounds of objects nested in a form relative to that form, so each
    form's matrix is concatenated on the way down instead of using the raw values.
    """
    if matrix is None:
        matrix = pdfium.PdfMatrix()
    # max_depth=1 keeps pypdfium2 from recursing itself; the recursion below tracks matrices.
    objects = page.get_objects(
        filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE, pdfium_c.FPDF_PAGEOBJ_FORM],
        max_depth=1,
        form=form,
        level=depth,
    )
    for obj in objects:
        if obj.type == pdfium_c.FPDF_PAGEOBJ_IMAGE:
            yield matrix.on_rect(*_object_bounds(obj))
        elif depth < _MAX_FORM_DEPTH:
            yield from _image_rects(page, obj, obj.get_matrix().multiply(matrix), depth + 1)


def _image_coverage(page: pdfium.PdfPage) -> float:
    """Return the share (0..1) of the page area covered by embedded images."""
    # The page box is in the same unrotated user space as the object bounds and need not
    # start at the origin (cropped pages), so clip against it rather than (0, 0, w, h).
    box_left, box_bottom, box_right, box_top = page.get_bbox()
    page_area = (box_right - box_left) * (box_top - box_bottom)
    if page_area <= 0:
        return 0.0
    covered = 0.0
    for left, bottom, right, top in _image_rects(page):
        covered += max(0.0, min(right, box_right) - max(left, box_left)) * max(
            0.0, min(top, box_top) - max(bottom, box_bottom)
        )
    return min(1.0, covered / page_area)


def _has_content_annotations(page: pdfium.PdfPage) -> bool:
    """Return True when the page carries annotations that add content (ink, fields, …)."""
    raw_page = page.raw
    for index in range(pdfium_c.FPDFPage_GetAnnotCount(raw_page)):
        annot = pdfium_c.FPDFPage_GetAnnot(raw_page, index)
        try:
            if pdfium_c.FPDFAnnot_GetSubtype(annot) not in _MARKUP_ONLY_ANNOT_SUBTYPES:
                return True
        finally:
            pdfium_c.FPDFPage_CloseAnnot(annot)
    return False


def _is_freehand_path(path: pdfium.PdfObject) -> bool:
    """Return True for a path with a curve or a diagonal line (not a ruled line or box)."""
    previous_x: float | None = None
    previous_y: float | None = None
    for index in range(max(pdfium_c.FPDFPath_CountSegments(path), 0)):
        segment = pdfium_c.FPDFPath_GetPathSegment(path, index)
        kind = pdfium_c.FPDFPathSegment_GetType(segment)
        if kind == pdfium_c.FPDF_SEGMENT_BEZIERTO:
            return True
        x, y = ctypes.c_float(0.0), ctypes.c_float(0.0)
        if not pdfium_c.FPDFPathSegment_GetPoint(segment, x, y):
            continue
        if (
            kind == pdfium_c.FPDF_SEGMENT_LINETO
            and previous_x is not None
            and previous_y is not None
            and abs(x.value - previous_x) > _AXIS_TOLERANCE
            and abs(y.value - previous_y) > _AXIS_TOLERANCE
        ):
            return True
        previous_x, previous_y = x.value, y.value
    return False


def _freehand_path_count(page: pdfium.PdfPage) -> int:
    """Count freehand-looking vector paths, stopping once the policy threshold is reached."""
    count = 0
    for obj in page.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_PATH]):
        if _is_freehand_path(obj):
            count += 1
            if count >= MIN_FREEHAND_PATHS:
                break
    return count


def _measure_page(page: pdfium.PdfPage, *, check_layout: bool) -> tuple[str, PageTextStats]:
    """Return the page's embedded text (stripped) and its :class:`PageTextStats`.

    The non-text measurements (images, annotations, vector paths) are only taken when
    *check_layout* is set and the text itself is already usable: a page that fails on its
    text alone rules the text layer out, so the extra pass would be wasted.
    """
    textpage = page.get_textpage()
    try:
        # get_text_range() (not get_text_bounded()) so text running past the page edge is kept.
        text = textpage.get_text_range().replace("\r\n", "\n").replace("\r", "\n").strip()
    finally:
        textpage.close()
    visible = [char for char in text if not char.isspace()]
    printable = sum(1 for char in visible if _is_printable(char))
    # char_count is what survives cleaning, so a page of only control codes has no text.
    stats = PageTextStats(
        char_count=printable,
        printable_ratio=printable / len(visible) if visible else 0.0,
        image_coverage=0.0,
    )
    text = _clean_text(text).strip()
    if check_layout and page_has_usable_text(stats):
        stats = dataclasses.replace(
            stats,
            image_coverage=_image_coverage(page),
            has_content_annotations=_has_content_annotations(page),
            freehand_path_count=_freehand_path_count(page),
        )
    return text, stats


class PdfInputProvider(InputProvider):
    """Loads a PDF as embedded text when it has a complete text layer, else as page images.

    Page renders are written to *tmp_dir*.  The first page path is returned as the
    source preview image for the output document — also for text-only PDFs, where
    that is the only page rendered.
    """

    def __init__(
        self,
        tmp_dir: Path,
        pdf_dpi: int = 300,
        text_layer: PdfTextLayerMode = "auto",
    ) -> None:
        self._tmp_dir = tmp_dir
        self._pdf_dpi = pdf_dpi
        self._text_layer = text_layer

    def load(self, file_path: Path) -> InputPayload:
        """Return the PDF's embedded text, or render every page to PNG for OCR.

        Args:
            file_path: Path to the PDF file.

        Returns:
            :class:`InputPayload` with a single text unit (all pages) and the first
            page as preview source image when the text layer is used; otherwise
            image units for all rendered pages and the first page as preview source
            image.  An empty payload for an empty PDF.
        """
        pdf = pdfium.PdfDocument(str(file_path))
        try:
            page_count = len(pdf)
            text_layer_note: str | None = "text layer ignored (mode: never)"
            if self._text_layer != "never":
                texts: list[str] = []
                measured: list[PageTextStats] = []

                def measure_pages() -> Iterator[PageTextStats]:
                    for page_num in range(page_count):
                        page = pdf[page_num]
                        try:
                            page_text, page_stats = _measure_page(
                                page, check_layout=self._text_layer == "auto"
                            )
                        finally:
                            page.close()
                        texts.append(page_text)
                        measured.append(page_stats)
                        yield page_stats

                # Pages are measured on demand and only up to the first one that rules
                # the text layer out, so scanned PDFs go straight on to rendering.
                if use_text_layer(self._text_layer, measure_pages()):
                    preview = self._render_page(pdf, 0, file_path)
                    return InputPayload(
                        units=[InputUnit.text_content(join_labelled_pages(texts))],
                        source_image=preview,
                        text_origin="pdf_text_layer",
                    )
                # The scan stops at the first page that rules the text layer out: the last one.
                text_layer_note = (
                    "PDF has no pages"
                    if not measured
                    else f"page {len(measured)}: "
                    + (unusable_text_reason(measured[-1]) or "no embedded text")
                )

            units: list[InputUnit] = []
            source_image: Path | None = None
            for page_num in range(page_count):
                raw_path = self._render_page(pdf, page_num, file_path)
                units.append(InputUnit.image(raw_path))
                if page_num == 0:
                    source_image = raw_path
        finally:
            pdf.close()
        return InputPayload(units=units, source_image=source_image, text_layer_note=text_layer_note)

    def _render_page(self, pdf: pdfium.PdfDocument, page_num: int, file_path: Path) -> Path:
        """Render page *page_num* of *pdf* to a PNG in the temp dir and return its path."""
        page = pdf[page_num]
        try:
            bitmap = page.render(scale=self._pdf_dpi / 72.0)
            raw_path = self._tmp_dir / f"{file_path.stem}_page{page_num}.png"
            bitmap.to_pil().save(str(raw_path))
        finally:
            page.close()
        return raw_path


class ImageInputProvider(InputProvider):
    """Passes an image file through unchanged — no rendering needed."""

    def load(self, file_path: Path) -> InputPayload:
        """Return the image file itself as the single page.

        Args:
            file_path: Path to the image file (JPG, PNG, etc.).

        Returns:
            :class:`InputPayload` with a single image unit and source image.
        """
        return InputPayload(units=[InputUnit.image(file_path)], source_image=file_path)


class TextInputProvider(InputProvider):
    """Loads plain text files as text units for OCR-bypass processing."""

    def load(self, file_path: Path) -> InputPayload:
        """Return file contents as a single text unit."""
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = file_path.read_text(encoding="latin-1")
        return InputPayload(
            units=[InputUnit.text_content(text)], source_image=None, text_origin="file"
        )
