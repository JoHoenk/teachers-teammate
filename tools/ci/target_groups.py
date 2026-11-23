#!/usr/bin/env python3
"""Split the test targets of ``//tests/...`` into the groups CI runs on separate machines.

``tests.yml`` asks this script for the group names (``--matrix``) and runs one leg per group with
that group's targets, so the legs together are exactly ``bazel test //tests/...`` and a maintainer
can list or run one group on a laptop:

    python tools/ci/target_groups.py gui
    bazel test --target_pattern_file=<(python tools/ci/target_groups.py application)

A group is cut so that a change leaves the other groups' tests as they were, which is what keeps
their Bazel caches warm:

* ``walkthrough``: the tests of ``//tests:walkthrough_tests`` (tag ``walkthrough``), the slowest
  and the ones that grow with the app.
* ``gui``: what depends on the GUI layer.
* ``application``: what depends on the application layer and not on the GUI.
* ``rest``: everything else, so a new test can only land in a group, never fall between them.

Only ``walkthrough`` and ``gui`` import Qt (the layering keeps it in ``gui/``), so only their
machines install the Qt system library.

A test is in the first group, in this order, that claims it.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import json
import subprocess
import sys

GUI_LIB = "//teachers_teammate:gui_layer_lib"
APPLICATION_LIB = "//teachers_teammate:application_lib"

#: The test targets ``bazel test //tests/...`` runs: ``tests()`` also lists ``manual`` ones, which
#: the wildcard skips.
ALL_TESTS = 'tests(//tests/...) except attr("tags", "manual", //tests/...)'

Query = Callable[[str], list[str]]


def depending_on(library: str) -> str:
    """Return the query for the tests that depend, directly or not, on *library*."""
    return f"rdeps(//tests/..., {library}) intersect ({ALL_TESTS})"


#: What each group claims, in the order a test is given to the first that claims it. The last
#: group has no query: it is whatever the others left.
CLAIMS: dict[str, str | None] = {
    "walkthrough": f"tests(//tests:walkthrough_tests) intersect ({ALL_TESTS})",
    "gui": depending_on(GUI_LIB),
    "application": depending_on(APPLICATION_LIB),
    "rest": None,
}
GROUPS = tuple(CLAIMS)

#: The groups whose machines need the Qt system library.
QT_GROUPS = ("walkthrough", "gui")


def partition(
    everything: Sequence[str], claimed: Mapping[str, Sequence[str]]
) -> dict[str, list[str]]:
    """Return every target of *everything* in exactly one group.

    *claimed* maps a group to the targets its query found. A target goes to the first group in
    ``GROUPS`` that claims it; whatever none claims is in the last group, so a target no query has
    heard of still runs, and a query cannot add one the wildcard does not run.
    """
    left = set(everything)
    found: dict[str, list[str]] = {}
    for group in GROUPS[:-1]:
        taken = left & set(claimed.get(group, ()))
        found[group] = sorted(taken)
        left -= taken
    found[GROUPS[-1]] = sorted(left)
    return found


def groups(query: Query) -> dict[str, list[str]]:
    """Return the groups of the test targets *query* finds, and fail if one of them is empty.

    An empty group means a layer no test depends on any more or a lost ``walkthrough`` tag: its
    leg would run nothing and pass, so this stops before that.
    """
    claimed = {group: query(expression) for group, expression in CLAIMS.items() if expression}
    found = partition(query(ALL_TESTS), claimed)
    empty = [name for name, targets in found.items() if not targets]
    if empty:
        raise SystemExit(
            f"no test target in the group(s) {', '.join(empty)}: check the layer libraries"
            " and the walkthrough tag"
        )
    return found


def bazel_query(expression: str, *, config: str = "") -> list[str]:
    """Return the labels ``bazel query`` finds for *expression*."""
    command = [
        "bazel",
        "query",
        *([f"--config={config}"] if config else []),
        "--output=label",
        expression,
    ]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"bazel query failed ({done.returncode}):\n{done.stderr}")
    return done.stdout.split()


def matrix() -> dict[str, list[dict[str, object]]]:
    """Return the Actions matrix: one entry per group, and whether its machine needs Qt."""
    return {"include": [{"group": group, "qt": group in QT_GROUPS} for group in GROUPS]}


def main(argv: Sequence[str] | None = None, *, query: Query | None = None) -> int:
    """Print the Actions matrix as JSON (``--matrix``) or the targets of one group."""
    parser = argparse.ArgumentParser(description="List the test targets of one CI group.")
    parser.add_argument("group", nargs="?", choices=GROUPS)
    parser.add_argument("--matrix", action="store_true", help="print the Actions matrix as JSON")
    parser.add_argument("--config", default="", help="the Bazel --config to query with")
    options = parser.parse_args(argv)
    if options.matrix:
        print(json.dumps(matrix()))
        return 0
    if options.group is None:
        parser.error("name a group or pass --matrix")
    ask = query or (lambda expression: bazel_query(expression, config=options.config))
    print("\n".join(groups(ask)[options.group]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
