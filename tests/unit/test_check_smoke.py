"""The smoke-result check: the bundle's Python and bundled versions must be the locked ones."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.build import check_smoke

_LOCKED = "6.12.0"


def _smoke(tmp_path: Path, python: str | None, **versions: str) -> Path:
    reported = {
        **(dict.fromkeys(("PySide6", "cv2"), _LOCKED) if python else {}),
        **versions,
    }
    if python:
        reported["python"] = python
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps({"ok": [], "failed": [], "versions": reported}), encoding="utf-8")
    return path


def _tree(tmp_path: Path, *, qt: str = _LOCKED, opencv: str = _LOCKED) -> Path:
    """Lay out the inventory the bundle's versions are held to."""
    (tmp_path / "third_party_licenses").mkdir(parents=True)
    (tmp_path / "third_party_licenses" / "inventory.csv").write_text(
        "Component,Version,License,Scope,Kind\n"
        f"pyside6-essentials,{qt},LGPL-3.0-only,bundle,package\n"
        f"opencv-python-headless,{opencv},Apache-2.0,bundle,package\n",
        encoding="utf-8",
    )
    return tmp_path


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
    tree = _tree(tmp_path)

    same = check_smoke.main(
        [
            str(smoke),
            "--python-version-file",
            str(_version_file(tmp_path, "3.14.10")),
            "--tree",
            str(tree),
        ]
    )
    other = check_smoke.main(
        [
            str(smoke),
            "--python-version-file",
            str(_version_file(tmp_path, "3.14.11")),
            "--tree",
            str(tree),
        ]
    )

    assert (same, other) == (0, 1)


def test_a_bundle_that_differs_from_the_inventory_fails(tmp_path: Path) -> None:
    """
    Given  a bundle whose frozen OpenCV is not the one the inventory lists
    When   main() runs
    Then   it returns 1 and names both versions, so a lock bump cannot ship unattended
    """
    smoke = _smoke(tmp_path, "3.14.10", cv2="5.2.0.10")
    tree = _tree(tmp_path)

    code = check_smoke.main(
        [
            str(smoke),
            "--python-version-file",
            str(_version_file(tmp_path, "3.14.10")),
            "--tree",
            str(tree),
        ]
    )

    assert code == 1


def test_check_locked_versions_names_a_version_that_disagrees(tmp_path: Path) -> None:
    """
    Given  a smoke result that reported a newer Qt than the inventory lists
    When   check_locked_versions() is called
    Then   the message names the module, its version and the inventory's
    """
    problems = check_smoke.check_locked_versions(
        {"PySide6": "6.12.1", "cv2": _LOCKED}, _tree(tmp_path)
    )

    assert problems == ["PySide6 is 6.12.1, the inventory says pyside6-essentials 6.12.0"]


def test_check_locked_versions_refuses_a_result_without_versions(tmp_path: Path) -> None:
    """
    Given  a smoke result that reports no versions at all
    When   smoke_versions() is asked for them
    Then   a ValueError says so, so the check cannot pass by silence
    """
    path = tmp_path / "smoke.json"
    path.write_text(json.dumps({"ok": [], "failed": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="holds no smoke result with a version"):
        check_smoke.smoke_versions(path)
