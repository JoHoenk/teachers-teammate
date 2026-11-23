"""What a pip resolve brings that the add-on lock does not pin: a platform-only package.

The lock is compiled on Linux; a package only Windows or macOS needs never appears in it, so the
bundle's build writes the pins its own dry run resolved into ``addon_lock.txt``
(``platform_lock.py``), and the resolution check fails while one of them has no pin.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any

import pytest

from tools.build import addon_pins, platform_lock


def _report(*installed: tuple[str, str]) -> dict[str, Any]:
    """Return a pip ``--report`` payload that installed *installed*, name and version."""
    return {
        "install": [
            {"metadata": {"name": name, "version": version}} for name, version in installed
        ],
        "environment": {},
    }


def test_report_pins_names_packages_the_way_pip_does() -> None:
    """
    Given  a report that installed a package spelled with a capital and an underscore
    When   its pins are read
    Then   the name is the PEP 503 form, with the installed version
    """
    report = _report(("PySide6_Essentials", "6.12.0"), ("spacy", "3.8.18"))

    assert addon_pins.report_pins(report) == {
        "pyside6-essentials": "6.12.0",
        "spacy": "3.8.18",
    }


def test_unpinned_names_what_the_pins_do_not_hold() -> None:
    """
    Given  a resolve that brings a package the pins have never seen, one they hold at another
    version and one they pin
    When   the unpinned pins are worked out
    Then   the first two are named in sorted order and the third is not
    """
    report = _report(("langchain-core", "1.8.4"), ("numpy", "2.6.0"), ("spacy", "3.8.18"))

    assert addon_pins.unpinned(report, {"numpy": "2.5.4", "spacy": "3.8.18"}) == [
        "langchain-core==1.8.4",
        "numpy==2.6.0",
    ]


def test_a_resolve_the_pins_hold_completely_is_clean() -> None:
    """
    Given  a report of the packages the lock pins, at the versions it pins
    When   the unpinned pins are worked out
    Then   there are none, so the check passes
    """
    report = _report(("spacy", "3.8.18"), ("langchain-core", "1.8.4"))
    pins = {"spacy": "3.8.18", "langchain-core": "1.8.4"}

    assert addon_pins.unpinned(report, pins) == []


class _Pip:
    """A stand-in for the pip dry run: writes the report each add-on is told to resolve to."""

    def __init__(self, reports: dict[str, dict[str, Any]], *, returncode: int = 0) -> None:
        self._reports = reports
        self._returncode = returncode

    def __call__(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        """Return the finished dry run of the add-on whose requirement is in *command*."""
        report = Path(command[command.index("--report") + 1])
        if self._returncode == 0:
            requirement = command[-1]
            report.write_text(json.dumps(self._reports[requirement]), encoding="utf-8")
        return subprocess.CompletedProcess(command, self._returncode, "", "no matching wheel")


def _files(tmp_path: Path) -> tuple[Path, Path]:
    """Write a lock that pins ``click`` and constraints that pin ``numpy``."""
    lock = tmp_path / "addon_lock.txt"
    constraints = tmp_path / "addon_constraints.txt"
    lock.write_text("click==8.5.1\n", encoding="utf-8")
    constraints.write_text("numpy==2.4.0\n", encoding="utf-8")
    return lock, constraints


def test_platform_pins_collects_what_each_addon_installs_that_nothing_pins(tmp_path: Path) -> None:
    """
    Given  two add-ons whose dry runs install packages the lock and the constraints pin, and one
           (colorama) that only this platform needs
    When   the platform pins are collected
    Then   exactly the unpinned package is named, once
    """
    lock, constraints = _files(tmp_path)
    reports = {
        "a": _report(("click", "8.5.1"), ("colorama", "0.4.6")),
        "b": _report(("numpy", "2.4.0"), ("colorama", "0.4.6")),
    }

    pins = platform_lock.platform_pins(
        lock,
        constraints,
        Path("python"),
        {"first": ["a"], "second": ["b"]},
        run=_Pip(reports),
    )

    assert pins == ["colorama==0.4.6"]


def test_platform_pins_fail_when_an_addon_cannot_be_resolved(tmp_path: Path) -> None:
    """
    Given  a dry run that fails
    When   the platform pins are collected
    Then   it raises, so a lock without this platform's pins is never written quietly
    """
    lock, constraints = _files(tmp_path)

    with pytest.raises(platform_lock.ResolveError, match="no matching wheel"):
        platform_lock.platform_pins(
            lock, constraints, Path("python"), {"first": ["a"]}, run=_Pip({}, returncode=1)
        )
