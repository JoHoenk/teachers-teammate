"""Provider resolver/factory for input ingestion.

The pipeline uses this module as the single source of truth for:
- supported input suffixes
- provider class selection by suffix
- provider construction details
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..exceptions import PipelineInputError
from ..interfaces import SUPPORTED_SUFFIXES, InputProvider

_ProviderBuilder = Callable[[Path], InputProvider]


def _build_pdf(tmp_dir: Path) -> InputProvider:
    from .input_providers import PdfInputProvider  # noqa: PLC0415

    return PdfInputProvider(tmp_dir=tmp_dir)


def _build_image(_tmp_dir: Path) -> InputProvider:
    from .input_providers import ImageInputProvider  # noqa: PLC0415

    return ImageInputProvider()


def _build_text(_tmp_dir: Path) -> InputProvider:
    from .input_providers import TextInputProvider  # noqa: PLC0415

    return TextInputProvider()


_SUFFIX_TO_BUILDER: dict[str, _ProviderBuilder] = {
    ".pdf": _build_pdf,
    ".png": _build_image,
    ".jpg": _build_image,
    ".jpeg": _build_image,
    ".txt": _build_text,
}

assert frozenset(_SUFFIX_TO_BUILDER.keys()) == SUPPORTED_SUFFIXES, (
    f"_SUFFIX_TO_BUILDER keys {set(_SUFFIX_TO_BUILDER)} do not match "
    f"SUPPORTED_SUFFIXES {SUPPORTED_SUFFIXES} — update both together"
)


def supported_suffixes() -> frozenset[str]:
    """Return all input suffixes handled by registered providers."""
    return SUPPORTED_SUFFIXES


def get_input_provider(suffix: str, *, tmp_dir: Path) -> InputProvider:
    """Resolve and construct an :class:`InputProvider` for *suffix*."""
    normalized = suffix.lower()
    builder = _SUFFIX_TO_BUILDER.get(normalized)
    if builder is None:
        supported = ", ".join(sorted(_SUFFIX_TO_BUILDER))
        raise PipelineInputError(f"Unsupported input suffix '{suffix}'. Supported: {supported}")
    return builder(tmp_dir)
