"""GUI tests for ResultsTable using pytest-qt."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint
from PySide6.QtWidgets import QMenu

from teachers_teammate.application.queue import QueueRow, StageState
from teachers_teammate.domain.proofreading.review import CorrectionReview, UnclearNote
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._export_menu import ExportKind
from teachers_teammate.gui._results_table import ResultsTable
from teachers_teammate.gui._types import FileDoneEvent, StageStatus


def _row(source_id: str = "/tmp/a.png", name: str = "a.png", **extra: Any) -> QueueRow:
    """Return a queue row as the store would report it."""
    return QueueRow(source_id=source_id, name=name, **extra)


def _table(qtbot, *rows: QueueRow) -> ResultsTable:
    """Return a table showing *rows*."""
    table = ResultsTable()
    qtbot.addWidget(table)
    table.set_queue(list(rows))
    return table


def _done_row(source_id: str = "/tmp/preview.png", name: str = "preview.png") -> QueueRow:
    """Return a row whose document has a transcription, so it can be opened."""
    return _row(source_id, name, ocr=StageState.DONE, has_text=True)


@pytest.mark.gui
def test_a_warning_of_the_run_gives_way_when_the_stored_result_goes_stale(qtbot) -> None:
    """
    Given  a proofreading that ran with a warning, and was stored
    When   the store later says the proofreading is stale
    Then   the cell shows it as not done, not as the warning of the run that made it
    """
    source_id = "/tmp/a.png"
    table = _table(qtbot, _row(source_id, ocr=StageState.DONE, correction=StageState.DONE))
    table.mark_stage_running(source_id, "correction")
    table.add_result(
        FileDoneEvent(
            source_id=source_id,
            name="a.png",
            ok=True,
            message="kept the original",
            ocr_s=0.0,
            correction_s=1.0,
        )
    )
    assert table.cellWidget(0, ResultsTable._COL_CORRECTION) is not None

    table.update_row(_row(source_id, ocr=StageState.DONE, correction=StageState.STALE))

    assert table.cellWidget(0, ResultsTable._COL_CORRECTION) is None
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "-"


@pytest.mark.gui
def test_results_table_queue_and_processing_transition(qtbot) -> None:
    """
    Given  a ResultsTable with two queued files
    When   one file is marked as processing
    Then   that row status changes from pending to processing
    """
    table = _table(qtbot, _row("/tmp/a.pdf", "a.pdf"), _row("/tmp/b.png", "b.png"))

    table.mark_processing("/tmp/a.pdf")

    ocr_cell = table.item(0, ResultsTable._COL_OCR)
    assert ocr_cell is not None
    assert ocr_cell.text() == "▶"


@pytest.mark.gui
def test_a_finished_file_shows_its_duration_and_keeps_the_store_row(qtbot) -> None:
    """
    Given  a queued row whose recognition is stored
    When   the run's result for it arrives
    Then   the duration is shown and the row still says what the store said
    """
    table = _table(qtbot, _done_row())

    table.add_result(
        FileDoneEvent(
            source_id="/tmp/preview.png",
            name="preview.png",
            ok=True,
            message="",
            ocr_s=1.2,
            correction_s=0.3,
        )
    )

    duration = table.item(0, ResultsTable._COL_DURATION)
    assert duration is not None
    assert duration.text() == "1.5 s"
    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.has_text("/tmp/preview.png")


@pytest.mark.gui
def test_results_table_selected_source_ids_returns_multiple_rows(qtbot) -> None:
    """
    Given  a ResultsTable with queued rows and explicit source ids
    When   two rows are selected
    Then   selected_source_ids() returns both source ids in row order
    """
    table = _table(
        qtbot,
        _row("/tmp/a.png", "a.png"),
        _row("/tmp/b.png", "b.png"),
        _row("/tmp/c.png", "c.png"),
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
def test_results_table_asks_for_the_check_tab_on_double_click(qtbot) -> None:
    """
    Given  a row whose document has a result
    When   the row is double-clicked
    Then   check_requested carries its source id
    """
    table = _table(qtbot, _done_row())
    seen: list[str] = []
    table.check_requested.connect(seen.append)

    table.cellDoubleClicked.emit(0, ResultsTable._COL_FILE)

    assert seen == ["/tmp/preview.png"]


@pytest.mark.gui
def test_results_table_does_not_open_a_row_without_a_result(qtbot) -> None:
    """
    Given  a queued row whose document has neither text nor a page
    When   the row is double-clicked
    Then   nothing is asked for: there would be nothing to check
    """
    table = _table(qtbot, _row("/tmp/waiting.png", "waiting.png"))
    table.selectRow(0)
    seen: list[str] = []
    table.check_requested.connect(seen.append)

    table.cellDoubleClicked.emit(0, ResultsTable._COL_FILE)

    assert seen == []


@pytest.mark.gui
def test_results_table_opens_the_current_row_on_enter(qtbot) -> None:
    """
    Given  two rows, the second one with a result
    When   Enter is pressed with the second row current
    Then   check_requested carries the second row's source id
    """
    table = _table(
        qtbot,
        _row("/tmp/a.png", "a.png"),
        _done_row("/tmp/b.png", "b.png"),
    )
    table.selectRow(1)
    seen: list[str] = []
    table.check_requested.connect(seen.append)

    table.keyPressEvent(_enter_key())

    assert seen == ["/tmp/b.png"]


def _enter_key():
    """Return a key event for Enter."""
    from PySide6.QtCore import QEvent, Qt  # noqa: PLC0415
    from PySide6.QtGui import QKeyEvent  # noqa: PLC0415

    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier)


@pytest.mark.gui
def test_results_table_context_menu_opens_the_check_tab(qtbot, monkeypatch) -> None:
    """
    Given  a selected row with a result
    When   the context menu's *Check* entry is chosen
    Then   check_requested carries its source id
    """
    table = _table(qtbot, _done_row())
    table.selectRow(0)
    monkeypatch.setattr(
        "teachers_teammate.gui._results_table.QMenu",
        _menu_picking("Check"),
    )

    with qtbot.waitSignal(table.check_requested, timeout=1000) as sig:
        table._on_context_menu_requested(QPoint(1, 1))

    assert sig.args == ["/tmp/preview.png"]


def _menu_picking(label: str):
    """Return a QMenu subclass whose exec() picks the entry called *label*."""

    class _PickingMenu(QMenu):
        def exec(self, *args, **kwargs):  # Qt naming: overrides QMenu.exec
            for action in self.actions():
                if action.text() == label:
                    return action
            raise AssertionError(f"no {label} entry")

    return _PickingMenu


@pytest.mark.gui
def test_results_table_context_menu_emits_stage_run_requested(qtbot, monkeypatch) -> None:
    """
    Given  a selected row
    When   the context menu's *Re-run Proofreading* entry is chosen
    Then   stage_run_requested carries the selected source ids and the stage
    """
    table = _table(qtbot, _row("/tmp/a.png", "a.png"))
    table.selectRow(0)
    monkeypatch.setattr(
        "teachers_teammate.gui._results_table.QMenu",
        _menu_picking("Re-run Proofreading"),
    )

    with qtbot.waitSignal(table.stage_run_requested, timeout=1000) as sig:
        table._on_context_menu_requested(QPoint(1, 1))

    assert sig.args[0] == ["/tmp/a.png"]
    assert sig.args[1] == "correction"


@pytest.mark.gui
def test_the_stage_cells_show_what_the_store_says(qtbot) -> None:
    """
    Given  a row whose recognition is stored and whose proofreading is not
    When   the row is shown, and then the store reports the proofreading too
    Then   each stage cell shows a tick only for a stage the store has
    """
    table = _table(qtbot, _row("/tmp/page.png", "page.png", ocr=StageState.DONE))

    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "-"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "-"

    table.update_row(
        _row(
            "/tmp/page.png",
            "page.png",
            ocr=StageState.DONE,
            correction=StageState.DONE,
        )
    )

    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "✓"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "-"


@pytest.mark.gui
def test_a_running_proofreading_shows_the_reading_as_done(qtbot) -> None:
    """
    Given  a row whose recognition is stored
    When   the proofreading is marked as running
    Then   the recognition cell keeps its tick and the proofreading cell shows ▶
    """
    table = _table(qtbot, _row("/tmp/a.png", "a.png", ocr=StageState.DONE))

    table.mark_stage_running("/tmp/a.png", "correction")

    assert table.item(0, ResultsTable._COL_OCR).text() == "✓"
    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "▶"


@pytest.mark.gui
def test_a_running_review_shows_the_proofreading_as_done(qtbot) -> None:
    """
    Given  a row whose proofreading is stored
    When   the content review is marked as running
    Then   the proofreading cell keeps its tick and the review cell shows ▶
    """
    table = _table(
        qtbot,
        _row("/tmp/a.png", "a.png", ocr=StageState.DONE, correction=StageState.DONE),
    )

    table.mark_stage_running("/tmp/a.png", "evaluation")

    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "✓"
    assert table.item(0, ResultsTable._COL_EVALUATION).text() == "▶"


@pytest.mark.gui
def test_marking_an_unknown_source_adds_no_row(qtbot) -> None:
    """
    Given  a table with one queued row
    When   a stage is marked as running for a source that is not queued
    Then   nothing changes and no row is added
    """
    table = _table(qtbot, _row("/tmp/a.png", "a.png"))

    table.mark_processing("missing")
    table.mark_stage_running("missing", "correction")

    assert table.rowCount() == 1


@pytest.mark.gui
def test_the_row_hands_back_what_the_store_says(qtbot) -> None:
    """
    Given  a queued row with a transcription and a review
    When   the row is asked for
    Then   the same row comes back, and an unknown source has none
    """
    review = CorrectionReview(unclear=(UnclearNote(0, 3, "odd", "unclear", "rephrase"),))
    row = _row("/tmp/doc.png", "doc.png", ocr=StageState.DONE, has_text=True, review=review)
    table = _table(qtbot, row)

    assert table.row_for("/tmp/doc.png") is row
    assert table.row_for("/tmp/nope.png") is None
    assert table.review_for_source("/tmp/doc.png") is review
    assert table.review_for_source("/tmp/nope.png") is None


@pytest.mark.gui
def test_update_row_replaces_what_the_store_says(qtbot) -> None:
    """
    Given  a queued row without a transcription
    When   the store reports one
    Then   the row says so, and the table can open the document
    """
    table = _table(qtbot, _row("/tmp/doc.png", "doc.png"))

    assert not table.has_text("/tmp/doc.png")

    table.update_row(_row("/tmp/doc.png", "doc.png", ocr=StageState.DONE, has_text=True))

    assert table.has_text("/tmp/doc.png")
    assert table.checkable_source_ids() == ["/tmp/doc.png"]


@pytest.mark.gui
def test_results_table_queued_accessors(qtbot) -> None:
    """
    Given  three queued rows with explicit source ids
    When   queued_source_ids() and queued_file_names() are queried
    Then   both return all rows in queue order
    """
    table = _table(
        qtbot,
        _row("/tmp/a.png", "a.png"),
        _row("/tmp/b.png", "b.png"),
        _row("/tmp/c.png", "c.png"),
    )

    assert table.queued_source_ids() == ["/tmp/a.png", "/tmp/b.png", "/tmp/c.png"]
    assert table.queued_file_names() == ["a.png", "b.png", "c.png"]


@pytest.mark.gui
def test_selected_source_ids_is_empty_without_a_selection(qtbot) -> None:
    """
    Given  a table with one row and no selection
    When   selected_source_ids() is queried
    Then   it is empty
    """
    table = _table(qtbot, _row("/tmp/a.png", "a.png"))
    table.clearSelection()

    assert table.selected_source_ids() == []


@pytest.mark.gui
def test_source_id_for_row_names_the_queued_file(qtbot) -> None:
    """
    Given  a queued row
    When   source_id_for_row() is queried
    Then   it returns the source id the row was queued with
    """
    table = _table(qtbot, _row("/tmp/plain.png", "plain.png"))

    assert table.source_id_for_row(0) == "/tmp/plain.png"


@pytest.mark.gui
def test_results_table_failed_correction_shows_cross_with_tooltip(qtbot) -> None:
    """
    Given  a row whose proofreading is running (▶)
    When   add_result() is called with ok=False and an error message
    Then   the proofreading cell shows ✗ and its tooltip contains the error message
    """
    table = _table(qtbot, _row("/tmp/doc.pdf", "doc.pdf", ocr=StageState.DONE))
    table.mark_stage_running("/tmp/doc.pdf", "correction")

    error_msg = "Correction failed: model 'typo-model' not found"
    table.add_result(
        FileDoneEvent(
            source_id="/tmp/doc.pdf",
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
    Given  a row whose recognition is running (▶)
    When   add_result() is called with ok=False and an error message
    Then   the recognition cell shows ✗ and its tooltip contains the error message
    """
    table = _table(qtbot, _row("/tmp/doc.pdf", "doc.pdf"))
    table.mark_processing("/tmp/doc.pdf")

    error_msg = "OCR failed: empty response"
    table.add_result(
        FileDoneEvent(
            source_id="/tmp/doc.pdf",
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


@pytest.mark.gui
@pytest.mark.parametrize(
    ("entry", "kind"),
    [
        ("Word Document…", ExportKind.DOCX),
        ("Text File…", ExportKind.TEXT),
        ("Anonymized Text File…", ExportKind.TEXT_ANONYMIZED),
    ],
)
def test_results_table_export_submenu_emits_export_requested(
    qtbot, monkeypatch, entry: str, kind: ExportKind
) -> None:
    """
    Given  a selected queue row and a context menu with an Export submenu
    When   the user picks one of its entries
    Then   export_requested emits the selected source id and that export kind
    """
    table = _table(qtbot, _row("/tmp/a.png", "a.png"))
    table.selectRow(0)

    class _PickingMenu(QMenu):
        def exec(self, *args, **kwargs):  # Qt naming: overrides QMenu.exec
            for action in self.actions():
                submenu = action.menu()
                if submenu is not None and action.text() == "Export":
                    return next(a for a in submenu.actions() if a.text() == entry)
            raise AssertionError("no Export submenu")

    monkeypatch.setattr("teachers_teammate.gui._results_table.QMenu", _PickingMenu)

    with qtbot.waitSignal(table.export_requested, timeout=1000) as sig:
        table._on_context_menu_requested(QPoint(1, 1))

    assert sig.args == [["/tmp/a.png"], kind]


@pytest.mark.gui
def test_unclear_badge_keeps_the_proofreading_stage_done(qtbot) -> None:
    """
    Given  a file whose proofreading is stored and whose review has two unclear expressions
    When   the row is shown again with one expression, and then with none
    Then   the stage reads as done each time and the cell text never accumulates badges
    """
    done = _row("/tmp/a.pdf", "a.pdf", ocr=StageState.DONE, correction=StageState.DONE)
    table = _table(qtbot, dataclasses.replace(done, unclear_count=2))
    cell = table.item(0, ResultsTable._COL_CORRECTION)
    assert cell is not None
    assert "?2" in cell.text()
    assert table.get_stage_statuses_for_source("/tmp/a.pdf")[1] is StageStatus.DONE

    table.update_row(dataclasses.replace(done, unclear_count=1))
    assert cell.text().count("?") == 1
    assert table.get_stage_statuses_for_source("/tmp/a.pdf")[1] is StageStatus.DONE

    table.update_row(done)
    assert cell.text() == "✓"
    assert table.get_stage_statuses_for_source("/tmp/a.pdf")[1] is StageStatus.DONE


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_a_kept_proofreading_says_so_and_stays_done(qtbot) -> None:
    """
    Given  a file whose proofreading is stored
    When   the store reports it as kept over other settings, and then as done again
    Then   the cell keeps its tick, gains the note *kept* and says why in its tooltip
    """
    done = _row("/tmp/a.pdf", "a.pdf", ocr=StageState.DONE, correction=StageState.DONE)
    table = _table(qtbot, dataclasses.replace(done, correction=StageState.KEPT))
    cell = table.item(0, ResultsTable._COL_CORRECTION)
    assert cell is not None

    assert cell.text() == f"\u2713 {txt.KEPT_NOTE}"
    assert txt.KEPT_TOOLTIP in cell.toolTip()
    assert table.get_stage_statuses_for_source("/tmp/a.pdf")[1] is StageStatus.DONE

    table.update_row(done)

    assert cell.text() == "\u2713"
    assert txt.KEPT_TOOLTIP not in cell.toolTip()


@pytest.mark.gui
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_a_kept_proofreading_keeps_its_unclear_badge(qtbot) -> None:
    """
    Given  a file whose proofreading is kept and whose review has one unclear expression
    When   the row is shown
    Then   the cell carries both notes, and neither replaces the other
    """
    table = _table(
        qtbot,
        _row(
            "/tmp/a.pdf",
            "a.pdf",
            ocr=StageState.DONE,
            correction=StageState.KEPT,
            unclear_count=1,
        ),
    )

    cell = table.item(0, ResultsTable._COL_CORRECTION)
    assert cell is not None
    assert cell.text() == f"\u2713 ?1 {txt.KEPT_NOTE}"
    assert txt.KEPT_TOOLTIP in cell.toolTip()
    assert txt.filter_unclear_label(1) in cell.toolTip()


@pytest.mark.gui
def test_the_stored_review_is_handed_back_for_the_preview_and_the_export(qtbot) -> None:
    """
    Given  a row whose proofreading stored a review
    When   the review is asked for
    Then   the same review comes back, and a row without one gives None
    """
    review = CorrectionReview(
        unclear=(UnclearNote(0, 3, "odd", "unclear", "rephrase"),),
    )
    table = _table(
        qtbot,
        _row("/tmp/a.pdf", "a.pdf", ocr=StageState.DONE, correction=StageState.DONE, review=review),
        _row("/tmp/b.pdf", "b.pdf", ocr=StageState.DONE, correction=StageState.DONE),
    )

    assert table.review_for_source("/tmp/a.pdf") is review
    assert table.review_for_source("/tmp/b.pdf") is None


# ── skipped scans (text recognition switched off) ────────────────────────────


@pytest.mark.gui
def test_a_skipped_result_is_shown_as_skipped_not_as_a_failure(qtbot) -> None:
    """
    Given  a queued scan that the run left out because text recognition is off
    When   its result arrives with skipped=True
    Then   the recognition cell says "skipped" with the reason as tooltip, the other stages
           stay pending, the duration is empty and the row is neither green nor red
    """
    table = _table(qtbot, _row("/in/scan.png", "scan.png"))
    table.mark_processing("/in/scan.png")

    table.add_result(
        FileDoneEvent(
            source_id="/in/scan.png",
            name="scan.png",
            ok=False,
            message="Needs text recognition, which is switched off.",
            ocr_s=0.0,
            correction_s=0.0,
            skipped=True,
        )
    )

    ocr = table.item(0, ResultsTable._COL_OCR)
    assert ocr is not None
    assert ocr.text() == "skipped"
    assert "switched off" in ocr.toolTip()
    for col in (ResultsTable._COL_CORRECTION, ResultsTable._COL_EVALUATION):
        cell = table.item(0, col)
        assert cell is not None
        assert cell.text() == "-"
    duration = table.item(0, ResultsTable._COL_DURATION)
    assert duration is not None
    assert duration.text() == ""
    background = ocr.background().color().name()
    assert background not in ("#1a3d2a", "#3d1a1a")


@pytest.mark.gui
def test_queued_scans_can_be_marked_skipped_before_the_run_and_unmarked_again(qtbot) -> None:
    """
    Given  a queue of a scan and a text file
    When   the scan is marked as skipped, and then the marks are cleared
    Then   only the scan shows "skipped" in between, and it is pending again afterwards
    """
    table = _table(
        qtbot,
        _row("/in/scan.png", "scan.png"),
        _row("/in/notes.txt", "notes.txt"),
    )

    table.set_skipped_sources({"/in/scan.png"}, "Needs text recognition, which is switched off.")
    assert table.item(0, ResultsTable._COL_OCR).text() == "skipped"
    assert table.item(1, ResultsTable._COL_OCR).text() == "-"

    table.set_skipped_sources(set())
    assert table.item(0, ResultsTable._COL_OCR).text() == "-"


def _stopped_event(source: str, stage: str, **extra) -> FileDoneEvent:
    return FileDoneEvent(
        source_id=source,
        name="essay.png",
        ok=False,
        message=f"Stopped at {stage}.",
        ocr_s=1.0,
        correction_s=0.0,
        stopped=True,
        stopped_stage=stage,
        **extra,
    )


@pytest.mark.gui
def test_a_stop_at_the_review_is_shown_in_the_review_cell(qtbot) -> None:
    """
    Given  a file whose proofreading is stored while its review was cut short by "Stop now"
           (no cell is marked running for the review: nothing reports that step starting)
    When   the stopped result arrives
    Then   the review cell says "stopped" with the note, and the proofreading cell keeps its tick
    """
    table = _table(
        qtbot,
        _row("/e.png", "essay.png", ocr=StageState.DONE, correction=StageState.DONE),
    )
    table.mark_stage_running("/e.png", "correction")

    table.add_result(_stopped_event("/e.png", "evaluation"))

    assert [table.item(0, col).text() for col in (1, 2, 3)] == ["✓", "✓", "stopped"]
    assert "Stopped at evaluation." in table.item(0, 3).toolTip()


@pytest.mark.gui
def test_a_stop_at_the_review_does_not_blame_a_failed_proofreading(qtbot) -> None:
    """
    Given  proofreading that did not store a text (it failed), then "Stop now" at the review
    When   the stopped result arrives
    Then   the proofreading cell is not labelled "stopped" (it stays at the dash) and the
           review cell is
    """
    table = _table(qtbot, _row("/e.png", "essay.png", ocr=StageState.DONE))
    table.mark_stage_running("/e.png", "correction")

    table.add_result(_stopped_event("/e.png", "evaluation"))

    assert [table.item(0, col).text() for col in (1, 2, 3)] == ["✓", "-", "stopped"]


@pytest.mark.gui
def test_a_stopped_file_keeps_its_stored_review_badge(qtbot) -> None:
    """
    Given  a stored proofreading with unclear expressions, then "Stop now" at the review
    When   the stopped result arrives
    Then   the row keeps its review and the unclear badge
    """
    review = CorrectionReview(unclear=(UnclearNote(0, 3, "odd", "unclear", "rephrase"),))
    table = _table(
        qtbot,
        _row(
            "/e.png",
            "essay.png",
            ocr=StageState.DONE,
            correction=StageState.DONE,
            unclear_count=1,
            review=review,
        ),
    )
    table.mark_processing("/e.png")

    table.add_result(_stopped_event("/e.png", "evaluation"))

    assert "1" in table.item(0, 2).text()


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_the_queue_has_a_checked_column_after_the_content_review(qtbot) -> None:
    """
    Given  a results table
    When   its columns are read
    Then   *Checked* sits between the content review and the duration
    """
    table = ResultsTable()
    qtbot.addWidget(table)

    headers = [table.horizontalHeaderItem(col).text() for col in range(table.columnCount())]

    assert headers == [
        "File",
        "Recognition",
        "Proofreading",
        "Content review",
        "Checked",
        "Duration",
        "Open",
    ]


@pytest.mark.gui
@pytest.mark.parametrize(
    ("label", "shown"), [("checked", "Checked"), ("text", "Text checked"), ("", "")]
)
@pytest.mark.use_case("Mark_Document_Checked")
def test_the_checked_cell_says_what_was_checked(qtbot, label: str, shown: str) -> None:
    """
    Given  a queued row
    When   the store reports the corrections checked, the transcription alone, or neither
    Then   the cell says "Checked", "Text checked" or nothing
    """
    table = _table(qtbot, _row("/a.png", "a.png"))

    table.update_row(_row("/a.png", "a.png", checked=label))

    assert table.item(0, ResultsTable._COL_CHECKED).text() == shown


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_a_finished_file_brings_its_checked_mark(qtbot) -> None:
    """
    Given  a queued row whose document was checked before the run
    When   the run's result for it arrives and the row is read from the store again
    Then   the row shows the mark, and the run's own state does not take it away
    """
    table = _table(qtbot, _row("/a.png", "a.png", checked="checked"))

    table.add_result(
        FileDoneEvent(
            source_id="/a.png",
            name="a.png",
            ok=True,
            message="",
            ocr_s=1.0,
            correction_s=0.0,
        )
    )
    table.update_row(_row("/a.png", "a.png", ocr=StageState.DONE, checked="checked"))

    assert table.item(0, ResultsTable._COL_CHECKED).text() == "Checked"


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_select_checked_takes_both_kinds_and_leaves_the_rest(qtbot) -> None:
    """
    Given  four rows, one checked for its corrections, one for its text, two unchecked
    When   *Select checked* is chosen
    Then   the two checked rows are selected, so Export writes just those
    """
    table = _table(
        qtbot,
        _row("/a", "a.png", checked="checked"),
        _row("/b", "b.png", checked="text"),
        _row("/c", "c.png"),
        _row("/d", "d.png"),
    )

    table.select_checked()

    assert table.selected_source_ids() == ["/a", "/b"]
    assert table.checked_source_ids() == ["/a", "/b"]


@pytest.mark.gui
@pytest.mark.use_case("Mark_Document_Checked")
def test_a_failed_file_keeps_the_checked_mark_it_had(qtbot) -> None:
    """
    Given  a row marked as checked
    When   a run's result for it arrives as a failure, which says nothing about the mark
    Then   the row still shows *Checked*: a failure is not "unchecked"
    """
    table = _table(qtbot, _row("/a.png", "a.png", checked="checked"))

    table.add_result(
        FileDoneEvent(
            source_id="/a.png",
            name="a.png",
            ok=False,
            message="OCR failed",
            ocr_s=1.0,
            correction_s=0.0,
        )
    )

    assert table.item(0, ResultsTable._COL_CHECKED).text() == "Checked"


@pytest.mark.gui
def test_a_refresh_after_a_stage_run_keeps_the_row_it_showed(qtbot) -> None:
    """
    Given  a row whose proofreading a stage run just made
    When   the queue is read from the store again
    Then   the row says the same, and the run's own state is not lost
    """
    table = _table(qtbot, _row("/a.png", "a.png", ocr=StageState.DONE))
    table.mark_stage_running("/a.png", "correction")
    table.add_result(
        FileDoneEvent(
            source_id="/a.png",
            name="a.png",
            ok=True,
            message="",
            ocr_s=1.0,
            correction_s=2.0,
        )
    )
    done = _row("/a.png", "a.png", ocr=StageState.DONE, correction=StageState.DONE, has_text=True)

    table.set_queue([done])

    assert table.item(0, ResultsTable._COL_CORRECTION).text() == "✓"
    assert table.item(0, ResultsTable._COL_DURATION).text() == "3.0 s"
    assert table.has_text("/a.png")


@pytest.mark.gui
def test_a_row_filled_at_start_up_can_be_sent_and_opened(qtbot) -> None:
    """
    Given  a queue read from the store, with no run behind it
    When   the row is asked what it has
    Then   it has text to send and can be opened in the Check tab
    """
    table = _table(
        qtbot,
        _row("/a.png", "a.png", ocr=StageState.DONE, correction=StageState.DONE, has_text=True),
        _row("/b.png", "b.png", ocr=StageState.DONE, has_scan=True),
        _row("/c.png", "c.png"),
    )

    assert table.has_text("/a.png")
    assert not table.has_text("/b.png")
    assert table.checkable_source_ids() == ["/a.png", "/b.png"]
    assert table.text_format_for_source("/a.png") == "plain"


@pytest.mark.gui
def test_the_notation_of_the_texts_comes_from_the_row(qtbot) -> None:
    """
    Given  a row whose texts are Markdown
    When   the notation is asked for
    Then   it is Markdown, and an unknown source is plain
    """
    table = _table(
        qtbot,
        dataclasses.replace(_done_row("/a.png", "a.png"), text_format="markdown"),
    )

    assert table.text_format_for_source("/a.png") == "markdown"
    assert table.text_format_for_source("/nope.png") == "plain"


@pytest.mark.gui
def test_clear_results_empties_the_queue(qtbot) -> None:
    """
    Given  a table with two rows
    When   clear_results() is called
    Then   no row is left and no source is queued
    """
    table = _table(qtbot, _row("/a.png", "a.png"), _row("/b.png", "b.png"))

    table.clear_results()

    assert table.rowCount() == 0
    assert table.queued_source_ids() == []
    assert table.row_for("/a.png") is None


@pytest.mark.gui
def test_the_stage_tooltip_says_where_the_text_goes(qtbot) -> None:
    """
    Given  a row whose proofreading is stored
    When   the stage tooltips are set
    Then   the proofreading cell's tooltip carries the stage line
    """
    table = _table(
        qtbot,
        _row("/a.png", "a.png", ocr=StageState.DONE, correction=StageState.DONE),
    )

    table.set_stage_tooltips({"correction": "Goes to the model on this computer."})

    cell = table.item(0, ResultsTable._COL_CORRECTION)
    assert cell is not None
    assert "Goes to the model on this computer." in cell.toolTip()


@pytest.mark.gui
def test_a_row_without_a_state_has_no_page_to_open(qtbot, tmp_path: Path) -> None:
    """
    Given  a queued file the store knows nothing about
    When   the queue is read
    Then   the row is pending, has no text and cannot be opened
    """
    source = tmp_path / "new.png"
    table = _table(qtbot, QueueRow.empty(source))

    assert table.item(0, ResultsTable._COL_OCR).text() == "-"
    assert not table.has_text(str(source.resolve()))
    assert table.checkable_source_ids() == []
