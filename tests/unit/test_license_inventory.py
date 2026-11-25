"""The committed license inventory: what it lists, and that it is not stale."""

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tomllib
from unittest.mock import patch

from teachers_teammate.texts import app as app_text
from tools.build.make_version_resource import LEGAL_COPYRIGHT
from tools.licenses import inventory, notice
from tools.release import mirror_sources

#: The license id in the DEP-5 spelling the Debian copyright uses, which is its own vocabulary.
_DEP5 = {"GPL-3.0-or-later": "GPL-3+"}

#: The checkout as Bazel's runfiles see it, or the source tree when the test runs directly.
_ROOT = (
    (Path(os.environ["TEST_SRCDIR"]) / os.environ["LICENSE_PYPROJECT"]).parent
    if os.environ.get("TEST_SRCDIR") and os.environ.get("LICENSE_PYPROJECT")
    else Path(__file__).resolve().parents[2]
)
_STORE = _ROOT / "third_party_licenses"

#: Archives that carry plugins only, so no ``libQt6<Module>`` library is named after them.
_PLUGIN_ONLY = {"qtimageformats"}


def _committed() -> list[inventory.Row]:
    """Return the rows of the committed inventory."""
    return inventory.read_inventory(_STORE / "inventory.csv")


def test_the_inventory_lists_exactly_what_the_locks_and_the_app_say() -> None:
    """
    Given  the committed inventory and the locks it is derived from
    When   the rows are worked out again
    Then   they are the same rows, versions included — a lock bump without a refresh is red
    """
    committed = [(row.component, row.version, row.scope, row.kind) for row in _committed()]
    expected = [
        (row.component, row.version, row.scope, row.kind)
        for row in inventory.expected(inventory.load_policy(), _ROOT)
    ]

    assert committed == expected, (
        "the inventory is out of date: run python tools/licenses/inventory.py"
    )


def test_a_package_the_add_on_lock_and_the_platform_pins_share_is_one_row() -> None:
    """
    Given  a package that the add-on lock carries and the platform pins list again
    When   the expected rows are worked out
    Then   the package has one add-on row, at the lock's version
    """
    locked = next(
        row
        for row in inventory.expected(inventory.load_policy(), _ROOT)
        if row.scope == "addon" and row.kind == "package"
    )

    with patch.object(inventory, "addon_platform_pins", return_value={locked.component: "0.0"}):
        rows = inventory.expected(inventory.load_policy(), _ROOT)

    assert [row.version for row in rows if row.component == locked.component] == [locked.version]


def test_every_row_is_allowed_or_accepted() -> None:
    """
    Given  the committed inventory
    When   every row is held against the allow list
    Then   nothing we must not ship or install is listed
    """
    assert inventory.problems(_committed(), inventory.load_policy()) == []


def test_no_text_names_a_copyleft_license_the_vendored_table_does_not() -> None:
    """
    Given  the committed inventory and the license texts of every bundle row
    When   each text is searched for the copyleft families
    Then   every family a text names is accounted for by the row's license or by a [vendored]
           entry — which is how a library linked into a wheel is found
    """
    policy = inventory.load_policy()

    assert inventory.copyleft_findings(_committed(), policy, _STORE) == [], (
        "a text names a copyleft license that nothing accounts for: name the part in the "
        "[vendored] table of tools/licenses/policy.toml, or say there why no source is owed"
    )


def test_the_scan_finds_the_libiconv_inside_lxml() -> None:
    """
    Given  the committed policy without lxml's entry
    When   the texts are searched
    Then   lxml's LGPL is named, so the check cannot pass by seeing nothing
    """
    policy = inventory.load_policy()
    without = replace(
        policy, vendored={name: parts for name, parts in policy.vendored.items() if name != "lxml"}
    )

    found = inventory.copyleft_findings(_committed(), without, _STORE)

    assert [line for line in found if line.startswith("lxml carries")] == [
        "lxml carries a text that names the LGPL (LICENSES.txt): name the part in [vendored]"
    ]


def test_every_part_that_owes_a_source_is_named_in_notice() -> None:
    """
    Given  the [vendored] table and NOTICE
    When   each part without a reason it needs no source is looked for in the source section
    Then   NOTICE names it inside its package, so the sources release fetches it
    """
    policy = inventory.load_policy()
    named = {(entry.part, entry.package) for entry in notice.vendored_entries(_notice())}

    missing = [
        f"{package} carries {part.part} ({part.license}): name it in NOTICE's Source code section"
        for package, parts in policy.vendored.items()
        for part in parts
        if not part.no_source and (part.part, package) not in named
    ]

    assert missing == []


def test_every_bundle_row_has_a_text() -> None:
    """
    Given  the committed inventory and the store
    When   each bundle row's texts are gathered
    Then   none is empty — a wheel's files, a curated folder or a [text] entry carries it
    """
    policy = inventory.load_policy()
    missing = [
        row.component
        for row in _committed()
        if row.scope == "bundle" and not inventory.texts_of(row.component, policy, _STORE)
    ]

    assert missing == [], (
        "no license text for these bundle rows: add the wheel's files, a folder under "
        "third_party_licenses/curated/ naming them, or a [text] entry in tools/licenses/policy.toml"
    )


def test_every_curated_stamp_matches_its_rows() -> None:
    """
    Given  the stamps of the hand-owned folders under curated/
    When   each is held against the rows it names
    Then   every row exists and its version still starts with the stamp's valid_for
    """
    versions = {row.component: row.version for row in _committed()}
    problems: list[str] = []
    for stamp in inventory.stamps(_STORE):
        for component in stamp.rows:
            version = versions.get(component)
            if version is None:
                problems.append(f"{stamp.folder.name} names {component}, which is not a row")
            elif stamp.valid_for and not version.startswith(stamp.valid_for):
                problems.append(
                    f"{stamp.folder.name} was written for {stamp.valid_for}, "
                    f"but {component} is {version}"
                )

    assert problems == [], (
        "a curated folder no longer matches its rows: refresh its texts, then its STAMP.toml"
    )


def test_the_compiled_outputs_are_what_the_store_compiles_to() -> None:
    """
    Given  the committed store
    When   the list is compiled again from it
    Then   it is byte for byte the committed one — no hand edit, no forgotten compile
    """
    rows = _committed()
    policy = inventory.load_policy()
    # Bytes, not text: some license files carry CRLF, and a text read would normalise them away.
    outputs = {
        _ROOT / "teachers_teammate" / "assets" / "third_party_licenses.txt": (
            inventory.compile_text(rows, policy, _STORE)
        ),
        _ROOT / "teachers_teammate" / "assets" / "third_party_inventory.csv": inventory.render(
            rows
        ),
    }
    stale = [
        str(path.relative_to(_ROOT))
        for path, text in outputs.items()
        if path.read_bytes() != text.encode("utf-8")
    ]

    assert stale == [], (
        f"stale compiled list: {stale} — run python tools/licenses/inventory.py --compile"
    )


def test_the_package_carries_the_license_we_ship_under() -> None:
    """
    Given  the repository's LICENSE
    When   the copy the app shows is read
    Then   it is byte for byte the same text — a relicensed LICENSE without a compile is red
    """
    asset = _ROOT / "teachers_teammate" / "assets" / "LICENSE.txt"

    assert asset.is_file(), "the app's own license does not ship: run inventory.py --compile"
    assert asset.read_bytes() == (_ROOT / "LICENSE").read_bytes(), (
        "teachers_teammate/assets/LICENSE.txt is stale: run inventory.py --compile"
    )


def _file(env: str, default: str) -> Path:
    """Return the checkout's *default* file, as Bazel's runfiles hand it over when it is data."""
    if os.environ.get("TEST_SRCDIR") and os.environ.get(env):
        return Path(os.environ["TEST_SRCDIR"]) / os.environ[env]
    return _ROOT / default


def test_the_license_id_is_the_one_every_file_states() -> None:
    """
    Given  the license id the About window and --version print
    When   every file that writes the license out by hand is compared with it
    Then   none disagrees, and LICENSE is the text of the version that id names
    """
    license_id = app_text.LICENSE_ID
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    wheel_bzl = _file("LICENSE_WHEEL_BZL", "teachers_teammate/wheel.bzl").read_text(
        encoding="utf-8"
    )
    nsis = _file("LICENSE_NSI", "tools/packaging/windows.nsi").read_text(encoding="utf-8")
    deb = _file("LICENSE_DEB", "tools/packaging/make_deb.sh").read_text(encoding="utf-8")

    assert pyproject["project"]["license"] == license_id
    assert f'license = "{license_id}"' in wheel_bzl
    assert LEGAL_COPYRIGHT.endswith(license_id)
    assert f"${{U+00B7}} {license_id}" in nsis
    assert f"License: {_DEP5[license_id]}" in deb
    assert (_ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[1].strip() == (
        "Version 3, 29 June 2007"
    )


def test_the_license_the_app_names_is_the_one_in_license() -> None:
    """
    Given  the license name the About window and --version print
    When   it is compared with the first line of the repository's LICENSE
    Then   the name contains that line, so a relicense that forgets the texts is red
    """
    text = (_ROOT / "LICENSE").read_text(encoding="utf-8")
    heading = next(line.strip() for line in text.splitlines() if line.strip())

    assert heading.casefold() in app_text.LICENSE_TITLE.casefold(), (
        f"texts/app.py names {app_text.LICENSE_TITLE!r}, LICENSE opens with {heading!r}"
    )


def _notice() -> str:
    """Return the NOTICE of the checkout."""
    return (_ROOT / "NOTICE").read_text(encoding="utf-8")


#: What a bumped Qt, PySide6 or OpenCV needs. The failing test below carries it, because that is
#: where a maintainer meets it.
_BUMP_STEPS = (
    "run //tools/release:mirror_sources, name the new versions and the new release name in the "
    "Qt/PySide6 and FFmpeg entries of NOTICE, then python tools/licenses/inventory.py --compile "
    "(docs/deployment.md, third-party sources)"
)


def test_notice_names_the_locked_versions_in_its_source_section() -> None:
    """
    Given  NOTICE and the committed inventory
    When   the source section is read
    Then   the Qt, PySide6 and lxml versions it names are the locked ones, and the sources
           release tag names the versions of Qt, lxml and OpenCV — a bump without a new pointer is red
    """
    versions = {row.component: row.version for row in _committed()}
    qt = versions["pyside6-essentials"]
    opencv = versions["opencv-python-headless"]
    lxml = versions["lxml"]

    section = notice.section(_notice(), notice.SOURCE_CODE)

    assert f"Qt {qt}" in section, _BUMP_STEPS
    assert f"PySide6 {qt}" in section, _BUMP_STEPS
    assert f"/archive/qt/{'.'.join(qt.split('.')[:2])}/{qt}/submodules/" in section, _BUMP_STEPS
    assert f"PySide6-{qt}-src" in section, _BUMP_STEPS
    assert f"third-party-sources-qt{qt}-lxml-{lxml}-opencv-python-headless-{opencv}" in section, (
        _BUMP_STEPS
    )
    assert f"inside lxml {lxml}" in section, _BUMP_STEPS


def test_notice_names_the_sources_the_mirror_publishes() -> None:
    """
    Given  NOTICE and the table that says which Qt submodule carries which module
    When   the archives a sources release is built from are held against the notice
    Then   NOTICE names no archive the table cannot place, and every part it names has a source
           URL this tool knows how to fetch.  The table knows archives the set does not hold
           (qttools); the gate asks for one the moment a bundle carries its module.
    """
    text = _notice()
    section = notice.section(text, notice.SOURCE_CODE)
    entries = notice.vendored_entries(text)

    assert set(notice.qt_modules(text)) - _PLUGIN_ONLY <= set(
        mirror_sources.MODULE_ARCHIVE.values()
    )
    assert entries, "NOTICE names no part inside a package"
    for entry in entries:
        assert mirror_sources.part_source(entry.part).folder in section, _BUMP_STEPS
        assert f"{entry.part.lower()}-{entry.version}.tar" in section, _BUMP_STEPS


def test_notice_holds_every_part_to_the_version_the_inventory_lists() -> None:
    """
    Given  the parts NOTICE says ride inside a package, and the committed inventory
    When   each entry's package version is held against its row
    Then   they are the same version, so an lxml or OpenCV bump stays red until a person has
           read the version of the part inside it
    """
    versions = {row.component: row.version for row in _committed()}

    stale = [
        f"NOTICE names {entry.part} {entry.version} inside {entry.package} "
        f"{entry.package_version}, the inventory {versions.get(entry.package)}"
        for entry in notice.vendored_entries(_notice())
        if versions.get(entry.package) != entry.package_version
    ]

    assert stale == [], _BUMP_STEPS


def test_notice_carries_the_written_offer() -> None:
    """
    Given  NOTICE
    When   the written offer is read
    Then   it names the program's own source and the sections it rests on: access to the source
           from the place of the download (6(d)) under the GPL and the LGPL
    """
    section = notice.section(_notice(), "Written offer")

    assert "Corresponding Source" in section
    assert '"Source code" archive' in section
    assert "GPL-3.0 section 6(d)" in section
    assert "LGPL-2.1 section 6(d)" in section
