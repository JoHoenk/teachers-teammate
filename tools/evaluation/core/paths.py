"""Where a path on a command line points: the repository, not the runfiles tree.

``bazel run`` starts a tool inside its runfiles tree, so a relative path such as the default
``evaluations/proofreading`` would silently point there and not into the repository, where an export
would write the run files into ``bazel-bin``.  Bazel names the repository in
``BUILD_WORKSPACE_DIRECTORY``; outside Bazel the variable is unset and paths stay as typed.  A
leading ``~`` is the home directory.

Shared by every command line, so a relative path means the same in all of them.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_path(text: str) -> Path:
    """Return the path named on the command line; a relative one is relative to the repository."""
    path = Path(text).expanduser()
    base = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    return Path(base) / path if base and not path.is_absolute() else path


__all__ = ["workspace_path"]
