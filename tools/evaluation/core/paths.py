"""Where a path on a command line points: the repository, not the runfiles tree, and never a store
inside the repository.

``bazel run`` starts a tool inside its runfiles tree, so a relative path would silently point there
and not into the workspace.  Bazel names the workspace the tool was started in in
``BUILD_WORKSPACE_DIRECTORY`` (this repository, or the evaluation repository); outside Bazel the
variable is unset and paths stay as typed.  A leading ``~`` is the home directory.

Shared by every command line, so a relative path means the same in all of them.

The evaluation records live in a repository of their own (:data:`STORE_REPOSITORY`), because they hold
the corpora's text: a store inside this repository would put that text into its history, where a
slip cannot be taken back.  :func:`store_path` refuses one.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

STORE_REPOSITORY = "teachers-teammate-evals"
"""The private repository the evaluation records live in, never this one.  A command names its
clone with ``--store``; nothing is guessed."""


def workspace_path(text: str) -> Path:
    """Return the path named on the command line; a relative one is relative to the repository."""
    path = Path(text).expanduser()
    base = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    return Path(base) / path if base and not path.is_absolute() else path


def repository_root() -> Path:
    """Return the app repository's working tree: three levels above this file
    (``tools/evaluation/core/paths.py``), whatever the directory the tool was started in.

    Not ``BUILD_WORKSPACE_DIRECTORY``: the tools also run from the evaluation repository, whose
    workspace that is.  Under Bazel the file is a link into the source tree, which ``resolve()``
    follows.
    """
    return Path(__file__).resolve().parents[3]


def store_path(text: str) -> Path:
    """Return the store *text* names, and refuse one inside the app repository's working tree.

    Raises:
        ValueError: if the path is inside the app repository, naming the store that is meant.
    """
    path = workspace_path(text)
    resolved = path.resolve()
    top = repository_root().resolve()
    if resolved == top or top in resolved.parents:
        raise ValueError(
            f"{path} is inside the app repository; the evaluation records live in "
            f"the repository {STORE_REPOSITORY}, a repository of their own"
        )
    return path


def store_argument(text: str) -> Path:
    """Return the store a command line names, as :mod:`argparse` wants it: a refusal is a message."""
    try:
        return store_path(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None


__all__ = [
    "STORE_REPOSITORY",
    "repository_root",
    "store_argument",
    "store_path",
    "workspace_path",
]
