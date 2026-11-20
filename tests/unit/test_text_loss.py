"""Unit tests for the dropped-text guard of step ① (``domain/proofreading/text_loss.py``)."""

from __future__ import annotations

from teachers_teammate.domain.proofreading.text_loss import (
    ADDED_RATIO,
    LOST_RATIO,
    RUN_WORDS,
    dropped_text,
    repair,
    measure,
    words,
)

#: One paragraph of German; a page is several of them, so dropping one keeps the ratio near 1.
PARAGRAPH = (
    "Die Schülerinnen und Schüler haben in der letzten Woche ein Projekt vorbereitet. "
    "Sie haben dazu in Gruppen gearbeitet und ihre Ergebnisse auf Plakaten festgehalten. "
    "Am Freitag haben sie die Plakate in der Aula aufgehängt und den anderen Klassen erklärt. "
    "Die Lehrerin war mit dem Ergebnis sehr zufrieden und hat den Gruppen gedankt. "
    "In der nächsten Woche wollen sie das Projekt in der Schülerzeitung vorstellen. "
)

#: A page of six paragraphs, long enough that one dropped paragraph keeps the ratio near 1.
PAGE = PARAGRAPH * 6


def test_a_dropped_paragraph_is_caught_by_the_run_not_by_the_ratio() -> None:
    """
    Given  a page and an answer that leaves out one of its paragraphs
    When   the answer is measured against the page
    Then   the length ratio stays near 1 and the deleted run alone marks it as lost
    """
    measured = measure(PAGE, PAGE.replace(PARAGRAPH, "", 1))

    assert measured.words_out > LOST_RATIO * measured.words_in
    assert measured.cut_run >= RUN_WORDS
    assert measured.lost is True
    assert measured.problem is True


def test_a_heavy_but_complete_correction_is_not_flagged() -> None:
    """
    Given  a page and an answer that rewrites many of its words but keeps all of them
    When   the answer is measured against the page
    Then   neither the ratio nor a run marks it, because no stretch of text is missing
    """
    corrected = PAGE.replace("haben", "hatten").replace("wollen", "wollten").replace("ist", "war")

    measured = measure(PAGE, corrected)

    assert measured.words_out == measured.words_in
    assert measured.cut_run < RUN_WORDS
    assert measured.problem is False


def test_a_short_answer_is_lost_by_the_ratio() -> None:
    """
    Given  a page and an answer that keeps only half of it
    When   the answer is measured against the page
    Then   the length ratio marks it, whatever the runs are
    """
    measured = measure(PAGE, PAGE[: len(PAGE) // 2])

    assert measured.words_out < LOST_RATIO * measured.words_in
    assert measured.lost is True


def test_an_answer_that_writes_much_more_is_flagged_as_added() -> None:
    """
    Given  a page and an answer that appends a long remark of its own
    When   the answer is measured against the page
    Then   the length ratio marks it as added, and it is not reported as lost
    """
    remark = "Das Modell erklärt hier ausführlich, was es geändert hat. " * 12

    measured = measure(PAGE, f"{PAGE} {remark}")

    assert measured.words_out > ADDED_RATIO * measured.words_in
    assert measured.added is True
    assert measured.lost is False
    assert measured.problem is True


def test_a_text_whose_own_correction_cuts_a_paragraph_is_flagged_too() -> None:
    """
    Given  a page and an answer that cuts a paragraph the text itself repeats
    When   the answer is measured against the page
    Then   it is flagged, because without a reference the app cannot tell the two apart: the
           guard asks once more and keeps the text only if the second answer does the same
    """
    measured = measure(PAGE, PAGE.replace(PARAGRAPH, "", 1))

    assert measured.lost is True


def test_a_usable_answer_has_no_measurement_to_report() -> None:
    """
    Given  a page and an answer that corrects it without losing or adding text
    When   the answer is checked for dropped text
    Then   there is nothing to report
    """
    assert dropped_text(PAGE, PAGE.replace("haben", "hatten")) is None


def test_words_counts_the_words_of_the_app_tokeniser_without_marks() -> None:
    """
    Given  a sentence with punctuation and a line break
    When   its words are counted
    Then   every word counts, and neither a mark nor whitespace does
    """
    assert words("Ich habe einen Hund.\nEr ist nett.") == 7


def test_a_short_text_is_not_judged_by_the_ratio() -> None:
    """
    Given  texts of three and four words that gain a capital letter and a full stop or a word
    When   the answers are measured
    Then   neither is a problem, because a small text changes its length by a large share
    """
    assert dropped_text("ich bin müde", "Ich bin müde.") is None
    assert dropped_text("ich gehe schule morgen", "Ich gehe morgen zur Schule.") is None


def test_marks_are_not_words() -> None:
    """
    Given  a sentence and the same sentence with marks added
    When   the words are counted
    Then   the marks do not count, so punctuation alone never moves the ratio
    """
    assert words("Ich bin müde") == words("Ich bin müde.") == 3
    assert measure("Ich bin müde", "Ich bin müde!!!").words_out == 3


# ── repair ───────────────────────────────────────────────────────────────────

LONG_SOURCE = "Ich habe ein grosse Hund. Er ist nett und spielt gern im Garten mit meinem Bruder."


def test_repair_puts_back_the_stretch_the_answer_left_out() -> None:
    """
    Given  an answer that corrected the first sentence and left the second out
    When   it is repaired
    Then   the second sentence stands again, after the corrected first one, and the stretch is
           reported with its words and its place in the text that was sent
    """
    repaired = repair(LONG_SOURCE, "Ich habe einen großen Hund.")

    assert repaired is not None
    assert repaired.text == "Ich habe einen großen Hund. " + LONG_SOURCE.split(". ", 1)[1]
    [part] = repaired.left_out
    assert part.text == "Er ist nett und spielt gern im Garten mit meinem Bruder."
    assert LONG_SOURCE[part.start : part.end] == part.text
    assert part.words == 11
    assert repaired.words == 11


def test_repair_keeps_the_answer_everywhere_else() -> None:
    """
    Given  an answer that corrected two words and left a middle sentence out
    When   it is repaired
    Then   both corrections survive and the middle sentence is back between them
    """
    source = "Ich habe ein grosse Hund. Er spielt gern im Garten mit meinem Bruder und seinen Freunden. Wir sind froh."
    answer = "Ich habe einen großen Hund. Wir sind froh."

    repaired = repair(source, answer)

    assert repaired is not None
    assert repaired.text == (
        "Ich habe einen großen Hund. "
        "Er spielt gern im Garten mit meinem Bruder und seinen Freunden. Wir sind froh."
    )


def test_repair_leaves_short_cuts_alone() -> None:
    """
    Given  an answer that cut a few words here and there
    When   it is repaired
    Then   there is nothing to put back: no cut reaches the length of a sentence
    """
    source = "Heute gehe ich mit meinem kleinen Bruder und unserer Oma in den grossen Park."
    assert repair(source, "Heute gehe ich mit meinem Bruder in den grossen Park.") is None


def test_repair_changes_nothing_for_an_answer_that_kept_everything() -> None:
    """
    Given  an answer that only corrected words
    When   it is repaired
    Then   there is nothing to put back
    """
    assert repair(PAGE, PAGE.replace("haben", "hatten")) is None


_THREE_PARAGRAPHS = (
    "Erster Absatz mit Text.\n\n"
    "Zweiter Absatz hat viele Woerter die das Modell einfach weglaesst ohne Grund.\n\n"
    "Dritter Absatz."
)


def test_repair_puts_a_left_out_paragraph_back_with_its_paragraph_breaks() -> None:
    """
    Given  an answer that left out the middle paragraph of three
    When   it is repaired
    Then   the text is the source again: the paragraph breaks around the stretch are the
           source's, not a single space
    """
    repaired = repair(_THREE_PARAGRAPHS, "Erster Absatz mit Text.\n\nDritter Absatz.")

    assert repaired is not None
    assert repaired.text == _THREE_PARAGRAPHS


def test_repair_puts_a_left_out_last_paragraph_back_after_a_paragraph_break() -> None:
    """
    Given  an answer that ends before the last two paragraphs
    When   it is repaired
    Then   the stretch follows the first paragraph after the source's own break
    """
    repaired = repair(_THREE_PARAGRAPHS, "Erster Absatz mit Text.")

    assert repaired is not None
    assert repaired.text == _THREE_PARAGRAPHS
