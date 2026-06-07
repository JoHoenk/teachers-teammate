"""Unit tests for release version stamping tooling."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.release import stamp_versions


def test_normalize_version_strips_v_prefix() -> None:
    """
    Given  a version string prefixed with 'v'
    When   normalize_version() is called
    Then   the returned version has no leading 'v' and remains PEP 440-valid
    """
    assert stamp_versions.normalize_version("v1.2.3") == "1.2.3"


def test_normalize_version_rejects_invalid_value() -> None:
    """
    Given  a version string that is not PEP 440 compliant
    When   normalize_version() is called
    Then   a ValueError is raised
    """
    with pytest.raises(ValueError, match="not a valid PEP 440 version"):
        stamp_versions.normalize_version("version-one")


def test_stamp_init_version_updates_version_field() -> None:
    """
    Given  a __init__.py snippet with exactly one __version__ field
    When   stamp_init_version() is called
    Then   the __version__ field is updated to the requested value
    """
    content = '"""OCR pipeline package."""\n\n__version__ = "0.1.0"\n'
    updated = stamp_versions.stamp_init_version(content, "1.2.3")
    assert '__version__ = "1.2.3"' in updated


def test_stamp_init_version_requires_exactly_one_field() -> None:
    """
    Given  __init__.py content with no __version__ field
    When   stamp_init_version() is called
    Then   a ValueError is raised to prevent ambiguous stamping
    """
    content = '"""OCR pipeline package."""\n'
    with pytest.raises(ValueError, match="exactly one __version__ field"):
        stamp_versions.stamp_init_version(content, "1.2.3")


def test_stamp_bazel_wheel_version_updates_named_target_only() -> None:
    """
    Given  a BUILD snippet with two py_wheel targets
    When   stamp_bazel_wheel_version() is called for teachers_teammate_wheel
    Then   only that target's version is changed
    """
    content = (
        "py_wheel(\n"
        '    name = "other_wheel",\n'
        '    version = "0.0.1",\n'
        ")\n\n"
        "py_wheel(\n"
        '    name = "teachers_teammate_wheel",\n'
        '    version = "0.1.0",\n'
        ")\n"
    )
    updated = stamp_versions.stamp_bazel_wheel_version(content, "2.0.0", "teachers_teammate_wheel")
    assert 'name = "other_wheel",\n    version = "0.0.1",' in updated
    assert 'name = "teachers_teammate_wheel",\n    version = "2.0.0",' in updated


def test_stamp_bazel_wheel_version_fails_when_target_missing() -> None:
    """
    Given  a BUILD snippet without the requested py_wheel target
    When   stamp_bazel_wheel_version() is called
    Then   a ValueError is raised
    """
    content = 'py_wheel(\n    name = "other_wheel",\n    version = "0.0.1",\n)\n'
    with pytest.raises(ValueError, match="could not find py_wheel target"):
        stamp_versions.stamp_bazel_wheel_version(content, "2.0.0", "teachers_teammate_wheel")


def test_stamp_files_updates_both_files(tmp_path: Path) -> None:
    """
    Given  temporary __init__.py and BUILD files with old versions
    When   stamp_files() is called with a normalized version
    Then   both files are rewritten with the new version
    """
    init_path = tmp_path / "__init__.py"
    init_path.write_text('"""OCR pipeline package."""\n\n__version__ = "0.1.0"\n', encoding="utf-8")

    build_path = tmp_path / "BUILD"
    build_path.write_text(
        'py_wheel(\n    name = "teachers_teammate_wheel",\n    version = "0.1.0",\n)\n',
        encoding="utf-8",
    )

    stamp_versions.stamp_files("3.4.5", init_path, build_path, "teachers_teammate_wheel")

    assert '__version__ = "3.4.5"' in init_path.read_text(encoding="utf-8")
    assert 'version = "3.4.5"' in build_path.read_text(encoding="utf-8")
