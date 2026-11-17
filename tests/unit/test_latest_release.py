"""The newest-release helper: which tag is "what a teacher has today", and its assets."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from tools.packaging import latest_release


class _Gh:
    """A stand-in for ``gh``: records the calls and answers with canned output."""

    def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = "") -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr
        self.calls: list[list[str]] = []

    def __call__(self, command, **kwargs):
        self.calls.append(list(command))
        return subprocess.CompletedProcess(command, self.returncode, self.stdout, self.stderr)


def test_latest_tag_reads_the_newest_published_release(monkeypatch) -> None:
    """
    Given  gh answering with a tag
    When   the newest release is asked for
    Then   the tag is returned and gh was asked for the latest release, not for a named one
    """
    gh = _Gh(json.dumps({"tagName": "v0.6.0"}))
    monkeypatch.setattr(latest_release.subprocess, "run", gh)

    assert latest_release.latest_tag() == "v0.6.0"
    assert gh.calls == [["gh", "release", "view", "--json", "tagName"]]


def test_latest_tag_fails_loudly_when_gh_fails(monkeypatch) -> None:
    """
    Given  gh exiting non-zero, as it does when nothing is published
    When   the newest release is asked for
    Then   the run stops with gh's own message instead of returning an empty tag
    """
    monkeypatch.setattr(
        latest_release.subprocess, "run", _Gh(returncode=1, stderr="release not found")
    )

    with pytest.raises(SystemExit, match="release not found"):
        latest_release.latest_tag()


def test_latest_tag_fails_loudly_when_gh_names_no_tag(monkeypatch) -> None:
    """
    Given  gh answering with an empty tag
    When   the newest release is asked for
    Then   the run stops, so a broken answer cannot turn the update test into a no-op
    """
    monkeypatch.setattr(latest_release.subprocess, "run", _Gh(json.dumps({"tagName": ""})))

    with pytest.raises(SystemExit, match="named no release"):
        latest_release.latest_tag()


def test_download_returns_the_one_asset_that_matches(tmp_path: Path, monkeypatch) -> None:
    """
    Given  gh writing the setup into the folder it was given
    When   the asset is downloaded by pattern
    Then   its path is returned and gh was asked for that tag and pattern
    """
    asset = tmp_path / "teachers-teammate-0.6.0-w64-setup.exe"

    def fake(command, **kwargs):
        asset.write_bytes(b"MZ")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(latest_release.subprocess, "run", fake)

    assert latest_release.download("v0.6.0", "*-w64-setup.exe", tmp_path) == asset


def test_download_fails_when_the_pattern_matches_nothing(tmp_path: Path, monkeypatch) -> None:
    """
    Given  gh succeeding but writing no file, as a renamed asset would
    When   the asset is downloaded by pattern
    Then   the run stops instead of handing the test an empty path
    """
    monkeypatch.setattr(
        latest_release.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "", ""),
    )

    with pytest.raises(SystemExit, match="expected one"):
        latest_release.download("v0.6.0", "*-w64-setup.exe", tmp_path)


def test_main_prints_the_tag(capsys, monkeypatch) -> None:
    """
    Given  gh answering with a tag
    When   the tag subcommand runs
    Then   the tag is the only thing on stdout, so a shell can capture it
    """
    monkeypatch.setattr(latest_release.subprocess, "run", _Gh(json.dumps({"tagName": "v0.6.0"})))

    assert latest_release.main(["tag"]) == 0
    assert capsys.readouterr().out == "v0.6.0\n"
