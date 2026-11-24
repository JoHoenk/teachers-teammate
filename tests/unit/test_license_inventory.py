"""The committed license inventory: what it lists, and that it is not stale."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from tools.licenses import inventory, notice
from tools.release import mirror_sources

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


def test_every_row_passes_the_deny_rules_or_is_accepted() -> None:
    """
    Given  the committed inventory
    When   every row is held against the deny rules
    Then   nothing we must not ship or install is listed
    """
    assert inventory.problems(_committed(), inventory.load_policy()) == []


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


def test_the_license_the_app_names_is_the_one_in_license() -> None:
    """
    Given  the license name the About window and --version print
    When   it is compared with the first line of the repository's LICENSE
    Then   the name contains that line, so a relicense that forgets the texts is red
    """
    from teachers_teammate.texts import app as app_text  # noqa: PLC0415

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
    "run //tools/release:mirror_sources, name the new versions in the Qt/PySide6 and FFmpeg "
    "entries of NOTICE, then python tools/licenses/inventory.py --compile "
    "(docs/deployment.md, third-party sources)"
)


def test_notice_names_the_locked_versions_in_its_source_section() -> None:
    """
    Given  NOTICE and the committed inventory
    When   the source section is read
    Then   the Qt, PySide6 and OpenCV versions it names are the locked ones, and the sources
           release tag names the Qt version — a bump without a new pointer is red
    """
    versions = {row.component: row.version for row in _committed()}
    qt = versions["pyside6-essentials"]
    opencv = versions["opencv-python-headless"]

    section = notice.section(_notice(), notice.SOURCE_CODE)

    assert f"Qt {qt}" in section, _BUMP_STEPS
    assert f"PySide6 {qt}" in section, _BUMP_STEPS
    assert f"/archive/qt/{'.'.join(qt.split('.')[:2])}/{qt}/submodules/" in section, _BUMP_STEPS
    assert f"PySide6-{qt}-src" in section, _BUMP_STEPS
    assert f"third-party-sources-qt{qt}" in section, _BUMP_STEPS
    assert f"opencv-python-headless {opencv}" in section, _BUMP_STEPS


def test_notice_names_the_sources_the_mirror_publishes() -> None:
    """
    Given  NOTICE and the table that says which Qt submodule carries which module
    When   the archives a sources release is built from are held against the notice
    Then   NOTICE names no archive the table cannot place, and the FFmpeg source set is the
           notice's version.  The table knows archives the set does not hold (qttools); the gate
           asks for one the moment a bundle carries its module.
    """
    text = _notice()
    section = notice.section(text, notice.SOURCE_CODE)
    ffmpeg, _ = notice.ffmpeg_versions(text)

    assert set(notice.qt_modules(text)) - _PLUGIN_ONLY <= set(
        mirror_sources.MODULE_ARCHIVE.values()
    )
    assert f"ffmpeg-{ffmpeg}.tar" in section
    assert "the opencv-python-headless source" in section


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
