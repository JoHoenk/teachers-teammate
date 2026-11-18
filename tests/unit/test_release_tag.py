"""Unit tests for the release logic the workflows call (tools/release/release_tag.py)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tools.release import release_tag

_RELEASE_NAMES = (
    "teachers_teammate-0.5.7-py3-none-any.whl",
    "teachers-teammate_0.5.7_amd64.deb",
    "teachers-teammate-0.5.7-linux-x86_64.tar.xz",
    "teachers-teammate-0.5.7-w64-setup.exe",
    "teachers-teammate-0.5.7-macos.dmg",
)


def _artifacts(tmp_path: Path, names: tuple[str, ...] = _RELEASE_NAMES) -> Path:
    """Lay the files out as `gh run download` does: one folder per artifact."""
    root = tmp_path / "artifacts"
    for index, name in enumerate(names):
        folder = root / f"release-{index}"
        folder.mkdir(parents=True)
        (folder / name).write_bytes(f"content of {name}".encode())
    return root


@pytest.mark.parametrize(
    "tag", ["v0.5.7", "v1.0.0-rc.1", "v1.0.0-rc1", "v0.5.7-beta.3", "v2.0.0-pre1"]
)
def test_check_form_accepts_release_tags(tag: str) -> None:
    """
    Given  a tag in the form vX.Y.Z or vX.Y.Z-(alpha|beta|rc|pre)[.]N
    When   check_form() is called
    Then   it returns without raising
    """
    release_tag.check_form(tag)


@pytest.mark.parametrize(
    "tag",
    ["0.5.7", "v0.5", "v0.5.7rc1", "v0.5.7-rc", "v0.0.0-test", "v1.0.0-final.1", "v0.5.7-rc.1.2"],
)
def test_check_form_rejects_everything_else(tag: str) -> None:
    """
    Given  a tag that is not a release version (no v, no pre-release number, PEP 440 form)
    When   check_form() is called
    Then   a ValueError names the tag, so it is never published as Latest
    """
    with pytest.raises(ValueError, match="is not a release tag"):
        release_tag.check_form(tag)


@pytest.mark.parametrize(
    ("tag", "latest", "expected"),
    [
        ("v0.5.7", "v0.5.6", True),
        ("v0.6.0", "v0.5.9", True),
        ("v1.0.0", "v0.99.99", True),
        ("v0.5.7-rc.1", "v0.5.6", True),
        ("v0.5.6", "v0.5.6", True),
        ("v0.5.6-rc.1", "v0.5.6", False),
        ("v0.5.5", "v0.5.6", False),
        ("v0.4.9", "v0.5.6", False),
        ("v0.10.0", "v0.9.0", True),
        ("v0.5.7", None, True),
    ],
)
def test_newer_than_latest(tag: str, latest: str | None, expected: bool) -> None:
    """
    Given  a tag and the release GitHub calls Latest
    When   newer_than_latest() is called
    Then   only a strictly higher base version (or the same final tag again) passes, and the
           numbers compare as numbers (0.10.0 is newer than 0.9.0)
    """
    assert release_tag.newer_than_latest(tag, latest) is expected


@pytest.mark.parametrize(
    ("tag", "latest", "expected"),
    [
        ("v0.5.7", "v0.5.6", "tick"),
        ("v0.5.7", None, "tick"),
        ("v0.5.7-rc.1", "v0.5.6", "prerelease"),
        ("v0.5.5", "v0.5.6", "untick"),
        ("v0.5.6", "v0.5.6", "tick"),
    ],
)
def test_latest_hint(tag: str, latest: str | None, expected: str) -> None:
    """
    Given  a tag and the release GitHub calls Latest
    When   latest_hint() is called
    Then   it says whether to tick "Set as the latest release" when publishing
    """
    assert release_tag.latest_hint(tag, latest) == expected


def test_version_forms_drop_the_prefix_and_the_prerelease_suffix() -> None:
    """
    Given  a pre-release tag
    When   version_forms() is called
    Then   `value` is the release string without the v and `win` is X.Y.Z for VIProductVersion
    """
    assert release_tag.version_forms("v0.5.7-rc.1") == {"value": "0.5.7-rc.1", "win": "0.5.7"}
    assert release_tag.version_forms("v0.5.7") == {"value": "0.5.7", "win": "0.5.7"}


def test_draft_arguments_mark_a_prerelease_and_link_the_signing_policy() -> None:
    """
    Given  a final tag and a pre-release tag
    When   draft_arguments() is called
    Then   both are drafts that verify the tag and start with the signing-policy link, and only
           the pre-release carries --prerelease
    """
    final = release_tag.draft_arguments("v0.5.7", "owner/name")
    pre = release_tag.draft_arguments("v0.5.7-rc.1", "owner/name")

    assert final[:3] == ["--draft", "--verify-tag", "--generate-notes"]
    assert "https://github.com/owner/name/blob/main/docs/code_signing_policy.md" in " ".join(final)
    assert "--prerelease" not in final
    assert pre[-1] == "--prerelease"


def test_collect_files_copies_one_file_of_each_kind(tmp_path: Path) -> None:
    """
    Given  the five release files, each in its own artifact folder
    When   collect_files() is called
    Then   all five are copied flat into the destination
    """
    copied = release_tag.collect_files(_artifacts(tmp_path), tmp_path / "release")

    assert sorted(path.name for path in copied) == sorted(_RELEASE_NAMES)
    assert all(path.parent == tmp_path / "release" for path in copied)


def test_collect_files_refuses_a_missing_installer(tmp_path: Path) -> None:
    """
    Given  artifacts without the macOS dmg
    When   collect_files() is called
    Then   it exits with an error naming the missing kind, and copies nothing
    """
    artifacts = _artifacts(tmp_path, _RELEASE_NAMES[:-1])

    with pytest.raises(SystemExit, match=r"expected exactly one '\*-macos\.dmg', found 0"):
        release_tag.collect_files(artifacts, tmp_path / "release")

    assert not (tmp_path / "release").exists()


def test_collect_files_refuses_a_stray_file(tmp_path: Path) -> None:
    """
    Given  artifacts that hold the five files and a smoke report
    When   collect_files() is called
    Then   it exits with an error: nothing else may reach the release
    """
    artifacts = _artifacts(tmp_path)
    (artifacts / "release-linux-smoke").mkdir()
    (artifacts / "release-linux-smoke" / "smoke.json").write_text("{}")

    with pytest.raises(SystemExit, match="hold 6 files, expected exactly 5"):
        release_tag.collect_files(artifacts, tmp_path / "release")


def test_collect_files_refuses_two_files_of_one_kind(tmp_path: Path) -> None:
    """
    Given  artifacts with two wheels
    When   collect_files() is called
    Then   it exits with an error, because a re-run must not ship a stale one
    """
    artifacts = _artifacts(tmp_path)
    extra = artifacts / "old"
    extra.mkdir()
    (extra / "teachers_teammate-0.5.6-py3-none-any.whl").write_bytes(b"old")

    with pytest.raises(SystemExit, match=r"expected exactly one '\*\.whl', found 2"):
        release_tag.collect_files(artifacts, tmp_path / "release")


def test_write_checksums_matches_the_sha256sum_format(tmp_path: Path) -> None:
    """
    Given  two files
    When   write_checksums() is called
    Then   SHA256SUMS holds `<hash>  <name>` lines, sorted by name, which `sha256sum -c` reads
    """
    first = tmp_path / "b.bin"
    second = tmp_path / "a.bin"
    first.write_bytes(b"bravo")
    second.write_bytes(b"alpha")

    target = release_tag.write_checksums([first, second], tmp_path)

    assert target.name == "SHA256SUMS"
    assert target.read_text(encoding="utf-8").splitlines() == [
        f"{hashlib.sha256(b'alpha').hexdigest()}  a.bin",
        f"{hashlib.sha256(b'bravo').hexdigest()}  b.bin",
    ]


def test_summary_markdown_lists_the_assets_and_the_latest_advice(tmp_path: Path) -> None:
    """
    Given  an older tag than the latest release and one asset
    When   summary_markdown() is called
    Then   it tells the maintainer not to tick "latest" and lists the asset with its size
    """
    asset = tmp_path / "app.dmg"
    asset.write_bytes(b"x" * 2_097_152)

    text = release_tag.summary_markdown("v0.5.5", "owner/name", [asset], "v0.5.6")

    assert "**Do not** tick *Set as the latest release*: `v0.5.6` is newer than `v0.5.5`." in text
    assert "| app.dmg | 2.0 MB |" in text
    assert "https://github.com/owner/name/releases/edit/v0.5.5" in text


def _completed(
    returncode: int, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_published_latest_reads_the_tag() -> None:
    """
    Given  GitHub names v0.5.6 as the latest release
    When   published_latest() is called
    Then   it returns the tag
    """
    with patch.object(release_tag, "_run", return_value=_completed(0, "v0.5.6\n")):
        assert release_tag.published_latest("owner/name") == "v0.5.6"


def test_published_latest_treats_404_as_no_release_yet() -> None:
    """
    Given  a repository without a published release (HTTP 404)
    When   published_latest() is called
    Then   it returns None
    """
    with patch.object(
        release_tag, "_run", return_value=_completed(1, stderr="gh: Not Found (HTTP 404)")
    ):
        assert release_tag.published_latest("owner/name") is None


def test_published_latest_fails_on_any_other_error() -> None:
    """
    Given  GitHub answers with a server error
    When   published_latest() is called
    Then   it exits instead of letting the "newer than latest" check pass silently
    """
    with (
        patch.object(release_tag, "_run", return_value=_completed(1, stderr="HTTP 502")),
        pytest.raises(SystemExit, match="could not read the latest release"),
    ):
        release_tag.published_latest("owner/name")


def test_check_command_rejects_a_tag_that_is_not_on_main(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Given  a well-formed tag whose commit is not on main
    When   the check command runs
    Then   it exits 1 and says how to delete the tag
    """
    with patch.object(release_tag, "_on_main", return_value=False):
        code = release_tag.main(["check", "v0.5.7", "--repo", "owner/name", "--sha", "abc"])

    assert code == 1
    assert "does not point at a commit on main" in capsys.readouterr().out


def test_check_command_rejects_a_stale_tag(capsys: pytest.CaptureFixture[str]) -> None:
    """
    Given  a tag that is not newer than the latest release
    When   the check command runs with --skip-main (the dry run)
    Then   it exits 1
    """
    with patch.object(release_tag, "published_latest", return_value="v0.5.6"):
        code = release_tag.main(["check", "v0.5.5", "--repo", "owner/name", "--skip-main"])

    assert code == 1
    assert "is not newer than the latest release 'v0.5.6'" in capsys.readouterr().out


def test_check_command_passes_a_new_tag_on_main() -> None:
    """
    Given  a tag on main that is newer than the latest release
    When   the check command runs
    Then   it exits 0
    """
    with (
        patch.object(release_tag, "_on_main", return_value=True),
        patch.object(release_tag, "published_latest", return_value="v0.5.6"),
    ):
        assert release_tag.main(["check", "v0.5.7", "--repo", "owner/name", "--sha", "abc"]) == 0


def test_forms_command_appends_to_the_github_output_file(tmp_path: Path) -> None:
    """
    Given  a pre-release tag and a GITHUB_OUTPUT file that already holds a line
    When   the forms command runs with --github-output
    Then   the two forms are appended and the earlier line is kept
    """
    output = tmp_path / "output"
    output.write_text("earlier=1\n", encoding="utf-8")

    release_tag.main(["forms", "v0.5.7-rc.1", "--github-output", str(output)])

    assert output.read_text(encoding="utf-8") == "earlier=1\nvalue=0.5.7-rc.1\nwin=0.5.7\n"


def test_draft_command_refuses_a_published_release() -> None:
    """
    Given  a release for the tag that is already published
    When   the draft command runs
    Then   it exits 1 and creates nothing, because its files must not be replaced
    """
    with patch.object(release_tag, "_run", return_value=_completed(0, "false\n")) as run:
        code = release_tag.main(["draft", "v0.5.7", "--repo", "owner/name"])

    assert code == 1
    assert run.call_count == 1


def test_draft_command_reuses_an_unfinished_draft() -> None:
    """
    Given  a draft a failed run left behind
    When   the draft command runs again (a re-run of all jobs)
    Then   it exits 0 without creating a second release
    """
    with patch.object(release_tag, "_run", return_value=_completed(0, "true\n")) as run:
        assert release_tag.main(["draft", "v0.5.7", "--repo", "owner/name"]) == 0

    assert run.call_count == 1


def test_draft_command_creates_the_draft_when_none_exists() -> None:
    """
    Given  no release for the tag
    When   the draft command runs
    Then   `gh release create` is called with the draft arguments
    """
    answers = [_completed(1, stderr="release not found"), _completed(0)]
    with patch.object(release_tag, "_run", side_effect=answers) as run:
        assert release_tag.main(["draft", "v0.5.7", "--repo", "owner/name"]) == 0

    created = run.call_args_list[1].args[0]
    assert created[:5] == ["gh", "release", "create", "v0.5.7", "--repo"]
    assert "--draft" in created
