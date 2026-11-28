"""Unit tests for teachers_teammate.infrastructure.addon_staging."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys

import pytest

from teachers_teammate.exceptions import AddonInstallError, InstallFailure
from teachers_teammate.infrastructure import addon_folder, addon_list, addon_staging
from teachers_teammate.infrastructure.storage_root import app_data_folder


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


def test_merge_does_not_copy_a_package_the_bundle_has(tmp_path: Path, frozen_bundle: Path) -> None:
    """
    Given  a staging folder with numpy at the version the bundle pins, and spaCy
    When   the staged packages are merged into the live folder
    Then   numpy is not copied (the bundle has it) and spaCy moves in
    """
    live = tmp_path / "packages"
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "numpy", "2.5.4")
    _dist_info(staging, "spacy", "3.8.0")

    pending = addon_staging.merge_staged_packages(staging, live, frozen_bundle)

    assert pending == []
    assert not (live / "numpy").exists()
    assert not (live / "numpy-2.5.4.dist-info").exists()
    assert (live / "spacy" / "__init__.py").is_file()
    assert (live / "spacy-3.8.0.dist-info" / "RECORD").is_file()


def test_merge_skips_a_version_that_is_already_live(tmp_path: Path) -> None:
    """
    Given  a live folder that already holds spaCy 3.8.0, and a staging folder with the same
    When   the staged packages are merged
    Then   nothing is copied over the live copy
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.8.0")
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/extra.py",))

    assert addon_staging.merge_staged_packages(staging, live, None) == []
    assert not (live / "spacy" / "extra.py").exists()


def test_merge_replaces_an_older_copy_by_its_record(tmp_path: Path) -> None:
    """
    Given  spaCy 3.7.0 live and 3.8.0 staged
    When   the staged packages are merged
    Then   the old copy's files and dist-info go, and the new ones are in place
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.7.0", files=("spacy/old.py",))
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))

    assert addon_staging.merge_staged_packages(staging, live, None) == []
    assert not (live / "spacy-3.7.0.dist-info").exists()
    assert not (live / "spacy" / "old.py").exists()
    assert (live / "spacy" / "new.py").is_file()
    assert (live / "spacy-3.8.0.dist-info").is_dir()


def _lock_file(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Make Windows refuse to move the file called *name* (it is loaded by the running app)."""
    real_replace = os.replace

    def locked(source: object, destination: object, *args: object, **kwargs: object) -> None:
        if Path(str(source)).name == name:
            raise PermissionError(13, "Zugriff verweigert", str(source))
        real_replace(source, destination, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(os, "replace", locked)


def test_a_locked_file_parks_the_distribution_in_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a live pydantic_core with a compiled module Windows has locked, and a newer one staged
    When   the staged packages are merged
    Then   pydantic_core waits in packages.pending/, the other package is merged anyway, and the
           old copy is still whole: no file of it was deleted before the locked one was found
    """
    live = tmp_path / "packages"
    old_files = ("pydantic_core/other.py", "pydantic_core/_core.pyd")
    _dist_info(live, "pydantic_core", "2.0.0", files=old_files)
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "pydantic_core", "2.1.0", files=("pydantic_core/_core.pyd",))
    _dist_info(staging, "spacy", "3.8.0")
    _lock_file(monkeypatch, "_core.pyd")

    pending = addon_staging.merge_staged_packages(staging, live, None)

    assert pending == ["pydantic-core"]
    assert (tmp_path / "packages.pending" / "pydantic_core-2.1.0.dist-info").is_dir()
    assert (tmp_path / "packages.pending" / "pydantic_core" / "_core.pyd").is_file()
    assert (live / "spacy-3.8.0.dist-info").is_dir()  # the merge went on
    for file in ("pydantic_core/__init__.py", *old_files):
        assert (live / file).is_file(), file  # the old copy is whole
    assert (live / "pydantic_core-2.0.0.dist-info" / "RECORD").is_file()
    assert not (tmp_path / "packages.trash").exists()


def test_a_replaced_copy_leaves_nothing_in_the_trash(tmp_path: Path) -> None:
    """
    Given  spaCy 3.7.0 live and 3.8.0 staged
    When   the old copy is replaced
    Then   the trash folder the old files passed through is gone again
    """
    live = tmp_path / "packages"
    _dist_info(live, "spacy", "3.7.0", files=("spacy/old.py",))
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0")

    addon_staging.merge_staged_packages(staging, live, None)

    assert not (tmp_path / "packages.trash").exists()


def _fail_moves(monkeypatch: pytest.MonkeyPatch, name: str, *, only_into: Path | None) -> None:
    """Make moving the file *name* fail like a full disk, into *only_into* or anywhere."""
    real_move = shutil.move

    def flaky(source: object, destination: object, *args: object, **kwargs: object) -> object:
        wanted = only_into is None or only_into in Path(str(destination)).parents
        if Path(str(source)).name == name and wanted:
            raise OSError(28, "No space left on device", str(destination))
        return real_move(source, destination, *args, **kwargs)  # ty: ignore[invalid-argument-type]

    monkeypatch.setattr(shutil, "move", flaky)


def test_a_package_that_cannot_be_put_anywhere_is_an_error_and_leaves_no_half_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staged spaCy whose second file cannot be moved, into the live and the pending folder
    When   the staged packages are merged
    Then   the merge raises "merge failed" instead of reporting success, and neither folder
           keeps a half package: what moved was put back
    """
    live = tmp_path / "packages"
    live.mkdir()
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))
    _fail_moves(monkeypatch, "new.py", only_into=None)

    with pytest.raises(AddonInstallError) as raised:
        addon_staging.merge_staged_packages(staging, live, None)

    assert raised.value.reason is InstallFailure.MERGE_FAILED
    assert not any(live.rglob("*.py"))
    assert not (live / "spacy-3.8.0.dist-info").exists()
    assert not (tmp_path / "packages.pending" / "spacy-3.8.0.dist-info").exists()
    assert (staging / "spacy" / "__init__.py").is_file()  # still whole where pip put it
    assert (staging / "spacy" / "new.py").is_file()


def test_a_failed_move_into_the_live_folder_falls_back_to_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staged spaCy that cannot be moved into the live folder, but into the pending one
    When   the staged packages are merged
    Then   spaCy is reported pending and complete there, and the live folder holds no half copy
    """
    live = tmp_path / "packages"
    live.mkdir()
    staging = tmp_path / "packages.staging" / "1"
    _dist_info(staging, "spacy", "3.8.0", files=("spacy/new.py",))
    _fail_moves(monkeypatch, "new.py", only_into=live)

    pending = addon_staging.merge_staged_packages(staging, live, None)

    assert pending == ["spacy"]
    assert not any(live.rglob("*.py"))
    assert (tmp_path / "packages.pending" / "spacy" / "new.py").is_file()
    assert (tmp_path / "packages.pending" / "spacy-3.8.0.dist-info").is_dir()


def _age(folder: Path, *, hours: int) -> None:
    """Make *folder* look *hours* old."""
    then = folder.stat().st_mtime - hours * 3600
    os.utime(folder, (then, then))


def test_prepare_applies_pending_and_drops_redundant_copies(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pending pydantic_core, a redundant numpy copy, a leftover staging folder
    When   prepare_packages_dir() runs at start-up
    Then   the pending package is in place, the redundant copy and the staging folder are gone
    """
    live = tmp_path / "packages"
    _dist_info(live, "numpy", "2.5.4")  # the bundle has it at this version
    _dist_info(live, "spacy", "3.8.0")
    _dist_info(tmp_path / "packages.pending", "pydantic_core", "2.1.0")
    _dist_info(tmp_path / "packages.staging" / "1", "spacy", "3.9.0")
    _age(tmp_path / "packages.staging" / "1", hours=24)  # a crash left it, a day ago
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)

    addon_staging.prepare_packages_dir()

    assert (live / "pydantic_core-2.1.0.dist-info").is_dir()
    assert not (tmp_path / "packages.pending").exists()
    assert not (live / "numpy").exists()
    assert not (live / "numpy-2.5.4.dist-info").exists()
    assert (live / "spacy-3.8.0.dist-info").is_dir()
    assert not (tmp_path / "packages.staging").exists()


def test_prepare_once_cleans_a_folder_a_single_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pip install's entry point, which starts with no earlier cleanup
    When   the cleanup is asked for twice in one process
    Then   the retired add-on is removed by the first, and the folder is not walked again
    """
    live = tmp_path / "packages"
    _plant_package(live, "paddleocr-3.0.0", ["paddleocr/__init__.py"])
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)
    monkeypatch.setattr(addon_staging, "_prepared", set())
    retired = addon_folder.remove_retired_addons
    calls: list[Path] = []
    monkeypatch.setattr(
        addon_staging, "remove_retired_addons", lambda path: calls.append(path) or retired(path)
    )

    addon_staging.prepare_packages_dir_once()
    addon_staging.prepare_packages_dir_once()

    assert calls == [live]
    assert not (live / "paddleocr").exists()


def test_prepare_removes_a_retired_addon(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Given  the packages folder holding an add-on that is no longer installed
    When   prepare_packages_dir() runs at start-up
    Then   its files are gone before anything is imported from the folder
    """
    live = tmp_path / "packages"
    _plant_package(live, "paddleocr-3.0.0", ["paddleocr/__init__.py"])
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)

    addon_staging.prepare_packages_dir()

    assert not (live / "paddleocr").exists()


def test_prepare_keeps_the_staging_folder_of_an_install_that_may_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a staging folder just made, as a second app instance's install would have, and an
           old one a crash left, and a trash folder
    When   prepare_packages_dir() runs at start-up
    Then   only the old staging folder and the trash go
    """
    live = tmp_path / "packages"
    live.mkdir()
    (tmp_path / "packages.staging" / "fresh").mkdir(parents=True)
    (tmp_path / "packages.staging" / "old").mkdir()
    _age(tmp_path / "packages.staging" / "old", hours=24)
    (tmp_path / "packages.trash" / "1").mkdir(parents=True)
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)

    addon_staging.prepare_packages_dir()

    assert (tmp_path / "packages.staging" / "fresh").is_dir()
    assert not (tmp_path / "packages.staging" / "old").exists()
    assert not (tmp_path / "packages.trash").exists()


def test_prepare_never_stops_the_app_from_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pending-package step that fails with a permission error
    When   prepare_packages_dir() runs at start-up
    Then   it does not raise, and the clean-up steps after it still run
    """
    live = tmp_path / "packages"
    live.mkdir()
    (tmp_path / "packages.staging" / "old").mkdir(parents=True)
    _age(tmp_path / "packages.staging" / "old", hours=24)
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)

    def denied(*_args: object, **_kwargs: object) -> list[str]:
        raise PermissionError(13, "Zugriff verweigert")

    monkeypatch.setattr(addon_staging, "apply_pending_packages", denied)

    addon_staging.prepare_packages_dir()

    assert not (tmp_path / "packages.staging").exists()


def test_a_pending_package_is_reported_until_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a package waiting in packages.pending/
    When   the app asks whether a restart is needed
    Then   it says so, names the package, and stops saying so once the folder is empty
    """
    live = tmp_path / "packages"
    _dist_info(tmp_path / "packages.pending", "spacy", "3.8.0")
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: live.parent)

    assert addon_staging.addons_restart_needed() is True
    assert addon_staging.pending_addon_packages() == ["spacy"]
    assert addon_staging.addon_waits_for_restart("spaCy") is True
    assert addon_staging.addon_waits_for_restart("langchain-openai") is False

    shutil.rmtree(tmp_path / "packages.pending")
    assert addon_staging.addons_restart_needed() is False
