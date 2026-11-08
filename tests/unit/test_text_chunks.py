"""Unit tests for splitting a long text into request-sized pieces.

The invariant that makes the pieces safe to reassemble is checked on every case:
``"".join(chunk.text + chunk.separator for chunk in chunks) == text``.
"""

from __future__ import annotations

import pytest

from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.text_chunks import Chunk, split_for_budget


def _joined(chunks: tuple[Chunk, ...]) -> str:
    """Return the text that the chunks reassemble to."""
    return "".join(chunk.text + chunk.separator for chunk in chunks)


@pytest.mark.use_case("Text_Chunks")
@pytest.mark.parametrize(
    ("text", "text_format"),
    [
        ("Kurzer Text.", "plain"),
        ("Erster Absatz.\n\nZweiter Absatz.\n\nDritter Absatz.", "plain"),
        ("Ein Satz. Noch einer! Und ein dritter?", "plain"),
        ("Ein Absatz mit\nweichem Umbruch.", "plain"),
        ("  \n\n  ", "plain"),
        ("```python\nprint('hallo')\n```\n\nDanach Text.", "markdown"),
        ("| a | b |\n| - | - |\n| 1 | 2 |\n\n- eins\n- zwei", "markdown"),
    ],
)
def test_the_pieces_reassemble_to_the_source_text(text: str, text_format: TextFormat) -> None:
    """
    Given  a text of any shape and a budget smaller than the text
    When   the text is split
    Then   joining every piece with its separator gives the source text back
    """
    chunks = split_for_budget(text, max_chars=5, text_format=text_format)

    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_text_that_fits_is_one_piece() -> None:
    """
    Given  a text shorter than the budget
    When   the text is split
    Then   it comes back as one piece and no separator
    """
    chunks = split_for_budget("Ein kurzer Satz.", max_chars=100)

    assert chunks == (Chunk(text="Ein kurzer Satz."),)


@pytest.mark.use_case("Text_Chunks")
def test_short_paragraphs_are_packed_up_to_the_budget() -> None:
    """
    Given  three paragraphs of ten characters each and a budget of forty
    When   the text is split
    Then   they share one piece, because together they still fit
    """
    text = "Erster Ab.\n\nZweiter Ab.\n\nDritter Ab."

    chunks = split_for_budget(text, max_chars=40)

    assert len(chunks) == 1
    assert chunks[0].text == text


@pytest.mark.use_case("Text_Chunks")
def test_paragraphs_are_kept_together_while_they_fit() -> None:
    """
    Given  paragraphs of thirty characters and a budget of forty
    When   the text is split
    Then   every piece carries one paragraph, never a part of one
    """
    text = "A" * 30 + "\n\n" + "B" * 30 + "\n\n" + "C" * 30

    chunks = split_for_budget(text, max_chars=40)

    assert [chunk.text for chunk in chunks] == ["A" * 30, "B" * 30, "C" * 30]
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_long_paragraph_is_cut_at_sentence_ends() -> None:
    """
    Given  one paragraph of three sentences, longer than the budget
    When   the text is split
    Then   the cut falls after a sentence end, never inside a sentence
    """
    text = "Erster Satz hier. Zweiter Satz hier! Dritter Satz hier?"

    chunks = split_for_budget(text, max_chars=20)

    assert [chunk.text for chunk in chunks] == [
        "Erster Satz hier.",
        "Zweiter Satz hier!",
        "Dritter Satz hier?",
    ]
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_paragraph_without_punctuation_is_cut_at_line_breaks() -> None:
    """
    Given  one long paragraph of lines without any sentence end, like a list from OCR
    When   the text is split
    Then   every piece fits the budget and the cut falls between two lines
    """
    lines = [f"Zeile Nummer {number} ohne Punkt" for number in range(20)]
    text = "\n".join(lines)

    chunks = split_for_budget(text, max_chars=100)

    assert len(chunks) > 1
    assert all(chunk.length <= 100 for chunk in chunks)
    assert all(chunk.text.split("\n")[0].startswith("Zeile") for chunk in chunks)
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_long_run_without_punctuation_or_lines_is_cut_between_words() -> None:
    """
    Given  one long line without punctuation, like unpunctuated handwriting
    When   the text is split
    Then   every piece fits the budget and no word is cut apart
    """
    words = [f"wort{number}" for number in range(60)]
    text = " ".join(words)

    chunks = split_for_budget(text, max_chars=50)

    assert all(chunk.length <= 50 for chunk in chunks)
    assert [word for chunk in chunks for word in chunk.text.split()] == words
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_single_word_longer_than_the_budget_stays_one_piece() -> None:
    """
    Given  one word longer than the budget
    When   the text is split
    Then   it is returned as its own piece, because there is no boundary left to cut at
    """
    text = "x" * 30

    chunks = split_for_budget(text, max_chars=10)

    assert chunks == (Chunk(text=text),)


@pytest.mark.use_case("Text_Chunks")
def test_a_markdown_code_block_is_never_cut() -> None:
    """
    Given  a Markdown text whose code block is longer than the budget
    When   the text is split as Markdown
    Then   the code block stays in one piece, with its fence complete
    """
    code = "```python\n" + "print('x')\n" * 10 + "```"
    text = f"Vor dem Block.\n\n{code}\n\nNach dem Block."

    chunks = split_for_budget(text, max_chars=40, text_format="markdown")

    assert any(chunk.text == code for chunk in chunks)
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_long_markdown_code_block_with_sentence_ends_is_not_cut_inside() -> None:
    """
    Given  a code block longer than the budget whose text contains ". " and blank lines
    When   the text is split as Markdown
    Then   the whole fence, from opening to closing line, is one piece
    """
    code = "```\nEin Satz. Noch ein Satz. Und einer.\n\nNoch ein Absatz. Mit Ende.\n```"
    text = f"Vor dem Block.\n\n{code}\n\nNach dem Block."

    chunks = split_for_budget(text, max_chars=30, text_format="markdown")

    assert any(chunk.text == code for chunk in chunks)
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_fence_that_is_never_closed_does_not_make_the_rest_uncuttable() -> None:
    """
    Given  a Markdown text with a stray opening fence and a lot of text after it
    When   the text is split as Markdown
    Then   the text after the fence is cut to the budget like any other text
    """
    text = "```\nStreuner Zaun.\n\n" + "\n\n".join(["Ein Absatz hier."] * 10)

    chunks = split_for_budget(text, max_chars=40, text_format="markdown")

    assert all(chunk.length <= 40 for chunk in chunks)
    assert _joined(chunks) == text


@pytest.mark.use_case("Text_Chunks")
def test_a_non_positive_budget_returns_the_whole_text() -> None:
    """
    Given  a text and a budget of zero
    When   the text is split
    Then   nothing is split, so a caller cannot loop forever on tiny budgets
    """
    text = "Ein Satz. Noch einer."

    assert split_for_budget(text, max_chars=0) == (Chunk(text=text),)
