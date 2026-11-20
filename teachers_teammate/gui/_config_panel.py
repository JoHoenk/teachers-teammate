"""Configuration panel — the workflow of one batch, as numbered steps.

What changes per batch lives here: the documents, the language, which steps run, their
instructions, and where the results go.  What is set up once (which AI models, scan
quality, privacy rules, this computer, add-ons) is edited in the Settings window and stored
in ``_settings_dict`` alongside.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import dataclasses
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..application.correction_options import migrate_saved_evaluation_prompt
from ..application.data_flow import ticked_steps
from ..application.service import RequirementIssue
from ..config import (
    DEFAULTS,
    SECONDARY_MODEL_NONE,
    Config,
    OcrConfig,
    normalize_notes,
    normalize_ocr_engine,
    normalize_ocr_model,
    normalize_preprocess_method,
    replaced_ocr_engine,
)
from ..domain.correction_policy import LEVELS, normalize_level, normalize_output_mode
from ..domain.correction_prompts import NOTES_LIMIT
from ..domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_EVALUATION,
    STAGE_OCR,
    DataFlow,
)
from ..domain.pdf_text_layer import normalize_pdf_text_layer
from ..texts import dialogs as dlg
from ..texts.settings import WHERE_ADDONS, WHERE_AI_MODELS
from . import _dialogs
from . import _user_text as txt
from ._background import LatestTask
from ._constants import _LANGUAGES
from ._data_flow_summary import (
    DataFlowSummary,
    UsesLine,
    names_line,
    same_model_line,
    uses_line,
    uses_tooltip,
)
from ._fix_targets import FixAction, fix_for
from ._path_row import PathRow
from ._prompt_editor import PromptEditor
from ._types import SettingsDict
from .settings._page import FOCUS_OCR, FOCUS_PROOFREADING, FOCUS_REVIEW, SettingsPage

#: The three choices of the "Text file" row: which text files a run writes.
TEXT_MODE_AS_WRITTEN = "as written"
TEXT_MODE_ANONYMIZED = "anonymized"
TEXT_MODE_BOTH = "both"

#: The step the teacher sees for each stage the requirement guard can untick.
_STAGE_STEP_TITLES = {
    "ocr": txt.RECOGNIZE_TEXT,
    "correction": txt.PROOFREAD,
    "evaluation": txt.REVIEW_CONTENT,
    "anonymizer": txt.REMOVE_PERSONAL_INFO,
}


class _PromptDialog(QDialog):
    """Modal dialog for the content review's instructions, built around :class:`PromptEditor`."""

    def __init__(
        self,
        title: str,
        prompt: str,
        parent: QWidget | None = None,
        *,
        builtin_text: str = "",
        fixed_text: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 420)
        vbox = QVBoxLayout(self)
        self._editor = PromptEditor(prompt, self, builtin_text=builtin_text, fixed_text=fixed_text)
        vbox.addWidget(self._editor, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

    def prompt(self) -> str:
        """Return the edited instructions; ``""`` means the built-in prompt is used."""
        return self._editor.prompt()


class _ProofreadingDialog(QDialog):
    """Modal dialog for the proofreading options: the notes, and what the AI is asked.

    The instructions themselves are fixed, so they are shown read-only, one heading per step;
    the teacher's own words go into the notes, which every step carries.  *prompts* answers
    the steps that run for a given *corrections only* choice, so the section follows the box.
    """

    def __init__(
        self,
        title: str,
        notes: str,
        prompts: Callable[[bool], tuple[tuple[str, str], ...]],
        parent: QWidget | None = None,
        *,
        explanations: tuple[str, str],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 480)
        self._prompts = prompts
        vbox = QVBoxLayout(self)
        vbox.addWidget(self._build_explanations_group(*explanations))
        vbox.addWidget(self._build_notes_group(notes), stretch=1)
        vbox.addWidget(self._build_prompts_section())
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)
        self._sync_prompts()

    def notes(self) -> str:
        """Return the notes as the dialog leaves them: stripped, at most ``NOTES_LIMIT``."""
        return self._notes.toPlainText().strip()[:NOTES_LIMIT].rstrip()

    def explanation_values(self) -> dict[str, object]:
        """Return ``explanation_language`` and ``correction_output``."""
        return {
            "explanation_language": str(self._explanation_language.currentData() or ""),
            "correction_output": "text" if self._corrections_only.isChecked() else "review",
        }

    def _build_explanations_group(self, language: str, output_mode: str) -> QGroupBox:
        """Build the explanation language and the "corrections only" choice."""
        group = QGroupBox(txt.EXPLANATIONS_GROUP)
        form = QFormLayout(group)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._explanation_language = QComboBox()
        self._explanation_language.addItem(txt.SAME_AS_TEXT, "")
        for name in _LANGUAGES:  # the item data is what explanation_values() reads back
            self._explanation_language.addItem(name, name)
        index = self._explanation_language.findData(language)
        self._explanation_language.setCurrentIndex(max(index, 0))
        self._explanation_language.setToolTip(txt.EXPLANATION_LANGUAGE_TOOLTIP)
        form.addRow("Explanation language:", self._explanation_language)

        self._corrections_only = QCheckBox(txt.CORRECTIONS_ONLY)
        self._corrections_only.setToolTip(txt.CORRECTIONS_ONLY_TOOLTIP)
        self._corrections_only.setChecked(output_mode == "text")
        self._corrections_only.toggled.connect(self._sync_prompts)
        form.addRow("", self._corrections_only)
        return group

    def _build_notes_group(self, notes: str) -> QGroupBox:
        """Build the notes box with its character counter."""
        group = QGroupBox(txt.NOTES_HEADING)
        vbox = QVBoxLayout(group)
        self._notes = QPlainTextEdit()
        self._notes.setPlaceholderText(txt.NOTES_PLACEHOLDER)
        self._notes.setToolTip(txt.NOTES_TOOLTIP)
        self._notes.setPlainText(notes)
        self._notes.setMaximumHeight(90)
        vbox.addWidget(self._notes)
        self._notes_counter = QLabel()
        self._notes_counter.setStyleSheet("color: #888; font-size: 8pt;")
        vbox.addWidget(self._notes_counter)
        self._notes.textChanged.connect(self._on_notes_changed)
        self._on_notes_changed()
        return group

    def _build_prompts_section(self) -> QWidget:
        """Build the collapsed, read-only list of the prompts the steps send."""
        box = QWidget()
        vbox = QVBoxLayout(box)
        vbox.setContentsMargins(0, 0, 0, 0)
        self._prompts_toggle = QPushButton(f"▸ {txt.PROMPTS_HEADING}")
        self._prompts_toggle.setFlat(True)
        self._prompts_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prompts_toggle.setStyleSheet("text-align: left; font-weight: bold;")
        self._prompts_toggle.setToolTip(txt.PROMPTS_TOOLTIP)
        self._prompts_toggle.clicked.connect(self._on_toggle_prompts)
        vbox.addWidget(self._prompts_toggle)
        self._prompts_view = QPlainTextEdit()
        self._prompts_view.setReadOnly(True)
        self._prompts_view.setStyleSheet("color: #aaa;")
        self._prompts_view.setMinimumHeight(160)
        self._prompts_view.setVisible(False)
        vbox.addWidget(self._prompts_view)
        return box

    def _on_notes_changed(self) -> None:
        """Keep the notes within the limit and the counter right."""
        text = self._notes.toPlainText()
        if len(text) > NOTES_LIMIT:
            position = self._notes.textCursor().position()
            self._notes.blockSignals(True)
            self._notes.setPlainText(text[:NOTES_LIMIT])
            self._notes.blockSignals(False)
            cursor = self._notes.textCursor()
            cursor.setPosition(min(position, NOTES_LIMIT))
            self._notes.setTextCursor(cursor)
            text = text[:NOTES_LIMIT]
        self._notes_counter.setText(txt.notes_counter(len(text)))

    def _on_toggle_prompts(self) -> None:
        expanded = not self._prompts_view.isVisible()
        self._prompts_view.setVisible(expanded)
        self._prompts_toggle.setText(f"{'▾' if expanded else '▸'} {txt.PROMPTS_HEADING}")

    def _sync_prompts(self) -> None:
        """Show the prompts of the steps that run for the current *corrections only* choice."""
        steps = self._prompts(self._corrections_only.isChecked())
        self._prompts_view.setPlainText(
            "\n\n".join(f"{title}\n\n{prompt}" for title, prompt in steps)
        )


class ConfigPanel(QScrollArea):
    """Left-hand panel: main pipeline configuration as a numbered workflow."""

    paths_changed = Signal()
    #: A settings dict named a text-recognition engine that is gone; carries a ReplacedEngine.
    ocr_engine_replaced = Signal(object)
    anonymizer_configure_requested = Signal(str, object)  # language, AnonymizerConfig
    #: Open a Settings page: the page, the focus value on it, entries to preselect.  Sent by the
    #: "change…" links under the steps and by the requirement guard.
    open_settings_requested = Signal(object, str, list)
    #: The user changed the Results step (folder, which files); the main window saves.
    results_changed = Signal()
    #: The user confirmed an Instructions dialog (notes, explanations).
    instructions_changed = Signal()
    #: The last rendered data flow, so cells elsewhere can say where a step's text goes.
    data_flow_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service: object | None = None  # ProcessingApplicationService, set via set_service()
        # One per stage: ticking a box must only make *its own* earlier check stale.
        self._guard_tasks: dict[str, LatestTask] = {}
        # True while load_from_dict applies saved state: stage requirement guards only
        # make sense for a user ticking a box, not for restoring settings at startup.
        self._loading = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)

        # Advanced settings stored here; edited via the Settings dialog.
        self._settings_dict: SettingsDict = {
            "ocr_engine": DEFAULTS["ocr_engine"],
            "ocr_model": DEFAULTS["ocr_model"],
            "ollama_url": DEFAULTS["ollama_url"],
            "hardware_profile": DEFAULTS["hardware_profile"],
            "ocr_timeout": DEFAULTS["ocr_timeout"],
            "recognition_enabled": DEFAULTS["recognition_enabled"],
            "preprocess_method": DEFAULTS["preprocess_method"],
            "pdf_render_dpi": DEFAULTS["pdf_render_dpi"],
            "pdf_text_layer": DEFAULTS["pdf_text_layer"],
            "correction_provider": DEFAULTS["correction_provider"],
            "correction_model": DEFAULTS["correction_model"],
            "explanation_language": DEFAULTS["explanation_language"],
            "correction_output": DEFAULTS["correction_output"],
            "correction_notes": DEFAULTS["correction_notes"],
            "evaluate_provider": DEFAULTS["evaluate_provider"],
            "evaluate_model": DEFAULTS["evaluate_model"],
            "output_dir": "",
            "docx_format": DEFAULTS["docx_format"],
            "docx_enabled": DEFAULTS["docx_enabled"],
            "text_enabled": DEFAULTS["text_enabled"],
            "text_anonymized_enabled": DEFAULTS["text_anonymized_enabled"],
            "evaluation_enabled": DEFAULTS["evaluation_enabled"],
            "anonymization_enabled": DEFAULTS["anonymization_enabled"],
            "debug": DEFAULTS["debug"],
            "keep_awake": DEFAULTS["keep_awake"],
            "check_for_updates": DEFAULTS["check_for_updates"],
        }
        self._settings_dict["anonymizer_primary_model"] = None
        # SECONDARY_MODEL_AUTO by default; SECONDARY_MODEL_NONE = the user chose "(none)".
        self._settings_dict["anonymizer_secondary_model"] = DEFAULTS["anonymizer_secondary_model"]
        self._settings_dict["anonymizer_patterns"] = None

        # Input selection state: either a folder or an explicit file list.
        self._selected_folder: Path | None = None
        self._selected_files: list[Path] = []

        # The teacher's notes for the AI, and the content review's prompt, stored internally
        # (not live QPlainTextEdits in the panel).  "" for the prompt means "use the built-in
        # one" — the default text is never copied here, so a code-side change keeps being
        # recognised and the language setting keeps swapping the preset.
        self._correction_notes_text: str = ""
        self._evaluation_prompt_text: str = ""

        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)

        self._data_flow: DataFlow | None = None
        self._data_flow_summary = DataFlowSummary()
        self._layout.addWidget(self._data_flow_summary)
        self._layout.addWidget(self._separator())
        self._build_step1_select_input()
        self._layout.addWidget(self._separator())
        self._build_step2_language()
        self._layout.addWidget(self._separator())
        self._build_step3_what_should_happen()
        self._layout.addWidget(self._separator())
        self._build_step4_results()
        self._layout.addStretch()

        self.setWidget(inner)

        self._language.currentTextChanged.connect(self._on_language_changed)
        self._on_language_changed(self._language.currentText())

        self._update_correction_visibility()
        self._update_anonymize_visibility()

    # ── step header helper ────────────────────────────────────────────────────────

    @staticmethod
    def _step_header(text: str, tooltip: str) -> QWidget:
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 4, 0, 2)
        lbl = QLabel(text.upper())
        lbl.setStyleSheet("QLabel { font-size: 11pt; font-weight: bold; color: #aaa; }")
        info = QLabel("i")
        info.setStyleSheet(
            "QLabel { color: white; background: #2980b9; border-radius: 9px; "
            "padding: 1px 5px; font-size: 11pt; font-weight: normal; }"
        )
        info.setToolTip(tooltip)
        h.addWidget(lbl)
        h.addStretch()
        h.addWidget(info)
        return row

    # ── section builders ──────────────────────────────────────────────────────────

    def _build_step1_select_input(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "1  Documents",
                "Choose the documents you want to process.\n"
                "You can pick individual files or a whole folder.\n"
                "Subfolders are included automatically.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._input_row = PathRow(txt.DOCUMENTS_LABEL, txt.DOCUMENTS_PLACEHOLDER)
        self._select_folder_btn = self._input_row.add_button(
            txt.DOCUMENTS_FOLDER_BUTTON, txt.DOCUMENTS_FOLDER_TOOLTIP, self._on_select_folder
        )
        self._select_files_btn = self._input_row.add_button(
            txt.DOCUMENTS_FILES_BUTTON, txt.DOCUMENTS_FILES_TOOLTIP, self._on_select_files
        )
        v.addWidget(self._input_row)

        self._layout.addWidget(inner)

    def _build_step2_language(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "2  Language",
                "Select the language the documents are written in.\n"
                "This helps the app recognise text accurately and\n"
                "proofread in the correct language.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(4)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._language = QComboBox()
        self._language.addItems(_LANGUAGES)
        self._language.setEditable(True)
        self._language.setCurrentText("English")
        self._language.activated.connect(self._language.hidePopup)
        row.addWidget(self._language, stretch=1)

        # The level describes the students' writing, so it sits next to the language.
        level_label = QLabel("Level:")
        level_label.setToolTip(txt.LEVEL_TOOLTIP)
        self._correction_level = QComboBox()
        self._correction_level.addItems(list(LEVELS))
        self._correction_level.setCurrentText(DEFAULTS["correction_level"])
        self._correction_level.setToolTip(txt.LEVEL_TOOLTIP)
        row.addWidget(level_label)
        row.addWidget(self._correction_level)
        v.addLayout(row)

        self._layout.addWidget(inner)

    def _build_step3_what_should_happen(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "3  What should happen",
                "The text is always read from the scans. The other steps are optional.\n"
                "Removing personal info runs on this computer, before anything is sent\n"
                f"(it needs name detection, see {WHERE_ADDONS}). Proofreading and\n"
                f"content review use the AI models set under {WHERE_AI_MODELS}.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._recognition_enabled, self._ocr_uses, self._recognition_note = (
            self._build_recognition_rows(v)
        )

        self._anonymization_enabled = QCheckBox(txt.REMOVE_PERSONAL_INFO)
        self._anonymization_enabled.setChecked(False)
        self._anonymization_enabled.toggled.connect(self._on_anonymization_changed)
        self._anon_configure_btn = QPushButton(txt.RULES_BUTTON)
        self._anon_configure_btn.setToolTip(
            "Set which types of personal information to hide and how."
        )
        self._anon_configure_btn.clicked.connect(self._on_configure_anonymizer)
        v.addLayout(self._checkbox_row(self._anonymization_enabled, self._anon_configure_btn))
        # Personal-info removal has no place to change here: its button is "Rules…".
        self._anonymization_uses = UsesLine(with_link=False)
        v.addWidget(self._anonymization_uses)

        self._correction_enabled = QCheckBox(txt.PROOFREAD)
        self._correction_enabled.setChecked(True)
        self._correction_enabled.setToolTip(
            "Use an AI to fix spelling and grammar in the extracted text.\n"
            f"Which AI: see {WHERE_AI_MODELS}."
        )
        self._correction_enabled.toggled.connect(self._update_correction_visibility)
        self._correction_prompt_btn = QPushButton(txt.OPTIONS_BUTTON)
        self._correction_prompt_btn.setToolTip(
            "Change the explanations (their language, or corrections only) and add notes\n"
            "for the AI. The instructions themselves are fixed; the dialog shows them."
        )
        self._correction_prompt_btn.clicked.connect(self._on_configure_correction_prompt)
        v.addLayout(self._checkbox_row(self._correction_enabled, self._correction_prompt_btn))
        self._correction_uses = self._uses_line(SettingsPage.AI_MODELS, FOCUS_PROOFREADING)
        v.addWidget(self._correction_uses)

        # Content review reads the proofread text, so its rows sit under Proofread.
        review_rows = QWidget()
        review_layout = QVBoxLayout(review_rows)
        review_layout.setContentsMargins(20, 0, 0, 0)
        review_layout.setSpacing(6)
        self._evaluation_enabled = QCheckBox(txt.REVIEW_CONTENT)
        self._evaluation_enabled.setChecked(False)
        self._evaluation_enabled.toggled.connect(self._on_evaluation_enabled_changed)
        self._evaluation_prompt_btn = QPushButton(txt.OPTIONS_BUTTON)
        self._evaluation_prompt_btn.setToolTip(
            "Change the instructions used to generate the content review report."
        )
        self._evaluation_prompt_btn.clicked.connect(self._on_configure_evaluation_prompt)
        review_layout.addLayout(
            self._checkbox_row(self._evaluation_enabled, self._evaluation_prompt_btn)
        )
        self._evaluation_uses = self._uses_line(SettingsPage.AI_MODELS, FOCUS_REVIEW)
        review_layout.addWidget(self._evaluation_uses)
        v.addWidget(review_rows)

        self._layout.addWidget(inner)

    def _build_recognition_rows(self, v: QVBoxLayout) -> tuple[QCheckBox, UsesLine, QLabel]:
        """Add "Recognize text", its "uses" line and the queue note to *v*; return the widgets."""
        checkbox = QCheckBox(txt.RECOGNIZE_TEXT)
        checkbox.setChecked(DEFAULTS["recognition_enabled"])
        checkbox.setToolTip(txt.RECOGNIZE_TEXT_TOOLTIP)
        checkbox.toggled.connect(self._on_recognition_changed)
        v.addWidget(checkbox)
        uses = self._uses_line(SettingsPage.AI_MODELS, FOCUS_OCR)
        v.addWidget(uses)
        # What the switch means for the queue: scans that will be skipped, or "not needed".
        note = QLabel()
        note.setWordWrap(True)
        note.setVisible(False)
        v.addWidget(note)
        return checkbox, uses, note

    def _uses_line(self, page: SettingsPage, focus: str) -> UsesLine:
        """Return a "uses" line whose "change…" opens *page* on *focus*."""
        line = UsesLine()
        line.change_requested.connect(lambda: self.open_settings_requested.emit(page, focus, []))
        return line

    @staticmethod
    def _checkbox_row(checkbox: QCheckBox, button: QPushButton) -> QHBoxLayout:
        """Return a row with *checkbox* on the left and its *button* right-aligned."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(checkbox, stretch=1)
        row.addWidget(button)
        return row

    def _build_step4_results(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "4  Results",
                "Where the results are saved, and which files a run creates there.",
            )
        )
        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(0, 2, 0, 4)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        self._output_row = PathRow(txt.RESULTS_LABEL, txt.RESULTS_PLACEHOLDER)
        self._output_dir = self._output_row.field()
        self._output_row.set_tooltip(txt.RESULTS_TOOLTIP)
        self._output_browse = self._output_row.add_button(
            txt.RESULTS_BUTTON, txt.RESULTS_BUTTON_TOOLTIP, self._on_browse_output_dir
        )
        form.addRow(self._output_row)

        docx_row = QWidget()
        drl = QHBoxLayout(docx_row)
        drl.setContentsMargins(0, 0, 0, 0)
        self._docx_enabled = QCheckBox("Word document")
        self._docx_enabled.setToolTip(
            "Generate a Word document for each input file containing the image,\n"
            "extracted text, and (if enabled) the proofread text."
        )
        self._docx_format = QComboBox()
        self._docx_format.addItems(["table", "comments"])
        self._docx_format.setToolTip(
            "Table — side-by-side columns: Image | Extracted text | Proofread text.\n"
            "Comments — two columns with tracked changes shown as Word comments."
        )
        drl.addWidget(self._docx_enabled)
        drl.addWidget(self._docx_format)
        drl.addStretch(1)
        form.addRow(docx_row)

        text_row = QWidget()
        trl = QHBoxLayout(text_row)
        trl.setContentsMargins(0, 0, 0, 0)
        self._text_enabled = QCheckBox("Text file")
        self._text_enabled.setToolTip(txt.TEXT_FILE_TOOLTIP)
        self._text_mode = QComboBox()
        self._text_mode.addItems([TEXT_MODE_AS_WRITTEN, TEXT_MODE_ANONYMIZED, TEXT_MODE_BOTH])
        self._text_mode.setToolTip(txt.TEXT_MODE_TOOLTIP)
        trl.addWidget(self._text_enabled)
        trl.addWidget(self._text_mode)
        trl.addStretch(1)
        form.addRow(text_row)

        self._text_hint = QLabel(
            "Text files need an output folder outside the input folder; otherwise the next "
            "run would read them as new documents."
        )
        self._text_hint.setWordWrap(True)
        form.addRow(self._text_hint)

        self._docx_enabled.toggled.connect(lambda _on: self._on_results_widget_changed())
        self._docx_format.currentTextChanged.connect(lambda _t: self._on_results_widget_changed())
        self._text_enabled.toggled.connect(lambda _on: self._on_results_widget_changed())
        self._text_mode.currentTextChanged.connect(lambda _t: self._on_results_widget_changed())
        self._layout.addWidget(inner)
        self._sync_results_widgets()

    # ── results step ──────────────────────────────────────────────────────────────

    def _results_widgets(self) -> tuple[QWidget, ...]:
        return (
            self._output_dir,
            self._docx_enabled,
            self._docx_format,
            self._text_enabled,
            self._text_mode,
        )

    def _sync_results_widgets(self) -> None:
        """Show the Results keys of ``_settings_dict`` without reporting a change."""
        settings = self._settings_dict
        for widget in self._results_widgets():
            widget.blockSignals(True)
        try:
            self._output_dir.setText(str(settings.get("output_dir", "") or ""))
            self._docx_enabled.setChecked(
                bool(settings.get("docx_enabled", DEFAULTS["docx_enabled"]))
            )
            index = self._docx_format.findText(
                str(settings.get("docx_format", DEFAULTS["docx_format"]))
            )
            if index >= 0:
                self._docx_format.setCurrentIndex(index)
            as_written = bool(settings.get("text_enabled", DEFAULTS["text_enabled"]))
            anonymized = bool(
                settings.get("text_anonymized_enabled", DEFAULTS["text_anonymized_enabled"])
            )
            self._text_enabled.setChecked(as_written or anonymized)
            mode = (
                TEXT_MODE_BOTH
                if as_written and anonymized
                else TEXT_MODE_ANONYMIZED
                if anonymized
                else TEXT_MODE_AS_WRITTEN
            )
            index = self._text_mode.findText(mode)
            if index >= 0:
                self._text_mode.setCurrentIndex(index)
        finally:
            for widget in self._results_widgets():
                widget.blockSignals(False)
        self._update_results_state()

    def _update_results_state(self) -> None:
        """Enable the Word layout only for Word; warn when text files have no folder."""
        self._docx_format.setEnabled(self._docx_enabled.isChecked())
        self._text_mode.setEnabled(self._text_enabled.isChecked())
        wants_text = self._text_enabled.isChecked()
        missing_folder = wants_text and not self._output_dir.text().strip()
        color = "#e67e22" if missing_folder else "#888"
        self._text_hint.setStyleSheet(f"color: {color}; font-size: 8pt;")

    def _on_results_widget_changed(self) -> None:
        """Write the Results step into the settings dict and tell the main window."""
        self._settings_dict["docx_enabled"] = self._docx_enabled.isChecked()
        self._settings_dict["docx_format"] = self._docx_format.currentText()
        text_enabled, text_anonymized = self._text_keys()
        self._settings_dict["text_enabled"] = text_enabled
        self._settings_dict["text_anonymized_enabled"] = text_anonymized
        self._update_results_state()
        if not self._loading:
            self.results_changed.emit()

    def _text_keys(self) -> tuple[bool, bool]:
        """Return the two text-file keys the row stands for: (as written, anonymized)."""
        if not self._text_enabled.isChecked():
            return False, False
        mode = self._text_mode.currentText()
        return mode in (TEXT_MODE_AS_WRITTEN, TEXT_MODE_BOTH), mode in (
            TEXT_MODE_ANONYMIZED,
            TEXT_MODE_BOTH,
        )

    def _on_output_dir_edited(self) -> None:
        folder = self._output_dir.text().strip()
        if folder == str(self._settings_dict.get("output_dir", "") or "").strip():
            return  # focus left the field without a change
        self._settings_dict["output_dir"] = folder
        self._update_results_state()
        self.refresh_data_flow()
        if not self._loading:
            self.results_changed.emit()

    def results_problem(self) -> bool:
        """Return whether a file output is on but no folder to write it to is chosen."""
        wants_files = self._docx_enabled.isChecked() or self._text_enabled.isChecked()
        return wants_files and not self._output_dir.text().strip()

    def choose_output_dir(self) -> None:
        """Ask for the results folder (the "…" button and the readiness line's "Choose…")."""
        self._on_browse_output_dir()

    def _on_browse_output_dir(self) -> None:
        start = self._output_dir.text().strip() or self._results_start_dir()
        chosen = _dialogs.pick_folder(self, dlg.select_output_folder(start))
        if chosen is not None:
            self._output_dir.setText(str(chosen))
            self._on_output_dir_edited()

    def _results_start_dir(self) -> str:
        """Return where the results picker starts: next to the documents."""
        input_dir = self.get_input_dir()
        if input_dir:
            return str(Path(input_dir).parent)
        return str(Path.home())

    # ── static helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _separator() -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(0)
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #444;")
        layout.addWidget(line)
        return container

    # ── input selection ───────────────────────────────────────────────────────────

    def select_folder(self) -> None:
        """Ask for an input folder (the "Select Folder…" button and File → Open Folder…)."""
        self._on_select_folder()

    def select_files(self) -> None:
        """Ask for input files (the "Select Files…" button and File → Open Files…)."""
        self._on_select_files()

    def _on_select_folder(self) -> None:
        start = str(self._selected_folder or Path.home())
        path = _dialogs.pick_folder(self, dlg.select_input_folder(start))
        if path is not None:
            self._selected_folder = path
            self._selected_files = []
            self._update_input_label()
            self.paths_changed.emit()

    def _on_select_files(self) -> None:
        start = str(self._selected_folder or Path.home())
        paths = _dialogs.pick_files(self, dlg.select_input_files(start))
        if paths:
            self._selected_files = paths
            self._selected_folder = None
            self._update_input_label()
            self.paths_changed.emit()

    def _update_input_label(self) -> None:
        if self._selected_files:
            n = len(self._selected_files)
            parent = self._selected_files[0].parent
            self._input_row.set_text(txt.files_from_folder(n, parent))
            names = [p.name for p in self._selected_files[:20]]
            if n > len(names):
                names.append(txt.files_more(n - len(names)))
            self._input_row.set_tooltip("\n".join(names))
        elif self._selected_folder is not None:
            self._input_row.set_text(str(self._selected_folder))
            self._input_row.set_tooltip(str(self._selected_folder))
        else:
            self._input_row.set_text("")
            self._input_row.set_tooltip("")

    # ── prompt dialogs ────────────────────────────────────────────────────────────

    def _proofreading_prompts(self, corrections_only: bool) -> tuple[tuple[str, str], ...]:
        """Return the prompts the AI is asked for, for the dialog's read-only section.

        Empty without a service: the panel alone cannot say what the exchange sends.
        """
        if self._service is None:
            return ()
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        config = dataclasses.replace(
            self.lookup_config(),
            correction_output="text" if corrections_only else "review",
        )
        return service.proofreading_prompts(config)

    def _on_configure_correction_prompt(self) -> None:
        settings = self._settings_dict
        dialog = _ProofreadingDialog(
            txt.CORRECTION_PROMPT_DIALOG_TITLE,
            self._correction_notes_text,
            self._proofreading_prompts,
            self,
            explanations=(
                str(settings.get("explanation_language", DEFAULTS["explanation_language"])),
                str(settings.get("correction_output", DEFAULTS["correction_output"])),
            ),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_correction_notes(dialog.notes())
            self.update_settings(dialog.explanation_values())
            self.instructions_changed.emit()

    def _evaluation_fixed_text(self) -> str:
        """Return the read-only note the app always adds to the review instructions."""
        if self._service is None:
            return ""
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        return txt.formatted_text_note(str(service.evaluation_fixed_instructions("markdown")))

    def _on_configure_evaluation_prompt(self) -> None:
        dialog = _PromptDialog(
            txt.EVALUATION_PROMPT_DIALOG_TITLE,
            self._evaluation_prompt_text,
            self,
            builtin_text=str(DEFAULTS["evaluate_prompt"]),
            fixed_text=self._evaluation_fixed_text(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._evaluation_prompt_text = dialog.prompt()
            self.instructions_changed.emit()

    # ── language change ───────────────────────────────────────────────────────────

    def _on_language_changed(self, _language: str) -> None:
        """Re-render what the language decides: the detection models and the data flow."""
        self.refresh_data_flow()

    # ── visibility updates ────────────────────────────────────────────────────────

    def refresh_data_flow(self) -> None:
        """Re-render the "where are my documents processed" box for the live settings.

        Does nothing while settings are being loaded: :meth:`load_from_dict` renders once
        at the end instead of once per restored checkbox.
        """
        if self._service is None or self._loading:
            return
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        # _service is injected as object; narrowed to the concrete service type
        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        config = self.lookup_config()
        # Ask once: the box shows the ticked steps, the lines under the steps show every step.
        steps = service.describe_steps(config)
        self._data_flow = ticked_steps(
            steps,
            correction_enabled=config.correction_enabled,
            evaluation_enabled=config.evaluation_enabled,
            recognition_enabled=config.recognition_enabled,
        )
        self._data_flow_summary.set_flow(self._data_flow)
        self._show_uses_lines(config, steps)
        self.data_flow_changed.emit(self._data_flow)

    def _show_uses_lines(self, config: Config, steps: DataFlow) -> None:
        """Fill the lines under the steps from *steps* (every step as if it were ticked).

        An unticked step keeps its line, in grey, so the teacher sees what ticking it would
        use.  The model is not part of the data flow, so it comes from *config*.
        """
        ocr = steps.stage(STAGE_OCR)
        # Only Ollama has a model; the built-in reader is named by the engine itself.
        ocr_model = config.ocr.model if config.ocr.engine == "ollama" else ""
        self._ocr_uses.set_line(
            uses_line(ocr, ocr_model, enabled=config.recognition_enabled) if ocr else "",
            uses_tooltip(ocr) if ocr else "",
        )
        names = steps.stage(STAGE_ANONYMIZATION)
        self._anonymization_uses.set_line(names_line(names) if names else "")

        proofread = steps.stage(STAGE_CORRECTION)
        proofread_on = self._correction_enabled.isChecked()
        self._correction_uses.set_line(
            uses_line(proofread, config.effective_correction_model, enabled=proofread_on)
            if proofread
            else "",
            uses_tooltip(proofread) if proofread else "",
        )
        review = steps.stage(STAGE_EVALUATION)
        if review is None:
            self._evaluation_uses.set_line("")
            return
        review_on = self.review_will_run()
        if (config.evaluate_provider, config.effective_evaluate_model) == (
            config.correction_provider,
            config.effective_correction_model,
        ):
            text = same_model_line(review, enabled=review_on)
        else:
            text = uses_line(review, config.effective_evaluate_model, enabled=review_on)
        self._evaluation_uses.set_line(text, uses_tooltip(review))

    def data_flow(self) -> DataFlow | None:
        """Return the flow last rendered in the box (``None`` before the first render)."""
        return self._data_flow

    def _on_recognition_changed(self, checked: bool) -> None:
        """The teacher switched text recognition on or off."""
        self._settings_dict["recognition_enabled"] = checked
        self.refresh_data_flow()
        # The note belongs to the old state; the readiness check that follows says what is
        # skipped (or not needed) now.
        self._recognition_note.setVisible(False)
        if checked:
            self._guard_stage("ocr")  # switched on: the engine has to be there

    def show_recognition_note(self, *, skipped: int = 0, not_needed: bool = False) -> None:
        """Say under "Recognize text" what the switch does to the documents in the queue.

        *skipped* scans are left out while the step is off (orange); *not_needed* says the
        step has nothing to do because every queued file is a text file (grey).  Neither:
        the note is hidden.
        """
        if not self._recognition_enabled.isChecked() and skipped:
            self._recognition_note.setText(txt.scans_skipped_note(skipped))
            self._recognition_note.setStyleSheet("color: #e67e22; font-size: 8pt;")
            self._recognition_note.setVisible(True)
        elif self._recognition_enabled.isChecked() and not_needed:
            self._recognition_note.setText(txt.RECOGNIZE_NOT_NEEDED)
            self._recognition_note.setStyleSheet("color: #888; font-size: 8pt;")
            self._recognition_note.setVisible(True)
        else:
            self._recognition_note.setVisible(False)

    def recognition_enabled(self) -> bool:
        """Return whether "Recognize text" is ticked."""
        return self._recognition_enabled.isChecked()

    def turn_on_recognition(self) -> None:
        """Tick "Recognize text" as the teacher would (the "Turn on" button of the readiness line)."""
        self._recognition_enabled.setChecked(True)

    def _update_correction_visibility(self, checked: bool = True) -> None:
        self._sync_review_enabled()
        self.refresh_data_flow()
        if not checked:
            self._settings_dict["correction_enabled"] = False
            return
        self._settings_dict["correction_enabled"] = True
        self._correction_prompt_btn.setEnabled(True)
        # The review comes back with proofreading, so its requirements count again: they are
        # checked once proofreading's are met (see :meth:`_on_stage_guard_result`), so a
        # problem both share is explained once, by proofreading.
        self._guard_stage("correction")

    def _sync_review_enabled(self) -> None:
        """Enable "Review content" only while "Proofread" is ticked.

        The pipeline drops a review without proofreading, so the box would look "on" and do
        nothing.  The tick is kept (and saved): the teacher finds it again with proofreading.
        """
        proofreading = self._correction_enabled.isChecked()
        self._evaluation_enabled.setEnabled(proofreading)
        self._evaluation_enabled.setToolTip("" if proofreading else txt.REVIEW_NEEDS_PROOFREADING)

    def _update_anonymize_visibility(self) -> None:
        pass  # driven by _on_anonymization_changed

    def _on_evaluation_enabled_changed(self, checked: bool) -> None:
        self.refresh_data_flow()
        if not checked:
            self._settings_dict["evaluation_enabled"] = False
            return
        self._settings_dict["evaluation_enabled"] = True
        self._guard_stage("evaluation")

    def set_anonymization_enabled(self, enabled: bool) -> None:
        """Tick or untick "Remove personal info" as the teacher would.

        The normal ``toggled`` path does the rest: the settings dict, the data-flow box and the
        stage guard (which unticks the box again if name detection is missing).
        """
        self._anonymization_enabled.setChecked(enabled)

    def _on_anonymization_changed(self, checked: bool = True) -> None:
        self.refresh_data_flow()
        if not checked:
            self._settings_dict["anonymization_enabled"] = False
            return
        self._settings_dict["anonymization_enabled"] = True
        self._guard_stage("anonymizer")

    def build_anonymizer_config(self) -> object:
        """Build an :class:`AnonymizerConfig` from the current settings dict.

        ``anonymizer_patterns`` of ``None`` (or the list of the earlier built-in
        defaults) means "use the current default patterns"; any other list, even an
        empty one, overrides them (mirrors ``Config.anonymizer_patterns``).
        Centralised here so callers (this panel and the main window's privacy
        preview) do not each rebuild the config from raw settings keys.
        """
        from ..application.service import (  # noqa: PLC0415
            AnonymizerConfig,
            resolve_anonymizer_patterns,
        )

        return AnonymizerConfig(
            primary_model=self._settings_dict.get("anonymizer_primary_model") or None,
            # "auto:system" and "none" are meaningful values, not "not set"
            secondary_model=self._settings_dict.get("anonymizer_secondary_model")
            or SECONDARY_MODEL_NONE,
            patterns=resolve_anonymizer_patterns(self._settings_dict.get("anonymizer_patterns")),
        )

    def _on_configure_anonymizer(self) -> None:
        language = self._language.currentText().strip()
        self.anonymizer_configure_requested.emit(language, self.build_anonymizer_config())

    def anonymizer_context(self) -> tuple[str, object]:
        """Return ``(document language, current AnonymizerConfig)`` for the privacy dialog."""
        return self._language.currentText().strip(), self.build_anonymizer_config()

    def request_anonymizer_config(self) -> None:
        """Emit anonymizer_configure_requested (called from the menu action)."""
        self._on_configure_anonymizer()

    def update_anonymizer_config(self, config: object) -> None:
        """Write an updated AnonymizerConfig back into the settings dict."""
        from ..application.service import (  # noqa: PLC0415
            DEFAULT_ANONYMIZER_PATTERNS,
            AnonymizerConfig,
        )

        if not isinstance(config, AnonymizerConfig):
            return
        self._settings_dict["anonymizer_primary_model"] = config.primary_model
        self._settings_dict["anonymizer_secondary_model"] = config.secondary_model
        # Unchanged defaults stay symbolic (None) so later changes to them reach this user;
        # an empty list is kept, it means "no regex anonymization".
        self._settings_dict["anonymizer_patterns"] = (
            None if config.patterns == tuple(DEFAULT_ANONYMIZER_PATTERNS) else list(config.patterns)
        )

    # ── public API ────────────────────────────────────────────────────────────────

    def get_input_dir(self) -> str:
        """Return the current input directory as a string.

        Returns the selected folder, or the common parent of selected files, or empty string.
        """
        if self._selected_folder is not None:
            return str(self._selected_folder)
        if self._selected_files:
            return str(self._selected_files[0].parent)
        return ""

    def set_input_dir(self, path: str) -> None:
        """Set folder-mode input (e.g. from drag-and-drop). Clears any explicit file selection."""
        if path:
            self._selected_folder = Path(path)
        else:
            self._selected_folder = None
        self._selected_files = []
        self._update_input_label()
        self.paths_changed.emit()

    def get_selected_files(self) -> list[Path] | None:
        """Return the explicitly selected file list, or None when using folder-based discovery."""
        return list(self._selected_files) if self._selected_files else None

    def set_selected_files(self, paths: list[Path]) -> None:
        """Set an explicit list of input files (e.g. from drag-and-drop of individual files)."""
        self._selected_files = list(paths)
        self._selected_folder = None
        self._update_input_label()
        self.paths_changed.emit()

    def get_correction_notes(self) -> str:
        """Return the notes the teacher added for the AI."""
        return self._correction_notes_text

    def set_correction_notes(self, text: str) -> None:
        """Update the notes for the AI (as the options dialog does on OK)."""
        self._correction_notes_text = normalize_notes(text)
        self._settings_dict["correction_notes"] = self._correction_notes_text

    def get_evaluation_prompt(self) -> str:
        """Return the current evaluation system prompt text."""
        return self._evaluation_prompt_text

    def set_evaluation_prompt(self, text: str) -> None:
        """Update the evaluation system prompt (as the instructions dialog does on OK)."""
        self._evaluation_prompt_text = text

    def get_output_dir(self) -> str:
        """Return the configured output directory (set in the Results step)."""
        return str(self._settings_dict.get("output_dir", "")).strip()

    def get_settings_dict(self) -> dict:
        """Return a copy of the settings (what the Settings window shows and edits)."""
        return dict(self._settings_dict)

    def lookup_language(self) -> str:
        """Return the document language currently chosen in the panel."""
        return self._language.currentText().strip()

    def update_settings(self, settings: Mapping[str, Any]) -> None:
        """Apply values returned by the Settings window (or another source of settings)."""
        # Callers pass all keys or only a subset (e.g. just ``ollama_url``).
        self._settings_dict.update(cast("SettingsDict", settings))
        self._sync_results_widgets()
        self.refresh_data_flow()

    def _ocr_config_from_settings(self) -> OcrConfig:
        """Build the :class:`OcrConfig` slice from the stored advanced settings."""
        return OcrConfig.from_settings(self._settings_dict)

    def review_will_run(self) -> bool:
        """Return whether the content review runs: it is ticked and so is proofreading.

        Every config the panel builds uses this, so the data-flow box, the preflight, the model
        list and the memory check never count a review the run would drop.
        """
        return self._evaluation_enabled.isChecked() and self._correction_enabled.isChecked()

    def to_config(self) -> Config:
        """Build and return a Config from current field values."""
        return self._build(strict=True)

    def lookup_config(self) -> Config:
        """Return a Config of the live panel values for requirement lookups.

        Unlike :meth:`to_config` it needs no valid input/output folder, so it also works at
        start-up and before a folder was chosen.  Every other field is the same, so a lookup
        and a run never disagree about the state folder or about a stage's settings.
        """
        return self._build(strict=False)

    def _build(self, *, strict: bool) -> Config:
        """Build the Config of the live panel values; *strict* validates the folders."""
        input_text = self.get_input_dir()
        output_text = str(self._settings_dict.get("output_dir", "")).strip()
        docx_enabled = bool(self._settings_dict.get("docx_enabled", DEFAULTS["docx_enabled"]))
        text_enabled = bool(self._settings_dict.get("text_enabled", DEFAULTS["text_enabled"]))
        text_anonymized_enabled = bool(
            self._settings_dict.get("text_anonymized_enabled", DEFAULTS["text_anonymized_enabled"])
        )
        input_dir = Path(input_text) if input_text else Path(".")
        if strict:
            if not input_text:
                raise ValueError("Input folder is required.")
            if not output_text and (docx_enabled or text_enabled or text_anonymized_enabled):
                raise ValueError("Output folder is required.")
            if not input_dir.is_dir():
                raise ValueError(f"Input folder does not exist: {input_dir}")
        output_dir = Path(output_text) if output_text else input_dir

        return Config(
            input_dir=input_dir,
            output_dir=output_dir,
            # The GUI is intentionally always-recursive; no toggle is exposed.
            recursive=True,
            debug=bool(self._settings_dict.get("debug", DEFAULTS["debug"])),
            keep_awake=bool(self._settings_dict.get("keep_awake", DEFAULTS["keep_awake"])),
            check_for_updates=bool(
                self._settings_dict.get("check_for_updates", DEFAULTS["check_for_updates"])
            ),
            ocr=self._ocr_config_from_settings(),
            language=self._language.currentText().strip() or DEFAULTS["language"],
            ollama_url=self._settings_dict.get("ollama_url", DEFAULTS["ollama_url"]),
            ocr_timeout=int(self._settings_dict.get("ocr_timeout", DEFAULTS["ocr_timeout"])),
            recognition_enabled=self._recognition_enabled.isChecked(),
            correction_enabled=self._correction_enabled.isChecked(),
            anonymization_enabled=self._anonymization_enabled.isChecked(),
            anonymizer_primary_model=self._settings_dict.get(
                "anonymizer_primary_model", DEFAULTS["anonymizer_primary_model"]
            )
            or None,
            anonymizer_secondary_model=self._settings_dict.get(
                "anonymizer_secondary_model", DEFAULTS["anonymizer_secondary_model"]
            )
            or SECONDARY_MODEL_NONE,
            anonymizer_patterns=self._settings_dict.get("anonymizer_patterns"),
            correction_provider=self._settings_dict.get(
                "correction_provider", DEFAULTS["correction_provider"]
            ),
            correction_model=self._settings_dict.get(
                "correction_model", DEFAULTS["correction_model"]
            ),
            correction_notes=self._correction_notes_text,
            correction_level=self._correction_level.currentText(),
            explanation_language=str(
                self._settings_dict.get("explanation_language", DEFAULTS["explanation_language"])
            ),
            correction_output=str(
                self._settings_dict.get("correction_output", DEFAULTS["correction_output"])
            ),
            evaluation_enabled=self.review_will_run(),
            evaluate_provider=self._settings_dict.get(
                "evaluate_provider", DEFAULTS["evaluate_provider"]
            ),
            evaluate_model=self._settings_dict.get("evaluate_model", DEFAULTS["evaluate_model"]),
            evaluate_prompt=self._evaluation_prompt_text,
            docx_format=self._settings_dict.get("docx_format", DEFAULTS["docx_format"]),
            docx_enabled=docx_enabled,
            text_enabled=text_enabled,
            text_anonymized_enabled=text_anonymized_enabled,
            hardware_profile=str(
                self._settings_dict.get("hardware_profile", DEFAULTS["hardware_profile"])
            ),
            proofreading_context=str(
                self._settings_dict.get("proofreading_context", DEFAULTS["proofreading_context"])
            ),
        )

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def load_from_dict(self, values: dict) -> None:
        """Populate fields from a dict (e.g. loaded from ocr.toml).

        Requirement guards are suspended meanwhile: restoring saved settings must not
        contact Ollama or open dialogs (this runs inside the window constructor).
        """
        self._loading = True
        try:
            self._load_from_dict(values)
        finally:
            self._loading = False
        self.refresh_data_flow()

    def _load_from_dict(self, values: dict) -> None:
        if "input" in values:
            path = Path(str(values["input"]))
            if path.is_dir():
                self._selected_folder = path
                self._selected_files = []
            self._update_input_label()
        if "output" in values:
            self._settings_dict["output_dir"] = str(values["output"])

        self._load_prompts_and_language(values)

        if "recognition_enabled" in values:
            self._recognition_enabled.setChecked(bool(values["recognition_enabled"]))
            self._settings_dict["recognition_enabled"] = self._recognition_enabled.isChecked()
        if "correction_enabled" in values:
            self._correction_enabled.setChecked(bool(values["correction_enabled"]))
        if "anonymization_enabled" in values:
            self._anonymization_enabled.setChecked(bool(values["anonymization_enabled"]))
        if "evaluation_enabled" in values:
            self._evaluation_enabled.setChecked(bool(values["evaluation_enabled"]))

        self._apply_settings_dict_values(values)
        self._sync_results_widgets()

        self._update_correction_visibility(self._correction_enabled.isChecked())
        self._sync_review_enabled()

    def _load_prompts_and_language(self, values: dict) -> None:
        """Apply the notes, the content review's prompt and the language combo from *values*.

        Sets them BEFORE the language so that when the language combo fires
        ``currentTextChanged -> _on_language_changed``, the panel already holds the new state.
        """
        self._language.currentTextChanged.disconnect(self._on_language_changed)
        try:
            if "correction_notes" in values:
                self._correction_notes_text = normalize_notes(values["correction_notes"])
            if "evaluate_prompt" in values:
                # "" = the built-in default; a saved copy of it means the same.
                self._evaluation_prompt_text = migrate_saved_evaluation_prompt(
                    str(values["evaluate_prompt"])
                )
            if "language" in values:
                self._set_combo(self._language, str(values["language"]))
        finally:
            self._language.currentTextChanged.connect(self._on_language_changed)
        self._on_language_changed(self._language.currentText())

    def _with_local_ocr_engine(self, values: dict) -> dict:
        """Return *values* with an unavailable OCR engine (and its model) replaced.

        A settings file from an older version may name an online engine: fall back to the
        default engine and reset the model that went with it.  A removed local engine
        (Tesseract, PaddleOCR) becomes the built-in reader.  Either replacement is reported
        through :attr:`ocr_engine_replaced`, so it never happens silently.
        """
        if "ocr_engine" not in values:
            return values
        replaced = replaced_ocr_engine(values["ocr_engine"])
        if replaced is not None:
            self.ocr_engine_replaced.emit(replaced)
        fixed = {**values, "ocr_engine": normalize_ocr_engine(values["ocr_engine"])}
        if "ocr_model" in values:
            fixed["ocr_model"] = normalize_ocr_model(values["ocr_engine"], values["ocr_model"])
        return fixed

    def _apply_settings_dict_values(self, values: dict) -> None:
        """Copy the recognised string/scalar/flag keys from *values* into the settings dict."""
        values = self._with_local_ocr_engine(values)
        if "ocr_engine" in values:
            self._settings_dict["ocr_engine"] = values["ocr_engine"]

        for key in (
            "ocr_model",
            "ollama_url",
            "hardware_profile",
            "correction_provider",
            "correction_model",
            "explanation_language",
            "correction_output",
            "evaluate_provider",
            "evaluate_model",
            "docx_format",
        ):
            if key in values:
                self._settings_dict[key] = str(values[key])

        if "correction_level" in values:
            self._correction_level.setCurrentText(normalize_level(str(values["correction_level"])))
        if "correction_output" in values:
            self._settings_dict["correction_output"] = normalize_output_mode(
                str(values["correction_output"])
            )

        if "preprocess_method" in values:
            # Methods of earlier versions (grayscale, clahe, …) read as the automatic one.
            self._settings_dict["preprocess_method"] = normalize_preprocess_method(
                values["preprocess_method"]
            )

        if "pdf_text_layer" in values:
            self._settings_dict["pdf_text_layer"] = normalize_pdf_text_layer(
                str(values["pdf_text_layer"])
            )

        for key in ("ocr_timeout", "pdf_render_dpi"):
            if key in values:
                self._settings_dict[key] = int(values[key])

        for key in (
            "debug",
            "keep_awake",
            "check_for_updates",
            "docx_enabled",
            "text_enabled",
            "text_anonymized_enabled",
            "evaluation_enabled",
            "anonymization_enabled",
        ):
            if key in values:
                self._settings_dict[key] = bool(values[key])

        if "anonymizer_primary_model" in values:
            self._settings_dict["anonymizer_primary_model"] = (
                values["anonymizer_primary_model"] or None
            )
        if "anonymizer_secondary_model" in values:
            self._settings_dict["anonymizer_secondary_model"] = (
                values["anonymizer_secondary_model"] or SECONDARY_MODEL_NONE
            )
        if "anonymizer_patterns" in values:
            self._settings_dict["anonymizer_patterns"] = values["anonymizer_patterns"]

    def set_service(self, service: object) -> None:
        """Inject the application service so the panel can run requirement checks."""
        self._service = service
        self.refresh_data_flow()

    def _stage_checkbox(self, stage: str) -> QCheckBox:
        """Return the enable checkbox of pipeline *stage*."""
        if stage == "ocr":
            return self._recognition_enabled
        if stage == "correction":
            return self._correction_enabled
        if stage == "evaluation":
            return self._evaluation_enabled
        return self._anonymization_enabled

    def _guard_stage(self, stage: str) -> None:
        """Check the requirements of *stage* in the background after the user enabled it.

        The check contacts Ollama (or imports spaCy), so it must never run on the GUI
        thread.  The box stays ticked meanwhile; if a requirement is unmet it is
        unticked and the guard dialog explains why.  Nothing happens while saved
        settings are being loaded.
        """
        checkbox = self._stage_checkbox(stage)
        if self._loading or not checkbox.isChecked() or self._service is None:
            return
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        # _service is injected as object; narrowed to the concrete service type
        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        config = self.lookup_config()
        task = self._guard_tasks.setdefault(stage, LatestTask(self))
        task.submit(
            lambda: service.check_stage_requirements(config, stage),
            lambda result, error: self._on_stage_guard_result(config, stage, result, error),
        )

    @staticmethod
    def _stage_inputs(config: Config, stage: str) -> tuple[str, ...]:
        """Return the config values the requirements of *stage* depend on."""
        if stage == "ocr":
            return (config.ocr.engine,)
        if stage == "correction":
            return (
                config.correction_provider,
                config.effective_correction_model,
                config.ollama_url,
            )
        if stage == "evaluation":
            return (config.evaluate_provider, config.effective_evaluate_model, config.ollama_url)
        return (config.language,)

    def _on_stage_guard_result(
        self, config: Config, stage: str, result: object, error: BaseException | None
    ) -> None:
        """Untick *stage* and explain when its background requirement check failed."""
        issues = (
            [issue for issue in result if isinstance(issue, RequirementIssue)]
            if error is None and isinstance(result, list)
            else []
        )
        if not issues:
            # All met (or the check itself failed: never block the UI on that).  A review that
            # was ticked before proofreading came back is checked now.
            if stage == "correction" and self._evaluation_enabled.isChecked():
                self._guard_stage("evaluation")
            return
        checkbox = self._stage_checkbox(stage)
        if not checkbox.isChecked():
            return  # the user unticked it in the meantime
        if stage == "evaluation" and not self._correction_enabled.isChecked():
            return  # no review without proofreading: its tick stays for when it comes back
        if self._stage_inputs(self.lookup_config(), stage) != self._stage_inputs(config, stage):
            return  # the user chose another model/provider/server: this answer is outdated
        checkbox.blockSignals(True)
        checkbox.setChecked(False)
        checkbox.blockSignals(False)
        self.refresh_data_flow()  # signals were blocked: the box must be told itself
        if stage == "ocr":
            self._settings_dict["recognition_enabled"] = False
        elif stage == "correction":
            self._settings_dict["correction_enabled"] = False
        elif stage == "evaluation":
            self._settings_dict["evaluation_enabled"] = False
        else:
            self._settings_dict["anonymization_enabled"] = False
        self._sync_review_enabled()

        from ..application.service import ProcessingApplicationService  # noqa: PLC0415
        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

        title = txt.GUARD_TITLE.format(step=_STAGE_STEP_TITLES.get(stage, stage))
        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        fix = fix_for(
            stage,
            issues[0],
            lambda provider: str(service.get_provider_info(provider).get("display_name", provider)),
            language=self._language.currentText().strip(),
        )
        dialog = DependencyGuardDialog(title, issues, fix.actions, self)
        dialog.action_chosen.connect(self._open_fix)
        dialog.exec()

    def _open_fix(self, action: FixAction) -> None:
        """Open the Settings place a fix button names."""
        if action.page is not None:
            self.open_settings_requested.emit(action.page, action.focus, list(action.preselect))

    def restore_defaults(self) -> None:
        self.load_from_dict(DEFAULTS)

    def to_dict(self) -> dict:
        """Return current values as a TOML-compatible settings dict.

        Note: the GUI is intentionally always-recursive (see :meth:`to_config`),
        so ``recursive`` is deliberately not persisted — writing it would imply a
        toggle the GUI does not expose.
        """
        values = {
            "input": self.get_input_dir(),
            "output": str(self._settings_dict.get("output_dir", "")).strip(),
            "language": self._language.currentText().strip(),
            "recognition_enabled": self._recognition_enabled.isChecked(),
            "correction_enabled": self._correction_enabled.isChecked(),
            "anonymization_enabled": self._anonymization_enabled.isChecked(),
            "correction_notes": self._correction_notes_text,
            "correction_level": self._correction_level.currentText(),
            "evaluate_prompt": self._evaluation_prompt_text,
            **self._settings_dict,
        }
        if values.get("anonymizer_secondary_model") is None:
            # None is skipped when writing the file, so an explicit "(none)" would be lost.
            values["anonymizer_secondary_model"] = SECONDARY_MODEL_NONE
        return values
