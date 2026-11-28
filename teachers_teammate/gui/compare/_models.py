"""The models the window offers, what each of them costs, and what each of them did."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...application.model_comparison import Candidate
from ...domain.data_location import DataLocation
from ...domain.model_fit import Suitability
from ...texts import compare as txt
from ...texts.models import compact_suitability_text

__all__ = ["ModelRow", "ModelsPanel"]

#: The columns of the list: the model, what the catalog says about it, its time and its state.
_NAME, _ROLE, _TIME, _STATE = range(4)


@dataclass(frozen=True)
class ModelRow:
    """One line of the list: a candidate and the cells the window shows of it."""

    candidate: Candidate
    name: QTableWidgetItem
    state: QTableWidgetItem


class ModelsPanel(QWidget):
    """The models to tick, each with its time and what it has done so far.

    A row is ticked to be asked; a row that answered already, or that the document's own
    proofreading answers, says so and is not ticked again.
    """

    #: The teacher picked another row: the footer's button follows the model they chose.
    row_chosen = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, ModelRow] = {}
        self._labels: dict[str, str] = {}
        self._answered: set[str] = set()
        self._own: set[str] = set()
        self._running: set[str] = set()
        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels([txt.MODELS, "", txt.TIME, ""])
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.itemSelectionChanged.connect(self.row_chosen)
        self._table.itemChanged.connect(lambda _item: self.row_chosen.emit())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)
        self._empty = QLabel(txt.NOTHING_STORED)
        self._empty.setStyleSheet("color: #888;")
        self._empty.setWordWrap(True)
        layout.addWidget(self._empty)

    def show_candidates(self, candidates: Sequence[Candidate], labels: Mapping[str, str]) -> None:
        """Fill the list with *candidates*, naming each by its model in *labels*."""
        self._rows.clear()
        self._labels = dict(labels)
        self._answered = {candidate.key for candidate in candidates if candidate.done}
        self._own = {candidate.key for candidate in candidates if candidate.own}
        self._running = set()
        self._table.setRowCount(0)
        for candidate in candidates:
            self._add(candidate)
        self._table.resizeColumnsToContents()
        self._empty.setVisible(not candidates)

    def _add(self, candidate: Candidate) -> None:
        """Add one candidate as a row, ticked unless it is done already."""
        row = self._table.rowCount()
        self._table.insertRow(row)
        name = QTableWidgetItem(candidate.model)
        name.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        name.setCheckState(Qt.CheckState.Unchecked if candidate.done else Qt.CheckState.Checked)
        self._table.setItem(row, _NAME, name)
        self._table.setItem(row, _ROLE, QTableWidgetItem(self._role(candidate)))
        self._table.setItem(row, _TIME, QTableWidgetItem(self._time(candidate)))
        state = QTableWidgetItem(self._state(candidate))
        self._table.setItem(row, _STATE, state)
        self._rows[candidate.key] = ModelRow(candidate=candidate, name=name, state=state)

    @staticmethod
    def _state(candidate: Candidate) -> str:
        """Return what the row says before anything is asked: whose answer it already holds."""
        if candidate.own:
            return txt.YOUR_PROOFREADING
        return txt.ANSWERED if candidate.done else txt.NOT_RUN

    @staticmethod
    def _role(candidate: Candidate) -> str:
        """Return what the catalog says about a local model, or that an online one is online."""
        if candidate.location.location is DataLocation.ONLINE:
            return txt.ONLINE
        if candidate.suitability is Suitability.UNKNOWN:
            return ""
        return compact_suitability_text(
            candidate.suitability, "general", very_slow=candidate.very_slow
        )

    @staticmethod
    def _time(candidate: Candidate) -> str:
        """Return the estimated time of this text with the model, or nothing without one."""
        if candidate.estimated is None:
            return ""
        return txt.estimate(candidate.estimated.low_s, candidate.estimated.high_s)

    def rows(self) -> tuple[ModelRow, ...]:
        """Return the rows the list shows, in their order."""
        return tuple(self._rows.values())

    def row_of(self, key: str) -> ModelRow | None:
        """Return the row of the version *key*, or ``None`` when it is not listed."""
        return self._rows.get(key)

    def label_of(self, key: str) -> str:
        """Return what the window calls the version *key*: its model, or the key itself."""
        return self._labels.get(key, key)

    def ticked(self) -> tuple[Candidate, ...]:
        """Return the ticked models that have not answered yet, in the order of the list."""
        return tuple(
            row.candidate
            for row in self._rows.values()
            if row.name.checkState() is Qt.CheckState.Checked and not row.candidate.done
        )

    def chosen(self) -> Candidate | None:
        """Return the model of the selected row, or ``None`` when no row is selected."""
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, _NAME)
        return next((found.candidate for found in self._rows.values() if found.name is item), None)

    def set_running(self, keys: Sequence[str]) -> None:
        """Say that the models of *keys* are about to be asked, one after another."""
        for key in keys:
            self.set_state(key, txt.RUNNING)
            self._running.add(key)

    def set_state(self, key: str, text: str) -> None:
        """Say what became of the model of *key*: running, done, stopped or why it failed."""
        row = self._rows.get(key)
        if row is not None:
            row.state.setText(text)
        self._running.discard(key)

    def settle(self) -> None:
        """Put the models that were not asked back to what they said before: the asking is over.

        A stop, or a failure that ends the whole job, leaves the models after it unasked; they must
        not say "running" any more.
        """
        for key in list(self._running):
            row = self._rows.get(key)
            if row is not None:
                row.state.setText(self._state(row.candidate))
        self._running.clear()

    def own(self, key: str) -> bool:
        """Return whether the answer of the model of *key* is the document's own proofreading."""
        return key in self._own

    def answered(self, key: str) -> bool:
        """Return whether the model of *key* has an answer stored for this text."""
        return key in self._answered

    def mark_done(self, key: str) -> None:
        """Show the model of *key* as answered, so running it again is a choice of its own."""
        row = self._rows.get(key)
        if row is not None:
            self._answered.add(key)
            row.name.setCheckState(Qt.CheckState.Unchecked)
