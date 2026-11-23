"""Unit tests for the groups CI splits the tests into (``tools/ci/target_groups.py``)."""

from __future__ import annotations

import json

import pytest

from tools.ci import target_groups
from tools.ci.target_groups import ALL_TESTS, CLAIMS, GROUPS

EVERYTHING = [
    "//tests:test_domain",
    "//tests:test_state",
    "//tests:test_queue",
    "//tests:test_gui_window",
    "//tests:test_gui_walkthrough",
]


def _answers(**by_group: list[str]) -> target_groups.Query:
    """Return a stand-in for ``bazel query`` that answers the query of each group it was taught."""
    known = {ALL_TESTS: EVERYTHING}
    known.update({CLAIMS[group]: targets for group, targets in by_group.items()})

    def query(expression: str) -> list[str]:
        return known.get(expression, [])

    return query


def test_every_target_is_in_exactly_one_group() -> None:
    """
    Given  tests that are walkthroughs, depend on the GUI layer, on the application layer (the
           GUI ones among them) or on neither
    When   they are split
    Then   each target is in exactly one group and the first group that claims it wins
    """
    found = target_groups.groups(
        _answers(
            walkthrough=["//tests:test_gui_walkthrough"],
            gui=["//tests:test_gui_window", "//tests:test_gui_walkthrough"],
            application=[
                "//tests:test_queue",
                "//tests:test_gui_window",
                "//tests:test_gui_walkthrough",
            ],
        )
    )

    assert found == {
        "walkthrough": ["//tests:test_gui_walkthrough"],
        "gui": ["//tests:test_gui_window"],
        "application": ["//tests:test_queue"],
        "rest": ["//tests:test_domain", "//tests:test_state"],
    }
    flat = [target for targets in found.values() for target in targets]
    assert sorted(flat) == sorted(EVERYTHING)


def test_a_target_no_query_names_still_runs() -> None:
    """
    Given  a new test that no group's query lists
    When   the targets are split
    Then   it is in the last group, so it runs on one of the machines
    """
    found = target_groups.partition(
        [*EVERYTHING, "//tests:test_brand_new"], {"gui": ["//tests:test_gui_window"]}
    )

    assert "//tests:test_brand_new" in found[GROUPS[-1]]


def test_a_query_cannot_add_a_target_the_wildcard_does_not_run() -> None:
    """
    Given  a group query that lists a target outside the test targets of //tests/...
    When   the targets are split
    Then   that target is in no group
    """
    found = target_groups.partition(EVERYTHING, {"gui": ["//tests:a_manual_test"]})

    assert all("//tests:a_manual_test" not in targets for targets in found.values())


@pytest.mark.parametrize("empty", ["walkthrough", "gui", "application"])
def test_an_empty_group_stops_the_run(empty: str) -> None:
    """
    Given  a group that no test is in any more (a renamed layer library, a lost tag)
    When   the groups are built
    Then   it stops and names the empty group, so its machine never passes by running nothing
    """
    answers = {
        "walkthrough": ["//tests:test_gui_walkthrough"],
        "gui": ["//tests:test_gui_window"],
        "application": ["//tests:test_queue"],
    }
    del answers[empty]

    with pytest.raises(SystemExit, match=empty):
        target_groups.groups(_answers(**answers))


def test_the_last_group_is_the_complement() -> None:
    """
    Given  the groups the script knows
    When   their claims are read
    Then   only the last one has no query, so it alone takes what the others leave
    """
    assert [group for group, expression in CLAIMS.items() if expression is None] == [GROUPS[-1]]


def test_the_matrix_lists_every_group_for_the_workflow(capsys: pytest.CaptureFixture) -> None:
    """
    Given  the groups the script knows
    When   it is asked for the matrix
    Then   it prints their names as a JSON list, so the workflow never names a group itself
    """
    target_groups.main(["--matrix"])

    assert json.loads(capsys.readouterr().out) == list(GROUPS)


def test_the_main_function_prints_one_group_one_target_per_line(
    capsys: pytest.CaptureFixture,
) -> None:
    """
    Given  the targets of the groups
    When   one group is asked for
    Then   its targets are printed one per line, the form ``--target_pattern_file`` reads
    """
    query = _answers(
        walkthrough=["//tests:test_gui_walkthrough"],
        gui=["//tests:test_gui_window"],
        application=["//tests:test_queue", "//tests:test_gui_window"],
    )

    code = target_groups.main(["application"], query=query)

    assert code == 0
    assert capsys.readouterr().out.split() == ["//tests:test_queue"]


def test_asking_for_nothing_is_an_error() -> None:
    """
    Given  neither a group nor ``--matrix``
    When   the script runs
    Then   it exits with the usage error instead of printing nothing
    """
    with pytest.raises(SystemExit) as stopped:
        target_groups.main([])

    assert stopped.value.code == 2
