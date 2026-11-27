"""Tests for the developer tool that writes the line measure's references from a Chandra run."""

from __future__ import annotations

import json
from pathlib import Path
import shutil

from tools.dev.chandra_gt import (
    _references,
    lines_of,
    main,
    reference_of_blocks,
    references_of_markdown,
)


def _chunks(folder: Path, stem: str, pages: list[list[dict]]) -> None:
    """Write a Chandra ``_chunks.json`` of *pages*, one list of blocks per page."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{stem}_chunks.json").write_text(
        json.dumps(
            [
                {"page_num": number, "page_box": [0, 0, 100, 100], "chunks": blocks}
                for number, blocks in enumerate(pages)
            ]
        ),
        encoding="utf-8",
    )


def _block(content: str, label: str = "Text") -> dict:
    """Return one block the way Chandra writes it."""
    return {"bbox": [0, 0, 100, 10], "label": label, "content": content}


def test_a_line_break_is_two_written_lines() -> None:
    """
    Given  one block whose text breaks a line with a ``<br>``
    When   its lines are read
    Then   the break is a line of its own, because the measure compares written lines
    """
    assert lines_of("Der Hund läuft<br>über die Wiese") == ["Der Hund läuft", "über die Wiese"]


def test_a_paragraph_end_is_a_new_written_line() -> None:
    """
    Given  two paragraphs in one block and a table row
    When   their lines are read
    Then   each paragraph is a line and the cells of a row stay on one
    """
    assert lines_of("<p>Der Hund</p><p>läuft</p>") == ["Der Hund", "läuft"]
    assert lines_of("<tr><td>Eins</td><td>Zwei</td></tr>") == ["Eins Zwei"]


def test_markup_and_entities_do_not_reach_the_reference() -> None:
    """
    Given  a block with bold text, an inline formula and an escaped ampersand
    When   its lines are read
    Then   the words remain and only the markup is gone
    """
    assert lines_of("<b>Hund</b> &amp; Katze") == ["Hund & Katze"]
    assert lines_of("Zwei&nbsp;Wörter") == ["Zwei Wörter"]


def test_a_picture_block_is_no_written_line() -> None:
    """
    Given  a page with a text block, an image block and a figure block
    When   the transcription of the page is written
    Then   only the text block is in it
    """
    blocks = [
        _block("Der Hund läuft"),
        _block("image-block", label="Image"),
        _block("Eine Zeichnung", label="Figure"),
        _block("über die Wiese"),
    ]

    assert reference_of_blocks(blocks) == "Der Hund läuft\nüber die Wiese\n"


def test_a_page_without_written_text_has_an_empty_transcription() -> None:
    """
    Given  a page of nothing but a picture
    When   its transcription is written
    Then   it is empty, and the measure has nothing to compare the rows with
    """
    assert reference_of_blocks([_block("", label="Image")]) == ""


def test_each_page_of_the_output_gives_its_own_transcription(tmp_path: Path) -> None:
    """
    Given  a Chandra output of two pages, the second one with two written lines
    When   a transcription per page is read
    Then   page 1 holds its line and page 2 its two, in reading order
    """
    _chunks(tmp_path / "scan", "scan", [[_block("Erste Seite")], [_block("Zweite<br>Seite")]])
    (tmp_path / "scan" / "scan.md").write_text("ignored", encoding="utf-8")

    assert _references(tmp_path / "scan", "scan", 2) == ["Erste Seite\n", "Zweite\nSeite\n"]


def test_markdown_names_its_pages_only_when_it_was_paginated() -> None:
    """
    Given  a paginated Markdown output and one without page separators
    When   two pages are asked for
    Then   the paginated one is split and the other is refused rather than cut by guess
    """
    paginated = "Seite eins\n\n0" + "-" * 48 + "\n\n## Seite zwei\n\n- Ein Punkt\n"

    assert references_of_markdown(paginated, 2) == ["Seite eins\n", "Seite zwei\nEin Punkt\n"]
    assert references_of_markdown("Seite eins\nSeite zwei\n", 2) is None


def test_a_transcription_is_written_beside_the_page(tmp_path: Path, capsys) -> None:
    """
    Given  a page image and Chandra's output for it
    When   the tool runs
    Then   the transcription sits beside the page under the name the measure looks for
    """
    pages = tmp_path / "pages" / "handwritten"
    pages.mkdir(parents=True)
    (pages / "s01.png").write_bytes(b"page")
    chandra = tmp_path / "chandra"
    _chunks(chandra / "s01", "s01", [[_block("Der Hund läuft<br>über die Wiese")]])

    code = main(["--chandra", str(chandra), "--pages", str(pages)])

    assert code == 0
    assert (pages / "s01.gt.txt").read_text(encoding="utf-8") == "Der Hund läuft\nüber die Wiese\n"
    assert "s01.png: 2 lines -> s01.gt.txt" in capsys.readouterr().out


def test_each_page_of_a_pdf_gets_the_transcription_named_for_it(
    tmp_path: Path, sample_multipage_pdf
) -> None:
    """
    Given  a PDF of two pages and Chandra's output for both
    When   the tool runs
    Then   the transcriptions are named by their page, as the measure reads them
    """
    pages = tmp_path / "pages" / "photographed"
    pages.mkdir(parents=True)
    shutil.copy(sample_multipage_pdf(pages=2), pages / "scan.pdf")
    chandra = tmp_path / "chandra"
    _chunks(chandra / "scan", "scan", [[_block("Erste Seite")], [_block("Zweite Seite")]])

    assert main(["--chandra", str(chandra), "--pages", str(pages)]) == 0

    assert (pages / "scan_p1.gt.txt").read_text(encoding="utf-8") == "Erste Seite\n"
    assert (pages / "scan_p2.gt.txt").read_text(encoding="utf-8") == "Zweite Seite\n"


def test_a_transcription_that_is_already_there_is_kept(tmp_path: Path, capsys) -> None:
    """
    Given  a page whose transcription was written by hand
    When   the tool runs, and again with ``--overwrite``
    Then   the hand-written one survives the first run and gives way to the second
    """
    pages = tmp_path / "pages" / "printed"
    pages.mkdir(parents=True)
    (pages / "s01.png").write_bytes(b"page")
    (pages / "s01.gt.txt").write_text("Von Hand getippt\n", encoding="utf-8")
    chandra = tmp_path / "chandra"
    _chunks(chandra / "s01", "s01", [[_block("Von Chandra")]])

    assert main(["--chandra", str(chandra), "--pages", str(pages)]) == 1
    assert (pages / "s01.gt.txt").read_text(encoding="utf-8") == "Von Hand getippt\n"
    assert "s01.gt.txt: already there, kept" in capsys.readouterr().err

    assert main(["--chandra", str(chandra), "--pages", str(pages), "--overwrite"]) == 0
    assert (pages / "s01.gt.txt").read_text(encoding="utf-8") == "Von Chandra\n"


def test_a_page_chandra_did_not_read_is_named_and_skipped(tmp_path: Path, capsys) -> None:
    """
    Given  a page with no Chandra output
    When   the tool runs
    Then   it says where it looked and writes nothing
    """
    pages = tmp_path / "pages" / "handwritten"
    pages.mkdir(parents=True)
    (pages / "s01.png").write_bytes(b"page")

    assert main(["--chandra", str(tmp_path / "chandra"), "--pages", str(pages)]) == 1
    assert not (pages / "s01.gt.txt").exists()
    assert "s01.png: no Chandra output under" in capsys.readouterr().err
