"""The commit the tools were at, for the header of a run and the manifest of a set.

It sits in ``core`` because both the builder of a test set and the runner of a matrix record it, and
the builder may not import the run layer: this module needs the standard library and ``git`` only.

The commit is read from the checkout the tools live in, found by walking up from this file.  When
``git`` cannot answer — it refuses a checkout another user owns, and a copied tree carries no
history — the same commit is read from the repository's own files, so a run still says what it ran
with.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

_logger = logging.getLogger(__name__)

MODULE = Path("tools") / "evaluation" / "core" / "revision.py"
"""Where this module sits in the checkout it belongs to: a folder without it is another repository."""

UNKNOWN = "unknown"
"""What a run records when no checkout can be read: a packaged copy of the tools."""

UNVERIFIED = "+unverified"
"""Appended to a commit that was read from the repository's files: git could not say whether the
working tree has uncommitted changes."""


def tool_revision(run: Callable[..., Any] = subprocess.run, *, start: Path | None = None) -> str:
    """Return the commit the tools are at, with ``+dirty`` for uncommitted changes.

    ``unknown`` when this is not a git checkout (a packaged copy).  ``+unverified`` follows a commit
    when git could not say whether the tree is clean.  *start* is where the search for the checkout
    begins; it defaults to this module's folder.
    """
    begin = start if start is not None else Path(__file__).resolve().parent
    root = _checkout(begin)
    if root is None:
        _logger.warning("no checkout of the tools above %s: the revision is %s", begin, UNKNOWN)
        return UNKNOWN
    commit = _git(root, run, "rev-parse", "--short", "HEAD") or _head_commit(root)
    if commit is None:
        _logger.warning("the commit of %s could not be read: the revision is %s", root, UNKNOWN)
        return UNKNOWN
    changes = _git(root, run, "status", "--porcelain", "--untracked-files=no")
    if changes is None:
        _logger.warning("git cannot say whether %s has changes: the revision is %s", root, commit)
        return commit + UNVERIFIED
    return commit + ("+dirty" if changes else "")


def _checkout(start: Path) -> Path | None:
    """Return the folder of the tools' git checkout, or ``None``.

    A folder counts only when it holds both a repository and this module: the store is a repository
    of its own, and its commit is the commit of the records, not of the code that made them.
    """
    for folder in _candidates(start):
        if (folder / ".git").exists() and (folder / MODULE).is_file():
            return folder
    return None


def _candidates(start: Path) -> Iterator[Path]:
    """Yield the folders the checkout may be in, from *start* outwards.

    ``bazel run`` copies the tools instead of linking them when the platform cannot link, so the
    module's own folder then lies outside the checkout; the workspace Bazel names is inside it.
    """
    seen: set[Path] = set()
    for base in (start, Path.cwd(), _workspace()):
        if base is None:
            continue
        for folder in (base, *base.parents):
            if folder not in seen:
                seen.add(folder)
                yield folder


def _workspace() -> Path | None:
    """Return the workspace ``bazel run`` names, or ``None``."""
    named = os.environ.get("BUILD_WORKSPACE_DIRECTORY", "")
    return Path(named) if named else None


def _git(root: Path, run: Callable[..., Any], *args: str) -> str | None:
    """Return what ``git *args*`` prints in *root*, or ``None`` when git cannot answer."""
    try:
        done = run(
            ["git", *args], cwd=str(root), capture_output=True, text=True, timeout=15, check=False
        )
    except OSError, subprocess.SubprocessError:
        return None
    return done.stdout.strip() if getattr(done, "returncode", 1) == 0 else None


def _head_commit(root: Path) -> str | None:
    """Return the commit ``HEAD`` names, read from the repository's own files.

    This is what git reads after its ownership check, which is the step that fails for a checkout
    made by another user or on a mounted filesystem.
    """
    folder = _git_dir(root)
    if folder is None:
        return None
    try:
        head = (folder / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return _short(head)
    reference = head[len("ref: ") :].strip()
    common = _common_dir(folder)
    for where in dict.fromkeys((folder, common)):
        loose = where / reference
        if loose.is_file():
            try:
                return _short(loose.read_text(encoding="utf-8"))
            except OSError:
                return None
    return _packed_commit(common, reference)


def _common_dir(folder: Path) -> Path:
    """Return the folder that holds the branches: *folder* itself, or the one a worktree shares.

    A linked worktree has its own ``HEAD`` but keeps branches and ``packed-refs`` in the main
    repository, which its ``commondir`` file names.
    """
    try:
        named = (folder / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return folder  # not a linked worktree: the branches are here
    return (folder / named).resolve() if named else folder


def _short(commit: str) -> str | None:
    """Return the short form of *commit*, or ``None`` when the text is not one."""
    text = commit.strip()
    if len(text) < 7 or any(character not in "0123456789abcdef" for character in text.lower()):
        return None
    return text[:7]


def _git_dir(root: Path) -> Path | None:
    """Return the folder holding the repository's files, or ``None``.

    A linked worktree keeps them elsewhere and names that folder in a ``.git`` file.
    """
    dot = root / ".git"
    if dot.is_dir():
        return dot
    try:
        text = dot.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text.startswith("gitdir: "):
        return None
    named = Path(text[len("gitdir: ") :].strip())
    return named if named.is_absolute() else root / named


def _packed_commit(folder: Path, reference: str) -> str | None:
    """Return the commit *reference* has in ``packed-refs``, or ``None``."""
    try:
        lines = (folder / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and parts[1] == reference and not line.startswith("#"):
            return _short(parts[0])
    return None


__all__ = ["UNKNOWN", "UNVERIFIED", "tool_revision"]
