"""Unit tests for writing a small file in one step (``infrastructure/atomic_file.py``)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from teachers_teammate.infrastructure.atomic_file import write_text_atomic


def test_write_text_atomic_writes_the_text(tmp_path: Path) -> None:
    """
    Given  a file that does not exist yet
    When   the text is written atomically
    Then   the file holds the text
    """
    path = tmp_path / "state.json"

    write_text_atomic(path, '{"a": 1}')

    assert path.read_text(encoding="utf-8") == '{"a": 1}'


def test_write_text_atomic_replaces_an_existing_file(tmp_path: Path) -> None:
    """
    Given  a file that already holds an older text
    When   a new text is written atomically
    Then   the file holds the new text and no temporary file is left behind
    """
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")

    write_text_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert [item.name for item in tmp_path.iterdir()] == ["state.json"]


def test_write_text_atomic_creates_the_folder(tmp_path: Path) -> None:
    """
    Given  a folder that does not exist yet
    When   a file in it is written atomically
    Then   the folder is made and the file holds the text
    """
    path = tmp_path / "deep" / "state.json"

    write_text_atomic(path, "text")

    assert path.read_text(encoding="utf-8") == "text"


def test_write_text_atomic_keeps_the_old_file_when_the_swap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a swap (os.replace) that fails
    When   the text is written atomically
    Then   the error is raised, the old file is untouched and no temporary file is left
    """
    path = tmp_path / "state.json"
    path.write_text("old", encoding="utf-8")

    def broken_replace(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("teachers_teammate.infrastructure.atomic_file.os.replace", broken_replace)

    with pytest.raises(OSError, match="disk full"):
        write_text_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert [item.name for item in tmp_path.iterdir()] == ["state.json"]


def test_write_text_atomic_writes_in_place_when_the_swap_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a swap (os.replace) that Windows refuses because the file is held open
    When   the text is written atomically
    Then   the text is still written, and no temporary file is left behind
    """
    monkeypatch.setattr("teachers_teammate.infrastructure.atomic_file._REPLACE_WAIT_S", 0.0)
    path = tmp_path / "state.json"

    def refused(*_args: object) -> None:
        raise PermissionError("held open")

    monkeypatch.setattr("teachers_teammate.infrastructure.atomic_file.os.replace", refused)

    write_text_atomic(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_text_atomic_retries_a_refused_swap_before_giving_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a swap that is refused once and then works
    When   the text is written atomically
    Then   the swap is retried and the text is stored by the swap
    """
    real_replace = os.replace  # the patch below replaces it on the shared os module

    monkeypatch.setattr("teachers_teammate.infrastructure.atomic_file._REPLACE_WAIT_S", 0.0)
    path = tmp_path / "state.json"
    calls: list[int] = []

    def flaky(source, target) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise PermissionError("held open")
        real_replace(source, target)

    monkeypatch.setattr("teachers_teammate.infrastructure.atomic_file.os.replace", flaky)

    write_text_atomic(path, "new")

    assert len(calls) == 2
    assert path.read_text(encoding="utf-8") == "new"


def test_two_writes_never_share_a_temporary_file(tmp_path: Path) -> None:
    """
    Given  one file written from two threads at once
    When   both write many times
    Then   none fails and the stored text is one of the two
    """
    import threading  # noqa: PLC0415

    path = tmp_path / "state.json"
    errors: list[BaseException] = []

    def work(text: str) -> None:
        try:
            for _ in range(30):
                write_text_atomic(path, text)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work, args=(text,)) for text in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert path.read_text(encoding="utf-8") in {"a", "b"}
