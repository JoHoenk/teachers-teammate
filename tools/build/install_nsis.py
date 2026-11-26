#!/usr/bin/env python3
"""Unpack the pinned NSIS release into a folder and print the folder that holds ``makensis``.

The Windows installer is compiled by NSIS.  The portable release is downloaded from its own
release page, checked against the SHA-256 written here and unpacked, so the compiler is the one
file this repository names and not whatever a package feed serves that day.  The archive is in
``<folder>/download``, the unpacked NSIS in ``<folder>/unpacked``.

Usage (from the repository root):

    python tools/build/install_nsis.py "$RUNNER_TEMP/nsis"

The last line printed is the folder to put on the PATH.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import hashlib
import logging
from pathlib import Path
import shutil
import sys
import time
import urllib.request
import zipfile

NSIS_VERSION = "3.13"
NSIS_URL = (
    "https://downloads.sourceforge.net/project/nsis/NSIS%203/"
    f"{NSIS_VERSION}/nsis-{NSIS_VERSION}.zip"
)
NSIS_SHA256 = "ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8"

ATTEMPTS = 4
PAUSE_SECONDS = 15
TIMEOUT_SECONDS = 120

LOG = logging.getLogger(__name__)

Fetch = Callable[[str], bytes]


def sha256(data: bytes) -> str:
    """Return the SHA-256 of *data* as lowercase hex."""
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes:
    """Return the bytes *url* serves."""
    request = urllib.request.Request(url, headers={"User-Agent": "teachers-teammate-ci"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read()


def download(
    archive: Path,
    *,
    get: Fetch = fetch,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Put the pinned archive at *archive*, unless one with the right hash is already there.

    A mirror that fails or serves a page instead of the file is tried again.

    Raises:
        SystemExit: every attempt failed or served the wrong bytes.
    """
    if archive.is_file() and sha256(archive.read_bytes()) == NSIS_SHA256:
        return
    problem = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            data = get(NSIS_URL)
        except OSError as exc:
            problem = f"the download failed: {exc}"
        else:
            if sha256(data) == NSIS_SHA256:
                archive.parent.mkdir(parents=True, exist_ok=True)
                archive.write_bytes(data)
                return
            problem = f"the download has SHA-256 {sha256(data)}, expected {NSIS_SHA256}"
        LOG.warning("NSIS download, attempt %d of %d: %s", attempt, ATTEMPTS, problem)
        if attempt < ATTEMPTS:
            sleep(PAUSE_SECONDS)
    raise SystemExit(f"NSIS {NSIS_VERSION} could not be downloaded: {problem}")


def unpack(archive: Path, target: Path) -> Path:
    """Unpack *archive* into *target* and return the folder that holds ``makensis.exe``.

    Raises:
        SystemExit: the archive has no ``makensis.exe``.
    """
    if target.exists():
        shutil.rmtree(target)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(target)
    folder = target.resolve() / f"nsis-{NSIS_VERSION}"
    if not (folder / "makensis.exe").is_file():
        raise SystemExit(f"{archive.name} holds no nsis-{NSIS_VERSION}/makensis.exe")
    return folder


def install(directory: Path, *, get: Fetch = fetch) -> Path:
    """Return the folder with ``makensis.exe`` of the pinned NSIS, fetched into *directory*."""
    archive = directory / "download" / f"nsis-{NSIS_VERSION}.zip"
    download(archive, get=get)
    return unpack(archive, directory / "unpacked")


def main(argv: list[str] | None = None) -> int:
    """Install NSIS into the folder named on the command line and print where makensis is."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="where to download and unpack NSIS")
    arguments = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    print(install(arguments.directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
