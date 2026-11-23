"""The add-on lock the installed app gives to pip: the versions this release was tested with."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.build import addon_lock


def test_addon_pins_leave_out_the_packages_the_bundle_already_pins() -> None:
    """
    Given  a lock that resolves the whole project and the bundle's own constraints
    When   the add-on pins are worked out
    Then   only what the bundle does not ship is left, sorted, one pin per package
    """
    lock = "numpy==2.5.5\nspacy==3.8.18\nlangchain-core==1.8.1\n"
    bundle = "numpy==2.5.4\npydantic==2.14.3\n"

    assert addon_lock.addon_pins(lock, bundle) == ["langchain-core==1.8.1", "spacy==3.8.18"]


def test_addon_pins_compare_names_the_way_pip_does() -> None:
    """
    Given  a lock and a bundle that spell one package differently
    When   the add-on pins are worked out
    Then   the package counts as the bundle's, so the two files cannot disagree
    """
    assert (
        addon_lock.addon_pins("PySide6_Essentials==6.11.0\n", "pyside6-essentials==6.11.0\n") == []
    )


def test_write_lock_writes_one_pin_per_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a lock, a bundle's constraints and an output path
    When   the lock is written
    Then   the file holds the add-ons' pins and the count is returned
    """
    lock = tmp_path / "requirements_addons_lock.txt"
    lock.write_text("numpy==2.5.5\nspacy==3.8.18\n", encoding="utf-8")
    bundle = tmp_path / "addon_constraints.txt"
    bundle.write_text("numpy==2.5.4\n", encoding="utf-8")
    monkeypatch.setattr(addon_lock, "LOCK", lock)
    monkeypatch.setattr(addon_lock, "BUNDLE", bundle)
    output = tmp_path / "_build" / "addon_lock.txt"

    count = addon_lock.write_lock(output)

    assert count == 1
    assert output.read_text(encoding="utf-8") == "spacy==3.8.18\n"


def test_write_lock_without_a_bundle_keeps_every_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a lock and no bundle constraints file (a checkout that never built)
    When   the lock is written
    Then   nothing is dropped, so the file is still usable
    """
    lock = tmp_path / "requirements_addons_lock.txt"
    lock.write_text("numpy==2.5.5\nspacy==3.8.18\n", encoding="utf-8")
    monkeypatch.setattr(addon_lock, "LOCK", lock)
    monkeypatch.setattr(addon_lock, "BUNDLE", tmp_path / "missing.txt")
    output = tmp_path / "addon_lock.txt"

    assert addon_lock.write_lock(output) == 2
    assert output.read_text(encoding="utf-8").splitlines() == ["numpy==2.5.5", "spacy==3.8.18"]


def test_extra_pins_are_added_and_win_over_the_locks_line() -> None:
    """
    Given  a lock, the bundle's constraints and the pins this platform resolved on its own
    When   the add-on pins are worked out
    Then   a package the lock has never seen is added, and a different version of one it holds
           replaces that line, so the file pins one version per package
    """
    lock = "numpy==2.5.5\nspacy==3.8.18\nlangchain-core==1.8.1\n"
    bundle = "numpy==2.5.4\n"

    assert addon_lock.addon_pins(lock, bundle, ["pywin32==311", "langchain-core==1.8.4"]) == [
        "langchain-core==1.8.4",
        "pywin32==311",
        "spacy==3.8.18",
    ]


def test_write_lock_writes_the_platforms_pins_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a lock, a bundle's constraints and one pin of this platform
    When   the lock is written
    Then   the pin is in the file and counted
    """
    lock = tmp_path / "requirements_addons_lock.txt"
    lock.write_text("spacy==3.8.18\n", encoding="utf-8")
    bundle = tmp_path / "addon_constraints.txt"
    bundle.write_text("numpy==2.5.4\n", encoding="utf-8")
    monkeypatch.setattr(addon_lock, "LOCK", lock)
    monkeypatch.setattr(addon_lock, "BUNDLE", bundle)
    output = tmp_path / "addon_lock.txt"

    assert addon_lock.write_lock(output, extra_pins=["pywin32==311"]) == 2
    assert output.read_text(encoding="utf-8").splitlines() == ["pywin32==311", "spacy==3.8.18"]


def test_read_lock_pins_of_a_file_that_is_not_there_is_empty(tmp_path: Path) -> None:
    """
    Given  a constraints file the build has not written (a bundle build before the first step)
    When   its pins are read
    Then   the answer is empty, so the caller needs no test of its own
    """
    assert addon_lock.read_lock_pins(tmp_path / "missing.txt") == {}
