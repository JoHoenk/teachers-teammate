"""The comparison a build makes: an artifact's license files and versions against the tree."""

from __future__ import annotations

from pathlib import Path

from tools.licenses import artifacts


def _tree(tmp_path: Path, *, qt: str = "6.12.0", opencv: str = "5.1.0.95") -> Path:
    """Lay the repository's license files and inventory out as a fixture tree."""
    for name in artifacts.PACKAGE_FILES:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"content of {name}\n".encode())
    (tmp_path / "third_party_licenses").mkdir(parents=True, exist_ok=True)
    (tmp_path / artifacts.INVENTORY).write_text(
        "Component,Version,License,Scope,Kind\n"
        f"pyside6-essentials,{qt},LGPL-3.0-only,bundle,package\n"
        f"opencv-python-headless,{opencv},Apache-2.0,bundle,package\n",
        encoding="utf-8",
    )
    return tmp_path


def _carried(tree: Path) -> dict[str, bytes]:
    """Return what a correct artifact carries: the tree's own files, by their repository path."""
    return {name: (tree / name).read_bytes() for name in artifacts.PACKAGE_FILES}


def test_locked_versions_read_the_inventory(tmp_path: Path) -> None:
    """
    Given  a tree with an inventory
    When   locked_versions() is called
    Then   it returns one version per component, so a caller needs no second reader
    """
    tree = _tree(tmp_path)

    assert artifacts.locked_versions(tree) == {
        "pyside6-essentials": "6.12.0",
        "opencv-python-headless": "5.1.0.95",
    }


def test_a_matching_artifact_reports_nothing(tmp_path: Path) -> None:
    """
    Given  an artifact whose license files are the tree's own bytes
    When   problems() is called
    Then   there is nothing to report
    """
    tree = _tree(tmp_path)

    assert artifacts.problems(_carried(tree), tree) == []


def test_a_missing_file_is_named(tmp_path: Path) -> None:
    """
    Given  a wheel that lacks one of the license files
    When   problems() is called
    Then   the file is named as missing
    """
    tree = _tree(tmp_path)
    carried = _carried(tree)
    del carried["teachers_teammate/assets/third_party_licenses.txt"]

    assert artifacts.problems(carried, tree) == [
        "teachers_teammate/assets/third_party_licenses.txt is missing"
    ]


def test_a_changed_file_names_both_sides(tmp_path: Path) -> None:
    """
    Given  an artifact whose license text was edited
    When   problems() is called
    Then   the message names the file and the tree's own copy
    """
    tree = _tree(tmp_path)
    carried = _carried(tree)
    carried["teachers_teammate/assets/third_party_inventory.csv"] = b"a hand edit\n"

    problems = artifacts.problems(carried, tree)

    assert len(problems) == 1
    assert "third_party_inventory.csv differs from" in problems[0]


def test_only_the_named_files_are_compared(tmp_path: Path) -> None:
    """
    Given  a wheel that carries one license file and an empty list of names
    When   problems() is asked for no file at all
    Then   it compares nothing, so a caller decides which of them an artifact has to carry
    """
    tree = _tree(tmp_path)
    carried = {"teachers_teammate/assets/third_party_licenses.txt": b"another text"}

    assert artifacts.problems(carried, tree, ()) == []


def test_a_matching_version_reports_nothing(tmp_path: Path) -> None:
    """
    Given  an environment that resolved the versions the inventory names
    When   version_problems() is called
    Then   there is nothing to report
    """
    locked = artifacts.locked_versions(_tree(tmp_path))
    reported = {"PySide6-Essentials": "6.12.0", "opencv-python-headless": "5.1.0.95"}

    assert artifacts.version_problems(reported, locked) == []


def test_a_changed_version_names_both(tmp_path: Path) -> None:
    """
    Given  an environment that resolved a newer Qt than the inventory lists
    When   version_problems() is called
    Then   the message names the version and the inventory's own
    """
    locked = artifacts.locked_versions(_tree(tmp_path))
    reported = {"PySide6-Essentials": "6.12.1", "opencv-python-headless": "5.1.0.95"}

    assert artifacts.version_problems(reported, locked) == [
        "PySide6-Essentials is 6.12.1, the inventory says pyside6-essentials 6.12.0"
    ]


def test_a_version_nobody_reported_is_named(tmp_path: Path) -> None:
    """
    Given  a bundle whose smoke result reported no OpenCV version
    When   version_problems() is called
    Then   the missing report is a problem, so the check cannot pass by silence
    """
    locked = artifacts.locked_versions(_tree(tmp_path))

    assert artifacts.version_problems({"PySide6": "6.12.0"}, locked, artifacts.SMOKE_REPORTED) == [
        "cv2 reported no version"
    ]


def test_a_row_the_inventory_lacks_is_named(tmp_path: Path) -> None:
    """
    Given  an inventory without the row a reported part belongs to
    When   version_problems() is called
    Then   the missing row is a problem, not a silent pass
    """
    locked = {"opencv-python-headless": "5.1.0.95"}

    reported = {"PySide6": "6.12.0", "cv2": "5.1.0.95"}

    assert artifacts.version_problems(reported, locked, artifacts.SMOKE_REPORTED) == [
        "the inventory has no row for pyside6-essentials"
    ]
