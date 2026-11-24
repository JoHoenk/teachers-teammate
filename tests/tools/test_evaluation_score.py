"""Unit tests for the one scoring path, ``score_row``.

The numbers are pinned as golden values, the fallback rule counts a review that fell back as
unexplained, a ``correct`` text counts its untouched sentences, the marks a model added are ignored
unless the report asks for them, and an accepted alternative of the key is counted apart.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace
from typing import Any

import pytest

from teachers_teammate.domain.proofreading.edits import EditPart
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote

from tools.evaluation.core.formats import FormatError
from tools.evaluation.core.labels import ReferenceLabel
from tools.evaluation.core.modes import ALL_STEPS, Exchange, Step
from tools.evaluation.core.records import (
    AcceptedEdit,
    Answer,
    Input,
    Protocol,
    RunHeader,
    RunSettings,
    TestRecord,
)
from tools.evaluation.metrics import text_loss
from tools.evaluation.metrics.quality import apply_alternatives, edits
from tools.evaluation.report.score import (
    SCORER_VERSION,
    ScoreOptions,
    Scored,
    added_marks,
    category_agreement,
    review_for_counting,
    score,
    score_row,
    scored_from_dict,
    strip_added_marks,
)

SOURCE = "Er gehen heim."
OUTPUT = "Er geht heim."
REFERENCE = "Er geht heim."

LONG_SOURCE = "Gestern hat sie ins Kino gegangen mit sein Freundin."
LONG_REFERENCE = "Gestern ist sie mit ihrer Freundin ins Kino gegangen."


@dataclass(frozen=True)
class Extended(Scored):
    """A scored answer of a later version, with one count more than :class:`Scored`."""

    new_count: int = 0


def _record(**overrides) -> TestRecord:
    """Return a test record of one error text, with sensible defaults."""
    values: dict[str, Any] = {
        "id": "de-01",
        "language": "de",
        "source": SOURCE,
        "reference": REFERENCE,
        "origin": "Falko-MERLIN test",
        "band": "short",
    }
    values.update(overrides)
    return TestRecord(**values)


def _answer(**overrides) -> Answer:
    """Return a stored answer of the default text, with sensible defaults."""
    values: dict[str, Any] = {
        "text": "de/de-01",
        "text_sha256": _record().text_sha256,
        "output": OUTPUT,
    }
    values.update(overrides)
    return Answer(**values)


def _run(**overrides) -> RunHeader:
    """Return a run header of the default text, with sensible defaults."""
    values: dict[str, Any] = {
        "input": Input(
            testset="gec-2026-12",
            question_sha256=_record().text_sha256,
            texts=1,
            languages=("de",),
        ),
        "protocol": Protocol(model="gemma4:12b"),
        "run": RunSettings(seed=20261109),
    }
    values.update(overrides)
    return RunHeader(**values)


def _review(source: str, *changes: tuple[str, str, str, str]) -> CorrectionReview:
    """Return a review whose notes quote *changes* (original, corrected, category, explanation)."""
    notes = []
    for index, (original, corrected, category, explanation) in enumerate(changes, start=1):
        start = source.find(original)
        notes.append(
            EditNote(
                id=index,
                parts=(EditPart(start, start + len(original), original, corrected),),
                sentences=(),
                category=category,
                explanation=explanation,
            )
        )
    return CorrectionReview(edits=tuple(notes))


# ── the version is pinned ────────────────────────────────────────────────────


def test_the_scorer_is_versioned() -> None:
    """
    Given  the one scoring path
    When   its version is read
    Then   it is the version the golden numbers of these tests were written for
    """
    assert SCORER_VERSION == 4


# ── the numbers are pinned ───────────────────────────────────────────────────


def test_the_edit_counts_are_pinned() -> None:
    """
    Given  one answer of one error text
    When   it is scored by the one scoring path
    Then   the counts are the golden ones
    """
    scored = score_row(_answer(), _record(), _run())

    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (1, 0, 0)
    assert (scored.scores["located"], scored.scores["ref_edits"]) == (1, 1)


def test_the_numbers_are_pinned() -> None:
    """
    Given  a longer text answered with the reference
    When   it is scored by the one scoring path
    Then   every number of the item is the golden one
    """
    record = _record(source=LONG_SOURCE, reference=LONG_REFERENCE)
    answer = _answer(output=LONG_REFERENCE)

    scored = score_row(answer, record, _run())

    assert scored.scores == {
        "located": 3,
        "ref_edits": 3,
        "tp_h1": 2,
        "tp_h2": 1,
        "fn_h1": 0,
        "fn_h2": 0,
        "added_run": 0,
        "cut_run": 0,
        "words_out": 10,
        "words_ref": 10,
        "gleu_c": 10,
        "gleu_r": 10,
        "gleu_p1": 10,
        "gleu_p2": 9,
        "gleu_p3": 8,
        "gleu_p4": 7,
        "gleu_m1": 10,
        "gleu_m2": 9,
        "gleu_m3": 8,
        "gleu_m4": 7,
        "gleu_src_c": 10,
        "gleu_src_p1": 10,
        "gleu_src_p2": 9,
        "gleu_src_p3": 8,
        "gleu_src_p4": 7,
        "gleu_src_m1": 6,
        "gleu_src_m2": 0,
        "gleu_src_m3": 0,
        "gleu_src_m4": 0,
    }
    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (3, 0, 0)


def test_the_explanations_are_pinned() -> None:
    """
    Given  an answer with one explained change
    When   it is scored by the one scoring path
    Then   the change is counted, explained, and needs no second explanation; the category is not
           checked, because the record carries no labels
    """
    record = _record()
    answer = _answer(
        review=_review(SOURCE, ("gehen", "geht", "grammar", "Das Verb steht im Singular."))
    )

    scored = score_row(answer, record, _run())

    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (1, 0, 0)
    assert (scored.explained, scored.needed, scored.orphans) == (1, 1, 0)
    assert (scored.category_checked, scored.category_agreed) == (0, 0)


def test_the_scorer_is_pure() -> None:
    """
    Given  one answer, one record and one run
    When   the same three inputs are scored twice
    Then   the two results are equal, so a report can be rebuilt from the files alone
    """
    record, answer, run = _record(), _answer(), _run()

    assert score_row(answer, record, run) == score_row(answer, record, run)


# ── the fallback rule ────────────────────────────────────────────────────────


def test_a_run_that_asked_for_explanations_and_got_none_counts_them_as_unexplained() -> None:
    """
    Given  a run that asked for step ② and an answer that carries no review
    When   the answer is scored
    Then   its change needs an explanation and none is explained, so falling back cannot help
    """
    run = _run(protocol=Protocol(model="gemma4:12b", steps=(Step.CORRECT, Step.EXPLAIN)))

    scored = score_row(_answer(review=None), _record(), run)

    assert scored.needed > 0
    assert scored.explained == 0


def test_a_review_mode_answer_that_fell_back_counts_its_changes_as_unexplained() -> None:
    """
    Given  a review-mode run, whose one answer asks for corrections with explanations, and an answer
           that carries no review although its text changed
    When   the answer is scored
    Then   its one change needs an explanation and none is explained, so a fallback cannot inflate
           the explained share of the gate
    """
    run = _run(
        protocol=Protocol(model="gemma4:12b", exchange=Exchange.ONE_REQUEST_REVIEW, steps=ALL_STEPS)
    )

    scored = score_row(_answer(review=None), _record(), run)

    assert (scored.needed, scored.explained) == (1, 0)


def test_a_run_that_asked_for_no_explanations_needs_none() -> None:
    """
    Given  a correct-only run and an answer that carries no review
    When   the answer is scored
    Then   nothing needs an explanation, so the explained share stays empty
    """
    run = _run(protocol=Protocol(model="gemma4:12b", steps=(Step.CORRECT,)))

    scored = score_row(_answer(review=None), _record(), run)

    assert scored.needed == 0


def test_the_fallback_rule_is_applied_in_one_place() -> None:
    """
    Given  a run that asked for explanations, an answer without a review and a changed text
    When   the review the explanations are counted against is asked for
    Then   it is an empty review, so every change counts as unexplained
    """
    run = _run(protocol=Protocol(model="gemma4:12b", steps=(Step.CORRECT, Step.EXPLAIN)))

    counted = review_for_counting(run, None, SOURCE, OUTPUT)

    assert counted is not None
    assert counted.edits == ()


def test_an_unchanged_text_needs_no_explanation_even_without_a_review() -> None:
    """
    Given  a run that asked for explanations and an answer that changed nothing
    When   the review the explanations are counted against is asked for
    Then   there is none, because there is nothing to explain
    """
    run = _run(protocol=Protocol(model="gemma4:12b", steps=(Step.CORRECT, Step.EXPLAIN)))

    assert review_for_counting(run, None, SOURCE, SOURCE) is None


# ── a correct text ──────────────────────────────────────────────────────────


def test_a_correct_text_counts_the_untouched_sentences() -> None:
    """
    Given  an already-correct text whose answer kept one of two sentences
    When   the answer is scored
    Then   one of two sentences counts as unchanged and no edit is scored
    """
    record = _record(
        kind="correct",
        source="Ein Satz. Noch einer.",
        reference="",
        sentences=("Ein Satz.", "Noch einer."),
    )
    answer = _answer(output="Ein Satz. Ein anderer.")

    scored = score_row(answer, record, _run())

    assert scored.unchanged == 1
    assert scored.true_positives == 0


def test_a_correct_text_measures_the_words_of_its_answer() -> None:
    """
    Given  an already-correct text whose answer dropped its second sentence
    When   the answer is scored
    Then   the words of the answer against the source's are there, so the loss is visible even
           though the text has no reference and no other number
    """
    record = _record(
        kind="correct",
        source="Ein Satz. Noch einer.",
        reference="",
        sentences=("Ein Satz.", "Noch einer."),
    )
    answer = _answer(output="Ein Satz.")

    scored = score_row(answer, record, _run())

    assert set(text_loss.KEYS) <= set(scored.scores)
    assert scored.scores["words_out"] < scored.scores["words_ref"]
    assert scored.lost_text is True


def test_an_answer_the_app_repaired_is_lost_text_though_its_output_is_whole() -> None:
    """
    Given  an answer whose stretches the app put back, so its output is the reference
    When   it is scored
    Then   the output measures clean, and the row is lost text all the same because the count of
           put-back stretches is in its scores
    """
    scored = score_row(_answer(output=REFERENCE, left_out=2), _record(), _run())

    assert scored.scores[text_loss.LEFT_OUT] == 2
    assert scored.lost_text is True


def test_a_correct_text_the_app_repaired_is_lost_text() -> None:
    """
    Given  an already-correct text whose answer had a stretch put back by the app
    When   it is scored
    Then   the count is in its scores and the row is lost text, though the output is whole
    """
    record = _record(
        kind="correct",
        source="Ein Satz. Noch einer.",
        reference="",
        sentences=("Ein Satz.", "Noch einer."),
    )

    scored = score_row(_answer(output="Ein Satz. Noch einer.", left_out=1), record, _run())

    assert scored.scores[text_loss.LEFT_OUT] == 1
    assert scored.lost_text is True


def test_an_answer_the_app_did_not_repair_carries_no_count() -> None:
    """
    Given  an answer the app did not have to repair
    When   it is scored
    Then   its scores carry no put-back count, so a row of an older run reads the same
    """
    scored = score_row(_answer(), _record(), _run())

    assert text_loss.LEFT_OUT not in scored.scores


def test_a_correct_text_the_model_edited_still_counts_its_explanations() -> None:
    """
    Given  an already-correct text that the answer changed anyway, with a note for the change
    When   the answer is scored
    Then   the run's explanation counts are there: a model that edits a text it was told is correct
           claimed an edit, and the report says so instead of dropping the claim
    """
    record = _record(
        kind="correct",
        source="Ein Satz. Noch einer.",
        reference="",
        sentences=("Ein Satz.", "Noch einer."),
    )
    answer = _answer(
        output="Ein Satz. Noch zweier.",
        review=_review("Ein Satz. Noch einer.", ("einer", "zweier", "grammar", "Ein Zahlwort.")),
    )

    scored = score_row(answer, record, _run())

    assert (scored.explained, scored.needed, scored.orphans) == (1, 1, 0)
    assert scored.true_positives == 0


# ── the marks a model added ─────────────────────────────────────────────────


def test_marks_the_model_added_are_ignored_by_default() -> None:
    """
    Given  an answer that "highlights" its change with bold text the source does not have
    When   it is scored
    Then   the fix counts as a fix, and the marks are reported so a teacher knows they are there
    """
    answer = _answer(output="Er **geht** heim.")

    scored = score_row(answer, _record(), _run())

    assert scored.true_positives == 1
    assert scored.marks_added is True


def test_marks_the_model_added_are_counted_when_the_report_asks() -> None:
    """
    Given  the same answer
    When   it is scored with the marks counted
    Then   the marks are part of the text, so the answer no longer matches the reference exactly
    """
    answer = _answer(output="Er **geht** heim.")

    scored = score_row(answer, _record(), _run(), ScoreOptions(count_marks=True))

    assert scored.true_positives == 0
    assert scored.marks_added is True


def test_a_mark_the_source_already_has_is_not_reported() -> None:
    """
    Given  a Markdown source and an answer that keeps its bold text
    When   the added marks are checked
    Then   nothing is reported, because the model added nothing
    """
    source = "Er **geht** heim."

    assert added_marks(source, source) is False


def test_stripping_leaves_a_mark_the_source_already_has() -> None:
    """
    Given  a Markdown source and an answer that adds a second bold word
    When   the marks the source does not have are stripped
    Then   the source's own bold text stays, because it is part of the text
    """
    source = "Er **geht** heim."
    output = "Er **geht** gleich heim."

    assert strip_added_marks(source, output) == output


# ── the accepted alternatives of the key ────────────────────────────────────


def test_an_alternative_the_reference_also_makes_is_not_counted_apart() -> None:
    """
    Given  a key that accepts a fix the reference makes too, and an answer that makes it
    When   the answer is scored
    Then   nothing is counted apart, because the figures with and without it are the same
    """
    record = _record(accepted=(AcceptedEdit(span=(3, 8), replacement="geht"),))

    scored = score_row(_answer(), record, _run())

    assert scored.accepted == 0
    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (1, 0, 0)


def test_an_accepted_alternative_the_answer_does_not_make_costs_nothing() -> None:
    """
    Given  a key that accepts a fix the answer does not make
    When   the answer is scored
    Then   nothing is counted and no false negative is added, because the fix was optional
    """
    record = _record(accepted=(AcceptedEdit(span=(3, 8), replacement="ginge"),))

    scored = score_row(_answer(), record, _run())

    assert scored.accepted == 0
    assert scored.false_negatives == 0


def test_an_answer_that_fixes_an_error_the_way_another_annotator_did_is_not_punished() -> None:
    """
    Given  a reference that fixes one error one way, and a key that accepts another annotator's fix
           of the same error, which the answer makes
    When   the answer is scored
    Then   its fix is a true positive and the reference's fix counts as found, so neither an
           invention nor a miss is left
    """
    record = _record(accepted=(AcceptedEdit(span=(3, 8), replacement="gehe"),))
    answer = _answer(output="Er gehe heim.")

    scored = score_row(answer, record, _run())

    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (1, 0, 0)
    assert scored.found == 1
    assert scored.accepted == 1
    assert scored.recall == 1.0


def test_an_optional_alternative_the_answer_leaves_out_costs_nothing() -> None:
    """
    Given  a key that accepts a fix of another error, which the answer does not make
    When   the answer is scored
    Then   nothing is counted and no miss is added, because the fix was optional
    """
    record = _record(accepted=(AcceptedEdit(span=(9, 13), replacement="dort"),))

    scored = score_row(_answer(), record, _run())

    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (1, 0, 0)
    assert scored.accepted == 0


def test_an_optional_alternative_the_answer_makes_is_a_true_positive() -> None:
    """
    Given  a key that accepts a fix of another error, which the answer makes
    When   the answer is scored
    Then   the fix is a true positive and not an invention
    """
    record = _record(accepted=(AcceptedEdit(span=(9, 13), replacement="dort"),))
    answer = _answer(output="Er geht dort.")

    scored = score_row(answer, record, _run())

    assert (scored.true_positives, scored.false_positives, scored.false_negatives) == (2, 0, 0)
    assert scored.accepted == 1
    assert scored.recall <= 1


def test_the_accepted_alternatives_can_be_turned_off() -> None:
    """
    Given  a key that accepts the fix the answer makes
    When   the answer is scored with the alternatives turned off
    Then   the count is zero, so a report can show the figures without them
    """
    record = _record(accepted=(AcceptedEdit(span=(3, 8), replacement="geht"),))

    scored = score_row(_answer(), record, _run(), ScoreOptions(accepted=False))

    assert scored.accepted == 0


# ── the arithmetic of an accepted alternative ───────────────────────────────

ALTERNATIVE_SOURCE = "a b c d e"
"""A text of five words, so an edit is a token span a test can write down."""


def _alternative_score(made, expected, alternatives):
    """Return the counts of one hand-written edit set, as ``(tp, fp, found, fn, accepted)``."""
    score, accepted = apply_alternatives(
        ALTERNATIVE_SOURCE, made=made, expected=expected, alternatives=alternatives
    )
    return (
        score.true_positives,
        score.false_positives,
        score.found,
        score.false_negatives,
        accepted,
    )


def test_the_review_example_counts_the_invention_and_no_miss() -> None:
    """
    Given  a reference with one change, and an answer that makes it and one change of its own
    When   the answer is scored
    Then   one true positive, one invention and no miss, so the precision is one half
    """
    expected = {(2, 3, ("B",))}
    made = expected | {(6, 7, ("D",))}

    assert _alternative_score(made, expected, frozenset()) == (1, 1, 1, 0, 0)


def test_one_alternative_over_two_reference_changes_finds_both() -> None:
    """
    Given  a reference with two changes, and an answer that makes one alternative covering both
    When   the answer is scored
    Then   the alternative is one true positive and both reference changes count as found
    """
    expected = {(2, 3, ("B",)), (6, 7, ("D",))}
    made = {(2, 7, ("B", " ", "c", " ", "D"))}

    assert _alternative_score(made, expected, made) == (1, 0, 2, 0, 1)


def test_an_optional_alternative_is_a_true_positive_only_when_it_is_made() -> None:
    """
    Given  a key that accepts a change of another error, and an answer that makes it or not
    When   the answer is scored
    Then   making it adds one true positive and no miss, and leaving it out changes nothing
    """
    expected = {(2, 3, ("B",))}
    optional = {(6, 7, ("D",))}

    assert _alternative_score(expected | optional, expected, optional) == (2, 0, 1, 0, 1)
    assert _alternative_score(expected, expected, optional) == (1, 0, 1, 0, 0)


def test_the_recall_of_random_edit_sets_stays_at_most_one() -> None:
    """
    Given  random sets of made changes, reference changes and accepted alternatives
    When   each is scored
    Then   the recall is at most one, so no answer is punished for an optional change
    """
    words = ALTERNATIVE_SOURCE.split()
    places = [(index * 2, index * 2 + 1) for index in range(len(words))]
    for seed in range(200):
        chosen = random.Random(seed)
        expected = {
            (start, end, (chosen.choice("XYZ"),))
            for start, end in chosen.sample(places, chosen.randint(0, 3))
        }
        alternatives = {
            (start, end, (chosen.choice("XYZ"),))
            for start, end in chosen.sample(places, chosen.randint(0, 3))
        }
        made = {
            (start, end, (chosen.choice("XYZ"),))
            for start, end in chosen.sample(places, chosen.randint(0, 3))
        }
        score, _ = apply_alternatives(
            ALTERNATIVE_SOURCE, made=made, expected=expected, alternatives=alternatives
        )
        assert score.recall <= 1.0
        assert score.false_negatives >= 0
        assert score.false_positives >= 0


# ── the category agreement ──────────────────────────────────────────────────


def test_a_note_with_the_category_of_the_label_agrees() -> None:
    """
    Given  an exactly fixed error labelled as a grammar error, and a note saying "grammar"
    When   the category agreement is counted
    Then   one error was checked and the note agrees
    """
    labels = (ReferenceLabel(start=3, end=8, replacement="geht", code="R:VERB:SVA"),)
    review = _review(SOURCE, ("gehen", "geht", "grammar", "Das Verb steht im Singular."))

    result = category_agreement(SOURCE, OUTPUT, REFERENCE, review, labels)

    assert (result.checked, result.agreed) == (1, 1)


def test_a_note_with_another_category_is_checked_but_does_not_agree() -> None:
    """
    Given  the same fixed error, and a note saying "word_choice"
    When   the category agreement is counted
    Then   one error was checked and the note does not agree
    """
    labels = (ReferenceLabel(start=3, end=8, replacement="geht", code="R:VERB:SVA"),)
    review = _review(SOURCE, ("gehen", "geht", "word_choice", "Das Verb steht im Singular."))

    result = category_agreement(SOURCE, OUTPUT, REFERENCE, review, labels)

    assert (result.checked, result.agreed) == (1, 0)


def test_a_label_that_maps_to_no_category_is_not_checked() -> None:
    """
    Given  a label whose code maps to "other", which is no category at all
    When   the category agreement is counted
    Then   nothing was checked, because the corpus label says nothing about the category
    """
    labels = (ReferenceLabel(start=3, end=8, replacement="geht", code="U:OTHER"),)
    review = _review(SOURCE, ("gehen", "geht", "grammar", "Das Verb steht im Singular."))

    result = category_agreement(SOURCE, OUTPUT, REFERENCE, review, labels)

    assert (result.checked, result.agreed) == (0, 0)


def test_a_text_without_a_review_has_no_category_agreement() -> None:
    """
    Given  a label and an answer that carries no explanations
    When   the category agreement is counted
    Then   nothing was checked, because no note can agree with anything
    """
    labels = (ReferenceLabel(start=3, end=8, replacement="geht", code="R:VERB:SVA"),)

    result = category_agreement(SOURCE, OUTPUT, REFERENCE, None, labels)

    assert (result.checked, result.agreed) == (0, 0)


# ── the numbers a report reads ──────────────────────────────────────────────


def test_the_precision_and_the_recall_are_the_counts() -> None:
    """
    Given  a scored answer with one true positive and one false negative
    When   the two shares are asked for
    Then   they are the counts over what was made and what was expected
    """
    scored = Scored(true_positives=1, found=1, false_negatives=1)

    assert scored.precision == 1.0
    assert scored.recall == 0.5


def test_a_scored_answer_that_made_no_change_has_no_precision() -> None:
    """
    Given  a scored answer that made no change at all
    When   the precision is asked for
    Then   it is zero rather than a division by zero
    """
    scored = Scored(false_negatives=3)

    assert scored.precision == 0.0
    assert scored.f_beta() == 0.0


def test_the_flat_dict_carries_every_number_a_report_shows() -> None:
    """
    Given  a scored answer
    When   it is flattened for a score cache line
    Then   every count is there under the name the report uses
    """
    scored = score_row(_answer(), _record(), _run())

    flat = scored.to_dict()

    for name in (
        "tp",
        "fp",
        "found",
        "fn",
        "explained",
        "needed",
        "orphans",
        "category_checked",
        "category_agreed",
        "unchanged",
        "unclear",
        "marks_added",
        "accepted",
    ):
        assert name in flat
    assert flat["tp"] == 1


def test_a_count_added_to_the_class_round_trips_with_no_other_edit() -> None:
    """
    Given  a scored answer of a class that carries one count more than ``Scored``
    When   it is flattened and read back
    Then   the new count is stored and read back, because both sides read the class's fields
    """
    scored = replace(Extended(new_count=7), true_positives=1, found=1, scores={"gleu": 3})

    flat = scored.to_dict()

    assert flat["new_count"] == 7
    assert Extended.from_dict(flat) == scored


def test_every_count_of_the_class_is_stored_under_a_name_of_its_own() -> None:
    """
    Given  a scored answer
    When   it is flattened
    Then   every field but the item scores is there, so no count is forgotten
    """
    scored = Scored(true_positives=1, found=1, scores={"gleu": 3})

    flat = scored.to_dict()

    assert set(flat) - set(scored.scores) == {
        "tp",
        "fp",
        "found",
        "fn",
        "explained",
        "needed",
        "orphans",
        "category_checked",
        "category_agreed",
        "unchanged",
        "unclear",
        "marks_added",
        "accepted",
        "explanations_known",
    }
    assert scored_from_dict(flat) == scored


# ── the task decides the scorer ─────────────────────────────────────────────


def test_the_task_of_the_run_picks_the_scorer() -> None:
    """
    Given  a run of the proofreading task
    When   its answer is scored
    Then   the proofreading scorer's numbers come back
    """
    assert score(_answer(), _record(), _run()) == score_row(_answer(), _record(), _run())


def test_a_task_without_a_scorer_is_refused() -> None:
    """
    Given  a run whose task no scorer knows
    When   its answer is scored
    Then   it is refused, because a task is never guessed at
    """
    with pytest.raises(FormatError, match="is not one of proofread"):
        score(_answer(), _record(), _run(task="translate"))


# ── a broken test set is refused ────────────────────────────────────────────


def test_a_sentence_span_that_is_not_on_token_boundaries_is_refused() -> None:
    """
    Given  a record whose sentence span starts inside a word
    When   the answer is scored
    Then   the scorer refuses it, because the defect is the test set's and not the answer's
    """
    record = _record(sentence_spans=((1, 9),))

    with pytest.raises(ValueError, match="does not lie on token boundaries"):
        score_row(_answer(), record, _run())


def test_a_sentence_span_on_token_boundaries_is_counted() -> None:
    """
    Given  a record whose sentence spans lie on token boundaries
    When   the answer is scored
    Then   the spans reach the metrics, so the sentences left alone are counted
    """
    source = "Er geht heim. Er geht heim."
    record = _record(
        source=source,
        reference=source,
        sentence_spans=((0, 13), (14, 27)),
        unchanged_sentences=(0, 1),
    )

    scored = score_row(_answer(output=source), record, _run())

    assert scored.scores["alone"] == 2
    assert scored.scores["alone_of"] == 2


# ── the exchange is not the mode ────────────────────────────────────────────


def test_the_steps_of_the_run_decide_whether_explanations_are_counted() -> None:
    """
    Given  two runs of one model that differ only in the steps they asked for
    When   the same answer without a review is scored for both
    Then   only the run that asked for step ② counts its change as unexplained
    """
    with_explanations = _run(
        protocol=Protocol(
            model="gemma4:12b", exchange=Exchange.TEXT_FIRST, steps=(Step.CORRECT, Step.EXPLAIN)
        )
    )
    without = _run(
        protocol=Protocol(model="gemma4:12b", exchange=Exchange.TEXT_FIRST, steps=(Step.CORRECT,))
    )

    answer = _answer(review=None)

    assert score_row(answer, _record(), with_explanations).needed > 0
    assert score_row(answer, _record(), without).needed == 0
