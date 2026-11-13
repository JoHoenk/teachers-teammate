"""Dialog shown when a step is turned on, or a run started, with something missing."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..application.service import RequirementIssue
from ._fix_targets import COMMAND_RETRY, FixAction


class DependencyGuardDialog(QDialog):
    """List what is missing, with one button per way to fix it.

    The buttons come from :func:`~._fix_targets.fix_for`, so the dialog offers what the
    readiness line offers, under the same names.  An action that is only a retry has no button
    here: the caller checks again by itself when the dialog closes.
    """

    #: A fix button was pressed; the caller opens the place it names.
    action_chosen = Signal(object)

    def __init__(
        self,
        title: str,
        issues: Sequence[RequirementIssue],
        actions: Sequence[FixAction] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        #: What is missing, as listed in the dialog.
        self.issues = tuple(issues)
        #: The fix buttons offered (without a plain retry), in order.
        self.fix_actions = tuple(action for action in actions if action.command != COMMAND_RETRY)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title_lbl = QLabel(f"<b>{title}:</b>")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        for issue in issues:
            row = QLabel(f"• {issue.message}")
            row.setWordWrap(True)
            layout.addWidget(row)

        buttons = QDialogButtonBox()
        for action in self.fix_actions:
            button = QPushButton(action.label)
            button.setToolTip(action.tooltip)
            buttons.addButton(button, QDialogButtonBox.ButtonRole.AcceptRole)
            button.clicked.connect(lambda _checked=False, a=action: self._on_action(a))
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_action(self, action: FixAction) -> None:
        self.action_chosen.emit(action)
        self.accept()
