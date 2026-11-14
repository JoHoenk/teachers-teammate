"""The build's pin check: what pip's report holds, and what its markers hide on Linux."""

from __future__ import annotations

from tools.build import check_pins


def _report(*items: tuple[str, str, list[str]]) -> dict:
    return {
        "install": [
            {"metadata": {"name": name, "version": version, "requires_dist": requires}}
            for name, version, requires in items
        ]
    }


def test_unpinned_in_report_names_missing_and_different_versions() -> None:
    """
    Given  a pip report with one unpinned and one differently pinned distribution
    When   the report is checked against the pins
    Then   both are named and the pinned one is not
    """
    report = _report(("six", "1.0", []), ("attrs", "2.0", []), ("idna", "3.1", []))

    problems = check_pins.unpinned_in_report(report, {"attrs": "2.0", "idna": "3.0"})

    assert problems == ["idna==3.1 is pinned at 3.0", "six==1.0 is not pinned"]


def test_a_windows_only_dependency_is_found_although_pip_skipped_it() -> None:
    """
    Given  a report where pip skipped pefile because the host is Linux
    When   the Windows markers are evaluated
    Then   pefile is reported as unpinned, and not on macOS
    """
    report = _report(
        (
            "pyinstaller",
            "6.0",
            ['pefile>=2022; sys_platform == "win32"', 'macholib; sys_platform == "darwin"'],
        )
    )

    windows = check_pins.skipped_dependencies(report, {}, "win_amd64")
    macos = check_pins.skipped_dependencies(report, {}, "macosx_14_0_arm64")

    assert windows == ["pefile (needed by pyinstaller on win_amd64) is not pinned"]
    assert macos == ["macholib (needed by pyinstaller on macosx_14_0_arm64) is not pinned"]


def test_a_pinned_or_resolved_dependency_is_not_reported() -> None:
    """
    Given  a Windows-only dependency that is pinned and an extra that is not requested
    When   the markers are evaluated
    Then   nothing is reported
    """
    report = _report(
        ("pyinstaller", "6.0", ['pefile; sys_platform == "win32"', 'pytest; extra == "test"'])
    )

    assert check_pins.skipped_dependencies(report, {"pefile": "1"}, "win_amd64") == []
