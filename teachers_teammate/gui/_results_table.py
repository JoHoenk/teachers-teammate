"""Results table: per-file per-stage status and duration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
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

from ._constants import (
    _COLOUR_CACHED,
    _COLOUR_CORRECTION_BAR,
    _COLOUR_ERROR,
    _COLOUR_EVAL_BAR,
    _COLOUR_OCR_BAR,
    _COLOUR_OK,
)
from ._image_utils import load_pixmap
from ._types import FileDoneEvent, StageStatus

_PENDING_SYMBOL = "-"  # hyphen-minus pending indicator
_RUNNING_SYMBOL = "▶"  # ▶
_DONE_SYMBOL = "✓"  # ✓
_FAIL_SYMBOL = "✗"  # ✗
_PENDING_COLOR = "#888888"

_STAGE_FONT = QFont()
_STAGE_FONT.setPointSize(13)
_STAGE_FONT.setBold(True)


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
    privacy_preview_requested = Signal(str, str)  # source_id, raw_txt
    export_docx_requested = Signal(list)  # list[str] source_ids

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

    _STAGE_ACTIVE_COLORS: ClassVar[dict[int, str]] = {
        _COL_OCR: _COLOUR_OCR_BAR,
        _COL_CORRECTION: _COLOUR_CORRECTION_BAR,
        _COL_EVALUATION: _COLOUR_EVAL_BAR,
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(0, 6, parent)
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
            item.setForeground(QColor(color))
            item.setFont(_STAGE_FONT)

    def _stage_symbol(self, row: int, col: int) -> str:
        item = self.item(row, col)
        return item.text() if item is not None else ""

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
        item = self.item(row, col)
        if item is not None:
            item.setToolTip(message)

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
        item = self.item(row, col)
        if item is not None:
            item.setToolTip(message)

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
        self.resizeColumnToContents(self._COL_FILE)

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

    def set_row_artifacts(
        self,
        source_id: str,
        preview_img: str,
        raw_txt: str,
        corr_txt: str,
        eval_txt: str,
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
        message = event.message
        preview_img = event.preview_img
        raw_txt = event.raw_txt
        corr_txt = event.corr_txt
        eval_txt = event.eval_txt

        row = self._row_for_source(source_id)
        if row is None:
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

        # Finalize stage cells based on outcome and artifact presence
        for col, artifact in (
            (self._COL_OCR, raw_txt),
            (self._COL_CORRECTION, corr_txt),
            (self._COL_EVALUATION, eval_txt),
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

        self.setItem(row, self._COL_DURATION, QTableWidgetItem(f"{event.total_s:.1f} s"))

        # Tint entire row for at-a-glance overall status
        row_bg = QColor("#1a3d2a" if ok else "#3d1a1a")
        for col in range(self.columnCount()):
            cell = self.item(row, col)
            if cell is not None:
                cell.setBackground(row_bg)

        file_item = self.item(row, self._COL_FILE)
        if file_item is not None:
            file_item.setData(self._ROLE_SOURCE, source_id)
            file_item.setData(self._ROLE_IMAGE, preview_img)
            file_item.setData(self._ROLE_PIXMAP, load_pixmap(preview_img))
            file_item.setData(self._ROLE_RAW, raw_txt)
            file_item.setData(self._ROLE_CORR, corr_txt)
            file_item.setData(self._ROLE_EVAL, eval_txt)

        self.resizeColumnToContents(self._COL_FILE)
        self.resizeColumnToContents(self._COL_DURATION)

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
        export_docx = menu.addAction("Export as Word Document…")
        menu.addSeparator()
        privacy_preview = menu.addAction("Personal Information Preview…")

        action = menu.exec(self.viewport().mapToGlobal(pos))  # ty: ignore[invalid-argument-type]  # PySide6 stub lists a no-arg exec() overload first; exec(QPoint) is valid
        if action is None:
            return
        if action is rerun_ocr:
            self.stage_run_requested.emit(selected_source_ids, "ocr")
        elif action is rerun_correction:
            self.stage_run_requested.emit(selected_source_ids, "correction")
        elif action is rerun_evaluation:
            self.stage_run_requested.emit(selected_source_ids, "evaluation")
        elif action is export_docx:
            self.export_docx_requested.emit(selected_source_ids)
        elif action is privacy_preview:
            first_id = selected_source_ids[0]
            first_row = self._row_by_source.get(first_id)
            raw_txt = ""
            if first_row is not None:
                item = self.item(first_row, self._COL_FILE)
                if item is not None:
                    raw_txt = item.data(self._ROLE_RAW) or ""
            self.privacy_preview_requested.emit(first_id, raw_txt)

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
