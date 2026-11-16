"""Tests of the groups a local result folder makes, so gate 2 reads one like a committed run."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval import cache_groups, gate_two, grouping, selection
from tools.model_eval.answer_mode import AnswerMode
from tools.model_eval.viewer import load


def _testset(tmp_path: Path) -> load.TestSet:
    """Write the fixture test set and return it, read as a run reads it."""
    return load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))


def _wanted(**changes) -> selection.Selection:
    """Return a selection of the fixture model, with what a test varies."""
    values = {
        "model": fixtures.MODEL,
        "mode": AnswerMode.EXCHANGE,
        "temperature": 1.0,
    }
    values.update(changes)
    return selection.Selection(**values)


def _groups(tmp_path: Path, *folders: Path) -> list[grouping.RunGroup]:
    """Return the groups the folders hold, read against the fixture test set."""
    testset = _testset(tmp_path)
    return cache_groups.cache_groups([load.read_cache(folder, testset) for folder in folders])


def test_a_folder_makes_one_group_per_repeat(tmp_path):
    """
    Given  two folders, each holding three repeats of one model
    When   their runs are grouped
    Then   each folder makes one group of three repeats, every repeat with every text
    """
    first = fixtures.cache(tmp_path / "first", repeats=3)
    second = fixtures.cache(tmp_path / "second", model=fixtures.OTHER_MODEL, repeats=3)

    found = _groups(tmp_path, first, second)

    assert [group.key.model for group in found] == [fixtures.MODEL, fixtures.OTHER_MODEL]
    assert [group.repeats for group in found] == [(1, 2, 3), (1, 2, 3)]
    assert all(group.complete for group in found)
    assert [len(items) for group in found for items in group.items] == [2] * 6


def test_a_resumed_file_is_one_run_and_not_two(tmp_path):
    """
    Given  a folder whose one file carries two sessions, as a resumed run leaves it
    When   its runs are grouped
    Then   the file is one run of one repeat, because the sessions are one measurement
    """
    folder = tmp_path / "resumed"
    fixtures.write_cache(
        folder,
        "resumed",
        fixtures.header(repeat=1),
        [fixtures.row(text) for text in fixtures.records()],
        headers=[
            fixtures.header(repeat=1, date="2026-11-14T20:00:00+00:00"),
            fixtures.header(repeat=1, date="2026-11-14T23:22:00+00:00"),
        ],
    )

    (found,) = _groups(tmp_path, folder)  # pylint: disable=unbalanced-tuple-unpacking  # one group

    assert found.repeats == (1,)
    assert not found.complete


def test_two_runs_of_one_repeat_are_refused(tmp_path):
    """
    Given  a folder holding two files that both carry repeat 1
    When   its runs are grouped
    Then   the grouping is refused, because a repeat is one run and two would be counted twice
    """
    folder = fixtures.cache(tmp_path / "twice", repeats=1)
    fixtures.write_cache(
        folder,
        "second",
        fixtures.header(repeat=1),
        [fixtures.row(text) for text in fixtures.records()],
    )

    with pytest.raises(ValueError, match="two runs of repeat"):
        _groups(tmp_path, folder)


def test_two_profiles_in_one_folder_ask_for_a_choice(tmp_path):
    """
    Given  a folder holding one model asked with compact JSON on and off
    When   a comparison selects that model
    Then   it refuses and asks for one folder per set of sampling values, instead of picking one
    """
    folder = fixtures.cache(tmp_path / "both", repeats=1)
    fixtures.write_cache(
        folder,
        "compact",
        fixtures.header(repeat=2, compact_json=True),
        [fixtures.row(text) for text in fixtures.records()],
    )

    with pytest.raises(ValueError, match="keep one folder per set of sampling values"):
        selection.select_cache([load.read_cache(folder, _testset(tmp_path))], _wanted())


def test_one_model_at_two_temperatures_is_two_groups(tmp_path):
    """
    Given  one folder holding a model at two temperatures
    When   a comparison names one temperature, and then both
    Then   it reads the group of that temperature, and the refinement reads both
    """
    folder = fixtures.cache(tmp_path / "both", repeats=1)
    fixtures.write_cache(
        folder,
        "cool",
        fixtures.header(repeat=2, temperature=0.5),
        [fixtures.row(text, temperature=0.5) for text in fixtures.records()],
    )
    caches = [load.read_cache(folder, _testset(tmp_path))]

    (cool,) = selection.select_cache(  # pylint: disable=unbalanced-tuple-unpacking  # one group
        caches, _wanted(temperature=0.5)
    )
    pair = selection.select_cache(
        caches, _wanted(temperature=0.5, against=fixtures.MODEL, against_temperature=1.0)
    )

    assert cool.key.temperature == 0.5
    assert [group.key.temperature for group in pair] == [0.5, 1.0]


def test_a_missing_temperature_is_refused(tmp_path):
    """
    Given  a folder holding one model at one temperature
    When   a comparison asks for another temperature
    Then   it refuses and names the temperature and the mode it looked for
    """
    folder = fixtures.cache(tmp_path / "one", repeats=1)

    caches = [load.read_cache(folder, _testset(tmp_path))]

    with pytest.raises(ValueError, match=r"no run of .* at temperature 0.5, exchange mode"):
        selection.select_cache(caches, _wanted(temperature=0.5))


def test_the_corrections_of_two_modes_are_compared_with_corrections(tmp_path):
    """
    Given  an exchange folder and a correct-only folder of one model
    When   the two groups are compared, with and without corrections
    Then   only the comparison with corrections judges them, because step ① is the same request
    """
    exchange = fixtures.cache(tmp_path / "exchange", repeats=3)
    correct_only = fixtures.cache(tmp_path / "correct-only", mode="correct-only", repeats=3)
    caches = [load.read_cache(folder, _testset(tmp_path)) for folder in (exchange, correct_only)]

    first, second = selection.select_cache(  # pylint: disable=unbalanced-tuple-unpacking  # one group per side
        caches,
        _wanted(against=fixtures.MODEL, against_mode=AnswerMode.CORRECT_ONLY),
    )

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.NOT_COMPARABLE
    assert gate_two.compare(first, second, corrections=True).comparison is not (
        gate_two.Comparison.NOT_COMPARABLE
    )


def test_review_against_exchange_stays_not_comparable_with_corrections(tmp_path):
    """
    Given  a review folder and an exchange folder of one model
    When   the two groups are compared with corrections
    Then   they are not comparable, because review answers another question than step ①
    """
    review = fixtures.cache(tmp_path / "review", mode="review", repeats=3)
    exchange = fixtures.cache(tmp_path / "exchange", repeats=3)
    caches = [load.read_cache(folder, _testset(tmp_path)) for folder in (review, exchange)]

    first, second = selection.select_cache(  # pylint: disable=unbalanced-tuple-unpacking  # one group per side
        caches,
        _wanted(
            mode=AnswerMode.REVIEW,
            against=fixtures.MODEL,
            against_mode=AnswerMode.EXCHANGE,
        ),
    )

    assert gate_two.compare(first, second, corrections=True).comparison is (
        gate_two.Comparison.NOT_COMPARABLE
    )


def test_a_folder_that_corrected_nothing_is_worse(tmp_path):
    """
    Given  a folder whose model answered with the reference and one whose model changed nothing
    When   the two groups are compared
    Then   the first is better, so a folder is judged by the same rules as a committed run
    """
    good = fixtures.cache(tmp_path / "good", repeats=3)
    bad = fixtures.cache(tmp_path / "bad", model=fixtures.OTHER_MODEL, repeats=3, unchanged=True)
    caches = [load.read_cache(folder, _testset(tmp_path)) for folder in (good, bad)]

    first, second = selection.select_cache(  # pylint: disable=unbalanced-tuple-unpacking  # one group per side
        caches, _wanted(against=fixtures.OTHER_MODEL)
    )

    assert gate_two.compare(first, second).comparison is gate_two.Comparison.BETTER
