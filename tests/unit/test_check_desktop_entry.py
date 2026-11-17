"""The Linux launcher check: the entry, its Exec and Icon, and the icon's folder."""

# pylint: disable=W0621  # redefined-outer-name — pytest fixtures shadow module-scope names by design

from __future__ import annotations

from pathlib import Path
import struct
import subprocess

import pytest

from tools.packaging import check_desktop_entry

_ENTRY = """\
[Desktop Entry]
Version=1.0
Type=Application
Name=Teacher's Teammate
Exec=teachers-teammate
Icon=teachers-teammate
Terminal=false
Categories=Office;
"""


def _png(path: Path, width: int, height: int) -> Path:
    """Write a PNG header of *width* x *height* to *path*; the pixels do not matter here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header = check_desktop_entry._PNG_MAGIC + b"\x00\x00\x00\x0dIHDR"
    path.write_bytes(header + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00")
    return path


def _launcher(tmp_path: Path, icon_size: str = "256x256", width: int = 256) -> tuple[Path, Path]:
    """Write an entry and an icon into *tmp_path* and return both paths."""
    desktop = tmp_path / "teachers-teammate.desktop"
    desktop.write_text(_ENTRY, encoding="utf-8")
    icon_root = tmp_path / "hicolor"
    _png(icon_root / icon_size / "apps" / "teachers-teammate.png", width, width)
    return desktop, icon_root


@pytest.fixture
def installed(monkeypatch):
    """Pretend the container has desktop-file-validate and the installed program on PATH."""

    def which(name: str) -> str | None:
        return (
            f"/usr/bin/{name}" if name in ("desktop-file-validate", "teachers-teammate") else None
        )

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(check_desktop_entry.shutil, "which", which)
    monkeypatch.setattr(check_desktop_entry.subprocess, "run", run)


def test_read_entry_returns_the_desktop_entry_keys(tmp_path: Path) -> None:
    """
    Given  a .desktop file with one [Desktop Entry] group
    When   its keys are read
    Then   the keys of that group are returned and the group header is not one of them
    """
    desktop, _ = _launcher(tmp_path)

    entry = check_desktop_entry.read_entry(desktop)

    assert entry["Exec"] == "teachers-teammate"
    assert entry["Icon"] == "teachers-teammate"
    assert "Desktop Entry" not in entry


def test_png_size_reads_the_header(tmp_path: Path) -> None:
    """
    Given  a PNG whose header says 776x776
    When   its size is read
    Then   the header's width and height are returned
    """
    icon = _png(tmp_path / "icon.png", 776, 776)

    assert check_desktop_entry.png_size(icon) == (776, 776)


def test_png_size_rejects_a_file_that_is_not_a_png(tmp_path: Path) -> None:
    """
    Given  a file that is not a PNG
    When   its size is read
    Then   the check says so instead of unpacking nonsense
    """
    icon = tmp_path / "icon.png"
    icon.write_bytes(b"not a png at all, really")

    with pytest.raises(ValueError, match="not a PNG"):
        check_desktop_entry.png_size(icon)


def test_check_accepts_an_icon_in_the_folder_that_names_its_size(tmp_path: Path, installed) -> None:
    """
    Given  an entry whose Exec resolves and an icon in the folder of its real size
    When   the launcher is checked
    Then   no problem is reported
    """
    desktop, icon_root = _launcher(tmp_path, icon_size="256x256", width=256)

    assert check_desktop_entry.check(desktop, icon_root) == []


def test_check_names_an_icon_in_the_wrong_folder(tmp_path: Path, installed) -> None:
    """
    Given  a 776x776 icon installed into 256x256/apps, as make_deb.sh once did
    When   the launcher is checked
    Then   the mismatch is named, so the menu cannot draw a stretched icon unnoticed
    """
    desktop, icon_root = _launcher(tmp_path, icon_size="256x256", width=776)

    problems = check_desktop_entry.check(desktop, icon_root)

    assert len(problems) == 1
    assert "is 776x776, the folder says 256x256" in problems[0]


def test_check_names_a_validator_that_rejects_the_entry(
    tmp_path: Path, installed, monkeypatch
) -> None:
    """
    Given  desktop-file-validate exiting non-zero, as it does for two main categories
    When   the launcher is checked
    Then   its message is reported
    """
    desktop, icon_root = _launcher(tmp_path)
    monkeypatch.setattr(
        check_desktop_entry.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "contains more than one main category", ""
        ),
    )

    problems = check_desktop_entry.check(desktop, icon_root)

    assert any("more than one main category" in problem for problem in problems)


def test_check_names_an_exec_that_is_not_installed(tmp_path: Path, installed) -> None:
    """
    Given  an entry whose Exec names a program that is not on PATH
    When   the launcher is checked
    Then   the missing program is named
    """
    desktop, icon_root = _launcher(tmp_path)
    desktop.write_text(_ENTRY.replace("Exec=teachers-teammate", "Exec=no-such-program"), "utf-8")

    problems = check_desktop_entry.check(desktop, icon_root)

    assert any("does not resolve to an installed program" in problem for problem in problems)


def test_check_names_an_icon_that_is_not_installed(tmp_path: Path, installed) -> None:
    """
    Given  an entry whose Icon names no installed file
    When   the launcher is checked
    Then   the missing icon is named
    """
    desktop, icon_root = _launcher(tmp_path)
    desktop.write_text(_ENTRY.replace("Icon=teachers-teammate", "Icon=other"), "utf-8")

    problems = check_desktop_entry.check(desktop, icon_root)

    assert any("names no file under" in problem for problem in problems)


def test_check_names_a_missing_entry(tmp_path: Path) -> None:
    """
    Given  no .desktop file at all
    When   the launcher is checked
    Then   the missing file is the one problem reported
    """
    problems = check_desktop_entry.check(tmp_path / "missing.desktop", tmp_path)

    assert problems == [f"{tmp_path / 'missing.desktop'} is missing"]


def test_main_reports_a_broken_launcher(tmp_path: Path, installed, capsys) -> None:
    """
    Given  a launcher whose icon sits in the wrong folder
    When   the script runs
    Then   it exits 1 and prints the problem
    """
    desktop, icon_root = _launcher(tmp_path, icon_size="256x256", width=776)

    assert check_desktop_entry.main(["--desktop", str(desktop), "--icon-root", str(icon_root)]) == 1
    assert "the folder says 256x256" in capsys.readouterr().out
