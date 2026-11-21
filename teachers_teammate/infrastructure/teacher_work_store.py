"""The teacher's work on disk: one file per essay, in the data root.

The machine results live in the cache, which is per output folder and may be emptied; what the
teacher changed and decided must not go with it.  So their work has a store of its own under
``resolve_data_folder("work")``, keyed by the document's content (``compute_file_hash``): one
essay has one work wherever its results go and whatever it is called, and a changed file is
another essay.

One writer: the window, through ``DocumentCommands``.  Runs and the CLI only read.

Robustness is the point of the small print here.  A file written by a newer version of the app
is ignored and never overwritten — the older app does not know what it would lose.  A file that
is not a work file is moved aside rather than deleted, so a person can look at it; one that only
cannot be opened right now (another program holds it) is left alone and no write goes over it.
An empty work deletes its file, so the folder holds only what the teacher really did.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import threading

from ..domain.teacher_work import TeacherWork, WorkTooNewError
from .atomic_file import write_text_atomic
from .storage_root import resolve_data_folder

__all__ = [
    "TeacherWorkStore",
    "WorkTooNewError",
    "default_work_root",
]


def default_work_root() -> Path:
    """Return the folder the teacher's work is kept in (tests patch this)."""
    return resolve_data_folder("work")


class TeacherWorkStore:
    """Reads and writes the teacher's work, one file per essay."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_work_root()
        #: Hashes whose file was written by a newer version: a write would lose what it holds.
        self._too_new: set[str] = set()
        #: One writer at a time: the window writes from the GUI thread and from background
        #: tasks, and a read-modify-write must not interleave.
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        """The folder this store keeps its files in."""
        return self._root

    def path_for(self, source_hash: str) -> Path:
        """Return the file of the essay whose content hash is *source_hash*."""
        return self._root / f"{source_hash}.json"

    def load(self, source_hash: str) -> TeacherWork | None:
        """Return the work of *source_hash*, or ``None`` when there is none to use.

        A file of a newer version is ignored (and remembered, so a write is refused); a file
        that is not a work file is moved aside and treated as absent.  A file that cannot be
        read *now* (another program holds it open) is neither moved nor treated as a work: the
        answer is ``None`` for this call only, and :meth:`update` refuses to write over it.
        """
        try:
            return self._read(source_hash)
        except OSError:
            return None

    def update(
        self,
        source_hash: str,
        name: str,
        change: Callable[[TeacherWork], TeacherWork],
    ) -> TeacherWork:
        """Apply *change* to the work of *source_hash* and store the result.

        The work is loaded, changed and written under one lock, so two writes of one document
        cannot lose each other.  An empty result deletes the file.

        Raises:
            WorkTooNewError: the file was written by a newer version of the app.
            OSError: the file cannot be read or written; nothing is written then, because what
                is on disk is not known.
        """
        with self._lock:
            # Read first: whether the file is too new is only known once it was read.
            current = self._read(source_hash)
            if source_hash in self._too_new:
                raise WorkTooNewError(f"the work of {name} was saved by a newer version of the app")
            current = current or TeacherWork(source_hash=source_hash, name=name)
            changed = change(current)
            if changed.empty:
                self.path_for(source_hash).unlink(missing_ok=True)
                return changed
            stored = TeacherWork(
                source_hash=source_hash,
                name=name,
                transcription=changed.transcription,
                corrections=changed.corrections,
            )
            write_text_atomic(
                self.path_for(source_hash),
                json.dumps(stored.to_dict(), indent=2, ensure_ascii=False),
            )
            return stored

    def _read(self, source_hash: str) -> TeacherWork | None:
        """Return the work of *source_hash*; raise ``OSError`` for a file that cannot be read.

        Under the store's lock, so a read in this process never sees a write of it half done.
        """
        with self._lock:
            path = self.path_for(source_hash)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                self._too_new.discard(source_hash)
                return None
            except ValueError:
                # Not JSON, or not UTF-8: the file itself is broken.
                self._move_aside(path)
                return None
            try:
                work = TeacherWork.from_dict(raw if isinstance(raw, dict) else {})
            except WorkTooNewError:
                self._too_new.add(source_hash)
                return None
            except ValueError:
                self._move_aside(path)
                return None
            if work.source_hash != source_hash:
                # The file is named after its content; one that says otherwise is not this
                # essay's.
                self._move_aside(path)
                return None
            self._too_new.discard(source_hash)
            return work

    def _move_aside(self, path: Path) -> None:
        """Move a broken file out of the way, so it is not read again and not lost.

        Raises:
            OSError: the file cannot be moved.  It then stays where it is and the caller must
                not write over it.
        """
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path.replace(path.with_name(f"{path.name}.broken-{stamp}"))
