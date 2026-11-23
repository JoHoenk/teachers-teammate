"""Settings → Privacy: what the app stored for earlier runs, and deleting or expiring it.

The box says how much it is, offers the one button that deletes it, and the choice of how long
it may stay.  The confirmation names what stays — the Word files and the teacher's own work —
because "delete the stored results" sounds like it takes everything.

The retention is a setting like any other on this page: applied with OK, and acted on at the
next start (:class:`~.._settings_file.SettingsFile`).

Measuring and deleting touch every stored file, so both run off the GUI thread.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...application.stored_results import RunInProgressError, StoredSummary
from ...config import DEFAULTS
from ...texts import dialogs as dlg
from ...texts import privacy as priv
from .. import _dialogs
from .._background import LatestTask, run_in_background
from ._page import SettingsContext

_logger = logging.getLogger(__name__)

#: How many days a teacher may keep the results of earlier runs; the schema takes the same range.
KEEP_DAYS_MAX = 365

#: What the retention spin box shows before the teacher ticks the box.
KEEP_DAYS_PRESELECTED = 90


class StoredResultsBox(QGroupBox):
    """How much the app stored, deleting it, and how long it may stay."""

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(priv.STORED_GROUP, parent)
        self._stored = context.app_service.stored
        self._activity = context.app_service.run
        self._measuring = LatestTask(self)
        layout = QVBoxLayout(self)

        self._size = QLabel()
        self._size.setWordWrap(True)
        layout.addWidget(self._size)

        explain = QLabel(priv.STORED_EXPLAIN)
        explain.setWordWrap(True)
        explain.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(explain)

        delete_row = QHBoxLayout()
        self._delete_btn = QPushButton(priv.STORED_DELETE)
        self._delete_btn.clicked.connect(self._delete)
        delete_row.addWidget(self._delete_btn)
        delete_row.addStretch(1)
        layout.addLayout(delete_row)

        self._include_work = QCheckBox(priv.STORED_INCLUDE_WORK)
        self._include_work.setToolTip(priv.STORED_INCLUDE_WORK_TIP)
        layout.addWidget(self._include_work)

        keep_row = QHBoxLayout()
        self._keep = QCheckBox(priv.STORED_KEEP)
        self._days = QSpinBox()
        self._days.setRange(1, KEEP_DAYS_MAX)
        self._days.setValue(KEEP_DAYS_PRESELECTED)
        self._days.setSuffix(priv.STORED_KEEP_SUFFIX)
        self._keep.toggled.connect(self._days.setEnabled)
        keep_row.addWidget(self._keep)
        keep_row.addWidget(self._days)
        keep_row.addStretch(1)
        layout.addLayout(keep_row)

        self.refresh()

    # ── public API ────────────────────────────────────────────────────────────

    def load(self, values: Mapping[str, Any]) -> None:
        """Show the retention *values* hold; ``0`` means the results are kept."""
        days = int(values.get("results_keep_days", DEFAULTS["results_keep_days"]))
        if days > 0:
            self._days.setValue(min(days, KEEP_DAYS_MAX))
        self._keep.setChecked(days > 0)
        self._days.setEnabled(days > 0)

    def keep_days(self) -> int:
        """Return the retention as shown: ``0`` = keep the results of earlier runs."""
        return self._days.value() if self._keep.isChecked() else 0

    def delete_button(self) -> QPushButton:
        """Return the button that deletes the stored results."""
        return self._delete_btn

    def size_text(self) -> str:
        """Return the line that says how much is stored."""
        return self._size.text()

    def include_work_checkbox(self) -> QCheckBox:
        """Return the "also delete my corrections and marks" choice."""
        return self._include_work

    def keep_checkbox(self) -> QCheckBox:
        """Return the switch that turning on a retention is."""
        return self._keep

    def refresh(self) -> None:
        """Say how much is stored, and offer the button only when it can be pressed.

        The button follows the run at once; the size follows when it has been measured.
        """
        self._delete_btn.setEnabled(not self._activity.busy())
        self._measuring.submit(self._stored.summary, self._show_size)

    def _show_size(self, result: object, error: BaseException | None) -> None:
        """Say how much is stored, as measured; a measurement that failed leaves the line."""
        if error is not None or not isinstance(result, StoredSummary):
            _logger.warning("Measuring the stored results failed: %s", type(error).__name__)
            return
        self._size.setText(priv.stored_size(result.documents, result.bytes_taken))

    # ── internal ──────────────────────────────────────────────────────────────

    def _delete(self) -> None:
        """Delete what is stored, after the teacher confirmed it."""
        include_work = self._include_work.isChecked()
        if not _dialogs.ask(self, dlg.delete_stored_results(include_work=include_work)):
            return
        self._delete_btn.setEnabled(False)
        run_in_background(
            lambda: self._stored.delete(include_work=include_work), self._deleted, owner=self
        )

    def _deleted(self, _result: object, error: BaseException | None) -> None:
        """Say what became of the deletion, then show how much is left."""
        if isinstance(error, RunInProgressError):
            # A run started between the last refresh and this click; nothing was deleted.
            _dialogs.notify(self, dlg.busy_with_run())
        elif error is not None:
            _logger.warning("Deleting the stored results failed: %s", type(error).__name__)
        self.refresh()
