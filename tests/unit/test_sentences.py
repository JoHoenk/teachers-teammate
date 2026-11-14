"""Unit tests for the sentence boundaries of a plain text (``domain/proofreading/sentences.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.sentences import (
    sentence_index,
    sentence_spans,
    sentence_texts,
)


def test_sentence_spans_split_at_sentence_ends() -> None:
    """
    Given  a text of three sentences
    When   its sentence spans are taken
    Then   every sentence is one span, without the whitespace around it
    """
    text = "Ich gehe heim. Du bleibst hier! Kommst du mit?"

    spans = sentence_spans(text)

    assert sentence_texts(text, spans) == (
        "Ich gehe heim.",
        "Du bleibst hier!",
        "Kommst du mit?",
    )


def test_a_line_break_inside_a_sentence_is_not_a_boundary() -> None:
    """
    Given  a sentence that an OCR line break cuts in two
    When   its sentence spans are taken
    Then   it stays one sentence
    """
    text = "Ich gehe\nnach Hause."

    assert sentence_texts(text, sentence_spans(text)) == ("Ich gehe\nnach Hause.",)


def test_a_blank_line_ends_a_sentence() -> None:
    """
    Given  a heading without a full stop, a blank line, then a sentence
    When   its sentence spans are taken
    Then   the heading is a sentence of its own
    """
    text = "Aufgabe 1\n\nIch gehe heim."

    assert sentence_texts(text, sentence_spans(text)) == ("Aufgabe 1", "Ich gehe heim.")


def test_a_closing_quote_belongs_to_the_sentence() -> None:
    """
    Given  a sentence that ends with a quote after its full stop
    When   its sentence spans are taken
    Then   the quote is part of the sentence
    """
    text = 'Er sagte "Nein." Dann ging er.'

    assert sentence_texts(text, sentence_spans(text)) == ('Er sagte "Nein."', "Dann ging er.")


def test_an_empty_text_has_no_sentences() -> None:
    """
    Given  an empty and a whitespace-only text
    When   their sentence spans are taken
    Then   neither has a sentence
    """
    assert sentence_spans("") == ()
    assert sentence_spans("  \n\n  ") == ()


def test_sentence_index_finds_the_sentence_of_an_offset() -> None:
    """
    Given  a text of two sentences
    When   the sentence of an offset in the second one is asked for
    Then   the second sentence is returned
    """
    text = "Ich gehe heim. Du bleibst hier."
    spans = sentence_spans(text)

    assert sentence_index(spans, 0) == 0
    assert sentence_index(spans, text.index("Du")) == 1


def test_sentence_index_of_an_offset_between_two_sentences_is_the_first() -> None:
    """
    Given  a text of two sentences
    When   the sentence of the space between them is asked for
    Then   the sentence before it is returned
    """
    text = "Ich gehe heim. Du bleibst hier."
    spans = sentence_spans(text)

    assert sentence_index(spans, text.index(" Du")) == 0


def test_sentence_index_without_sentences_is_minus_one() -> None:
    """
    Given  a text without a sentence
    When   the sentence of an offset is asked for
    Then   -1 is returned
    """
    assert sentence_index((), 0) == -1
