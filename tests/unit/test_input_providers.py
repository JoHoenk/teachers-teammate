"""Unit tests for teachers_teammate.input_providers."""

from __future__ import annotations

from pathlib import Path

from teachers_teammate.infrastructure.input_providers import (
    ImageInputProvider,
    PdfInputProvider,
    TextInputProvider,
)


def test_image_provider_list_has_one_entry(sample_png: Path) -> None:
    """
    Given  an ImageInputProvider and a single PNG file path
    When   load() is called
    Then   exactly one page path is returned and it equals the input file
    """
    provider = ImageInputProvider()
    payload = provider.load(sample_png)
    assert len(payload.units) == 1
    assert payload.units[0].kind == "image"
    assert payload.units[0].image_path == sample_png
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
        p = unit.image_path
        assert unit.kind == "image"
        assert p is not None
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
        p = unit.image_path
        assert unit.kind == "image"
        assert p is not None
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

    lo_path = low.units[0].image_path
    hi_path = high.units[0].image_path
    assert lo_path is not None
    assert hi_path is not None
    with Image.open(lo_path) as lo, Image.open(hi_path) as hi:
        assert hi.size[0] > lo.size[0]
        assert hi.size[1] > lo.size[1]


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
    assert payload.units[0].kind == "text"
    assert payload.units[0].text == "alpha\nbeta"
