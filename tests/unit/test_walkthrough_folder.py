"""The folder an app walkthrough runs in: its own temporary one, or the caller's.

An upgrade walk runs on the data root an earlier version left, and one being looked into
afterwards has to stay: both need the walk in a folder of the caller's.  What is checked here is
the folder only — the walk itself is the tests of ``tests/gui/test_gui_main_walkthrough.py``.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.e2e import walkthrough


def test_a_walk_in_a_temporary_folder_removes_it(tmp_path: Path, monkeypatch) -> None:
    """
    Given  the variable the app reads its root from, unset
    When   the walk's isolation is used without a folder of the caller's
    Then   the app reads a folder of its own while it runs, and that folder is gone afterwards
    """
    monkeypatch.delenv("TEACHERS_TEAMMATE_TMPDIR", raising=False)

    with walkthrough.isolated() as root:
        assert os.environ["TEACHERS_TEAMMATE_TMPDIR"] == str(root)
        (root / "ocr.toml").write_text("check_for_updates = false\n", encoding="utf-8")

    assert not root.exists()
    assert "TEACHERS_TEAMMATE_TMPDIR" not in os.environ


def test_a_walk_in_the_callers_folder_keeps_it(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a folder of the caller's, and a root the caller had set already
    When   the walk's isolation is used around a path that is new, with *keep*
    Then   the app reads that folder, what the walk wrote is still there, and the variable is
           what it was
    """
    monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path / "before"))
    root = tmp_path / "upgrade"

    with walkthrough.isolated(root, keep=True) as folder:
        assert folder == root.resolve()
        assert os.environ["TEACHERS_TEAMMATE_TMPDIR"] == str(root.resolve())
        (folder / "ocr.toml").write_text("check_for_updates = false\n", encoding="utf-8")

    assert (root / "ocr.toml").is_file()
    assert os.environ["TEACHERS_TEAMMATE_TMPDIR"] == str(tmp_path / "before")


def test_a_folder_that_was_there_before_the_walk_is_never_removed(tmp_path: Path) -> None:
    """
    Given  a folder of the caller's that exists before the walk and holds a file of an old version
    When   the walk's isolation is used around it without *keep*
    Then   the folder and the file are still there, so a wrong --root cannot delete a data root
    """
    root = tmp_path / "old-data"
    root.mkdir()
    (root / "ocr.toml").write_text("check_for_updates = false\n", encoding="utf-8")

    with walkthrough.isolated(root) as folder:
        assert folder == root.resolve()

    assert (root / "ocr.toml").is_file()


def test_a_folder_the_walk_made_is_removed_without_keep(tmp_path: Path) -> None:
    """
    Given  a path of the caller's that does not exist yet
    When   the walk's isolation is used around it without *keep*
    Then   the walk makes the folder and removes it at the end, as it does a temporary one
    """
    root = tmp_path / "walk"

    with walkthrough.isolated(root) as folder:
        assert folder.is_dir()

    assert not root.exists()
