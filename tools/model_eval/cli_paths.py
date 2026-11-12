"""Paths named on a command line of the evaluation tools.

Shared by every command line, so a relative path means the same in all of them.
"""

from __future__ import annotations

import os
from pathlib import Path


def workspace_path(text: str) -> Path:
    """Return the path named on the command line; a relative one is relative to the repository.

    ``bazel run`` starts the tool inside its runfiles tree, so a relative path such as the default
    ``evaluations/proofreading`` would silently point there and not into the repository, where
    ``export`` would write the run files into ``bazel-bin``.  Bazel names the repository in
    ``BUILD_WORKSPACE_DIRECTORY``; outside Bazel the variable is unset and paths stay as typed.
    A leading ``~`` is the home directory.

    Used as the ``type=`` of every path option, so argparse resolves a default the same way.
    """
    path = Path(text).expanduser()
    base = os.environ.get("BUILD_WORKSPACE_DIRECTORY")
    return Path(base) / path if base and not path.is_absolute() else path
