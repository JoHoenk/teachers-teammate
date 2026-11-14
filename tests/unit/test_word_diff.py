"""Unit tests for the one word diff of the app (``domain/proofreading/word_diff.py``)."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.correction_review import align, CorrectionReview
from teachers_teammate.domain.proofreading.word_diff import (
    Hunk,
    Similarity,
    diff_opcodes,
    similarity,
    tokenize,
    word_diff,
)


def test_word_diff_reports_one_hunk_per_change() -> None:
    """
    Given  a text with two separate mistakes
    When   the word diff is taken
    Then   each mistake is one hunk with its span in both texts
    """
    hunks = word_diff("Ich habe ein grosse Hund.", "Ich habe einen großen Hund.")

    assert [hunk.kind for hunk in hunks] == ["replace", "replace"]
    assert hunks[0].original == "ein"
    assert hunks[0].corrected == "einen"
    assert hunks[1].original == "grosse"
    assert hunks[1].corrected == "großen"


def test_word_diff_spans_point_at_the_words() -> None:
    """
    Given  a text whose second word is corrected
    When   the word diff is taken
    Then   the raw span covers exactly the old word and the corrected span the new one
    """
    raw, corrected = "Helo world.", "Hello world."

    hunks = word_diff(raw, corrected)
    assert len(hunks) == 1
    hunk = hunks[0]

    assert raw[hunk.raw_start : hunk.raw_end] == "Helo"
    assert corrected[hunk.corrected_start : hunk.corrected_end] == "Hello"


def test_word_diff_counts_a_layout_change_as_equal() -> None:
    """
    Given  a text whose only difference is a line break the model joined into a space
    When   the word diff is taken
    Then   there is no hunk, because a teacher cannot see that change
    """
    assert word_diff("zu Hause.\nWeil es regnete.", "zu Hause. Weil es regnete.") == ()


def test_word_diff_reports_an_insertion_with_an_empty_raw_span() -> None:
    """
    Given  a text where the model inserted a word
    When   the word diff is taken
    Then   the hunk is an insertion whose raw span is empty
    """
    hunks = word_diff("Ich habe gewartet.", "Ich habe lange gewartet.")
    assert len(hunks) == 1
    hunk = hunks[0]

    assert hunk.kind == "insert"
    assert hunk.raw_start == hunk.raw_end
    assert hunk.corrected == "lange"
    assert "Ich habe lange gewartet."[hunk.corrected_start : hunk.corrected_end] == "lange"


def test_word_diff_reports_a_deletion_with_an_empty_corrected_span() -> None:
    """
    Given  a text where the model deleted a word
    When   the word diff is taken
    Then   the hunk is a deletion whose corrected span is empty
    """
    hunks = word_diff("Ich habe sehr lange gewartet.", "Ich habe lange gewartet.")
    assert len(hunks) == 1
    hunk = hunks[0]

    assert hunk.kind == "delete"
    assert hunk.corrected_start == hunk.corrected_end
    assert hunk.original == "sehr"
    assert "Ich habe sehr lange gewartet."[hunk.raw_start : hunk.raw_end] == "sehr"


def test_word_diff_trims_layout_whitespace_off_a_hunk() -> None:
    """
    Given  a text where the model joined two sentences and changed the first word of the second
    When   the word diff is taken
    Then   the hunk starts at the changed word, not in the gap between the sentences
    """
    raw = "Wir blieben zu Hause.\nWeil es regnete."
    corrected = "Wir blieben zu Hause. Da es regnete."

    hunks = word_diff(raw, corrected)
    assert len(hunks) == 1
    hunk = hunks[0]

    assert hunk.original == "Weil"
    assert hunk.corrected == "Da"
    assert raw[hunk.raw_start : hunk.raw_end] == "Weil"


def test_align_numbers_the_same_hunks_as_the_word_diff() -> None:
    """
    Given  a text with two mistakes and a review without notes
    When   the review is aligned with the text
    Then   the aligned changes are the hunks of the word diff, in the same order and spans
    """
    raw, corrected = "Ich habe ein grosse Hund.", "Ich habe einen großen Hund."

    aligned = align(raw, corrected, CorrectionReview())

    assert [change.hunk for change in aligned.changes] == list(word_diff(raw, corrected))
    assert [change.number for change in aligned.changes] == [1, 2]


def test_diff_opcodes_keeps_the_layout_rule_for_text_tokens_only() -> None:
    """
    Given  two sequences of tuples (a caller that diffs lines, not words)
    When   the opcodes are taken
    Then   a difference is reported as a difference, because tuples have no layout
    """
    opcodes = diff_opcodes([("a", 1)], [("b", 1)])

    assert [tag for tag, *_ in opcodes] == ["replace"]


def test_tokenize_keeps_whitespace_and_punctuation() -> None:
    """
    Given  a sentence with a comma
    When   it is tokenised
    Then   the words, the comma and the spaces are separate tokens
    """
    assert tokenize("Hallo, Welt") == ["Hallo", ",", " ", "Welt"]


def test_similarity_is_one_for_equal_texts_and_low_for_unrelated_ones() -> None:
    """
    Given  a sentence, a copy of it and a placeholder
    When   the similarity is measured
    Then   the copy is 1.0 and the placeholder is far below half
    """
    assert similarity("Ich habe gewartet.", "Ich habe gewartet.") == 1.0
    assert similarity("Ich habe gewartet.", "corrected") < 0.5


def test_similarity_skips_candidates_below_the_threshold() -> None:
    """
    Given  a target and a candidate that cannot reach the threshold
    When   the ratio is asked for with that threshold
    Then   it is 0.0, so the fuzzy anchor does not compute the expensive comparison
    """
    matcher = Similarity("Ich habe lange gewartet.")

    assert matcher.ratio("voellig anders", at_least=0.9) == 0.0
    assert matcher.ratio("Ich habe lange gewartet.", at_least=0.9) == 1.0


@pytest.mark.parametrize(
    ("raw", "corrected"),
    [
        ("", ""),
        ("Ein Satz.", "Ein Satz."),
        ("Ein Satz.", ""),
        ("", "Ein Satz."),
    ],
)
def test_word_diff_handles_empty_and_equal_texts(raw: str, corrected: str) -> None:
    """
    Given  two texts that are equal, or one of them empty
    When   the word diff is taken
    Then   there is no hunk when they are equal, and one hunk otherwise
    """
    hunks = word_diff(raw, corrected)

    assert bool(hunks) is (raw != corrected)


def test_a_hunk_summary_names_an_empty_side() -> None:
    """
    Given  an insertion, a deletion and a replacement
    When   their summaries are read
    Then   the empty side is named instead of shown as nothing
    """
    assert Hunk(0, 0, 0, 5, "", "lange", "insert").summary == "(inserted) → lange"
    assert Hunk(0, 5, 0, 0, "sehr ", "", "delete").summary == "sehr → (deleted)"
    assert Hunk(0, 3, 0, 4, "ein", "einen", "replace").summary == "ein → einen"
