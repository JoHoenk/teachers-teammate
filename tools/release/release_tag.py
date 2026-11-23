#!/usr/bin/env python3
"""The release logic the workflows used to carry as shell: tags, version forms, release files.

``release.yml`` and ``release_dry_run.yml`` call this script, so a rule is written once and has
unit tests (``tests/unit/test_release_tag.py``), and a maintainer can run the same check on a
laptop before pushing a tag.

Usage (from the repository root; ``gh`` and ``GH_TOKEN`` are needed for the commands that talk
to GitHub):

    python tools/release/release_tag.py check v0.5.7 --repo OWNER/NAME --sha <commit>
    python tools/release/release_tag.py check v0.5.7-rc.1 --repo OWNER/NAME --skip-main
    python tools/release/release_tag.py forms v0.5.7-rc.1 --github-output "$GITHUB_OUTPUT"
    python tools/release/release_tag.py draft v0.5.7 --repo OWNER/NAME
    python tools/release/release_tag.py collect artifacts release
    python tools/release/release_tag.py summary v0.5.7 release --repo OWNER/NAME
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.release.stamp_versions import normalize_version  # noqa: E402

#: ``vX.Y.Z`` or ``vX.Y.Z-(alpha|beta|rc|pre)[.]N``.  The number is required, and a PEP 440 form
#: without the dash (``v0.5.7rc1``) is refused: GitHub would publish it as *Latest*, and
#: ``VIProductVersion`` would break ``makensis``.
TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(-(alpha|beta|rc|pre)\.?\d+)?$")

#: What a release carries, one file of each kind.  The dry run checks the same list.
RELEASE_FILE_PATTERNS = (
    "*.whl",
    "*.deb",
    "*-w64-setup.exe",
    "*-macos.dmg",
)

CHECKSUMS_NAME = "SHA256SUMS"

SIGNING_POLICY = "docs/code_signing_policy.md"


def check_form(tag: str) -> None:
    """Refuse a tag that is not a release version.

    Raises:
        ValueError: *tag* has the wrong form, or is not a valid PEP 440 version once stamped.
    """
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(
            f"'{tag}' is not a release tag. Use vX.Y.Z or vX.Y.Z-(alpha|beta|rc|pre)[.]N."
        )
    normalize_version(tag)


def base_numbers(tag: str) -> tuple[int, int, int]:
    """Return the three numbers of *tag*, without a pre-release suffix."""
    match = TAG_PATTERN.fullmatch(tag)
    if match is None:
        raise ValueError(f"'{tag}' is not a release tag")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def is_prerelease(tag: str) -> bool:
    """Return whether *tag* names a pre-release (``-rc.1``, ``-beta``, ...)."""
    return "-" in tag


def newer_than_latest(tag: str, latest: str | None) -> bool:
    """Return whether *tag* may ship after the published release *latest*.

    The app's update check reads ``/releases/latest``, so a version that is not newer would
    either replace it as *Latest* or ship a build nobody is offered.  A pre-release of the latest
    version itself is stale too, so the base must be strictly higher; the final tag itself passes
    (a re-run of the same release).  No published release yet passes.
    """
    if not latest or latest == tag:
        return True
    return base_numbers(tag) > base_numbers(latest)


def latest_hint(tag: str, latest: str | None) -> str:
    """Say how to tick *Set as the latest release* when publishing *tag*.

    GitHub would pick *Latest* by creation date, so an older tag that finishes last could take it
    from a higher version.  Returns ``"prerelease"``, ``"tick"`` or ``"untick"``.
    """
    if is_prerelease(tag):
        return "prerelease"
    if not latest or base_numbers(tag) >= base_numbers(latest):
        return "tick"
    return "untick"


def version_forms(tag: str) -> dict[str, str]:
    """Return the forms of the version the build steps need.

    ``value`` is the release string without the ``v`` (installer file names, the exe's version
    resource); ``win`` drops the pre-release suffix, because ``VIProductVersion`` wants X.Y.Z.W.
    """
    value = tag.removeprefix("v")
    return {"value": value, "win": value.split("-", 1)[0]}


def draft_arguments(tag: str, repo: str) -> list[str]:
    """Return the ``gh release create`` arguments for the draft of *tag*.

    The body starts with the link to the code signing policy: the signing service asks for it on
    every download page.  GitHub puts ``--notes`` in front of the generated notes, so both stay.
    """
    notes = (
        "**What is signed, where the files come from and what the app does with your data:** "
        f"[code signing policy](https://github.com/{repo}/blob/main/{SIGNING_POLICY})"
    )
    arguments = ["--draft", "--verify-tag", "--generate-notes", "--notes", notes]
    arguments += ["--title", f"Release {tag}"]
    if is_prerelease(tag):
        arguments.append("--prerelease")
    return arguments


def collect_files(artifacts: Path, destination: Path) -> list[Path]:
    """Copy exactly one file of each kind in :data:`RELEASE_FILE_PATTERNS` into *destination*.

    A draft with a missing installer, or with a stray extra file, must never be offered for
    publishing, so anything but exactly those files is an error.

    Raises:
        SystemExit: a pattern matches none or several files, or the artifacts hold other files.
    """
    problems: list[str] = []
    chosen: list[Path] = []
    for pattern in RELEASE_FILE_PATTERNS:
        found = sorted(path for path in artifacts.rglob(pattern) if path.is_file())
        if len(found) != 1:
            problems.append(f"expected exactly one '{pattern}', found {len(found)}")
        else:
            chosen.append(found[0])
    everything = sorted(path for path in artifacts.rglob("*") if path.is_file())
    if len(everything) != len(RELEASE_FILE_PATTERNS):
        names = ", ".join(str(path.relative_to(artifacts)) for path in everything)
        problems.append(
            f"the artifacts hold {len(everything)} files, expected exactly "
            f"{len(RELEASE_FILE_PATTERNS)}: {names}"
        )
    if problems:
        raise SystemExit("\n".join(f"::error::{problem}" for problem in problems))
    destination.mkdir(parents=True, exist_ok=True)
    return [Path(shutil.copy2(path, destination / path.name)) for path in chosen]


def write_checksums(files: list[Path], destination: Path) -> Path:
    """Write ``SHA256SUMS`` (the format of ``sha256sum``) for *files* into *destination*."""
    lines = []
    for path in sorted(files, key=lambda item: item.name):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.name}")
    target = destination / CHECKSUMS_NAME
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def summary_markdown(tag: str, repo: str, files: list[Path], latest: str | None) -> str:
    """Return the step summary of a finished draft."""
    lines = [
        f"### Draft release `{tag}`",
        "",
        "Nothing is public yet. Check the notes and the assets, then click **Publish**:",
        f"https://github.com/{repo}/releases/edit/{tag}",
        "",
    ]
    hint = latest_hint(tag, latest)
    if hint == "prerelease":
        lines.append("The draft is already marked pre-release.")
    elif hint == "tick":
        lines.append("Tick **Set as the latest release** when you publish.")
    else:
        lines.append(
            f"**Do not** tick *Set as the latest release*: `{latest}` is newer than `{tag}`."
        )
    lines += ["", "| Asset | Size |", "|---|---|"]
    for path in sorted(files, key=lambda item: item.name):
        lines.append(f"| {path.name} | {path.stat().st_size / 1_048_576:.1f} MB |")
    return "\n".join(lines) + "\n"


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"{command[0]} is not on PATH") from None
    if check and result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemExit(f"{' '.join(command)} failed: {detail}")
    return result


def published_latest(repo: str) -> str | None:
    """Return the tag of the release GitHub calls *Latest*, or ``None`` when there is none.

    Raises:
        SystemExit: GitHub answered with anything but the release or a 404, so the check can
            neither pass nor fail honestly.
    """
    result = _run(["gh", "api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"], check=False)
    if result.returncode == 0:
        return result.stdout.strip() or None
    if "404" in result.stderr:
        return None
    raise SystemExit(f"could not read the latest release: {result.stderr.strip()}")


def _on_main(sha: str) -> bool:
    return (
        _run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], check=False).returncode
        == 0
    )


def _command_check(args: argparse.Namespace) -> int:
    tag = args.tag
    delete_hint = f"Delete it with: git push --delete origin '{tag}'"
    try:
        check_form(tag)
    except ValueError as exc:
        print(f"::error::{exc}\n{delete_hint}")
        return 1
    if not args.skip_main and not _on_main(args.sha):
        print(
            f"::error::'{tag}' does not point at a commit on main, so it cannot ship.\n{delete_hint}"
        )
        return 1
    latest = published_latest(args.repo)
    if not newer_than_latest(tag, latest):
        print(f"::error::'{tag}' is not newer than the latest release '{latest}'.\n{delete_hint}")
        return 1
    print(f"{tag} is a release tag (latest published: {latest or 'none'})")
    return 0


def _command_forms(args: argparse.Namespace) -> int:
    forms = version_forms(args.tag)
    text = "".join(f"{key}={value}\n" for key, value in forms.items())
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(text)
    else:
        print(text, end="")
    return 0


def _command_draft(args: argparse.Namespace) -> int:
    """Create the draft, or reuse the one a failed run left (never a published release)."""
    view = _run(
        [
            "gh",
            "release",
            "view",
            args.tag,
            "--repo",
            args.repo,
            "--json",
            "isDraft",
            "--jq",
            ".isDraft",
        ],
        check=False,
    )
    if view.returncode == 0:
        if view.stdout.strip() != "true":
            print(f"::error::{args.tag} is already published; its files must not be replaced.")
            return 1
        print(f"The draft for {args.tag} exists already; its assets are replaced.")
        return 0
    _run(
        [
            "gh",
            "release",
            "create",
            args.tag,
            "--repo",
            args.repo,
            *draft_arguments(args.tag, args.repo),
        ]
    )
    return 0


def _command_collect(args: argparse.Namespace) -> int:
    files = collect_files(args.artifacts, args.destination)
    checksums = write_checksums(files, args.destination)
    for path in [*files, checksums]:
        print(path)
    return 0


def _command_summary(args: argparse.Namespace) -> int:
    files = sorted(path for path in args.directory.iterdir() if path.is_file())
    latest = published_latest(args.repo)
    sys.stdout.write(summary_markdown(args.tag, args.repo, files, latest))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run one of the commands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="validate a release tag")
    check.add_argument("tag")
    check.add_argument("--repo", required=True, help="OWNER/NAME, for the latest release")
    check.add_argument("--sha", help="the commit the tag points at (not needed with --skip-main)")
    check.add_argument("--skip-main", action="store_true", help="do not require the commit on main")
    check.set_defaults(handler=_command_check)

    forms = commands.add_parser("forms", help="print the version forms the build steps need")
    forms.add_argument("tag")
    forms.add_argument("--github-output", help="append key=value lines to this file instead")
    forms.set_defaults(handler=_command_forms)

    draft = commands.add_parser(
        "draft", help="create the draft release, or reuse an unfinished one"
    )
    draft.add_argument("tag")
    draft.add_argument("--repo", required=True)
    draft.set_defaults(handler=_command_draft)

    collect = commands.add_parser("collect", help="gather the release files and write SHA256SUMS")
    collect.add_argument("artifacts", type=Path)
    collect.add_argument("destination", type=Path)
    collect.set_defaults(handler=_command_collect)

    summary = commands.add_parser("summary", help="print the markdown summary of the draft")
    summary.add_argument("tag")
    summary.add_argument("directory", type=Path)
    summary.add_argument("--repo", required=True)
    summary.set_defaults(handler=_command_summary)

    args = parser.parse_args(argv)
    if args.command == "check" and not args.skip_main and not args.sha:
        parser.error("check needs --sha unless --skip-main is given")
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
