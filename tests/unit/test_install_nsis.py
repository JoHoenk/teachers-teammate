"""The pinned NSIS download: the hash decides what is used, a bad mirror is retried."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

import pytest

from tools.build import install_nsis


def _archive(*names: str) -> bytes:
    """Return a zip that holds an empty file for each of *names*."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name in names:
            bundle.writestr(name, "")
    return buffer.getvalue()


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Pin the archive of a folder with ``makensis.exe``; return its bytes."""
    data = _archive("nsis-3.13/makensis.exe")
    monkeypatch.setattr(install_nsis, "NSIS_SHA256", install_nsis.sha256(data))
    monkeypatch.setattr(install_nsis, "NSIS_VERSION", "3.13")
    return data


def test_the_download_with_the_pinned_hash_is_unpacked(tmp_path: Path, pinned: bytes) -> None:
    """
    Given  a mirror that serves the pinned archive
    When   NSIS is installed
    Then   the folder with makensis.exe is returned
    """
    folder = install_nsis.install(tmp_path, get=lambda _url: pinned)

    assert (folder / "makensis.exe").is_file()


def test_a_page_instead_of_the_file_is_fetched_again(tmp_path: Path, pinned: bytes) -> None:
    """
    Given  a mirror that serves an HTML page once and then the archive
    When   the archive is downloaded
    Then   the page is not kept and the second answer is
    """
    answers = [b"<html>try again</html>", pinned]
    archive = tmp_path / "nsis.zip"

    install_nsis.download(archive, get=lambda _url: answers.pop(0), sleep=lambda _s: None)

    assert archive.read_bytes() == pinned


def test_a_mirror_that_never_serves_the_file_fails(tmp_path: Path, pinned: bytes) -> None:
    """
    Given  a mirror that always serves other bytes
    When   the archive is downloaded
    Then   it stops after every attempt, names the hash it got, and leaves no file
    """
    archive = tmp_path / "nsis.zip"
    asked: list[int] = []

    def get(_url: str) -> bytes:
        asked.append(1)
        return b"other"

    with pytest.raises(SystemExit, match="SHA-256"):
        install_nsis.download(archive, get=get, sleep=lambda _s: None)

    assert len(asked) == install_nsis.ATTEMPTS
    assert not archive.exists()


def test_a_failing_connection_is_retried(tmp_path: Path, pinned: bytes) -> None:
    """
    Given  a mirror whose first request raises OSError
    When   the archive is downloaded
    Then   the next attempt gets it
    """
    calls: list[int] = []

    def get(_url: str) -> bytes:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("reset")
        return pinned

    archive = tmp_path / "nsis.zip"
    install_nsis.download(archive, get=get, sleep=lambda _s: None)

    assert archive.read_bytes() == pinned


def test_a_cached_archive_with_the_pinned_hash_is_not_downloaded(
    tmp_path: Path, pinned: bytes
) -> None:
    """
    Given  an archive in the download folder with the pinned hash
    When   NSIS is installed
    Then   nothing is fetched
    """
    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "nsis-3.13.zip").write_bytes(pinned)

    def get(_url: str) -> bytes:
        raise AssertionError("fetched")

    assert (install_nsis.install(tmp_path, get=get) / "makensis.exe").is_file()


def test_a_damaged_cached_archive_is_replaced(tmp_path: Path, pinned: bytes) -> None:
    """
    Given  a cached archive whose bytes are not the pinned ones
    When   NSIS is installed
    Then   it is downloaded again
    """
    (tmp_path / "download").mkdir()
    (tmp_path / "download" / "nsis-3.13.zip").write_bytes(b"damaged")

    folder = install_nsis.install(tmp_path, get=lambda _url: pinned)

    assert (folder / "makensis.exe").is_file()


def test_an_archive_without_makensis_is_refused(tmp_path: Path) -> None:
    """
    Given  an archive that has no makensis.exe in the NSIS folder
    When   it is unpacked
    Then   the failure says so
    """
    archive = tmp_path / "empty.zip"
    archive.write_bytes(_archive("nsis-3.13/COPYING"))

    with pytest.raises(SystemExit, match=r"makensis\.exe"):
        install_nsis.unpack(archive, tmp_path / "unpacked")
