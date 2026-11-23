"""Unit tests for teachers_teammate.input_providers."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from teachers_teammate.infrastructure.input_providers import (
    ImageInputProvider,
    PdfInputProvider,
    TextInputProvider,
    pdf_has_text_layer,
)
from teachers_teammate.interfaces import ImageUnit, TextUnit
from tests.conftest import write_text_pdf


def test_image_provider_list_has_one_entry(sample_png: Path) -> None:
    """
    Given  an ImageInputProvider and a single PNG file path
    When   load() is called
    Then   exactly one page path is returned and it equals the input file
    """
    provider = ImageInputProvider()
    payload = provider.load(sample_png)
    assert len(payload.units) == 1
    unit = payload.units[0]
    assert isinstance(unit, ImageUnit)
    assert unit.path == sample_png
    assert payload.source_image == sample_png


def test_pdf_provider_extracts_at_least_one_page(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  a PdfInputProvider and a single-page PDF
    When   load() is called
    Then   at least one page image path is returned
    """
    provider = PdfInputProvider(tmp_dir=tmp_path)
    payload = provider.load(sample_pdf)
    assert len(payload.units) >= 1


def test_pdf_provider_pages_are_existing_files(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  a PdfInputProvider and a single-page PDF
    When   load() is called
    Then   every returned page path points to an existing file on disk
    """
    provider = PdfInputProvider(tmp_dir=tmp_path)
    payload = provider.load(sample_pdf)
    for unit in payload.units:
        assert isinstance(unit, ImageUnit)
        p = unit.path
        assert p.exists(), f"Page image does not exist: {p}"


def test_pdf_provider_pages_are_png(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  a PdfInputProvider and a single-page PDF
    When   load() is called
    Then   every returned page path has a .png extension
    """
    provider = PdfInputProvider(tmp_dir=tmp_path)
    payload = provider.load(sample_pdf)
    for unit in payload.units:
        assert isinstance(unit, ImageUnit)
        p = unit.path
        assert p.suffix.lower() == ".png", f"Expected PNG, got: {p.suffix}"


def test_pdf_provider_dpi_scales_rendered_resolution(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  two PdfInputProviders rendering the same PDF at 72 and 300 DPI
    When   load() is called on each
    Then   the higher-DPI page is rendered larger (scale == dpi / 72)
    """
    from PIL import Image  # noqa: PLC0415

    low_dir = tmp_path / "low"
    high_dir = tmp_path / "high"
    low_dir.mkdir()
    high_dir.mkdir()

    low = PdfInputProvider(tmp_dir=low_dir, pdf_dpi=72).load(sample_pdf)
    high = PdfInputProvider(tmp_dir=high_dir, pdf_dpi=300).load(sample_pdf)

    lo_unit, hi_unit = low.units[0], high.units[0]
    assert isinstance(lo_unit, ImageUnit)
    assert isinstance(hi_unit, ImageUnit)
    lo_path, hi_path = lo_unit.path, hi_unit.path
    with Image.open(lo_path) as lo, Image.open(hi_path) as hi:
        assert hi.size[0] > lo.size[0]
        assert hi.size[1] > lo.size[1]


def test_a_page_bigger_than_the_pixel_cap_is_rendered_smaller(
    tmp_path: Path, sample_pdf: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a page that the pixel cap cannot hold at the chosen resolution
    When   the page is rendered
    Then   the image is scaled down to the cap instead of the render failing or filling memory
    """
    from PIL import Image  # noqa: PLC0415

    from teachers_teammate.infrastructure import input_providers  # noqa: PLC0415

    monkeypatch.setattr(input_providers, "_MAX_PAGE_PIXELS", 10_000)

    payload = PdfInputProvider(tmp_dir=tmp_path, pdf_dpi=300).load(sample_pdf)
    unit = payload.units[0]
    assert isinstance(unit, ImageUnit)
    with Image.open(unit.path) as image:
        # The scale gives the cap; rounding to whole pixels may add a row or a column.
        assert image.size[0] * image.size[1] <= 11_000


def test_text_provider_loads_text_content(tmp_path: Path) -> None:
    """
    Given  a TextInputProvider and a UTF-8 .txt file
    When   load() is called
    Then   one text unit with the file content is returned
    """
    file_path = tmp_path / "note.txt"
    file_path.write_text("alpha\nbeta", encoding="utf-8")

    provider = TextInputProvider()
    payload = provider.load(file_path)

    assert payload.source_image is None
    assert len(payload.units) == 1
    unit = payload.units[0]
    assert isinstance(unit, TextUnit)
    assert unit.text == "alpha\nbeta"


@pytest.mark.parametrize("ending", ["\r\n", "\r"])
def test_text_provider_keeps_the_line_breaks_of_every_platform(tmp_path: Path, ending: str) -> None:
    """
    Given  a .txt file with Windows or classic Mac line endings
    When   load() is called
    Then   every line ending is a newline and no line runs into the next
    """
    file_path = tmp_path / "note.txt"
    file_path.write_bytes(f"one{ending}two{ending}{ending}three".encode())

    payload = TextInputProvider().load(file_path)

    assert payload.units[0].text == "one\ntwo\n\nthree"


def test_text_provider_keeps_the_joiners_and_direction_marks_of_the_writer(tmp_path: Path) -> None:
    """
    Given  a .txt file with an emoji joiner, a Persian half-space and a right-to-left mark,
           and a control code
    When   load() is called
    Then   the joiners and marks stay and only the control code goes
    """
    written = (
        "\U0001f468\u200d\U0001f469 \u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645 \u200fabc"
    )
    file_path = tmp_path / "note.txt"
    file_path.write_text(written + "\x0c", encoding="utf-8")

    payload = TextInputProvider().load(file_path)

    assert payload.units[0].text == written


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"\xef\xbb\xbfHallo Welt", "Hallo Welt"),
        ("Grüße".encode(), "Grüße"),
        ("\u201eHallo\u201c \u2013 5 \u20ac".encode("cp1252"), "\u201eHallo\u201c \u2013 5 \u20ac"),
        ("Hallo Welt".encode("utf-16"), "Hallo Welt"),
    ],
)
def test_text_provider_reads_the_encoding_the_file_has(
    tmp_path: Path, data: bytes, expected: str
) -> None:
    """
    Given  a .txt file with a BOM, in UTF-8 and in cp1252 with its quotes, dash and euro sign
    When   load() is called
    Then   the text it holds comes back, without the BOM and without control codes
    """
    file_path = tmp_path / "note.txt"
    file_path.write_bytes(data)

    payload = TextInputProvider().load(file_path)

    assert payload.units[0].text == expected


def test_text_provider_drops_what_no_document_can_hold(tmp_path: Path) -> None:
    """
    Given  a .txt file from pdftotext, which leaves a form feed between its pages
    When   load() is called
    Then   the text comes back without the form feed and keeps its line breaks
    """
    file_path = tmp_path / "note.txt"
    file_path.write_bytes(b"Seite eins\n\x0cSeite zwei")

    payload = TextInputProvider().load(file_path)

    assert payload.units[0].text == "Seite eins\nSeite zwei"


# ── Embedded PDF text layer ────────────────────────────────────────────────

_LONG_TEXT = "The quick brown fox jumps over the lazy dog while the teacher grades essays."


def test_pdf_provider_uses_embedded_text_for_text_only_pdf(tmp_path: Path) -> None:
    """
    Given  a text-only PDF and a PdfInputProvider in auto mode
    When   load() is called
    Then   a single text unit with the embedded text is returned and every page is rendered
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT, _LONG_TEXT.upper()])
    render_dir = tmp_path / "renders"
    render_dir.mkdir()

    payload = PdfInputProvider(tmp_dir=render_dir, text_layer="auto").load(pdf)

    assert all(isinstance(unit, TextUnit) for unit in payload.units)
    text = payload.units[0].text or ""
    assert text.startswith(f"Page 1:\n{_LONG_TEXT}")
    assert f"Page 2:\n{_LONG_TEXT.upper()}" in text
    assert payload.source_image is not None and payload.source_image.exists()
    assert [p.name for p in sorted(render_dir.iterdir())] == [
        "typed_page0.png",
        "typed_page1.png",
    ]
    assert payload.page_images == (render_dir / "typed_page0.png", render_dir / "typed_page1.png")


def test_pdf_provider_text_layer_renders_every_page_for_the_scan(tmp_path: Path) -> None:
    """
    Given  a three-page text-only PDF
    When   load() is called in auto mode
    Then   all three pages are rendered, in page order, and the first is the source image
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT] * 3)

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert [path.name for path in payload.page_images] == [
        "typed_page0.png",
        "typed_page1.png",
        "typed_page2.png",
    ]
    assert all(path.exists() for path in payload.page_images)
    assert payload.source_image == payload.page_images[0]


def test_pdf_provider_text_layer_renders_the_scan_at_the_ocr_resolution(
    tmp_path: Path,
) -> None:
    """
    Given  a text-only PDF and a provider that renders pages for OCR at 300 DPI
    When   load() is called in auto mode
    Then   the pages of the scan are rendered at the same resolution, so zooming into the scan
           shows the page and not an enlarged copy of it
    """
    from PIL import Image  # noqa: PLC0415

    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT])
    scan = PdfInputProvider(tmp_dir=tmp_path / "a", text_layer="auto", pdf_dpi=300)
    ocr = PdfInputProvider(tmp_dir=tmp_path / "b", text_layer="never", pdf_dpi=300)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    scan_page = scan.load(pdf).page_images[0]
    ocr_unit = ocr.load(pdf).units[0]
    assert isinstance(ocr_unit, ImageUnit)
    ocr_page = ocr_unit.path

    with Image.open(scan_page) as shown, Image.open(ocr_page) as read:
        assert shown.width == read.width
        assert shown.height == read.height


def test_pdf_provider_text_layer_keeps_the_pages_rendered_before_a_broken_one(
    tmp_path: Path,
) -> None:
    """
    Given  a three-page text-only PDF whose second page cannot be rendered
    When   load() is called in auto mode
    Then   the pages rendered so far are kept and the text is still returned
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT] * 3)
    provider = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto")
    render_page = provider._render_page  # the failing page is the point

    def fail_on_page_1(pdf_doc: Any, page_num: int, file_path: Path, **kwargs: Any) -> Path:
        if page_num == 1:
            raise RuntimeError("page 2 is broken")
        return render_page(pdf_doc, page_num, file_path, **kwargs)

    with patch.object(provider, "_render_page", side_effect=fail_on_page_1):
        payload = provider.load(pdf)

    assert [path.name for path in payload.page_images] == ["typed_page0.png", "typed_page2.png"]
    assert payload.source_image == payload.page_images[0]
    assert isinstance(payload.units[0], TextUnit)


def test_pdf_provider_single_page_text_has_no_page_label(tmp_path: Path) -> None:
    """
    Given  a single-page text-only PDF
    When   load() is called in auto mode
    Then   the text is returned without a "Page N:" label (matching single-page OCR output)
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT])

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert payload.units[0].text == _LONG_TEXT


def test_pdf_provider_never_mode_renders_text_pdf(tmp_path: Path) -> None:
    """
    Given  a text-only PDF and text_layer="never"
    When   load() is called
    Then   every page is rendered to an image unit and no text unit is returned
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT, _LONG_TEXT])

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="never").load(pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)
    assert len(payload.units) == 2
    assert payload.text_layer_note == "text layer ignored (mode: never)"


def test_pdf_provider_text_layer_used_has_no_fallback_note(tmp_path: Path) -> None:
    """
    Given  a text-only PDF
    When   load() is called in auto mode
    Then   the embedded text is used and no fallback note is set
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT])

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert payload.text_origin == "pdf_text_layer"
    assert payload.text_layer_note is None


def test_pdf_provider_auto_mode_renders_scanned_pdf(tmp_path: Path, sample_pdf: Path) -> None:
    """
    Given  an image-only (scanned) PDF without a text layer
    When   load() is called in auto mode
    Then   the page is rendered to an image unit for OCR
    """
    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(sample_pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)
    assert payload.text_layer_note == "page 1: no embedded text"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"full_page_image": True}, id="scan_with_text_layer"),
        pytest.param({"annotation": "Ink"}, id="handwriting_annotation"),
        pytest.param({"annotation": "FreeText"}, id="text_box_annotation"),
        pytest.param({"annotation": "Widget"}, id="filled_form_field"),
        pytest.param({"scribbles": 12}, id="vector_pen_strokes"),
        pytest.param({"scribbles": 1}, id="single_pen_stroke"),
        pytest.param({"image_size": (200, 150)}, id="pasted_answer_photo"),
        pytest.param({"image_in_form": True}, id="scan_inside_scaled_form"),
        pytest.param(
            {"full_page_image": True, "media_box": (100, 100, 712, 892)}, id="scan_on_cropped_page"
        ),
    ],
)
def test_pdf_provider_auto_mode_distrusts_text_next_to_handwriting(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    """
    Given  a PDF with text but also a full-page image or an ink annotation
    When   load() is called in auto mode
    Then   the pages are rendered for OCR instead of dropping the non-text content
    """
    pdf = write_text_pdf(tmp_path / "mixed.pdf", [_LONG_TEXT], **kwargs)

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)


def test_pdf_provider_always_mode_trusts_text_over_scan(tmp_path: Path) -> None:
    """
    Given  a PDF with a text layer on top of a full-page image
    When   load() is called with text_layer="always"
    Then   the embedded text is returned without OCR
    """
    pdf = write_text_pdf(tmp_path / "scan.pdf", [_LONG_TEXT], full_page_image=True)

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="always").load(pdf)

    assert all(isinstance(unit, TextUnit) for unit in payload.units)


def test_pdf_provider_auto_mode_rejects_nearly_empty_text(tmp_path: Path) -> None:
    """
    Given  a PDF whose text layer holds only a few characters (e.g. a page number)
    When   load() is called in auto mode
    Then   the page is rendered for OCR
    """
    pdf = write_text_pdf(tmp_path / "sparse.pdf", ["3"])

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"annotation": "Highlight"}, id="highlight_markup"),
        pytest.param({"ruled_lines": 30}, id="ruled_lines"),
        pytest.param({"image_size": (40, 30)}, id="small_logo"),
        pytest.param({"media_box": (100, 100, 712, 892)}, id="cropped_page_without_image"),
    ],
)
def test_pdf_provider_auto_mode_keeps_text_for_harmless_extras(
    tmp_path: Path, kwargs: dict[str, Any]
) -> None:
    """
    Given  a text PDF whose only extras are markup-only annotations, axis-aligned lines or
           a small logo
    When   load() is called in auto mode
    Then   the embedded text is still used (these do not hide student content)
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT], **kwargs)

    payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert all(isinstance(unit, TextUnit) for unit in payload.units)


def test_pdf_provider_auto_mode_stops_measuring_at_first_scanned_page(tmp_path: Path) -> None:
    """
    Given  a multi-page scanned PDF with a text layer
    When   load() is called in auto mode
    Then   only the first page is measured before the PDF is rendered for OCR
    """
    pdf = write_text_pdf(tmp_path / "scan.pdf", [_LONG_TEXT] * 4, full_page_image=True)

    with patch(
        "teachers_teammate.infrastructure.input_providers._image_coverage", return_value=1.0
    ) as coverage:
        payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="auto").load(pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)
    assert len(payload.units) == 4
    assert coverage.call_count == 1


class _FakeTextPage:
    def __init__(self, text: str) -> None:
        self._text = text

    def get_text_range(self) -> str:
        return self._text

    def close(self) -> None:
        pass


@pytest.mark.parametrize("mode", ["auto", "always"])
def test_pdf_provider_strips_control_characters_from_embedded_text(
    tmp_path: Path, mode: Any
) -> None:
    """
    Given  a PDF whose text layer contains control codes and U+FFFE
    When   load() is called in auto or always mode
    Then   the returned text holds only printable characters and line breaks
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT])
    dirty = f"{_LONG_TEXT[:20]}\x02\x0b{_LONG_TEXT[20:]}\ufffe\nsecond line"

    with patch("pypdfium2.PdfPage.get_textpage", return_value=_FakeTextPage(dirty)):
        payload = PdfInputProvider(tmp_dir=tmp_path, text_layer=mode).load(pdf)

    assert all(isinstance(unit, TextUnit) for unit in payload.units)
    assert payload.units[0].text == f"{_LONG_TEXT}\nsecond line"


def test_pdf_provider_always_mode_renders_page_without_printable_text(tmp_path: Path) -> None:
    """
    Given  a PDF whose text layer consists only of control characters
    When   load() is called with text_layer="always"
    Then   the page is rendered for OCR instead of returning an empty text unit
    """
    pdf = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT])

    with patch("pypdfium2.PdfPage.get_textpage", return_value=_FakeTextPage("\x02\x03\ufffe")):
        payload = PdfInputProvider(tmp_dir=tmp_path, text_layer="always").load(pdf)

    assert all(isinstance(unit, ImageUnit) for unit in payload.units)


# ── pdf_has_text_layer: the decision without rendering ─────────────────────


def test_pdf_has_text_layer_agrees_with_the_provider_without_rendering(
    tmp_path: Path, sample_pdf: Path
) -> None:
    """
    Given  a text-only PDF, a scanned PDF, and a render directory that must stay empty
    When   pdf_has_text_layer asks about both in auto mode, and about the text PDF in "never"
    Then   only the typed PDF has a usable layer, "never" refuses it, and nothing was rendered
    """
    typed = write_text_pdf(tmp_path / "typed.pdf", [_LONG_TEXT, _LONG_TEXT])

    assert pdf_has_text_layer(typed, "auto") is True
    assert pdf_has_text_layer(sample_pdf, "auto") is False
    assert pdf_has_text_layer(typed, "never") is False
    assert list(tmp_path.glob("*_page*.png")) == []
