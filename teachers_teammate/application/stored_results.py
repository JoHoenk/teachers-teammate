"""What the app stored for earlier runs: how much it is, and deleting or expiring it.

The cache holds the recognised and proofread result of every document — a state record and its
page previews — in ``<storage root>/artifacts``, one folder per output folder.  It costs time to
rebuild and nothing else, so it is the one thing the app may throw away: the teacher can, and a
retention of ``results_keep_days`` does it at every start.

The teacher's own work is not the cache: it lives in the data root (``TeacherWorkStore``) and
goes only when the teacher asks for it by name.  The output files — Word documents, text files —
are never touched: they are in the teacher's own folder.

A run in progress is never cut short: both deletions refuse while one is going, because the run
is writing the very files they would remove.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import time

from ..exceptions import StorageResolutionError
from ..infrastructure.storage_root import artifacts_root
from ..infrastructure.teacher_work_store import TeacherWorkStore
from .ports import RunActivity

__all__ = ["STATE_FOLDER", "RunInProgressError", "StoredResults", "StoredSummary"]

_logger = logging.getLogger(__name__)

#: The folder inside a run's cache that holds the state records; the previews sit beside it.
STATE_FOLDER = "state"

_SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class StoredSummary:
    """How much the app keeps for earlier runs."""

    documents: int
    """The documents that have a stored result."""
    bytes_taken: int
    """The space their files take, in bytes."""


class RunInProgressError(Exception):
    """A run is going, so the stored results it is writing are left alone."""


class StoredResults:
    """The results kept for earlier runs: how much, removing them, and expiring them."""

    def __init__(
        self,
        *,
        activity: RunActivity,
        work_store: TeacherWorkStore,
        root: Path | None = None,
    ) -> None:
        self._activity = activity
        #: The teacher's own work, which only an explicit choice deletes.
        self._work_store = work_store
        #: ``<storage root>/artifacts``: one folder per output folder.  ``None`` looks it up when
        #: it is needed, so a computer without a writable cache folder still starts the app.
        self._fixed_root = root

    def summary(self) -> StoredSummary:
        """Return how much is stored: the documents, and the space they take."""
        documents = 0
        size = 0
        for path in self._files():
            try:
                size += path.stat().st_size
            except OSError as exc:
                _logger.debug("Cannot measure the stored file %s: %s", path, exc)
                continue
            if path.parent.name == STATE_FOLDER and path.suffix == ".json":
                documents += 1
        return StoredSummary(documents=documents, bytes_taken=size)

    def delete(self, *, include_work: bool = False) -> None:
        """Delete everything kept for earlier runs; the output files and the sources stay.

        ``include_work`` also deletes the teacher's own corrections, marks and edited texts —
        the one thing that cannot be rebuilt, so it is a choice of its own.

        Raises:
            RunInProgressError: a run is going, so a second writer must not remove its files.
        """
        self._refuse_while_running()
        root = self._root()
        if root is not None:
            self._remove_tree(root)
        if include_work:
            self._remove_files(self._work_store.root)

    def expire(self, days: int) -> int:
        """Delete the results last written more than *days* days ago; return how many went.

        ``0`` or less keeps everything, which is what "keep" means in the settings.  A
        document's previews go with its state record.

        Raises:
            RunInProgressError: a run is going, so a second writer must not remove its files.
        """
        if days <= 0:
            return 0
        self._refuse_while_running()
        cutoff = time.time() - days * _SECONDS_PER_DAY
        return sum(
            1 for state_file in self._state_files() if self._expire_document(state_file, cutoff)
        )

    # ── internal ──────────────────────────────────────────────────────────────

    def _root(self) -> Path | None:
        """Return the folder of the cache, or ``None`` when there is no writable place for one."""
        if self._fixed_root is not None:
            return self._fixed_root
        try:
            return artifacts_root()
        except StorageResolutionError as exc:
            _logger.warning("No folder for stored results: %s", exc)
            return None

    def _refuse_while_running(self) -> None:
        """Refuse while a run is going; it is writing the files this would delete."""
        if self._activity.busy():
            raise RunInProgressError

    def _files(self) -> Iterator[Path]:
        """Return every file of the cache root."""
        root = self._root()
        if root is None or not root.is_dir():
            return
        for path in root.rglob("*"):
            if path.is_file():
                yield path

    def _state_files(self) -> Iterator[Path]:
        """Return the state record of every stored document."""
        root = self._root()
        if root is None or not root.is_dir():
            return
        for folder in sorted(root.glob(f"*/{STATE_FOLDER}")):
            yield from sorted(folder.glob("*.json"))

    def _expire_document(self, state_file: Path, cutoff: float) -> bool:
        """Delete one document's result if it was last written before *cutoff*.

        Returns:
            ``True`` when its state record was deleted, so the caller can count it.
        """
        try:
            if state_file.stat().st_mtime >= cutoff:
                return False
            state_file.unlink()
        except OSError as exc:
            _logger.warning("Cannot delete the stored result %s: %s", state_file, exc)
            return False
        for preview in self._previews_of(state_file):
            try:
                preview.unlink(missing_ok=True)
            except OSError as exc:
                _logger.warning("Cannot delete the stored preview %s: %s", preview, exc)
        return True

    @staticmethod
    def _previews_of(state_file: Path) -> list[Path]:
        """Return the preview images of the document *state_file* belongs to.

        They are named after the state record's stem and sit beside the state folder
        (``<stem>_preprocessed.png``, ``<stem>_p2.png``, ``<stem>_original.pdf``, …).  Matched on
        the whole name rather than with a glob, so a stem with pattern characters stays a name.
        """
        folder = state_file.parent.parent
        prefix = f"{state_file.stem}_"
        try:
            entries = list(folder.iterdir())
        except OSError as exc:
            _logger.warning("Cannot list the stored files of %s: %s", state_file.name, exc)
            return []
        return [entry for entry in entries if entry.is_file() and entry.name.startswith(prefix)]

    @staticmethod
    def _remove_tree(root: Path) -> None:
        """Delete *root* with everything in it; a folder that is not there is nothing to do."""
        try:
            shutil.rmtree(root)
        except FileNotFoundError:  # nothing stored, so nothing to delete
            return
        except OSError as exc:
            _logger.warning("Cannot delete all stored results in %s: %s", root, exc)

    @staticmethod
    def _remove_files(folder: Path) -> None:
        """Delete what *folder* holds and keep the folder itself."""
        try:
            entries = list(folder.iterdir())
        except FileNotFoundError:  # no folder of the teacher's work, so nothing to empty
            return
        except OSError as exc:
            _logger.warning("Cannot read the folder of the teacher's work %s: %s", folder, exc)
            return
        for entry in entries:
            try:
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink(missing_ok=True)
            except OSError as exc:
                _logger.warning("Cannot delete %s: %s", entry, exc)
