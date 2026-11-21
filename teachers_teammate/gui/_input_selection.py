"""The Documents step: the folder or the files a run reads.

One object because a folder and a list of files are one answer — choosing either clears the
other, and both are shown in the same row.  It holds the choice and shows it; the panel is told
when the teacher picks something.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from ..texts import dialogs as dlg
from . import _dialogs
from . import _user_text as txt
from ._path_row import PathRow


@dataclasses.dataclass(frozen=True)
class SelectionHooks:
    """What the Documents step asks its owner for."""

    #: The teacher chose another folder or other files: the queue follows the new choice.
    changed: Callable[[], None]
    #: The widget the pickers open over: the step is one section of it.
    dialog_parent: QWidget


class InputSelection(QObject):
    """The documents row and the choice behind it."""

    def __init__(self, hooks: SelectionHooks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hooks = hooks
        self._folder: Path | None = None
        self._files: list[Path] = []
        self.row = PathRow(txt.DOCUMENTS_LABEL, txt.DOCUMENTS_PLACEHOLDER)
        self.folder_button = self.row.add_button(
            txt.DOCUMENTS_FOLDER_BUTTON, txt.DOCUMENTS_FOLDER_TOOLTIP, self.choose_folder
        )
        self.files_button = self.row.add_button(
            txt.DOCUMENTS_FILES_BUTTON, txt.DOCUMENTS_FILES_TOOLTIP, self.choose_files
        )

    def folder(self) -> str:
        """Return the chosen folder, or the one the chosen files come from, or ``""``."""
        if self._folder is not None:
            return str(self._folder)
        if self._files:
            return str(self._files[0].parent)
        return ""

    def files(self) -> list[Path] | None:
        """Return the files chosen one by one, or ``None`` when a folder decides."""
        return list(self._files) if self._files else None

    def set_folder(self, path: Path | str | None) -> None:
        """Choose one folder; an empty *path* clears the choice. Shows it, tells nobody."""
        self._folder = Path(path) if path else None
        self._files = []
        self._show_choice()

    def set_files(self, paths: list[Path]) -> None:
        """Choose these files instead of a folder. Shows them, tells nobody."""
        self._files = list(paths)
        self._folder = None
        self._show_choice()

    def choose_folder(self) -> None:
        """Ask for the documents folder and show what was chosen."""
        start = str(self._folder or Path.home())
        chosen = _dialogs.pick_folder(self._hooks.dialog_parent, dlg.select_input_folder(start))
        if chosen is not None:
            self.set_folder(chosen)
            self._hooks.changed()

    def choose_files(self) -> None:
        """Ask for the documents themselves and show what was chosen."""
        start = str(self._folder or Path.home())
        paths = _dialogs.pick_files(self._hooks.dialog_parent, dlg.select_input_files(start))
        if paths:
            self.set_files(paths)
            self._hooks.changed()

    def _show_choice(self) -> None:
        """Show the folder, or how many files and which of them, in the row."""
        if self._files:
            count = len(self._files)
            parent = self._files[0].parent
            self.row.set_text(txt.files_from_folder(count, parent))
            names = [path.name for path in self._files[:20]]
            if count > len(names):
                names.append(txt.files_more(count - len(names)))
            self.row.set_tooltip("\n".join(names))
        elif self._folder is not None:
            self.row.set_text(str(self._folder))
            self.row.set_tooltip(str(self._folder))
        else:
            self.row.set_text("")
            self.row.set_tooltip("")
