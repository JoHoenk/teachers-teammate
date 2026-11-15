"""Tests of the viewer's scorecard: the rows, the mean and the range over the repeats."""

from __future__ import annotations

import pytest

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval import scoring, summary
from tools.model_eval.summary import share_of
from tools.model_eval.viewer import numbers

TEXTS = fixtures.records()


def _rows(*outputs: str | None) -> dict[int, list]:
    """Return the rows of one repeat per output, the *n*-th answered with the *n*-th output."""
    answers = outputs or (None,)
    return {
        repeat: [fixtures.row(text, output=answers[repeat - 1]) for text in TEXTS]
        for repeat in range(1, len(answers) + 1)
    }


def test_a_language_row_is_the_summary_the_report_prints():
    """
    Given  one repeat of a variant
    When   its scorecard is built
    Then   the row of a language carries the numbers summary.summarise gives for it
    """
    rows = _rows()
    built = numbers.build("v1", rows, complete=True)
    part = next(one for one in summary.summarise(rows[1]) if one.language == "de" and not one.band)

    row = built.row("de")

    assert row is not None
    assert row.measures["recall"].values == (part.recall,)
    assert row.measures["precision"].values == (part.precision,)
    assert row.measures["f_beta"].values == (part.f_beta,)
    assert row.measures["gleu"].values == (part.gleu,)
    assert row.measures["alone"].values == (share_of(part.alone, part.alone_of),)
    assert row.measures["explained"].values == (share_of(part.explained, part.needed),)
    assert row.measures["failed"].values == (float(part.failed),)


def test_the_row_over_the_deciding_languages_is_their_mean_not_their_sum():
    """
    Given  a variant with German and English texts that fare differently
    When   the row over the deciding languages is read
    Then   each figure is the mean of the two languages' figures, as the report prints it
    """
    english = fixtures.record(
        item="en-01",
        language="en",
        source="She go home.",
        reference="She goes home.",
        codes=("R:VERB:SVA",),
        spans=((0, 6),),
        unchanged=(),
    )
    texts = (*TEXTS, english)
    answers = {
        "de-01": None,
        "de-02": None,
        "en-01": "She go home.",
    }
    rows = {
        1: [fixtures.row(text, output=answers[text.id]) for text in texts],
    }
    built = numbers.build("v1", rows, complete=True)
    parts = {
        one.language: one
        for one in summary.summarise(rows[1])
        if one.language in ("de", "en") and not one.band
    }

    row = built.row(numbers.DECIDING)

    assert row is not None
    assert parts["de"].recall == 1.0
    assert parts["en"].recall == 0.0
    assert row.measures["recall"].values == (0.5,)
    expected = (scoring.f_beta(parts["de"].score) + scoring.f_beta(parts["en"].score)) / 2
    assert row.measures["f_beta"].values == (expected,)
    assert row.measures["f_beta"].values != (scoring.f_beta(scoring.pooled(rows[1])),)


def test_the_deciding_row_leaves_out_a_language_without_a_figure():
    """
    Given  a variant whose only language is German
    When   the row over the deciding languages is read
    Then   it is the German row, English not counting as zero
    """
    rows = _rows()
    built = numbers.build("v1", rows, complete=True)

    german = built.row("de")
    deciding = built.row(numbers.DECIDING)

    assert german is not None
    assert deciding is not None
    assert deciding.measures["recall"].values == german.measures["recall"].values
    assert deciding.measures["f_beta"].values == german.measures["f_beta"].values


def test_a_band_row_holds_the_texts_of_that_band():
    """
    Given  a variant whose two texts are in different bands
    When   the scorecard is built
    Then   each band has a row of its own and the language row holds both texts
    """
    built = numbers.build("v1", _rows(), complete=True)

    assert built.row("de", "short").texts == 1
    assert built.row("de", "page").texts == 1
    assert built.row("de").texts == 2


def test_the_range_is_the_spread_of_the_repeats():
    """
    Given  three repeats, one of them answering worse than the other two
    When   the scorecard is built
    Then   the recall has three values and its range runs from the worst to the best
    """
    rows = _rows(None, None, fixtures.SOURCE_ONE + " " + fixtures.SOURCE_TWO)
    built = numbers.build("v1", rows, complete=True)

    recall = built.row("de").measures["recall"]

    assert len(recall.values) == 3
    assert recall.low == min(recall.values)
    assert recall.high == max(recall.values)
    assert recall.mean == pytest.approx(sum(recall.values) / 3)


def test_one_repeat_has_no_spread():
    """
    Given  a variant of one repeat
    When   the scorecard is built
    Then   the mean is the value and the range is that value on both ends
    """
    built = numbers.build("v1", _rows(), complete=True)

    recall = built.row("de").measures["recall"]

    assert recall.values == (1.0,)
    assert recall.low == recall.high == recall.mean == 1.0


def test_an_incomplete_variant_has_no_range():
    """
    Given  a variant whose third repeat holds one text of the two
    When   the scorecard is built
    Then   the row is marked incomplete and the figures have no spread
    """
    rows = _rows(None, None)
    rows[3] = [fixtures.row(TEXTS[0])]
    built = numbers.build("v1", rows, complete=False)

    row = built.row("de")

    assert not row.complete
    assert row.measures["recall"].values == (1.0, 1.0, 1.0)
    assert not row.measures["recall"].has_range
    assert row.measures["recall"].mean == 1.0


def test_a_repeat_without_a_text_of_a_band_has_no_value_for_it():
    """
    Given  a variant whose third repeat lacks the text of the page band
    When   the scorecard is built
    Then   the page row has no value for that repeat and therefore no spread
    """
    rows = _rows(None, None)
    rows[3] = [fixtures.row(TEXTS[0])]
    built = numbers.build("v1", rows, complete=False)

    page = built.row("de", "page")

    assert page.measures["recall"].values == (1.0, 1.0, None)
    assert not page.measures["recall"].has_range
    assert page.measures["recall"].mean is None


def test_the_seconds_per_hundred_words_come_from_the_rows():
    """
    Given  a variant whose texts took a known time
    When   the scorecard is built
    Then   the cost is the seconds over the words of the texts, times a hundred
    """
    rows = {repeat: [fixtures.row(text, seconds=2.0) for text in TEXTS] for repeat in (1, 2)}
    built = numbers.build("v1", rows, complete=True)
    words = sum(row.words for row in rows[1])

    cost = built.row("de").measures["seconds_per_100_words"]

    assert cost.values == (pytest.approx(4.0 / words * 100),) * 2


def test_rows_of_two_configurations_in_one_repeat_are_refused():
    """
    Given  a repeat whose rows hold two models
    When   the scorecard is built
    Then   it is refused, because a variant is one model at one temperature in one mode
    """
    rows = {
        1: [
            fixtures.row(TEXTS[0]),
            fixtures.row(TEXTS[1], model=fixtures.OTHER_MODEL),
        ]
    }

    with pytest.raises(ValueError, match="configurations"):
        numbers.build("v1", rows, complete=True)


def test_a_row_of_a_language_the_variant_has_not_is_none():
    """
    Given  a variant of German texts only
    When   the row of English is asked for
    Then   there is none, and the page shows no row rather than a zero
    """
    built = numbers.build("v1", _rows(), complete=True)

    assert built.row("en") is None
