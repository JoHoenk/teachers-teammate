"""The page set: its pages, their transcriptions, what ``pages.jsonl`` says, its version, its split."""

from __future__ import annotations

from pathlib import Path
import shutil

from PIL import Image
import pytest

from tools.ocr_eval import pages, records


def _page(folder: Path, name: str, text: str = "Hallo Welt") -> Path:
    """Write one page image and the transcription beside it, and return the image."""
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / name
    image.write_bytes(b"an image")
    image.with_name(image.stem + pages.REFERENCE_SUFFIX).write_text(text, encoding="utf-8")
    return image


def _meta(folder: Path, rows: list[str]) -> None:
    """Write a ``pages.jsonl`` into *folder*."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / pages.METADATA_NAME).write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_page_files_finds_images_of_every_folder_below(tmp_path: Path) -> None:
    """Given a set with two groups
    When   page_files() is called on the set's root
    Then   it returns both pages, each group found below the root
    """
    one = _page(tmp_path / "set" / "handwritten_scan", "s01.png")
    two = _page(tmp_path / "set" / "printed_photo", "s02.jpg")

    assert pages.page_files(tmp_path / "set") == [one, two]


def test_cases_reads_the_transcription_beside_each_page(tmp_path: Path) -> None:
    """Given a page with a transcription and a page without one
    When   cases() is called on the folder
    Then   one case is returned, its group the folder's name, and the missing one is said on stderr
    """
    _page(tmp_path / "handwritten_scan", "s01.png", "Hallo Welt")
    (tmp_path / "handwritten_scan" / "s02.png").write_bytes(b"an image")

    found = pages.cases([tmp_path])

    assert [case.page_id for case in found] == ["s01"]
    assert found[0].reference == "Hallo Welt"
    assert found[0].group == "handwritten_scan"
    assert found[0].page is None


def test_transcription_counts_the_pages_of_a_longer_pdf(tmp_path: Path) -> None:
    """Given a PDF of several pages and a single-page file
    When   the transcription of page 2 of each is asked for
    Then   the PDF's is counted, and the single page's is the plain ``.gt.txt``
    """
    assert pages.transcription(Path("scan.pdf"), 1, 3).name == "scan_p2.gt.txt"
    assert pages.transcription(Path("s01.png"), 0, 1).name == "s01.gt.txt"
    assert pages.page_id(Path("scan.pdf"), 2, 3) == "scan_p3"


def test_metadata_reads_the_writer_and_the_language_of_each_page(tmp_path: Path) -> None:
    """Given a pages.jsonl naming one page's writer and language by its code, and one page bare
    When   metadata() is called
    Then   the code is read as the language's name, and the bare page is its own writer with no
           language of its own
    """
    _meta(tmp_path, ['{"id": "s01", "writer": "w3", "language": "de"}', '{"id": "s02"}'])

    found = pages.metadata(tmp_path)

    assert found["s01"] == pages.PageMeta(id="s01", writer="w3", language="German")
    assert found["s02"] == pages.PageMeta(id="s02", writer="s02", language="")


def test_metadata_refuses_a_line_without_an_id(tmp_path: Path) -> None:
    """Given a pages.jsonl whose line names no id
    When   metadata() is called
    Then   it refuses, naming the line
    """
    _meta(tmp_path, ['{"writer": "w3"}'])

    with pytest.raises(ValueError, match="no id"):
        pages.metadata(tmp_path)


def test_metadata_of_a_folder_without_one_is_empty(tmp_path: Path) -> None:
    """Given a set with no pages.jsonl
    When   metadata() is called
    Then   it returns nothing, so a run can say what is missing
    """
    assert pages.metadata(tmp_path) == {}


def test_set_version_changes_when_a_transcription_changes(tmp_path: Path) -> None:
    """Given a set whose transcription is then corrected
    When   the version is asked for before and after
    Then   the two versions differ, so the old figures are never pooled with the new
    """
    _page(tmp_path, "s01.png", "Hallo Welt")
    before = pages.set_version(pages.cases([tmp_path]))

    _page(tmp_path, "s01.png", "Hallo Welt!")
    after = pages.set_version(pages.cases([tmp_path]))

    assert before != after


def test_split_holds_whole_writers_out(tmp_path: Path) -> None:
    """Given six pages of four writers
    When   the set is split
    Then   no writer is on both sides, and the check side is not empty
    """
    rows = []
    for index, writer in enumerate(["w1", "w1", "w2", "w2", "w3", "w4"]):
        name = f"s{index:02d}"
        _page(tmp_path / "handwritten_scan", f"{name}.png")
        rows.append(f'{{"id": "{name}", "writer": "{writer}"}}')
    _meta(tmp_path, rows)
    found = pages.cases([tmp_path])
    meta = pages.metadata(tmp_path)

    sides = pages.split(found, meta)

    tune = {meta[page].writer for page, side in sides.items() if side == records.TUNE}
    check = {meta[page].writer for page, side in sides.items() if side == records.CHECK}
    assert tune & check == set()
    assert check


def test_a_page_is_taken_with_the_transcription_beside_it(tmp_path: Path, capsys) -> None:
    """
    Given  a folder of one page with a ``.gt.txt`` and one without
    When   the cases are read
    Then   the folder is the group and the page without a transcription is named and skipped
    """
    group = tmp_path / "handwritten"
    group.mkdir()
    (group / "s01.png").write_bytes(b"page")
    (group / "s01.gt.txt").write_text("Der Hund läuft\n", encoding="utf-8")
    (group / "s02.png").write_bytes(b"page")

    cases = pages.cases([tmp_path])

    assert [(case.image.name, case.group, case.reference, case.page) for case in cases] == [
        ("s01.png", "handwritten", "Der Hund läuft\n", None)
    ]
    assert "s02.png: no s02.gt.txt, skipped" in capsys.readouterr().err


def test_a_one_page_pdf_takes_the_transcription_of_its_name(
    tmp_path: Path, sample_pdf: Path, capsys
) -> None:
    """
    Given  a PDF of one page and the transcription named after the file
    When   the cases are read
    Then   the page is read against ``sample.gt.txt``, as page 0 of the PDF
    """
    group = tmp_path / "photographed"
    group.mkdir()
    shutil.copy(sample_pdf, group / "sample.pdf")
    (group / "sample.gt.txt").write_text("Der Hund läuft\n", encoding="utf-8")

    cases = pages.cases([group])

    assert [(case.image.name, case.page, case.reference) for case in cases] == [
        ("sample.pdf", 0, "Der Hund läuft\n")
    ]
    assert capsys.readouterr().err == ""


def test_each_page_of_a_longer_pdf_takes_its_own_transcription(
    tmp_path: Path, sample_multipage_pdf, capsys
) -> None:
    """
    Given  a PDF of three pages with a transcription for its second page only
    When   the cases are read
    Then   that page is case 1 and the two others are named by their page and skipped
    """
    group = tmp_path / "photographed"
    group.mkdir()
    shutil.copy(sample_multipage_pdf(), group / "scan.pdf")
    (group / "scan_p2.gt.txt").write_text("Der Hund läuft\n", encoding="utf-8")

    cases = pages.cases([group])

    assert [(case.image.name, case.page, case.reference) for case in cases] == [
        ("scan.pdf", 1, "Der Hund läuft\n")
    ]
    err = capsys.readouterr().err
    assert "scan.pdf page 1: no scan_p1.gt.txt, skipped" in err
    assert "scan.pdf page 3: no scan_p3.gt.txt, skipped" in err


def test_a_pdf_that_cannot_be_read_is_named_and_skipped(tmp_path: Path, capsys) -> None:
    """
    Given  a file that is called a PDF but is not one
    When   the cases are read
    Then   it is skipped with a line on stderr, not a traceback
    """
    group = tmp_path / "photographed"
    group.mkdir()
    (group / "broken.pdf").write_bytes(b"not a pdf")
    (group / "broken.gt.txt").write_text("Der Hund läuft\n", encoding="utf-8")

    assert pages.cases([group]) == []
    assert "broken.pdf: cannot be read, skipped" in capsys.readouterr().err


def test_every_page_of_a_pdf_is_rendered_the_way_a_run_renders_it(
    tmp_path: Path, sample_multipage_pdf
) -> None:
    """
    Given  a PDF of two pages
    When   its pages are rendered for the measure
    Then   there is one image per page, in page order, and it is a picture
    """
    pdf = sample_multipage_pdf(pages=2)

    rendered = pages.rendered(pdf, tmp_path)

    assert [path.name for path in rendered] == ["sample_page0.png", "sample_page1.png"]
    with Image.open(rendered[0]) as first, Image.open(rendered[1]) as second:
        assert first.size == second.size
        assert first.width > 0


def test_a_set_whose_metadata_leaves_a_page_out_is_refused(tmp_path: Path) -> None:
    """Given a set of two pages whose pages.jsonl names only one
    When   it is loaded
    Then   it is refused, naming the page left out: a record holds only the ids the set names
    """
    _page(tmp_path / "handwritten_scan", "s01.png")
    _page(tmp_path / "handwritten_scan", "Anna_Aufsatz.png")
    _meta(tmp_path, ['{"id": "s01", "writer": "w1"}'])

    with pytest.raises(ValueError, match="names no page Anna_Aufsatz"):
        pages.load(tmp_path)


def test_a_loaded_set_holds_its_pages_their_sides_and_its_version(tmp_path: Path) -> None:
    """Given a set of two pages of two writers
    When   it is loaded
    Then   each page is on one side, the set has a version, and a side holds its own pages
    """
    _page(tmp_path / "handwritten_scan", "s01.png")
    _page(tmp_path / "printed_scan", "s02.png")
    _meta(tmp_path, ['{"id": "s01", "writer": "w1"}', '{"id": "s02", "writer": "w2"}'])

    found = pages.load(tmp_path)

    assert set(found.sides.values()) == {records.TUNE, records.CHECK}
    assert found.version == pages.set_version(found.cases)
    assert [case.page_id for case in found.side(records.ALL)] == ["s01", "s02"]
    assert len(found.side(records.CHECK)) == 1
