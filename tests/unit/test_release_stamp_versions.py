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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v0.5.7-rc1", "0.5.7rc1"),
        ("v0.5.7-rc.1", "0.5.7rc1"),
        ("v0.5.7-alpha2", "0.5.7a2"),
        ("v0.5.7-beta.3", "0.5.7b3"),
        ("v0.5.7-pre", "0.5.7rc0"),
    ],
)
def test_normalize_version_accepts_the_release_tag_forms(raw: str, expected: str) -> None:
    """
    Given  a release tag with a pre-release label, with or without a dot before the number
    When   normalize_version() is called
    Then   the PEP 440 form of the same version is returned
    """
    assert stamp_versions.normalize_version(raw) == expected


@pytest.mark.parametrize("raw", ["v0.5.7-rc.", "v0.5.7-rc.x", "v0.5.7-", "v0.5.7-foo1"])
def test_normalize_version_rejects_a_dangling_dot_or_unknown_label(raw: str) -> None:
    """
    Given  a tag whose pre-release part has a trailing dot, no label or an unknown label
    When   normalize_version() is called
    Then   a ValueError is raised, so two tags cannot stamp the same version
    """
    with pytest.raises(ValueError, match="not a valid PEP 440 version"):
        stamp_versions.normalize_version(raw)


def test_normalize_version_accepts_a_pep440_form_without_a_dash() -> None:
    """
    Given  a PEP 440 pre-release version with no dash (a local build may use one)
    When   normalize_version() is called
    Then   it is accepted unchanged, so only the release workflow's tag check rejects it
    """
    assert stamp_versions.normalize_version("v0.5.7rc1") == "0.5.7rc1"


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


def _write_stampable_files(tmp_path: Path) -> tuple[Path, Path]:
    """Write an __init__.py and a BUILD file the stamping functions accept."""
    init_path = tmp_path / "__init__.py"
    init_path.write_text('"""OCR pipeline package."""\n\n__version__ = "0.1.0"\n', encoding="utf-8")
    build_path = tmp_path / "BUILD"
    build_path.write_text(
        'py_wheel(\n    name = "teachers_teammate_wheel",\n    version = "0.1.0",\n)\n',
        encoding="utf-8",
    )
    return init_path, build_path


def test_check_prints_the_normalised_version_and_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a release tag and files that still carry the old version
    When   main() runs with --check
    Then   it exits 0, prints the normalised version, and leaves both files untouched
    """
    init_path, build_path = _write_stampable_files(tmp_path)

    exit_code = stamp_versions.main(
        [
            "--version",
            "v0.5.7-rc1",
            "--check",
            "--init-file",
            str(init_path),
            "--build-file",
            str(build_path),
        ]
    )

    assert exit_code == 0
    assert "Resolved version: 0.5.7rc1" in capsys.readouterr().out
    assert '__version__ = "0.1.0"' in init_path.read_text(encoding="utf-8")
    assert 'version = "0.1.0"' in build_path.read_text(encoding="utf-8")


def test_check_fails_on_a_version_it_cannot_normalise(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a string that is not a version
    When   main() runs with --check
    Then   it exits 1, reports the error, and writes nothing
    """
    init_path, build_path = _write_stampable_files(tmp_path)

    exit_code = stamp_versions.main(
        [
            "--version",
            "v0.5.7-rc1-extra",
            "--check",
            "--init-file",
            str(init_path),
            "--build-file",
            str(build_path),
        ]
    )

    assert exit_code == 1
    assert "ERROR:" in capsys.readouterr().err
    assert '__version__ = "0.1.0"' in init_path.read_text(encoding="utf-8")
