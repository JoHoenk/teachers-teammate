"""The Export menu shared by the toolbar button and the results table's right-click menu."""

from __future__ import annotations

from enum import Enum

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QWidget

from ..domain.output_files import ANONYMIZED_TEXT_SUFFIX, DOCX_SUFFIX, TEXT_SUFFIX


class ExportKind(Enum):
    """What an export writes."""

    DOCX = "docx"
    TEXT = "text"
    TEXT_ANONYMIZED = "text_anonymized"

    @property
    def label(self) -> str:
        """Menu entry text."""
        return _LABELS[self]

    @property
    def file_suffix(self) -> str:
        """Ending appended to the document's name (``report`` -> ``report_anonymized.txt``)."""
        return _FILE_SUFFIXES[self]

    @property
    def file_filter(self) -> str:
        """File-dialog filter."""
        return "Word Document (*.docx)" if self is ExportKind.DOCX else "Text File (*.txt)"

    @property
    def is_text(self) -> bool:
        """Whether this writes a ``.txt`` file."""
        return self is not ExportKind.DOCX


_LABELS = {
    ExportKind.DOCX: "Word Document…",
    ExportKind.TEXT: "Text File…",
    ExportKind.TEXT_ANONYMIZED: "Anonymized Text File…",
}
_FILE_SUFFIXES = {
    ExportKind.DOCX: DOCX_SUFFIX,
    ExportKind.TEXT: TEXT_SUFFIX,
    ExportKind.TEXT_ANONYMIZED: ANONYMIZED_TEXT_SUFFIX,
}


def build_export_menu(
    parent: QWidget | QMenu | None = None,
) -> tuple[QMenu, dict[QAction, ExportKind]]:
    """Return an "Export" menu and the export kind each of its entries stands for."""
    menu = QMenu("Export", parent)
    kinds: dict[QAction, ExportKind] = {}
    for kind in ExportKind:
        action = menu.addAction(kind.label)
        assert action is not None
        kinds[action] = kind
    return menu, kinds
