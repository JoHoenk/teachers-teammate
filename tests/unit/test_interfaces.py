"""Unit tests for teachers_teammate.interfaces."""

from __future__ import annotations

from teachers_teammate.interfaces import SUPPORTED_SUFFIXES


def test_supported_suffixes_contains_all_accepted_extensions() -> None:
    """
    Given  the SUPPORTED_SUFFIXES constant defined in interfaces (the canonical source)
    When   its membership is checked
    Then   all accepted input extensions (.pdf, .png, .jpg, .jpeg, .txt) are present
    """
    assert {".pdf", ".png", ".jpg", ".jpeg", ".txt"} <= SUPPORTED_SUFFIXES
