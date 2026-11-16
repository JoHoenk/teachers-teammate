"""The build's add-on constraints and the dry-run check that every add-on fits the bundle."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools.build import addon_constraints, check_addon_resolution


def test_shipped_pins_leave_out_the_build_tools_and_the_project() -> None:
    """
    Given  an environment with the app's packages, PyInstaller, pip and the project itself
    When   the pins are made
    Then   only what ships is pinned, sorted, with PEP 503 names
    """
    installed = [
        ("PySide6_Essentials", "6.11.0"),
        ("numpy", "2.5.4"),
        ("pyinstaller", "6.20"),
        ("pyinstaller-hooks-contrib", "2026.1"),
        ("altgraph", "0.17"),
        ("pip", "26.2"),
        ("teachers-teammate", "0.5.7"),
        ("", "1.0"),
    ]

    pins = addon_constraints.shipped_pins(installed)

    assert pins == ["numpy==2.5.4", "pyside6-essentials==6.11.0"]


def test_write_constraints_writes_one_pin_per_line(tmp_path: Path) -> None:
    """
    Given  the running environment
    When   the constraints are written
    Then   the file holds name==version lines and the count is returned
    """
    output = tmp_path / "_build" / "addon_constraints.txt"

    count = addon_constraints.write_constraints(output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert count == len(lines) > 0
    assert all("==" in line for line in lines)
    assert not any(line.startswith("pip==") for line in lines)


def test_find_constraints_looks_in_the_internal_folder(tmp_path: Path) -> None:
    """
    Given  a PyInstaller folder build with the constraints under _internal
    When   the file is looked for from the executable
    Then   it is found, and a bundle without it raises
    """
    app = tmp_path / "teachers-teammate"
    with pytest.raises(FileNotFoundError):
        check_addon_resolution.find_constraints(app)
    target = tmp_path / "_internal" / "teachers_teammate" / "_build" / "addon_constraints.txt"
    target.parent.mkdir(parents=True)
    target.write_text("numpy==2.5.4\n", encoding="utf-8")

    assert check_addon_resolution.find_constraints(app) == target


def test_dry_run_command_installs_nothing_and_follows_the_apps_policy(tmp_path: Path) -> None:
    """
    Given  an app, a constraints file and an add-on's requirement
    When   the check's command is built
    Then   it is the app's own pip in dry-run mode, wheels only, with the constraints
    """
    command = check_addon_resolution.dry_run_command(
        tmp_path / "app", tmp_path / "c.txt", tmp_path / "t", ["spacy>=3.7"]
    )

    assert command[:3] == [str(tmp_path / "app"), "--_pip_install_mode", "install"]
    assert "--dry-run" in command
    assert "--upgrade" in command
    assert command[command.index("--only-binary") + 1] == ":all:"
    assert command[command.index("-c") + 1] == str(tmp_path / "c.txt")
    assert command[-1] == "spacy>=3.7"


def test_python_dry_run_command_is_the_same_question_without_the_app(tmp_path: Path) -> None:
    """
    Given  an interpreter, a constraints file and an add-on's requirement
    When   the check's command is built for a build environment
    Then   it is that interpreter's pip in dry-run mode, wheels only, with the constraints
    """
    command = check_addon_resolution.python_dry_run_command(
        tmp_path / "python", tmp_path / "c.txt", tmp_path / "t", ["spacy>=3.7"]
    )

    assert command[:4] == [str(tmp_path / "python"), "-m", "pip", "install"]
    assert "--dry-run" in command
    assert "--ignore-installed" in command
    assert command[command.index("--only-binary") + 1] == ":all:"
    assert command[command.index("-c") + 1] == str(tmp_path / "c.txt")
    assert command[-1] == "spacy>=3.7"


def test_addons_to_check_leaves_the_amd_addon_to_linux() -> None:
    """
    Given  the add-ons the app installs
    When   the list for macOS and for Linux is worked out
    Then   pyamdgpuinfo is checked on Linux only, and every other add-on on both
    """
    macos = check_addon_resolution.addons_to_check("darwin")
    linux = check_addon_resolution.addons_to_check("linux")

    assert "amd" not in macos
    assert macos["privacy"] == ["spacy>=3.7"]
    assert linux["amd"] == ["pyamdgpuinfo"]
    assert set(macos) == set(linux) - {"amd"}


def test_check_addon_reports_the_end_of_pips_output_when_it_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a pip that fails with a long conflict message
    When   one add-on is checked
    Then   the add-on is named with the last lines of the message, and a pass gives ""
    """
    output = "\n".join(f"line {number}" for number in range(30))

    def fake_run(command, **_kwargs):
        code = 1 if "bad>=1" in command else 0
        return subprocess.CompletedProcess(command, code, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    failed = check_addon_resolution.check_addon(tmp_path, tmp_path, "bad", ["bad>=1"])
    passed = check_addon_resolution.check_addon(tmp_path, tmp_path, "good", ["good>=1"])

    assert failed.startswith("bad (bad>=1):")
    assert "line 29" in failed
    assert "line 5" not in failed
    assert passed == ""
