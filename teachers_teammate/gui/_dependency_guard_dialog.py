"""Dialog shown when a pipeline stage is enabled but its dependencies are not met."""

from __future__ import annotations

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


class DependencyGuardDialog(QDialog):
    """Show unmet requirements for a pipeline stage and optionally open Downloads."""

    open_downloads = Signal()

    def __init__(
        self,
        stage: str,
        issues: list[RequirementIssue],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{stage.capitalize()} — Missing Requirements")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title_lbl = QLabel(f"<b>Cannot enable {stage.lower()} — requirements not met:</b>")
        title_lbl.setWordWrap(True)
        layout.addWidget(title_lbl)

        for issue in issues:
            row = QLabel(f"• {issue.message}")
            row.setWordWrap(True)
            layout.addWidget(row)

        has_fixable = any(i.fixable_via_downloads for i in issues)

        buttons = QDialogButtonBox()
        if has_fixable:
            _dl_btn = QPushButton("Open Downloads…")
            buttons.addButton(_dl_btn, QDialogButtonBox.ButtonRole.AcceptRole)
            _dl_btn.clicked.connect(self._on_open_downloads)
        buttons.addButton(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_open_downloads(self) -> None:
        self.open_downloads.emit()
        self.accept()
