"""Write a small file so that a crash never leaves half of it behind.

The stores of the app write one JSON file per document — the cache's state and the teacher's
work — and the settings file holds the setup a teacher cannot rebuild.  A process may end during
a save (the window closed while a run was going), and a half-written file would lose the whole
record, so the text goes to a temporary file beside the target first and is then swapped in.

The temporary name is unique, so two saves of one document never share a file.  On Windows a
file that something else holds open (the window reading it, a virus scanner) cannot be replaced;
writing in place still works there, which is better than losing the save.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import time

_REPLACE_ATTEMPTS = 5
_REPLACE_WAIT_S = 0.05


def write_text_atomic(path: Path, text: str) -> None:
    """Write *text* to *path*, replacing it in one step.

    A symlink stays a link: the text goes where it points.  A file that exists keeps its
    permissions; a new one is private to the user.  The data is on disk before it is swapped in.

    Raises:
        OSError: the file cannot be written at all.
    """
    if path.is_symlink():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _keep_mode(path, temp)
        _replace_file(temp, path)
    except PermissionError:
        temp.unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError:
        temp.unlink(missing_ok=True)
        raise


def _keep_mode(target: Path, temp: Path) -> None:
    """Give *temp* the permission bits of *target* when it exists."""
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
    except FileNotFoundError:
        return
    temp.chmod(mode)


def _replace_file(source: Path, target: Path) -> None:
    """Move *source* over *target*; retry a refusal for a moment (Windows: file held open)."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_WAIT_S)
