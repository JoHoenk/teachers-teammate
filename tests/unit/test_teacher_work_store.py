"""Unit tests for the teacher's work on disk (``infrastructure/teacher_work_store.py``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from teachers_teammate.domain.teacher_work import (
    WORK_VERSION,
    CorrectionsWork,
    TeacherWork,
    TranscriptionWork,
    WorkTooNewError,
)
from teachers_teammate.infrastructure.teacher_work_store import TeacherWorkStore

HASH = "a" * 64


def _store(tmp_path: Path) -> TeacherWorkStore:
    """Return a store under *tmp_path*."""
    return TeacherWorkStore(tmp_path / "work")


def _transcription(text: str = "Der bunte Hund") -> TranscriptionWork:
    """Return a transcription part with *text*."""
    return TranscriptionWork(text=text, text_format="plain")


def _corrections(text: str | None = "Der bunte Hund.") -> CorrectionsWork:
    """Return a corrections part bound to one answer."""
    return CorrectionsWork(bound_to="answer-1", text=text, checked=True)


def _with_transcription(hash_: str, name: str) -> TeacherWork:
    """Return the work of an essay whose transcription the teacher fixed."""
    return TeacherWork(hash_, name, transcription=_transcription())


def test_load_of_a_missing_work_is_none(tmp_path: Path) -> None:
    """
    Given  a store that holds nothing
    When   the work of a document is loaded
    Then   there is none
    """
    assert _store(tmp_path).load(HASH) is None


def test_update_stores_and_load_returns_it(tmp_path: Path) -> None:
    """
    Given  a store that holds nothing
    When   a transcription part is stored
    Then   loading the document gives the same work back
    """
    store = _store(tmp_path)

    stored = store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    assert stored.transcription == _transcription()
    assert store.load(HASH) == stored


def test_update_keeps_the_other_part(tmp_path: Path) -> None:
    """
    Given  a work whose transcription the teacher fixed
    When   a corrections part is stored
    Then   both parts are there
    """
    store = _store(tmp_path)
    store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    stored = store.update(
        HASH,
        "essay.pdf",
        lambda work: TeacherWork(HASH, "essay.pdf", work.transcription, _corrections()),
    )

    assert stored.transcription == _transcription()
    assert stored.corrections == _corrections()


def test_update_writes_the_name_of_the_last_write(tmp_path: Path) -> None:
    """
    Given  a work stored under one file name
    When   it is stored again under another name
    Then   the stored name is the new one, and the key is still the content
    """
    store = _store(tmp_path)
    store.update(HASH, "first.pdf", lambda work: _with_transcription(HASH, "first.pdf"))

    stored = store.update(
        HASH, "second.pdf", lambda work: TeacherWork(HASH, "second.pdf", work.transcription)
    )

    assert stored.name == "second.pdf"
    assert store.load(HASH).name == "second.pdf"


def test_update_of_an_empty_result_deletes_the_file(tmp_path: Path) -> None:
    """
    Given  a work with a transcription part
    When   the change leaves nothing to store
    Then   the file is gone and loading gives none
    """
    store = _store(tmp_path)
    store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    store.update(HASH, "essay.pdf", lambda work: TeacherWork(HASH, "essay.pdf"))

    assert not store.path_for(HASH).exists()
    assert store.load(HASH) is None


def test_update_leaves_no_temporary_file(tmp_path: Path) -> None:
    """
    Given  a store that holds nothing
    When   a work is stored
    Then   the folder holds that one file and nothing else
    """
    store = _store(tmp_path)

    store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    assert [item.name for item in store.root.iterdir()] == [f"{HASH}.json"]


def test_the_file_is_named_after_the_content_hash(tmp_path: Path) -> None:
    """
    Given  a store
    When   the path of a document's work is asked for
    Then   it is the content hash in the work folder
    """
    store = _store(tmp_path)

    assert store.path_for(HASH) == store.root / f"{HASH}.json"


def test_the_same_content_in_two_folders_is_one_work(tmp_path: Path) -> None:
    """
    Given  one essay stored from one folder
    When   the work of the same content is loaded, whatever it is called
    Then   it is the same work
    """
    store = _store(tmp_path)
    store.update(HASH, "first/essay.pdf", lambda work: _with_transcription(HASH, "first/essay.pdf"))

    assert store.load(HASH).transcription == _transcription()


def test_a_work_of_a_newer_version_is_ignored_and_never_overwritten(tmp_path: Path) -> None:
    """
    Given  a work file written by a newer version of the app
    When   it is loaded and then written
    Then   loading gives none, the write is refused, and the file is untouched
    """
    store = _store(tmp_path)
    path = store.path_for(HASH)
    path.parent.mkdir(parents=True)
    newer = json.dumps({"version": WORK_VERSION + 1, "source_hash": HASH, "name": "essay.pdf"})
    path.write_text(newer, encoding="utf-8")

    assert store.load(HASH) is None
    with pytest.raises(WorkTooNewError):
        store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))
    assert path.read_text(encoding="utf-8") == newer


def test_a_broken_file_is_moved_aside_and_treated_as_absent(tmp_path: Path) -> None:
    """
    Given  a work file that is not JSON
    When   it is loaded
    Then   it is moved aside under a broken name and loading gives none
    """
    store = _store(tmp_path)
    path = store.path_for(HASH)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert store.load(HASH) is None
    assert not path.exists()
    aside = [item for item in store.root.iterdir() if ".broken-" in item.name]
    assert len(aside) == 1
    assert aside[0].read_text(encoding="utf-8") == "{not json"


def test_a_file_of_another_essay_is_moved_aside(tmp_path: Path) -> None:
    """
    Given  a work file whose content hash is not the one it is named after
    When   it is loaded
    Then   it is moved aside and loading gives none
    """
    store = _store(tmp_path)
    path = store.path_for(HASH)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"version": WORK_VERSION, "source_hash": "b" * 64, "name": "other.pdf"}),
        encoding="utf-8",
    )

    assert store.load(HASH) is None
    assert not path.exists()


def test_a_work_without_a_version_is_moved_aside(tmp_path: Path) -> None:
    """
    Given  a work file without a version
    When   it is loaded
    Then   it is moved aside and loading gives none
    """
    store = _store(tmp_path)
    path = store.path_for(HASH)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"source_hash": HASH}), encoding="utf-8")

    assert store.load(HASH) is None
    assert not path.exists()


def test_the_stored_file_holds_the_version_first(tmp_path: Path) -> None:
    """
    Given  a stored work
    When   its file is read
    Then   the version stands first, so a person opening it sees what it is
    """
    store = _store(tmp_path)

    store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    raw = json.loads(store.path_for(HASH).read_text(encoding="utf-8"))
    assert next(iter(raw)) == "version"
    assert raw["version"] == 1  # nothing in it that an older app would lose


def test_a_newer_file_is_not_overwritten_by_the_first_write(tmp_path: Path) -> None:
    """
    Given  a work file written by a newer version of the app, and a store that never read it
    When   the first thing the store is asked to do is write
    Then   the write is refused and the file is untouched
    """
    path = _store(tmp_path).path_for(HASH)
    path.parent.mkdir(parents=True)
    newer = json.dumps({"version": WORK_VERSION + 1, "source_hash": HASH, "name": "essay.pdf"})
    path.write_text(newer, encoding="utf-8")
    fresh = _store(tmp_path)

    with pytest.raises(WorkTooNewError):
        fresh.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    assert path.read_text(encoding="utf-8") == newer


def test_a_file_that_cannot_be_opened_right_now_is_left_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a good work file that another program holds open for a moment
    When   it is loaded and then written
    Then   it is not moved aside, loading gives none for now, and the write is refused
    """
    store = _store(tmp_path)
    store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))
    path = store.path_for(HASH)
    good = path.read_text(encoding="utf-8")
    real_read = Path.read_text

    def held(self: Path, *args, **kwargs):
        if self == path:
            raise PermissionError("held open")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", held)

    assert store.load(HASH) is None
    with pytest.raises(OSError, match="held open"):
        store.update(HASH, "essay.pdf", lambda work: TeacherWork(HASH, "essay.pdf"))

    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == good
    assert [item.name for item in store.root.iterdir()] == [f"{HASH}.json"]
    assert store.load(HASH) is not None


def test_a_broken_file_that_cannot_be_moved_is_not_written_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a work file that is not JSON and cannot be moved aside
    When   the store is asked to write the work of that essay
    Then   the write is refused, so the file a person could still look at is not replaced
    """
    store = _store(tmp_path)
    path = store.path_for(HASH)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    def refused(self: Path, *_args, **_kwargs):
        raise PermissionError("cannot move")

    monkeypatch.setattr(Path, "replace", refused)

    with pytest.raises(OSError, match="cannot move"):
        store.update(HASH, "essay.pdf", lambda work: _with_transcription(HASH, "essay.pdf"))

    assert path.read_text(encoding="utf-8") == "not json"
