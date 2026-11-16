"""The Windows version resource, and the product name it shares with the installer."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re

import pytest

from tools.build import make_version_resource

#: The installer script, as Bazel's runfiles see it or in the source tree when run directly.
_NSI = (
    Path(os.environ["TEST_SRCDIR"]) / os.environ["WINDOWS_NSI"]
    if os.environ.get("TEST_SRCDIR") and os.environ.get("WINDOWS_NSI")
    else Path(__file__).resolve().parents[2] / "tools" / "packaging" / "windows.nsi"
)

#: ``!define APP_NAME      "Teacher's Teammate"``
_APP_NAME = re.compile(r'^!define\s+APP_NAME\s+"([^"]*)"', re.MULTILINE)
#: ``<product-name>Teacher's Teammate</product-name>`` in a signing artifact configuration.
_PRODUCT_NAME = re.compile(r"<product-name>([^<]*)</product-name>")


def test_a_release_version_gives_the_four_numbers_and_the_release_string() -> None:
    """
    Given  the release version 0.5.7
    When   the version resource is written
    Then   the fixed fields hold 0.5.7.0 and ProductVersion is the string the setup gets
    """
    assert make_version_resource.numeric_version("0.5.7") == (0, 5, 7, 0)
    fields = make_version_resource.fields("0.5.7")

    assert fields["ProductVersion"] == "0.5.7"
    assert fields["FileVersion"] == "0.5.7"
    assert fields["ProductName"] == "Teacher's Teammate"
    assert fields["CompanyName"] == "Teacher's Teammate Contributors"
    assert fields["OriginalFilename"] == "teachers-teammate.exe"


def test_a_prerelease_version_keeps_its_label_in_the_string_fields() -> None:
    """
    Given  the pre-release version 0.5.7-rc.1
    When   the version resource is written
    Then   the fixed fields drop the label and ProductVersion keeps it, as the setup does
    """
    assert make_version_resource.numeric_version("0.5.7-rc.1") == (0, 5, 7, 0)
    fields = make_version_resource.fields("0.5.7-rc.1")

    assert fields["ProductVersion"] == "0.5.7-rc.1"
    assert fields["FileVersion"] == "0.5.7-rc.1"


def test_the_unstamped_version_is_a_valid_resource() -> None:
    """
    Given  the version a build without a release tag carries, 0.1.0
    When   the version resource is written
    Then   it holds 0.1.0.0 and the same product strings
    """
    assert make_version_resource.numeric_version("0.1.0") == (0, 1, 0, 0)
    assert make_version_resource.fields("0.1.0")["ProductVersion"] == "0.1.0"


@pytest.mark.parametrize("version", ["0.5.7", "0.5.7-rc.1", "0.1.0"])
def test_the_written_file_is_the_structure_pyinstaller_reads(version: str) -> None:
    """
    Given  a version
    When   the resource text is written
    Then   it parses as Python, calls VSVersionInfo, and holds every field with its value
    """
    text = make_version_resource.version_resource(version)

    tree = ast.parse(text)
    assert isinstance(tree.body[0], ast.Expr)
    call = tree.body[0].value
    assert isinstance(call, ast.Call)
    assert getattr(call.func, "id", "") == "VSVersionInfo"
    for name, value in make_version_resource.fields(version).items():
        assert f"StringStruct({json.dumps(name)}, {json.dumps(value)})" in text


def test_the_product_name_is_the_same_in_the_exe_and_the_installer() -> None:
    """
    Given  the version resource generator and the NSIS script
    When   both are read
    Then   they name the same product, which a code-signing service checks
    """
    names = {make_version_resource.PRODUCT_NAME}
    names.update(_APP_NAME.findall(_NSI.read_text(encoding="utf-8")))

    # The artifact configurations of the signing workflow name the product too; they are read
    # here as soon as they exist, so the three files cannot drift apart.
    for configuration in sorted((_NSI.parent / "signpath").glob("*.xml")):
        text = configuration.read_text(encoding="utf-8")
        names.update(_PRODUCT_NAME.findall(text))

    assert names == {make_version_resource.PRODUCT_NAME}


def test_the_progress_line_is_ascii(tmp_path: Path, monkeypatch, capsys) -> None:
    """
    Given  a build on Windows, whose console encodes output as cp1252
    When   the generator runs from the command line
    Then   everything it prints is ASCII, so no character can crash it
    """
    monkeypatch.setattr(
        "sys.argv",
        ["make_version_resource.py", "--version", "0.5.7", "--output", str(tmp_path / "v.txt")],
    )

    make_version_resource.main()

    assert capsys.readouterr().out.isascii()
