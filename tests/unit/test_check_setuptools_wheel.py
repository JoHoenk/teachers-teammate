"""The setuptools wheel check: the declared package data is in the wheel."""

from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from tools.ci import check_setuptools_wheel


def _pyproject(tmp_path: Path, *assets: str) -> Path:
    """Return a pyproject.toml whose package-data lists *assets*."""
    path = tmp_path / "pyproject.toml"
    listed = ", ".join(f'"{name}"' for name in assets)
    path.write_text(f"[tool.setuptools.package-data]\nteachers_teammate = [{listed}]\n")
    return path


def _wheel(folder: Path, *members: str) -> Path:
    """Return a wheel in *folder* that holds an empty file for each of *members*."""
    folder.mkdir(exist_ok=True)
    wheel = folder / "teachers_teammate-1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for member in members:
            archive.writestr(member, "")
    return wheel


def test_a_wheel_with_every_declared_asset_passes(tmp_path: Path) -> None:
    """
    Given  a wheel that holds the declared asset
    When   it is checked
    Then   the check passes
    """
    wheel = _wheel(tmp_path / "dist", "teachers_teammate/icon.png")
    pyproject = _pyproject(tmp_path, "icon.png")

    code = check_setuptools_wheel.main([str(wheel.parent), "--pyproject", str(pyproject)])

    assert code == 0


def test_a_missing_asset_is_named(tmp_path: Path) -> None:
    """
    Given  a wheel without a declared asset
    When   it is checked
    Then   the failure names the missing file
    """
    wheel = _wheel(tmp_path / "dist", "teachers_teammate/other.png")
    pyproject = _pyproject(tmp_path, "icon.png")

    with pytest.raises(SystemExit, match=r"teachers_teammate/icon\.png"):
        check_setuptools_wheel.main([str(wheel.parent), "--pyproject", str(pyproject)])


def test_an_empty_declaration_fails(tmp_path: Path) -> None:
    """
    Given  a pyproject that declares no package data
    When   it is checked
    Then   it fails, because a check of nothing would pass for the wrong reason
    """
    wheel = _wheel(tmp_path / "dist", "teachers_teammate/icon.png")
    pyproject = _pyproject(tmp_path)

    with pytest.raises(SystemExit):
        check_setuptools_wheel.main([str(wheel.parent), "--pyproject", str(pyproject)])
