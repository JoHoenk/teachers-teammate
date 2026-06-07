"""Unit tests for teachers_teammate.infrastructure.input_provider_factory."""

from __future__ import annotations

from pathlib import Path

import pytest

from teachers_teammate.infrastructure.input_provider_factory import (
    get_input_provider,
    supported_suffixes,
)
from teachers_teammate.infrastructure.input_providers import (
    ImageInputProvider,
    PdfInputProvider,
    TextInputProvider,
)
from teachers_teammate.interfaces import SUPPORTED_SUFFIXES


# ── supported_suffixes ─────────────────────────────────────────────────────


def test_supported_suffixes_matches_interfaces_constant() -> None:
    """
    Given  the factory module and the interfaces module
    When   supported_suffixes() is called
    Then   it returns the same frozenset as SUPPORTED_SUFFIXES from teachers_teammate.interfaces
           (the canonical source — membership content is asserted in test_interfaces.py)
    """
    assert supported_suffixes() == SUPPORTED_SUFFIXES


# ── get_input_provider dispatch ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("suffix", "expected_type"),
    [
        (".pdf", PdfInputProvider),
        (".png", ImageInputProvider),
        (".jpg", ImageInputProvider),
        (".jpeg", ImageInputProvider),
        (".txt", TextInputProvider),
        # Case-insensitive: suffix is normalised to lowercase before dispatch.
        (".PDF", PdfInputProvider),
        (".PNG", ImageInputProvider),
    ],
)
def test_get_input_provider_dispatches_by_suffix(
    suffix: str, expected_type: type, tmp_path: Path
) -> None:
    """
    Given  a supported file suffix (any case)
    When   get_input_provider is called
    Then   an instance of the matching provider class is returned
    """
    provider = get_input_provider(suffix, tmp_dir=tmp_path)
    assert isinstance(provider, expected_type)


# ── Error path ─────────────────────────────────────────────────────────────


def test_get_input_provider_unsupported_suffix_raises_value_error(tmp_path: Path) -> None:
    """
    Given  an unsupported suffix '.xyz'
    When   get_input_provider is called
    Then   ValueError is raised and the message mentions the unsupported suffix
    """
    with pytest.raises(ValueError, match=r"\.xyz"):
        get_input_provider(".xyz", tmp_dir=tmp_path)


def test_get_input_provider_error_message_lists_supported_suffixes(tmp_path: Path) -> None:
    """
    Given  an unsupported suffix
    When   get_input_provider raises ValueError
    Then   the error message lists '.pdf' as a supported suffix
    """
    with pytest.raises(ValueError, match=r"\.pdf"):
        get_input_provider(".docx", tmp_dir=tmp_path)
