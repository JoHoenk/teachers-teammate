"""The compared proofreading results on disk: one file per text and model.

``<cache>/compare/<text hash>/<key>.json``.  One folder per text, so every model that answered
about it sits together and deleting one text's results is deleting one folder; one file per
answer, so a result is written and replaced on its own.  A model the teacher runs again
keeps both answers: the second one is ``<key>~2.json`` beside the first.

The cache is the right place: a result holds student text but no decision, and asking the same
model the same question again gives it back.  ``StoredResults`` counts it, *Delete stored
results* removes it and the retention expires it.

A file of another shape, of a newer app, or one that cannot be read right now (another program
holds it) is no result: the window shows the model as not run and the answer can be made again,
which is cheaper than a wrong answer shown as one of the model's.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import threading

from ..atomic_file import write_text_atomic
from ..domain.proofreading.compared_result import ComparedResult, stem_of
from .storage_root import compare_root

__all__ = ["CompareStore", "default_compare_root"]

_logger = logging.getLogger(__name__)


def default_compare_root() -> Path:
    """Return the folder the compared results are kept in (tests patch this)."""
    return compare_root()


class CompareStore:
    """Reads and writes the compared proofreading results, one file per text and model."""

    def __init__(self, root: Path | None = None) -> None:
        self._fixed_root = root
        #: One writer at a time: the comparison runs models one after another on a worker thread
        #: while the window asks what is stored.
        self._lock = threading.RLock()

    @property
    def root(self) -> Path:
        """The folder this store keeps its folders in."""
        return self._root()

    def results(self, text_sha: str) -> dict[str, ComparedResult]:
        """Return every stored answer about the text *text_sha*, by the name of its file.

        A model that answered twice has two entries: ``key`` and ``key~2``.
        """
        folder = self._folder(text_sha)
        with self._lock:
            try:
                paths = sorted(folder.glob("*.json"))
            except OSError as exc:
                _logger.warning("Cannot list the compared results in %s: %s", folder, exc)
                return {}
        found: dict[str, ComparedResult] = {}
        for path in paths:
            result = self._read(path)
            if result is not None:
                found[result.stem] = result
        return found

    def load(self, text_sha: str, key: str, repeat: int = 0) -> ComparedResult | None:
        """Return the stored answer of the settings *key* and its run *repeat* about the text."""
        return self._read(self._file(text_sha, key, repeat))

    def save(self, result: ComparedResult) -> None:
        """Write *result*, replacing a stored answer of the same settings, text and run.

        Raises:
            OSError: the file cannot be written; the caller shows the model as failed.
        """
        path = self._folder(result.text_sha) / f"{result.stem}.json"
        text = json.dumps(result.to_dict(), ensure_ascii=False, indent=1, sort_keys=True)
        with self._lock:
            write_text_atomic(path, text)

    def _root(self) -> Path:
        """Return the folder of the compared results, looked up when it is needed."""
        return self._fixed_root if self._fixed_root is not None else default_compare_root()

    def _folder(self, text_sha: str) -> Path:
        """Return the folder of the text *text_sha*."""
        return self._root() / text_sha

    def _file(self, text_sha: str, key: str, repeat: int) -> Path:
        """Return the file of one answer of the settings *key* about the text *text_sha*."""
        return self._folder(text_sha) / f"{stem_of(key, repeat)}.json"

    @staticmethod
    def _read(path: Path) -> ComparedResult | None:
        """Return the result stored at *path*, or ``None`` when there is none to read."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # nothing stored for this model yet
            return None
        except (OSError, ValueError) as exc:
            _logger.warning("Cannot read the compared result %s: %s", path, exc)
            return None
        result = ComparedResult.from_dict(data)
        if result is None:
            _logger.warning("Ignoring the compared result %s: not a result of this app", path)
        return result
