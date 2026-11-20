"""The new tools against the reports the old ones froze.

The acceptance tests that compared old and new output needed the old package, which the refactor
deleted, so their earlier passes could not be re-checked.  This file keeps the comparison without
the package: the old tools' output is frozen as markdown, and the new tools must print the same
numbers.

Both halves need data outside the repository, so they are skipped unless ``GEC_RESULTS`` names the
old store and ``GEC_REFERENCE`` names the frozen reports::

    bazel test //tests:test_evaluation_reference --test_output=errors \\
      --test_env=GEC_RESULTS=$HOME/gec-testset \\
      --test_env=GEC_REFERENCE=$HOME/gec-testset/reference-reports/2026-11-20

The comparison itself — reading a markdown table, collecting its numbers per column, and naming
what moved — is tested here on tables written in the test, so it runs everywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import re

import pytest

from tools import report

GEC_RESULTS = os.environ.get("GEC_RESULTS")
"""The old store, when the frozen reports are to be compared; never set in CI."""

GEC_REFERENCE = os.environ.get("GEC_REFERENCE")
"""The directory the old tools' output was frozen into; never set in CI."""

needs_reports = pytest.mark.skipif(
    not (GEC_RESULTS and GEC_REFERENCE),
    reason="the old store and the frozen reports are outside the repository; "
    "set GEC_RESULTS and GEC_REFERENCE",
)

STORE = Path(GEC_RESULTS).expanduser() / "store" if GEC_RESULTS else Path()
"""The converted store, which the new tools read."""

REFERENCE = Path(GEC_REFERENCE).expanduser() if GEC_REFERENCE else Path()
"""The frozen reports, which the old tools wrote."""

REPORTS = {
    "sweep": ("sweep.md", 1),
    "passes": ("passes.md", 3),
    "by-error": ("by-error.md", 3),
}
"""Each old command's frozen report, and how many leading cells name one of its rows."""

NEW_COMMANDS = {
    "sweep": ("compare", "--along", "temperature"),
    "passes": ("passes",),
    "by-error": ("table", "--by-error", "category"),
}
"""The new command that answers each old one; ``--store`` is added when it runs."""

ALLOWED_COLUMNS = {
    ("sweep", "Δ F0.5 [95 %]"): "the bootstrap draws each language from its own stream (DX12)",
}
"""A column whose numbers may move, and why.  Every other number must be the old one."""

LEFT_OUT = (
    "results-text/gemma4_e2b__T1__text__r1.header.json",
    "results-text/nemotron-3.5-bolt_e4b__T1__text__r1.header.json",
)
"""The two runs the converter leaves out: the isolated folder holds them again.

The old report counts both copies as repeats of one configuration, so its row for that model holds
one repeat more than the new one.  The row is not compared, and the reason says so.
"""


# ── reading a report ────────────────────────────────────────────────────────

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _cells(line: str) -> list[str]:
    """Return the cells of one markdown table row."""
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_rule(cells: Sequence[str]) -> bool:
    """Whether *cells* is the ``| --- | --- |`` line under a header."""
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _tables(text: str) -> list[tuple[str, list[str], list[list[str]]]]:
    """Return every markdown table of *text*: its heading, its header and its rows."""
    found: list[tuple[str, list[str], list[list[str]]]] = []
    heading = ""
    header: list[str] | None = None
    rows: list[list[str]] = []

    def close() -> None:
        """Keep the table that just ended, if one was open."""
        if header is not None:
            found.append((heading, header, rows.copy()))

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            close()
            heading, header, rows = stripped.lstrip("#").strip(), None, []
            continue
        if not stripped.startswith("|"):
            close()
            header, rows = None, []
            continue
        cells = _cells(stripped)
        if header is None:
            header = cells
        elif not _is_rule(cells):
            rows.append(cells)
    close()
    return found


def _numbers(cell: str) -> list[float]:
    """Return the numbers of one cell, in the order they are printed."""
    return [float(found) for found in _NUMBER.findall(cell)]


def _figures(
    text: str,
    key_cells: int,
    *,
    keep: Callable[[str], bool] | None = None,
) -> dict[tuple[str, ...], dict[str, list[float]]]:
    """Return the numbers of every row of *text*, keyed by its first *key_cells* cells.

    Args:
        text: A report as markdown.
        key_cells: How many leading cells name a row; the rest are figures.
        keep: Which tables to read, by their heading; every table when ``None``.
    """
    found: dict[tuple[str, ...], dict[str, list[float]]] = {}
    for heading, header, rows in _tables(text):
        if keep is not None and not keep(heading):
            continue
        for row in rows:
            figures = found.setdefault(tuple(row[:key_cells]), {})
            for name, cell in zip(header[key_cells:], row[key_cells:], strict=False):
                figures.setdefault(name, []).extend(_numbers(cell))
    return found


def _differences(
    old: Mapping[tuple[str, ...], Mapping[str, list[float]]],
    new: Mapping[tuple[str, ...], Mapping[str, list[float]]],
    *,
    allowed_columns: Mapping[str, str],
    allowed_rows: Mapping[tuple[str, ...], str],
) -> list[str]:
    """Return one line per number that moved, or per row that only one side has.

    Args:
        old: The frozen report's figures.
        new: The new tool's figures.
        allowed_columns: A column that may move, and why.
        allowed_rows: A row that is not compared at all, and why.
    """
    problems: list[str] = []
    for key in sorted(set(old) - set(new)):
        if key not in allowed_rows:
            problems.append(f"{key}: the old report has this row, the new one does not")
    for key in sorted(set(new) - set(old)):
        if key not in allowed_rows:
            problems.append(f"{key}: the new report has this row, the old one does not")
    for key in sorted(set(old) & set(new)):
        if key in allowed_rows:
            continue
        for name, before in old[key].items():
            if name in allowed_columns:
                continue
            after = new[key].get(name)
            if after is None:
                problems.append(f"{key} {name}: the new report has no such column")
            elif after != before:
                problems.append(f"{key} {name}: {before} became {after}")
    return problems


# ── the comparison, on tables written here ──────────────────────────────────

OLD_SWEEP = """## `gemma4:e2b`

| T | Profile | F0.5 | Left alone DE/EN | Δ F0.5 [95 %] |
| --- | --- | --- | --- | --- |
| 0.2 | default | 0.512 | 0.91/0.88 | — |
| 0.7 | default | 0.498 | 0.90/0.87 | -0.014 [-0.031, +0.003] |

Best: 0.2 (default) at F0.5 0.512.

## `qwen3:4b`

| T | Profile | F0.5 | Left alone DE/EN | Δ F0.5 [95 %] |
| --- | --- | --- | --- | --- |
| 0.2 | default | 0.470 | 0.85/0.83 | — |
"""
"""A frozen sweep: one table per model, the model in the heading."""


def test_a_table_is_read_per_column():
    """
    Given  a frozen sweep
    When   its figures are read, the temperature naming each row
    Then   every column holds the numbers of that column, and a dash holds none
    """
    figures = _figures(OLD_SWEEP, 1)

    assert figures[("0.2",)]["F0.5"] == [0.512, 0.470]
    assert figures[("0.2",)]["Left alone DE/EN"] == [0.91, 0.88, 0.85, 0.83]
    assert figures[("0.2",)]["Δ F0.5 [95 %]"] == []


def test_a_heading_selects_one_table():
    """
    Given  a frozen sweep of two models
    When   the figures of one model are read
    Then   only that model's table is read
    """
    figures = _figures(OLD_SWEEP, 1, keep=lambda heading: heading.strip("`") == "qwen3:4b")

    assert figures[("0.2",)]["F0.5"] == [0.470]


def test_an_interval_gives_three_numbers():
    """
    Given  a cell that holds a difference and its interval
    When   its numbers are read
    Then   the difference and both ends are read, in the order they are printed
    """
    assert _numbers("-0.014 [-0.031, +0.003]") == [-0.014, -0.031, 0.003]


def test_a_number_that_moved_is_a_difference():
    """
    Given  the same sweep twice, one number moved
    When   the two are compared
    Then   the moved number is named, with both values
    """
    moved = OLD_SWEEP.replace("| 0.7 | default | 0.498 |", "| 0.7 | default | 0.497 |")

    problems = _differences(
        _figures(OLD_SWEEP, 1), _figures(moved, 1), allowed_columns={}, allowed_rows={}
    )

    assert problems == [("('0.7',) F0.5: [0.498] became [0.497]")]


def test_a_column_on_the_allow_list_is_not_a_difference():
    """
    Given  the same sweep twice, an interval moved
    When   the two are compared with that column allowed
    Then   nothing is reported
    """
    moved = OLD_SWEEP.replace("[-0.031, +0.003]", "[-0.030, +0.004]")

    problems = _differences(
        _figures(OLD_SWEEP, 1),
        _figures(moved, 1),
        allowed_columns={"Δ F0.5 [95 %]": "the bootstrap's stream"},
        allowed_rows={},
    )

    assert problems == []


def test_a_row_only_the_old_report_has_is_a_difference():
    """
    Given  a frozen sweep and a new one that lost a row
    When   the two are compared
    Then   the missing row is named
    """
    shorter = OLD_SWEEP.replace(
        "| 0.7 | default | 0.498 | 0.90/0.87 | -0.014 [-0.031, +0.003] |\n", ""
    )

    problems = _differences(
        _figures(OLD_SWEEP, 1), _figures(shorter, 1), allowed_columns={}, allowed_rows={}
    )

    assert problems == [("('0.7',): the old report has this row, the new one does not")]


def test_an_allowed_row_is_not_compared():
    """
    Given  a frozen sweep and a new one whose row for one model holds one repeat less
    When   the two are compared with that row allowed
    Then   nothing is reported, and the reason is the caller's to give
    """
    shorter = OLD_SWEEP.replace(
        "| 0.7 | default | 0.498 | 0.90/0.87 | -0.014 [-0.031, +0.003] |\n", ""
    )

    problems = _differences(
        _figures(OLD_SWEEP, 1),
        _figures(shorter, 1),
        allowed_columns={},
        allowed_rows={("0.7",): "the converter leaves this run out"},
    )

    assert problems == []


# ── the frozen reports, on the real store ───────────────────────────────────


def _printed(argv: Sequence[str]) -> str:
    """Return what ``report`` prints for *argv*."""
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = report.main(list(argv))
    assert code == 0, f"report {' '.join(argv)} exited {code}"
    return buffer.getvalue()


def _models(text: str) -> list[str]:
    """Return the models a frozen sweep holds, one table each."""
    return [heading.strip("`") for heading, _, _ in _tables(text) if heading]


def _left_out_models() -> set[str]:
    """Return the model tags of the runs the converter leaves out."""
    found: set[str] = set()
    for name in LEFT_OUT:
        header = json.loads((Path(GEC_RESULTS or "").expanduser() / name).read_text("utf-8"))
        found.add(str(header[0]["model"]))
    return found


def _by_error_tables(heading: str) -> bool:
    """Whether *heading* names one of the two tables ``--by-error`` prints."""
    return "errors found exactly" in heading or "Category the model gave" in heading


@needs_reports
def test_the_frozen_reports_still_hold():
    """
    Given  the old store converted into a new one, and the reports the old tools froze
    When   the new tools print the same three reports
    Then   every number is the one the old report holds, except the columns and rows listed above
    """
    left_out = _left_out_models()
    problems: list[str] = []
    for name, (file, key_cells) in REPORTS.items():
        frozen = (REFERENCE / file).read_text(encoding="utf-8")
        allowed_columns = {
            column: why
            for (report_name, column), why in ALLOWED_COLUMNS.items()
            if report_name == name
        }
        if name == "sweep":
            for model in _models(frozen):
                if model in left_out:
                    continue
                old = _figures(
                    frozen, key_cells, keep=lambda heading, m=model: heading.strip("`") == m
                )
                new = _figures(
                    _printed([*NEW_COMMANDS[name], "--model", model, "--store", str(STORE)]),
                    key_cells,
                )
                problems += [
                    f"{name} {model}: {line}"
                    for line in _differences(
                        old, new, allowed_columns=allowed_columns, allowed_rows={}
                    )
                ]
            continue
        keep = _by_error_tables if name == "by-error" else None
        old = _figures(frozen, key_cells, keep=keep)
        new = _figures(_printed([*NEW_COMMANDS[name], "--store", str(STORE)]), key_cells, keep=keep)
        allowed_rows = {
            key: "the converter leaves this run out"
            for key in set(old) | set(new)
            if key[0].strip("`") in left_out
        }
        problems += [
            f"{name}: {line}"
            for line in _differences(
                old, new, allowed_columns=allowed_columns, allowed_rows=allowed_rows
            )
        ]
    assert not problems, "\n".join(problems)
