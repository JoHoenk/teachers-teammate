"""The sources mirror without the network: the names, the digests, the set and the gh commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.release import mirror_sources

QT, OPENCV, FFMPEG = "6.12.0", "5.1.0.95", "9.0.2"
MODULES = ("qtbase", "qtsvg", "qtimageformats", "qtwayland")

SUBMODULES = f"{mirror_sources.QT_ARCHIVE}/6.12/{QT}/submodules"
PYSIDE_DIR = f"{mirror_sources.PYSIDE6_SOURCES}/PySide6-{QT}-src"
FFMPEG_DIR = mirror_sources.FFMPEG_RELEASES
SDIST = f"opencv_python_headless-{OPENCV}.tar.gz"

PYSIDE = f"pyside-setup-everywhere-src-{QT}.tar.xz"
FFMPEG_ARCHIVE = f"ffmpeg-{FFMPEG}.tar.xz"


def _md5(data: bytes) -> str:
    """Return the digest Qt's ``md5sums.txt`` would carry for *data*."""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _sha256(data: bytes) -> str:
    """Return the digest PyPI publishes for *data*."""
    return hashlib.sha256(data).hexdigest()


def _files() -> dict[str, bytes]:
    """Return the bytes of every file of a set, keyed by the name it is published under."""
    return {
        **{
            f"{module}-everywhere-src-{QT}.tar.xz": f"{module} source".encode()
            for module in MODULES
        },
        PYSIDE: b"pyside source",
        FFMPEG_ARCHIVE: b"ffmpeg source",
        f"{FFMPEG_ARCHIVE}.asc": b"a signature",
        SDIST: b"opencv source",
    }


def _listing(*names: str) -> str:
    """Return an Apache-style directory index that lists *names*."""
    links = "".join(f'<a href="{name}">' for name in names)
    return f'<html><body>{links}<a href="../">Parent</a></body></html>'


def _answers(**overrides: str) -> dict[str, str]:
    """Return what each url of a run answers: the listings, the digests and the PyPI JSON."""
    files = _files()
    answers = {
        f"{SUBMODULES}/": _listing(
            *(f"{module}-everywhere-src-{QT}.tar.xz" for module in MODULES), "md5sums.txt"
        ),
        f"{SUBMODULES}/md5sums.txt": "".join(
            f"{_md5(files[f'{module}-everywhere-src-{QT}.tar.xz'])}  "
            f"{module}-everywhere-src-{QT}.tar.xz\n"
            for module in MODULES
        ),
        f"{PYSIDE_DIR}/": _listing(PYSIDE),
        f"{FFMPEG_DIR}/": _listing(FFMPEG_ARCHIVE, f"{FFMPEG_ARCHIVE}.asc", "ffmpeg-9.0.1.tar.xz"),
        f"{mirror_sources.PYPI}/opencv-python-headless/{OPENCV}/json": json.dumps(
            {
                "urls": [
                    {
                        "packagetype": "sdist",
                        "filename": SDIST,
                        "url": f"https://files.pythonhosted.org/packages/{SDIST}",
                        "digests": {"sha256": _sha256(files[SDIST])},
                    }
                ]
            }
        ),
    }
    answers.update(overrides)
    return answers


def _read(answers: dict[str, str]) -> Callable[[str], str]:
    """Return a reader that answers the urls of *answers* and nothing else."""

    def read(url: str) -> str:
        if url not in answers:
            raise OSError(f"nothing answers {url}")
        return answers[url]

    return read


def _fetch(paths: dict[str, bytes]) -> Callable[[str, Path], Path]:
    """Return a fetcher that writes the fixture bytes instead of asking the network."""

    def fetch(url: str, path: Path) -> Path:
        path.write_bytes(paths[url.rsplit("/", 1)[-1]])
        return path

    return fetch


def test_listing_files_returns_the_files_and_not_the_folders() -> None:
    """
    Given  the HTML of an Apache directory index
    When   listing_files() is called
    Then   the file names come back and the parent folder does not
    """
    html = _listing("qtbase-everywhere-src-6.12.0.tar.xz", "md5sums.txt")

    assert mirror_sources.listing_files(html) == (
        "qtbase-everywhere-src-6.12.0.tar.xz",
        "md5sums.txt",
    )


def test_sums_entries_read_a_digest_list() -> None:
    """
    Given  a digest list in ``md5sum`` and in ``sha256sum`` form, and a line that is neither
    When   sums_entries() is called
    Then   the digests come back by file name and the junk line is skipped
    """
    text = f"{'a' * 32}  qtbase.tar.xz\n{'b' * 64} *qtsvg.tar.xz\nSigned by: somebody\n"

    assert mirror_sources.sums_entries(text) == {
        "qtbase.tar.xz": "a" * 32,
        "qtsvg.tar.xz": "b" * 64,
    }


def test_check_digest_accepts_the_published_digest(tmp_path: Path) -> None:
    """
    Given  a file and the digest its author published
    When   check_digest() is called
    Then   it returns without raising
    """
    path = tmp_path / "archive.tar.xz"
    path.write_bytes(b"the source")

    mirror_sources.check_digest(path, _md5(path.read_bytes()), path.name)


def test_check_digest_refuses_another_file(tmp_path: Path) -> None:
    """
    Given  a file and a digest that belongs to another one
    When   check_digest() is called
    Then   a ValueError says which file does not match
    """
    path = tmp_path / "archive.tar.xz"
    path.write_bytes(b"the source")

    with pytest.raises(ValueError, match="does not match its published md5"):
        mirror_sources.check_digest(path, _md5(b"another source"), path.name)


def test_first_present_takes_the_first_packaging_the_listing_holds() -> None:
    """
    Given  a listing that holds the tarball and the tarball's signature
    When   first_present() is asked for the archive
    Then   the archive comes back, not its signature
    """
    names = ("ffmpeg-9.0.2.tar.asc", "ffmpeg-9.0.2.tar.xz")

    found = mirror_sources.first_present(
        names, mirror_sources.packaged("ffmpeg-9.0.2"), mirror_sources.FFMPEG_RELEASES
    )

    assert found == "ffmpeg-9.0.2.tar.xz"


def test_first_present_refuses_a_name_that_is_not_there() -> None:
    """
    Given  a listing without the archive we need
    When   first_present() is asked for it
    Then   a ValueError names the folder, so the tool never uploads a guessed name
    """
    with pytest.raises(ValueError, match="lists none of"):
        mirror_sources.first_present(
            ("other.tar.xz",), mirror_sources.packaged("ffmpeg-9.0.2"), FFMPEG_DIR
        )


def test_pyside_file_falls_back_to_the_series_name() -> None:
    """
    Given  a Qt for Python folder that names the tarball after the series, not the release
    When   pyside_file() is asked for Qt 6.12.0
    Then   the tarball it publishes is returned
    """
    names = ("pyside-setup-everywhere-src-6.12.tar.xz",)

    assert mirror_sources.pyside_file(names, QT, PYSIDE_DIR) == names[0]


def test_bundle_modules_read_the_qt_libraries_of_a_folder(tmp_path: Path) -> None:
    """
    Given  the Qt library folder of a bundle, with two versions of one library and a plugin
    When   bundle_modules() is called
    Then   each module is named once and nothing else is
    """
    for name in (
        "libQt6Core.so.6.12.0",
        "libQt6Core.so.6",
        "libQt6Svg.so.6.12.0",
        "libpyside6.abi3.so",
    ):
        (tmp_path / name).write_bytes(b"")

    assert mirror_sources.bundle_modules(tmp_path) == ("Core", "Svg")


def test_uncovered_names_a_module_whose_archive_is_not_in_the_set() -> None:
    """
    Given  a bundle with the Qt SVG module and a set of the other three archives
    When   uncovered() is called
    Then   it says that qtsvg is missing
    """
    problems = mirror_sources.uncovered(("Core", "Svg"), ("qtbase", "qtwayland"))

    assert problems == ["QtSvg comes with qtsvg, which the set does not hold"]


def test_uncovered_names_a_module_no_submodule_is_known_for() -> None:
    """
    Given  a bundle with a Qt module the table does not name
    When   uncovered() is called
    Then   it fails instead of covering the module with nothing
    """
    problems = mirror_sources.uncovered(("Charts",), MODULES)

    assert problems == ["QtCharts is in no submodule we know; add it where it lives"]


def test_sources_release_tag_names_the_qt_version() -> None:
    """
    Given  a Qt version
    When   sources_release_tag() is called
    Then   the tag is the one NOTICE points at
    """
    assert mirror_sources.sources_release_tag(QT) == f"third-party-sources-qt{QT}"


def _tree(tmp_path: Path, *, opencv: str = OPENCV) -> Path:
    """Lay out an inventory and a NOTICE as the repository has them."""
    (tmp_path / "third_party_licenses").mkdir(parents=True)
    (tmp_path / "third_party_licenses" / "inventory.csv").write_text(
        "Component,Version,License,Scope,Kind\n"
        f"pyside6-essentials,{QT},LGPL-3.0-only,bundle,package\n"
        f"opencv-python-headless,{opencv},Apache-2.0,bundle,package\n",
        encoding="utf-8",
    )
    (tmp_path / "NOTICE").write_text(
        "Teacher's Teammate\n\n"
        "Source code\n"
        "-----------\n"
        f"Qt {QT} and PySide6 {QT}, the versions the bundle carries:\n"
        "  https://download.qt.io/archive/qt/6.12/6.12.0/submodules/\n"
        "      (qtbase, qtsvg, qtimageformats, qtwayland)\n"
        "  https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/\n"
        "\n"
        f"FFmpeg {FFMPEG}, inside opencv-python-headless {OPENCV} (Linux and macOS):\n"
        f"  https://ffmpeg.org/releases/ffmpeg-{FFMPEG}.tar.gz\n",
        encoding="utf-8",
    )
    return tmp_path


def test_tree_versions_come_from_the_inventory_and_notice(tmp_path: Path) -> None:
    """
    Given  an inventory and a NOTICE that agree
    When   tree_versions() is called
    Then   the Qt, OpenCV and FFmpeg versions of the downloads come back
    """
    assert mirror_sources.tree_versions(_tree(tmp_path)) == (QT, OPENCV, FFMPEG)


def test_tree_versions_refuse_a_notice_that_names_another_opencv(tmp_path: Path) -> None:
    """
    Given  an inventory whose OpenCV is newer than the NOTICE that names its sources
    When   tree_versions() is called
    Then   a ValueError says which side says what, so the set is never built from a stale notice
    """
    tree = _tree(tmp_path, opencv="5.2.0.10")

    with pytest.raises(
        ValueError, match=f"NOTICE names opencv-python-headless {OPENCV}, the inventory 5.2.0.10"
    ):
        mirror_sources.tree_versions(tree)


def test_qt_module_archives_are_the_ones_notice_names(tmp_path: Path) -> None:
    """
    Given  a NOTICE that names the Qt submodules of the shipped libraries
    When   qt_module_archives() is called
    Then   the four archives come back in the order NOTICE writes them
    """
    assert mirror_sources.qt_module_archives(_tree(tmp_path)) == MODULES


def test_collect_fetches_the_set_and_writes_its_digests(tmp_path: Path) -> None:
    """
    Given  listings, digests and a PyPI answer for the shipped versions
    When   collect() runs with a fetcher that writes those bytes
    Then   the set holds every source plus its signature, README and SHA256SUMS, and the
           digests list one entry per file
    """
    answers = _answers()

    found = mirror_sources.collect(
        tmp_path,
        (QT, OPENCV, FFMPEG),
        modules=MODULES,
        fetch=_fetch(_files()),
        read=_read(answers),
    )

    names = {path.name for path in tmp_path.iterdir()}
    assert {source.name for source in found} == names - {
        f"{FFMPEG_ARCHIVE}.asc",
        mirror_sources.README_NAME,
        mirror_sources.CHECKSUMS_NAME,
    }
    listed = mirror_sources.sums_entries(
        (tmp_path / mirror_sources.CHECKSUMS_NAME).read_text(encoding="utf-8")
    )
    assert set(listed) == {source.name for source in found}
    readme = (tmp_path / mirror_sources.README_NAME).read_text(encoding="utf-8")
    assert all(source.name in readme for source in found)


def test_collect_refuses_a_digest_that_does_not_match(tmp_path: Path) -> None:
    """
    Given  a published digest list that belongs to another file
    When   collect() runs
    Then   a ValueError stops it before anything is uploaded
    """
    answers = _answers(
        **{f"{SUBMODULES}/md5sums.txt": f"{'0' * 32}  qtbase-everywhere-src-{QT}.tar.xz\n"}
    )

    with pytest.raises(ValueError, match="does not match its published md5"):
        mirror_sources.collect(
            tmp_path,
            (QT, OPENCV, FFMPEG),
            modules=MODULES,
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_collect_refuses_a_module_the_listing_does_not_carry(tmp_path: Path) -> None:
    """
    Given  a Qt submodule folder without the image-formats archive
    When   collect() runs with that module in the set
    Then   a ValueError names the missing archive instead of shipping an incomplete set
    """
    answers = _answers(
        **{
            f"{SUBMODULES}/": _listing(
                "qtbase-everywhere-src-6.12.0.tar.xz", "qtsvg-everywhere-src-6.12.0.tar.xz"
            )
        }
    )

    with pytest.raises(ValueError, match="qtimageformats-everywhere-src"):
        mirror_sources.collect(
            tmp_path,
            (QT, OPENCV, FFMPEG),
            modules=MODULES,
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_readme_names_every_file_with_its_rule(tmp_path: Path) -> None:
    """
    Given  a set of sources
    When   readme() is written
    Then   each file, its version and the rule it satisfies are in it
    """
    sources = (mirror_sources.Source("qtbase.tar.xz", "https://example", QT, "the Qt rule"),)

    text = mirror_sources.readme((QT, OPENCV, FFMPEG), sources)

    assert "| qtbase.tar.xz | 6.12.0 | the Qt rule |" in text
    assert "Do not delete this release" in text


def test_upload_creates_a_release_that_is_never_latest(tmp_path: Path) -> None:
    """
    Given  a folder that holds a set and no release of that name
    When   upload_commands() is asked for the commands
    Then   one ``gh release create`` names every file and passes --latest=false
    """
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")

    commands = mirror_sources.upload_commands(
        "third-party-sources-qt6.12.0", tmp_path, "owner/name", notes="what it holds", exists=False
    )

    assert len(commands) == 1
    assert commands[0][:4] == ["gh", "release", "create", "third-party-sources-qt6.12.0"]
    assert str(tmp_path / "SHA256SUMS") in commands[0]
    assert "--latest=false" in commands[0]


def test_upload_fills_an_existing_release(tmp_path: Path) -> None:
    """
    Given  a release that a failed run left behind
    When   upload_commands() is asked for the commands
    Then   it uploads over the old files instead of creating a second release
    """
    (tmp_path / "SHA256SUMS").write_text("", encoding="utf-8")

    commands = mirror_sources.upload_commands(
        "third-party-sources-qt6.12.0", tmp_path, "owner/name", notes="what it holds", exists=True
    )

    assert commands == [
        [
            "gh",
            "release",
            "upload",
            "third-party-sources-qt6.12.0",
            str(tmp_path / "SHA256SUMS"),
            "--repo",
            "owner/name",
            "--clobber",
        ]
    ]
