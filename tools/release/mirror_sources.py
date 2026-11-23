#!/usr/bin/env python3
"""Mirror the third-party sources our downloads promise: one release per version we ship.

Nothing in this repository holds the Qt, PySide6 or FFmpeg sources, and the LGPL wants them next
to the download, so every version a release carries gets a sources release of its own:
``third-party-sources-qt<version>``.  This tool fetches the files NOTICE names, holds each one to
the digest its author publishes, writes ``SHA256SUMS`` and a ``README.txt`` into the set, and
uploads the folder.  It is never marked *Latest*, so the app's update check cannot see it.

The versions come from ``third_party_licenses/inventory.csv`` and NOTICE, never from an argument:
the set holds what the download promises.  The file names come from the authors' own directory
listings, so a name that changed is an error here and not a 404 on a teacher's download page.

Usage (from the repository root; ``--upload`` needs ``gh`` and ``GH_TOKEN``):

    python tools/release/mirror_sources.py                        # fetch and check, then discard
    python tools/release/mirror_sources.py --keep dist/sources    # keep the set for reading
    python tools/release/mirror_sources.py --bundle dist/teachers-teammate
    python tools/release/mirror_sources.py --upload --repo JoHoenk/teachers-teammate
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.licenses import artifacts, notice  # noqa: E402

QT_ARCHIVE = "https://download.qt.io/archive/qt"
"""Where Qt keeps every version, including the ones a mirror no longer carries."""

PYSIDE6_SOURCES = "https://download.qt.io/official_releases/QtForPython/pyside6"
"""Where the Qt for Python sources live, one folder per release."""

FFMPEG_RELEASES = "https://ffmpeg.org/releases"
"""Where FFmpeg publishes its tarballs and their signatures."""

PYPI = "https://pypi.org/pypi"
"""The JSON of one package release names its source distribution and its digest."""

ARCHIVE_SUFFIXES = (".tar.xz", ".tar.bz2", ".tar.gz")
"""What the authors package a source tree in; the first one a listing holds is taken."""

CHECKSUMS_NAME = "SHA256SUMS"
"""The digest list of the set, in ``sha256sum``'s format."""

README_NAME = "README.txt"
"""What the set holds, which version, and the rule each file satisfies."""

SUMS_NAMES = ("sha256sums.txt", "md5sums.txt")
"""The digest lists Qt publishes next to its tarballs, the stronger one first."""

USER_AGENT = "teachers-teammate-sources-mirror (+https://github.com/JoHoenk/teachers-teammate)"

QT_LICENSE_RULE = "LGPL-3.0 section 4: the Corresponding Source of the Qt libraries the app loads"
PYSIDE_RULE = "LGPL-3.0: the sources of the PySide6 bindings and the Qt for Python build"
FFMPEG_RULE = "LGPL-2.1: the source of the FFmpeg that ships inside OpenCV (Linux and macOS)"
OPENCV_RULE = "the build recipe and the FFmpeg version of the OpenCV wheel (Apache-2.0)"

#: Which Qt submodule archive carries which module: the ``libQt6<Module>`` files of a bundle have
#: to be accounted for by one of the archives NOTICE names.  A module PySide6 gains is a red check
#: here, so a person decides whether its source joins the set — it must not slip in.
MODULE_ARCHIVE: dict[str, str] = {
    "Core": "qtbase",
    "DBus": "qtbase",
    "Gui": "qtbase",
    "Network": "qtbase",
    "OpenGL": "qtbase",
    "OpenGLWidgets": "qtbase",
    "PrintSupport": "qtbase",
    "Sql": "qtbase",
    "Test": "qtbase",
    "Widgets": "qtbase",
    "Xml": "qtbase",
    "Concurrent": "qtbase",
    "EglFSDeviceIntegration": "qtbase",
    "EglFsKmsSupport": "qtbase",
    "UiTools": "qtbase",
    "XcbQpa": "qtbase",
    "Svg": "qtsvg",
    "SvgWidgets": "qtsvg",
    "WaylandClient": "qtwayland",
    "WaylandCompositor": "qtwayland",
    "WaylandEglClientHwIntegration": "qtwayland",
}


@dataclass(frozen=True)
class Source:
    """One file of the set."""

    name: str
    url: str
    version: str
    rule: str


def sources_release_tag(qt: str) -> str:
    """Return the name of the release that holds the sources of Qt *qt*."""
    return f"third-party-sources-qt{qt}"


def tree_versions(tree: Path = ROOT) -> tuple[str, str, str]:
    """Return the Qt, OpenCV and FFmpeg versions the downloads of *tree* carry.

    Raises:
        ValueError: NOTICE and the inventory disagree, or the inventory lacks a row.
    """
    locked = artifacts.locked_versions(tree)
    qt = locked.get("pyside6-essentials")
    opencv = locked.get("opencv-python-headless")
    if qt is None or opencv is None:
        raise ValueError("the inventory lacks a row for PySide6 or opencv-python-headless")
    ffmpeg, notice_opencv = notice.ffmpeg_versions((tree / "NOTICE").read_text(encoding="utf-8"))
    if notice_opencv != opencv:
        raise ValueError(
            f"NOTICE names opencv-python-headless {notice_opencv}, the inventory {opencv}"
        )
    return qt, opencv, ffmpeg


def qt_module_archives(tree: Path = ROOT) -> tuple[str, ...]:
    """Return the Qt submodule archives NOTICE names as the sources of the shipped libraries."""
    return notice.qt_modules((tree / "NOTICE").read_text(encoding="utf-8"))


def listing_files(html: str) -> tuple[str, ...]:
    """Return the file names an Apache-style directory index lists, folders left out."""
    names = re.findall(r'href="([^"/?#]+)"', html)
    return tuple(name for name in names if not name.endswith("/") and name != "..")


def sums_entries(text: str) -> dict[str, str]:
    """Return file name -> digest from a ``md5sum``- or ``sha256sum``-style list.

    A line that is not a digest and a name (a header, a signature) is skipped.
    """
    entries: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and re.fullmatch(r"[0-9a-fA-F]{32}|[0-9a-fA-F]{64}", parts[0]):
            entries[parts[1].lstrip("*")] = parts[0].lower()
    return entries


def digest_of(path: Path, algorithm: str) -> str:
    """Return the *algorithm* digest of *path*, in lowercase hex."""
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_digest(path: Path, expected: str, name: str) -> None:
    """Hold *path* to the published digest *expected*.

    Raises:
        ValueError: the digest does not match, or is of a kind we cannot compute.
    """
    algorithm = {32: "md5", 64: "sha256"}.get(len(expected))
    if algorithm is None:
        raise ValueError(f"the digest published for {name} is neither md5 nor sha256")
    actual = digest_of(path, algorithm)
    if actual != expected:
        raise ValueError(f"{name} does not match its published {algorithm}: {actual}")


def bundle_modules(lib_dir: Path) -> tuple[str, ...]:
    """Return the Qt module names of the ``libQt6<Module>.so*`` files in *lib_dir*."""
    modules = {
        match.group(1)
        for path in lib_dir.glob("libQt6*.so*")
        if (match := re.match(r"libQt6(\w+?)\.so", path.name))
    }
    return tuple(sorted(modules))


def uncovered(modules: tuple[str, ...], archives: tuple[str, ...]) -> list[str]:
    """Return the Qt modules of a bundle that no archive in *archives* carries."""
    found: list[str] = []
    for module in modules:
        archive = MODULE_ARCHIVE.get(module)
        if archive is None:
            found.append(f"Qt{module} is in no submodule we know; add it where it lives")
        elif archive not in archives:
            found.append(f"Qt{module} comes with {archive}, which the set does not hold")
    return found


def readme(versions: tuple[str, str, str], sources: tuple[Source, ...]) -> str:
    """Return the ``README.txt`` of a set: what each file is, its version and its rule."""
    qt, opencv, ffmpeg = versions
    lines = [
        f"Teacher's Teammate: third-party sources of Qt {qt}, OpenCV {opencv}, FFmpeg {ffmpeg}",
        "",
        "The LGPL asks for the source of the libraries a download carries. This release holds it:",
        "every file is unmodified, exactly as its author published it, and SHA256SUMS holds the",
        "digest of each one. The versions are the ones the downloads of this app carry; NOTICE and",
        "third_party_licenses/inventory.csv in the repository name them.",
        "",
        "| File | Version | The rule it satisfies |",
        "|---|---|---|",
    ]
    lines += [f"| {source.name} | {source.version} | {source.rule} |" for source in sources]
    lines += [
        "",
        "Do not delete this release while a download carrying these versions exists.",
        "",
    ]
    return "\n".join(lines)


def release_notes(versions: tuple[str, str, str]) -> str:
    """Return the release body: what the set holds and that it is never a version of the app."""
    qt, opencv, ffmpeg = versions
    return (
        f"The sources of the third-party libraries our downloads carry: the Qt {qt} submodules "
        f"NOTICE names, pyside-setup, FFmpeg {ffmpeg} and the opencv-python-headless {opencv} "
        "source. Unmodified, with SHA256SUMS. This release is never an application version and is "
        "never marked *Latest*; see NOTICE in the repository."
    )


def write_checksums(folder: Path, names: tuple[str, ...]) -> Path:
    """Write ``SHA256SUMS`` for *names* in *folder* and return it."""
    lines = [f"{digest_of(folder / name, 'sha256')}  {name}" for name in sorted(names)]
    target = folder / CHECKSUMS_NAME
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def read_url(url: str) -> str:
    """Return the text at *url*."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", "replace")


def download(url: str, path: Path) -> Path:
    """Fetch *url* into *path* and return it."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=600) as response, path.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return path


def first_present(names: tuple[str, ...], candidates: tuple[str, ...], where: str) -> str:
    """Return the first of *candidates* that the listing *names* holds.

    Raises:
        ValueError: the listing holds none of them, so the tool cannot guess a name.
    """
    for candidate in candidates:
        if candidate in names:
            return candidate
    raise ValueError(f"{where} lists none of {', '.join(candidates)}")


def packaged(base: str) -> tuple[str, ...]:
    """Return the archive names *base* may be published under."""
    return tuple(f"{base}{suffix}" for suffix in ARCHIVE_SUFFIXES)


def pyside_file(names: tuple[str, ...], qt: str, where: str) -> str:
    """Return the pyside-setup archive of Qt *qt* in the listing *names*.

    Qt for Python names the tarball after the release or after the series, so both are tried
    before the listing's single remaining candidate.

    Raises:
        ValueError: the listing holds none, or no single candidate either.
    """
    series = ".".join(qt.split(".")[:2])
    candidates = (
        *packaged(f"pyside-setup-everywhere-src-{qt}"),
        *packaged(f"pyside-setup-everywhere-src-{series}"),
    )
    for candidate in candidates:
        if candidate in names:
            return candidate
    found = sorted(name for name in names if name.startswith("pyside-setup-everywhere-src-"))
    if len(found) == 1:
        return found[0]
    raise ValueError(f"{where} lists no single pyside-setup archive: {found}")


def published_sums(
    read: Callable[[str], str], directory: str, names: tuple[str, ...]
) -> dict[str, str]:
    """Return the digests *directory* publishes, empty when it publishes none."""
    for candidate in SUMS_NAMES:
        if candidate in names:
            return sums_entries(read(f"{directory}/{candidate}"))
    return {}


def pypi_sdist(opencv: str, read: Callable[[str], str]) -> tuple[str, str, str]:
    """Return the name, url and sha256 of the OpenCV wheel's source distribution.

    Raises:
        ValueError: PyPI knows no source distribution for that version.
    """
    payload = json.loads(read(f"{PYPI}/opencv-python-headless/{opencv}/json"))
    sdists = [item for item in payload.get("urls", []) if item.get("packagetype") == "sdist"]
    if not sdists:
        raise ValueError(f"PyPI lists no source distribution of opencv-python-headless {opencv}")
    return str(sdists[0]["filename"]), str(sdists[0]["url"]), str(sdists[0]["digests"]["sha256"])


def collect(
    folder: Path,
    versions: tuple[str, str, str],
    *,
    modules: tuple[str, ...],
    fetch: Callable[[str, Path], Path] = download,
    read: Callable[[str], str] = read_url,
) -> tuple[Source, ...]:
    """Fetch every file of the set into *folder* and hold each one to its published digest.

    The file names come from the authors' directory listings rather than from the version, because
    Qt for Python drops the patch number and FFmpeg's compression has changed.  A name that is not
    there fails here, before anything is uploaded.

    Raises:
        ValueError: a listing does not carry a file, or a digest does not match.
    """
    qt, opencv, ffmpeg = versions
    series = ".".join(qt.split(".")[:2])
    found: list[Source] = []

    def take(name: str, url: str, version: str, rule: str, expected: str | None) -> None:
        path = fetch(url, folder / name)
        if expected is not None:
            check_digest(path, expected, name)
        found.append(Source(name, url, version, rule))

    submodules = f"{QT_ARCHIVE}/{series}/{qt}/submodules"
    qt_files = listing_files(read(f"{submodules}/"))
    qt_sums = published_sums(read, submodules, qt_files)
    for module in modules:
        name = first_present(qt_files, packaged(f"{module}-everywhere-src-{qt}"), submodules)
        take(name, f"{submodules}/{name}", qt, QT_LICENSE_RULE, qt_sums.get(name))

    pyside_dir = f"{PYSIDE6_SOURCES}/PySide6-{qt}-src"
    pyside_files = listing_files(read(f"{pyside_dir}/"))
    pyside_sums = published_sums(read, pyside_dir, pyside_files)
    name = pyside_file(pyside_files, qt, pyside_dir)
    take(name, f"{pyside_dir}/{name}", qt, PYSIDE_RULE, pyside_sums.get(name))

    ffmpeg_files = listing_files(read(f"{FFMPEG_RELEASES}/"))
    name = first_present(ffmpeg_files, packaged(f"ffmpeg-{ffmpeg}"), FFMPEG_RELEASES)
    take(name, f"{FFMPEG_RELEASES}/{name}", ffmpeg, FFMPEG_RULE, None)
    signature = f"{name}.asc"
    if signature not in ffmpeg_files:
        raise ValueError(f"{FFMPEG_RELEASES} publishes no signature for {name}")
    fetch(f"{FFMPEG_RELEASES}/{signature}", folder / signature)

    recipe = pypi_sdist(opencv, read)
    take(recipe[0], recipe[1], opencv, OPENCV_RULE, recipe[2])

    write_checksums(folder, tuple(source.name for source in found))
    (folder / README_NAME).write_text(readme(versions, tuple(found)), encoding="utf-8")
    return tuple(found)


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"{command[0]} is not on PATH") from None
    if check and result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemExit(f"{' '.join(command)} failed: {detail}")
    return result


def upload_commands(
    tag: str, folder: Path, repo: str, *, notes: str, exists: bool
) -> list[list[str]]:
    """Return the ``gh`` commands that publish *folder* as *tag*, never as *Latest*."""
    names = [str(path) for path in sorted(folder.iterdir()) if path.is_file()]
    if exists:
        return [["gh", "release", "upload", tag, *names, "--repo", repo, "--clobber"]]
    return [
        [
            "gh",
            "release",
            "create",
            tag,
            *names,
            "--repo",
            repo,
            "--latest=false",
            "--title",
            f"Third-party sources for Qt {tag.removeprefix('third-party-sources-qt')}",
            "--notes",
            notes,
        ]
    ]


def upload(tag: str, folder: Path, repo: str, *, notes: str) -> None:
    """Create or fill the release *tag*, then check that GitHub serves the set we wrote."""
    exists = _run(["gh", "release", "view", tag, "--repo", repo], check=False).returncode == 0
    for command in upload_commands(tag, folder, repo, notes=notes, exists=exists):
        _run(command)
    latest = _run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "isLatest", "--jq", ".isLatest"]
    )
    if latest.stdout.strip() != "false":
        raise SystemExit(f"{tag} is Latest; the app's update check would offer it as an update")
    with tempfile.TemporaryDirectory() as temporary:
        _run(
            [
                "gh",
                "release",
                "download",
                tag,
                "--repo",
                repo,
                "--pattern",
                CHECKSUMS_NAME,
                "--dir",
                temporary,
            ]
        )
        served = Path(temporary) / CHECKSUMS_NAME
        if served.read_bytes() != (folder / CHECKSUMS_NAME).read_bytes():
            raise SystemExit(f"{tag} serves another {CHECKSUMS_NAME} than the one we wrote")
    print(f"Published {tag}; it is not the latest release.")


def main(argv: list[str] | None = None) -> int:
    """Fetch, check and (with ``--upload``) publish the sources of the shipped versions."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        type=Path,
        default=None,
        metavar="DIR",
        help="keep the set in DIR instead of a temporary folder",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        metavar="DIR",
        help="the folder build whose Qt modules the set must cover (default: no check)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="publish the set as third-party-sources-qt<version> (public; needs gh)",
    )
    parser.add_argument(
        "--repo", default=None, metavar="OWNER/NAME", help="the repository --upload writes to"
    )
    args = parser.parse_args(argv)

    try:
        versions = tree_versions()
        modules = qt_module_archives()
    except (KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.bundle is not None:
        lib_dir = args.bundle / "_internal" / "PySide6" / "Qt" / "lib"
        if not lib_dir.is_dir():
            print(f"{lib_dir} is missing; build the bundle first", file=sys.stderr)
            return 1
        problems = uncovered(bundle_modules(lib_dir), modules)
        if problems:
            print("\n".join(problems), file=sys.stderr)
            return 1

    if args.upload and not args.repo:
        print("--upload needs --repo OWNER/NAME", file=sys.stderr)
        return 1

    with ExitStack() as stack:
        if args.keep is None:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        else:
            folder = args.keep
            folder.mkdir(parents=True, exist_ok=True)
        try:
            files = collect(folder, versions, modules=modules)
        except (OSError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        for source in files:
            print(f"{source.name}\t{source.version}\t{source.rule}")
        print(f"{len(files)} files with {CHECKSUMS_NAME} and {README_NAME} in {folder}")
        if args.upload and args.repo:
            upload(
                sources_release_tag(versions[0]), folder, args.repo, notes=release_notes(versions)
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
