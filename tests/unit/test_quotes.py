"""Unit tests for the quote lookup (``domain/proofreading/quotes.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.quotes import find_spans, map_back
from teachers_teammate.domain.proofreading.word_diff import word_diff


def test_an_exact_quote_is_found() -> None:
    """
    Given  a text and words it contains
    When   the quote is looked up
    Then   its span is the exact position in the text
    """
    text = "Ich ging nach Hause. Er war dort."

    assert find_spans(text, ["Er war dort."]) == [(21, 33)]


def test_a_quote_with_other_spacing_is_found() -> None:
    """
    Given  a text whose words are separated by a line break
    When   the quote is looked up with a single space
    Then   the span covers the words in the text, line break included
    """
    text = "Ich ging\nnach Hause."

    assert find_spans(text, ["ging nach"]) == [(4, 13)]


def test_a_quote_inside_a_word_is_not_found() -> None:
    """
    Given  a text that contains the quote only as part of a longer word
    When   the quote is looked up
    Then   it is not found
    """
    text = "Er ist ohne mich gegangen."

    assert find_spans(text, ["ohne mich gegangener"]) == [None]


def test_two_quotes_never_share_a_match() -> None:
    """
    Given  a text that repeats one expression
    When   the same quote is looked up twice, unordered
    Then   the second lookup finds the second occurrence
    """
    text = "Er war dort. Sie war dort."

    assert find_spans(text, ["war dort", "war dort"]) == [(3, 11), (17, 25)]


def test_an_ordered_lookup_starts_where_the_last_one_ended() -> None:
    """
    Given  a text with two expressions in text order
    When   both are looked up in order
    Then   each span is the one of its own expression
    """
    text = "Er war dort. Sie war dort."

    assert find_spans(text, ["Er war dort.", "Sie war dort."], ordered=True) == [
        (0, 12),
        (13, 26),
    ]


def test_a_quote_that_was_rewritten_is_found_fuzzily() -> None:
    """
    Given  a text and a quote that differs from it in one word
    When   the quote is looked up
    Then   the whole sentence-like piece is returned
    """
    text = "Ich ging nach Hause. Er war sehr müde."

    assert find_spans(text, ["Er war sehr muede."]) == [(21, 38)]


def test_a_span_in_an_equal_region_maps_back() -> None:
    """
    Given  a correction that inserts words before a sentence
    When   a span after the insertion is mapped back
    Then   it moves by the length of the insertion, the space after it included
    """
    raw = "Ich ging nach Hause."
    corrected = "Ich ging gestern nach Hause."
    hunks = word_diff(raw, corrected)

    assert map_back((17, 28), hunks) == (9, 20)
    assert raw[9:20] == "nach Hause."


def test_a_span_that_touches_an_edit_does_not_map_back() -> None:
    """
    Given  a correction that replaces a word
    When   a span covering that word is mapped back
    Then   there is no span, because the two texts differ there
    """
    raw = "Ich habe ein grosse Hund."
    corrected = "Ich habe einen großen Hund."
    hunks = word_diff(raw, corrected)

    assert map_back((9, 22), hunks) is None


def test_a_span_after_a_deleted_word_maps_to_the_same_words() -> None:
    """
    Given  a correction that deletes a word, the two sides of the hunk trimming different spaces
    When   a quote after the deletion is mapped back
    Then   the span in the student's text holds exactly the quoted words
    """
    raw = "Er kam gestern sehr spät nach Hause. Sie sagte es ihr."
    corrected = "Er kam gestern spät nach Hause. Sie sagte es ihr."
    hunks = word_diff(raw, corrected)

    for quote in ("nach Hause", "Sie sagte es ihr"):
        start = corrected.index(quote)
        mapped = map_back((start, start + len(quote)), hunks)
        assert mapped is not None
        assert raw[mapped[0] : mapped[1]] == quote
