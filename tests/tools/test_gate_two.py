"""Tests of gate 2: the floors of one model, and the comparison of two."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import gate_two, spot_check


def _judge(group, marks=None) -> gate_two.Judgement:
    """Return the judgement of *group* under the starting rule."""
    return gate_two.GateTwoRule().judge(group, marks)


def _check(checks, name: str) -> gate_two.Check:
    """Return the check called *name* of *checks*."""
    return next(check for check in checks if check.name == name)


def _named(judgement: gate_two.Judgement, name: str) -> gate_two.Check:
    """Return the check called *name* of *judgement*."""
    return _check(judgement.checks, name)


def _diagnostic(judgement: gate_two.Judgement, name: str) -> gate_two.Check:
    """Return the diagnostic called *name* of *judgement*."""
    return next(check for check in judgement.diagnostics if check.name == name)


def _sample(group) -> spot_check.Sample:
    """Return the explanation sample of *group*'s run of repeat 1."""
    run, items = group.first()
    return spot_check.sample(run, items)


def _worse(texts, **changes) -> list[fixtures.Text]:
    """Return *texts* with one number changed, as a weaker model would have it."""
    return [replace(text, **changes) for text in texts]


def _fell_back(texts, ids: set[str]) -> list[fixtures.Text]:
    """Return *texts* with the texts of *ids* fallen back to corrections only."""
    return [
        replace(text, fallback=True, cost=fixtures.performance()) if text.id in ids else text
        for text in texts
    ]


# ── the floors ────────────────────────────────────────────────────────────────


def test_a_failed_text_fails():
    """
    Given  a group in which one text could not be proofread at all
    When   the floors are judged
    Then   the model fails, because a text the teacher cannot use is a hard stop
    """
    texts = [
        replace(text, failed=True) if text.id == "de-01" else text
        for text in fixtures.baseline_texts()
    ]

    judgement = _judge(fixtures.group(*texts))

    assert judgement.outcome is gate_two.Outcome.FAIL
    assert _named(judgement, "failed texts, de").passed is False


def test_a_cut_off_that_fell_back_fails():
    """
    Given  a text whose answer was cut off and which fell back to corrections only
    When   the floors are judged
    Then   the model fails, because the teacher lost the explanations of that text
    """
    texts = [
        replace(text, fallback=True, cost=fixtures.performance(done_reason="length"))
        if text.id == "de-01"
        else text
        for text in fixtures.baseline_texts()
    ]

    judgement = _judge(fixtures.group(*texts))

    assert judgement.outcome is gate_two.Outcome.FAIL
    assert _named(judgement, "cut-offs that cost the explanations, de").passed is False


def test_an_absorbed_cut_off_is_only_a_diagnostic():
    """
    Given  a text whose first answer was cut off and whose second try answered
    When   the floors are judged
    Then   the model passes, and the absorbed cut-off is printed as a diagnostic, because it cost
           time and not the explanations
    """
    texts = [
        replace(text, cost=fixtures.performance(done_reason="length"))
        if text.id == "de-01"
        else text
        for text in fixtures.baseline_texts()
    ]

    judgement = _judge(fixtures.group(*texts))

    assert judgement.outcome is gate_two.Outcome.PASS_PENDING
    assert _named(judgement, "cut-offs that cost the explanations, de").passed is True
    absorbed = _diagnostic(judgement, "cut-offs the second try absorbed")
    assert absorbed.passed is None
    assert absorbed.value == "de 3"


def test_other_fallbacks_are_counted_per_language():
    """
    Given  two fallbacks that were not cut off in one language, and one in each language
    When   the floors are judged
    Then   the first fails, because a language may have one such fallback and no more
    """
    texts = fixtures.baseline_texts()
    twice = fixtures.group_of([_fell_back(texts, {"de-01", "de-02"}), texts, texts])
    once = fixtures.group_of([_fell_back(texts, {"de-01", "en-01"}), texts, texts])

    assert _judge(twice).outcome is gate_two.Outcome.FAIL
    assert _judge(once).outcome is gate_two.Outcome.PASS_PENDING


def test_a_partial_review_counts_as_a_fallback():
    """
    Given  a text whose review covered only part of it, so some pieces fell back
    When   the floors are judged
    Then   it counts as a fallback and not as a cut-off, because the window did not cut it off
    """
    texts = fixtures.baseline_texts()

    judgement = _judge(fixtures.group_of([_fell_back(texts, {"de-01"}), texts, texts]))

    assert _named(judgement, "other fallbacks, de").value == "1"
    assert _named(judgement, "other fallbacks, en").value == "0"
    assert _named(judgement, "cut-offs that cost the explanations, de").value == "0"
    assert judgement.outcome is gate_two.Outcome.PASS_PENDING


def test_coverage_is_pooled_over_the_repeats():
    """
    Given  a group whose coverage is 0.88, 0.91 and 0.92 over its three repeats
    When   the floors are judged
    Then   the pooled 0.903 passes, because a rate on one repeat is noisy
    """
    texts = fixtures.minimal_texts()

    pooled = _judge(fixtures.varying([88, 91, 92], field="cover", texts=texts))
    below = _judge(fixtures.varying([88, 89, 90], field="cover", texts=texts))

    assert _named(pooled, "coverage, de").value == "0.90"
    assert _named(pooled, "coverage, de").passed is True
    assert _named(below, "coverage, de").passed is False


def test_explained_share_is_not_a_floor_in_text_mode():
    """
    Given  a text-mode group whose changes carry no explanations, because text mode has none
    When   the floors are judged
    Then   there is no explained-share check, and the model passes
    """
    texts = _worse(fixtures.minimal_texts(), explained=0)

    judgement = _judge(fixtures.group(*texts, mode="text"))

    assert not [check for check in judgement.checks if check.name == "explained share"]
    assert judgement.outcome is gate_two.Outcome.PASS


def test_gleu_must_beat_the_source_in_every_run():
    """
    Given  a group whose second repeat scores below the unchanged source
    When   the floors are judged
    Then   the model fails, because the guard holds in every run and not on average
    """
    texts = fixtures.minimal_texts()
    good = fixtures.gleu_scores()
    bad = fixtures.gleu_scores(matched=(10, 5, 2, 1))
    group = fixtures.group_of(
        [
            [replace(text, counts=good) for text in texts],
            [replace(text, counts=bad) for text in texts],
            [replace(text, counts=good) for text in texts],
        ]
    )

    judgement = _judge(group)

    assert _named(judgement, "GLEU above the source, de").passed is False
    assert judgement.outcome is gate_two.Outcome.FAIL


def test_left_alone_below_half_fails():
    """
    Given  a model that leaves 49 of the 100 sentences alone that the reference leaves alone
    When   the floors are judged
    Then   it fails, because rewriting correct sentences costs the teacher a check each
    """
    texts = _worse(fixtures.minimal_texts(), alone=49)

    judgement = _judge(fixtures.group(*texts))

    assert _named(judgement, "left alone, de").value == "0.49"
    assert judgement.outcome is gate_two.Outcome.FAIL


def test_every_floor_is_measured_per_language():
    """
    Given  a group of German, English and French texts
    When   the floors are measured
    Then   each language gets its own check for every floor, events included, and nothing is
           pooled across languages
    """
    measurement = gate_two.GateTwoRule().measure(fixtures.group(*fixtures.baseline_texts()))

    assert measurement.languages() == ("de", "en", "fr")
    for floor in (
        "failed texts",
        "cut-offs that cost the explanations",
        "other fallbacks",
        "added marks",
        "coverage",
        "explained share",
        "GLEU above the source",
        "left alone",
    ):
        assert [check.name for check in measurement.checks if check.name.startswith(floor)] == [
            f"{floor}, de",
            f"{floor}, en",
            f"{floor}, fr",
        ]


def test_a_failing_french_check_is_shown_but_does_not_decide():
    """
    Given  a group whose French text covers a tenth of the errors and leaves a tenth alone
    When   the floors are judged
    Then   the French checks are shown with their ✗ and "(not deciding)", and the model passes
    """
    judgement = _judge(fixtures.group(*fixtures.baseline_texts()))

    assert judgement.outcome is gate_two.Outcome.PASS_PENDING
    assert _named(judgement, "coverage, fr").passed is False
    assert _named(judgement, "coverage, fr").deciding is False
    assert "(not deciding)" in _named(judgement, "coverage, fr").line()
    assert _named(judgement, "coverage, de").deciding is True


def test_adding_french_to_the_deciding_languages_makes_it_decide():
    """
    Given  the same group, whose French text fails the coverage floor
    When   the rule is given French among its deciding languages
    Then   the model fails, because the calibration changes a value and not a branch
    """
    group = fixtures.group(*fixtures.baseline_texts())

    assert _judge(group).outcome is gate_two.Outcome.PASS_PENDING
    assert (
        gate_two.GateTwoRule(deciding=("de", "en", "fr")).judge(group).outcome
        is gate_two.Outcome.FAIL
    )


def test_two_repeats_are_incomplete():
    """
    Given  a group of two repeats
    When   the floors are judged
    Then   nothing is judged, because the spread of two values says nothing
    """
    judgement = _judge(fixtures.group(*fixtures.baseline_texts(), repeats=2))

    assert judgement.outcome is gate_two.Outcome.INCOMPLETE
    assert _named(judgement, "repeats").passed is False


# ── the explanations ──────────────────────────────────────────────────────────


def test_no_marks_is_pass_pending():
    """
    Given  a group that meets every floor and whose explanations nobody has marked
    When   the floors are judged
    Then   it passes pending, which is enough for an alternative's place and not for a star's
    """
    judgement = _judge(fixtures.group(*fixtures.baseline_texts()))

    assert judgement.outcome is gate_two.Outcome.PASS_PENDING
    assert _named(judgement, "explanations, de").value == "10 to mark"


def test_review_mode_without_a_sample_is_pending_not_pass():
    """
    Given  a review-mode group of short texts only, so its explanation sample is empty
    When   the floors are judged
    Then   the outcome is pending and not a pass, because no star is given without marks
    """
    judgement = _judge(fixtures.group(*fixtures.minimal_texts()))

    assert judgement.outcome is gate_two.Outcome.PASS_PENDING
    assert _named(judgement, "explanations, de").passed is None
    assert _named(judgement, "explanations, de").value == "nothing to mark"


def test_a_language_without_sample_notes_is_pending():
    """
    Given  a group whose German texts are pages and whose English text is short, so only German
           has explanations to mark
    When   the floors are judged
    Then   English gets a check of its own that is neither passed nor failed, and the outcome is
           pending
    """
    texts = [*fixtures.page_texts("de"), fixtures.Text(id="en-01", language="en", band="short")]

    judgement = _judge(fixtures.group(*texts))

    assert _named(judgement, "explanations, de").value == "10 to mark"
    assert _named(judgement, "explanations, en").passed is None
    assert _named(judgement, "explanations, en").value == "nothing to mark"
    assert judgement.outcome is gate_two.Outcome.PASS_PENDING


def test_text_mode_has_no_explanation_check():
    """
    Given  a text-mode group, which has no explanations at all
    When   the floors are judged
    Then   there is no explanation check and the model passes, because text mode has none by mode
           and not because its sample happens to be empty
    """
    judgement = _judge(fixtures.group(*fixtures.baseline_texts(), mode="text"))

    assert not [check for check in judgement.checks if check.name.startswith("explanations,")]
    assert judgement.outcome is gate_two.Outcome.PASS


def test_seven_of_ten_correct_fails():
    """
    Given  a sample of ten explanations per language of which seven are marked correct
    When   the floors are judged
    Then   the model fails, because eight of ten have to be correct
    """
    group = fixtures.group(*fixtures.baseline_texts())
    marks = fixtures.marks_for(_sample(group), correct=7)

    judgement = _judge(group, marks)

    assert _named(judgement, "explanations, de").value == "7/10 correct"
    assert judgement.outcome is gate_two.Outcome.FAIL


def test_vague_does_not_count():
    """
    Given  a sample of ten explanations per language, eight correct and two vague
    When   the floors are judged
    Then   the model passes; and with seven correct and three vague it fails, because only
           ``correct`` counts
    """
    group = fixtures.group(*fixtures.baseline_texts())
    sample = _sample(group)

    passing = _judge(group, fixtures.marks_for(sample, correct=8, vague=2))
    failing = _judge(group, fixtures.marks_for(sample, correct=7, vague=3))

    assert passing.outcome is gate_two.Outcome.PASS
    assert failing.outcome is gate_two.Outcome.FAIL


# ── the comparison ────────────────────────────────────────────────────────────


def _french_worse(texts):
    """Return *texts* with the French ones scoring worse, so a mean over them would differ."""
    return [replace(text, tp=50, fp=50) if text.language == "fr" else text for text in texts]


def test_compare_prints_each_language_and_rates_on_the_deciding_mean():
    """
    Given  two models whose German and English F0.5 are apart and whose French is worse
    When   they are compared
    Then   each language's figures are printed, and the verdict rests on the mean of German and
           English, not on French
    """
    texts = _french_worse(fixtures.baseline_texts())
    first = fixtures.group_of(
        [[replace(text, tp=value) for text in texts] for value in (90, 91, 92)],
        model=fixtures.MODEL,
    )
    second = fixtures.group_of(
        [[replace(text, tp=value) for text in texts] for value in (80, 81, 82)],
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    result = gate_two.compare(first, second)

    assert result.comparison is gate_two.Comparison.BETTER
    assert [check.name for check in result.per_language] == [
        f"{figure}, {code}"
        for code in ("de", "en", "fr")
        for figure in ("F0.5", "F0.5, texts", "GLEU", "page band, F0.5")
    ]
    deciding = [
        float(_check(result.per_language, f"F0.5, {code}").value.split()[0])
        for code in ("de", "en")
    ]
    assert _check(result.checks, "F0.5, repeats").value.startswith(f"{sum(deciding) / 2:.3f}")


def test_the_bootstrap_of_a_language_that_does_not_decide_is_a_real_figure():
    """
    Given  two models of which the first is better in French too
    When   they are compared
    Then   French has its own bootstrap, P(Δ ≤ 0) of 0, and not the 1.000 of "no text": the
           per-language figures are measured for every language, whether it decides or not
    """
    texts = _french_worse(fixtures.baseline_texts())
    first = fixtures.group_of(
        [[replace(text, tp=value) for text in texts] for value in (90, 91, 92)],
        model=fixtures.MODEL,
    )
    second = fixtures.group_of(
        [[replace(text, tp=value) for text in texts] for value in (80, 81, 82)],
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    result = gate_two.compare(first, second)

    assert _check(result.per_language, "F0.5, texts, fr").value == "P(Δ ≤ 0) = 0.000"


def test_adding_french_to_the_deciding_languages_makes_the_comparison_use_it():
    """
    Given  two models that differ only in French, the first being better there
    When   they are compared with French among the deciding languages
    Then   the bootstrap of the verdict reads French too, so the calibration changes a value
    """
    texts = fixtures.baseline_texts()
    first = fixtures.group_of(
        [[replace(t, tp=95 if t.language == "fr" else 90) for t in texts] for _ in range(3)],
        model=fixtures.MODEL,
    )
    second = fixtures.group_of(
        [[replace(t, tp=40 if t.language == "fr" else 90) for t in texts] for _ in range(3)],
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    only_french = gate_two.compare(first, second, deciding=("fr",))

    assert _check(only_french.checks, "F0.5, texts").value == "P(Δ ≤ 0) = 0.000"


def test_a_group_without_a_deciding_language_is_incomplete_and_not_a_pass():
    """
    Given  runs that hold German texts only
    When   the floors judge them
    Then   the outcome is incomplete and names the language with no text, because a pass that
           nothing decided would be a star for a model measured on half the languages
    """
    texts = [text for text in fixtures.minimal_texts() if text.language == "de"]

    judgement = gate_two.GateTwoRule().judge(fixtures.group(*texts))

    assert judgement.outcome is gate_two.Outcome.INCOMPLETE
    assert any(c.name == "languages" and "en" in c.value for c in judgement.checks)


def test_the_bootstrap_ignores_correct_texts():
    """
    Given  two groups whose error texts are identical and whose correct text scores differently
    When   the paired bootstrap runs
    Then   it sees no difference, because a correct text has no errors to compare
    """
    error = fixtures.Text(id="de-01", language="de")
    correct = fixtures.Text(id="de-02", language="de", kind="correct")
    first = fixtures.group_of(
        [[error, replace(correct, tp=90)] for _ in range(3)], model=fixtures.MODEL
    )
    second = fixtures.group_of(
        [[error, replace(correct, tp=10)] for _ in range(3)],
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    assert gate_two.bootstrap(first, second) == 1.0


def test_compare_refuses_groups_with_different_items():
    """
    Given  two groups of the same settings whose texts differ, and a group whose runs differ
    When   they are compared
    Then   the answer is not comparable, with an items check, because the bootstrap would compare
           different texts
    """
    texts = fixtures.minimal_texts()
    first = fixtures.group(*texts, model=fixtures.MODEL)
    second = fixtures.group(*texts[1:], model=fixtures.OTHER, digest=fixtures.OTHER_DIGEST)
    uneven = fixtures.group_of([[texts[0], texts[1]], [texts[0]], [texts[0], texts[1]]])

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.NOT_COMPARABLE
    assert _check(gate_two.compare(first, second).checks, "items").passed is False
    assert gate_two.compare(first, uneven).comparison is gate_two.Comparison.NOT_COMPARABLE
    assert _check(gate_two.compare(first, uneven).checks, "items").passed is False


def test_compare_within_the_spread_is_a_tie():
    """
    Given  two models whose F0.5 ranges overlap over the repeats
    When   they are compared
    Then   the answer is a tie, because the difference is inside the run-to-run noise
    """
    first = fixtures.varying([80, 90, 85], model=fixtures.MODEL)
    second = fixtures.varying([82, 88, 86], model=fixtures.OTHER, digest=fixtures.OTHER_DIGEST)

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.TIE


def test_compare_beyond_the_spread_is_better():
    """
    Given  two models whose F0.5 ranges are apart over the repeats
    When   they are compared
    Then   the better one is better, and the other is worse
    """
    first = fixtures.varying([90, 91, 92], model=fixtures.MODEL)
    second = fixtures.varying([80, 81, 82], model=fixtures.OTHER, digest=fixtures.OTHER_DIGEST)

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.BETTER
    assert gate_two.compare(second, first).comparison is gate_two.Comparison.WORSE


def test_the_bootstrap_stops_a_consistent_small_gap():
    """
    Given  a model that is better on two texts and worse on two, and better pooled
    When   it is compared with the other
    Then   the ranges are apart but the paired bootstrap is not, so the answer is a tie: a gap
           the choice of texts could make is not shown
    """
    texts = [fixtures.Text(id=f"de-{number:02d}", language="de", band="short") for number in (1, 2)]
    first = fixtures.group(
        *[
            replace(texts[0], tp=95, fp=5, fn=5),
            replace(texts[1], tp=95, fp=5, fn=5),
            replace(texts[0], id="de-03", tp=80, fp=20, fn=20),
            replace(texts[1], id="de-04", tp=80, fp=20, fn=20),
        ]
    )
    second = fixtures.group(
        *[
            replace(texts[0], tp=80, fp=20, fn=20),
            replace(texts[1], tp=80, fp=20, fn=20),
            replace(texts[0], id="de-03", tp=85, fp=15, fn=15),
            replace(texts[1], id="de-04", tp=85, fp=15, fn=15),
        ],
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    result = gate_two.compare(first, second)

    assert _named_check(result, "F0.5, repeats").passed is True
    assert _named_check(result, "F0.5, texts").passed is False
    assert result.comparison is gate_two.Comparison.TIE


def test_the_bootstrap_is_reproducible():
    """
    Given  two groups
    When   the paired bootstrap is drawn twice
    Then   both draws give the same number, because the seed is fixed
    """
    first = fixtures.varying([90, 91, 92], model=fixtures.MODEL)
    second = fixtures.varying([80, 81, 82], model=fixtures.OTHER, digest=fixtures.OTHER_DIGEST)

    once = gate_two.bootstrap(first, second)
    twice = gate_two.bootstrap(first, second)

    assert once == twice
    assert 0.0 <= once <= 1.0


def test_a_worse_page_band_stops_better():
    """
    Given  a model that is better over all texts and worse on the page band
    When   it is compared with the other
    Then   it is not better, because the page band may not be worse
    """
    short = [
        fixtures.Text(id=f"de-{number:02d}", language="de", band="short", tp=95, fp=5, fn=5)
        for number in (1, 2, 3, 4, 5, 6)
    ]
    page = [
        fixtures.Text(id=f"de-{number:02d}", language="de", band="page", tp=60, fp=40, fn=40)
        for number in (7, 8)
    ]
    first = fixtures.group(*(short + page))
    second = fixtures.group(
        *_worse(short + page, tp=70, fp=30, fn=30),
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
    )

    result = gate_two.compare(first, second)

    assert _named_check(result, "F0.5, repeats").passed is True
    assert _named_check(result, "page band, F0.5").passed is False
    assert result.comparison is gate_two.Comparison.TIE


@pytest.mark.parametrize(
    "change",
    [
        {"testset_id": "other-2026-11"},
        {"eval_version": 3},
        {"machine": "other-machine"},
        {"num_ctx": 4096},
        {"mode": "text"},
    ],
)
def test_compare_refuses_another_testset_eval_version_machine_window_or_mode(change):
    """
    Given  two groups that differ in a part of the key a comparison may not vary
    When   they are compared
    Then   they are not comparable, because their numbers were measured differently
    """
    texts = fixtures.minimal_texts()
    first = fixtures.group(*texts)
    second = fixtures.group(*texts, model=fixtures.OTHER, **change)

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.NOT_COMPARABLE


def test_compare_accepts_different_temperatures_and_prints_both():
    """
    Given  two models that each started at the temperature their vendor recommends, which differs
    When   they are compared
    Then   they are comparable, and the side-by-side figures name both temperatures, so a reader
           sees that the two were not asked alike
    """
    texts = fixtures.minimal_texts()
    first = fixtures.group(*texts, temperature=1.0)
    second = fixtures.group(
        *_worse(texts, tp=50, fp=50, fn=50),
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
        temperature=0.05,
    )

    result = gate_two.compare(first, second)

    assert result.comparison is gate_two.Comparison.BETTER
    assert next(c for c in result.side_by_side if c.name == "temperature").value == "1 / 0.05"


def test_compare_accepts_one_model_at_two_temperatures():
    """
    Given  one model run at its recommended temperature and again at a refined one
    When   the two groups are compared
    Then   they are comparable, which is how a later refinement is judged
    """
    texts = fixtures.minimal_texts()
    start = fixtures.group(*texts, temperature=1.0)
    refined = fixtures.group(*_worse(texts, tp=50, fp=50, fn=50), temperature=0.7)

    assert gate_two.compare(start, refined).comparison is gate_two.Comparison.BETTER


def test_compare_accepts_different_profiles():
    """
    Given  two models with different digests and different request profiles
    When   they are compared
    Then   they are comparable, because each model is run as the app runs it
    """
    texts = fixtures.minimal_texts()
    first = fixtures.group(*texts)
    second = fixtures.group(
        *_worse(texts, tp=50, fp=50, fn=50),
        model=fixtures.OTHER,
        digest=fixtures.OTHER_DIGEST,
        compact_json=True,
    )

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.BETTER


def _named_check(result: gate_two.ComparisonResult, name: str) -> gate_two.Check:
    """Return the check called *name* of a comparison."""
    return next(check for check in result.checks if check.name == name)


def test_a_deciding_floor_nobody_measured_is_incomplete_not_a_pass():
    """
    Given  a group whose German texts have no sentence spans, so coverage has nothing to measure
           (the situation of a test set built before the spans were kept)
    When   the group is judged
    Then   the coverage check is unmeasured and marked "?", and the outcome is INCOMPLETE,
           never PASS
    """
    texts = [
        replace(text, cover=0, cover_of=0) if text.language == "de" else text
        for text in fixtures.baseline_texts()
    ]

    group = fixtures.group(*texts)
    judgement = _judge(group, fixtures.marks_for(_sample(group)))

    coverage = _named(judgement, "coverage, de")
    assert coverage.passed is None
    assert coverage.line().startswith("?")
    assert judgement.outcome is gate_two.Outcome.INCOMPLETE


def test_an_unmeasured_floor_of_a_language_that_does_not_decide_is_ignored():
    """
    Given  a group whose French texts have no coverage to measure
    When   the group is judged with German and English deciding
    Then   French shows "·" and does not hold back the verdict
    """
    texts = [
        replace(text, cover=0, cover_of=0) if text.language == "fr" else text
        for text in fixtures.baseline_texts()
    ]
    group = fixtures.group(*texts)

    judgement = _judge(group, fixtures.marks_for(_sample(group)))

    assert _named(judgement, "coverage, fr").line().startswith("·")
    assert judgement.outcome is gate_two.Outcome.PASS


def test_a_failed_floor_beats_an_unmeasured_one():
    """
    Given  a group with one failed floor and one unmeasured floor
    When   the group is judged
    Then   the outcome is FAIL
    """
    texts = [
        replace(text, cover=0, cover_of=0, failed=True) if text.id == "de-01" else text
        for text in fixtures.baseline_texts()
    ]
    texts = [
        replace(text, cover=0, cover_of=0) if text.language == "de" else text for text in texts
    ]

    assert _judge(fixtures.group(*texts)).outcome is gate_two.Outcome.FAIL
