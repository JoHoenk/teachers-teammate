"""The sources mirror without the network: the names, the digests, the set and the gh commands."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest

from tools.licenses import notice
from tools.release import mirror_sources

QT, OPENCV, FFMPEG, LIBICONV, LXML = "6.12.0", "5.1.0.95", "9.0.2", "1.18", "6.1.3"
MODULES = ("qtbase", "qtsvg", "qtimageformats", "qtwayland")
SOURCES_TAG = f"third-party-sources-qt{QT}-lxml-{LXML}-opencv-python-headless-{OPENCV}"

SUBMODULES = f"{mirror_sources.QT_ARCHIVE}/6.12/{QT}/submodules"
PYSIDE_DIR = f"{mirror_sources.PYSIDE6_SOURCES}/PySide6-{QT}-src"
FFMPEG_DIR = mirror_sources.FFMPEG_RELEASES
LIBICONV_DIR = mirror_sources.LIBICONV_RELEASES
SDIST = f"opencv_python_headless-{OPENCV}.tar.gz"
LXML_SDIST = f"lxml-{LXML}.tar.gz"

PYSIDE = f"pyside-setup-everywhere-src-{QT}.tar.xz"
FFMPEG_ARCHIVE = f"ffmpeg-{FFMPEG}.tar.xz"
FFMPEG_SIGNATURE = f"{FFMPEG_ARCHIVE}.asc"
LIBICONV_ARCHIVE = f"libiconv-{LIBICONV}.tar.gz"
LIBICONV_SIGNATURE = f"{LIBICONV_ARCHIVE}.sig"


def _contents(
    *,
    qt: str = QT,
    opencv: str = OPENCV,
    ffmpeg: str = FFMPEG,
    vendored: tuple[notice.Vendored, ...] | None = None,
) -> mirror_sources.Contents:
    """Return what a sources release of the fixture versions holds."""
    return mirror_sources.Contents(
        qt,
        MODULES,
        vendored
        if vendored is not None
        else (
            notice.Vendored("FFmpeg", ffmpeg, "opencv-python-headless", opencv, "Linux and macOS"),
            notice.Vendored("libiconv", LIBICONV, "lxml", LXML, "Linux and macOS"),
        ),
    )


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
        FFMPEG_SIGNATURE: b"a signature",
        SDIST: b"opencv source",
        LIBICONV_ARCHIVE: b"libiconv source",
        LIBICONV_SIGNATURE: b"a signature",
        LXML_SDIST: b"lxml source",
    }


def _set_files() -> tuple[str, ...]:
    """Return the file names a finished set holds, as a release holds them."""
    return (
        *(f"{module}-everywhere-src-{QT}.tar.xz" for module in MODULES),
        PYSIDE,
        FFMPEG_ARCHIVE,
        FFMPEG_SIGNATURE,
        SDIST,
        LIBICONV_ARCHIVE,
        LIBICONV_SIGNATURE,
        LXML_SDIST,
        mirror_sources.CHECKSUMS_NAME,
        mirror_sources.README_NAME,
    )


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
        f"{PYSIDE_DIR}/{PYSIDE}.sha256": f"{_sha256(files[PYSIDE])}  {PYSIDE}\n",
        f"{FFMPEG_DIR}/": _listing(FFMPEG_ARCHIVE, FFMPEG_SIGNATURE, "ffmpeg-9.0.1.tar.xz"),
        f"{LIBICONV_DIR}/": _listing(LIBICONV_ARCHIVE, LIBICONV_SIGNATURE, "libiconv-1.17.tar.gz"),
        **_sdist_answers("opencv-python-headless", OPENCV, SDIST, files),
        **_sdist_answers("lxml", LXML, LXML_SDIST, files),
    }
    answers.update(overrides)
    return answers


def _sdist_answers(
    package: str, version: str, name: str, files: dict[str, bytes]
) -> dict[str, str]:
    """Return PyPI's answer for one release: its source distribution and the sdist's sha256."""
    return {
        f"{mirror_sources.PYPI}/{package}/{version}/json": json.dumps(
            {
                "urls": [
                    {
                        "packagetype": "sdist",
                        "filename": name,
                        "url": f"https://files.pythonhosted.org/packages/{name}",
                        "digests": {"sha256": _sha256(files[name])},
                    }
                ]
            }
        )
    }


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


def test_published_sums_refuse_a_folder_that_publishes_no_digest() -> None:
    """
    Given  a folder that lists its files and no digest list
    When   published_sums() is called
    Then   a ValueError names the folder, so its files are never taken unchecked
    """
    with pytest.raises(ValueError, match=r"publishes no sha256sums\.txt or md5sums\.txt"):
        mirror_sources.published_sums(_read(_answers()), PYSIDE_DIR, (PYSIDE,))


def test_sidecar_digest_reads_the_sha256_file_beside_a_tarball() -> None:
    """
    Given  a folder whose index omits the <name>.sha256 file that sits beside the tarball
    When   sidecar_digest() is called
    Then   the digest in that file is returned
    """
    digest = mirror_sources.sidecar_digest(_read(_answers()), PYSIDE_DIR, PYSIDE)

    assert digest == _sha256(_files()[PYSIDE])


def test_sidecar_digest_refuses_a_tarball_without_a_sha256_file() -> None:
    """
    Given  a folder with no <name>.sha256 beside the tarball
    When   sidecar_digest() is called
    Then   a ValueError names the folder, so the file is never taken unchecked
    """
    answers = _answers()
    del answers[f"{PYSIDE_DIR}/{PYSIDE}.sha256"]

    with pytest.raises(ValueError, match=f"publishes no digest for {PYSIDE}"):
        mirror_sources.sidecar_digest(_read(answers), PYSIDE_DIR, PYSIDE)


def test_published_digest_names_a_file_the_list_leaves_out() -> None:
    """
    Given  a published digest list that does not name the file we fetched
    When   published_digest() is called
    Then   a ValueError says so instead of returning nothing to check
    """
    with pytest.raises(ValueError, match=f"publishes no digest for {PYSIDE}"):
        mirror_sources.published_digest({}, PYSIDE, PYSIDE_DIR)


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


def test_qt_version_drops_the_pyside_hotfix_number() -> None:
    """
    Given  a PySide6 hotfix release, which numbers itself one component further than Qt
    When   qt_version() is called
    Then   the Qt version comes back, so the archive folder it names exists
    """
    assert mirror_sources.qt_version("6.8.2.1") == "6.8.2"


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


def test_bundle_modules_refuse_a_folder_without_qt_libraries(tmp_path: Path) -> None:
    """
    Given  a folder that holds no ``libQt6*.so*`` file, like a macOS or Windows bundle
    When   bundle_modules() is called
    Then   a ValueError says which folder a Linux bundle keeps its modules in, so the coverage
           check cannot pass by seeing nothing
    """
    (tmp_path / "QtCore.framework").mkdir()

    with pytest.raises(ValueError, match=r"holds no libQt6\*\.so\* file"):
        mirror_sources.bundle_modules(tmp_path)


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


def test_uncovered_places_the_wayland_client_modules_in_qtbase() -> None:
    """
    Given  a bundle with the Wayland client and its wl-shell integration, which Qt 6.12 builds
           from qtbase, and a set that holds qtbase but not qtwayland
    When   uncovered() is called
    Then   nothing is missing, so the check does not ask for an archive the libraries are not in
    """
    assert mirror_sources.uncovered(("WaylandClient", "WlShellIntegration"), ("qtbase",)) == []


def test_uncovered_asks_for_qttools_when_the_bundle_carries_uitools() -> None:
    """
    Given  a bundle with QtUiTools, which comes from qttools and not from qtbase
    When   uncovered() is called with the four modules the set holds
    Then   it asks for qttools, so the check cannot pass on qtbase's account
    """
    problems = mirror_sources.uncovered(("UiTools",), MODULES)

    assert problems == ["QtUiTools comes with qttools, which the set does not hold"]


def test_sources_release_tag_names_every_package_that_carries_a_vendored_part() -> None:
    """
    Given  the Qt version and the packages that carry FFmpeg and libiconv
    When   sources_release_tag() is called
    Then   the tag names Qt and each package with its version, in a fixed order, so a bump of any
           of them makes another release and an older download keeps the sources it points at
    """
    tag = mirror_sources.sources_release_tag(_contents())

    assert tag == SOURCES_TAG
    assert tag != mirror_sources.sources_release_tag(_contents(opencv="5.2.0.1"))
    assert tag != mirror_sources.sources_release_tag(_contents(qt="6.13.0"))


def test_sources_release_title_names_qt_and_the_packages() -> None:
    """
    Given  the contents of a sources release
    When   sources_release_title() is called
    Then   the title says which Qt and which packages the set is for
    """
    title = mirror_sources.sources_release_title(_contents())

    assert title == f"Third-party sources for Qt {QT}, lxml {LXML}, opencv-python-headless {OPENCV}"


def _tree(tmp_path: Path, *, opencv: str = OPENCV) -> Path:
    """Lay out an inventory and a NOTICE as the repository has them."""
    (tmp_path / "third_party_licenses").mkdir(parents=True)
    (tmp_path / "third_party_licenses" / "inventory.csv").write_text(
        "Component,Version,License,Scope,Kind\n"
        f"pyside6-essentials,{QT},LGPL-3.0-only,bundle,package\n"
        f"opencv-python-headless,{opencv},Apache-2.0,bundle,package\n"
        f"lxml,{LXML},BSD-3-Clause,bundle,package\n",
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
        f"  https://ffmpeg.org/releases/ffmpeg-{FFMPEG}.tar.gz\n"
        "\n"
        f"libiconv {LIBICONV}, inside lxml {LXML} (Linux, Windows and macOS):\n"
        f"  https://ftp.gnu.org/pub/gnu/libiconv/libiconv-{LIBICONV}.tar.gz\n",
        encoding="utf-8",
    )
    return tmp_path


def test_tree_contents_come_from_the_inventory_and_notice(tmp_path: Path) -> None:
    """
    Given  an inventory and a NOTICE that agree
    When   tree_contents() is called
    Then   the Qt version, its modules and every part NOTICE says rides inside a package come back
    """
    contents = mirror_sources.tree_contents(_tree(tmp_path))

    assert contents.qt == QT
    assert contents.modules == MODULES
    assert [(entry.part, entry.version, entry.package) for entry in contents.vendored] == [
        ("FFmpeg", FFMPEG, "opencv-python-headless"),
        ("libiconv", LIBICONV, "lxml"),
    ]


def test_tree_contents_refuse_a_notice_that_names_another_opencv(tmp_path: Path) -> None:
    """
    Given  an inventory whose OpenCV is newer than the NOTICE that names its sources
    When   tree_contents() is called
    Then   a ValueError says which side says what, so the set is never built from a stale notice
    """
    tree = _tree(tmp_path, opencv="5.2.0.10")

    with pytest.raises(
        ValueError, match=f"NOTICE names opencv-python-headless {OPENCV}, the inventory 5.2.0.10"
    ):
        mirror_sources.tree_contents(tree)


def test_tree_contents_refuse_a_package_the_inventory_does_not_list(tmp_path: Path) -> None:
    """
    Given  a NOTICE that names a part inside a package no row lists
    When   tree_contents() is called
    Then   a ValueError names the package, so a renamed dependency cannot pass unnoticed
    """
    tree = _tree(tmp_path)
    (tree / "NOTICE").write_text(
        (tree / "NOTICE").read_text(encoding="utf-8").replace("inside lxml", "inside lxm1"),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="the inventory lacks a row for lxm1"):
        mirror_sources.tree_contents(tree)


def test_tree_contents_refuse_a_notice_that_leaves_out_a_part(tmp_path: Path) -> None:
    """
    Given  a NOTICE whose FFmpeg line no longer has the form of a part line
    When   tree_contents() is called
    Then   a ValueError names FFmpeg, so the set cannot lose its tarball unnoticed
    """
    tree = _tree(tmp_path)
    (tree / "NOTICE").write_text(
        (tree / "NOTICE")
        .read_text(encoding="utf-8")
        .replace("(Linux and macOS):", "(Linux and macOS)."),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no line for FFmpeg"):
        mirror_sources.tree_contents(tree)


def test_tree_contents_refuse_a_part_whose_source_is_unknown(tmp_path: Path) -> None:
    """
    Given  a NOTICE with a part this tool has no place for
    When   tree_contents() is called
    Then   a ValueError names the part, where the release gate can turn it into an error line
    """
    tree = _tree(tmp_path)
    (tree / "NOTICE").write_text(
        (tree / "NOTICE").read_text(encoding="utf-8")
        + f"\nzlib 1.3, inside lxml {LXML} (Linux):\n  https://zlib.net/\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="NOTICE names zlib"):
        mirror_sources.tree_contents(tree)


def test_collect_takes_the_sdist_of_a_package_once(tmp_path: Path) -> None:
    """
    Given  two parts NOTICE says ride inside the same package
    When   collect() runs
    Then   the package's source distribution is in the set, and listed, once
    """
    contents = _contents(
        vendored=(
            notice.Vendored("FFmpeg", FFMPEG, "opencv-python-headless", OPENCV),
            notice.Vendored("libiconv", LIBICONV, "opencv-python-headless", OPENCV),
        )
    )

    found = mirror_sources.collect(
        tmp_path, contents, fetch=_fetch(_files()), read=_read(_answers())
    )

    assert found.files.count(SDIST) == 1
    assert [source.name for source in found.sources].count(SDIST) == 1


def test_collect_fetches_the_set_and_writes_its_digests(tmp_path: Path) -> None:
    """
    Given  listings, digests and a PyPI answer for the shipped versions
    When   collect() runs with a fetcher that writes those bytes
    Then   the set holds every source plus the signature, README and SHA256SUMS, the digests
           cover every file of the set but the list itself, and files names what an upload sends
    """
    answers = _answers()

    found = mirror_sources.collect(
        tmp_path,
        _contents(),
        fetch=_fetch(_files()),
        read=_read(answers),
    )

    assert {path.name for path in tmp_path.iterdir()} == set(found.files)
    assert set(found.files) == set(_set_files())
    assert {source.name for source in found.sources} == set(_set_files()) - {
        FFMPEG_SIGNATURE,
        LIBICONV_SIGNATURE,
        mirror_sources.README_NAME,
        mirror_sources.CHECKSUMS_NAME,
    }
    listed = mirror_sources.sums_entries(
        (tmp_path / mirror_sources.CHECKSUMS_NAME).read_text(encoding="utf-8")
    )
    assert set(listed) == set(found.files) - {mirror_sources.CHECKSUMS_NAME}
    readme = (tmp_path / mirror_sources.README_NAME).read_text(encoding="utf-8")
    assert all(source.name in readme for source in found.sources)


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
            _contents(),
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
                "qtbase-everywhere-src-6.12.0.tar.xz",
                "qtsvg-everywhere-src-6.12.0.tar.xz",
                "md5sums.txt",
            )
        }
    )

    with pytest.raises(ValueError, match="qtimageformats-everywhere-src"):
        mirror_sources.collect(
            tmp_path,
            _contents(),
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_collect_refuses_a_libiconv_tarball_without_its_signature(tmp_path: Path) -> None:
    """
    Given  a libiconv folder that publishes the tarball and no signature
    When   collect() runs
    Then   a ValueError names the missing signature, the only evidence of that tarball's origin
    """
    answers = _answers(**{f"{LIBICONV_DIR}/": _listing(LIBICONV_ARCHIVE)})

    with pytest.raises(ValueError, match=f"publishes no signature for {LIBICONV_ARCHIVE}"):
        mirror_sources.collect(
            tmp_path,
            _contents(),
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_collect_refuses_a_part_whose_source_is_not_known(tmp_path: Path) -> None:
    """
    Given  a NOTICE that names a part this tool has never fetched
    When   collect() runs
    Then   a ValueError says where to record the part's source, so no file is guessed
    """
    contents = _contents(vendored=(notice.Vendored("zlib", "1.3", "lxml", LXML, ""),))

    with pytest.raises(ValueError, match="NOTICE names zlib, whose source this tool does not know"):
        mirror_sources.collect(tmp_path, contents, fetch=_fetch(_files()), read=_read(_answers()))


def test_collect_refuses_a_package_whose_source_owes_no_known_rule(tmp_path: Path) -> None:
    """
    Given  a NOTICE that names a part inside a package whose source distribution nothing explains
    When   collect() runs
    Then   a ValueError asks for the reason before the set takes a file it cannot justify
    """
    contents = _contents(vendored=(notice.Vendored("FFmpeg", FFMPEG, "openssl", "3.0.0", ""),))

    with pytest.raises(ValueError, match="nothing says why the source of openssl is owed"):
        mirror_sources.collect(tmp_path, contents, fetch=_fetch(_files()), read=_read(_answers()))


def test_pypi_sdist_refuses_a_release_without_one() -> None:
    """
    Given  a PyPI answer that lists wheels and no source distribution
    When   pypi_sdist() is called
    Then   a ValueError names the package and version instead of taking a wheel
    """
    answers = {
        f"{mirror_sources.PYPI}/lxml/{LXML}/json": json.dumps(
            {"urls": [{"packagetype": "bdist_wheel", "filename": "lxml.whl"}]}
        )
    }

    with pytest.raises(ValueError, match=f"lists no source distribution of lxml {LXML}"):
        mirror_sources.pypi_sdist("lxml", LXML, _read(answers))


def test_collect_refuses_a_pyside_folder_that_publishes_no_digest(tmp_path: Path) -> None:
    """
    Given  the PySide6 source folder without the <name>.sha256 beside its tarball
    When   collect() runs
    Then   a ValueError names the folder instead of taking the tarball unchecked
    """
    answers = _answers()
    del answers[f"{PYSIDE_DIR}/{PYSIDE}.sha256"]

    with pytest.raises(ValueError, match=f"{PYSIDE_DIR} publishes no digest for {PYSIDE}"):
        mirror_sources.collect(
            tmp_path,
            _contents(),
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_collect_refuses_a_qt_archive_its_digest_list_leaves_out(tmp_path: Path) -> None:
    """
    Given  a Qt digest list that names three of the four archives
    When   collect() runs
    Then   a ValueError names the file whose digest is missing
    """
    answers = _answers(
        **{
            f"{SUBMODULES}/md5sums.txt": "".join(
                f"{_md5(_files()[f'{module}-everywhere-src-{QT}.tar.xz'])}  "
                f"{module}-everywhere-src-{QT}.tar.xz\n"
                for module in MODULES
                if module != "qtsvg"
            )
        }
    )

    with pytest.raises(ValueError, match=f"publishes no digest for qtsvg-everywhere-src-{QT}"):
        mirror_sources.collect(
            tmp_path,
            _contents(),
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_collect_refuses_an_ffmpeg_tarball_without_its_signature(tmp_path: Path) -> None:
    """
    Given  an FFmpeg folder that publishes the tarball and no signature
    When   collect() runs
    Then   a ValueError names the missing signature, the only evidence of that tarball's origin
    """
    answers = _answers(**{f"{FFMPEG_DIR}/": _listing(FFMPEG_ARCHIVE)})

    with pytest.raises(ValueError, match=f"publishes no signature for {FFMPEG_ARCHIVE}"):
        mirror_sources.collect(
            tmp_path,
            _contents(),
            fetch=_fetch(_files()),
            read=_read(answers),
        )


def test_readme_names_every_file_with_its_rule() -> None:
    """
    Given  a set of sources
    When   readme() is written
    Then   each file, its version and the rule it satisfies are in it, and it says that FFmpeg
           was not verified against a digest
    """
    sources = (mirror_sources.Source("qtbase.tar.xz", "https://example", QT, "the Qt rule"),)

    text = mirror_sources.readme(sources)

    assert "| qtbase.tar.xz | 6.12.0 | the Qt rule |" in text
    assert "FFmpeg and libiconv publish a signature" in text
    assert "unverified" in text
    assert "Do not delete this release" in text


def test_missing_assets_accept_the_whole_set() -> None:
    """
    Given  a release that holds every file of the set
    When   missing_assets() is asked what it lacks
    Then   nothing is missing
    """
    assert mirror_sources.missing_assets(_set_files(), _contents()) == []


def test_missing_assets_find_the_sources_of_another_ffmpeg() -> None:
    """
    Given  a release that holds the set of an older FFmpeg while the commit ships a newer one
    When   missing_assets() is asked what it lacks
    Then   the tarball and its signature are named, so a bumped OpenCV cannot pass on the
           release's name alone
    """
    held = tuple(name for name in _set_files() if not name.startswith("ffmpeg-"))

    missing = mirror_sources.missing_assets(held, _contents(ffmpeg="9.0.3"))

    assert missing == [
        "it holds no FFmpeg 9.0.3 source",
        "it holds no FFmpeg 9.0.3 signature",
    ]


def test_missing_assets_refuse_a_pyside_of_another_series() -> None:
    """
    Given  a release that holds pyside-setup of an older Qt series
    When   missing_assets() is asked what it lacks
    Then   the shipped PySide6 source is named, so an old bindings tarball is no substitute
    """
    held = (
        *(name for name in _set_files() if name != PYSIDE),
        "pyside-setup-everywhere-src-6.11.0.tar.xz",
    )

    missing = mirror_sources.missing_assets(held, _contents())

    assert missing == [f"it holds no PySide6 {QT} source"]


def test_missing_assets_tell_a_signature_from_its_archive() -> None:
    """
    Given  a release that holds the FFmpeg signature and not the tarball it belongs to
    When   missing_assets() is asked what it lacks
    Then   the tarball is named and the signature is not
    """
    held = tuple(name for name in _set_files() if name != FFMPEG_ARCHIVE)

    missing = mirror_sources.missing_assets(held, _contents())

    assert missing == [f"it holds no FFmpeg {FFMPEG} source"]


def test_set_folder_problem_names_a_folder_that_is_not_empty(tmp_path: Path) -> None:
    """
    Given  a folder that holds an earlier run's tarballs
    When   set_folder_problem() is called
    Then   it refuses the folder, so an upload cannot publish files this run did not fetch
    """
    (tmp_path / "ffmpeg-9.0.1.tar.xz").write_bytes(b"an older tarball")

    problem = mirror_sources.set_folder_problem(tmp_path)

    assert problem is not None
    assert "is not empty" in problem


def test_set_folder_problem_accepts_an_empty_or_missing_folder(tmp_path: Path) -> None:
    """
    Given  an empty folder and a folder that does not exist yet
    When   set_folder_problem() is called
    Then   both are accepted, so --keep can name them
    """
    assert mirror_sources.set_folder_problem(tmp_path) is None
    assert mirror_sources.set_folder_problem(tmp_path / "new") is None


def test_release_assets_read_the_files_the_release_holds() -> None:
    """
    Given  a ``gh release view`` that lists the release's assets
    When   release_assets() is called
    Then   the file names come back, which is what the gate holds to the shipped versions
    """
    completed = subprocess.CompletedProcess(
        args=[], returncode=0, stdout="README.txt\nSHA256SUMS\n", stderr=""
    )

    with patch.object(mirror_sources, "_run", return_value=completed):
        assert mirror_sources.release_assets(SOURCES_TAG, "owner/name") == (
            "README.txt",
            "SHA256SUMS",
        )


def test_release_assets_return_nothing_for_a_release_that_is_not_there() -> None:
    """
    Given  a ``gh release view`` that answers "release not found"
    When   release_assets() is called
    Then   it returns None, which the gate reports as a release to create
    """
    completed = subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="release not found"
    )

    with patch.object(mirror_sources, "_run", return_value=completed):
        assert mirror_sources.release_assets(SOURCES_TAG, "owner/name") is None


def test_release_assets_exit_when_github_answers_otherwise() -> None:
    """
    Given  a GitHub that answers with a server error
    When   release_assets() is called
    Then   it exits instead of letting a gate pass or fail on the wrong side
    """
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="HTTP 502")

    with (
        patch.object(mirror_sources, "_run", return_value=completed),
        pytest.raises(SystemExit, match="could not read the release"),
    ):
        mirror_sources.release_assets(SOURCES_TAG, "owner/name")


def test_upload_creates_a_release_that_is_never_latest(tmp_path: Path) -> None:
    """
    Given  a set and no release of that name
    When   upload_commands() is asked for the commands
    Then   one ``gh release create`` names every file of the set and passes --latest=false
    """
    files = _set_files()

    commands = mirror_sources.upload_commands(
        SOURCES_TAG,
        tmp_path,
        files,
        "owner/name",
        title="a title",
        notes="what it holds",
        existing=None,
    )

    assert len(commands) == 1
    assert commands[0][:4] == ["gh", "release", "create", SOURCES_TAG]
    assert str(tmp_path / files[0]) in commands[0]
    assert "--latest=false" in commands[0]


def test_upload_fills_an_existing_release_and_rewrites_its_text(tmp_path: Path) -> None:
    """
    Given  a release that a failed run left behind
    When   upload_commands() is asked for the commands
    Then   it uploads over the old files and rewrites the title and notes that described them
    """
    files = _set_files()

    commands = mirror_sources.upload_commands(
        SOURCES_TAG,
        tmp_path,
        files,
        "owner/name",
        title="a title",
        notes="what it holds",
        existing=files,
    )

    upload, edit = commands
    assert upload[:4] == ["gh", "release", "upload", SOURCES_TAG]
    assert "--clobber" in upload
    assert edit[:4] == ["gh", "release", "edit", SOURCES_TAG]
    assert edit[-2:] == ["--notes", "what it holds"]


def test_upload_drops_the_files_of_an_earlier_set(tmp_path: Path) -> None:
    """
    Given  a release that still holds the tarballs of an older FFmpeg
    When   upload_commands() is asked for the commands
    Then   it deletes the assets this set does not write and keeps the rest, which the upload
           overwrites in place, so the release holds this set and nothing else
    """
    files = _set_files()

    commands = mirror_sources.upload_commands(
        SOURCES_TAG,
        tmp_path,
        files,
        "owner/name",
        title="a title",
        notes="what it holds",
        existing=("ffmpeg-9.0.1.tar.xz", "ffmpeg-9.0.1.tar.xz.asc", mirror_sources.README_NAME),
    )

    deleted = [command[4] for command in commands if command[2] == "delete-asset"]
    assert deleted == ["ffmpeg-9.0.1.tar.xz", "ffmpeg-9.0.1.tar.xz.asc"]
    assert commands[0][:5] == [
        "gh",
        "release",
        "delete-asset",
        SOURCES_TAG,
        "ffmpeg-9.0.1.tar.xz",
    ]
    assert [command[2] for command in commands[2:]] == ["upload", "edit"]


def _linux_bundle(root: Path, *libraries: str) -> Path:
    """Lay out the Qt library folder of a Linux bundle holding *libraries*."""
    lib_dir = root / "_internal" / "PySide6" / "Qt" / "lib"
    lib_dir.mkdir(parents=True)
    for name in libraries:
        (lib_dir / name).touch()
    return root


def test_no_fetch_accepts_a_bundle_whose_modules_the_set_covers(tmp_path: Path) -> None:
    """
    Given  a Linux bundle that holds QtCore and QtSvg, and the four archives NOTICE names
    When   main() runs with --bundle and --no-fetch
    Then   it exits 0 and downloads nothing, so CI can run it on every build
    """
    bundle = _linux_bundle(tmp_path, "libQt6Core.so.6", "libQt6Svg.so.6")

    with (
        patch.object(mirror_sources, "tree_contents", return_value=_contents()),
        patch.object(mirror_sources, "collect") as collect,
    ):
        code = mirror_sources.main(["--bundle", str(bundle), "--no-fetch"])

    assert code == 0
    collect.assert_not_called()


def test_no_fetch_fails_for_a_module_no_archive_carries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  a Linux bundle that gained QtCharts, which no archive of the set carries
    When   main() runs with --bundle and --no-fetch
    Then   it exits 1 and names the module, so a PySide6 that gains a module turns the build red
    """
    bundle = _linux_bundle(tmp_path, "libQt6Core.so.6", "libQt6Charts.so.6")

    with (
        patch.object(mirror_sources, "tree_contents", return_value=_contents()),
        patch.object(mirror_sources, "collect") as collect,
    ):
        code = mirror_sources.main(["--bundle", str(bundle), "--no-fetch"])

    assert code == 1
    assert "QtCharts" in capsys.readouterr().err
    collect.assert_not_called()


def test_no_fetch_without_a_bundle_is_refused() -> None:
    """
    Given  --no-fetch and no bundle
    When   main() parses its arguments
    Then   it exits with a usage error instead of checking nothing and reporting success
    """
    with pytest.raises(SystemExit) as stopped:
        mirror_sources.main(["--no-fetch"])

    assert stopped.value.code == 2
