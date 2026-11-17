"""Unit tests for carrying offsets over a text whose runs were replaced (``domain/offset_map.py``)."""

from __future__ import annotations

from teachers_teammate.domain.offset_map import OffsetMap


def _map(*runs: tuple[int, int, int, int]) -> OffsetMap:
    """Return an offset map of *runs*."""
    return OffsetMap(tuple(runs))


def test_a_position_before_a_run_does_not_move() -> None:
    """
    Given  a text whose run 5..10 grew by three characters
    When   a position before that run is carried over
    Then   it stays where it was
    """
    offsets = _map((5, 10, 5, 13))

    assert (offsets.start(2), offsets.end(2)) == (2, 2)


def test_a_position_after_a_growing_run_moves_by_the_difference() -> None:
    """
    Given  a text whose run 5..10 grew by three characters
    When   a position after that run is carried over
    Then   it moves by the three characters the run gained
    """
    offsets = _map((5, 10, 5, 13))

    assert (offsets.start(12), offsets.end(12)) == (15, 15)


def test_a_position_after_a_shrinking_run_moves_back() -> None:
    """
    Given  a text whose run 5..10 shrank by four characters
    When   a position after that run is carried over
    Then   it moves back by the four characters the run lost
    """
    offsets = _map((5, 10, 5, 6))

    assert (offsets.start(12), offsets.end(12)) == (8, 8)


def test_a_position_inside_a_run_widens_to_the_replacement() -> None:
    """
    Given  a text whose run 5..10 became 5..13
    When   a position inside that run is carried over
    Then   it begins at the replacement's start and ends at its end
    """
    offsets = _map((5, 10, 5, 13))

    assert (offsets.start(7), offsets.end(7)) == (5, 13)


def test_a_position_at_a_runs_end_is_already_past_it() -> None:
    """
    Given  a text whose run 5..10 grew by three characters
    When   the position at the run's end is carried over
    Then   it is past the run, so it moves by the difference and stays zero-width
    """
    offsets = _map((5, 10, 5, 13))

    assert (offsets.start(10), offsets.end(10)) == (13, 13)


def test_several_runs_add_up() -> None:
    """
    Given  a text with two replaced runs, one growing and one shrinking
    When   a position after both is carried over
    Then   it moves by the sum of both differences
    """
    offsets = _map((2, 4, 2, 8), (10, 14, 14, 15))

    assert (offsets.start(20), offsets.end(20)) == (21, 21)


def test_an_empty_map_moves_nothing() -> None:
    """
    Given  a text nothing was replaced in
    When   a position is carried over
    Then   it stays where it was
    """
    offsets = OffsetMap()

    assert (offsets.start(3), offsets.end(9)) == (3, 9)


def test_a_span_that_ends_where_a_run_begins_stops_before_the_replacement() -> None:
    """
    Given  a text whose run 13..23 became 13..24 (a placeholder replaced by a longer name)
    When   a span that ends exactly where the run begins is carried over
    Then   it ends before the replacement, and a span that begins there begins at its start
    """
    offsets = _map((13, 23, 13, 24))

    assert offsets.end(13) == 13
    assert offsets.start(13) == 13


def test_a_span_that_ends_inside_a_run_still_covers_the_whole_replacement() -> None:
    """
    Given  a text whose run 13..23 became 13..24
    When   a span that ends one character into the run is carried over
    Then   it ends at the replacement's end
    """
    offsets = _map((13, 23, 13, 24))

    assert offsets.end(14) == 24


def test_positions_between_many_runs_add_up_the_runs_before_them() -> None:
    """
    Given  a text with three replaced runs, each longer than the placeholder it replaced
    When   positions between and after the runs are carried over
    Then   each moves by the runs that end at or before it, and no more
    """
    offsets = _map((2, 4, 2, 5), (10, 12, 11, 15), (20, 22, 23, 26))

    assert [offsets.start(at) for at in (0, 4, 9, 12, 19, 22, 30)] == [0, 5, 10, 15, 22, 26, 34]
    assert [offsets.end(at) for at in (0, 4, 10, 12, 20, 22, 30)] == [0, 5, 11, 15, 23, 26, 34]
