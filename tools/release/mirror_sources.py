#!/usr/bin/env python3
"""Mirror the third-party sources our downloads promise: one release per version we ship.

Nothing in this repository holds the Qt, PySide6, FFmpeg or libiconv sources, and the LGPL wants
them next to the download, so every combination of versions a release carries gets a sources
release of its own: ``third-party-sources-qt<qt>`` and, for each package that carries a part whose
source we owe (OpenCV with its FFmpeg, lxml with its libiconv), ``-<package>-<version>``.  A set is
never replaced by another version's, so an older download keeps its sources.  This tool fetches the
files NOTICE names, holds each one to the digest its author publishes, writes ``SHA256SUMS`` and a
``README.txt`` into the set, and uploads the set.  It is never marked *Latest*, so the app's update
check cannot see it.

FFmpeg and libiconv publish a signature (``.asc``, ``.sig``) and no digest, so those two tarballs
are signature-only: the set carries the signature and neither the tool nor the set verifies it.
Every other file is refused when its author publishes no digest, never taken unchecked.

The versions come from ``third_party_licenses/inventory.csv`` and NOTICE, never from an argument:
the set holds what the download promises.  The file names come from the authors' own directory
listings, so a name that changed is an error here and not a 404 on a teacher's download page.

Which parts and which packages owe a source is NOTICE's ``Source code`` section, read by
``tools/licenses/notice.py``: a part it names is fetched from ``PART_SOURCES`` and the source
distribution of the package it rides in joins the set as well.

Usage (from the repository root; ``--upload`` needs ``gh`` and ``GH_TOKEN``):

    python tools/release/mirror_sources.py                        # fetch and check, then discard
    python tools/release/mirror_sources.py --keep dist/sources    # keep the set for reading
    python tools/release/mirror_sources.py --bundle dist/teachers-teammate
    python tools/release/mirror_sources.py --bundle dist/teachers-teammate --no-fetch
    python tools/release/mirror_sources.py --upload --repo JoHoenk/teachers-teammate
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
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

LIBICONV_RELEASES = "https://ftp.gnu.org/pub/gnu/libiconv"
"""Where libiconv publishes its tarballs and their signatures."""

PYPI = "https://pypi.org/pypi"
"""The JSON of one package release names its source distribution and its digest."""

ARCHIVE_SUFFIXES = (".tar.xz", ".tar.bz2", ".tar.gz")
"""What the authors package a source tree in; the first one a listing holds is taken."""

ARCHIVE_PATTERN = r"\.tar\.(xz|bz2|gz)"
"""How the name of a source archive ends; the asset names add the anchor they need."""

CHECKSUMS_NAME = "SHA256SUMS"
"""The digest list of the set, in ``sha256sum``'s format; it lists every other file of the set."""

README_NAME = "README.txt"
"""What the set holds, which version, and the rule each file satisfies."""

SUMS_NAMES = ("sha256sums.txt", "md5sums.txt")
"""The digest lists Qt publishes next to its tarballs, the stronger one first."""

USER_AGENT = "teachers-teammate-sources-mirror (+https://github.com/JoHoenk/teachers-teammate)"

QT_LICENSE_RULE = "LGPL-3.0 section 4: the Corresponding Source of the Qt libraries the app loads"
PYSIDE_RULE = "LGPL-3.0: the sources of the PySide6 bindings and the Qt for Python build"
FFMPEG_RULE = "LGPL-2.1: the source of the FFmpeg that ships inside OpenCV (Linux and macOS)"
LIBICONV_RULE = "LGPL-2.1: the source of the libiconv that lxml links statically"
OPENCV_RULE = "the build recipe and the FFmpeg version of the OpenCV wheel (Apache-2.0)"
LXML_RULE = "LGPL-2.1 section 6(a): the source of the work that links libiconv in"

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
    "UiTools": "qttools",
    "XcbQpa": "qtbase",
    "Svg": "qtsvg",
    "SvgWidgets": "qtsvg",
    "WaylandClient": "qtwayland",
    "WaylandCompositor": "qtwayland",
    "WaylandEglClientHwIntegration": "qtwayland",
}


@dataclass(frozen=True)
class PartSource:
    """Where the author of a part publishes its source, and the rule that file satisfies."""

    folder: str
    signature: str
    rule: str


PART_SOURCES: dict[str, PartSource] = {
    "FFmpeg": PartSource(FFMPEG_RELEASES, ".asc", FFMPEG_RULE),
    "libiconv": PartSource(LIBICONV_RELEASES, ".sig", LIBICONV_RULE),
}
"""Where the source of each part NOTICE names is published, by the name NOTICE gives the part."""

PACKAGE_RULES: dict[str, str] = {
    "opencv-python-headless": OPENCV_RULE,
    "lxml": LXML_RULE,
}
"""Why the source distribution of a package whose wheel carries a part joins the set."""


@dataclass(frozen=True)
class Contents:
    """What the sources release of a tree's downloads has to hold."""

    qt: str
    modules: tuple[str, ...]
    vendored: tuple[notice.Vendored, ...]


@dataclass(frozen=True)
class Source:
    """One file of the set."""

    name: str
    url: str
    version: str
    rule: str


@dataclass(frozen=True)
class SourceSet:
    """What one run wrote into the folder: the sources, and every file of the set.

    ``files`` is what an upload sends, so a folder that holds another run's tarballs cannot
    publish them by accident.
    """

    sources: tuple[Source, ...]
    files: tuple[str, ...]


def sources_release_tag(contents: Contents) -> str:
    """Return the name of the release that holds the sources of *contents*.

    Qt's version and that of every package that carries a vendored part (OpenCV with its FFmpeg,
    lxml with its libiconv) name the set, so a bump of any of them makes another release instead of
    replacing the files an older download still points at.
    """
    packages = sorted({(entry.package, entry.package_version) for entry in contents.vendored})
    return f"third-party-sources-qt{contents.qt}" + "".join(
        f"-{package}-{version}" for package, version in packages
    )


def sources_release_title(contents: Contents) -> str:
    """Return the title of the sources release of *contents*."""
    packages = sorted({(entry.package, entry.package_version) for entry in contents.vendored})
    named = [f"Qt {contents.qt}", *(f"{package} {version}" for package, version in packages)]
    return "Third-party sources for " + ", ".join(named)


def qt_version(pyside: str) -> str:
    """Return the Qt version of a PySide6 release.

    PySide6 may add a hotfix number of its own (``6.8.2.1``) on top of the Qt version it binds
    (``6.8.2``), and Qt's archive folders are named after the latter.
    """
    return ".".join(pyside.split(".")[:3])


def tree_contents(tree: Path = ROOT) -> Contents:
    """Return what the sources release of *tree*'s downloads has to hold.

    The versions come from the rows the inventory lists, so a notice that names another version of
    a package than the one we ship is refused here rather than published.

    Raises:
        ValueError: NOTICE and the inventory disagree, the inventory lacks a row, NOTICE leaves
            out a part whose source is owed, or names one this tool cannot place.
    """
    text = (tree / "NOTICE").read_text(encoding="utf-8")
    locked = artifacts.locked_versions(tree)
    pyside = locked.get("pyside6-essentials")
    if pyside is None:
        raise ValueError("the inventory lacks a row for pyside6-essentials")
    vendored = notice.vendored_entries(text)
    unnamed = sorted(set(PART_SOURCES) - {entry.part for entry in vendored})
    if unnamed:
        raise ValueError(
            f"NOTICE's source section names no line for {', '.join(unnamed)}, "
            "whose source the downloads owe"
        )
    for entry in vendored:
        version = locked.get(entry.package)
        if version is None:
            raise ValueError(f"the inventory lacks a row for {entry.package}")
        if version != entry.package_version:
            raise ValueError(
                f"NOTICE names {entry.package} {entry.package_version}, the inventory {version}"
            )
        part_source(entry.part)
        package_rule(entry.package)
    return Contents(qt_version(pyside), notice.qt_modules(text), vendored)


def part_source(part: str) -> PartSource:
    """Return where the source of *part* is published.

    Raises:
        ValueError: NOTICE names a part whose source no one has said where to find.
    """
    source = PART_SOURCES.get(part)
    if source is None:
        raise ValueError(
            f"NOTICE names {part}, whose source this tool does not know: add it to PART_SOURCES"
        )
    return source


def package_rule(package: str) -> str:
    """Return why the source distribution of *package* joins the set.

    Raises:
        ValueError: no one has said why the source of that package is owed.
    """
    rule = PACKAGE_RULES.get(package)
    if rule is None:
        raise ValueError(
            f"nothing says why the source of {package} is owed: add it to PACKAGE_RULES"
        )
    return rule


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
    """Return the Qt module names of the ``libQt6<Module>.so*`` files in *lib_dir*.

    Raises:
        ValueError: the folder holds no Qt module, so it is not the Qt library folder of a Linux
            bundle and the coverage check would otherwise pass by seeing nothing.
    """
    modules = {
        match.group(1)
        for path in lib_dir.glob("libQt6*.so*")
        if (match := re.match(r"libQt6(\w+?)\.so", path.name))
    }
    if not modules:
        raise ValueError(
            f"{lib_dir} holds no libQt6*.so* file: --bundle reads the Qt library folder of a "
            "Linux bundle (_internal/PySide6/Qt/lib), not a macOS or Windows one"
        )
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


def asset_patterns(contents: Contents) -> dict[str, re.Pattern[str]]:
    """Return what a sources release of *contents* has to hold: what it is -> how its name ends.

    The names are patterns because the authors' own names may differ from the version (Qt for
    Python drops the patch number) and because a tarball's compression has changed before.
    """
    qt = contents.qt
    archive = ARCHIVE_PATTERN

    def pattern(prefix: str) -> re.Pattern[str]:
        """Return the pattern of a file of the set that starts with *prefix*."""
        return re.compile(rf"^{re.escape(prefix)}{archive}$")

    patterns = {
        f"{module} source of Qt {qt}": pattern(f"{module}-everywhere-src-{qt}")
        for module in contents.modules
    }
    series = ".".join(qt.split(".")[:2])
    patterns[f"PySide6 {qt} source"] = re.compile(
        rf"^pyside-setup-everywhere-src-({re.escape(qt)}|{re.escape(series)}){archive}$"
    )
    for entry in contents.vendored:
        source = part_source(entry.part)
        stem = f"{entry.part.lower()}-{entry.version}"
        patterns[f"{entry.part} {entry.version} source"] = pattern(stem)
        patterns[f"{entry.part} {entry.version} signature"] = re.compile(
            rf"^{re.escape(stem)}{archive}{re.escape(source.signature)}$"
        )
        patterns[f"{entry.package} {entry.package_version} source"] = pattern(
            f"{entry.package.replace('-', '_')}-{entry.package_version}"
        )
    patterns["digest list"] = re.compile(rf"^{re.escape(CHECKSUMS_NAME)}$")
    patterns["readme"] = re.compile(rf"^{re.escape(README_NAME)}$")
    return patterns


def missing_assets(names: Iterable[str], contents: Contents) -> list[str]:
    """Return what a release holding *names* lacks of the sources of *contents*.

    The release name carries versions, not files: a half-filled release of a failed upload has the
    right name, so what is inside it is what counts.
    """
    return [
        f"it holds no {what}"
        for what, pattern in asset_patterns(contents).items()
        if not any(pattern.search(name) for name in names)
    ]


def readme(sources: tuple[Source, ...]) -> str:
    """Return the ``README.txt`` of a set: what each file is, its version and its rule."""
    lines = [
        "Teacher's Teammate: the third-party sources the downloads carry",
        "",
        "The LGPL asks for the source of the libraries a download carries. This release holds it:",
        "every file is unmodified, exactly as its author published it, and SHA256SUMS holds the",
        "digest of each one. The versions are the ones the downloads of this app carry; NOTICE and",
        "third_party_licenses/inventory.csv in the repository name them.",
        "",
        "Each file was held to the digest its author publishes: Qt's md5sums.txt next to the",
        "tarballs, PyPI's sha256 for a source distribution.",
        "FFmpeg and libiconv publish a signature (.asc, .sig) and no digest, so their tarballs",
        "travel with that signature unverified: check each signature against its author's release",
        "key before you trust the tarball itself.",
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


def release_notes(contents: Contents) -> str:
    """Return the release body: what the set holds and that it is never a version of the app."""
    parts = ", ".join(f"{entry.part} {entry.version}" for entry in contents.vendored)
    return (
        f"The sources of the third-party libraries our downloads carry: the Qt {contents.qt} "
        f"submodules NOTICE names, pyside-setup, {parts}, and the source of every package that "
        "carries one of them. Unmodified, with SHA256SUMS. This release is never an application "
        "version and is never marked *Latest*; see NOTICE in the repository."
    )


def write_checksums(folder: Path, names: tuple[str, ...]) -> Path:
    """Write ``SHA256SUMS`` for *names* in *folder* and return it, the list itself left out."""
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
    """Return the digests *directory* publishes for its files.

    Raises:
        ValueError: the folder publishes no digest list, so none of its files could be checked.
    """
    for candidate in SUMS_NAMES:
        if candidate in names:
            return sums_entries(read(f"{directory}/{candidate}"))
    raise ValueError(
        f"{directory} publishes no {' or '.join(SUMS_NAMES)}, so its files cannot be held to a "
        "published digest"
    )


def published_digest(sums: Mapping[str, str], name: str, where: str) -> str:
    """Return the digest *sums* holds for *name*.

    Raises:
        ValueError: the published list does not name the file.
    """
    digest = sums.get(name)
    if digest is None:
        raise ValueError(f"{where} publishes no digest for {name}")
    return digest


def pypi_sdist(package: str, version: str, read: Callable[[str], str]) -> tuple[str, str, str]:
    """Return the name, url and sha256 of one release's source distribution.

    Raises:
        ValueError: PyPI knows no source distribution for that version.
    """
    payload = json.loads(read(f"{PYPI}/{package}/{version}/json"))
    sdists = [item for item in payload.get("urls", []) if item.get("packagetype") == "sdist"]
    if not sdists:
        raise ValueError(f"PyPI lists no source distribution of {package} {version}")
    return str(sdists[0]["filename"]), str(sdists[0]["url"]), str(sdists[0]["digests"]["sha256"])


def collect(
    folder: Path,
    contents: Contents,
    *,
    fetch: Callable[[str, Path], Path] = download,
    read: Callable[[str], str] = read_url,
) -> SourceSet:
    """Fetch every file of the set into *folder* and hold each one to its published digest.

    The file names come from the authors' directory listings rather than from the version, because
    Qt for Python drops the patch number and FFmpeg's compression has changed.  A name that is not
    there fails here, before anything is uploaded.

    Raises:
        ValueError: a listing does not carry a file, a folder publishes no digest for one, a part
            or a package has no source or rule of its own, or a digest does not match.
    """
    qt = contents.qt
    series = ".".join(qt.split(".")[:2])
    found: list[Source] = []
    written: list[str] = []

    def take(name: str, url: str, version: str, rule: str, expected: str) -> None:
        """Fetch one file, hold it to *expected*, and add it to the set."""
        path = fetch(url, folder / name)
        check_digest(path, expected, name)
        found.append(Source(name, url, version, rule))
        written.append(name)

    submodules = f"{QT_ARCHIVE}/{series}/{qt}/submodules"
    qt_files = listing_files(read(f"{submodules}/"))
    qt_sums = published_sums(read, submodules, qt_files)
    for module in contents.modules:
        name = first_present(qt_files, packaged(f"{module}-everywhere-src-{qt}"), submodules)
        digest = published_digest(qt_sums, name, submodules)
        take(name, f"{submodules}/{name}", qt, QT_LICENSE_RULE, digest)

    pyside_dir = f"{PYSIDE6_SOURCES}/PySide6-{qt}-src"
    pyside_files = listing_files(read(f"{pyside_dir}/"))
    pyside_sums = published_sums(read, pyside_dir, pyside_files)
    name = pyside_file(pyside_files, qt, pyside_dir)
    digest = published_digest(pyside_sums, name, pyside_dir)
    take(name, f"{pyside_dir}/{name}", qt, PYSIDE_RULE, digest)

    # FFmpeg and libiconv publish a signature and no digest, so those tarballs are the files of
    # the set taken unchecked. The README says so; every other file needs a published digest.
    recipes: set[str] = set()
    for entry in contents.vendored:
        source = part_source(entry.part)
        rule = package_rule(entry.package)
        names = listing_files(read(f"{source.folder}/"))
        name = first_present(
            names, packaged(f"{entry.part.lower()}-{entry.version}"), source.folder
        )
        signature = f"{name}{source.signature}"
        if signature not in names:
            raise ValueError(f"{source.folder} publishes no signature for {name}")
        fetch(f"{source.folder}/{name}", folder / name)
        fetch(f"{source.folder}/{signature}", folder / signature)
        found.append(Source(name, f"{source.folder}/{name}", entry.version, source.rule))
        written += [name, signature]

        if entry.package not in recipes:
            recipes.add(entry.package)
            recipe = pypi_sdist(entry.package, entry.package_version, read)
            take(recipe[0], recipe[1], entry.package_version, rule, recipe[2])

    (folder / README_NAME).write_text(readme(tuple(found)), encoding="utf-8")
    checksums = write_checksums(folder, (*written, README_NAME))
    return SourceSet(tuple(found), (*written, README_NAME, checksums.name))


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise SystemExit(f"{command[0]} is not on PATH") from None
    if check and result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise SystemExit(f"{' '.join(command)} failed: {detail}")
    return result


def set_folder_problem(folder: Path) -> str | None:
    """Return what is wrong with *folder* as the place to write a set, ``None`` when nothing is.

    The set names what it holds, so a folder that already holds files would publish another run's
    tarballs with this run's ``SHA256SUMS``.
    """
    if folder.is_dir() and any(folder.iterdir()):
        return (
            f"{folder} is not empty; the set is written into an empty folder, so an upload cannot "
            "publish another run's files"
        )
    return None


def release_assets(tag: str, repo: str) -> tuple[str, ...] | None:
    """Return the files the release *tag* holds, or ``None`` when there is no such release.

    Raises:
        SystemExit: GitHub answered with anything but the release or a "not found".
    """
    result = _run(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "assets",
            "--jq",
            ".assets[].name",
        ],
        check=False,
    )
    if result.returncode == 0:
        return tuple(line.strip() for line in result.stdout.splitlines() if line.strip())
    if "not found" not in result.stderr.lower() and "not found" not in result.stdout.lower():
        raise SystemExit(f"could not read the release {tag}: {result.stderr.strip()}")
    return None


def upload_commands(
    tag: str,
    folder: Path,
    files: tuple[str, ...],
    repo: str,
    *,
    title: str,
    notes: str,
    existing: tuple[str, ...] | None,
) -> list[list[str]]:
    """Return the ``gh`` commands that make *tag* hold exactly the files of *folder*.

    *files* are names inside *folder* and the release is never marked *Latest*.  A release of an
    earlier run keeps the files this set does not have, and its title and notes would still
    describe them, so an existing release loses them first.
    """
    paths = [str(folder / name) for name in files]
    kept = set(files)
    commands = [
        ["gh", "release", "delete-asset", tag, name, "--repo", repo, "--yes"]
        for name in sorted(existing or ())
        if name not in kept
    ]
    if existing is None:
        commands.append(
            [
                "gh",
                "release",
                "create",
                tag,
                *paths,
                "--repo",
                repo,
                "--latest=false",
                "--title",
                title,
                "--notes",
                notes,
            ]
        )
        return commands
    commands.append(["gh", "release", "upload", tag, *paths, "--repo", repo, "--clobber"])
    commands.append(
        ["gh", "release", "edit", tag, "--repo", repo, "--title", title, "--notes", notes]
    )
    return commands


def upload(
    tag: str, folder: Path, files: tuple[str, ...], repo: str, *, title: str, notes: str
) -> None:
    """Create or fill the release *tag* with *files*, then check that GitHub serves them."""
    existing = release_assets(tag, repo)
    commands = upload_commands(
        tag, folder, files, repo, title=title, notes=notes, existing=existing
    )
    for command in commands:
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


def bundle_problem(bundle: Path, archives: tuple[str, ...]) -> str | None:
    """Return what keeps the Qt libraries of the Linux *bundle* from being covered, or ``None``."""
    lib_dir = bundle / "_internal" / "PySide6" / "Qt" / "lib"
    if not lib_dir.is_dir():
        return f"{lib_dir} is missing; build the bundle first"
    try:
        problems = uncovered(bundle_modules(lib_dir), archives)
    except ValueError as exc:
        return str(exc)
    return "\n".join(problems) or None


def main(argv: list[str] | None = None) -> int:
    """Fetch, check and (with ``--upload``) publish the sources of the shipped versions."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--keep",
        type=Path,
        default=None,
        metavar="DIR",
        help="keep the set in DIR, which has to be empty, instead of a temporary folder",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        metavar="DIR",
        help="the Linux bundle whose Qt modules the set must cover (default: no check)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="publish the set as third-party-sources-qt<qt>-<package>-<version>... (public; needs gh)",
    )
    parser.add_argument(
        "--repo", default=None, metavar="OWNER/NAME", help="the repository --upload writes to"
    )
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="with --bundle: check the bundle's Qt modules against NOTICE and stop (offline)",
    )
    args = parser.parse_args(argv)
    if args.no_fetch and args.bundle is None:
        parser.error("--no-fetch needs --bundle")
    if args.no_fetch and (args.upload or args.keep is not None):
        parser.error("--no-fetch downloads nothing, so there is nothing to --keep or --upload")

    try:
        contents = tree_contents()
    except (KeyError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    if args.bundle is not None:
        problem = bundle_problem(args.bundle, contents.modules)
        if problem is not None:
            print(problem, file=sys.stderr)
            return 1
        if args.no_fetch:
            print(f"Every Qt module of {args.bundle} has its source archive in NOTICE.")
            return 0

    if args.upload and not args.repo:
        print("--upload needs --repo OWNER/NAME", file=sys.stderr)
        return 1

    if args.keep is not None:
        empty = set_folder_problem(args.keep)
        if empty is not None:
            print(empty, file=sys.stderr)
            return 1

    with ExitStack() as stack:
        if args.keep is None:
            folder = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        else:
            folder = args.keep
            folder.mkdir(parents=True, exist_ok=True)
        try:
            found = collect(folder, contents)
        except (OSError, ValueError) as exc:
            print(exc, file=sys.stderr)
            return 1
        for source in found.sources:
            print(f"{source.name}\t{source.version}\t{source.rule}")
        print(f"{len(found.sources)} sources and {len(found.files)} files in {folder}")
        print("FFmpeg and libiconv publish no digest: their signatures travel unverified.")
        if args.upload and args.repo:
            upload(
                sources_release_tag(contents),
                folder,
                found.files,
                args.repo,
                title=sources_release_title(contents),
                notes=release_notes(contents),
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
