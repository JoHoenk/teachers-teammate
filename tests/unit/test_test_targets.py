"""The test targets of ``tests/BUILD``: one per test file, each tagged by its folder.

A suite in ``tests/BUILD`` takes its tests by tag, so nothing lists them one by one any more. What
keeps that honest is this test: a file no target runs, a target that runs no file, or a tag that
names another folder than the file's would otherwise fall out of every suite silently.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

#: The tags that name a folder of ``tests/``: one suite per folder takes every test that has it.
FOLDER_TAGS = ("unit", "gui", "integration", "tools")

#: ``tests/BUILD`` as Bazel's runfiles see it, or in the source tree when the test runs directly.
_BUILD = (
    Path(os.environ["TEST_SRCDIR"]) / os.environ["TESTS_BUILD"]
    if os.environ.get("TEST_SRCDIR") and os.environ.get("TESTS_BUILD")
    else Path(__file__).resolve().parents[1] / "BUILD"
)


def _targets() -> dict[str, tuple[str, tuple[str, ...]]]:
    """Return ``name → (the file it runs, its tags)`` of every ``py_test`` of the package."""
    found: dict[str, tuple[str, tuple[str, ...]]] = {}
    for node in ast.parse(_BUILD.read_text(encoding="utf-8")).body:
        call = getattr(node, "value", None)
        if not isinstance(call, ast.Call) or getattr(call.func, "id", "") != "py_test":
            continue
        keywords = {one.arg: one.value for one in call.keywords}
        name = ast.literal_eval(keywords["name"])
        args = ast.literal_eval(keywords["args"])
        tags = tuple(ast.literal_eval(keywords["tags"])) if "tags" in keywords else ()
        found[name] = (args[0] if args else "", tags)
    return found


def _suites() -> dict[str, tuple[str, ...]]:
    """Return ``name → tags`` of every ``test_suite`` that takes its tests by tag."""
    found: dict[str, tuple[str, ...]] = {}
    for node in ast.parse(_BUILD.read_text(encoding="utf-8")).body:
        call = getattr(node, "value", None)
        if not isinstance(call, ast.Call) or getattr(call.func, "id", "") != "test_suite":
            continue
        keywords = {one.arg: one.value for one in call.keywords}
        if "tags" in keywords:
            found[ast.literal_eval(keywords["name"])] = tuple(ast.literal_eval(keywords["tags"]))
    return found


def test_every_test_file_is_run_by_exactly_one_target() -> None:
    """
    Given  the test files of ``tests/`` and the ``py_test`` targets of its BUILD file
    When   each file is looked for among the files the targets run
    Then   every file has exactly one target, and every target runs a file that exists
    """
    runs: dict[str, list[str]] = {}
    for name, (file, _tags) in _targets().items():
        runs.setdefault(file, []).append(name)
    files = {
        path.relative_to(_BUILD.parent).as_posix() for path in _BUILD.parent.rglob("test_*.py")
    }

    assert sorted(set(runs) - files) == [], "these targets run no test file"
    assert sorted(files - set(runs)) == [], "these test files no target runs"
    assert {file: names for file, names in runs.items() if len(names) > 1} == {}, (
        "these test files are run by more than one target"
    )


def test_every_target_carries_the_tag_of_its_folder() -> None:
    """
    Given  every ``py_test`` of ``tests/BUILD``
    When   its tags are held against the folder of the file it runs
    Then   it carries exactly that folder's tag, so its folder's suite runs it
    """
    wrong = {
        name: (file, tags)
        for name, (file, tags) in _targets().items()
        if tuple(tag for tag in tags if tag in FOLDER_TAGS) != (file.split("/")[0],)
    }

    assert wrong == {}, "these targets carry no folder tag, or one of another folder"


def test_every_folder_has_its_suite() -> None:
    """
    Given  the folder tags the targets carry
    When   the suites that take their tests by tag are read
    Then   each folder has one, so no folder's tests fall out of every suite
    """
    folders = {tag for _file, tags in _targets().values() for tag in tags if tag in FOLDER_TAGS}
    suites = {tag for tags in _suites().values() for tag in tags if tag in FOLDER_TAGS}

    assert suites == folders
