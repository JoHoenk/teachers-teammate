"""The commit the tools were at, for the header of a run and the manifest of a set.

It sits in ``core`` because both the builder of a test set and the runner of a matrix record it, and
the builder may not import the run layer: this module needs the standard library and ``git`` only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess
from typing import Any


def tool_revision(run: Callable[..., Any] = subprocess.run) -> str:
    """Return the commit the tools are at, with ``+dirty`` for uncommitted changes.

    ``unknown`` when this is not a git checkout (a packaged copy).
    """
    here = str(Path(__file__).resolve().parent)

    def git(*args: str) -> str | None:
        try:
            done = run(
                ["git", *args], cwd=here, capture_output=True, text=True, timeout=15, check=False
            )
        except OSError, subprocess.SubprocessError:
            return None
        return done.stdout.strip() if getattr(done, "returncode", 1) == 0 else None

    commit = git("rev-parse", "--short", "HEAD")
    if not commit:
        return "unknown"
    return commit + ("+dirty" if git("status", "--porcelain", "--untracked-files=no") else "")


__all__ = ["tool_revision"]
