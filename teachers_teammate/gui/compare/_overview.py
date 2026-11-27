"""The *Overview* tab: one column of figures per version, and what one text cannot show."""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...domain.proofreading.model_comparison import Comparison
from ...texts import compare as txt
from ._rows import overview_cells

__all__ = ["OverviewPanel"]


class OverviewPanel(QWidget):
    """How long each answer took, what it changed and what went wrong with it.

    The line above the figures says what one text can and cannot answer, so no column is read as
    a verdict.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._note = QLabel(txt.overview_note(), self)
        self._note.setWordWrap(True)
        self._note.setStyleSheet("color: #888;")
        self._table = QTableWidget(0, 0, self)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._note)
        layout.addWidget(self._table)

    def show_comparison(self, comparison: Comparison | None, labels: Mapping[str, str]) -> None:
        """Show one column per version of *comparison*."""
        version_names = (
            [labels.get(figures.key, figures.key) for figures in comparison.versions]
            if comparison
            else []
        )
        cells = overview_cells(comparison.versions) if comparison else ()
        self._table.clear()
        self._table.setRowCount(len(txt.OVERVIEW_ROWS))
        self._table.setColumnCount(len(version_names))
        self._table.setHorizontalHeaderLabels(version_names)
        self._table.setVerticalHeaderLabels(list(txt.OVERVIEW_ROWS))
        for row, values in enumerate(cells):
            for column, value in enumerate(values):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._table.resizeColumnsToContents()
