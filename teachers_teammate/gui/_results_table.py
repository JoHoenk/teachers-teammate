"""Results table: per-file per-stage status and duration.

The table shows what the store says about each file (:class:`~..application.queue.QueueRow`)
and, on top of that, what the run is doing with it right now.  It keeps no texts of its own:
an action that needs one reads the document through the service.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..application.data_flow import sent_location
from ..application.queue import QueueRow, StageState
from ..domain.data_location import SentStatus
from ..domain.formatted_text import TextFormat
from ..domain.proofreading.review import CorrectionReview
from ..domain.run_summary import FileOutcome, FileStatus
from ..domain.stages import Stage
from ..texts import dialogs as dlg
from ..texts import main_window as win
from ..texts import review as txt
from ..texts.run import stopped_message
from . import _dialogs, _handles
from ._constants import (
    _COLOUR_CACHED,
    _COLOUR_CORRECTION_BAR,
    _COLOUR_ERROR,
    _COLOUR_EVAL_BAR,
    _COLOUR_OCR_BAR,
    _COLOUR_OK,
)
from ._export_menu import build_export_menu
from ._qt import present
from ._types import FileDoneEvent, StageStatus

_PENDING_SYMBOL = "-"  # hyphen-minus pending indicator
_RUNNING_SYMBOL = "▶"  # ▶
_DONE_SYMBOL = "✓"  # ✓
_FAIL_SYMBOL = "✗"  # ✗
_SKIPPED_TEXT = "skipped"  # a scan that is left out because text recognition is off
_STOPPED_TEXT = "stopped"  # the step a "Stop now" cut short
_PENDING_COLOR = "#888888"
_NEUTRAL_ROW_BG = (
    "#2b2b2b"  # skipped or stopped: neither the green of a success nor the red of a failure
)


def _warning_text(outcome: FileOutcome) -> str:
    """Return what the row says about *outcome*: why it failed, or the remarks about it."""
    if outcome.status is FileStatus.SAVED:
        return "; ".join(outcome.warnings)
    if outcome.status is FileStatus.STOPPED:
        return stopped_message(outcome.stopped_stage)
    return outcome.error


_STAGE_FONT = QFont()
_STAGE_FONT.setPointSize(13)
_STAGE_FONT.setBold(True)
_SKIPPED_FONT = QFont()
_SKIPPED_FONT.setPointSize(9)


@dataclass(frozen=True)
class LiveState:
    """What the run is doing with one file, drawn on top of what the store says.

    The store's answer is the row; this is the run's, and it is replaced whenever the file
    starts again or its result arrives.
    """

    stage: str = ""
    """The stage that is running, or ``""`` while none is."""
    finished: bool = False
    """The run finished this file: its results are the run's, not an older cache's."""
    failed: bool = False
    skipped: bool = False
    stopped: bool = False
    stopped_stage: str = ""
    warning: str = ""
    duration_s: float | None = None


class _HoverDelegate(QStyledItemDelegate):
    """Item delegate that highlights the entire hovered row."""

    def __init__(self, table: ResultsTable) -> None:
        super().__init__(table)
        self._table = table

    def paint(self, painter: Any, option: QStyleOptionViewItem, index: Any) -> None:
        if index.row() == self._table.hovered_row and not (
            option.state & QStyle.StateFlag.State_Selected
        ):
            painter.save()
            painter.fillRect(option.rect, QColor(255, 255, 255, 18))
            painter.restore()
        super().paint(painter, option, index)


class _ViewportLeaveFilter(QObject):
    """Resets the table's hovered_row when the mouse leaves the viewport."""

    def __init__(self, table: ResultsTable) -> None:
        super().__init__(table)
        self._table = table

    def eventFilter(self, _obj: QObject | None, event: QEvent | None) -> bool:
        if event is not None and event.type() == QEvent.Type.Leave:
            self._table.hovered_row = -1
            self._table.viewport().update()
        return False


class ResultsTable(QTableWidget):
    """Table of processed files: name, per-stage status, and duration.

    Columns: File | OCR | Correction | Evaluation | Checked | Duration | Open

    Each stage cell shows what the store says (``-`` or ``✓``), or what the run is doing with
    it (``▶``, ``✗``, ``stopped``, ``skipped``).
    """

    check_requested = Signal(str)  # source_id: open this document in the Check tab
    stage_run_requested = Signal(list, str)
    what_is_sent_requested = Signal(str)  # source_id
    export_requested = Signal(list, object)  # list[str] source_ids, ExportKind

    _COL_FILE = 0
    _COL_OCR = 1
    _COL_CORRECTION = 2
    _COL_EVALUATION = 3
    _COL_CHECKED = 4
    _COL_DURATION = 5
    _COL_PREVIEW = 6
    _ROLE_SOURCE = Qt.ItemDataRole.UserRole + 5
    _ROLE_NOTE = Qt.ItemDataRole.UserRole + 10  # str: the cell's own tooltip text

    _STAGE_ACTIVE_COLORS: ClassVar[dict[int, str]] = {
        _COL_OCR: _COLOUR_OCR_BAR,
        _COL_CORRECTION: _COLOUR_CORRECTION_BAR,
        _COL_EVALUATION: _COLOUR_EVAL_BAR,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 7, parent)
        #: What the store says about each file, keyed by source id.
        self._rows: dict[str, QueueRow] = {}
        #: What the run is doing with each file, on top of its row.
        self._live: dict[str, LiveState] = {}
        #: Files a run will leave out because text recognition is off, and why.
        self._skipped: dict[str, str] = {}
        #: Where the text of a stage goes, keyed by stage name; shown as a cell tooltip.
        self._stage_tooltips: dict[str, str] = {}
        self.setHorizontalHeaderLabels(
            [
                txt.FILE_COLUMN,
                txt.RECOGNITION_COLUMN,
                txt.PROOFREADING_COLUMN,
                txt.CONTENT_REVIEW_COLUMN,
                txt.CHECKED_COLUMN,
                txt.DURATION_COLUMN,
                txt.OPEN_COLUMN,
            ]
        )
        _header = present(self.horizontalHeader(), "header")
        _header.setStretchLastSection(False)
        _header.setDefaultSectionSize(90)
        self.setColumnWidth(self._COL_FILE, 200)
        self.setColumnWidth(self._COL_OCR, 80)
        self.setColumnWidth(self._COL_CORRECTION, 95)
        self.setColumnWidth(self._COL_EVALUATION, 100)
        self.setColumnWidth(self._COL_CHECKED, 95)
        self.setColumnWidth(self._COL_DURATION, 80)
        self.setColumnWidth(self._COL_PREVIEW, 120)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cellDoubleClicked.connect(self._on_cell_double_clicked)
        self.customContextMenuRequested.connect(self._on_context_menu_requested)
        _v_header = self.verticalHeader()
        if _v_header is not None:
            _v_header.setDefaultSectionSize(36)
        self._output_dir: Path | None = None
        self._row_by_source: dict[str, int] = {}
        self.hovered_row: int = -1
        self.viewport().setMouseTracking(True)
        self.setItemDelegate(_HoverDelegate(self))
        self.viewport().installEventFilter(_ViewportLeaveFilter(self))

    def mouseMoveEvent(self, event: Any) -> None:
        row = self.indexAt(event.pos()).row()
        if row != self.hovered_row:
            self.hovered_row = row
            self.viewport().update()
        super().mouseMoveEvent(event)

    def set_output_dir(self, path: Path) -> None:
        self._output_dir = path

    def get_output_dir(self) -> Path | None:
        return self._output_dir

    # ── The store's answer ────────────────────────────────────────────────

    def set_queue(self, rows: Sequence[QueueRow]) -> None:
        """Show *rows*: one row per file, as the store says it is.

        The files that were selected stay selected: a refresh is not the teacher's choice.
        """
        selected = set(self.selected_source_ids())
        self.setRowCount(0)
        self._row_by_source.clear()
        self._rows = {row.source_id: row for row in rows}
        # What the run did with a file survives a refresh: only the store's answer is new.
        self._live = {sid: live for sid, live in self._live.items() if sid in self._rows}
        self._skipped = {sid: why for sid, why in self._skipped.items() if sid in self._rows}
        for row in rows:
            index = self.rowCount()
            self.insertRow(index)
            self._row_by_source[row.source_id] = index
            file_item = QTableWidgetItem(row.name)
            file_item.setData(self._ROLE_SOURCE, row.source_id)
            self.setItem(index, self._COL_FILE, file_item)
            for col in (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION):
                cell = QTableWidgetItem(_PENDING_SYMBOL)
                cell.setForeground(QColor(_PENDING_COLOR))
                cell.setFont(_STAGE_FONT)
                self.setItem(index, col, cell)
            self.setItem(index, self._COL_DURATION, QTableWidgetItem(""))
            self.setItem(index, self._COL_CHECKED, QTableWidgetItem(""))
            self._add_preview_button(index, row.source_id)
            self._draw_row(row.source_id)
        self.resizeColumnToContents(self._COL_FILE)
        self._select_rows([self._row_by_source[sid] for sid in selected if sid in self._rows])

    def update_row(self, row: QueueRow) -> None:
        """Replace what the store says about one file, keeping what the run did with it."""
        if row.source_id not in self._row_by_source:
            self._ensure_row(row.source_id, row.name)
        self._rows[row.source_id] = row
        self._draw_row(row.source_id)

    def row_for(self, source_id: str) -> QueueRow | None:
        """Return what the store says about *source_id*, or ``None`` when it is not queued."""
        return self._rows.get(source_id)

    def has_text(self, source_id: str) -> bool:
        """Whether *source_id* has a transcription to show or to send."""
        row = self._rows.get(source_id)
        return bool(row is not None and row.has_text)

    def text_format_for_source(self, source_id: str) -> TextFormat:
        """Return the notation of *source_id*'s texts (plain when not recorded)."""
        row = self._rows.get(source_id)
        return row.text_format if row is not None else "plain"

    def review_for_source(self, source_id: str) -> CorrectionReview | None:
        """Return *source_id*'s proofreading review, or ``None`` when it has none."""
        row = self._rows.get(source_id)
        return row.review if row is not None else None

    # ── What the run is doing ─────────────────────────────────────────────

    def mark_stage_running(self, source_id: str, stage: str) -> None:
        """Show *stage* of *source_id* as running, on top of what the store says."""
        if source_id not in self._row_by_source:
            return
        self._live[source_id] = LiveState(stage=stage)
        self._draw_row(source_id)

    def mark_processing(self, source_id: str) -> None:
        """Mark the recognition stage as running for the given file."""
        self.mark_stage_running(source_id, Stage.OCR)

    def add_result(self, event: FileDoneEvent) -> None:
        """Show how the run ended for one file, on top of what the store says.

        The stored row is refreshed separately (:meth:`update_row`), so the cells come from
        the same place as on start-up; this only says what the run itself did.
        """
        outcome = event.outcome
        source_id = outcome.source_id
        if source_id not in self._row_by_source:
            self._ensure_row(source_id, outcome.name)
        if outcome.status is FileStatus.SKIPPED:
            self._skipped[source_id] = outcome.error
        self._live[source_id] = LiveState(
            stage=self._live.get(source_id, LiveState()).stage,
            finished=True,
            failed=outcome.status is FileStatus.FAILED,
            skipped=outcome.status is FileStatus.SKIPPED,
            stopped=outcome.status is FileStatus.STOPPED,
            stopped_stage=outcome.stopped_stage,
            warning=_warning_text(outcome),
            duration_s=None
            if outcome.status in (FileStatus.SKIPPED, FileStatus.STOPPED)
            else event.total_s,
        )
        self._draw_row(source_id)
        self.resizeColumnToContents(self._COL_FILE)
        self.resizeColumnToContents(self._COL_DURATION)

    def set_skipped_sources(self, source_ids: Collection[str], reason: str = "") -> None:
        """Show which queued files a run will leave out because text recognition is off.

        A row in *source_ids* that has not started shows "skipped" in the recognition cell
        (with *reason* as its tooltip); a row that was marked before and is no longer in
        *source_ids* goes back to what the store says.  Idempotent: the readiness check calls
        it after every answer.
        """
        wanted = dict.fromkeys(source_ids, reason)
        if wanted == self._skipped:
            return
        self._skipped = wanted
        for source_id in self._row_by_source:
            self._draw_row(source_id)

    def clear_results(self) -> None:
        self.setRowCount(0)
        self._row_by_source.clear()
        self._rows.clear()
        self._live.clear()
        self._skipped.clear()

    # ── Drawing one row ───────────────────────────────────────────────────

    def _draw_row(self, source_id: str) -> None:
        """Draw one row: the store's answer, with the run's state on top of it."""
        index = self._row_by_source.get(source_id)
        row = self._rows.get(source_id)
        if index is None or row is None:
            return
        live = self._live.get(source_id, LiveState())
        self._draw_stage_cells(index, row, live)
        self._draw_checked(index, row)
        self._draw_duration(index, live)
        self._draw_tint(index, live)

    def _draw_stage_cells(self, index: int, row: QueueRow, live: LiveState) -> None:
        """Draw the three stage cells of *index*."""
        for col, stage, state in (
            (self._COL_OCR, Stage.OCR, row.ocr),
            (self._COL_CORRECTION, Stage.CORRECTION, row.correction),
            (self._COL_EVALUATION, Stage.EVALUATION, row.evaluation),
        ):
            if stage == Stage.OCR and (live.skipped or row.source_id in self._skipped):
                self._set_skipped_cell(index, live.warning or self._skipped.get(row.source_id, ""))
                continue
            if live.stage == stage and not live.finished:
                self._set_stage_cell(index, col, _RUNNING_SYMBOL, self._STAGE_ACTIVE_COLORS[col])
                continue
            if live.failed and live.stage == stage:
                self._set_stage_cell(index, col, _FAIL_SYMBOL, _COLOUR_ERROR)
                if live.warning:
                    self._set_error_info_widget(index, col, live.warning)
                continue
            if live.stopped and self._is_cut_short(live, stage):
                self._set_stage_cell(index, col, _STOPPED_TEXT, _PENDING_COLOR)
                cell = self.item(index, col)
                if cell is not None:
                    cell.setFont(_SKIPPED_FONT)
                self._set_note(index, col, live.warning)
                continue
            if (
                live.warning
                and live.stage == stage
                and not (live.failed or live.stopped)
                and state in (StageState.DONE, StageState.KEPT)
            ):
                # The stage ran but fell back (e.g. the proofreading kept the original text).
                # Only while the store still has its result: a result that went stale since
                # shows as stale, not as the warning of the run that made it.
                self._set_warning_info_widget(index, col, live.warning)
                continue
            if state in (StageState.DONE, StageState.KEPT):
                colour = _COLOUR_OK if live.finished else _COLOUR_CACHED
                self._set_stage_cell(index, col, _DONE_SYMBOL, colour)
            else:
                self._set_stage_cell(index, col, _PENDING_SYMBOL, _PENDING_COLOR)
            if col == self._COL_CORRECTION:
                self._draw_correction_badges(index, row, live)

    @staticmethod
    def _is_cut_short(live: LiveState, stage: str) -> bool:
        """Whether *stage* is the step a stop cut short."""
        if live.stopped_stage:
            return live.stopped_stage == stage
        return live.stage == stage

    def _draw_correction_badges(self, index: int, row: QueueRow, live: LiveState) -> None:
        """Add the ``?n`` badge and the *kept* note to a done proofreading cell."""
        item = self.item(index, self._COL_CORRECTION)
        if item is None or self.cellWidget(index, self._COL_CORRECTION) is not None:
            # A cell widget carries the warning/error info; the notes would be hidden.
            return
        symbol = self._stage_symbol(index, self._COL_CORRECTION)
        if not symbol.startswith(_DONE_SYMBOL):
            return
        kept = row.correction is StageState.KEPT
        badges = [
            badge
            for badge in (txt.unclear_badge(row.unclear_count), txt.KEPT_NOTE if kept else "")
            if badge
        ]
        item.setText(" ".join([symbol, *badges]))
        notes = [
            note
            for note in (
                txt.filter_unclear_label(row.unclear_count) if row.unclear_count else "",
                txt.KEPT_TOOLTIP if kept else "",
            )
            if note
        ]
        self._set_note(index, self._COL_CORRECTION, "\n\n".join(notes))
        self._compose_tooltip(item, self._COL_CORRECTION, row, live)

    def _draw_checked(self, index: int, row: QueueRow) -> None:
        item = self.item(index, self._COL_CHECKED)
        if item is not None:
            item.setText(txt.checked_cell_label(row.checked))

    def _draw_duration(self, index: int, live: LiveState) -> None:
        item = self.item(index, self._COL_DURATION)
        if item is not None:
            item.setText("" if live.duration_s is None else f"{live.duration_s:.1f} s")

    def _draw_tint(self, index: int, live: LiveState) -> None:
        """Tint the row for at-a-glance overall status."""
        if live.skipped or live.stopped:
            brush = QBrush(QColor(_NEUTRAL_ROW_BG))
        elif live.finished:
            brush = QBrush(QColor("#1a3d2a" if not live.failed else "#3d1a1a"))
        else:
            brush = QBrush()
        self._tint_row(index, brush)

    def _set_stage_cell(self, row: int, col: int, symbol: str, color: str) -> None:
        self.removeCellWidget(row, col)
        item = self.item(row, col)
        if item is not None:
            item.setText(symbol)
            self._set_note(row, col, "")  # a new status drops the old ``?n`` badge's note
            item.setForeground(QColor(color))
            item.setFont(_STAGE_FONT)

    def _stage_symbol(self, row: int, col: int) -> str:
        """Return the status symbol of a stage cell, without a trailing ``?n`` badge."""
        item = self.item(row, col)
        return item.text().split(maxsplit=1)[0] if item is not None and item.text() else ""

    def _set_skipped_cell(self, row: int, reason: str) -> None:
        """Show "skipped" (grey, small) in the recognition cell of *row*."""
        self._set_stage_cell(row, self._COL_OCR, _SKIPPED_TEXT, _PENDING_COLOR)
        item = self.item(row, self._COL_OCR)
        if item is not None:
            item.setFont(_SKIPPED_FONT)
        self._set_note(row, self._COL_OCR, reason)

    def _tint_row(self, row: int, brush: QBrush) -> None:
        for col in range(self.columnCount()):
            cell = self.item(row, col)
            if cell is not None:
                cell.setBackground(brush)

    # ── Tooltips ──────────────────────────────────────────────────────────

    def set_stage_tooltips(self, tooltips: dict[str, str]) -> None:
        """Say where each stage's text goes; *tooltips* is keyed by stage name.

        The text of a cell keeps its own message (an unclear count, a warning or an error):
        the stage line is put in front of it, so neither replaces the other.
        """
        self._stage_tooltips = dict(tooltips)
        for source_id in self._row_by_source:
            self._draw_row(source_id)

    def _stage_key(self, col: int) -> str:
        """Return the pipeline stage a column stands for (empty for other columns)."""
        return {
            self._COL_CORRECTION: Stage.CORRECTION,
            self._COL_EVALUATION: Stage.EVALUATION,
        }.get(col, "")

    def _set_note(self, row: int, col: int, note: str) -> None:
        """Replace the cell's own tooltip text with *note*, keeping the stage line."""
        item = self.item(row, col)
        if item is None:
            return
        item.setData(self._ROLE_NOTE, note)
        source_id = self.source_id_for_row(row)
        stored = self._rows.get(source_id)
        live = self._live.get(source_id, LiveState())
        if stored is not None:
            self._compose_tooltip(item, col, stored, live)

    def _compose_tooltip(
        self, item: QTableWidgetItem, col: int, row: QueueRow, live: LiveState
    ) -> None:
        """Set the item tooltip from the stage line, how the result was sent, and the note."""
        stage = self._stage_key(col)
        status = row.sent.get(stage) if stage else None
        # A stage the run is redoing has no stored result to describe any more.
        sent = (
            txt.sent_lines(status, stage, sent_location(status.record))
            if isinstance(status, SentStatus) and not self._stage_is_live(live, stage)
            else ()
        )
        # A stored result says where it went; the setting for the next run only adds a second line.
        has_record = isinstance(status, SentStatus) and not self._stage_is_live(live, stage)
        parts = [
            part
            for part in (
                "" if has_record else self._stage_tooltips.get(stage, ""),
                "\n".join(sent),
                item.data(self._ROLE_NOTE),
            )
            if part
        ]
        item.setToolTip("\n\n".join(parts))

    @classmethod
    def _stage_is_live(cls, live: LiveState, stage: str) -> bool:
        """Whether the run is (re)making *stage* for this file."""
        if not stage:
            return False
        if live.stage == stage and not live.finished:
            return True
        if live.failed and live.stage == stage:
            return True
        return live.stopped and cls._is_cut_short(live, stage)

    # ── Cells and rows ────────────────────────────────────────────────────

    def _add_preview_button(self, row: int, source_id: str) -> None:
        btn = QPushButton(win.OPEN_BUTTON)
        btn.setObjectName(_handles.OPEN_DOCUMENT)
        btn.setProperty(_handles.SOURCE_ID_PROPERTY, source_id)
        f = btn.font()
        f.setPointSize(11)
        btn.setFont(f)
        btn.setStyleSheet(
            "QPushButton { border: 1px solid #555; border-radius: 4px; padding: 2px 6px; margin: 2px; }"
            "QPushButton:hover { border-color: #aaa; background: #2c3e50; }"
        )
        btn.clicked.connect(lambda _=False, sid=source_id: self._emit_check_for(sid))
        self.setCellWidget(row, self._COL_PREVIEW, btn)

    def _emit_check_for(self, source_id: str) -> None:
        """Select *source_id*'s row and ask for it to be opened in the Check tab."""
        row = self._row_for_source(source_id)
        if row is not None:
            self.selectRow(row)
            self.check_requested.emit(source_id)

    def _set_warning_info_widget(self, row: int, col: int, message: str) -> None:
        widget = QWidget()
        h = QHBoxLayout(widget)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(3)
        lbl = QLabel(win.WARNING_MARK)
        lbl.setStyleSheet("color: #e67e22; font-size: 13pt; font-weight: bold;")
        info_btn = QPushButton(win.INFO_MARK)
        info_btn.setFixedSize(18, 18)
        info_btn.setStyleSheet(
            "QPushButton { background: #e67e22; color: white; border-radius: 9px;"
            " font-size: 9pt; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #f39c12; }"
        )
        info_btn.setToolTip(message)
        info_btn.clicked.connect(
            lambda _=False, m=message: _dialogs.notify(None, dlg.stage_warning(m))
        )
        h.addWidget(lbl)
        h.addWidget(info_btn)
        h.addStretch()
        self.setCellWidget(row, col, widget)
        self._set_note(row, col, message)

    def _set_error_info_widget(self, row: int, col: int, message: str) -> None:
        widget = QWidget()
        h = QHBoxLayout(widget)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(3)
        lbl = QLabel(win.ERROR_MARK)
        lbl.setStyleSheet("color: #c0392b; font-size: 13pt; font-weight: bold;")
        info_btn = QPushButton(win.INFO_MARK)
        info_btn.setFixedSize(18, 18)
        info_btn.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; border-radius: 9px;"
            " font-size: 9pt; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #e74c3c; }"
        )
        info_btn.setToolTip(message)
        info_btn.clicked.connect(
            lambda _=False, m=message: _dialogs.notify(None, dlg.stage_error(m))
        )
        h.addWidget(lbl)
        h.addWidget(info_btn)
        h.addStretch()
        self.setCellWidget(row, col, widget)
        self._set_note(row, col, message)

    def _ensure_row(self, source_id: str, name: str) -> int:
        """Return *source_id*'s row, appending a fresh pending row when it has none."""
        row = self._row_for_source(source_id)
        if row is not None:
            return row
        index = self.rowCount()
        self.insertRow(index)
        self._row_by_source[source_id] = index
        self._rows[source_id] = QueueRow(source_id=source_id, name=name)
        file_item = QTableWidgetItem(name)
        file_item.setData(self._ROLE_SOURCE, source_id)
        self.setItem(index, self._COL_FILE, file_item)
        for col in (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION):
            cell = QTableWidgetItem(_PENDING_SYMBOL)
            cell.setForeground(QColor(_PENDING_COLOR))
            cell.setFont(_STAGE_FONT)
            self.setItem(index, col, cell)
        self.setItem(index, self._COL_DURATION, QTableWidgetItem(""))
        self.setItem(index, self._COL_CHECKED, QTableWidgetItem(""))
        self._add_preview_button(index, source_id)
        return index

    # ── Reading the table ─────────────────────────────────────────────────

    def _row_for_source(self, source_id: str) -> int | None:
        return self._row_by_source.get(source_id)

    def select_source(self, source_id: str) -> None:
        """Select the row of *source_id*, so the queue shows the document that is open."""
        row = self._row_for_source(source_id)
        if row is not None:
            self.selectRow(row)

    def get_stage_statuses_for_source(
        self, source_id: str
    ) -> tuple[StageStatus, StageStatus, StageStatus]:
        """Return (ocr, correction, evaluation) StageStatus for *source_id*."""
        _symbol_map: dict[str, StageStatus] = {
            _PENDING_SYMBOL: StageStatus.PENDING,
            _RUNNING_SYMBOL: StageStatus.RUNNING,
            _DONE_SYMBOL: StageStatus.DONE,
            _FAIL_SYMBOL: StageStatus.ERROR,
        }
        row = self._row_for_source(source_id)
        if row is None:
            return StageStatus.PENDING, StageStatus.PENDING, StageStatus.PENDING
        ocr = _symbol_map.get(self._stage_symbol(row, self._COL_OCR), StageStatus.PENDING)
        correction = _symbol_map.get(
            self._stage_symbol(row, self._COL_CORRECTION), StageStatus.PENDING
        )
        evaluation = _symbol_map.get(
            self._stage_symbol(row, self._COL_EVALUATION), StageStatus.PENDING
        )
        return ocr, correction, evaluation

    def checked_source_ids(self) -> list[str]:
        """Return the documents marked as checked, in queue order."""
        ids: list[str] = []
        for row in range(self.rowCount()):
            item = self.item(row, self._COL_CHECKED)
            if item is not None and item.text():
                source_id = self.source_id_for_row(row)
                if source_id:
                    ids.append(source_id)
        return ids

    def select_checked(self) -> None:
        """Select every row marked as checked, so Export writes just those."""
        rows = [
            row
            for row in range(self.rowCount())
            if (item := self.item(row, self._COL_CHECKED)) is not None and item.text()
        ]
        self._select_rows(rows)

    def _select_rows(self, rows: list[int]) -> None:
        """Select exactly *rows*; nothing happens for an empty list."""
        if not rows:
            return
        model = self.model()
        selection = QItemSelection()
        for row in rows:
            selection.select(model.index(row, 0), model.index(row, self.columnCount() - 1))
        self.selectionModel().select(
            selection,
            QItemSelectionModel.SelectionFlag.ClearAndSelect
            | QItemSelectionModel.SelectionFlag.Rows,
        )

    def source_id_for_row(self, row: int) -> str:
        item = self.item(row, self._COL_FILE)
        if item is None:
            return ""
        source_id = item.data(self._ROLE_SOURCE)
        if source_id:
            return str(source_id)
        return item.text()

    def _has_result(self, row: int) -> bool:
        """Whether *row*'s document has something to show: text that was read or a page."""
        stored = self._rows.get(self.source_id_for_row(row))
        return bool(stored is not None and stored.openable)

    def checkable_source_ids(self) -> list[str]:
        """Return the documents that can be opened in the Check tab, in queue order.

        A row without a result (waiting, failed, skipped, running) does not open: there would
        be nothing to check.
        """
        ids: list[str] = []
        for row in range(self.rowCount()):
            source_id = self.source_id_for_row(row)
            if source_id and self._has_result(row):
                ids.append(source_id)
        return ids

    def selected_source_ids(self) -> list[str]:
        """Return selected source IDs in row order."""
        rows = sorted(index.row() for index in self.selectionModel().selectedRows())
        ids: list[str] = []
        for row in rows:
            source_id = self.source_id_for_row(row)
            if source_id:
                ids.append(source_id)
        return ids

    def queued_source_ids(self) -> list[str]:
        """Return all queued source IDs in row order."""
        ids: list[str] = []
        for row in range(self.rowCount()):
            source_id = self.source_id_for_row(row)
            if source_id:
                ids.append(source_id)
        return ids

    def files(self, ids: list[str] | None = None) -> list[Path]:
        """Return the queued files in queue order, restricted to *ids* when given."""
        wanted = None if ids is None else set(ids)
        files: list[Path] = []
        for source_id in self.queued_source_ids():
            row = self._rows.get(source_id)
            if row is not None and (wanted is None or source_id in wanted):
                files.append(row.path)
        return files

    def recognised_files(self) -> set[Path]:
        """Return the queued files whose text is stored, so a run does not read them again."""
        return {
            row.path
            for source_id in self.queued_source_ids()
            if (row := self._rows.get(source_id)) is not None and row.ocr is StageState.DONE
        }

    def queued_file_names(self) -> list[str]:
        """Return all currently queued file names in row order."""
        names: list[str] = []
        for row in range(self.rowCount()):
            item = self.item(row, self._COL_FILE)
            if item is not None:
                names.append(item.text())
        return names

    # ── Opening a document ────────────────────────────────────────────────

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        source_id = self.source_id_for_row(row)
        if source_id and self._has_result(row):
            self.check_requested.emit(source_id)

    def keyPressEvent(self, event: Any) -> None:
        """Open the current row on Enter or Return (the table itself is not editable)."""
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            row = self.currentRow()
            source_id = self.source_id_for_row(row) if row >= 0 else ""
            if source_id and self._has_result(row):
                self.check_requested.emit(source_id)
                return
        super().keyPressEvent(event)

    def _on_context_menu_requested(self, pos: QPoint) -> None:
        rows = sorted(index.row() for index in self.selectionModel().selectedRows())
        if not rows:
            clicked = self.indexAt(pos)
            if not clicked.isValid():
                return
            self.selectRow(clicked.row())
            rows = [clicked.row()]

        selected_source_ids: list[str] = []
        for row in rows:
            source_id = self.source_id_for_row(row)
            if source_id:
                selected_source_ids.append(source_id)
        if not selected_source_ids:
            return

        menu = QMenu(self)
        check = menu.addAction(win.MENU_CHECK)
        # The first entry is the one a teacher reaches for; it is bold like a default action.
        _check_font = check.font()
        _check_font.setBold(True)
        check.setFont(_check_font)
        check.setEnabled(len(selected_source_ids) == 1 and self._has_result(rows[0]))
        select_checked = menu.addAction(txt.SELECT_CHECKED)
        select_checked.setToolTip(txt.SELECT_CHECKED_TOOLTIP)
        select_checked.setEnabled(bool(self.checked_source_ids()))
        menu.addSeparator()
        rerun_ocr = menu.addAction(win.MENU_RERUN_RECOGNITION)
        rerun_correction = menu.addAction(win.MENU_RERUN_PROOFREADING)
        rerun_evaluation = menu.addAction(win.MENU_RUN_REVIEW)
        menu.addSeparator()
        export_menu, export_kinds = build_export_menu(menu)
        menu.addMenu(export_menu)
        menu.addSeparator()
        what_is_sent = menu.addAction(win.MENU_WHAT_IS_SENT)

        # PySide6's stub lists a no-arg exec() overload first; exec(QPoint) is valid.
        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action is check:
            self.check_requested.emit(selected_source_ids[0])
        elif action is select_checked:
            self.select_checked()
        elif action is rerun_ocr:
            self.stage_run_requested.emit(selected_source_ids, Stage.OCR)
        elif action is rerun_correction:
            self.stage_run_requested.emit(selected_source_ids, Stage.CORRECTION)
        elif action is rerun_evaluation:
            self.stage_run_requested.emit(selected_source_ids, Stage.EVALUATION)
        elif action in export_kinds:
            self.export_requested.emit(selected_source_ids, export_kinds[action])
        elif action is what_is_sent:
            self.what_is_sent_requested.emit(selected_source_ids[0])
