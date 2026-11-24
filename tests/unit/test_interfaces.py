"""Unit tests for teachers_teammate.interfaces."""

from __future__ import annotations

from teachers_teammate.interfaces import INPUT_KINDS, SUPPORTED_SUFFIXES


def test_supported_suffixes_is_the_table_of_what_reads_each_one() -> None:
    """
    Given  the table naming what reads each input suffix
    When   the offered suffixes are read
    Then   they are exactly its keys, so a new format is one line in one place
    """
    assert SUPPORTED_SUFFIXES == frozenset(INPUT_KINDS)


def test_supported_suffixes_contains_all_accepted_extensions() -> None:
    """
    Given  the SUPPORTED_SUFFIXES constant defined in interfaces (the canonical source)
    When   its membership is checked
    Then   all accepted input extensions (.pdf, .png, .jpg, .jpeg, .txt) are present
    """
    assert {".pdf", ".png", ".jpg", ".jpeg", ".txt"} <= SUPPORTED_SUFFIXES
