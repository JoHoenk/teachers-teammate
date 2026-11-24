"""The venv recipe: what each purpose installs, and that it prints the script folder."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from tools.build import make_venv


def _python(venv: Path) -> str:
    """The interpreter of *venv* on this system."""
    return str(
        make_venv.scripts_folder(venv) / ("python.exe" if sys.platform == "win32" else "python")
    )


def test_build_installs_the_base_runtime_and_the_bundler() -> None:
    """
    Given  the build purpose
    When   the pip command is built
    Then   it installs the package itself and PyInstaller against the constraints
    """
    command = make_venv.pip_install_command(Path("/venv"), "build")

    assert command[:6] == [_python(Path("/venv")), "-m", "pip", "install", "--quiet", "-c"]
    assert command[6] == str(make_venv.CONSTRAINTS)
    assert command[7:] == ["-e", ".", "pyinstaller", "pyinstaller-hooks-contrib"]


@pytest.mark.parametrize(("purpose", "extra"), [("test", ".[test]"), ("dev", ".[dev,test,llm]")])
def test_a_test_or_dev_venv_installs_that_extra(purpose: str, extra: str) -> None:
    """
    Given  the test or the dev purpose
    When   the pip command is built
    Then   the package is installed with that extra, still against the constraints
    """
    command = make_venv.pip_install_command(Path("/venv"), purpose)

    assert command[-2:] == ["-e", extra]
    assert "-c" in command


def test_every_purpose_installs_against_the_constraints() -> None:
    """
    Given  each purpose
    When   the pip command is built
    Then   it passes the constraints file, so no purpose resolves a version afresh
    """
    for purpose in make_venv.INSTALLS:
        assert str(make_venv.CONSTRAINTS) in make_venv.pip_install_command(Path("/venv"), purpose)


def test_the_build_venv_is_recreated_and_its_pins_checked(tmp_path: Path) -> None:
    """
    Given  a venv folder that already exists
    When   the build venv is created
    Then   it is deleted first, and check_pins.py runs at the end
    """
    venv = tmp_path / "venv"
    venv.mkdir()
    (venv / "stale").write_text("", encoding="utf-8")

    with patch.object(make_venv.subprocess, "run") as run:
        make_venv.create(venv, "build")

    commands = [call.args[0] for call in run.call_args_list]
    assert commands[0] == [sys.executable, "-m", "venv", str(venv)]
    assert str(make_venv.CHECK_PINS) in commands[-1]
    assert not (venv / "stale").exists()


def test_a_test_venv_checks_no_pins(tmp_path: Path) -> None:
    """
    Given  the test purpose
    When   the venv is created
    Then   check_pins.py does not run: the test extra is not what the bundle ships
    """
    with patch.object(make_venv.subprocess, "run") as run:
        make_venv.create(tmp_path / "venv", "test")

    commands = [call.args[0] for call in run.call_args_list]
    assert all(str(make_venv.CHECK_PINS) not in command for command in commands)


def test_the_script_folder_is_printed_last(tmp_path: Path, capsys) -> None:
    """
    Given  a venv path
    When   the script runs
    Then   its last line is the folder the caller puts on the PATH
    """
    with patch.object(make_venv, "create"):
        assert make_venv.main([str(tmp_path / "venv")]) == 0

    printed = capsys.readouterr().out.splitlines()[-1]
    assert Path(printed) == make_venv.scripts_folder(tmp_path / "venv")
