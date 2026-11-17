#!/usr/bin/env python3
"""Find the newest published release and download one of its assets.

The installer tests need "the version a teacher has today": the newest release that is neither
a draft nor a pre-release.  ``gh release view`` without a tag answers exactly that, so the three
install jobs ask this script instead of each repeating the call.

Usage (from the repository root, with ``gh`` on PATH and ``GH_TOKEN`` set):

    python tools/packaging/latest_release.py tag
    python tools/packaging/latest_release.py download --pattern '*-w64-setup.exe' --dir /tmp/old

``tag`` prints the tag, ``download`` prints the path of the one asset that matches the pattern.
A missing release, a failed download or a pattern that matches nothing exits non-zero: the test
must fail loudly, never skip, so a renamed asset cannot switch the test off silently.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


def _gh(*arguments: str) -> str:
    """Run ``gh`` with *arguments* and return its stdout.

    Raises:
        SystemExit: ``gh`` is missing or exited non-zero.
    """
    try:
        result = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit("gh is not on PATH; it is what reads the releases") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemExit(f"gh {' '.join(arguments)} failed: {detail}")
    return result.stdout


def latest_tag() -> str:
    """Return the tag of the newest published release.

    ``gh release view`` without a tag names the latest release, which GitHub defines as the
    newest one that is neither a draft nor a pre-release.

    Raises:
        SystemExit: no release is published, or ``gh`` printed no tag.
    """
    data = json.loads(_gh("release", "view", "--json", "tagName"))
    tag = str(data.get("tagName", "")).strip()
    if not tag:
        raise SystemExit("gh named no release: is anything published yet?")
    return tag


def download(tag: str, pattern: str, directory: Path) -> Path:
    """Download the one asset of *tag* that matches *pattern* and return its path.

    Raises:
        SystemExit: the download failed, or the pattern matched no file or several.
    """
    directory.mkdir(parents=True, exist_ok=True)
    _gh("release", "download", tag, "--pattern", pattern, "--dir", str(directory))
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise SystemExit(f"{tag} left {len(matches)} files matching {pattern!r}, expected one")
    return matches[0]


def main(argv: list[str] | None = None) -> int:
    """Print the newest release's tag, or download one of its assets."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tag", help="print the tag of the newest published release")
    fetch = commands.add_parser("download", help="download one asset of the newest release")
    fetch.add_argument("--pattern", required=True, help="the asset name, as a glob")
    fetch.add_argument("--dir", type=Path, required=True, help="where to put the file")
    args = parser.parse_args(argv)

    tag = latest_tag()
    if args.command == "tag":
        print(tag)
        return 0
    print(download(tag, args.pattern, args.dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
