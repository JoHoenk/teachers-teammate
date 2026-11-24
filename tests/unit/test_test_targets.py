"""The test targets of ``tests/BUILD``: one ``app_test()`` call per test file.

A suite in ``tests/BUILD`` takes its tests by tag, so no suite lists its tests one by one, and
``app_test()`` (``tests/defs.bzl``) derives the target's name and its folder tag from the file it
runs. What keeps that honest is this test: a file no target runs, a target that runs a file that is
not there, or a call that writes a folder tag itself would otherwise fall out of every suite
silently.
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


def _name(src: str) -> str:
    """Return the target's name, as the macro derives it from the file."""
    return src.rsplit("/", maxsplit=1)[-1][: -len(".py")]


def _calls() -> dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]]:
    """Return ``name → (the file it runs, its extra sources, its further tags)``."""
    found: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {}
    for node in ast.parse(_BUILD.read_text(encoding="utf-8")).body:
        call = getattr(node, "value", None)
        if not isinstance(call, ast.Call) or getattr(call.func, "id", "") != "app_test":
            continue
        keywords = {one.arg: one.value for one in call.keywords}
        src = ast.literal_eval(call.args[0])
        extra = keywords.get("extra_srcs")
        tags = keywords.get("tags")
        found[_name(src)] = (
            src,
            tuple(ast.literal_eval(extra)) if extra else (),
            tuple(ast.literal_eval(tags)) if tags else (),
        )
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
    Given  the test files of ``tests/`` and the ``app_test()`` calls of its BUILD file
    When   each file is looked for among the files the calls run
    Then   every file has exactly one target, and no target runs a file that is not there
    """
    files = {
        path.relative_to(_BUILD.parent).as_posix() for path in _BUILD.parent.rglob("test_*.py")
    }
    runs = {src for src, _extra, _tags in _calls().values()}

    assert sorted(runs - files) == [], "these targets run no test file"
    assert sorted(files - runs) == [], "these test files no target runs"


def test_no_target_writes_a_folder_tag_itself() -> None:
    """
    Given  every ``app_test()`` call of ``tests/BUILD``
    When   its tags are read
    Then   none names a folder: the macro derives that one from the file's folder, so the test
           lands in its own folder's suite and in no other
    """
    wrong = {
        name: tags
        for name, (_src, _extra, tags) in _calls().items()
        if set(tags) & set(FOLDER_TAGS)
    }

    assert wrong == {}, "these calls write a folder tag the macro already adds"


def test_every_folder_has_its_suite() -> None:
    """
    Given  the folder tags the suites of ``tests/BUILD`` select on
    When   they are held against the folders of the test files
    Then   each folder has one, so no folder's tests fall out of every suite
    """
    folders = {
        path.relative_to(_BUILD.parent).as_posix().split("/")[0]
        for path in _BUILD.parent.rglob("test_*.py")
    }
    suites = {tag for tags in _suites().values() for tag in tags if tag in FOLDER_TAGS}

    assert suites == folders
