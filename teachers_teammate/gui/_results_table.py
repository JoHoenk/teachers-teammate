"""Results table: per-file per-stage status and duration."""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
)

from ..domain.correction_review import CorrectionReview
from ..domain.formatted_text import TextFormat
from ..domain.stages import STAGE_ORDER
from . import _user_text as txt
from ._constants import (
    _COLOUR_CACHED,
    _COLOUR_CORRECTION_BAR,
    _COLOUR_ERROR,
    _COLOUR_EVAL_BAR,
    _COLOUR_OCR_BAR,
    _COLOUR_OK,
)
from ._export_menu import build_export_menu
from ._image_utils import load_pixmap
from ._types import FileDoneEvent, PreprocessInfo, StageStatus

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

_STAGE_FONT = QFont()
_STAGE_FONT.setPointSize(13)
_STAGE_FONT.setBold(True)
_SKIPPED_FONT = QFont()
_SKIPPED_FONT.setPointSize(9)


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

    Columns: File | OCR | Correction | Evaluation | Duration | Open

    Each stage cell transitions through: ``-`` → ``▶`` → ``✓`` / ``✗``.
    Cached stages start at ``✓`` (muted green) before the worker runs.
    """

    preview_requested = Signal(QPixmap, str, str, str)
    stage_run_requested = Signal(list, str)
    what_is_sent_requested = Signal(str)  # source_id
    export_requested = Signal(list, object)  # list[str] source_ids, ExportKind

    _COL_FILE = 0
    _COL_OCR = 1
    _COL_CORRECTION = 2
    _COL_EVALUATION = 3
    _COL_DURATION = 4
    _COL_PREVIEW = 5
    _ROLE_IMAGE = Qt.ItemDataRole.UserRole
    _ROLE_RAW = Qt.ItemDataRole.UserRole + 1
    _ROLE_CORR = Qt.ItemDataRole.UserRole + 2
    _ROLE_PIXMAP = Qt.ItemDataRole.UserRole + 3
    _ROLE_EVAL = Qt.ItemDataRole.UserRole + 4
    _ROLE_SOURCE = Qt.ItemDataRole.UserRole + 5
    _ROLE_PREPROCESS = Qt.ItemDataRole.UserRole + 6  # PreprocessInfo
    _ROLE_TEXT_FORMAT = Qt.ItemDataRole.UserRole + 7  # "plain" | "markdown"
    _ROLE_REVIEW = Qt.ItemDataRole.UserRole + 8  # CorrectionReview
    _ROLE_CORRECTION_WARNING = Qt.ItemDataRole.UserRole + 9  # str
    _ROLE_NOTE = Qt.ItemDataRole.UserRole + 10  # str: the cell's own tooltip text

    _STAGE_ACTIVE_COLORS: ClassVar[dict[int, str]] = {
        _COL_OCR: _COLOUR_OCR_BAR,
        _COL_CORRECTION: _COLOUR_CORRECTION_BAR,
        _COL_EVALUATION: _COLOUR_EVAL_BAR,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 6, parent)
        #: Where the text of a stage goes, keyed by stage name; shown as a cell tooltip.
        self._stage_tooltips: dict[str, str] = {}
        self.setHorizontalHeaderLabels(
            ["File", "OCR", "Correction", "Evaluation", "Duration", "Open"]
        )
        _header = self.horizontalHeader()
        assert _header is not None
        _header.setStretchLastSection(False)
        _header.setDefaultSectionSize(90)
        self.setColumnWidth(self._COL_FILE, 200)
        self.setColumnWidth(self._COL_OCR, 80)
        self.setColumnWidth(self._COL_CORRECTION, 95)
        self.setColumnWidth(self._COL_EVALUATION, 100)
        self.setColumnWidth(self._COL_DURATION, 80)
        self.setColumnWidth(self._COL_PREVIEW, 120)
        _header.setStretchLastSection(False)
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

    def _row_for_source(self, source_id: str) -> int | None:
        return self._row_by_source.get(source_id)

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

    def set_stage_tooltips(self, tooltips: dict[str, str]) -> None:
        """Say where each stage's text goes; *tooltips* is keyed by stage name.

        The text of a cell keeps its own message (an unclear count, a warning or an error):
        the stage line is put in front of it, so neither replaces the other.
        """
        self._stage_tooltips = dict(tooltips)
        self._refresh_cell_tooltips()

    def _stage_key(self, col: int) -> str:
        """Return the pipeline stage a column stands for (empty for other columns)."""
        return {self._COL_CORRECTION: "correction", self._COL_EVALUATION: "evaluation"}.get(col, "")

    def _set_note(self, row: int, col: int, note: str) -> None:
        """Replace the cell's own tooltip text with *note*, keeping the stage line."""
        item = self.item(row, col)
        if item is None:
            return
        item.setData(self._ROLE_NOTE, note)
        self._compose_tooltip(item, col)

    def _compose_tooltip(self, item: QTableWidgetItem, col: int) -> None:
        """Set the item tooltip from the stage line and the cell's own note."""
        parts = [
            part
            for part in (
                self._stage_tooltips.get(self._stage_key(col), ""),
                item.data(self._ROLE_NOTE),
            )
            if part
        ]
        item.setToolTip("\n\n".join(parts))

    def _refresh_cell_tooltips(self) -> None:
        """Recompose the tooltip of every proofreading and content-review cell."""
        for row in range(self.rowCount()):
            for col in (self._COL_CORRECTION, self._COL_EVALUATION):
                item = self.item(row, col)
                if item is not None:
                    self._compose_tooltip(item, col)

    def _add_preview_button(self, row: int, source_id: str) -> None:
        btn = QPushButton("Open Preview")
        f = btn.font()
        f.setPointSize(11)
        btn.setFont(f)
        btn.setStyleSheet(
            "QPushButton { border: 1px solid #555; border-radius: 4px; padding: 2px 6px; margin: 2px; }"
            "QPushButton:hover { border-color: #aaa; background: #2c3e50; }"
        )
        btn.clicked.connect(lambda _=False, sid=source_id: self._emit_preview_for(sid))
        self.setCellWidget(row, self._COL_PREVIEW, btn)

    def _emit_preview_for(self, source_id: str) -> None:
        row = self._row_for_source(source_id)
        if row is not None:
            self.selectRow(row)
            self._on_cell_double_clicked(row, self._COL_FILE)

    def _set_warning_info_widget(self, row: int, col: int, message: str) -> None:
        widget = QWidget()
        h = QHBoxLayout(widget)
        h.setContentsMargins(2, 0, 2, 0)
        h.setSpacing(3)
        lbl = QLabel("⚠")
        lbl.setStyleSheet("color: #e67e22; font-size: 13pt; font-weight: bold;")
        info_btn = QPushButton("ⓘ")
        info_btn.setFixedSize(18, 18)
        info_btn.setStyleSheet(
            "QPushButton { background: #e67e22; color: white; border-radius: 9px;"
            " font-size: 9pt; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #f39c12; }"
        )
        info_btn.setToolTip(message)
        info_btn.clicked.connect(
            lambda _=False, m=message: QMessageBox.warning(None, "Stage Warning", m)
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
        lbl = QLabel("✗")
        lbl.setStyleSheet("color: #c0392b; font-size: 13pt; font-weight: bold;")
        info_btn = QPushButton("ⓘ")
        info_btn.setFixedSize(18, 18)
        info_btn.setStyleSheet(
            "QPushButton { background: #c0392b; color: white; border-radius: 9px;"
            " font-size: 9pt; font-weight: bold; border: none; }"
            "QPushButton:hover { background: #e74c3c; }"
        )
        info_btn.setToolTip(message)
        info_btn.clicked.connect(
            lambda _=False, m=message: QMessageBox.warning(None, "Stage Error", m)
        )
        h.addWidget(lbl)
        h.addWidget(info_btn)
        h.addStretch()
        self.setCellWidget(row, col, widget)
        self._set_note(row, col, message)

    def set_queue(self, names: list[str], source_ids: list[str] | None = None) -> None:
        """Pre-populate a row per file in 'Pending' state before processing begins."""
        ids = source_ids if source_ids is not None else names
        if len(ids) != len(names):
            raise ValueError("source_ids must be the same length as names")
        self.setRowCount(0)
        self._row_by_source.clear()
        for source_id, name in zip(ids, names, strict=False):
            row = self.rowCount()
            self.insertRow(row)
            self.setItem(row, self._COL_FILE, QTableWidgetItem(name))
            for col in (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION):
                cell = QTableWidgetItem(_PENDING_SYMBOL)
                cell.setForeground(QColor(_PENDING_COLOR))
                cell.setFont(_STAGE_FONT)
                self.setItem(row, col, cell)
            self.setItem(row, self._COL_DURATION, QTableWidgetItem(""))
            file_item = self.item(row, self._COL_FILE)
            if file_item is not None:
                file_item.setData(self._ROLE_SOURCE, source_id)
            self._row_by_source[source_id] = row
            self._add_preview_button(row, source_id)
        self._refresh_cell_tooltips()
        self.resizeColumnToContents(self._COL_FILE)

    def set_skipped_sources(self, source_ids: Collection[str], reason: str = "") -> None:
        """Show which queued files a run will leave out because text recognition is off.

        A row in *source_ids* that has not started shows "skipped" in the recognition cell
        (with *reason* as its tooltip); a row that was marked before and is no longer in
        *source_ids* goes back to pending.  Idempotent: the readiness check calls it after
        every answer.
        """
        for source_id, row in self._row_by_source.items():
            symbol = self._stage_symbol(row, self._COL_OCR)
            if source_id in source_ids:
                if symbol == _PENDING_SYMBOL:
                    self._set_skipped_cell(row, reason)
            elif symbol == _SKIPPED_TEXT:
                self._set_stage_cell(row, self._COL_OCR, _PENDING_SYMBOL, _PENDING_COLOR)
                self._tint_row(row, QBrush())

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

    def mark_processing(self, source_id: str) -> None:
        """Mark the OCR stage as running for the given file."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        self._set_stage_cell(row, self._COL_OCR, _RUNNING_SYMBOL, _COLOUR_OCR_BAR)

    def mark_correcting(self, source_id: str) -> None:
        """Mark OCR as done and correction as running for the given file."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        if self._stage_symbol(row, self._COL_OCR) == _RUNNING_SYMBOL:
            self._set_stage_cell(row, self._COL_OCR, _DONE_SYMBOL, _COLOUR_OK)
        self._set_stage_cell(row, self._COL_CORRECTION, _RUNNING_SYMBOL, _COLOUR_CORRECTION_BAR)

    def mark_evaluating(self, source_id: str) -> None:
        """Mark correction as done and evaluation as running for the given file."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        if self._stage_symbol(row, self._COL_CORRECTION) == _RUNNING_SYMBOL:
            self._set_stage_cell(row, self._COL_CORRECTION, _DONE_SYMBOL, _COLOUR_OK)
        self._set_stage_cell(row, self._COL_EVALUATION, _RUNNING_SYMBOL, _COLOUR_EVAL_BAR)

    def mark_cached(
        self,
        source_id: str,
        preview_img: str,
        raw_txt: str,
        corr_txt: str,
        eval_txt: str,
        *,
        ocr_done: bool = False,
        correction_done: bool = False,
        evaluation_done: bool = False,
    ) -> None:
        """Set per-stage cells to reflect cached state before processing begins."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        for col, done in (
            (self._COL_OCR, ocr_done),
            (self._COL_CORRECTION, correction_done),
            (self._COL_EVALUATION, evaluation_done),
        ):
            if done:
                self._set_stage_cell(row, col, _DONE_SYMBOL, _COLOUR_CACHED)
            else:
                self._set_stage_cell(row, col, _PENDING_SYMBOL, _PENDING_COLOR)

        file_item = self.item(row, self._COL_FILE)
        if file_item is not None:
            file_item.setData(self._ROLE_IMAGE, preview_img)
            file_item.setData(self._ROLE_PIXMAP, load_pixmap(preview_img))
            file_item.setData(self._ROLE_RAW, raw_txt)
            file_item.setData(self._ROLE_CORR, corr_txt)
            file_item.setData(self._ROLE_EVAL, eval_txt)

    def set_correction_review(
        self,
        source_id: str,
        review: CorrectionReview | None,
        warning: str = "",
    ) -> None:
        """Record *source_id*'s proofreading review (and why it has none) and badge it."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if row is None or item is None:
            return
        item.setData(self._ROLE_REVIEW, review)
        item.setData(self._ROLE_CORRECTION_WARNING, warning)
        self._refresh_unclear_badge(row, review)

    def set_row_artifacts(
        self,
        source_id: str,
        preview_img: str,
        raw_txt: str,
        corr_txt: str,
        eval_txt: str,
        preprocess: PreprocessInfo | None = None,
    ) -> None:
        """Attach artifact paths to an existing queued/result row identified by *source_id*."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        file_item = self.item(row, self._COL_FILE)
        if file_item is None:
            return
        file_item.setData(self._ROLE_IMAGE, preview_img)
        file_item.setData(self._ROLE_PIXMAP, load_pixmap(preview_img))
        file_item.setData(self._ROLE_RAW, raw_txt)
        file_item.setData(self._ROLE_CORR, corr_txt)
        file_item.setData(self._ROLE_EVAL, eval_txt)
        # ``preprocess=None`` leaves the stored preprocessing info untouched (callers that
        # only refresh text, e.g. after a manual edit, don't know it).
        if preprocess is not None:
            self._store_preprocess_info(file_item, preprocess)

    def set_preprocessing(self, source_id: str, info: PreprocessInfo) -> None:
        """Record how *source_id*'s preview image was produced (original page + steps)."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is not None:
            self._store_preprocess_info(item, info)

    def _store_preprocess_info(self, item: QTableWidgetItem, info: PreprocessInfo) -> None:
        item.setData(self._ROLE_PREPROCESS, info)

    def preprocessing_for_source(self, source_id: str) -> PreprocessInfo:
        """Return how *source_id*'s preview image was produced (unknown if not recorded)."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is None:
            return PreprocessInfo()
        info = item.data(self._ROLE_PREPROCESS)
        return info if isinstance(info, PreprocessInfo) else PreprocessInfo()

    def set_text_format(self, source_id: str, text_format: str) -> None:
        """Record the notation (plain / markdown) of *source_id*'s stored texts."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is not None:
            item.setData(self._ROLE_TEXT_FORMAT, text_format)

    def text_format_for_source(self, source_id: str) -> TextFormat:
        """Return the notation of *source_id*'s texts (plain when not recorded)."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is None:
            return "plain"
        text_format = item.data(self._ROLE_TEXT_FORMAT)
        return "markdown" if text_format == "markdown" else "plain"

    def review_for_source(self, source_id: str) -> CorrectionReview | None:
        """Return *source_id*'s proofreading review, or ``None`` when it has none."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is None:
            return None
        review = item.data(self._ROLE_REVIEW)
        return review if isinstance(review, CorrectionReview) else None

    def correction_warning_for_source(self, source_id: str) -> str:
        """Return why *source_id* has no review (empty when it has one or none is known)."""
        row = self._row_for_source(source_id)
        item = self.item(row, self._COL_FILE) if row is not None else None
        if item is None:
            return ""
        return str(item.data(self._ROLE_CORRECTION_WARNING) or "")

    def _refresh_unclear_badge(self, row: int, review: CorrectionReview | None) -> None:
        """Append the ``?n`` unclear badge to the proofreading cell when there is a review."""
        item = self.item(row, self._COL_CORRECTION)
        if item is None or self.cellWidget(row, self._COL_CORRECTION) is not None:
            # A cell widget carries the warning/error info; the badge would be hidden.
            return
        symbol = self._stage_symbol(row, self._COL_CORRECTION)
        if not symbol.startswith(_DONE_SYMBOL):
            return
        count = review.unclear_count if review is not None else 0
        if count:
            item.setText(f"{symbol} {txt.unclear_badge(count)}")
            self._set_note(row, self._COL_CORRECTION, txt.filter_unclear_label(count))
        else:
            item.setText(symbol)
            self._set_note(row, self._COL_CORRECTION, "")

    def source_id_for_row(self, row: int) -> str:
        item = self.item(row, self._COL_FILE)
        if item is None:
            return ""
        source_id = item.data(self._ROLE_SOURCE)
        if source_id:
            return str(source_id)
        return item.text()

    def add_result(self, event: FileDoneEvent) -> None:
        """Update an existing queued row, or append a new row if not pre-queued.

        ``event`` carries the file name (row key), success flag, status message
        (logged, not shown), total duration, and the preview/raw/correction/
        evaluation artifact paths (each may be empty).
        """
        source_id = event.source_id
        name = event.name
        ok = event.ok
        raw_txt = event.raw_txt
        corr_txt = event.corr_txt
        eval_txt = event.eval_txt

        row = self._ensure_row(source_id, name)
        if event.skipped:
            # Left out on purpose: not a success (green) and not a failure (red).
            self._set_skipped_cell(row, event.message)
            for col in (self._COL_CORRECTION, self._COL_EVALUATION):
                self._set_stage_cell(row, col, _PENDING_SYMBOL, _PENDING_COLOR)
            self.setItem(row, self._COL_DURATION, QTableWidgetItem(""))
            self._tint_row(row, QBrush(QColor(_NEUTRAL_ROW_BG)))
            self.resizeColumnToContents(self._COL_FILE)
            return
        if event.stopped:
            # The run was stopped: not a failure (red) and not complete (green).  What is done
            # keeps its tick; the step that was cut short says so.
            self._show_stopped_cells(row, event, (raw_txt, corr_txt, eval_txt))
            self.setItem(row, self._COL_DURATION, QTableWidgetItem(""))
            self._tint_row(row, QBrush(QColor(_NEUTRAL_ROW_BG)))
            self._store_artifacts(row, event)
            self.resizeColumnToContents(self._COL_FILE)
            return
        self._finalize_stage_cells(row, event, (raw_txt, corr_txt, eval_txt))
        self.setItem(row, self._COL_DURATION, QTableWidgetItem(f"{event.total_s:.1f} s"))

        # Tint entire row for at-a-glance overall status
        row_bg = QColor("#1a3d2a" if ok else "#3d1a1a")
        for col in range(self.columnCount()):
            cell = self.item(row, col)
            if cell is not None:
                cell.setBackground(row_bg)

        self._store_artifacts(row, event)

        self.resizeColumnToContents(self._COL_FILE)
        self.resizeColumnToContents(self._COL_DURATION)

    def _store_artifacts(self, row: int, event: FileDoneEvent) -> None:
        """Attach the preview and texts of *event* to *row* (double-click opens them)."""
        file_item = self.item(row, self._COL_FILE)
        if file_item is not None:
            file_item.setData(self._ROLE_SOURCE, event.source_id)
            file_item.setData(self._ROLE_IMAGE, event.preview_img)
            file_item.setData(self._ROLE_PIXMAP, load_pixmap(event.preview_img))
            file_item.setData(self._ROLE_RAW, event.raw_txt)
            file_item.setData(self._ROLE_CORR, event.corr_txt)
            file_item.setData(self._ROLE_EVAL, event.eval_txt)
            file_item.setData(self._ROLE_REVIEW, event.correction_review)
            file_item.setData(self._ROLE_CORRECTION_WARNING, event.correction_warning)
        self._refresh_unclear_badge(row, event.correction_review)

    def _show_stopped_cells(
        self, row: int, event: FileDoneEvent, artifacts: tuple[str, str, str]
    ) -> None:
        """Tick the steps that have a result, mark the cut-short one "stopped", leave the rest."""
        # The result names the step it stopped at: no table cell is marked running for the review
        # (nothing reports that step starting), so the running mark alone cannot tell.  Without
        # a name (a stop between two steps) the cell that was marked running is the one.
        cut = event.stopped_stage
        for col, stage, artifact in zip(
            (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION),
            STAGE_ORDER,
            artifacts,
            strict=True,
        ):
            if artifact:
                self._set_stage_cell(row, col, _DONE_SYMBOL, _COLOUR_OK)
            elif stage == cut or (not cut and self._stage_symbol(row, col) == _RUNNING_SYMBOL):
                self._set_stage_cell(row, col, _STOPPED_TEXT, _PENDING_COLOR)
                cell = self.item(row, col)
                if cell is not None:
                    cell.setFont(_SKIPPED_FONT)
                self._set_note(row, col, event.message)
            else:
                self._set_stage_cell(row, col, _PENDING_SYMBOL, _PENDING_COLOR)

    def _ensure_row(self, source_id: str, name: str) -> int:
        """Return *source_id*'s row, appending a fresh pending row when it has none."""
        row = self._row_for_source(source_id)
        if row is not None:
            return row
        row = self.rowCount()
        self.insertRow(row)
        self._row_by_source[source_id] = row
        self.setItem(row, self._COL_FILE, QTableWidgetItem(name))
        for col in (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION):
            cell = QTableWidgetItem(_PENDING_SYMBOL)
            cell.setForeground(QColor(_PENDING_COLOR))
            cell.setFont(_STAGE_FONT)
            self.setItem(row, col, cell)
        self.setItem(row, self._COL_DURATION, QTableWidgetItem(""))
        self._add_preview_button(row, source_id)
        return row

    def _finalize_stage_cells(
        self, row: int, event: FileDoneEvent, artifacts: tuple[str, str, str]
    ) -> None:
        """Turn the running/pending stage cells into done, warning or error cells."""
        ok = event.ok
        message = event.message
        for col, artifact in zip(
            (self._COL_OCR, self._COL_CORRECTION, self._COL_EVALUATION), artifacts, strict=False
        ):
            sym = self._stage_symbol(row, col)
            if ok:
                if sym == _RUNNING_SYMBOL:
                    if message:
                        # Stage-level warning: overall ok but this stage had a non-fatal error.
                        # (The coordinator passes "" when all stages succeeded, or the warning
                        # text when a stage like correction fell back to the original text.)
                        self._set_warning_info_widget(row, col, message)
                    else:
                        self._set_stage_cell(row, col, _DONE_SYMBOL, _COLOUR_OK)
                elif sym == _PENDING_SYMBOL and artifact:
                    self._set_stage_cell(row, col, _DONE_SYMBOL, _COLOUR_OK)
            elif sym == _RUNNING_SYMBOL:
                self._set_stage_cell(row, col, _FAIL_SYMBOL, _COLOUR_ERROR)
                if message:
                    self._set_error_info_widget(row, col, message)

    def clear_results(self) -> None:
        self.setRowCount(0)
        self._row_by_source.clear()

    def _on_cell_double_clicked(self, row: int, _col: int) -> None:
        item = self.item(row, self._COL_FILE)
        if item is None:
            return
        pix = item.data(self._ROLE_PIXMAP)
        raw = item.data(self._ROLE_RAW) or ""
        corr = item.data(self._ROLE_CORR) or ""
        eval_txt = item.data(self._ROLE_EVAL) or ""
        self.preview_requested.emit(
            pix if isinstance(pix, QPixmap) else QPixmap(),
            raw,
            corr,
            eval_txt,
        )

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
        rerun_ocr = menu.addAction("Re-run Text Recognition")
        rerun_correction = menu.addAction("Re-run Proofreading")
        rerun_evaluation = menu.addAction("Run Content Review")
        menu.addSeparator()
        export_menu, export_kinds = build_export_menu(menu)
        menu.addMenu(export_menu)
        menu.addSeparator()
        what_is_sent = menu.addAction("What is sent for this file…")

        action = menu.exec(self.viewport().mapToGlobal(pos))  # ty: ignore[invalid-argument-type]  # PySide6 stub lists a no-arg exec() overload first; exec(QPoint) is valid
        if action is None:
            return
        if action is rerun_ocr:
            self.stage_run_requested.emit(selected_source_ids, "ocr")
        elif action is rerun_correction:
            self.stage_run_requested.emit(selected_source_ids, "correction")
        elif action is rerun_evaluation:
            self.stage_run_requested.emit(selected_source_ids, "evaluation")
        elif action in export_kinds:
            self.export_requested.emit(selected_source_ids, export_kinds[action])
        elif action is what_is_sent:
            self.what_is_sent_requested.emit(selected_source_ids[0])

    def entry_for_source(self, source_id: str) -> tuple[str, str, str, str, str, str]:
        """Return (source_id, name, preview_img, raw_txt, corr_txt, eval_txt) for *source_id*."""
        row = self._row_for_source(source_id)
        if row is None:
            return "", "", "", "", "", ""
        item = self.item(row, self._COL_FILE)
        if item is None:
            return "", "", "", "", "", ""
        name = item.text()
        preview_img = item.data(self._ROLE_IMAGE) or ""
        raw_txt = item.data(self._ROLE_RAW) or ""
        corr_txt = item.data(self._ROLE_CORR) or ""
        eval_txt = item.data(self._ROLE_EVAL) or ""
        return source_id, name, preview_img, raw_txt, corr_txt, eval_txt

    def selected_entry(self) -> tuple[str, str, str, str, str, str]:
        """Return selected row payload as (source_id, name, preview_img, raw_txt, corr_txt, eval_txt)."""
        row = self.currentRow()
        if row < 0:
            return "", "", "", "", "", ""
        item = self.item(row, self._COL_FILE)
        if item is None:
            return "", "", "", "", "", ""
        source_id = self.source_id_for_row(row)
        name = item.text()
        preview_img = item.data(self._ROLE_IMAGE) or ""
        raw_txt = item.data(self._ROLE_RAW) or ""
        corr_txt = item.data(self._ROLE_CORR) or ""
        eval_txt = item.data(self._ROLE_EVAL) or ""
        return source_id, name, preview_img, raw_txt, corr_txt, eval_txt

    def selected_paths(self) -> tuple[str, str, str, str, str]:
        """Return selected row paths as (name, preview_img, raw_txt, corr_txt, eval_txt)."""
        _source_id, name, preview_img, raw_txt, corr_txt, eval_txt = self.selected_entry()
        return name, preview_img, raw_txt, corr_txt, eval_txt

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

    def queued_file_names(self) -> list[str]:
        """Return all currently queued file names in row order."""
        names: list[str] = []
        for row in range(self.rowCount()):
            item = self.item(row, self._COL_FILE)
            if item is not None:
                names.append(item.text())
        return names

    def set_evaluation_text(self, source_id: str, eval_txt: str) -> None:
        """Attach evaluation text content to an existing row."""
        row = self._row_for_source(source_id)
        if row is None:
            return
        item = self.item(row, self._COL_FILE)
        if item is not None:
            item.setData(self._ROLE_EVAL, eval_txt)
