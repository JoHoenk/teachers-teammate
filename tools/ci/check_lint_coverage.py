#!/usr/bin/env python3
"""Hold the lint set to the Python files of the first-party trees.

``//:py_ruff`` and ``//:py_ty`` visit the libraries the root ``BUILD``'s ``_LINTED`` names, and
nothing else lints Python. A package whose files are in no library of that list drops out of the
lint run without a word, so the lint job runs this after the linters:

    python3 tools/ci/check_lint_coverage.py

A file of ``//teachers_teammate``, ``//tools`` or ``//tests`` that no target of the set holds turns
the job red, and so does a label the set names that holds nothing.
"""

from __future__ import annotations

import argparse
import ast
from collections.abc import Callable, Iterable
import json
from pathlib import Path
import subprocess
import sys

#: The trees whose Python is linted.
TREES = ("//teachers_teammate", "//tools", "//tests")

#: Every ``py_*`` rule of those trees: the lint set's libraries and everything they must cover.
EXPRESSION = 'kind("py_.*", ' + " + ".join(f"{tree}/..." for tree in TREES) + ")"

Rule = dict[str, object]
Query = Callable[[str], list[Rule]]


def linted_labels(build: Path) -> list[str]:
    """Return the labels the lint set of *build* names."""
    for node in ast.parse(build.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", "") == "_LINTED" for target in node.targets
        ):
            return list(ast.literal_eval(node.value))
    raise SystemExit(f"{build}: no _LINTED list")


def sources(rules: Iterable[Rule]) -> dict[str, set[str]]:
    """Return ``label → the Python files it holds`` for *rules*."""
    found: dict[str, set[str]] = {}
    for rule in rules:
        attributes = {one["name"]: one for one in rule["attribute"]}  # ty: ignore[not-iterable]
        held = attributes.get("srcs", {}).get("stringListValue", [])
        found[str(rule["name"])] = {
            one for one in held if one.startswith("//") and one.endswith(".py")
        }
    return found


def gaps(held: dict[str, set[str]], linted: list[str]) -> tuple[list[str], list[str]]:
    """Return the files no lint target holds, and the lint targets that hold nothing."""
    covered: set[str] = set()
    for label in linted:
        covered |= held.get(label, set())
    lost = sorted({one for files in held.values() for one in files} - covered)
    empty = sorted(label for label in linted if not held.get(label))
    return lost, empty


def bazel_rules(expression: str) -> list[Rule]:
    """Return the rules ``bazel query`` finds for *expression*."""
    command = ["bazel", "query", "--output=streamed_jsonproto", expression]
    done = subprocess.run(command, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise SystemExit(f"bazel query failed ({done.returncode}):\n{done.stderr}")
    return [json.loads(line)["rule"] for line in done.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None, *, query: Query | None = None) -> int:
    """Fail when a Python file of the three trees is in no target of the lint set."""
    parser = argparse.ArgumentParser(description="Check that every Python file is linted.")
    parser.add_argument("--build", type=Path, default=Path("BUILD"), help="the root BUILD file")
    options = parser.parse_args(argv)
    ask = query or bazel_rules
    held = sources(ask(EXPRESSION))
    lost, empty = gaps(held, linted_labels(options.build))
    for label in empty:
        print(f"the lint set names {label}, which holds no Python file", file=sys.stderr)
    for one in lost:
        print(f"in no lint target: {one}", file=sys.stderr)
    if lost or empty:
        print("name each package's library in _LINTED in the root BUILD", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
