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
