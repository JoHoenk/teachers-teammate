"""Provider resolver/factory for input ingestion.

The pipeline uses this module as the single source of truth for:
- supported input suffixes
- provider class selection by suffix
- provider construction details
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..domain.pdf_text_layer import PdfTextLayerMode
from ..exceptions import PipelineInputError
from ..interfaces import SUPPORTED_SUFFIXES, InputProvider

if TYPE_CHECKING:
    from ..config import OcrConfig


@dataclass(frozen=True)
class InputOptions:
    """Settings that influence how input files are loaded.

    Every builder receives the whole object and picks what it needs, so a new
    format-specific option does not change any builder signature.
    """

    pdf_dpi: int = 300
    """Render resolution for PDF pages."""
    pdf_text_layer: PdfTextLayerMode = "auto"
    """Whether a PDF's embedded text may replace OCR."""

    @classmethod
    def from_ocr(cls, ocr: OcrConfig) -> InputOptions:
        """Return the input options selected by an :class:`~teachers_teammate.config.OcrConfig`."""
        return cls(pdf_dpi=ocr.pdf_render_dpi, pdf_text_layer=ocr.pdf_text_layer)


_ProviderBuilder = Callable[[Path, InputOptions], InputProvider]
_FingerprintBuilder = Callable[[InputOptions], tuple[str, ...]]


def _build_pdf(tmp_dir: Path, options: InputOptions) -> InputProvider:
    from .input_providers import PdfInputProvider  # noqa: PLC0415

    return PdfInputProvider(
        tmp_dir=tmp_dir, pdf_dpi=options.pdf_dpi, text_layer=options.pdf_text_layer
    )


def _build_image(_tmp_dir: Path, _options: InputOptions) -> InputProvider:
    from .input_providers import ImageInputProvider  # noqa: PLC0415

    return ImageInputProvider()


def _build_text(_tmp_dir: Path, _options: InputOptions) -> InputProvider:
    from .input_providers import TextInputProvider  # noqa: PLC0415

    return TextInputProvider()


def _pdf_fingerprint(options: InputOptions) -> tuple[str, ...]:
    # The default ("auto") adds nothing, so caches written before the setting existed stay
    # valid; only an explicit "always"/"never" changes where a PDF's text comes from.
    if options.pdf_text_layer == "auto":
        return ()
    return (f"pdf_text_layer={options.pdf_text_layer}",)


_SUFFIX_TO_BUILDER: dict[str, _ProviderBuilder] = {
    ".pdf": _build_pdf,
    ".png": _build_image,
    ".jpg": _build_image,
    ".jpeg": _build_image,
    ".txt": _build_text,
}

# Suffixes whose loaded content depends on an input option beyond the page images' DPI.
_SUFFIX_TO_FINGERPRINT: dict[str, _FingerprintBuilder] = {".pdf": _pdf_fingerprint}

assert frozenset(_SUFFIX_TO_BUILDER.keys()) == SUPPORTED_SUFFIXES, (
    f"_SUFFIX_TO_BUILDER keys {set(_SUFFIX_TO_BUILDER)} do not match "
    f"SUPPORTED_SUFFIXES {SUPPORTED_SUFFIXES} — update both together"
)


def supported_suffixes() -> frozenset[str]:
    """Return all input suffixes handled by registered providers."""
    return SUPPORTED_SUFFIXES


def input_settings_fingerprint(suffix: str, options: InputOptions) -> tuple[str, ...]:
    """Return the *options* that change what is loaded from a file with *suffix*.

    Format-specific settings only appear for the formats they affect, so changing a PDF
    option does not invalidate cached results of images or text files.  Options at their
    default contribute nothing, which keeps older cache entries valid.
    """
    fingerprint = _SUFFIX_TO_FINGERPRINT.get(suffix.lower())
    return fingerprint(options) if fingerprint is not None else ()


def get_input_provider(
    suffix: str,
    *,
    tmp_dir: Path,
    options: InputOptions | None = None,
) -> InputProvider:
    """Resolve and construct an :class:`InputProvider` for *suffix*."""
    normalized = suffix.lower()
    builder = _SUFFIX_TO_BUILDER.get(normalized)
    if builder is None:
        supported = ", ".join(sorted(_SUFFIX_TO_BUILDER))
        raise PipelineInputError(f"Unsupported input suffix '{suffix}'. Supported: {supported}")
    return builder(tmp_dir, options or InputOptions())
