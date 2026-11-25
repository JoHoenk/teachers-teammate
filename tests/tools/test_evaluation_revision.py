"""The commit a run records: read from the checkout, and from its files when git cannot answer.

A run says which tools made it.  ``git`` is the quick way to that commit; on a checkout another
user owns, or where the tools were copied instead of linked, git refuses and the same commit has to
come out of the repository's own files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tools.evaluation.core.revision import UNKNOWN, UNVERIFIED, tool_revision

SHA = "0123456789abcdef0123456789abcdef01234567"
"""A full commit, of which the short form is its first seven characters."""


def _checkout(root: Path) -> Path:
    """Make *root* look like the tools' checkout, and return the folder to start the search in."""
    (root / "tools" / "evaluation" / "core").mkdir(parents=True)
    (root / "tools" / "evaluation" / "core" / "revision.py").write_text("", encoding="utf-8")
    return root / "tools" / "evaluation" / "core"


def _head(root: Path, ref: str = "refs/heads/main", commit: str = SHA) -> None:
    """Write ``.git/HEAD`` naming *ref*, and *ref* itself with *commit*."""
    (root / ".git" / ref).parent.mkdir(parents=True, exist_ok=True)
    (root / ".git" / "HEAD").write_text(f"ref: {ref}\n", encoding="utf-8")
    (root / ".git" / ref).write_text(f"{commit}\n", encoding="utf-8")


def _git_says(commit: str = "abc1234", status: str = "") -> Callable[..., Any]:
    """Return a stand-in for ``subprocess.run`` that answers like a working git."""

    def run(args, **_):
        out = {("git", "rev-parse", "--short", "HEAD"): commit + "\n"}.get(
            tuple(args), status and status + "\n"
        )
        return SimpleNamespace(returncode=0 if out is not None else 128, stdout=out or "")

    return run


def _git_is_gone(args, **_):
    """Stand in for ``subprocess.run`` on a machine without git."""
    raise OSError("git not found")


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_revision_names_the_commit_and_marks_uncommitted_changes(tmp_path: Path) -> None:
    """Given a checkout whose working tree has an uncommitted change,
    When the revision is asked for,
    Then it is the short commit with +dirty.
    """
    start = _checkout(tmp_path / "app")
    _head(tmp_path / "app")

    assert tool_revision(_git_says(status=" M tools/x.py"), start=start) == "abc1234+dirty"


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_clean_checkout_gets_the_commit_alone(tmp_path: Path) -> None:
    """Given a checkout with nothing uncommitted,
    When the revision is asked for,
    Then it is the short commit and no more.
    """
    start = _checkout(tmp_path / "app")
    _head(tmp_path / "app")

    assert tool_revision(_git_says(), start=start) == "abc1234"


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_revision_comes_out_of_the_repository_when_git_fails(tmp_path: Path) -> None:
    """Given a checkout git refuses to read, as one owned by another user or on a mounted disk,
    When the revision is asked for,
    Then the commit comes out of the repository's own files, marked as unverified: git could not
    say whether the tree is clean.
    """
    start = _checkout(tmp_path / "app")
    _head(tmp_path / "app")

    assert tool_revision(_git_is_gone, start=start) == SHA[:7] + UNVERIFIED


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_packed_reference_is_read_as_well(tmp_path: Path) -> None:
    """Given a checkout whose branch is only in packed-refs, as a clone leaves it,
    When the revision is asked for without git,
    Then the packed commit is the revision.
    """
    start = _checkout(tmp_path / "app")
    (tmp_path / "app" / ".git").mkdir()
    (tmp_path / "app" / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (tmp_path / "app" / ".git" / "packed-refs").write_text(
        f"# pack-refs with: peeled fully-peeled sorted\n{SHA} refs/heads/main\n", encoding="utf-8"
    )

    assert tool_revision(_git_is_gone, start=start) == SHA[:7] + UNVERIFIED


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_detached_head_is_a_commit_of_its_own(tmp_path: Path) -> None:
    """Given a checkout on no branch,
    When the revision is asked for without git,
    Then the commit HEAD holds is the revision.
    """
    start = _checkout(tmp_path / "app")
    (tmp_path / "app" / ".git").mkdir()
    (tmp_path / "app" / ".git" / "HEAD").write_text(f"{SHA}\n", encoding="utf-8")

    assert tool_revision(_git_is_gone, start=start) == SHA[:7] + UNVERIFIED


@pytest.mark.use_case("Model_Eval_Measure")
@pytest.mark.parametrize("packed", [False, True])
def test_a_worktree_finds_its_branch_in_the_main_repository(tmp_path: Path, packed: bool) -> None:
    """Given a linked worktree, whose own git folder holds HEAD and whose branches are in the main
    repository, loose or packed,
    When the revision is asked for without git,
    Then the commit comes from the main repository's branch.
    """
    start = _checkout(tmp_path / "worktree")
    main = tmp_path / "main" / ".git"
    repo = main / "worktrees" / "worktree"
    repo.mkdir(parents=True)
    (repo / "HEAD").write_text("ref: refs/heads/joho_topic\n", encoding="utf-8")
    (repo / "commondir").write_text("../..\n", encoding="utf-8")
    if packed:
        (main / "packed-refs").write_text(f"{SHA} refs/heads/joho_topic\n", encoding="utf-8")
    else:
        (main / "refs" / "heads").mkdir(parents=True)
        (main / "refs" / "heads" / "joho_topic").write_text(f"{SHA}\n", encoding="utf-8")
    (tmp_path / "worktree" / ".git").write_text(f"gitdir: {repo}\n", encoding="utf-8")

    assert tool_revision(_git_is_gone, start=start) == SHA[:7] + UNVERIFIED


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_checkout_is_found_above_the_module(tmp_path: Path) -> None:
    """Given a start folder several levels below the tools' checkout,
    When the revision is asked for,
    Then the search walks up to the checkout.
    """
    deep = _checkout(tmp_path / "app") / "a" / "b"
    deep.mkdir(parents=True)
    _head(tmp_path / "app")

    assert tool_revision(_git_says(), start=deep) == "abc1234"


@pytest.mark.use_case("Model_Eval_Measure")
def test_another_repository_is_not_the_tools_checkout(tmp_path: Path, monkeypatch) -> None:
    """Given a copy of the tools inside another repository, such as the store,
    When the revision is asked for,
    Then no commit is named rather than the other repository's.
    """
    store = tmp_path / "store"
    (store / ".git" / "refs" / "heads").mkdir(parents=True)
    (store / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (store / ".git" / "refs" / "heads" / "main").write_text(f"{SHA}\n", encoding="utf-8")
    start = _checkout(store / "copy")
    monkeypatch.chdir(store / "copy")
    monkeypatch.delenv("BUILD_WORKSPACE_DIRECTORY", raising=False)

    assert tool_revision(_git_says(), start=start) == UNKNOWN


@pytest.mark.use_case("Model_Eval_Measure")
def test_git_that_cannot_read_the_status_leaves_the_commit_unverified(tmp_path: Path) -> None:
    """Given a git that names the commit but refuses the status,
    When the revision is asked for,
    Then the commit is marked unverified instead of passing for a clean tree.
    """
    start = _checkout(tmp_path / "app")
    _head(tmp_path / "app")

    def run(args, **_):
        if "status" in args:
            return SimpleNamespace(returncode=128, stdout="")
        return SimpleNamespace(returncode=0, stdout="abc1234\n")

    assert tool_revision(run, start=start) == "abc1234" + UNVERIFIED


@pytest.mark.use_case("Model_Eval_Measure")
def test_a_copied_tree_without_a_checkout_names_no_commit(tmp_path: Path, monkeypatch) -> None:
    """Given a packaged copy of the tools and no checkout anywhere,
    When the revision is asked for,
    Then it is "unknown" instead of an error.
    """
    start = _checkout(tmp_path / "copy")
    monkeypatch.chdir(tmp_path / "copy")
    monkeypatch.delenv("BUILD_WORKSPACE_DIRECTORY", raising=False)

    assert tool_revision(_git_is_gone, start=start) == UNKNOWN


@pytest.mark.use_case("Model_Eval_Measure")
def test_the_workspace_bazel_names_is_searched_too(tmp_path: Path, monkeypatch) -> None:
    """Given a copy of the tools outside the checkout and the workspace Bazel names,
    When the revision is asked for,
    Then the checkout in that workspace is found.
    """
    workspace = tmp_path / "app"
    _checkout(workspace)
    _head(workspace)
    start = _checkout(tmp_path / "copy")
    monkeypatch.chdir(tmp_path / "copy")
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace))

    assert tool_revision(_git_says(), start=start) == "abc1234"


@pytest.mark.use_case("Model_Eval_Measure")
@pytest.mark.parametrize("text", ["", "not a ref\n"])
def test_a_head_that_names_nothing_gives_unknown(tmp_path: Path, text: str) -> None:
    """Given a HEAD without a commit and without a branch,
    When the revision is asked for without git,
    Then it is "unknown".
    """
    start = _checkout(tmp_path / "app")
    (tmp_path / "app" / ".git").mkdir()
    (tmp_path / "app" / ".git" / "HEAD").write_text(text, encoding="utf-8")

    assert tool_revision(_git_is_gone, start=start) == UNKNOWN
