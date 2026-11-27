"""The teacher's verdicts on compared changes: one file per text, in the data root.

A mark is the teacher's work: it says which of the changes they read are right, and no run can
make it again.  So it lives beside ``TeacherWorkStore`` under ``resolve_data_folder("compare_marks")``
and not in the cache, which *Delete stored results* empties and the retention expires.

One file per text, keyed by the text's hash: the marks of one text are read and written together,
and a comparison writes the whole list it holds.  A mark holds no text — a span, the hash of the
replacement and a verdict.

A file written by a newer version of the app is not read as this one, and a file that cannot be
read right now (another program holds it) is left alone: the teacher's marks stay on disk, and the
window shows the changes unmarked until they can be read again.  So a save never replaces such a
file: it fails, and the teacher's earlier verdicts are still there when the file can be read.  A
verdict of a newer app inside a file of this format counts for nothing here, and a save writes it
back as it was.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading

from ..atomic_file import write_text_atomic
from ..domain.proofreading.option_marks import OptionMark, has_foreign_verdict
from ..exceptions import MarksKeptError
from ..messages import marks_not_replaced
from .storage_root import resolve_data_folder

__all__ = ["OptionMarkStore", "default_marks_root"]

_logger = logging.getLogger(__name__)

FORMAT = "compare-marks/1"
"""The shape of a stored marks file; a newer app writes another one."""


def default_marks_root() -> Path:
    """Return the folder the teacher's verdicts are kept in (tests patch this)."""
    return resolve_data_folder("compare_marks")


class OptionMarkStore:
    """Reads and writes the teacher's verdicts, one file per text."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root if root is not None else default_marks_root()
        #: One writer at a time: a click writes while the window reads what is stored.
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        """The folder this store keeps its files in."""
        return self._root

    def path_for(self, text_sha: str) -> Path:
        """Return the file of the verdicts about the text *text_sha*."""
        return self._root / f"{text_sha}.json"

    def marks(self, text_sha: str) -> tuple[OptionMark, ...]:
        """Return the verdicts about the text *text_sha*: none when nothing is stored."""
        path = self.path_for(text_sha)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # nothing marked yet
            return ()
        except (OSError, ValueError) as exc:
            _logger.warning("Cannot read the marks in %s now: %s", path, exc)
            return ()
        return self._read(text_sha, data, path)

    def save(self, text_sha: str, marks: tuple[OptionMark, ...]) -> None:
        """Write *marks* as the verdicts about the text *text_sha*; none deletes the file.

        Raises:
            OSError: the file cannot be written, or it holds verdicts that cannot be read now (it is
                locked, or a newer app wrote it): replacing it would lose them.  The caller tells
                the teacher.
        """
        path = self.path_for(text_sha)
        with self._lock:
            judged = {(mark.start, mark.end, mark.replacement_sha256) for mark in marks}
            foreign = [
                record
                for record in self._foreign_records(path)
                if (record["start"], record["end"], record["replacement_sha256"]) not in judged
            ]
            if not marks and not foreign:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    _logger.warning("Cannot delete the marks in %s: %s", path, exc)
                return
            record = {
                "format": FORMAT,
                "text_sha": text_sha,
                "marks": [*foreign, *(mark.to_dict() for mark in marks)],
            }
            write_text_atomic(
                path, json.dumps(record, ensure_ascii=False, indent=1, sort_keys=True)
            )

    @staticmethod
    def _foreign_records(path: Path) -> list[dict[str, object]]:
        """Return the verdicts of a newer app that the file at *path* holds, to be written back.

        Raises:
            MarksKeptError: the file holds verdicts that cannot be read now, or that another
                version of the app wrote: this save would destroy them.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # nothing marked yet: nothing to lose
            return []
        except ValueError:  # no JSON at all: there is no verdict in it to keep
            return []
        except OSError as exc:
            raise MarksKeptError(marks_not_replaced(locked=True)) from exc
        if not isinstance(data, dict):
            return []
        if data.get("format") != FORMAT:
            raise MarksKeptError(marks_not_replaced(locked=False))
        records = data.get("marks")
        if not isinstance(records, list):
            return []
        return [
            record for record in records if isinstance(record, dict) and has_foreign_verdict(record)
        ]

    @staticmethod
    def _read(text_sha: str, data: object, path: Path) -> tuple[OptionMark, ...]:
        """Return the marks the file at *path* holds about *text_sha*, or none to use."""
        if not isinstance(data, dict) or data.get("format") != FORMAT:
            _logger.warning("Ignoring the marks in %s: not a marks file of this app", path)
            return ()
        if data.get("text_sha") != text_sha:
            _logger.warning("Ignoring the marks in %s: they belong to another text", path)
            return ()
        records = data.get("marks")
        if not isinstance(records, list):
            _logger.warning("Ignoring the marks in %s: the file holds no list of them", path)
            return ()
        found = [
            mark
            for mark in (
                OptionMark.from_dict(text_sha, record)
                for record in records
                if isinstance(record, dict)
            )
            if mark is not None
        ]
        return tuple(found)
