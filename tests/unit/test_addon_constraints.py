"""The build's add-on constraints and the dry-run check that every add-on fits the bundle."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from teachers_teammate.infrastructure.addon_manager import addon_requirements
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


def test_addons_to_check_is_the_list_the_app_offers() -> None:
    """
    Given  the add-ons the app installs
    When   the list to check is worked out
    Then   it is exactly the app's own list, with no platform exception left
    """
    checked = check_addon_resolution.addons_to_check()

    assert checked == addon_requirements()
    assert checked["privacy"] == ["spacy>=3.7"]
    assert "nvidia" not in checked and "amd" not in checked


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


def test_the_dry_run_passes_the_add_on_lock_as_a_second_constraints_file(tmp_path: Path) -> None:
    """
    Given  an app, the bundle's constraints and the add-on lock
    When   the check's commands are built with and without the lock
    Then   with it pip gets both files as ``-c``, in the order the app uses; without it only one
    """
    for command in (
        check_addon_resolution.dry_run_command(
            tmp_path / "app", tmp_path / "c.txt", tmp_path / "t", ["spacy"], tmp_path / "l.txt"
        ),
        check_addon_resolution.python_dry_run_command(
            tmp_path / "py", tmp_path / "c.txt", tmp_path / "t", ["spacy"], tmp_path / "l.txt"
        ),
    ):
        constraint_files = [command[i + 1] for i, word in enumerate(command) if word == "-c"]
        assert constraint_files == [str(tmp_path / "c.txt"), str(tmp_path / "l.txt")]
        assert command[-1] == "spacy"

    without = check_addon_resolution.dry_run_command(
        tmp_path / "app", tmp_path / "c.txt", tmp_path / "t", ["spacy"]
    )
    assert without.count("-c") == 1


def test_find_lock_looks_next_to_the_constraints(tmp_path: Path) -> None:
    """
    Given  a PyInstaller folder build with the add-on lock under _internal
    When   the lock is looked for from the executable
    Then   it is found, and a bundle without it raises
    """
    app = tmp_path / "teachers-teammate"
    with pytest.raises(FileNotFoundError, match="add-on lock"):
        check_addon_resolution.find_lock(app)
    target = tmp_path / "_internal" / "teachers_teammate" / "_build" / "addon_lock.txt"
    target.parent.mkdir(parents=True)
    target.write_text("spacy==3.8.18\n", encoding="utf-8")

    assert check_addon_resolution.find_lock(app) == target


def test_main_resolves_every_add_on_against_the_newest_and_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given  a constraints file, an add-on lock and a pip that fails only for the locked run
    When   the check runs for a build environment
    Then   each add-on is tried without and with the lock, and a failing lock makes the run red
    """
    constraints = tmp_path / "c.txt"
    constraints.write_text("numpy==2.5.4\n", encoding="utf-8")
    lock = tmp_path / "l.txt"
    lock.write_text("spacy==3.8.18\n", encoding="utf-8")
    seen: list[int] = []

    def fake_run(command, **_kwargs):
        seen.append(command.count("-c"))
        return subprocess.CompletedProcess(command, 1 if command.count("-c") == 2 else 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(check_addon_resolution, "addons_to_check", lambda: {"privacy": ["spacy"]})
    arguments = ["--python", "python", "--constraints", str(constraints)]

    assert check_addon_resolution.main([*arguments, "--lock", str(lock)]) == 1
    assert seen == [1, 2]

    seen.clear()
    assert check_addon_resolution.main(arguments) == 0
    assert seen == [1]
