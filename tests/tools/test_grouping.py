"""Tests of the groups gate 2 reads: the key, the ranges and the repeats of a group."""

from __future__ import annotations

import pytest

from tests.tools import gate_fixtures as fixtures
from tools.model_eval import grouping


def _manifest(*texts: fixtures.Text) -> dict:
    """Return the manifest that types *texts*."""
    return {"testset": fixtures.TESTSET, "items": [text.entry() for text in texts]}


def test_two_runs_of_one_repeat_are_refused():
    """
    Given  two runs of one model that both carry repeat 1
    When   they are grouped
    Then   the grouping is refused, because a repeat is one run and two would be counted twice
    """
    texts = fixtures.minimal_texts()
    runs = [fixtures.run(*texts), fixtures.run(*texts)]

    with pytest.raises(ValueError, match="two runs of repeat"):
        grouping.groups(runs, _manifest(*texts))


def test_a_run_of_another_test_set_is_refused():
    """
    Given  a run of another test set than the manifest's
    When   it is grouped against that manifest
    Then   it is refused, because its items cannot be typed against another set's entries
    """
    texts = fixtures.minimal_texts()
    other = fixtures.run(*texts, testset_id="other-2026-11")

    with pytest.raises(ValueError, match="the run is of test set"):
        grouping.groups([other], _manifest(*texts))


@pytest.mark.parametrize(
    "change",
    [
        {"model": fixtures.OTHER},
        {"machine": "other-machine"},
        {"eval_version": 3},
        {"mode": "text"},
        {"temperature": 0.7},
        {"num_ctx": 4096},
        {"digest": fixtures.OTHER_DIGEST},
        {"compact_json": True},
        {"provider": "openai"},
        {"explainer": fixtures.OTHER},
    ],
)
def test_each_part_of_the_key_separates_groups(change):
    """
    Given  two runs that differ in one part of the group key
    When   they are grouped
    Then   they make two groups, because that part was measured differently
    """
    texts = fixtures.minimal_texts()
    runs = [fixtures.run(*texts), fixtures.run(*texts, **change)]

    found = grouping.groups(runs, _manifest(*texts))

    assert len(found) == 2
    assert found[0].key != found[1].key


def test_the_repeats_of_a_group_are_in_order():
    """
    Given  three runs of one model, given out of order
    When   they are grouped
    Then   one group holds them in repeat order, so a range reads in repeat order too
    """
    texts = fixtures.minimal_texts()
    runs = [fixtures.run(*texts, repeat=3), fixtures.run(*texts, repeat=1)]
    runs.append(fixtures.run(*texts, repeat=2))

    (found,) = grouping.groups(runs, _manifest(*texts))  # pylint: disable=unbalanced-tuple-unpacking  # one group

    assert found.repeats == (1, 2, 3)
    assert found.complete


def test_two_repeats_are_incomplete():
    """
    Given  a group of two repeats
    When   it is asked whether it is complete
    Then   it is not, because the spread of two values says nothing
    """
    group = fixtures.group(*fixtures.minimal_texts(), repeats=2)

    assert not group.complete


def test_beyond_at_its_edges():
    """
    Given  two ranges
    When   one is asked whether it lies beyond the other
    Then   only a lowest value above the other's highest counts, and equal values do not
    """
    assert grouping.beyond(grouping.Range.of([0.5, 0.6]), grouping.Range.of([0.3, 0.4]))
    assert not grouping.beyond(grouping.Range.of([0.5, 0.6]), grouping.Range.of([0.4, 0.55]))
    assert not grouping.beyond(grouping.Range.of([0.5, 0.5]), grouping.Range.of([0.5, 0.5]))
    assert grouping.within_the_spread(grouping.Range.of([0.5, 0.6]), grouping.Range.of([0.4, 0.55]))


def test_a_range_of_nothing_is_refused():
    """
    Given  no values at all
    When   a range is made of them
    Then   it is refused, because a range of nothing has no spread
    """
    with pytest.raises(ValueError, match="at least one value"):
        grouping.Range.of([])


def test_a_repeat_without_a_number_has_no_range():
    """
    Given  a group whose second repeat has no F0.5, because nothing was scored there
    When   the range of the deciding mean F0.5 is taken
    Then   there is none, because leaving that repeat out would make the spread look tighter
    """
    texts = fixtures.minimal_texts()
    group = fixtures.group_of(
        [
            [texts[0], texts[1]],
            [fixtures.Text(id="de-01", language="de", band="short", tp=0, fp=0, fn=0)],
            [texts[0], texts[1]],
        ]
    )

    assert grouping.ranges(group.summaries(), grouping.deciding_mean_f_beta) is None
