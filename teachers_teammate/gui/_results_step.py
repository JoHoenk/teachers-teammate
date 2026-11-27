"""The Results step: where a run writes and which files it creates there.

One object because the answers are one state — the folder, the Word layout and the two text
file choices.  It never reads the panel's settings dict: :meth:`ResultsStep.values` is what the
panel stores and :meth:`ResultsStep.apply` is what it shows.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
from pathlib import Path
from typing import Any, NamedTuple

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QWidget,
)

from ..settings_schema import DEFAULTS
from ..texts import dialogs as dlg
from ..texts import main_window as txt
from . import _dialogs, _handles
from ._path_row import PathRow


class _TextKeys(NamedTuple):
    """The two text files a run can write: as written and anonymized."""

    as_written: bool
    """A text file as the teacher wrote it."""
    anonymized: bool
    """A text file with the names hidden."""


@dataclasses.dataclass(frozen=True)
class ResultsValues:
    """What the Results step holds, under the keys of the settings dict."""

    output: str = ""
    docx_enabled: bool = bool(DEFAULTS["docx_enabled"])
    docx_format: str = str(DEFAULTS["docx_format"])
    text_enabled: bool = bool(DEFAULTS["text_enabled"])
    text_anonymized_enabled: bool = bool(DEFAULTS["text_anonymized_enabled"])

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any]) -> ResultsValues:
        """Return the values *settings* holds (a settings dict, or the defaults)."""
        return cls(
            output=str(settings.get("output", "") or ""),
            docx_enabled=bool(settings.get("docx_enabled", DEFAULTS["docx_enabled"])),
            docx_format=str(settings.get("docx_format", DEFAULTS["docx_format"])),
            text_enabled=bool(settings.get("text_enabled", DEFAULTS["text_enabled"])),
            text_anonymized_enabled=bool(
                settings.get("text_anonymized_enabled", DEFAULTS["text_anonymized_enabled"])
            ),
        )

    def as_settings(self) -> dict[str, Any]:
        """Return the values as the settings dict keys they are, for ``dict.update``."""
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ResultsHooks:
    """What the Results step asks its owner for."""

    #: The teacher changed a choice: which files a run writes, or the folder.
    changed: Callable[[], None]
    #: The results folder changed: what a step's text is saved into is that step's answer.
    folder_changed: Callable[[], None]
    #: Where the folder picker starts: next to the documents.
    start_dir: Callable[[], str]
    #: The widget the folder picker opens over: the step is one section of it.
    dialog_parent: QWidget


class ResultsStep(QObject):
    """The widgets of the Results step and the answers they hold."""

    def __init__(self, hooks: ResultsHooks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hooks = hooks
        self.root = QWidget()
        form = QFormLayout(self.root)
        form.setContentsMargins(0, 2, 0, 4)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._output_row = PathRow(txt.RESULTS_LABEL, txt.RESULTS_PLACEHOLDER)
        self._output_dir = self._output_row.field()
        self._output_dir.setObjectName(_handles.OUTPUT_PATH)
        self._output_row.set_tooltip(txt.RESULTS_TOOLTIP)
        self._output_browse = self._output_row.add_button(
            txt.RESULTS_BUTTON, txt.RESULTS_BUTTON_TOOLTIP, self.choose
        )
        self._output_browse.setObjectName(_handles.OUTPUT_FOLDER_BUTTON)
        form.addRow(self._output_row)

        docx_row = QWidget()
        drl = QHBoxLayout(docx_row)
        drl.setContentsMargins(0, 0, 0, 0)
        self._docx_enabled = QCheckBox(txt.RESULTS_WORD_CHECKBOX)
        self._docx_enabled.setToolTip(txt.RESULTS_WORD_TOOLTIP)
        self._docx_format = QComboBox()
        self._docx_format.addItems(["table", "comments"])
        self._docx_format.setToolTip(txt.RESULTS_WORD_FORMAT_TOOLTIP)
        drl.addWidget(self._docx_enabled)
        drl.addWidget(self._docx_format)
        drl.addStretch(1)
        form.addRow(docx_row)

        text_row = QWidget()
        trl = QHBoxLayout(text_row)
        trl.setContentsMargins(0, 0, 0, 0)
        self._text_enabled = QCheckBox(txt.RESULTS_TEXT_CHECKBOX)
        self._text_enabled.setToolTip(txt.TEXT_FILE_TOOLTIP)
        self._text_mode = QComboBox()
        self._text_mode.addItems([txt.TEXT_MODE_AS_WRITTEN, txt.TEXT_MODE_ANONYMIZED])
        self._text_mode.setToolTip(txt.TEXT_MODE_TOOLTIP)
        trl.addWidget(self._text_enabled)
        trl.addWidget(self._text_mode)
        trl.addStretch(1)
        form.addRow(text_row)

        self._text_hint = QLabel(txt.RESULTS_TEXT_HINT)
        self._text_hint.setWordWrap(True)
        form.addRow(self._text_hint)

        for widget in self._widgets():
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(lambda _on: self._on_widget_changed())
            elif isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(lambda _text: self._on_widget_changed())
        self._update_state()

    def values(self) -> ResultsValues:
        """Return what the widgets show, under the keys of the settings dict."""
        keys = self._text_keys()
        return ResultsValues(
            output=self._output_dir.text().strip(),
            docx_enabled=self._docx_enabled.isChecked(),
            docx_format=self._docx_format.currentText(),
            text_enabled=keys.as_written,
            text_anonymized_enabled=keys.anonymized,
        )

    def apply(self, values: ResultsValues) -> None:
        """Show *values* without reporting them as a change."""
        widgets = self._widgets()
        for widget in widgets:
            widget.blockSignals(True)
        try:
            self._output_dir.setText(values.output)
            self._docx_enabled.setChecked(values.docx_enabled)
            _show(self._docx_format, values.docx_format)
            self._text_enabled.setChecked(values.text_enabled or values.text_anonymized_enabled)
            _show(self._text_mode, _mode_text(values))
        finally:
            for widget in widgets:
                widget.blockSignals(False)
        self._update_state()

    def problem(self) -> bool:
        """Return whether a file output is on but no folder to write it to is chosen."""
        wants_files = self._docx_enabled.isChecked() or self._text_enabled.isChecked()
        return wants_files and not self._output_dir.text().strip()

    def choose(self) -> None:
        """Ask for the results folder and show what was chosen."""
        start = self._output_dir.text().strip() or self._hooks.start_dir()
        chosen = _dialogs.pick_folder(self._hooks.dialog_parent, dlg.select_output_folder(start))
        if chosen is not None:
            self._output_dir.setText(str(chosen))
            self._update_state()
            self._hooks.folder_changed()

    def _widgets(self) -> tuple[QWidget, ...]:
        """Return the widgets a value shows in."""
        return (
            self._output_dir,
            self._docx_enabled,
            self._docx_format,
            self._text_enabled,
            self._text_mode,
        )

    def _update_state(self) -> None:
        """Enable the Word layout only for Word; warn when text files have no folder."""
        self._docx_format.setEnabled(self._docx_enabled.isChecked())
        self._text_mode.setEnabled(self._text_enabled.isChecked())
        missing_folder = self._text_enabled.isChecked() and not self._output_dir.text().strip()
        color = "#e67e22" if missing_folder else "#888"
        self._text_hint.setStyleSheet(f"color: {color}; font-size: 8pt;")

    def _on_widget_changed(self) -> None:
        """Show the new state and tell the owner the teacher changed a choice."""
        self._update_state()
        self._hooks.changed()

    def _text_keys(self) -> _TextKeys:
        """Return the two text-file keys the row stands for."""
        if not self._text_enabled.isChecked():
            return _TextKeys(False, False)
        anonymized = self._text_mode.currentText() == txt.TEXT_MODE_ANONYMIZED
        return _TextKeys(as_written=not anonymized, anonymized=anonymized)


def _mode_text(values: ResultsValues) -> str:
    """Return which of the two "Text file" choices *values* stands for.

    Settings with both keys on show *anonymized*, the one file the run writes (``Config``).
    """
    if values.text_anonymized_enabled:
        return txt.TEXT_MODE_ANONYMIZED
    return txt.TEXT_MODE_AS_WRITTEN


def _show(combo: QComboBox, value: str) -> None:
    """Select *value* in *combo* when it offers it; keep the current choice otherwise."""
    index = combo.findText(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def start_folder(input_dir: str) -> str:
    """Return where the results picker starts: next to the documents."""
    if input_dir:
        return str(Path(input_dir).parent)
    return str(Path.home())
