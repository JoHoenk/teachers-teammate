"""Tests of the viewer's outcomes: what each edit came to, and the rows it is shown in."""

from __future__ import annotations

import pytest

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval import quality, run_format
from tools.model_eval.viewer import outcomes

SOURCE = fixtures.SOURCE_ONE
REFERENCE = fixtures.REFERENCE_ONE
SPANS = fixtures.SPANS_ONE
TOTAL = len(quality.tokenize(SOURCE))


def _outcomes(corrected: str, *, changes=(), labels=None) -> outcomes.Outcomes:
    """Return the outcomes of one version of the fixture text."""
    text = fixtures.record()
    rows = outcomes.sentence_rows(
        SPANS,
        [*quality.ordered_edits(SOURCE, REFERENCE), *quality.ordered_edits(SOURCE, corrected)],
        TOTAL,
    )
    return outcomes.outcomes(
        SOURCE,
        REFERENCE,
        text.labels if labels is None else labels,
        corrected,
        changes,
        rows,
    )


def test_an_edit_the_reference_makes_too_is_exact():
    """
    Given  a version that makes the reference's change
    When   its outcomes are computed
    Then   the edit is exact, it points at the reference edit, and the reference edit is fixed
    """
    found = _outcomes(REFERENCE)

    assert [edit.outcome for edit in found.edits] == [outcomes.EXACT]
    assert found.edits[0].ref == 0
    assert found.edits[0].text == "einen"
    assert [edit.outcome for edit in found.reference] == [outcomes.FIXED]
    assert found.counts == (1, 0, 0)
    assert found.reference_counts == (1, 0, 0)


def test_the_same_place_with_another_replacement_is_located():
    """
    Given  a version that changes the reference's words into something else
    When   its outcomes are computed
    Then   the edit is located, the reference edit is located too, and both count as a miss
    """
    found = _outcomes("Ich habe der Hund. Er ist sehr nett.")

    assert [edit.outcome for edit in found.edits] == [outcomes.LOCATED]
    assert found.edits[0].ref == 0
    assert [edit.outcome for edit in found.reference] == [outcomes.LOCATED]
    assert found.counts == (0, 1, 0)
    assert found.reference_counts == (0, 1, 0)


def test_an_edit_the_reference_does_not_make_is_extra():
    """
    Given  a version that makes the reference's change and one more
    When   its outcomes are computed
    Then   the second edit is extra and touches no reference edit
    """
    found = _outcomes("Ich habe einen Hund. Er ist wirklich sehr nett.")

    assert [edit.outcome for edit in found.edits] == [outcomes.EXACT, outcomes.EXTRA]
    assert found.edits[1].ref is None
    assert found.counts == (1, 0, 1)


def test_a_reference_edit_the_version_leaves_alone_is_missed():
    """
    Given  a version that changes nothing
    When   its outcomes are computed
    Then   the reference edit is missed and the version made no edit at all
    """
    found = _outcomes(SOURCE)

    assert found.edits == ()
    assert [edit.outcome for edit in found.reference] == [outcomes.MISSED]
    assert found.reference_counts == (0, 0, 1)


@pytest.mark.parametrize(
    "corrected",
    [
        REFERENCE,
        "Ich habe der Hund. Er ist sehr nett.",
        "Ich habe einen Hund. Er ist wirklich sehr nett.",
        SOURCE,
    ],
)
def test_the_outcomes_add_up_to_the_row_s_counts(corrected):
    """
    Given  a version of the fixture text
    When   its outcomes are computed and the row is scored
    Then   exact is the row's true positives, located plus extra its false positives, and the
           reference edits that are not fixed its false negatives
    """
    found = _outcomes(corrected)
    score = quality.score_edits(SOURCE, corrected, REFERENCE)
    exact, located, extra = found.counts
    fixed, placed, missed = found.reference_counts

    assert exact == score.true_positives
    assert located + extra == score.false_positives
    assert fixed == score.true_positives
    assert placed + missed == score.false_negatives


def test_the_rows_of_a_text_cover_it_and_join_to_the_version_s_text():
    """
    Given  a version of the fixture text
    When   its rows are joined with the version's edits applied
    Then   the result is the version's text, apart from whitespace
    """
    corrected = "Ich habe einen Hund. Er ist wirklich sehr nett."
    found = _outcomes(corrected)

    joined = "".join(
        run_format.apply_stored_edits(
            "".join(quality.tokenize(SOURCE)[row.start : row.end]),
            [
                (edit.start - row.start, edit.end - row.start, edit.text)
                for edit in found.edits
                if edit.row == number
            ],
        )
        for number, row in enumerate(found.rows)
    )

    assert "".join(joined.split()) == "".join(corrected.split())


def test_a_sentence_gets_one_row_and_the_whitespace_between_them_joins_the_first():
    """
    Given  a text of two sentences and no edit
    When   its rows are computed
    Then   there are two rows, the first reaching the start of the second, and they cover the text
    """
    rows = outcomes.sentence_rows(SPANS, [], TOTAL)

    assert [(row.start, row.end) for row in rows] == [(0, 9), (9, TOTAL)]


def test_an_edit_that_crosses_a_sentence_boundary_merges_the_two_sentences():
    """
    Given  an edit that starts in the first sentence and ends in the second
    When   the rows are computed
    Then   the two sentences are one row, so every version is shown in the same rows
    """
    rows = outcomes.sentence_rows(SPANS, [(2, 12, ())], TOTAL)

    assert [(row.start, row.end) for row in rows] == [(0, TOTAL)]


def test_an_insertion_at_a_sentence_boundary_belongs_to_the_sentence_that_ends_there():
    """
    Given  an insertion exactly where the first sentence ends
    When   the rows are computed
    Then   the first sentence keeps its own row, because a period added there is its change
    """
    rows = outcomes.sentence_rows(SPANS, [(8, 8, (".",))], TOTAL)

    assert [(row.start, row.end) for row in rows] == [(0, 9), (9, TOTAL)]


def test_a_text_without_sentence_spans_is_one_row():
    """
    Given  a text the corpus gave no sentence spans for
    When   its rows are computed
    Then   it is one row over the whole text
    """
    rows = outcomes.sentence_rows((), [(0, 1, ())], TOTAL)

    assert [(row.start, row.end) for row in rows] == [(0, TOTAL)]


def test_the_category_and_the_explanation_come_from_the_stored_changes():
    """
    Given  a row whose stored change carries a category and an explanation
    When   its outcomes are computed
    Then   the edit carries both, and the reference edit carries the corpus code
    """
    text = fixtures.record()
    stored = fixtures.row(text, explanations=("Akkusativ nach 'habe'.",), categories=("grammar",))

    found = _outcomes(REFERENCE, changes=stored.changes)

    assert found.edits[0].category == "grammar"
    assert found.edits[0].explanation == "Akkusativ nach 'habe'."
    assert found.reference[0].code == "R:DET:FORM"
    assert found.reference[0].category == "grammar"


def test_a_row_of_text_mode_has_edits_without_explanations():
    """
    Given  a row of text mode, which stores no changes
    When   its outcomes are computed
    Then   the edits are there and carry no category and no explanation
    """
    found = _outcomes(REFERENCE, changes=())

    assert [edit.outcome for edit in found.edits] == [outcomes.EXACT]
    assert found.edits[0].explanation == ""
    assert found.edits[0].category == ""


def test_a_note_that_explains_no_change_is_an_orphan():
    """
    Given  a stored change with no edit, which is a note the model could not place
    When   its outcomes are computed
    Then   it is kept as an orphan and is not counted as an edit
    """
    found = _outcomes(
        REFERENCE,
        changes=({"edit": None, "category": "grammar", "explanation": "Hier fehlt ein Komma."},),
    )

    assert found.edits[0].outcome == outcomes.EXACT
    assert [orphan.explanation for orphan in found.orphans] == ["Hier fehlt ein Komma."]


def test_a_reference_edit_without_a_label_keeps_an_empty_code():
    """
    Given  a reference whose change no annotated edit matched
    When   its outcomes are computed
    Then   the reference edit has no code and no category, and is still shown
    """
    found = _outcomes(REFERENCE, labels=())

    assert found.reference[0].code == ""
    assert found.reference[0].category == ""
    assert found.reference[0].outcome == outcomes.FIXED
