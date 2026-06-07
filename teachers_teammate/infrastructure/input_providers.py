"""Input providers: load source files into raw page images.

PDF pages are rendered via pypdfium2; image files (JPG, PNG …) are passed through
as-is.  All temporary renders are written to *tmp_dir*; the caller is responsible
for cleanup.
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium

from ..interfaces import InputPayload, InputProvider, InputUnit


class PdfInputProvider(InputProvider):
    """Renders each page of a PDF to a PNG image.

    All page renders are written to *tmp_dir*.  The first page path is also
    returned as the source preview image for the output document.
    """

    def __init__(self, tmp_dir: Path) -> None:
        self._tmp_dir = tmp_dir

    def load(self, file_path: Path) -> InputPayload:
        """Render every page of *file_path* to PNG and return image units.

        Args:
            file_path: Path to the PDF file.

        Returns:
            :class:`InputPayload` with image units for all rendered pages and
            the first page as preview source image, or an empty payload for an
            empty PDF.
        """
        pdf = pdfium.PdfDocument(str(file_path))
        units: list[InputUnit] = []
        source_image: Path | None = None
        try:
            for page_num in range(len(pdf)):
                page = pdf[page_num]
                bitmap = page.render(scale=1.0)
                image = bitmap.to_pil()
                raw_path = self._tmp_dir / f"{file_path.stem}_page{page_num}.png"
                image.save(str(raw_path))
                units.append(InputUnit.image(raw_path))
                if page_num == 0:
                    source_image = raw_path
        finally:
            pdf.close()
        return InputPayload(units=units, source_image=source_image)


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
        return InputPayload(units=[InputUnit.text_content(text)], source_image=None)
