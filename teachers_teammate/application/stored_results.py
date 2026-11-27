"""What the app stored for earlier runs: how much it is, and deleting or expiring it.

The cache holds the recognised and proofread result of every document — a state record and its
page previews — in ``<storage root>/artifacts``, one folder per output folder, and the answers of
the model comparison in ``<storage root>/compare``, one folder per text.  It costs time to rebuild
and nothing else, so it is the one thing the app may throw away: the teacher can, and a retention
of ``results_keep_days`` does it at every start.

The teacher's own work is not the cache: it lives in the data root (``TeacherWorkStore``) and
goes only when the teacher asks for it by name.  The output files — Word documents, text files —
are never touched: they are in the teacher's own folder.

A run in progress is never cut short: both deletions refuse while one is going, because the run
is writing the very files they would remove.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path
import shutil
import time

from ..domain.proofreading.compared_result import key_of_stem
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
        compare: Callable[[], Path] | None = None,
        own_work: Callable[[], Sequence[Path]] | None = None,
    ) -> None:
        self._activity = activity
        #: The teacher's own work, which only an explicit choice deletes.
        self._work_store = work_store
        #: ``<storage root>/artifacts``: one folder per output folder.  ``None`` looks it up when
        #: it is needed, so a computer without a writable cache folder still starts the app.
        self._fixed_root = root
        #: ``<storage root>/compare``: the answers of the model comparison, one folder per text.
        #: ``None`` when the app keeps none, or a callable that looks the folder up when it is
        #: needed — for the same reason as :attr:`_fixed_root`.
        self._compare_of = compare
        #: The folders of the teacher's own work beside :attr:`_work_store`: deleted with it, and
        #: looked up only then.
        self._own_work_of = own_work

    def summary(self) -> StoredSummary:
        """Return how much is stored: the documents, and the space they take.

        A compared proofreading is no document: its files count towards the space, and the window
        that holds one says how many there are.
        """
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

        ``include_work`` also deletes the teacher's own corrections, marks, edited texts and
        verdicts on compared changes — the one thing that cannot be rebuilt, so it is a choice of
        its own.

        Raises:
            RunInProgressError: a run is going, so a second writer must not remove its files.
        """
        self._refuse_while_running()
        for root in (self._root(), self._compare_root()):
            if root is not None:
                self._remove_tree(root)
        if include_work:
            for folder in (self._work_store.root, *self._own_work()):
                self._remove_files(folder)

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
        expired = sum(
            1 for state_file in self._state_files() if self._expire_document(state_file, cutoff)
        )
        return expired + self._expire_compared(cutoff)

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

    def _compare_root(self) -> Path | None:
        """Return the folder of the compared results, or ``None`` when the app keeps none."""
        if self._compare_of is None:
            return None
        try:
            return self._compare_of()
        except StorageResolutionError as exc:
            _logger.warning("No folder for compared results: %s", exc)
            return None

    def _own_work(self) -> Sequence[Path]:
        """Return the other folders of the teacher's own work, or none when they cannot be found."""
        if self._own_work_of is None:
            return ()
        try:
            return self._own_work_of()
        except StorageResolutionError as exc:
            _logger.warning("No folder for the teacher's work: %s", exc)
            return ()

    def _files(self) -> Iterator[Path]:
        """Return every file of the cache, the compared results included."""
        for root in (self._root(), self._compare_root()):
            if root is None or not root.is_dir():
                continue
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

    def _expire_compared(self, cutoff: float) -> int:
        """Delete the compared answers written before *cutoff*; return how many went.

        One folder per text: it goes with the last answer in it, so a text the teacher compared
        leaves nothing behind.
        """
        root = self._compare_root()
        if root is None or not root.is_dir():
            return 0
        try:
            folders = sorted(folder for folder in root.iterdir() if folder.is_dir())
        except OSError as exc:
            _logger.warning("Cannot list the compared results in %s: %s", root, exc)
            return 0
        gone = 0
        for folder in folders:
            gone += self._expire_answers(folder, cutoff)
            if self._is_empty(folder):
                self._remove_tree(folder)
        return gone

    @staticmethod
    def _expire_answers(folder: Path, cutoff: float) -> int:
        """Delete each model's answers in *folder* once its newest was written before *cutoff*.

        A model's answers go together: one left behind would read as its first, or leave a gap in
        the numbers of its runs.
        """
        models: dict[str, list[tuple[Path, float]]] = {}
        for path in sorted(folder.glob("*.json")):
            try:
                written = path.stat().st_mtime
            except OSError as exc:
                _logger.warning("Cannot read the compared result %s: %s", path, exc)
                written = float("inf")  # keeps this model's answers: none of them goes alone
            models.setdefault(key_of_stem(path.stem), []).append((path, written))
        gone = 0
        for answers in models.values():
            if max(written for _path, written in answers) >= cutoff:
                continue
            for path, _written in answers:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    _logger.warning("Cannot delete the compared result %s: %s", path, exc)
                    continue
                gone += 1
        return gone

    @staticmethod
    def _is_empty(folder: Path) -> bool:
        """Return whether *folder* holds nothing any more."""
        try:
            return not any(folder.iterdir())
        except OSError as exc:
            _logger.debug("Cannot read the stored folder %s: %s", folder, exc)
            return False

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
