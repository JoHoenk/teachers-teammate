"""The stored results of earlier runs: how much they are, deleting them, and expiring them.

The cache is the one thing the app may throw away, so these tests pin what goes with it and what
must stay: the output files in the teacher's folder, and the work they did by hand.
"""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from teachers_teammate.application.stored_results import (
    RunInProgressError,
    StoredResults,
    StoredSummary,
)
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore


class _Activity:
    """A run in progress, as the service asks it."""

    def __init__(self, *, busy: bool = False) -> None:
        self._busy = busy

    def busy(self) -> bool:
        """Return whether a run is going."""
        return self._busy


def _cache(tmp_path: Path) -> Path:
    """Return the cache root a test builds its results in."""
    return tmp_path / "artifacts"


def _state(root: Path, key: str, stem: str) -> Path:
    """Write one document's state record into *root* and return its path."""
    folder = root / key / "state"
    folder.mkdir(parents=True, exist_ok=True)
    record = folder / f"{stem}.json"
    record.write_text('{"schema_version": 3}', encoding="utf-8")
    return record


def _previews(root: Path, key: str, stem: str) -> list[Path]:
    """Write the two preview images of one document and return their paths."""
    written = []
    for name in (f"{stem}_preprocessed.png", f"{stem}_p2.png"):
        path = root / key / name
        path.write_bytes(b"png")
        written.append(path)
    return written


def _stored(tmp_path: Path, *, busy: bool = False) -> tuple[StoredResults, TeacherWorkStore]:
    """Return the service under test, and the store of the teacher's own work."""
    work_store = TeacherWorkStore(tmp_path / "work")
    return (
        StoredResults(activity=_Activity(busy=busy), work_store=work_store, root=_cache(tmp_path)),
        work_store,
    )


def test_the_summary_counts_the_documents_and_the_space_they_take(tmp_path: Path) -> None:
    """
    Given  two documents with a state record and two previews each
    When   the summary is asked for
    Then   it names two documents and the bytes of every stored file, previews included
    """
    stored, _work = _stored(tmp_path)
    for key, stem in (("aaa", "anna_1"), ("bbb", "ben_2")):
        _state(_cache(tmp_path), key, stem)
        _previews(_cache(tmp_path), key, stem)

    summary = stored.summary()

    expected = sum(path.stat().st_size for path in _cache(tmp_path).rglob("*") if path.is_file())
    assert summary == StoredSummary(documents=2, bytes_taken=expected)
    assert summary.bytes_taken > 0


def test_deleting_takes_the_cache_and_leaves_the_outputs_and_the_work(tmp_path: Path) -> None:
    """
    Given  a stored result, a Word file in the output folder and the teacher's own corrections
    When   the stored results are deleted
    Then   only the cache is gone: what the run wrote and what the teacher did stay
    """
    stored, _work = _stored(tmp_path)
    _state(_cache(tmp_path), "aaa", "anna_1")
    output = tmp_path / "output" / "anna.docx"
    output.parent.mkdir(parents=True)
    output.write_text("word", encoding="utf-8")
    work = tmp_path / "work" / "hash1.json"
    work.parent.mkdir(parents=True)
    work.write_text("{}", encoding="utf-8")

    stored.delete()

    assert not _cache(tmp_path).exists()
    assert output.is_file()
    assert work.is_file()


def test_the_work_choice_deletes_the_teachers_corrections_too(tmp_path: Path) -> None:
    """
    Given  the teacher's corrections beside a stored result
    When   the deletion is asked for with the work included
    Then   both are gone, and the output folder is untouched
    """
    stored, work_store = _stored(tmp_path)
    _state(_cache(tmp_path), "aaa", "anna_1")
    work = tmp_path / "work" / "hash1.json"
    work.parent.mkdir(parents=True)
    work.write_text("{}", encoding="utf-8")

    stored.delete(include_work=True)

    assert not _cache(tmp_path).exists()
    assert not any(work_store.root.iterdir())
    assert work_store.root.is_dir(), "the folder stays, so the next correction lands in it"


def test_everything_is_kept_while_there_is_nothing_to_delete(tmp_path: Path) -> None:
    """
    Given  a cache that was never written
    When   the summary is asked for and the results are deleted
    Then   nothing is reported and nothing fails
    """
    stored, _work = _stored(tmp_path)

    assert stored.summary() == StoredSummary(documents=0, bytes_taken=0)
    stored.delete()

    assert stored.summary() == StoredSummary(documents=0, bytes_taken=0)


def test_expiring_keeps_yesterday_and_drops_forty_days_ago(tmp_path: Path) -> None:
    """
    Given  one result written yesterday and one written forty days ago
    When   the results older than thirty days are expired
    Then   the old record and its previews go, and the recent one stays with its own
    """
    stored, _work = _stored(tmp_path)
    recent = _state(_cache(tmp_path), "aaa", "anna_1")
    recent_previews = _previews(_cache(tmp_path), "aaa", "anna_1")
    old = _state(_cache(tmp_path), "bbb", "ben_2")
    old_previews = _previews(_cache(tmp_path), "bbb", "ben_2")
    yesterday = time.time() - 1 * 86_400
    forty_days_ago = time.time() - 40 * 86_400
    os.utime(recent, (yesterday, yesterday))
    os.utime(old, (forty_days_ago, forty_days_ago))

    removed = stored.expire(30)

    assert removed == 1
    assert not old.exists()
    assert not any(path.exists() for path in old_previews)
    assert recent.is_file()
    assert all(path.is_file() for path in recent_previews)
    assert stored.summary().documents == 1


@pytest.mark.parametrize("days", [0, -1])
def test_a_retention_of_nothing_deletes_nothing(tmp_path: Path, days: int) -> None:
    """
    Given  a result written forty days ago
    When   the retention is no days
    Then   nothing is expired: "keep" is what the setting means
    """
    stored, _work = _stored(tmp_path)
    old = _state(_cache(tmp_path), "aaa", "anna_1")
    forty_days_ago = time.time() - 40 * 86_400
    os.utime(old, (forty_days_ago, forty_days_ago))

    assert stored.expire(days) == 0
    assert old.is_file()


def test_a_run_in_progress_holds_both_deletions_back(tmp_path: Path) -> None:
    """
    Given  a run that is writing the cache right now
    When   the results are deleted or expired
    Then   both refuse, and everything is still there
    """
    stored, _work = _stored(tmp_path, busy=True)
    record = _state(_cache(tmp_path), "aaa", "anna_1")

    with pytest.raises(RunInProgressError):
        stored.delete()
    with pytest.raises(RunInProgressError):
        stored.delete(include_work=True)
    with pytest.raises(RunInProgressError):
        stored.expire(30)

    assert record.is_file()


def test_a_computer_without_a_writable_cache_folder_has_nothing_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  no storage root can be written
    When   the service is built, summarised, deleted and expired
    Then   it is built, holds nothing, and neither deleting nor expiring fails
    """
    from teachers_teammate.exceptions import StorageResolutionError  # noqa: PLC0415

    def unresolvable() -> Path:
        raise StorageResolutionError("nothing is writable")

    monkeypatch.setattr("teachers_teammate.application.stored_results.artifacts_root", unresolvable)

    stored = StoredResults(activity=_Activity(), work_store=TeacherWorkStore(tmp_path / "work"))

    assert stored.summary() == StoredSummary(documents=0, bytes_taken=0)
    stored.delete()
    assert stored.expire(30) == 0
