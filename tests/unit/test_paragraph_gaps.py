"""Unit tests for the blank-line gaps of a request (``domain/proofreading/paragraph_gaps.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.paragraph_gaps import BREAK, collapse, restore_gaps

FIRST = "Meine Familie und ich wohnen am Stadtrand."
SECOND = "Am Wochenende fahren wir mit dem Fahrrad in den Wald."
THIRD = "Danach sind wir müde."

FOUR_BLANK_LINES = "\n\n\n\n\n"
"""The gap of the held-out text: four blank lines between two paragraphs."""


def test_a_four_line_gap_becomes_one_paragraph_break() -> None:
    """
    Given  a text of two essays with four blank lines between them
    When   the exchange narrows it for the request
    Then   one blank line stands between the essays and the gap is held for the answer
    """
    sent, gaps = collapse(f"{FIRST}{FOUR_BLANK_LINES}{SECOND}")

    assert sent == f"{FIRST}{BREAK}{SECOND}"
    assert gaps == (FOUR_BLANK_LINES,)


def test_the_gaps_come_back_where_the_answer_kept_the_breaks() -> None:
    """
    Given  a text with a long gap and corrected answers that kept its paragraph break
    When   the gaps are put back into each answer
    Then   the student's own gap stands in the text again, and a normal break is not widened
    """
    source = f"{FIRST}{FOUR_BLANK_LINES}{SECOND}\n\n{THIRD}"
    sent, gaps = collapse(source)
    answer = sent.replace("wohnen", "leben")

    assert sent == f"{FIRST}{BREAK}{SECOND}{BREAK}{THIRD}"
    assert restore_gaps(answer, gaps) == source.replace("wohnen", "leben")


def test_a_text_whose_breaks_are_all_one_blank_line_is_sent_unchanged() -> None:
    """
    Given  a text whose paragraphs stand one blank line apart
    When   it is narrowed for the request
    Then   it is sent as it is and no gap is held, so the exchange has nothing to put back
    """
    source = f"{FIRST}{BREAK}{SECOND}"

    sent, gaps = collapse(source)

    assert sent == source
    assert gaps == ()
    assert restore_gaps(source, gaps) == source


def test_the_paragraph_breaks_of_the_answer_are_not_moved() -> None:
    """
    Given  a text with one long gap and an answer that split a paragraph of its own
    When   the gaps are put back
    Then   the answer is stored as the model wrote it, because its breaks no longer count the
           same as the text's
    """
    sent, gaps = collapse(f"{FIRST}{FOUR_BLANK_LINES}{SECOND}")
    split = f"{FIRST}{BREAK}Mit dem Rad.{BREAK}{SECOND}"

    assert restore_gaps(split, gaps) == split


def test_an_answer_that_merged_the_paragraphs_keeps_its_own_layout() -> None:
    """
    Given  a text with one long gap and an answer that wrote both essays as one paragraph
    When   the gaps are put back
    Then   no gap is written into the answer, which has no paragraph break to carry it
    """
    sent, gaps = collapse(f"{FIRST}{FOUR_BLANK_LINES}{SECOND}")
    merged = f"{FIRST} {SECOND}"

    assert restore_gaps(merged, gaps) == merged
