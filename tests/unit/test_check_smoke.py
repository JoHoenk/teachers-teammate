"""The smoke-result check: the bundle's Python must be the one .python-version names."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.build import check_smoke


def _smoke(tmp_path: Path, python: str | None) -> Path:
    versions = {} if python is None else {"python": python}
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps({"ok": [], "failed": [], "versions": versions}), encoding="utf-8")
    return path


def _version_file(tmp_path: Path, version: str) -> Path:
    path = tmp_path / ".python-version"
    path.write_text(version + "\n", encoding="utf-8")
    return path


def test_check_python_version_accepts_the_same_version() -> None:
    """
    Given  a bundle on the version .python-version names
    When   check_python_version() is called
    Then   there is no problem to report
    """
    assert check_smoke.check_python_version("3.14.10", "3.14.10") is None


def test_check_python_version_names_both_versions_on_a_mismatch() -> None:
    """
    Given  a bundle on 3.14.9 while .python-version says 3.14.10
    When   check_python_version() is called
    Then   the message names both, so the log says which side to fix
    """
    message = check_smoke.check_python_version("3.14.9", "3.14.10")

    assert message == "the bundle runs on Python 3.14.9, .python-version says 3.14.10"


def test_bundle_python_reads_the_smoke_result(tmp_path: Path) -> None:
    """
    Given  a smoke.json written by the bundle
    When   bundle_python() is called
    Then   it returns the Python version the bundle reported
    """
    assert check_smoke.bundle_python(_smoke(tmp_path, "3.14.10")) == "3.14.10"


def test_bundle_python_refuses_a_result_without_a_version(tmp_path: Path) -> None:
    """
    Given  a smoke.json with no Python version (a changed smoke test must not switch this off)
    When   bundle_python() is called
    Then   a ValueError says so
    """
    with pytest.raises(ValueError, match="holds no smoke result with a Python version"):
        check_smoke.bundle_python(_smoke(tmp_path, None))


def test_main_returns_one_on_a_mismatch_and_zero_otherwise(tmp_path: Path) -> None:
    """
    Given  a smoke result and a .python-version file
    When   main() runs with the same and with another version
    Then   it returns 0 for the same and 1 for the other
    """
    smoke = _smoke(tmp_path, "3.14.10")

    same = check_smoke.main(
        [str(smoke), "--python-version-file", str(_version_file(tmp_path, "3.14.10"))]
    )
    other = check_smoke.main(
        [str(smoke), "--python-version-file", str(_version_file(tmp_path, "3.14.11"))]
    )

    assert (same, other) == (0, 1)
