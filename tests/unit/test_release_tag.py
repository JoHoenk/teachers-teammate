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
    Given  the four release files, each in its own artifact folder
    When   collect_files() is called
    Then   all four are copied flat into the destination
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
    Given  artifacts that hold the four files and a smoke report
    When   collect_files() is called
    Then   it exits with an error: nothing else may reach the release
    """
    artifacts = _artifacts(tmp_path)
    (artifacts / "release-linux-smoke").mkdir()
    (artifacts / "release-linux-smoke" / "smoke.json").write_text("{}")

    with pytest.raises(SystemExit, match="hold 5 files, expected exactly 4"):
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
        patch.object(release_tag, "missing_sources_release", return_value=None),
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


QT, OPENCV, FFMPEG, LIBICONV, LXML = "6.12.0", "5.1.0.95", "9.0.2", "1.18", "6.1.3"
MODULES = ("qtbase", "qtsvg", "qtimageformats", "qtwayland")


def _inventory(
    tmp_path: Path, qt: str = QT, opencv: str = OPENCV, notice_opencv: str | None = None
) -> Path:
    """Lay out the inventory and NOTICE of a checkout, as the sources gate reads them."""
    notice_opencv = opencv if notice_opencv is None else notice_opencv
    (tmp_path / "third_party_licenses").mkdir(parents=True)
    (tmp_path / "third_party_licenses" / "inventory.csv").write_text(
        "Component,Version,License,Scope,Kind\n"
        f"pyside6-essentials,{qt},LGPL-3.0-only,bundle,package\n"
        f"opencv-python-headless,{opencv},Apache-2.0,bundle,package\n"
        f"lxml,{LXML},BSD-3-Clause,bundle,package\n",
        encoding="utf-8",
    )
    (tmp_path / "NOTICE").write_text(
        "Teacher's Teammate\n\n"
        "Source code\n"
        "-----------\n"
        f"Qt {qt} and PySide6 {qt}, the versions the bundle carries:\n"
        f"  https://download.qt.io/archive/qt/6.12/{qt}/submodules/\n"
        "      (qtbase, qtsvg, qtimageformats, qtwayland)\n"
        f"  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{qt}-src/\n"
        "\n"
        f"FFmpeg {FFMPEG}, inside opencv-python-headless {notice_opencv} (Linux and macOS):\n"
        f"  https://ffmpeg.org/releases/ffmpeg-{FFMPEG}.tar.gz\n"
        "\n"
        f"libiconv {LIBICONV}, inside lxml {LXML} (Linux, Windows and macOS):\n"
        f"  https://ftp.gnu.org/pub/gnu/libiconv/libiconv-{LIBICONV}.tar.gz\n",
        encoding="utf-8",
    )
    return tmp_path


def _set(qt: str = QT, opencv: str = OPENCV, ffmpeg: str = FFMPEG) -> tuple[str, ...]:
    """Return the files a sources release of those versions holds."""
    return (
        *(f"{module}-everywhere-src-{qt}.tar.xz" for module in MODULES),
        f"pyside-setup-everywhere-src-{qt}.tar.xz",
        f"ffmpeg-{ffmpeg}.tar.xz",
        f"ffmpeg-{ffmpeg}.tar.xz.asc",
        f"opencv_python_headless-{opencv}.tar.gz",
        f"libiconv-{LIBICONV}.tar.gz",
        f"libiconv-{LIBICONV}.tar.gz.sig",
        f"lxml-{LXML}.tar.gz",
        "SHA256SUMS",
        "README.txt",
    )


def test_missing_sources_release_names_the_release_and_the_tool(tmp_path: Path) -> None:
    """
    Given  a checkout whose sources no release holds
    When   missing_sources_release() is called
    Then   the message names the release and the command that creates it, so a red validate job
           says what to do
    """
    tree = _inventory(tmp_path)
    mirror = release_tag.mirror_sources

    with patch.object(mirror, "release_assets", return_value=None):
        problem = release_tag.missing_sources_release("owner/name", tree)

    assert problem is not None
    assert mirror.sources_release_tag(mirror.tree_contents(tree)) in problem
    assert "mirror_sources.py --upload" in problem


def test_missing_sources_release_passes_when_the_release_holds_the_set(tmp_path: Path) -> None:
    """
    Given  a sources release that holds every file of the shipped versions
    When   missing_sources_release() is called
    Then   there is nothing to report
    """
    with patch.object(release_tag.mirror_sources, "release_assets", return_value=_set()):
        problem = release_tag.missing_sources_release("owner/name", _inventory(tmp_path))

    assert problem is None


def test_missing_sources_release_rejects_a_release_of_an_older_ffmpeg(tmp_path: Path) -> None:
    """
    Given  a release named after the shipped Qt that still holds the FFmpeg of an earlier OpenCV
    When   missing_sources_release() is called
    Then   the message names what the release lacks, so a bumped OpenCV cannot pass on the name
           alone
    """
    held = tuple(name for name in _set() if not name.startswith("ffmpeg-"))
    with patch.object(release_tag.mirror_sources, "release_assets", return_value=held):
        problem = release_tag.missing_sources_release("owner/name", _inventory(tmp_path))

    assert problem is not None
    assert "does not hold the sources this commit ships" in problem
    assert f"it holds no FFmpeg {FFMPEG} source" in problem
    assert f"it holds no FFmpeg {FFMPEG} signature" in problem


def test_missing_sources_release_names_a_qt_archive_the_release_lacks(tmp_path: Path) -> None:
    """
    Given  a release that holds every file but one Qt submodule archive
    When   missing_sources_release() is called
    Then   the missing module archive is named
    """
    held = tuple(name for name in _set() if not name.startswith("qtsvg-"))
    with patch.object(release_tag.mirror_sources, "release_assets", return_value=held):
        problem = release_tag.missing_sources_release("owner/name", _inventory(tmp_path))

    assert problem is not None
    assert f"it holds no qtsvg source of Qt {QT}" in problem


def test_missing_sources_release_exits_on_a_notice_the_inventory_contradicts(
    tmp_path: Path,
) -> None:
    """
    Given  a checkout whose NOTICE names another OpenCV than the inventory
    When   missing_sources_release() is called
    Then   it exits instead of looking for sources of a version we cannot name
    """
    with pytest.raises(SystemExit, match="NOTICE names opencv-python-headless"):
        release_tag.missing_sources_release(
            "owner/name", _inventory(tmp_path, opencv="5.2.0.10", notice_opencv=OPENCV)
        )


def test_missing_sources_release_exits_on_a_part_it_cannot_place(tmp_path: Path) -> None:
    """
    Given  a NOTICE with a part inside a package that PART_SOURCES does not know
    When   missing_sources_release() is called
    Then   it exits with the reason, not with a traceback
    """
    tree = _inventory(tmp_path)
    (tree / "NOTICE").write_text(
        (tree / "NOTICE").read_text(encoding="utf-8")
        + f"\nzlib 1.3, inside lxml {LXML} (Linux):\n  https://zlib.net/\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="NOTICE names zlib"):
        release_tag.missing_sources_release("owner/name", tree)


def test_check_command_rejects_a_tag_without_a_sources_release(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    Given  a well-formed, newer tag whose Qt sources were never mirrored
    When   the check command runs
    Then   it exits 1 and prints the reason as a workflow error
    """
    with (
        patch.object(release_tag, "published_latest", return_value="v0.5.6"),
        patch.object(
            release_tag,
            "missing_sources_release",
            return_value="there is no release 'the sources'",
        ),
    ):
        code = release_tag.main(["check", "v0.5.7", "--repo", "owner/name", "--skip-main"])

    assert code == 1
    assert "::error::there is no release" in capsys.readouterr().out


def test_check_command_skips_the_sources_release_in_a_dry_run() -> None:
    """
    Given  a rehearsal of a version whose sources are not mirrored yet
    When   the check command runs with --skip-sources
    Then   it passes and never asks GitHub for the release
    """
    with (
        patch.object(release_tag, "published_latest", return_value="v0.5.6"),
        patch.object(release_tag, "missing_sources_release") as sources,
    ):
        code = release_tag.main(
            ["check", "v0.5.7", "--repo", "owner/name", "--skip-main", "--skip-sources"]
        )

    assert (code, sources.call_count) == (0, 0)
