"""Tests of the words an answer keeps and adds against the reference."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import gate_two, grouping, report, text_loss
from tools.model_eval.text_loss import is_added, is_lost, measure

SENTENCES = [
    f"Im Jahr {2000 + index} hat die Klasse {index} einen langen Ausflug in die Stadt gemacht und dort viele alte Häuser und Museen besucht ."
    for index in range(8)
]
"""Eight distinct sentences of twenty-two words each: a text long enough for a ratio to stay calm."""

LONG = " ".join(SENTENCES)
"""A text of 176 words, the kind a model can drop a sentence from without looking short."""

REMARK = (
    "Das ist meine Anmerkung dazu , die ich dem Text hinzufuegen moechte , weil ich glaube , "
    "dass sie fuer alle Leser sehr wichtig ist ."
)
"""A remark of twenty-three words, the kind a model writes into a text it was asked to correct."""


def _numbers(**overrides: int) -> dict[str, int]:
    """Return the four numbers of one answer, with the ones a test does not vary at rest."""
    values = {"words_out": 100, "words_ref": 100, "cut_run": 0, "added_run": 0}
    values.update(overrides)
    return values


def _stored(**overrides: int) -> dict[str, int]:
    """Return the stored numbers of a text: its GLEU counts and the four words of its answer."""
    return {**fixtures.gleu_scores(), **_numbers(**overrides)}


def _group(*, lost_in: int) -> grouping.RunGroup:
    """Return a group of three repeats whose first German text lost text in *lost_in* of them."""
    base = fixtures.minimal_texts()
    lost = replace(base[0], counts=_stored(words_out=50))
    clean = replace(base[0], counts=_stored())
    repeats = [[lost if repeat < lost_in else clean, base[1]] for repeat in range(3)]
    return fixtures.group_of(repeats)


def _floor(group: grouping.RunGroup) -> gate_two.Check:
    """Return the lost-text check of the German texts of *group*."""
    name = "texts that lost text in 2 or more repeats, de"
    return next(
        check for check in gate_two.GateTwoRule().measure(group).checks if check.name == name
    )


@pytest.mark.use_case("Model_Eval_Report")
def test_a_dropped_sentence_of_a_long_text_is_lost_though_the_ratio_is_fine() -> None:
    """
    Given  a text of eight sentences whose answer dropped the third one
    When   the answer is measured against the reference
    Then   the words are still 87 % of the reference's, so the ratio says nothing, and the
           unmatched deleted run is what catches it
    """
    scores = measure(LONG, " ".join(SENTENCES[:2] + SENTENCES[3:]), LONG)

    assert scores["words_out"] / scores["words_ref"] > text_loss.LOST_RATIO
    assert scores["cut_run"] >= text_loss.RUN_WORDS
    assert is_lost(scores) is True


@pytest.mark.use_case("Model_Eval_Report")
def test_a_heavy_but_complete_correction_is_not_flagged() -> None:
    """
    Given  a text whose answer changes one word in every sentence and drops nothing
    When   the answer is measured against the reference
    Then   neither the ratio nor a run fires, because every change is a replacement
    """
    corrected = " ".join(sentence.replace("gemacht", "unternommen") for sentence in SENTENCES)

    scores = measure(LONG, corrected, LONG)

    assert scores["words_out"] == scores["words_ref"]
    assert (scores["cut_run"], scores["added_run"]) == (0, 0)
    assert is_lost(scores) is False
    assert is_added(scores) is False


@pytest.mark.use_case("Model_Eval_Report")
def test_a_run_the_reference_makes_too_is_not_a_loss() -> None:
    """
    Given  a text whose reference drops its last sentence, and an answer that drops it too
    When   the answer is measured against that reference
    Then   nothing is flagged: the reference is the yardstick, so its own cut is not a defect
    """
    reference = " ".join(SENTENCES[:-1])

    scores = measure(LONG, reference, reference)

    assert scores["words_out"] == scores["words_ref"]
    assert scores["cut_run"] == 0
    assert is_lost(scores) is False


@pytest.mark.use_case("Model_Eval_Report")
def test_an_appended_remark_is_added_text() -> None:
    """
    Given  a short text whose answer appends a remark of its own
    When   the answer is measured against the reference
    Then   the answer has more than twice the words of the reference, so it is flagged as added
    """
    source = "Der Hund laufen schnell ."

    scores = measure(source, f"{source} {REMARK}", source)

    assert scores["words_out"] > text_loss.ADDED_RATIO * scores["words_ref"]
    assert is_added(scores) is True
    assert is_lost(scores) is False


@pytest.mark.use_case("Model_Eval_Report")
def test_an_inserted_comment_is_added_though_the_ratio_is_fine() -> None:
    """
    Given  a long text whose answer inserts a sentence of its own in the middle
    When   the answer is measured against the reference
    Then   the words are only 12 % more, so the ratio says nothing, and the unmatched inserted
           run is what catches it
    """
    corrected = " ".join([*SENTENCES[:4], REMARK, *SENTENCES[4:]])

    scores = measure(LONG, corrected, LONG)

    assert scores["words_out"] / scores["words_ref"] < text_loss.ADDED_RATIO
    assert scores["added_run"] >= text_loss.RUN_WORDS
    assert is_added(scores) is True


@pytest.mark.use_case("Model_Eval_Report")
def test_a_correct_text_with_a_comment_is_measured_against_its_source() -> None:
    """
    Given  a ``correct`` text, which has no reference, whose answer appends a comment
    When   the answer is measured
    Then   the source is the yardstick, so the comment is added text
    """
    source = "Der Hund laeuft schnell ."

    scores = measure(source, f"{source} {REMARK}", "")

    assert scores["words_ref"] == 5
    assert is_added(scores) is True


@pytest.mark.use_case("Model_Eval_Report")
def test_a_row_written_before_the_words_were_stored_is_not_measured() -> None:
    """
    Given  the numbers of an older row, which carry none of the four keys
    When   it is asked whether it lost or added text
    Then   the answer is ``None``, not ``False``: not measured is not the same as clean
    """
    assert is_lost({"gleu_c": 7}) is None
    assert is_added({"gleu_c": 7}) is None


@pytest.mark.use_case("Model_Eval_Gate_Two")
def test_a_text_that_lost_text_in_one_repeat_passes_the_floor() -> None:
    """
    Given  a group whose German text lost text in one of its three repeats
    When   the floors are measured
    Then   the lost-text floor passes, because one repeat is shown and not marked
    """
    check = _floor(_group(lost_in=1))

    assert check.passed is True
    assert check.value == "0"


@pytest.mark.use_case("Model_Eval_Gate_Two")
def test_a_text_that_lost_text_in_two_repeats_fails_the_floor() -> None:
    """
    Given  a group whose German text lost text in two of its three repeats
    When   the floors are measured
    Then   the lost-text floor fails, because the configuration keeps losing text
    """
    check = _floor(_group(lost_in=2))

    assert check.passed is False
    assert check.value == "1"


@pytest.mark.use_case("Model_Eval_Gate_Two")
def test_a_group_written_before_the_words_were_stored_passes_as_not_measured() -> None:
    """
    Given  a group whose rows carry none of the four keys
    When   the floors are measured
    Then   the lost-text floor passes with the value saying that nothing was measured
    """
    check = _floor(fixtures.group_of([list(fixtures.minimal_texts()) for _ in range(3)]))

    assert check.passed is True
    assert check.value == "not measured"


@pytest.mark.use_case("Model_Eval_Report")
def test_the_report_names_the_texts_that_lost_or_added_text() -> None:
    """
    Given  a group with one text that lost text and one that added text
    When   the lines below the table are built
    Then   each is named with its model, its temperature, its id and its words in and out
    """
    base = fixtures.minimal_texts()
    lost = replace(base[0], counts=_stored(words_out=50))
    added = replace(base[1], counts=_stored(words_out=150))
    head = fixtures.header()

    rendered = report.loss_lines([lost.item(head), added.item(head)])

    assert "de-01: 100 → 50 words, cut 0" in rendered
    assert "en-01: 100 → 150 words, added 0" in rendered


@pytest.mark.use_case("Model_Eval_Report")
def test_a_group_that_lost_nothing_prints_no_lines() -> None:
    """
    Given  a group whose texts all kept their words
    When   the lines below the table are built
    Then   there are none, so the report prints no empty heading
    """
    clean = replace(fixtures.minimal_texts()[0], counts=_stored())

    assert report.loss_lines([clean.item(fixtures.header())]) == ""


@pytest.mark.use_case("Model_Eval_Report")
def test_a_run_is_counted_in_words_not_in_tokens() -> None:
    """
    Given  a long text whose answer drops ten words that no reference change overlaps
    When   the answer is measured against the reference
    Then   the cut run is ten words, not the twenty tokens the spaces between them add, so
           the answer is not called lost
    """
    words = LONG.split()
    scores = measure(LONG, " ".join(words[:40] + words[50:]), LONG)

    assert scores["cut_run"] == 10
    assert is_lost(scores) is False
