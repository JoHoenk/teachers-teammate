"""Tests that the page reads the fields the data files actually hold.

The viewer is JavaScript and cannot be run here, so the one thing that can go wrong silently is a
name: the page asks a text for ``ref_edits`` while the data file calls it ``reference``, and the
screen stays empty without an error.  These tests read the field names out of ``viewer.js`` and
check every one of them against a report built from the fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from tests.tools import viewer_fixtures as fixtures
from tools.model_eval.viewer import load, model, page

TEXTS = fixtures.records()

ACCESS = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)")
"""One field access in the page's script: the object's name and the field's name."""

DOM = {"appendChild", "className"}
"""The members the page calls on an element, which no data object has.

The page names a heat-map cell ``cell`` and a table row ``row``, and both are also elements, so a
few element members turn up under a data name.
"""


def _model(tmp_path):
    """Return the report of the fixture test set and one cache of one repeat."""
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET))
    directory = fixtures.cache(tmp_path / "results")
    return model.build(testset, load.read_caches([directory], testset))


def _data(folder: Path, name: str) -> dict:
    """Return what one data file assigns to ``window.REPORT``."""
    text = (folder / "data" / name).read_text(encoding="utf-8")
    assignment = text.strip().splitlines()[-1]
    return json.loads(assignment.split(" = ", 1)[1].rstrip(";"))


def _accessed(script: str) -> dict[str, set[str]]:
    """Return the field names the script reads, per object name."""
    found: dict[str, set[str]] = {}
    for name, field in ACCESS.findall(script):
        found.setdefault(name, set()).add(field)
    return found


def _fields(entries: dict | list[dict]) -> set[str]:
    """Return the field names of a list of dicts, or of one dict."""
    if isinstance(entries, dict):
        return set(entries)
    return {name for entry in entries for name in entry}


def test_every_field_the_page_reads_of_the_index_is_in_the_index(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of ``INDEX`` are compared with the index data
    Then   each of them is a key of the index
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["INDEX"] - DOM
    index = _data(out, page.INDEX_DATA)

    assert accessed
    assert accessed <= set(index)


def test_every_field_the_page_reads_of_a_text_is_in_the_text(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of a text are compared with a text's data file
    Then   each of them is a key of the text
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["text"] - DOM
    stored = _data(out, "text-de-01.js")

    assert accessed
    assert accessed <= set(stored["text"])


def test_every_field_the_page_reads_of_a_version_is_in_the_version(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of an item are compared with a version's data
    Then   each of them is a key of the item
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["item"] - DOM
    stored = _data(out, "text-de-01.js")

    assert accessed
    assert accessed <= set(stored["items"]["v1/r1/de-01"])


def test_every_field_the_page_reads_of_an_edit_is_in_the_edit(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of an edit are compared with the stored edits
    Then   each of them is a field of a model edit or of a reference edit
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["edit"] - DOM
    stored = _data(out, "text-de-01.js")
    fields = _fields(stored["items"]["v1/r1/de-01"]["edits"]) | _fields(stored["text"]["ref_edits"])

    assert accessed
    assert accessed <= fields


def test_every_field_the_page_reads_of_a_cell_is_in_the_heat_map(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of a heat-map cell are compared with the cells
    Then   each of them is a key of a cell
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["cell"] - DOM
    index = _data(out, page.INDEX_DATA)
    fields = {name for cells in index["cells"].values() for cell in cells.values() for name in cell}

    assert accessed
    assert accessed <= fields


def test_every_field_the_page_reads_of_a_variant_is_in_the_variant(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of a variant are compared with the index
    Then   each of them is a key of a variant
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["variant"] - DOM
    index = _data(out, page.INDEX_DATA)

    assert accessed
    assert accessed <= _fields(index["variants"])


def test_every_field_the_page_reads_of_an_error_row_or_an_input_is_there(tmp_path):
    """
    Given  a written report and the page's script
    When   the fields the script reads of an ``entry`` are compared with the error rows and inputs
    Then   each of them is a key of one of the two, and an error row has the shares it shows
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    script = (out / page.SCRIPT).read_text(encoding="utf-8")

    accessed = _accessed(script)["entry"] - DOM
    index = _data(out, page.INDEX_DATA)
    fields = _fields(index["errors"]) | _fields(index["inputs"])

    assert accessed
    assert accessed <= fields
    shares = next(iter(index["errors"][0]["shares"].values()))
    assert {"fixed", "located", "missed"} <= set(shares)


def test_the_index_names_the_eval_versions_the_page_makes_tabs_of(tmp_path):
    """
    Given  a written report
    When   the index is read
    Then   it lists the eval versions and every variant names its own
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    index = _data(out, page.INDEX_DATA)

    assert index["eval_versions"] == [2]
    assert {variant["eval_version"] for variant in index["variants"]} == {2}
