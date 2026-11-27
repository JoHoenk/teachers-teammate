"""Tests for the developer tool that asks whether the reader's rows line up with written lines."""

from __future__ import annotations

from pathlib import Path
import shutil

from teachers_teammate.domain.page_layout import ReadLine
from tools.dev.page_lines import (
    THRESHOLDS,
    Page,
    Row,
    _cases,
    _rendered,
    _report,
    _rows,
    _share,
    _sweep,
)


def _line(text: str, confidence: float = 0.9) -> ReadLine:
    """Return a line the reader gave: its text and confidence (the measure ignores the boxes)."""
    return ReadLine(text=text, confidence=confidence, boxes=())


def test_a_row_that_is_a_written_line_lines_up() -> None:
    """
    Given  two rows whose words are the two written lines
    When   the rows are scored against the transcription
    Then   both line up and neither has an error
    """
    rows = _rows(
        [_line("Der Hund läuft"), _line("über die Wiese")],
        "Der Hund läuft\nüber die Wiese\n",
    )

    assert [row.kind for row in rows] == ["lined_up", "lined_up"]
    assert [row.error for row in rows] == [False, False]


def test_a_row_holding_two_written_lines_is_merged() -> None:
    """
    Given  one row whose words are most of two written lines
    When   the row is scored
    Then   it is merged, not lined up
    """
    rows = _rows([_line("Der Hund läuft über")], "Der Hund läuft\nüber die Wiese")

    assert [row.kind for row in rows] == ["merged"]


def test_one_written_line_over_two_rows_is_split() -> None:
    """
    Given  two rows that share one written line
    When   both rows are scored
    Then   each is split, because the line sends words to the other row
    """
    rows = _rows(
        [_line("Der Hund"), _line("läuft über die Wiese")], "Der Hund läuft über die Wiese"
    )

    assert [row.kind for row in rows] == ["split", "split"]


def test_a_row_whose_word_was_not_read_exactly_has_an_error() -> None:
    """
    Given  a row with one word the transcription does not hold
    When   the row is scored
    Then   it still lines up and has an error, which is what *Check first* has to find
    """
    rows = _rows([_line("Der Hunt läuft")], "Der Hund läuft")

    assert [(row.kind, row.error) for row in rows] == [("lined_up", True)]


def test_a_row_no_word_of_which_matched_is_unaligned() -> None:
    """
    Given  a row the transcription does not hold a word of
    When   the row is scored
    Then   it is unaligned and has an error
    """
    rows = _rows([_line("xyz")], "Der Hund läuft")

    assert [(row.kind, row.error) for row in rows] == [("unaligned", True)]


def test_a_page_without_rows_has_no_rows() -> None:
    """
    Given  a page the reader returned no line for
    When   it is scored
    Then   there is nothing to report and nothing to divide by
    """
    assert _rows([], "Der Hund läuft") == ()
    assert _share(0, 0) == "0"


def test_a_threshold_marks_the_rows_below_it() -> None:
    """
    Given  three rows, two of them with an error, at confidence 0.9, 0.4 and 0.3
    When   a threshold of 0.5 and one of 0.95 are swept
    Then   the low one marks the two unsure rows and catches both errors, the high one marks all
    """
    rows = (
        Row("lined_up", False, 0.9),
        Row("lined_up", True, 0.4),
        Row("merged", True, 0.3),
    )

    low, high = _sweep(rows, 0.5), _sweep(rows, 0.95)

    assert (low.flagged, low.flagged_with_error, low.error_rows) == (2, 2, 2)
    assert (high.flagged, high.flagged_with_error, high.error_rows) == (3, 2, 2)


def test_the_sweep_runs_from_half_to_ninety_five_percent() -> None:
    """
    Given  the thresholds the sweep tries
    When   they are listed
    Then   they run from 0.50 to 0.95 in steps of 0.05
    """
    assert len(THRESHOLDS) == 10
    assert (THRESHOLDS[0], THRESHOLDS[-1]) == (0.5, 0.95)


def test_the_report_gives_the_counts_per_group_and_no_text() -> None:
    """
    Given  two groups, one of them with an error row
    When   the report is written
    Then   each group's rows and the sweep are there, and no word of a page is
    """
    pages = [
        Page("handwritten", (Row("lined_up", False, 0.9), Row("merged", True, 0.2))),
        Page("printed", (Row("lined_up", False, 0.8),)),
    ]

    report = _report(pages)

    assert "| handwritten | 1 | 2 | 1 (50%) | 1 (50%) | 0 (0%) | 0 (0%) | 1 (50%) |" in report
    assert "| printed | 1 | 1 | 1 (100%) | 0 (0%) | 0 (0%) | 0 (0%) | 0 (0%) |" in report
    assert "handwritten: 2 rows, 1 of them with an error" in report
    assert "| 0.50 | 1 (50%) | 1 (100%) | 1 (100%) |" in report
    assert "Der Hund" not in report


def test_a_group_with_no_rows_is_reported_without_dividing_by_zero() -> None:
    """
    Given  a page the reader read no line on
    When   the report is written
    Then   the group keeps its row of zeroes
    """
    report = _report([Page("printed", ())])

    assert "| printed | 1 | 0 | 0 | 0 | 0 | 0 | 0 |" in report


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

    cases = _cases([tmp_path])

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

    cases = _cases([group])

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

    cases = _cases([group])

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

    assert _cases([group]) == []
    assert "broken.pdf: cannot be read, skipped" in capsys.readouterr().err


def test_every_page_of_a_pdf_is_rendered_the_way_a_run_renders_it(
    tmp_path: Path, sample_multipage_pdf
) -> None:
    """
    Given  a PDF of two pages
    When   its pages are rendered for the measure
    Then   there is one image per page, in page order, and it is a picture
    """
    from PIL import Image  # noqa: PLC0415

    pdf = sample_multipage_pdf(pages=2)

    rendered = _rendered(pdf, tmp_path)

    assert [path.name for path in rendered] == ["sample_page0.png", "sample_page1.png"]
    with Image.open(rendered[0]) as first, Image.open(rendered[1]) as second:
        assert first.size == second.size
        assert first.width > 0


def test_nothing_to_read_is_not_a_report() -> None:
    """
    Given  no page at all
    When   the report is written
    Then   it says so instead of an empty table
    """
    assert _report([]) == "No page was read."
