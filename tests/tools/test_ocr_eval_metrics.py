"""The scores of one reading: CER, WER, bag of words, kept mistakes, and the failure flags."""

from __future__ import annotations

import time

from tools.ocr_eval import metrics


def test_plain_answer_collapses_whitespace() -> None:
    """Given an answer that joins its lines into paragraphs
    When   plain_answer() handles it
    Then   it is one line of words, as the comparison needs it
    """
    assert metrics.plain_answer("Hallo\n\nWelt   du\n") == "Hallo Welt du"


def test_reference_takes_the_braces_off_and_marks_the_illegible() -> None:
    """Given a transcription with one misspelt word and one illegible mark
    When   reference_of() reads it
    Then   the braced word is plain, the misspelling is marked, and so is the illegible word
    """
    found = metrics.reference_of("Der {Fehler} ist [?] hier\n")

    assert found.words == ("Der", "Fehler", "ist", "[?]", "hier")
    assert found.braced == frozenset({1})
    assert found.illegible == frozenset({3})


def test_cer_counts_the_letters_read_wrongly() -> None:
    """Given a five-letter word read with one letter missing
    When   it is scored
    Then   the CER is one fifth
    """
    score = metrics.score("Halo", "Hallo")

    assert score.cer == 0.2
    assert score.words == 1


def test_an_illegible_word_matches_any_one_word() -> None:
    """Given a transcription that marks one word as illegible
    When   the answer reads a word there
    Then   nothing counts as misread, because nobody could read it
    """
    score = metrics.score("Der Hund bellt", "Der [?] bellt")

    assert score.wer == 0.0
    assert score.flags.names() == ()


def test_a_word_nobody_can_read_costs_nothing_when_the_answer_skips_it() -> None:
    """Given a transcription that marks one word as illegible
    When   the answer reads nothing there at all
    Then   nothing counts as misread, because a word nobody can read was not one to get right
    """
    score = metrics.score("Der bellt", "Der [?] bellt")

    assert score.wer == 0.0


def test_the_braces_come_off_a_word_a_comma_follows() -> None:
    """Given a transcription whose misspelt word is followed by a comma
    When   reference_of() reads it
    Then   the word is plain and the comma is a token of its own
    """
    found = metrics.reference_of("Der {Fehler}, hier")

    assert found.words == ("Der", "Fehler", ",", "hier")
    assert found.braced == frozenset({1})


def test_kept_mistakes_count_the_words_read_as_the_student_wrote_them() -> None:
    """Given a transcription with two misspelt words
    When   the model reads one of them as written and corrects the other
    Then   it kept half of the mistakes, which is what proofreading needs to see
    """
    score = metrics.score("Der Fehler ist hia", "Der {Fehler} ist {hier}")

    assert score.kept == 0.5


def test_a_page_without_a_misspelt_word_has_no_kept_share() -> None:
    """Given a transcription that marks no mistake
    When   it is scored
    Then   the kept share is unknown, not zero
    """
    assert metrics.score("Hallo", "Hallo").kept is None


def test_bag_of_words_ignores_the_reading_order() -> None:
    """Given the same words in another order
    When   the answer is scored
    Then   the word error rate counts it and the bag of words does not
    """
    score = metrics.score("bellt Hund Der", "Der Hund bellt")

    assert score.wer > 0
    assert score.bag_wer == 0.0


def test_a_repeated_sequence_is_a_loop() -> None:
    """Given an answer that repeats three words five times
    When   it is scored
    Then   the loop flag is set
    """
    score = metrics.score(" ".join(["und dann der"] * 5), "Der Hund bellt")

    assert "loop" in score.flags.names()


def test_an_answer_much_longer_than_the_page_is_flagged() -> None:
    """Given a two-word page and an eight-word answer
    When   it is scored
    Then   the answer is flagged as not a reading of the page
    """
    score = metrics.score("a b c d e f g h", "Der Hund")

    assert "long" in score.flags.names()


def test_an_empty_answer_is_flagged_and_all_wrong() -> None:
    """Given a model that answered nothing
    When   it is scored against a page
    Then   the empty flag is set and every word counts as misread
    """
    score = metrics.score("", "Der Hund bellt")

    assert score.flags.empty is True
    assert score.wer == 1.0
    assert score.cer == 1.0


def test_a_cut_off_answer_carries_the_flag() -> None:
    """Given a model that stopped at its token limit
    When   the answer is scored
    Then   the cut-off flag is set, which the answer's words alone cannot tell
    """
    assert "cut_off" in metrics.score("Der Hund", "Der Hund bellt", cut_off=True).flags.names()


def test_a_long_page_is_scored_in_well_under_a_second() -> None:
    """Given a transcription of 2,000 characters and an answer of 8,000
    When   it is scored
    Then   the CER is there in well under a second, so a campaign of thirty pages is not a wait
    """
    reference = "Der Hund bellt laut. " * 100
    answer = (reference * 4).strip()

    started = time.perf_counter()
    score = metrics.score(answer, reference)
    seconds = time.perf_counter() - started

    assert seconds < 0.5
    assert score.words == 500
    assert round(score.cer, 1) == 3.0


def test_a_misspelt_word_before_a_comma_is_kept_when_read_as_written() -> None:
    """Given a transcription whose misspelt word is followed by a comma
    When   the model reads it exactly, the comma against the word as it is printed
    Then   nothing is misread and the mistake counts as kept: the answer is cut like the reference
    """
    score = metrics.score("Der Fehler, hier.", "Der {Fehler}, hier.")

    assert score.wer == 0.0
    assert score.cer == 0.0
    assert score.kept == 1.0


def test_every_word_of_a_braced_passage_is_a_misspelt_word() -> None:
    """Given a misspelling the student split into two words
    When   the model reads both words as written
    Then   each is a word of its own, both marked, both kept
    """
    found = metrics.reference_of("Wir gehen {zu sammen} heim")

    assert found.words == ("Wir", "gehen", "zu", "sammen", "heim")
    assert found.braced == frozenset({2, 3})
    assert metrics.score("Wir gehen zu sammen heim", "Wir gehen {zu sammen} heim").kept == 1.0


def test_an_umlaut_in_either_form_is_the_same_letter() -> None:
    """Given a transcription with a composed ü and an answer with u and a combining diaeresis
    When   the answer is scored
    Then   nothing is misread: both are one letter once composed
    """
    assert metrics.score("Mu\u0308ll", "M\u00fcll").cer == 0.0


def test_an_answer_is_handled_by_the_app_s_ocr_post_processor() -> None:
    """Given a model that answers in layout HTML, as the Chandra models do
    When   the answer is scored
    Then   the tags are gone, as they are from the text a run stores, and the words are right
    """
    answer = '<div data-label="Text"><p>Der Hund bellt</p></div>'

    assert metrics.plain_answer(answer) == "Der Hund bellt"
    assert metrics.score(answer, "Der Hund bellt").cer == 0.0


def test_a_long_answer_without_a_repeated_sequence_is_no_loop() -> None:
    """Given an answer of sixteen different words
    When   it is scored
    Then   no sequence repeats, so it is no loop
    """
    answer = " ".join(f"w{index}" for index in range(16))

    assert "loop" not in metrics.score(answer, answer).flags.names()
