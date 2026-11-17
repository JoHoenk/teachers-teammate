"""The macOS bundle check: which minimum macOS the .app asks for."""

from __future__ import annotations

import argparse
import plistlib
from pathlib import Path

import pytest

from tools.build import check_macos_bundle

#: Captured `vtool -show-build` output of a thin arm64 executable.
_THIN = """\
dist/teachers-teammate.app/Contents/MacOS/teachers-teammate:
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 11.0
      sdk 14.0
   ntools 1
     tool LD
  version 711.0
"""

#: Captured `vtool -show-build` output of a universal2 library: one block per slice.
_FAT = """\
dist/teachers-teammate.app/Contents/Frameworks/libcrypto.3.dylib (for architecture x86_64):
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 10.9
      sdk 14.0
   ntools 1
     tool LD
  version 711.0
dist/teachers-teammate.app/Contents/Frameworks/libcrypto.3.dylib (for architecture arm64):
      cmd LC_BUILD_VERSION
  cmdsize 32
 platform MACOS
    minos 11.0
      sdk 14.0
   ntools 1
     tool LD
  version 711.0
"""

#: Captured output of a library built before LC_BUILD_VERSION existed.
_OLD_COMMAND = """\
dist/teachers-teammate.app/Contents/Frameworks/libz.1.dylib:
      cmd LC_VERSION_MIN_MACOSX
  cmdsize 16
  version 10.13
      sdk 10.14
"""


def test_parse_minos_reads_every_architecture_of_a_fat_file() -> None:
    """
    Given  the vtool output of a universal2 library with two slices
    When   the minimum macOS versions are parsed
    Then   both slices are returned, not only the first
    """
    assert check_macos_bundle.parse_minos(_FAT) == [(10, 9), (11, 0)]


def test_parse_minos_ignores_the_linker_version() -> None:
    """
    Given  the vtool output of a thin file, whose tool entry names version 711.0
    When   the minimum macOS versions are parsed
    Then   only the minos line counts, not the linker's own version
    """
    assert check_macos_bundle.parse_minos(_THIN) == [(11, 0)]


def test_parse_minos_reads_the_older_version_command() -> None:
    """
    Given  the vtool output of a library that uses LC_VERSION_MIN_MACOSX
    When   the minimum macOS versions are parsed
    Then   its version line is read as the minimum macOS
    """
    assert check_macos_bundle.parse_minos(_OLD_COMMAND) == [(10, 13)]


def test_macho_arch_count_tells_a_fat_file_from_a_java_class() -> None:
    """
    Given  the magic bytes of a thin file, a fat file and a Java class file
    When   the architecture count is worked out
    Then   the thin file has one, the fat file two, and the class file none
    """
    thin = b"\xcf\xfa\xed\xfe" + b"\x07\x00\x00\x01"
    fat = b"\xca\xfe\xba\xbe" + (2).to_bytes(4, "big")
    java_class = b"\xca\xfe\xba\xbe" + b"\x00\x00\x00\x34"

    assert check_macos_bundle.macho_arch_count(thin) == 1
    assert check_macos_bundle.macho_arch_count(fat) == 2
    assert check_macos_bundle.macho_arch_count(java_class) == 0


def test_too_new_names_the_files_above_the_floor(tmp_path: Path) -> None:
    """
    Given  one file at the floor and one above it
    When   the bundle is checked against macOS 14.0
    Then   only the newer file is named
    """
    at_floor = tmp_path / "at-floor"
    above = tmp_path / "above"
    versions = {at_floor: (14, 0, 0), above: (15, 2, 0)}

    problems = check_macos_bundle.too_new(versions, (14,))

    assert problems == [f"{above} needs macOS 15.2"]


def test_parse_limit_pads_and_rejects(capsys) -> None:
    """
    Given  limits written as 14, 14.0 and a non-number
    When   they are parsed
    Then   14 equals 14.0, and the non-number is an argparse error
    """
    assert check_macos_bundle.parse_limit("14") == check_macos_bundle.parse_limit("14.0")
    with pytest.raises(argparse.ArgumentTypeError):
        check_macos_bundle.parse_limit("abc")


def test_read_versions_reports_vtool_failures(tmp_path: Path, monkeypatch) -> None:
    """
    Given  one file vtool reads and one it cannot
    When   the versions are read
    Then   the first has a version and the second is an error, not a silent skip
    """
    good, bad = tmp_path / "good", tmp_path / "bad"

    def fake(path: Path):
        if path == bad:
            raise RuntimeError("boom")
        return (11, 0, 0)

    monkeypatch.setattr(check_macos_bundle, "highest_minos", fake)

    versions, errors = check_macos_bundle.read_versions([good, bad])

    assert versions == {good: (11, 0, 0)}
    assert errors == [f"{bad}: vtool failed: boom"]


def test_too_new_skips_files_matching_an_ignore_pattern(tmp_path: Path) -> None:
    """
    Given  two files above the floor, one of them matching an --ignore pattern
    When   the bundle is checked
    Then   only the file outside the pattern is named
    """
    binding = tmp_path / "PySide6" / "QtCore.abi3.so"
    other = tmp_path / "other.dylib"
    versions = {binding: (26, 0, 0), other: (26, 0, 0)}

    problems = check_macos_bundle.too_new(versions, (14, 4), ("*/PySide6/*.abi3.so",))

    assert problems == [f"{other} needs macOS 26.0"]


def _app(tmp_path: Path, **overrides) -> Path:
    """Write a minimal .app whose Info.plist carries what the spec writes, plus *overrides*."""
    app = tmp_path / "teachers-teammate.app"
    resources = app / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (resources / "teachers_teammate.icns").write_bytes(b"icns")
    plist = {
        "CFBundleIdentifier": check_macos_bundle.BUNDLE_IDENTIFIER,
        "CFBundleShortVersionString": "1.2.3",
        "CFBundleVersion": "1.2.3.0",
        "CFBundleIconFile": "teachers_teammate.icns",
        "LSMinimumSystemVersion": check_macos_bundle.MACOS_MINIMUM,
    }
    plist.update(overrides)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(plist))
    return app


def test_check_info_plist_accepts_what_the_spec_writes(tmp_path: Path) -> None:
    """
    Given  an .app whose Info.plist carries the identifier, floor, version and icon the spec sets
    When   the plist is checked against the build's version
    Then   no problem is reported
    """
    app = _app(tmp_path)

    assert check_macos_bundle.check_info_plist(app, "1.2.3") == []


def test_check_info_plist_names_a_foreign_identifier(tmp_path: Path) -> None:
    """
    Given  an .app built with another bundle identifier
    When   the plist is checked
    Then   the identifier is named, because macOS keys the app's identity on it
    """
    app = _app(tmp_path, CFBundleIdentifier="com.example.other")

    problems = check_macos_bundle.check_info_plist(app)

    assert problems == [
        "CFBundleIdentifier is 'com.example.other', "
        f"expected {check_macos_bundle.BUNDLE_IDENTIFIER!r}"
    ]


def test_check_info_plist_names_a_floor_that_is_not_macos_minimum(tmp_path: Path) -> None:
    """
    Given  an .app that asks for macOS 15.0 while the floor is 14.4
    When   the plist is checked
    Then   the floor is named, so the .app and the Mach-O check cannot disagree
    """
    app = _app(tmp_path, LSMinimumSystemVersion="15.0")

    problems = check_macos_bundle.check_info_plist(app)

    assert problems == [
        f"LSMinimumSystemVersion is '15.0', expected {check_macos_bundle.MACOS_MINIMUM!r}"
    ]


def test_check_info_plist_names_a_version_that_is_not_the_builds(tmp_path: Path) -> None:
    """
    Given  an .app stamped 1.2.3 and a build that says 1.3.0
    When   the plist is checked against the build's version
    Then   the mismatch is named
    """
    app = _app(tmp_path)

    problems = check_macos_bundle.check_info_plist(app, "1.3.0")

    assert problems == ["CFBundleShortVersionString is '1.2.3', the build is '1.3.0'"]


def test_check_info_plist_names_an_icon_that_is_not_there(tmp_path: Path) -> None:
    """
    Given  an .app whose plist names an icon that was not copied into Resources
    When   the plist is checked
    Then   the missing icon is named
    """
    app = _app(tmp_path, CFBundleIconFile="missing.icns")

    problems = check_macos_bundle.check_info_plist(app)

    assert len(problems) == 1
    assert "names no file in" in problems[0]


def test_check_info_plist_names_a_missing_plist(tmp_path: Path) -> None:
    """
    Given  a folder that is not an .app bundle
    When   the plist is checked
    Then   the missing Info.plist is the one problem reported
    """
    problems = check_macos_bundle.check_info_plist(tmp_path)

    assert problems == [f"{tmp_path / 'Contents' / 'Info.plist'} is missing"]


def test_version_numbers_ignore_a_v_prefix_and_a_prerelease() -> None:
    """
    Given  a tag, a plain version and a pre-release
    When   their numbers are read
    Then   all three give the same three numbers
    """
    assert check_macos_bundle.version_numbers("v1.2.3") == (1, 2, 3)
    assert check_macos_bundle.version_numbers("1.2.3") == (1, 2, 3)
    assert check_macos_bundle.version_numbers("1.2.3-beta.1") == (1, 2, 3)


def test_main_plist_mode_needs_no_vtool(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a runner without vtool, as the install job's is
    When   the plist mode runs on the installed copy
    Then   it passes without asking for vtool
    """
    app = _app(tmp_path)
    monkeypatch.setattr(check_macos_bundle.shutil, "which", lambda name: None)

    assert check_macos_bundle.main(["--plist", "--version", "1.2.3", str(app)]) == 0
