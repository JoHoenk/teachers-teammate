"""Unit tests for teachers_teammate.infrastructure.addon_imports."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import importlib
import shutil
import sys

import pytest

from teachers_teammate.domain.local_models import PRIVACY_ADDON_ID
from teachers_teammate.infrastructure import addon_folder, addon_imports, addon_list
from teachers_teammate.infrastructure.addon_imports import (
    can_install_addons,
    clear_stale_modules,
    inject_packages_dir,
    is_addon_importable,
)
from teachers_teammate.infrastructure.storage_root import app_data_folder


# ── inject_packages_dir ───────────────────────────────────────────────────────


def test_inject_packages_dir_inserts_at_index_zero(tmp_path: Path) -> None:
    """
    Given  the packages dir is not on sys.path
    When   inject_packages_dir() is called
    Then   the packages dir is inserted at sys.path[0]
    """
    pkg_dir = tmp_path / "packages"
    with patch(
        "teachers_teammate.infrastructure.addon_imports.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        inject_packages_dir()
        assert sys.path[0] == str(pkg_dir)
        # Restore
        sys.path[:] = original


def test_inject_packages_dir_is_idempotent(tmp_path: Path) -> None:
    """
    Given  inject_packages_dir() has already been called once
    When   inject_packages_dir() is called a second time
    Then   the packages dir appears at most once in sys.path
    """
    pkg_dir = tmp_path / "packages2"
    pkg_str = str(pkg_dir)
    with patch(
        "teachers_teammate.infrastructure.addon_imports.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        inject_packages_dir()
        inject_packages_dir()
        assert sys.path.count(pkg_str) == 1
        sys.path[:] = original


def test_inject_packages_dir_does_not_double_insert_if_already_present(tmp_path: Path) -> None:
    """
    Given  the packages dir string is already present in sys.path
    When   inject_packages_dir() is called
    Then   no duplicate entry is added to sys.path
    """
    pkg_dir = tmp_path / "packages3"
    pkg_str = str(pkg_dir)
    with patch(
        "teachers_teammate.infrastructure.addon_imports.get_packages_dir",
        return_value=pkg_dir,
    ):
        original = sys.path.copy()
        sys.path.insert(0, pkg_str)
        inject_packages_dir()
        assert sys.path.count(pkg_str) == 1
        sys.path[:] = original


# ── is_addon_importable ───────────────────────────────────────────────────────


def test_is_addon_importable_returns_false_when_spacy_missing() -> None:
    """
    Given  spacy is not installed (absent from sys.modules)
    When   is_addon_importable(PRIVACY_ADDON_ID) is called
    Then   False is returned
    """
    with patch.dict(sys.modules, {"spacy": None}):
        assert is_addon_importable(PRIVACY_ADDON_ID) is False


def test_is_addon_importable_returns_true_when_spacy_present() -> None:
    """
    Given  spacy is importable (find_spec returns a non-None spec)
    When   is_addon_importable(PRIVACY_ADDON_ID) is called
    Then   True is returned
    """
    # Patch find_spec at the import site: inserting a MagicMock into sys.modules causes
    # importlib.util.find_spec to raise ValueError because __spec__ is not set on MagicMock.
    with patch(
        "importlib.util.find_spec",
        return_value=MagicMock(),
    ):
        assert is_addon_importable(PRIVACY_ADDON_ID) is True


def test_is_addon_importable_unknown_addon_returns_false() -> None:
    """
    Given  an unrecognised addon name not in the registry
    When   is_addon_importable() is called
    Then   False is returned
    """
    assert is_addon_importable("nonexistent") is False


# ── clear_stale_modules ───────────────────────────────────────────────────────


def test_clear_stale_modules_removes_spacy_entries() -> None:
    """
    Given  spacy and submodule entries are present in sys.modules
    When   clear_stale_modules(PRIVACY_ADDON_ID) is called
    Then   all spacy-prefixed keys are removed from sys.modules
    """
    fake_modules = {"spacy": MagicMock(), "spacy.util": MagicMock(), "spacy.lang.en": MagicMock()}
    with patch.dict(sys.modules, fake_modules):
        clear_stale_modules(PRIVACY_ADDON_ID)
        assert "spacy" not in sys.modules
        assert "spacy.util" not in sys.modules
        assert "spacy.lang.en" not in sys.modules


def test_clear_stale_modules_leaves_unrelated_modules() -> None:
    """
    Given  unrelated modules (e.g. 'requests') are in sys.modules
    When   clear_stale_modules(PRIVACY_ADDON_ID) is called
    Then   the unrelated modules remain untouched
    """
    with patch.dict(sys.modules, {"spacy": MagicMock()}):
        requests_before = sys.modules.get("requests")
        clear_stale_modules(PRIVACY_ADDON_ID)
        assert sys.modules.get("requests") is requests_before


def test_clear_stale_modules_unknown_addon_is_noop() -> None:
    """
    Given  an unrecognised addon name
    When   clear_stale_modules() is called
    Then   no exception is raised (silently ignored)
    """
    clear_stale_modules("nonexistent")  # must not raise


# ── inject_packages_dir skip-when-unreadable ─────────────────────────────────


def test_inject_packages_dir_skips_when_unreadable(tmp_path: Path, monkeypatch) -> None:
    """
    Given  the packages directory exists but is unreadable
    When   inject_packages_dir() is called
    Then   the directory is not added to sys.path
    """
    pkg_dir = tmp_path / "packages"
    pkg_dir.mkdir()
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)
    monkeypatch.setattr(addon_imports, "packages_dir_readable", lambda _p: False)

    pkg_str = str(pkg_dir)
    original = list(sys.path)
    try:
        inject_packages_dir()
        assert pkg_str not in sys.path
    finally:
        sys.path[:] = original


# ── can_install_addons ────────────────────────────────────────────────────────


def test_can_install_addons_false_in_source_checkout(monkeypatch) -> None:
    """
    Given  a normal (not frozen) Python process
    When   can_install_addons is called
    Then   False is returned so a dev environment is never modified automatically
    """
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert can_install_addons() is False


def test_can_install_addons_true_for_frozen_build_with_pip(monkeypatch) -> None:
    """
    Given  a frozen build in which pip can be imported
    When   can_install_addons is called
    Then   True is returned
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with patch("importlib.util.find_spec", return_value=object()):
        assert can_install_addons() is True


def test_can_install_addons_false_for_frozen_build_without_pip(monkeypatch) -> None:
    """
    Given  a frozen build without a bundled pip
    When   can_install_addons is called
    Then   False is returned
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with patch("importlib.util.find_spec", return_value=None):
        assert can_install_addons() is False


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


def test_inject_skips_a_folder_from_an_older_app(
    tmp_path: Path, frozen_bundle: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder holding another numpy than the bundle
    When   inject_packages_dir() runs
    Then   the folder is not put on sys.path, and it is once the copy matches
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "numpy", "2.4.0")
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)

    inject_packages_dir()
    assert str(pkg_dir) not in sys.path
    assert addon_folder.addons_outdated() is True

    frozen_bundle.write_text("numpy==2.4.0\n", encoding="utf-8")
    inject_packages_dir()
    assert str(pkg_dir) in sys.path
    assert addon_folder.addons_outdated() is False


# ── Add-ons built for another Python ─────────────────────────────────────────


@pytest.fixture(name="python_314")
def _python_314(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the installed app runs CPython 3.14 (the version is patched, not read)."""
    monkeypatch.setattr(addon_folder, "_running_python", lambda: (3, 14))
    monkeypatch.setattr(sys, "frozen", True, raising=False)


def test_inject_skips_a_folder_built_for_another_python(
    tmp_path: Path, python_314: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  an add-on folder holding a distribution compiled for 3.12
    When   inject_packages_dir() runs
    Then   the folder is not put on sys.path, and it is once the copy is for 3.14
    """
    pkg_dir = tmp_path / "packages"
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp312-cp312-manylinux_2_35_x86_64",))
    monkeypatch.setattr(addon_folder, "app_data_folder", lambda: pkg_dir.parent)

    inject_packages_dir()
    assert str(pkg_dir) not in sys.path
    assert addon_folder.addons_outdated() is True

    shutil.rmtree(pkg_dir / "spacy-3.8.0.dist-info")
    _dist_info(pkg_dir, "spacy", "3.8.0", tags=("cp314-cp314-manylinux_2_35_x86_64",))
    inject_packages_dir()
    assert str(pkg_dir) in sys.path
    assert addon_folder.addons_outdated() is False
