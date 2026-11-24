"""The guard that holds the lint set of the root BUILD to the Python files of the three trees."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.ci import check_lint_coverage


def _rule(label: str, *srcs: str) -> dict[str, object]:
    """Return one rule of a ``bazel query --output=streamed_jsonproto`` line, cut to its sources."""
    return {"name": label, "attribute": [{"name": "srcs", "stringListValue": list(srcs)}]}


#: What the query answers for two packages, one of them in the lint set.
RECORDED = [
    _rule("//tools/ci:lint_lib", "//tools/ci/__init__.py", "//tools/ci/new_tool.py"),
    _rule("//tools/new:new_lib", "//tools/new/__init__.py", "//tools/new/new_tool.py"),
]


def _build(tmp_path: Path, linted: list[str]) -> Path:
    """Return a BUILD file that names *linted* as its lint set."""
    build = tmp_path / "BUILD"
    build.write_text(f"_LINTED = {linted!r}\n", encoding="utf-8")
    return build


def test_a_file_of_no_lint_target_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a package whose source no target of the lint set holds
    When   the guard runs
    Then   it names that file and fails, so the missing entry cannot go unnoticed
    """
    build = _build(tmp_path, ["//tools/ci:lint_lib"])

    code = check_lint_coverage.main(["--build", str(build)], query=lambda _expression: RECORDED)

    assert code == 1
    assert "in no lint target: //tools/new/new_tool.py" in capsys.readouterr().err


def test_a_file_of_a_lint_target_is_not_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a lint set that holds every file of the two packages
    When   the guard runs
    Then   it says nothing and succeeds
    """
    build = _build(tmp_path, ["//tools/ci:lint_lib", "//tools/new:new_lib"])

    code = check_lint_coverage.main(["--build", str(build)], query=lambda _expression: RECORDED)

    assert code == 0
    assert capsys.readouterr().err == ""


def test_a_lint_target_that_holds_nothing_is_reported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a lint set that names a target which does not exist
    When   the guard runs
    Then   it names that target and fails, so a mistyped entry does not silently lint nothing
    """
    build = _build(tmp_path, ["//tools/ci:lint_lib", "//tools/ci:gone"])

    code = check_lint_coverage.main(["--build", str(build)], query=lambda _expression: RECORDED)

    assert code == 1
    assert "the lint set names //tools/ci:gone" in capsys.readouterr().err


def test_the_query_asks_for_the_rules_of_the_three_trees() -> None:
    """
    Given  the guard's query
    When   it is read
    Then   it names the trees whose Python is linted, so a fourth one cannot be looked past
    """
    assert "//teachers_teammate/..." in check_lint_coverage.EXPRESSION
    assert "//tools/..." in check_lint_coverage.EXPRESSION
    assert "//tests/..." in check_lint_coverage.EXPRESSION
