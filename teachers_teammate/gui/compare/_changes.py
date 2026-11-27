"""The *Changes* tab: one row per place a model changed, one column per model.

The row the teacher chooses shows what the models said about it, and one line per option with the
two verdicts they can give: *right* says the student was wrong here, *wrong* that the student's own
words are.  A verdict is on the option, so two models that wrote the same wording are judged at
once.
"""

from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application.model_comparison import CompareSource
from ...domain.proofreading.model_comparison import ChangeSite, Comparison, Option
from ...domain.proofreading.option_marks import Verdict
from ...texts import compare as txt
from .._handles import COMPARE_CHANGES_TABLE, COMPARE_MARK_RIGHT, COMPARE_MARK_WRONG
from ._rows import Sites, change_rows, kept

__all__ = ["ChangesPanel"]


class ChangesPanel(QWidget):
    """What each model wrote where any of them changed the student's words.

    A place one model fixed and another fixed differently is one row with two answers, so the
    teacher reads the disagreement rather than two separate lists.
    """

    mark_requested = Signal(object, object)
    """``(option, verdict)``: the teacher judged an option, with ``None`` taking a verdict back."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._comparison: Comparison | None = None
        self._labels: dict[str, str] = {}
        self._shown: tuple[ChangeSite, ...] = ()
        self._filter = QComboBox(self)
        for sites in Sites:
            self._filter.addItem(txt.FILTERS[sites.value], sites)
        self._filter.currentIndexChanged.connect(lambda _index: self._fill())
        self._table = QTableWidget(0, 0, self)
        self._table.setObjectName(COMPARE_CHANGES_TABLE)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._empty = QLabel()
        self._empty.setStyleSheet("color: #888;")
        self._empty.setWordWrap(True)
        self._table.itemSelectionChanged.connect(self._show_why)
        self._why = QLabel(txt.WHY_TOOLTIP)
        self._why.setWordWrap(True)
        self._why.setStyleSheet("color: #888;")
        self._verdicts = QVBoxLayout()
        top = QHBoxLayout()
        top.addWidget(self._filter)
        top.addStretch()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._table)
        layout.addWidget(self._why)
        layout.addLayout(self._verdicts)
        layout.addWidget(self._empty)

    def show_comparison(
        self, comparison: Comparison | None, source: CompareSource | None, labels: Mapping[str, str]
    ) -> None:
        """Show the places the models of *comparison* changed the text of *source*."""
        self._comparison = comparison
        self._labels = dict(labels)
        self._empty.setText(txt.NOT_READ if source is None else txt.NOTHING_STORED)
        self._fill()

    def _fill(self) -> None:
        """Draw the rows the chosen filter keeps, keeping the row the teacher had chosen."""
        chosen = self._table.currentRow()
        only = self._filter.currentData() or Sites.ALL
        rows = change_rows(self._comparison, only=only) if self._comparison else ()
        versions = self._comparison.versions if self._comparison else ()
        self._table.clear()
        self._table.setRowCount(0)
        self._table.setColumnCount(2 + len(versions))
        self._table.setHorizontalHeaderLabels(
            [
                txt.STUDENT_WROTE,
                *(self._labels.get(one.key, one.key) for one in versions),
                txt.AGREE,
            ]
        )
        for index, row in enumerate(rows):
            self._table.insertRow(index)
            self._table.setItem(index, 0, QTableWidgetItem(row.original))
            for column, cell in enumerate(row.cells, start=1):
                self._table.setItem(index, column, QTableWidgetItem(cell))
            self._table.setItem(index, len(versions) + 1, QTableWidgetItem(str(row.agreed)))
        sites = self._comparison.sites if self._comparison else ()
        self._shown = tuple(site for site in sites if kept(site, only))
        self._table.resizeColumnsToContents()
        self._empty.setVisible(not rows)
        if 0 <= chosen < len(rows):
            self._table.setCurrentCell(chosen, 0)
        self._show_why()

    def _show_why(self) -> None:
        """Say what the models explained about the chosen row: nothing until one is chosen."""
        row = self._table.currentRow()
        self._clear_verdicts()
        if row < 0 or row >= len(self._shown):
            self._why.setText(txt.WHY_TOOLTIP)
            return
        site = self._shown[row]
        self._why.setText(
            "\n".join(
                txt.explained_by(
                    self._labels.get(made.key, made.key), made.explanation, made.category
                )
                for option in site.options
                for made in option.made_by
            )
        )
        for option in site.options:
            self._verdicts.addWidget(self._verdict_row(option))

    def _clear_verdicts(self) -> None:
        """Take the verdict buttons of the row shown before away."""
        while self._verdicts.count():
            item = self._verdicts.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()

    def _verdict_row(self, option: Option) -> QWidget:
        """Return one option of the chosen row: its wording, and the verdicts it can be given."""
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(self._mark_button(option, Verdict.RIGHT))
        line.addWidget(self._mark_button(option, Verdict.WRONG))
        line.addWidget(QLabel(txt.option_wording(option.replacement)))
        line.addStretch()
        return row

    def _mark_button(self, option: Option, verdict: Verdict) -> QPushButton:
        """Return the button that gives *option* one verdict, or takes it back when clicked again."""
        right = verdict is Verdict.RIGHT
        button = QPushButton(txt.MARK_RIGHT if right else txt.MARK_WRONG)
        button.setObjectName(COMPARE_MARK_RIGHT if right else COMPARE_MARK_WRONG)
        button.setCheckable(True)
        holds = option.verdict is verdict
        button.setChecked(holds)
        if holds:
            button.setToolTip(txt.CLEAR_MARK_TIP)
        else:
            button.setToolTip(txt.MARK_RIGHT_TIP if right else txt.MARK_WRONG_TIP)
        button.clicked.connect(
            lambda checked, option=option, verdict=verdict: self.mark_requested.emit(
                option, verdict if checked else None
            )
        )
        return button
