"""List of stored runs for the current document, with A/B selection and deletion."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...infrastructure.benchmark.run_store import StoredRun

_RUN_ID_ROLE = Qt.ItemDataRole.UserRole


def _short_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return timestamp


class RunsList(QWidget):
    """Displays stored runs and emits the user's A/B selection and delete intents.

    Holds no business logic: it renders :class:`StoredRun` rows and emits the
    selected ``run_id`` values; the window resolves them via the service.
    """

    selection_changed = Signal(str, str)  # (run_id_a, run_id_b); "" when unset
    delete_requested = Signal(str)  # run_id
    clear_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._runs: list[StoredRun] = []
        self._a_id = ""
        self._b_id = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Stored runs"))

        self._list = QListWidget()
        layout.addWidget(self._list, stretch=1)

        buttons = QHBoxLayout()
        self._set_a_btn = QPushButton("Set as A")
        self._set_b_btn = QPushButton("Set as B")
        self._delete_btn = QPushButton("Delete")
        self._set_a_btn.clicked.connect(lambda: self._assign("a"))
        self._set_b_btn.clicked.connect(lambda: self._assign("b"))
        self._delete_btn.clicked.connect(self._on_delete)
        buttons.addWidget(self._set_a_btn)
        buttons.addWidget(self._set_b_btn)
        buttons.addWidget(self._delete_btn)
        layout.addLayout(buttons)

        self._clear_btn = QPushButton("Clear all runs for this document")
        self._clear_btn.clicked.connect(self.clear_all_requested.emit)
        layout.addWidget(self._clear_btn)

    def set_runs(self, runs: list[StoredRun]) -> None:
        """Populate the list; drop A/B selections that no longer exist."""
        self._runs = runs
        valid_ids = {r.run_id for r in runs}
        if self._a_id not in valid_ids:
            self._a_id = ""
        if self._b_id not in valid_ids:
            self._b_id = ""
        self._rebuild()
        self.selection_changed.emit(self._a_id, self._b_id)

    def _rebuild(self) -> None:
        self._list.clear()
        for run in self._runs:
            marker = ""
            if run.run_id == self._a_id:
                marker += "[A] "
            if run.run_id == self._b_id:
                marker += "[B] "
            words = len(run.raw_text.split())
            text = f"{marker}{_short_time(run.timestamp)} · {run.config_summary()} · {words} words"
            item = QListWidgetItem(text)
            item.setData(_RUN_ID_ROLE, run.run_id)
            self._list.addItem(item)

    def _selected_run_id(self) -> str:
        item = self._list.currentItem()
        return "" if item is None else str(item.data(_RUN_ID_ROLE))

    def _assign(self, slot: str) -> None:
        run_id = self._selected_run_id()
        if not run_id:
            return
        if slot == "a":
            self._a_id = run_id
        else:
            self._b_id = run_id
        self._rebuild()
        self.selection_changed.emit(self._a_id, self._b_id)

    def _on_delete(self) -> None:
        run_id = self._selected_run_id()
        if run_id:
            self.delete_requested.emit(run_id)
