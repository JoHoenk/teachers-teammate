"""GUI tests for ResultsTable using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QItemSelectionModel
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QMenu

from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._types import FileDoneEvent


@pytest.mark.gui
def test_results_table_queue_and_processing_transition(qtbot) -> None:
    """
    Given  a ResultsTable with two queued file names
    When   one file is marked as processing
    Then   that row status changes from pending to processing
    """
    table = ResultsTable()
    qtbot.addWidget(table)

    table.set_queue(["a.pdf", "b.png"])
    table.mark_processing("a.pdf")

    ocr_cell = table.item(0, ResultsTable._COL_OCR)
    assert ocr_cell is not None
    assert ocr_cell.text() == "▶"


@pytest.mark.gui
def test_results_table_add_result_and_selected_paths(qtbot, tmp_path: Path) -> None:
    """
    Given  a ResultsTable row with result artifacts
    When   the row is selected and selected_paths() is queried
    Then   all stored paths are returned for the selected file
    """
    raw = tmp_path / "item_ocr.txt"
    corr = tmp_path / "item_correction.txt"
    eval_txt = tmp_path / "item_evaluation.txt"
    raw.write_text("raw", encoding="utf-8")
    corr.write_text("corr", encoding="utf-8")
    eval_txt.write_text("eval", encoding="utf-8")

    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["item.png"], source_ids=["/tmp/item.png"])
    table.add_result(
        FileDoneEvent(
            source_id="/tmp/item.png",
            name="item.png",
            ok=True,
            message="saved",
            ocr_s=1.2,
            correction_s=0.0,
            preview_img="",
            raw_txt=str(raw),
            corr_txt=str(corr),
            eval_txt=str(eval_txt),
        )
    )

    table.selectRow(0)
    name, preview_img, raw_txt, corr_txt, eval_path = table.selected_paths()

    assert name == "item.png"
    assert preview_img == ""
    assert raw_txt == str(raw)
    assert corr_txt == str(corr)
    assert eval_path == str(eval_txt)


@pytest.mark.gui
def test_results_table_selected_source_ids_returns_multiple_rows(qtbot) -> None:
    """
    Given  a ResultsTable with queued rows and explicit source ids
    When   two rows are selected
    Then   selected_source_ids() returns both source ids in row order
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(
        ["a.png", "b.png", "c.png"],
        source_ids=["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"],
    )

    selection_model = table.selectionModel()
    assert selection_model is not None
    selection_model.select(
        table.model().index(2, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    selection_model.select(
        table.model().index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )

    assert table.selected_source_ids() == ["/tmp/a.png", "/tmp/c.png"]


@pytest.mark.gui
def test_results_table_emits_preview_requested_on_double_click(qtbot, tmp_path: Path) -> None:
    """
    Given  a ResultsTable row with raw/correction/evaluation artifact paths
    When   the file cell is double-clicked
    Then   preview_requested is emitted with a pixmap and the artifact paths
    """
    raw = tmp_path / "preview_ocr.txt"
    corr = tmp_path / "preview_correction.txt"
    eval_txt = tmp_path / "preview_evaluation.txt"
    raw.write_text("raw", encoding="utf-8")
    corr.write_text("corr", encoding="utf-8")
    eval_txt.write_text("eval", encoding="utf-8")

    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["preview.png"], source_ids=["/tmp/preview.png"])
    table.add_result(
        FileDoneEvent(
            source_id="/tmp/preview.png",
            name="preview.png",
            ok=True,
            message="saved",
            ocr_s=1.0,
            correction_s=0.0,
            preview_img="",
            raw_txt=str(raw),
            corr_txt=str(corr),
            eval_txt=str(eval_txt),
        )
    )

    with qtbot.waitSignal(table.preview_requested, timeout=1000) as sig:
        table.cellDoubleClicked.emit(0, ResultsTable._COL_FILE)

    args = sig.args
    assert isinstance(args[0], QPixmap)
    assert args[1] == str(raw)
    assert args[2] == str(corr)
    assert args[3] == str(eval_txt)


@pytest.mark.gui
def test_results_table_context_menu_emits_stage_run_requested(qtbot, monkeypatch) -> None:
    """
    Given  a selected queue row and a context menu opened on the table
    When   the user selects "Re-run Correction"
    Then   stage_run_requested emits the selected file name and stage id
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.png"])
    table.selectRow(0)

    # PySide6 C++ methods can't be patched at class level. Substitute the QMenu
    # class at the import site with a subclass whose exec() returns the action
    # immediately (no blocking modal dialog).
    class _NonBlockingMenu(QMenu):
        def exec(self, *args, **kwargs):  # Qt naming: overrides QMenu.exec
            return self.actions()[1]  # "Re-run Correction"

    monkeypatch.setattr("teachers_teammate.gui._results_table.QMenu", _NonBlockingMenu)

    with qtbot.waitSignal(table.stage_run_requested, timeout=1000) as sig:
        table._on_context_menu_requested(QPoint(1, 1))

    assert sig.args[0] == ["a.png"]
    assert sig.args[1] == "correction"


@pytest.mark.gui
def test_results_table_mark_cached_shows_stage_granularity(qtbot) -> None:
    """
    Given  a queued file row
    When   mark_cached() is called with OCR-only and then correction-complete states
    Then   status text reflects the highest cached stage instead of generic "Cached"
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["page.png"])

    table.mark_cached(
        "page.png",
        preview_img="",
        raw_txt="raw",
        corr_txt="",
        eval_txt="",
        ocr_done=True,
        correction_done=False,
        evaluation_done=False,
    )
    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "-"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "-"

    table.mark_cached(
        "page.png",
        preview_img="",
        raw_txt="raw",
        corr_txt="corr",
        eval_txt="",
        ocr_done=True,
        correction_done=True,
        evaluation_done=False,
    )
    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "✓"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "-"


@pytest.mark.gui
def test_results_table_mark_correcting_promotes_ocr(qtbot) -> None:
    """
    Given  a row whose OCR cell is currently running
    When   mark_correcting() is called
    Then   OCR flips to done (✓) and correction shows running (▶)
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.png"])
    table.mark_processing("a.png")

    table.mark_correcting("a.png")

    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "▶"


@pytest.mark.gui
def test_results_table_mark_evaluating_promotes_correction(qtbot) -> None:
    """
    Given  a row whose correction cell is currently running
    When   mark_evaluating() is called
    Then   correction flips to done (✓) and evaluation shows running (▶)
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.png"])
    table.mark_processing("a.png")
    table.mark_correcting("a.png")

    table.mark_evaluating("a.png")

    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "✓"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "▶"


@pytest.mark.gui
def test_results_table_mark_methods_noop_for_unknown_source(qtbot) -> None:
    """
    Given  a ResultsTable with no matching row
    When   the mark_* methods are called with an unknown source id
    Then   they return without error and add no rows
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.png"])

    table.mark_processing("missing")
    table.mark_correcting("missing")
    table.mark_evaluating("missing")
    table.set_row_artifacts("missing", "", "", "", "")
    table.set_evaluation_text("missing", "x")

    assert table.rowCount() == 1


@pytest.mark.gui
def test_results_table_entry_for_source_returns_artifacts(qtbot) -> None:
    """
    Given  a queued row with artifacts attached via set_row_artifacts
    When   entry_for_source() is queried
    Then   it returns the source id, name, and stored artifact strings
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["doc.png"], source_ids=["/tmp/doc.png"])
    table.set_row_artifacts("/tmp/doc.png", "prev.png", "raw", "corr", "eval")

    entry = table.entry_for_source("/tmp/doc.png")

    assert entry == ("/tmp/doc.png", "doc.png", "prev.png", "raw", "corr", "eval")


@pytest.mark.gui
def test_results_table_entry_for_source_unknown_returns_blanks(qtbot) -> None:
    """Given no matching row / When entry_for_source is queried / Then a 6-tuple of empty strings is returned."""
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["doc.png"], source_ids=["/tmp/doc.png"])

    assert table.entry_for_source("/tmp/nope.png") == ("", "", "", "", "", "")


@pytest.mark.gui
def test_results_table_set_evaluation_text_updates_entry(qtbot) -> None:
    """
    Given  a queued row
    When   set_evaluation_text() attaches evaluation content
    Then   entry_for_source reflects the new evaluation string
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["doc.png"], source_ids=["/tmp/doc.png"])

    table.set_evaluation_text("/tmp/doc.png", "Grade: A")

    assert table.entry_for_source("/tmp/doc.png")[5] == "Grade: A"


@pytest.mark.gui
def test_results_table_queued_accessors(qtbot) -> None:
    """
    Given  three queued rows with explicit source ids
    When   queued_source_ids() and queued_file_names() are queried
    Then   both return all rows in queue order
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(
        ["a.png", "b.png", "c.png"],
        source_ids=["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"],
    )

    assert table.queued_source_ids() == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
    assert table.queued_file_names() == ["a.png", "b.png", "c.png"]


@pytest.mark.gui
def test_results_table_selected_entry_empty_without_selection(qtbot) -> None:
    """Given a table with no current row / When selected_entry is queried / Then blanks are returned."""
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["a.png"])
    table.setCurrentCell(-1, -1)

    assert table.selected_entry() == ("", "", "", "", "", "")


@pytest.mark.gui
def test_results_table_source_id_for_row_falls_back_to_text(qtbot) -> None:
    """
    Given  a queued row created without an explicit source id
    When   source_id_for_row() is queried
    Then   it falls back to the displayed file name
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["plain.png"])

    assert table.source_id_for_row(0) == "plain.png"


@pytest.mark.gui
def test_results_table_failed_correction_shows_cross_with_tooltip(qtbot) -> None:
    """
    Given  a row whose correction stage is running (▶)
    When   add_result() is called with ok=False and an error message
    Then   the correction cell shows ✗ and its tooltip contains the error message
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["doc.pdf"])
    table.mark_processing("doc.pdf")
    table.mark_correcting("doc.pdf")

    error_msg = "Correction failed: model 'typo-model' not found"
    table.add_result(
        FileDoneEvent(
            source_id="doc.pdf",
            name="doc.pdf",
            ok=False,
            message=error_msg,
            ocr_s=1.5,
            correction_s=0.0,
        )
    )

    corr_cell = table.item(0, ResultsTable._COL_CORRECTION)
    assert corr_cell is not None
    assert corr_cell.text() == "✗"
    assert error_msg in corr_cell.toolTip()


@pytest.mark.gui
def test_results_table_failed_ocr_shows_cross_with_tooltip(qtbot) -> None:
    """
    Given  a row whose OCR stage is running (▶)
    When   add_result() is called with ok=False and an error message
    Then   the OCR cell shows ✗ and its tooltip contains the error message
    """
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(["doc.pdf"])
    table.mark_processing("doc.pdf")

    error_msg = "OCR failed: empty response"
    table.add_result(
        FileDoneEvent(
            source_id="doc.pdf",
            name="doc.pdf",
            ok=False,
            message=error_msg,
            ocr_s=0.5,
            correction_s=0.0,
        )
    )

    ocr_cell = table.item(0, ResultsTable._COL_OCR)
    assert ocr_cell is not None
    assert ocr_cell.text() == "✗"
    assert error_msg in ocr_cell.toolTip()
