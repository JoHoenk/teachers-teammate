"""Tests of the report folder: the page, the data files, the zip and the repo guard."""

from __future__ import annotations

import json
from pathlib import Path
import re
import zipfile

import pytest

from tests.tools import viewer_fixtures as fixtures
from tools import eval_report
from tools.model_eval.viewer import load, model, page

TEXTS = fixtures.records()


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


def test_the_folder_holds_the_page_the_script_and_one_file_per_text(tmp_path):
    """
    Given  a report of two texts
    When   it is written as a folder
    Then   the page, the script, the style, the index and one data file per text are there
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    assert (out / page.INDEX).is_file()
    assert (out / page.SCRIPT).is_file()
    assert (out / page.STYLE).is_file()
    assert (out / page.DATA / page.INDEX_DATA).is_file()
    assert sorted(one.name for one in (out / page.DATA).iterdir()) == [
        "index.js",
        "text-de-01.js",
        "text-de-02.js",
    ]


def test_the_page_loads_its_data_with_script_tags(tmp_path):
    """
    Given  a written report
    When   the page is read
    Then   it loads the index and the script by name, and nothing from the network
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    html = (out / page.INDEX).read_text(encoding="utf-8")

    assert re.search(r'<script src="data/index\.js\?[0-9a-f]{8}"></script>', html)
    assert re.search(r'<script src="viewer\.js\?[0-9a-f]{8}"></script>', html)
    assert re.search(r'<link rel="stylesheet" href="viewer\.css\?[0-9a-f]{8}">', html)
    assert "http://" not in html
    assert "https://" not in html


def test_every_url_carries_the_builds_stamp(tmp_path):
    """
    Given  a written report
    When   the page and its index data are read
    Then   every URL carries the same stamp, so a browser cannot serve the build before it
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    html = (out / page.INDEX).read_text(encoding="utf-8")
    stamp = _data(out, page.INDEX_DATA)["stamp"]

    assert f'<script src="viewer.js?{stamp}"></script>' in html
    assert f'<link rel="stylesheet" href="viewer.css?{stamp}">' in html
    assert f'<script src="data/index.js?{stamp}"></script>' in html


def test_no_file_of_the_report_reaches_the_network(tmp_path):
    """
    Given  a written report
    When   every file of it is read
    Then   none fetches anything, so the folder opens from file:// without a server
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    for path in out.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        assert "fetch(" not in text, path.name
        assert "XMLHttpRequest" not in text, path.name
        assert "http://" not in text, path.name
        assert "https://" not in text, path.name


def test_the_index_holds_the_variants_the_numbers_and_the_heat_map(tmp_path):
    """
    Given  a report of one variant
    When   the index data is read
    Then   it holds the variant, its scorecard, the texts and one heat-map cell per text
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    index = _data(out, page.INDEX_DATA)

    assert [variant["label"] for variant in index["variants"]] == [fixtures.MODEL]
    assert index["numbers"]["v1"]["rows"]["de/all"]["measures"]["recall"]["mean"] == 1.0
    assert [text["id"] for text in index["texts"]] == ["de-01", "de-02"]
    assert index["cells"]["v1"]["de-01"]["f_beta"] == 1.0
    assert index["errors"][0]["group"] == "WO"


def test_a_text_file_holds_the_text_and_every_version_of_it(tmp_path):
    """
    Given  a report of one variant
    When   a text's data file is read
    Then   it holds the text's rows and the version's edits, sentences and counts
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    stored = _data(out, "text-de-01.js")

    assert stored["text"]["id"] == "de-01"
    assert len(stored["text"]["rows"]) == 2
    assert list(stored["items"]) == ["v1/r1/de-01"]
    item = stored["items"]["v1/r1/de-01"]
    assert item["counts"]["exact"] == 1
    assert item["rows"][0]["text"].startswith("Ich habe einen Hund.")
    assert item["rows"][1] is None


def test_the_single_file_holds_the_whole_report(tmp_path):
    """
    Given  a report written as one file
    When   it is read
    Then   the index, every text and the script are in that one file
    """
    out = page.build(_model(tmp_path), tmp_path / "report", single_file=True)

    html = (out / page.SINGLE).read_text(encoding="utf-8")

    assert not (out / page.DATA).exists()
    assert "window.REPORT.index" in html
    assert "window.REPORT.text" in html
    assert "de-01" in html
    assert "function render()" in html


def test_the_single_file_holds_a_text_the_way_a_data_file_does(tmp_path):
    """
    Given  a report written as one file
    When   the text it assigns is read
    Then   it holds the text and its versions, as the folder's own data file does
    """
    out = page.build(_model(tmp_path), tmp_path / "report", single_file=True)

    html = (out / page.SINGLE).read_text(encoding="utf-8")
    assignment = html.split("window.REPORT.text = ", 1)[1].split("</script>", 1)[0]
    stored = json.loads(assignment.strip().rstrip(";"))

    assert stored["de-01"]["text"]["id"] == "de-01"
    assert list(stored["de-01"]["items"]) == ["v1/r1/de-01"]


def test_the_zip_is_written_next_to_the_folder(tmp_path):
    """
    Given  a report written with --zip
    When   the archive is opened
    Then   it holds the folder's files under the folder's name
    """
    out = page.build(_model(tmp_path), tmp_path / "report", zip_it=True)

    archive = out.with_suffix(".zip")

    assert archive.is_file()
    with zipfile.ZipFile(archive) as opened:
        names = opened.namelist()
    assert "report/index.html" in names
    assert "report/data/text-de-01.js" in names


def test_an_output_inside_a_work_tree_is_refused(tmp_path):
    """
    Given  a directory that is a git work tree
    When   a report is written inside it
    Then   it is refused, because the repository holds no corpus text
    """
    work_tree = tmp_path / "repo"
    (work_tree / ".git").mkdir(parents=True)

    with pytest.raises(ValueError, match="work tree"):
        page.build(_model(tmp_path), work_tree / "report")

    assert not (work_tree / "report").exists()


def test_a_text_file_creates_the_table_it_adds_itself_to(tmp_path):
    """
    Given  a folder report
    When   a text's data file is run on its own, as the page loads it, after index.js only
    Then   it creates window.REPORT.text itself instead of failing on a missing table
    """
    out = page.build(_model(tmp_path), tmp_path / "report")

    script = (out / "data" / "text-de-01.js").read_text(encoding="utf-8")

    first_use = script.index('window.REPORT.text["de-01"] =')
    assert "window.REPORT.text = window.REPORT.text || {};" in script[:first_use]


def test_a_rebuild_leaves_no_file_of_the_old_report(tmp_path):
    """
    Given  a report folder with a data file of a text that is no longer in the report
    When   the report is built again into the same folder
    Then   the old data file is gone, and a file that is not the report's stays
    """
    out = page.build(_model(tmp_path), tmp_path / "report")
    (out / "data" / "text-de-99.js").write_text("old", encoding="utf-8")
    (out / "notes.txt").write_text("mine", encoding="utf-8")

    page.build(_model(tmp_path), out)

    assert not (out / "data" / "text-de-99.js").exists()
    assert (out / "notes.txt").read_text(encoding="utf-8") == "mine"
    assert (out / "data" / "text-de-01.js").exists()


def test_a_single_file_rebuild_removes_the_old_folder_page(tmp_path):
    """
    Given  a folder report in a directory
    When   the report is built there again as a single file
    Then   the old index.html and data files are gone, so the directory holds one report
    """
    built = _model(tmp_path)
    out = page.build(built, tmp_path / "report")

    page.build(built, out, single_file=True)

    assert (out / page.SINGLE).exists()
    assert not (out / page.INDEX).exists()
    assert not (out / "data").exists()


def test_the_heat_map_says_whether_a_correct_text_was_left_alone(tmp_path):
    """
    Given  a text that has no reference error and a version that changes it
    When   the index is written
    Then   the cell of that text says in how many repeats the version changed it
    """
    correct = fixtures.record(
        item="de-03",
        source="Das ist gut.",
        reference="Das ist gut.",
        codes=(),
        spans=((0, 6),),
        unchanged=(0,),
        kind="correct",
    )
    texts = (*TEXTS, correct)
    testset = load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET, texts))
    directory = tmp_path / "results"
    header = fixtures.header()
    header["testset_sha256"] = testset.sha256
    fixtures.write_cache(
        directory,
        "run__T1__exchange__r1",
        header,
        [
            fixtures.row(text, output="Das ist gut!" if text.kind == "correct" else None)
            for text in texts
        ],
    )
    built = model.build(testset, load.read_caches([directory], testset))
    out = page.build(built, tmp_path / "report")

    cell = _data(out, "index.js")["cells"]["v1"]["de-03"]

    assert cell["f_beta"] is None
    assert (cell["changed"], cell["answered"]) == (1, 1)


def test_relative_paths_are_the_repositorys_not_the_runfiles(tmp_path, monkeypatch, capsys):
    """
    Given  a repository directory (as bazel run names it) holding a test set and a cache
    When   eval_report build is run with relative paths from another working directory
    Then   the paths are read and written relative to the repository, and the report is there
    """
    workspace = tmp_path / "ws"
    fixtures.write_testset(workspace / fixtures.TESTSET)
    fixtures.cache(workspace / "results")
    elsewhere = tmp_path / "runfiles"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("BUILD_WORKSPACE_DIRECTORY", str(workspace))

    code = eval_report.main(
        ["build", "--testset", fixtures.TESTSET, "--cache", "results", "--out", "reports/one"]
    )

    assert code == 0
    assert (workspace / "reports" / "one" / "index.html").exists()
    assert not (elsewhere / "reports").exists()
    assert "2 texts, 1 variants" in capsys.readouterr().out


def test_a_language_filter_does_not_list_the_other_languages_as_skipped(tmp_path, capsys):
    """
    Given  a cache of German texts and a build restricted to English
    When   eval_report build is run
    Then   the German rows are not reported as skipped
    """
    directory = fixtures.cache(tmp_path / "results")
    testset = fixtures.write_testset(
        tmp_path / fixtures.TESTSET,
        (
            *TEXTS,
            fixtures.record(
                item="en-01",
                language="en",
                source="She go.",
                reference="She goes.",
                codes=("R:VERB:SVA",),
                spans=((0, 3),),
                unchanged=(),
            ),
        ),
    )
    header = fixtures.header()
    header["testset_sha256"] = load.read_testset(testset).sha256
    for run in directory.glob("*.header.json"):
        run.write_text(json.dumps([header]), encoding="utf-8")

    code = eval_report.main(
        [
            "build",
            "--testset",
            str(testset),
            "--cache",
            str(directory),
            "--out",
            str(tmp_path / "report"),
            "--languages",
            "de",
        ]
    )

    assert code == 0
    assert ", 0 skipped" in capsys.readouterr().out


def test_a_folder_with_other_files_is_not_overwritten(tmp_path):
    """
    Given  an output folder that holds somebody's files and no earlier report
    When   a report is built into it
    Then   the build refuses and the files stay
    """
    built = model.build(load.read_testset(fixtures.write_testset(tmp_path / fixtures.TESTSET)), ())
    out = tmp_path / "docs"
    out.mkdir()
    (out / "viewer.js").write_text("mine", encoding="utf-8")

    with pytest.raises(ValueError, match="not an earlier report"):
        page.build(built, out)

    assert (out / "viewer.js").read_text(encoding="utf-8") == "mine"
