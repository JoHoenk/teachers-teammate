"""Unit tests for teachers_teammate.infrastructure.addon_folder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import os
import sys

import pytest

from teachers_teammate.infrastructure import addon_folder, addon_list
from teachers_teammate.infrastructure.addon_folder import (
    get_packages_dir,
    packages_dir_readable,
    remove_retired_addons,
)


# ── get_packages_dir ──────────────────────────────────────────────────────────


def test_get_packages_dir_returns_path() -> None:
    """
    Given  no special setup
    When   get_packages_dir() is called
    Then   it returns a Path instance
    """
    result = get_packages_dir()
    assert isinstance(result, Path)
    assert result.name == "packages"


# ── packages_dir_readable ────────────────────────────────────────────────────


def test_packages_dir_readable_true_for_empty_dir(tmp_path: Path) -> None:
    """
    Given  an empty packages directory
    When   packages_dir_readable() is called
    Then   True is returned
    """
    assert packages_dir_readable(tmp_path) is True


def test_packages_dir_readable_true_for_readable_py(tmp_path: Path) -> None:
    """
    Given  a packages directory containing a readable .py file
    When   packages_dir_readable() is called
    Then   True is returned
    """
    (tmp_path / "mod.py").write_text("x = 1\n")
    assert packages_dir_readable(tmp_path) is True


def test_packages_dir_readable_false_on_permission_error(tmp_path: Path) -> None:
    """
    Given  opening a .py file in the packages dir raises PermissionError
    When   packages_dir_readable() is called
    Then   False is returned
    """
    (tmp_path / "mod.py").write_text("x = 1\n")
    with patch("pathlib.Path.open", side_effect=PermissionError):
        assert packages_dir_readable(tmp_path) is False


def test_packages_dir_readable_false_when_listing_denied(tmp_path: Path) -> None:
    """
    Given  iterating the packages directory raises PermissionError
    When   packages_dir_readable() is called
    Then   False is returned
    """
    with patch("pathlib.Path.iterdir", side_effect=PermissionError):
        assert packages_dir_readable(tmp_path) is False


# ── where the packages live ──────────────────────────────────────────────────


def test_the_packages_folder_sits_in_the_user_data_folder() -> None:
    """
    Given  the per-user data folder of the app
    When   get_packages_dir() is called
    Then   it is the "packages" folder in there, beside the models
    """
    assert get_packages_dir() == addon_folder.app_data_folder() / "packages"


def test_a_folder_that_did_not_change_is_listed_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a packages folder that holds one package
    When   its distributions are asked for twice in a row
    Then   the folder is listed once, and a package added afterwards is seen all the same
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    _age(live, hours=1)
    listings: list[Path] = []
    read = addon_folder._read_distributions

    def counting(pkg_dir: Path) -> list[tuple[str, str, Path]]:
        listings.append(pkg_dir)
        return read(pkg_dir)

    monkeypatch.setattr(addon_folder, "_read_distributions", counting)

    first = addon_folder.distributions(live)
    second = addon_folder.distributions(live)
    _dist_info(live, "langchain_core", "1.4.7")
    third = addon_folder.distributions(live)

    assert first == second
    assert len(listings) == 2
    assert {name for name, _version, _folder in third} == {"spacy", "langchain-core"}


def test_a_folder_changed_a_moment_ago_is_listed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a packages folder whose stamp is as new as the clock can tell
    When   its distributions are asked for, a package is added within the same tick, and they
           are asked for again
    Then   the second answer holds the new package: a listing that fresh is never kept
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    stamp = live.stat().st_mtime_ns
    first = addon_folder.distributions(live)
    _dist_info(live, "langchain_core", "1.4.7")
    os.utime(live, ns=(stamp, stamp))  # the same stamp: the change fell into one tick

    second = addon_folder.distributions(live)

    assert {name for name, _version, _folder in first} == {"spacy"}
    assert {name for name, _version, _folder in second} == {"spacy", "langchain-core"}


# ── remove_retired_addons ─────────────────────────────────────────────────────


def _plant_package(pkg_dir: Path, dist: str, files: list[str], *, record: bool = True) -> None:
    """Create *files* under *pkg_dir* and a ``<dist>.dist-info`` that lists them in RECORD."""
    dist_info = pkg_dir / f"{dist}.dist-info"
    dist_info.mkdir(parents=True)
    for name in files:
        path = pkg_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    if record:
        lines = [f"{name},sha256=abc,1" for name in files]
        lines.append(f"{dist}.dist-info/RECORD,,")
        (dist_info / "RECORD").write_text("\n".join(lines), encoding="utf-8")


def test_remove_retired_addons_deletes_exactly_the_recorded_files(tmp_path: Path) -> None:
    """
    Given  a packages folder with the old Paddle add-on and an unrelated package
    When   remove_retired_addons() runs
    Then   only the files in the retired packages' RECORD go, the empty folders with them
    """
    _plant_package(tmp_path, "paddleocr-3.0.0", ["paddleocr/__init__.py", "paddleocr/sub/m.py"])
    _plant_package(tmp_path, "opencv_contrib_python-4.10.0.84", ["cv2/__init__.py"])
    _plant_package(tmp_path, "spacy-3.7.0", ["spacy/__init__.py"])
    (tmp_path / "paddleocr" / "stray.txt").write_text("not in RECORD", encoding="utf-8")

    removed = remove_retired_addons(tmp_path)

    assert sorted(removed) == ["opencv-contrib-python", "paddleocr"]
    assert not (tmp_path / "paddleocr" / "__init__.py").exists()
    assert not (tmp_path / "paddleocr" / "sub").exists()
    assert (tmp_path / "paddleocr" / "stray.txt").exists()  # not listed: left alone
    assert not (tmp_path / "cv2").exists()
    assert not (tmp_path / "paddleocr-3.0.0.dist-info").exists()
    assert (tmp_path / "spacy" / "__init__.py").exists()
    assert (tmp_path / "spacy-3.7.0.dist-info" / "RECORD").exists()


def test_remove_retired_addons_deletes_the_gpu_monitoring_packages(tmp_path: Path) -> None:
    """
    Given  a packages folder with the GPU-monitoring packages an earlier version installed
    When   remove_retired_addons() runs
    Then   both go: the app reads the card itself now (NVML is bundled, AMD comes from sysfs)
    """
    _plant_package(tmp_path, "nvidia_ml_py-12.560.30", ["pynvml.py"])
    _plant_package(tmp_path, "pyamdgpuinfo-2.1.7", ["pyamdgpuinfo/__init__.py"])

    removed = remove_retired_addons(tmp_path)

    assert sorted(removed) == ["nvidia-ml-py", "pyamdgpuinfo"]
    assert not (tmp_path / "pynvml.py").exists()
    assert not (tmp_path / "pyamdgpuinfo").exists()


def test_remove_retired_addons_leaves_a_package_without_record(tmp_path: Path) -> None:
    """
    Given  a retired package whose dist-info has no RECORD
    When   remove_retired_addons() runs
    Then   nothing of it is touched
    """
    _plant_package(tmp_path, "paddlepaddle-3.0.0", ["paddle/__init__.py"], record=False)

    assert remove_retired_addons(tmp_path) == []
    assert (tmp_path / "paddle" / "__init__.py").exists()


def test_remove_retired_addons_never_leaves_the_packages_folder(tmp_path: Path) -> None:
    """
    Given  a RECORD that lists a path outside the packages folder
    When   remove_retired_addons() runs
    Then   the outside file is not deleted
    """
    packages = tmp_path / "packages"
    packages.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    _plant_package(packages, "paddlex-3.0.0", ["paddlex/__init__.py", "../outside.txt"])

    remove_retired_addons(packages)

    assert outside.exists()
    assert not (packages / "paddlex").exists()


def test_remove_retired_addons_ignores_a_missing_folder(tmp_path: Path) -> None:
    """
    Given  a packages folder that does not exist
    When   remove_retired_addons() runs
    Then   it returns an empty list
    """
    assert remove_retired_addons(tmp_path / "nope") == []


# ── The bundle's constraints: the pip policy, outdated copies, the update ─────


def _dist_info(
    pkg_dir: Path,
    name: str,
    version: str,
    files: tuple[str, ...] = (),
    tags: tuple[str, ...] = (),
) -> None:
    """Plant what pip leaves for a package: ``<name>-<version>.dist-info`` with a RECORD.

    *tags* become the ``Tag:`` lines of the ``WHEEL`` file pip writes beside the RECORD; without
    them the distribution has no WHEEL file at all.
    """
    info = pkg_dir / f"{name}-{version}.dist-info"
    info.mkdir(parents=True)
    record = [f"{name}/__init__.py,sha256=x,1", *(f"{file},sha256=x,1" for file in files)]
    (pkg_dir / name).mkdir(exist_ok=True)
    (pkg_dir / name / "__init__.py").write_text("", encoding="utf-8")
    for file in files:
        (pkg_dir / file).parent.mkdir(parents=True, exist_ok=True)
        (pkg_dir / file).write_text("", encoding="utf-8")
    (info / "RECORD").write_text("\n".join(record) + "\n", encoding="utf-8")
    if tags:
        wheel = ["Wheel-Version: 1.0", "Generator: bdist_wheel (0.43.0)", "Root-Is-Purelib: false"]
        wheel += [f"Tag: {tag}" for tag in tags]
        (info / "WHEEL").write_text("\n".join(wheel) + "\n", encoding="utf-8")


@pytest.fixture(name="frozen_bundle")
def _frozen_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A frozen app whose bundle pins numpy 2.5.4 and pydantic 2.14.3; returns the pins file."""
    bundle = tmp_path / "bundle"
    pins = bundle / addon_list.ADDON_CONSTRAINTS_RELATIVE
    pins.parent.mkdir(parents=True)
    pins.write_text("numpy==2.5.4\npydantic==2.14.3\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    return pins


def test_outdated_shared_packages_compares_folder_and_constraints(
    tmp_path: Path, frozen_bundle: Path
) -> None:
    """
    Given  a folder with numpy 2.4.0 (the bundle has 2.5.4), pydantic 2.14.3 and spaCy
    When   the outdated packages are asked for
    Then   only numpy is named: same version and packages the app does not ship do not count
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0")
    _dist_info(pkg_dir, "pydantic", "2.14.3")
    _dist_info(pkg_dir, "spacy", "3.8.0")

    assert addon_folder.outdated_shared_packages(pkg_dir, frozen_bundle) == ["numpy"]


def test_nothing_is_outdated_without_constraints_or_folder(tmp_path: Path) -> None:
    """
    Given  a source run (no constraints), and a folder that does not exist
    When   the outdated packages are asked for
    Then   the list is empty
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "1.0")

    assert addon_folder.outdated_shared_packages(pkg_dir, None) == []
    assert addon_folder.outdated_shared_packages(tmp_path / "none", tmp_path / "c.txt") == []


def test_installed_addon_requirements_lists_what_is_in_the_folder(tmp_path: Path) -> None:
    """
    Given  a folder with spaCy and langchain-openai (and a language model)
    When   the installed add-on requirements are asked for
    Then   the add-ons' requirements come back, not the model or anything else
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    _dist_info(pkg_dir, "langchain_openai", "1.0.0")
    _dist_info(pkg_dir, "en_core_web_sm", "3.8.0")
    _dist_info(pkg_dir, "numpy", "2.4.0")

    assert addon_folder.installed_addon_requirements(pkg_dir) == [
        "spacy>=3.7",
        "langchain-openai>=0.2",
    ]
    assert addon_folder.addon_in_folder("spaCy", pkg_dir)
    assert not addon_folder.addon_in_folder("langchain-cohere", pkg_dir)


def test_drop_outdated_shared_packages_removes_by_record(
    tmp_path: Path, frozen_bundle: Path
) -> None:
    """
    Given  an outdated numpy copy, a current pydantic and spaCy in the folder
    When   the outdated copies are dropped
    Then   numpy's files are gone and everything else stays
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0", files=("numpy/core/a.py",))
    _dist_info(pkg_dir, "pydantic", "2.14.3")
    _dist_info(pkg_dir, "spacy", "3.8.0")

    removed = addon_folder.drop_outdated_shared_packages(pkg_dir, frozen_bundle)

    assert removed == ["numpy"]
    assert not (pkg_dir / "numpy").exists()
    assert not (pkg_dir / "numpy-2.4.0.dist-info").exists()
    assert (pkg_dir / "pydantic").is_dir()
    assert (pkg_dir / "spacy-3.8.0.dist-info").is_dir()


# ── Add-ons built for another Python ─────────────────────────────────────────


@pytest.fixture(name="python_314")
def _python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the installed app runs CPython 3.14 (the version is patched, not read)."""
    monkeypatch.setattr(addon_folder, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.mark.parametrize(
    ("tags", "foreign"),
    [
        (("cp312-cp312-manylinux_2_35_x86_64",), True),
        (("cp38-abi3-manylinux_2_35_x86_64",), False),
        (("py3-none-any",), False),
        (("cp314-cp314-manylinux_2_35_x86_64",), False),
        (("py313-none-any",), False),
        (("py315-none-any",), True),
        (("cp314-none-manylinux_2_35_x86_64",), False),
        (("cp313-none-any",), True),
        (("cp315-abi3-manylinux_2_35_x86_64",), True),
    ],
)
def test_foreign_python_packages_reads_the_wheel_tags(
    tmp_path: Path, python_314: None, tags: tuple[str, ...], foreign: bool
) -> None:
    """
    Given  a distribution whose WHEEL names *tags*
    When   the packages built for another Python are asked for
    Then   only a tag the running 3.14 cannot import counts as foreign
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=tags)

    assert addon_folder.foreign_python_packages(pkg_dir) == (["spacy"] if foreign else [])


def test_a_distribution_without_a_wheel_file_is_not_foreign(
    tmp_path: Path, python_314: None
) -> None:
    """
    Given  a distribution whose dist-info has no WHEEL file
    When   the packages built for another Python are asked for
    Then   it is left alone: nothing says which interpreter built it
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")

    assert addon_folder.foreign_python_packages(pkg_dir) == []


def test_a_language_model_stays(tmp_path: Path, python_314: None) -> None:
    """
    Given  a spaCy language model, which is a py3-none-any wheel
    When   the packages built for another Python are asked for
    Then   the model is not named, so an app update keeps it
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "en_core_web_sm", "3.8.0", tags=("py3-none-any",))

    assert addon_folder.foreign_python_packages(pkg_dir) == []


def test_a_wheel_file_that_is_not_text_is_left_alone(tmp_path: Path, python_314: None) -> None:
    """
    Given  a distribution whose WHEEL file is not valid UTF-8
    When   the packages built for another Python are asked for
    Then   nothing raises and the distribution is not named
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0")
    (pkg_dir / "spacy-3.8.0.dist-info" / "WHEEL").write_bytes(b"\xff\xfe\x00Tag: cp312")

    assert addon_folder.foreign_python_packages(pkg_dir) == []


def test_a_start_up_with_nothing_redundant_reads_no_record(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder without any copy of a bundled package
    When   the redundant copies are dropped (which every start does)
    Then   no RECORD file is read
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")

    def must_not_read(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a RECORD was read")

    monkeypatch.setattr(addon_folder, "record_targets", must_not_read)

    assert addon_folder.drop_redundant_shared_packages(live, frozen_bundle) == []


def _age(folder: Path, *, hours: int) -> None:
    """Make *folder* look *hours* old."""
    then = folder.stat().st_mtime - hours * 3600
    os.utime(folder, (then, then))
