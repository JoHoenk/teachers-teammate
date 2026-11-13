"""Tests of the Ollama program on this computer: where it is, how to start it, how much room."""

from __future__ import annotations

from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from teachers_teammate.infrastructure import ollama_install


def _existing(*paths: Path):
    """Patch the file check so only *paths* exist."""
    wanted = {str(path) for path in paths}
    return patch.object(Path, "exists", lambda self: str(self) in wanted)


@pytest.mark.unit
def test_the_windows_program_is_the_tray_app_then_the_path(tmp_path: Path) -> None:
    """
    Given  a Windows computer whose installer put the tray app under LOCALAPPDATA
    When   the program is looked for
    Then   the tray app is found, because it is what starts the server
    """
    app = tmp_path / "Programs" / "Ollama" / "ollama app.exe"

    with (
        patch.object(sys, "platform", "win32"),
        patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}),
        patch.object(ollama_install, "_which", return_value=[]),
        _existing(app),
    ):
        assert ollama_install.find_ollama_program() == app


@pytest.mark.unit
def test_the_windows_program_falls_back_to_the_path(tmp_path: Path) -> None:
    """
    Given  a Windows computer where only ollama.exe is on the PATH
    When   the program is looked for
    Then   that one is found
    """
    exe = tmp_path / "ollama.exe"

    with (
        patch.object(sys, "platform", "win32"),
        patch.dict("os.environ", {"LOCALAPPDATA": ""}),
        patch.object(ollama_install, "_which", return_value=[exe]),
        _existing(exe),
    ):
        assert ollama_install.find_ollama_program() == exe


@pytest.mark.unit
def test_the_macos_program_is_the_app_bundle(tmp_path: Path) -> None:
    """
    Given  a Mac with Ollama.app in the Applications folder
    When   the program is looked for
    Then   the app bundle is found
    """
    bundle = Path("/Applications/Ollama.app")

    with (
        patch.object(sys, "platform", "darwin"),
        patch.object(Path, "home", return_value=tmp_path),
        patch.object(ollama_install, "_which", return_value=[]),
        _existing(bundle),
    ):
        assert ollama_install.find_ollama_program() == bundle


@pytest.mark.unit
def test_the_linux_program_is_looked_for_on_the_path_and_in_both_bin_folders() -> None:
    """
    Given  a Linux computer with Ollama in /usr/local/bin and nothing on the PATH
    When   the program is looked for
    Then   the fixed folder is found, so a service install is recognised
    """
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "_which", return_value=[]),
        _existing(Path("/usr/local/bin/ollama")),
    ):
        assert ollama_install.find_ollama_program() == Path("/usr/local/bin/ollama")


@pytest.mark.unit
def test_no_program_anywhere_gives_none() -> None:
    """
    Given  a computer where none of the places holds an Ollama program
    When   the program is looked for
    Then   nothing is found, so the app says "wasn't found" rather than "isn't installed"
    """
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "_which", return_value=[]),
        _existing(),
    ):
        assert ollama_install.find_ollama_program() is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("platform", "expected"), [("win32", True), ("darwin", True), ("linux", False)]
)
def test_the_app_starts_ollama_only_where_a_normal_user_can(platform: str, expected: bool) -> None:
    """
    Given  Windows, macOS or Linux
    When   can_start_ollama is asked
    Then   only Windows and macOS say yes: on Linux the server is a system service
    """
    with (
        patch.object(sys, "platform", platform),
        patch.object(ollama_install, "find_ollama_program", return_value=None),
    ):
        assert ollama_install.can_start_ollama() is expected


@pytest.mark.unit
def test_wsl_starts_the_windows_tray_app() -> None:
    """
    Given  Linux inside WSL, where only the Windows tray app was found
    When   can_start_ollama is asked
    Then   it says yes, and the plain Linux program would still say no
    """
    app = Path("/mnt/c/Users/teacher/AppData/Local/Programs/Ollama/ollama app.exe")
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "find_ollama_program", return_value=app),
    ):
        assert ollama_install.can_start_ollama() is True
    with (
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "find_ollama_program", return_value=Path("/usr/bin/ollama")),
    ):
        assert ollama_install.can_start_ollama() is False


@pytest.mark.unit
def test_starting_on_macos_runs_a_homebrew_command_with_serve() -> None:
    """
    Given  a Homebrew ``ollama`` command on a Mac, not an app bundle
    When   it is started
    Then   it runs ``ollama serve``; ``open -a`` would fail on a command
    """
    with (
        patch.object(sys, "platform", "darwin"),
        patch.object(ollama_install.subprocess, "Popen") as popen,
    ):
        ollama_install.start_ollama(Path("/opt/homebrew/bin/ollama"))

    assert popen.call_args.args[0] == ["/opt/homebrew/bin/ollama", "serve"]


@pytest.mark.unit
def test_starting_on_windows_runs_a_command_on_the_path_with_serve() -> None:
    """
    Given  only ``ollama.exe`` on the PATH of a Windows computer
    When   it is started
    Then   it runs ``ollama.exe serve``; without it the program only prints its help
    """
    exe = Path("C:/Tools/ollama.exe")
    with (
        patch.object(sys, "platform", "win32"),
        patch.object(ollama_install.subprocess, "DETACHED_PROCESS", 0x8, create=True),
        patch.object(ollama_install.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, create=True),
        patch.object(ollama_install.subprocess, "Popen") as popen,
    ):
        ollama_install.start_ollama(exe)

    assert popen.call_args.args[0] == [str(exe), "serve"]


@pytest.mark.unit
def test_starting_on_macos_opens_the_app_bundle() -> None:
    """
    Given  the Ollama.app bundle on a Mac
    When   it is started
    Then   ``open -a`` is used, so the app starts the server itself
    """
    with (
        patch.object(sys, "platform", "darwin"),
        patch.object(ollama_install.subprocess, "Popen") as popen,
    ):
        ollama_install.start_ollama(Path("/Applications/Ollama.app"))

    assert popen.call_args.args[0] == ["open", "-a", "/Applications/Ollama.app"]


@pytest.mark.unit
def test_starting_on_windows_runs_the_tray_app_without_a_console() -> None:
    """
    Given  the Ollama tray app on Windows
    When   it is started
    Then   it runs detached and without a console window, so it outlives the app
    """
    app = Path("C:/Users/teacher/AppData/Local/Programs/Ollama/ollama app.exe")

    with (
        patch.object(sys, "platform", "win32"),
        # The two flags exist only on Windows; the values are the ones Windows defines.
        patch.object(ollama_install.subprocess, "DETACHED_PROCESS", 0x00000008, create=True),
        patch.object(
            ollama_install.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True
        ),
        patch.object(ollama_install.subprocess, "Popen") as popen,
    ):
        ollama_install.start_ollama(app)

    assert popen.call_args.args[0] == [str(app)]
    assert popen.call_args.kwargs["creationflags"] == 0x00000208


@pytest.mark.unit
def test_the_models_folder_is_the_one_ollama_was_told_to_use(tmp_path: Path) -> None:
    """
    Given  OLLAMA_MODELS set to a folder of the teacher's own
    When   the models folder is asked for
    Then   that folder wins, because Ollama itself would use it
    """
    with patch.dict("os.environ", {"OLLAMA_MODELS": str(tmp_path)}):
        assert ollama_install.ollama_models_dir() == tmp_path


@pytest.mark.unit
def test_the_models_folder_of_the_linux_service_is_used_when_it_exists(tmp_path: Path) -> None:
    """
    Given  a Linux computer where the system service keeps its models in its own folder
    When   the models folder is asked for
    Then   that folder is named, so the free-space warning is about the right disk
    """
    service_dir = tmp_path / "models"
    service_dir.mkdir()

    with (
        patch.dict("os.environ", {"OLLAMA_MODELS": ""}),
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "_LINUX_SERVICE_MODELS_DIR", service_dir),
    ):
        assert ollama_install.ollama_models_dir() == service_dir


@pytest.mark.unit
def test_the_models_folder_defaults_to_the_home_folder(tmp_path: Path) -> None:
    """
    Given  no OLLAMA_MODELS and no service folder
    When   the models folder is asked for
    Then   the default under the home folder is named
    """
    with (
        patch.dict("os.environ", {"OLLAMA_MODELS": ""}),
        patch.object(sys, "platform", "linux"),
        patch.object(ollama_install, "_LINUX_SERVICE_MODELS_DIR", tmp_path / "absent"),
        patch.object(Path, "home", return_value=tmp_path),
    ):
        assert ollama_install.ollama_models_dir() == tmp_path / ".ollama" / "models"


@pytest.mark.unit
def test_free_space_of_a_missing_folder_is_the_space_of_its_parent(tmp_path: Path) -> None:
    """
    Given  a models folder that does not exist yet, on a disk that can be read
    When   its free space is asked for
    Then   the space of the nearest folder that exists is returned, in GB
    """
    free = ollama_install.free_disk_gb(tmp_path / "not" / "there" / "yet")

    assert free is not None
    assert free > 0


@pytest.mark.unit
def test_free_space_is_unknown_when_the_disk_cannot_be_read() -> None:
    """
    Given  a disk whose usage cannot be read
    When   the free space is asked for
    Then   nothing is claimed, so no warning is shown
    """
    with patch.object(ollama_install.shutil, "disk_usage", side_effect=OSError("no disk")):
        assert ollama_install.free_disk_gb(Path("/nowhere")) is None
