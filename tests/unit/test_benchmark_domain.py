"""Unit tests for the pure benchmark comparison metrics."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.benchmark import (
    compare_pair,
    normalize_for_compare,
    similarity,
    text_stats,
)


def test_normalize_collapses_whitespace_and_lowercases() -> None:
    """
    Given  text with mixed case, newlines, and repeated spaces
    When   normalize_for_compare() is called
    Then   whitespace is collapsed to single spaces and the text is lowercased
    """
    assert normalize_for_compare("Hello   WORLD\n\n foo\t bar ") == "hello world foo bar"


def test_similarity_identical_text_is_one() -> None:
    """
    Given  two texts that differ only by casing and whitespace
    When   similarity() is computed
    Then   it returns 1.0 (normalisation removes the cosmetic differences)
    """
    assert similarity("The Quick Brown Fox", "the   quick brown fox") == 1.0


def test_similarity_two_empty_texts_is_one() -> None:
    """
    Given  two empty strings
    When   similarity() is computed
    Then   they are defined as identical (1.0) rather than undefined
    """
    assert similarity("", "") == 1.0


def test_similarity_is_symmetric_and_bounded() -> None:
    """
    Given  two different texts
    When   similarity() is computed both ways
    Then   the result is symmetric and within [0.0, 1.0]
    """
    first, second = "hello world", "hello there world"
    forward = similarity(first, second)
    backward = similarity(second, first)
    assert forward == backward
    assert 0.0 < forward < 1.0


def test_similarity_disjoint_text_is_low() -> None:
    """
    Given  two texts with no shared content
    When   similarity() is computed
    Then   the score is well below the midpoint
    """
    assert similarity("aaaa", "zzzz") < 0.3


def test_text_stats_counts_chars_words_and_nonempty_lines() -> None:
    """
    Given  multi-line text with a blank line
    When   text_stats() is called
    Then   chars/words counts include all content and lines counts only non-empty lines
    """
    stats = text_stats("foo bar\n\nbaz")
    assert stats.chars == len("foo bar\n\nbaz")
    assert stats.words == 3
    assert stats.lines == 2


@pytest.mark.use_case("OCR_Run_Comparison")
def test_compare_pair_bundles_similarity_and_stats() -> None:
    """
    Given  two OCR outputs
    When   compare_pair() is called
    Then   it returns the similarity plus per-output text statistics
    """
    result = compare_pair("hello world", "hello world!")
    assert 0.0 < result.similarity <= 1.0
    assert result.stats_a.words == 2
    assert result.stats_b.words == 2
