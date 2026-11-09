"""Configuration panel — the workflow of one batch, as numbered steps.

What changes per batch lives here: the documents, the language, which steps run, their
instructions, and where the results go.  What is set up once (which AI models, scan
quality, privacy rules, this computer, add-ons) is edited in the Settings window and stored
in ``_settings_dict`` alongside.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..application.correction_options import (
    migrate_saved_correction_prompt,
    migrate_saved_evaluation_prompt,
)
from ..application.service import RequirementIssue
from ..config import (
    DEFAULTS,
    SECONDARY_MODEL_NONE,
    Config,
    OcrConfig,
    migrate_answer_formats,
    normalize_ocr_engine,
    normalize_ocr_model,
)
from ..domain.correction_policy import LEVELS, normalize_level, normalize_output_mode
from ..domain.data_location import DataFlow
from ..domain.pdf_text_layer import normalize_pdf_text_layer
from . import _user_text as txt
from ._background import LatestTask
from ._constants import _LANGUAGES
from ._data_flow_summary import DataFlowSummary
from ._prompt_editor import PromptEditor
from ._types import SettingsDict


class _PromptDialog(QDialog):
    """Modal dialog for the machine instructions, built around :class:`PromptEditor`.

    For proofreading it also holds the "Explanations" options (*explanations* =
    ``(explanation_language, correction_output)``): how the task behaves is set here, never
    in the Settings window.
    """

    def __init__(  # noqa: PLR0913  # PromptEditor's options plus the explanations
        self,
        title: str,
        prompt: str,
        parent: QWidget | None = None,
        *,
        builtin_text: str = "",
        fixed_text: str = "",
        answer_formats: tuple[str, str] | None = None,
        saved_formats: tuple[str, str] = ("", ""),
        level_aware: bool = False,
        explanations: tuple[str, str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(560, 420)
        vbox = QVBoxLayout(self)
        self._explanation_language: QComboBox | None = None
        self._corrections_only: QCheckBox | None = None
        if explanations is not None:
            vbox.addWidget(self._build_explanations_group(*explanations))
        self._editor = PromptEditor(
            prompt,
            self,
            builtin_text=builtin_text,
            fixed_text=fixed_text,
            answer_formats=answer_formats,
            saved_formats=saved_formats,
            level_aware=level_aware,
        )
        vbox.addWidget(self._editor, stretch=1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        vbox.addWidget(buttons)

    def prompt(self) -> str:
        return self._editor.prompt()

    def answer_format_values(self) -> dict[str, object]:
        """Return the answer-format settings the dialog edits (empty without editors)."""
        return self._editor.answer_format_values()

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
        form.addRow("", self._corrections_only)
        return group

    def explanation_values(self) -> dict[str, object]:
        """Return ``explanation_language`` and ``correction_output`` (empty without the group)."""
        if self._explanation_language is None or self._corrections_only is None:
            return {}
        return {
            "explanation_language": str(self._explanation_language.currentData() or ""),
            "correction_output": "text" if self._corrections_only.isChecked() else "review",
        }


class ConfigPanel(QScrollArea):
    """Left-hand panel: main pipeline configuration as a numbered workflow."""

    paths_changed = Signal()
    anonymizer_configure_requested = Signal(str, object)  # language, AnonymizerConfig
    #: Bubbled from DependencyGuardDialog: Add-ons focus value, entries to preselect.
    open_downloads_requested = Signal(str, list)
    #: The user changed the Results step (folder, which files); the main window saves.
    results_changed = Signal()
    #: The user confirmed an Instructions dialog (prompt, answer formats, explanations).
    instructions_changed = Signal()
    #: The last rendered data flow, so cells elsewhere can say where a step's text goes.
    data_flow_changed = Signal(object)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        preset_prompts: dict[str, str] | None = None,
        default_prompt: str = "",
    ) -> None:
        super().__init__(parent)
        self._preset_prompts: dict[str, str] = preset_prompts or {}
        self._default_prompt: str = default_prompt
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
            "preprocess_method": DEFAULTS["preprocess_method"],
            "preprocess_dewarp": DEFAULTS["preprocess_dewarp"],
            "preprocess_deskew": DEFAULTS["preprocess_deskew"],
            "preprocess_border_crop": DEFAULTS["preprocess_border_crop"],
            "preprocess_denoise": DEFAULTS["preprocess_denoise"],
            "preprocess_gamma": DEFAULTS["preprocess_gamma"],
            "pdf_render_dpi": DEFAULTS["pdf_render_dpi"],
            "pdf_text_layer": DEFAULTS["pdf_text_layer"],
            "correction_provider": DEFAULTS["correction_provider"],
            "correction_model": DEFAULTS["correction_model"],
            "explanation_language": DEFAULTS["explanation_language"],
            "correction_output": DEFAULTS["correction_output"],
            "answer_format_review": DEFAULTS["answer_format_review"],
            "answer_format_text": DEFAULTS["answer_format_text"],
            "answer_format_version": DEFAULTS["answer_format_version"],
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
        }
        self._settings_dict["anonymizer_primary_model"] = None
        # SECONDARY_MODEL_AUTO by default; SECONDARY_MODEL_NONE = the user chose "(none)".
        self._settings_dict["anonymizer_secondary_model"] = DEFAULTS["anonymizer_secondary_model"]
        self._settings_dict["anonymizer_patterns"] = None

        # Input selection state: either a folder or an explicit file list.
        self._selected_folder: Path | None = None
        self._selected_files: list[Path] = []

        # Prompt texts stored internally (not live QPlainTextEdits in the panel).
        # "" means "use the built-in prompt" — the preset text is never copied here,
        # so a code-side preset change keeps being recognised and the language
        # setting keeps swapping the preset.
        self._correction_prompt_text: str = ""
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
        self._build_step3_anonymize()
        self._layout.addWidget(self._separator())
        self._build_step4_correct()
        self._layout.addWidget(self._separator())
        self._build_step5_evaluate()
        self._layout.addWidget(self._separator())
        self._build_step6_results()
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
                "Step 1: Select Your Documents",
                "Choose the documents you want to process.\n"
                "You can pick individual files or a whole folder.\n"
                "Subfolders are included automatically.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        btn_row = QWidget()
        h = QHBoxLayout(btn_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)

        self._select_folder_btn = QPushButton("Select Folder…")
        self._select_folder_btn.setToolTip(
            "Choose a folder — all supported files inside will be processed, including subfolders."
        )
        self._select_folder_btn.clicked.connect(self._on_select_folder)

        self._select_files_btn = QPushButton("Select Files…")
        self._select_files_btn.setToolTip(
            "Choose one or more individual files (PDF, images, or plain text)."
        )
        self._select_files_btn.clicked.connect(self._on_select_files)

        h.addWidget(self._select_folder_btn)
        h.addWidget(self._select_files_btn)
        v.addWidget(btn_row)

        self._input_selection_label = QLabel("No input selected")
        self._input_selection_label.setWordWrap(True)
        self._input_selection_label.setStyleSheet("color: #888; font-size: 8pt;")
        v.addWidget(self._input_selection_label)

        self._layout.addWidget(inner)

    def _build_step2_language(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 2: Document Language",
                "Select the language the documents are written in.\n"
                "This helps the app recognise text accurately and\n"
                "proofread in the correct language.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(4)

        self._language = QComboBox()
        self._language.addItems(_LANGUAGES)
        self._language.setEditable(True)
        self._language.setCurrentText("English")
        self._language.activated.connect(self._language.hidePopup)
        v.addWidget(self._language)

        self._layout.addWidget(inner)

    def _build_step3_anonymize(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 3: Remove Personal Information",
                "Automatically hides names, email addresses, and phone numbers\n"
                "in the extracted text, before anything is sent for proofreading\n"
                "or content review. The original information is restored in the\n"
                "final document. Runs right after text recognition and does not\n"
                "need proofreading. Not needed while everything runs on this\n"
                f"computer. Requires the Privacy add-on (see {txt.WHERE_ADDONS}).",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._anonymization_enabled = QCheckBox("Remove personal info")
        self._anonymization_enabled.setChecked(False)
        self._anonymization_enabled.toggled.connect(self._on_anonymization_changed)
        v.addWidget(self._anonymization_enabled)

        self._anon_configure_btn = QPushButton("Privacy rules…")
        self._anon_configure_btn.setVisible(True)
        self._anon_configure_btn.setToolTip(
            "Set which types of personal information to hide and how."
        )
        self._anon_configure_btn.clicked.connect(self._on_configure_anonymizer)
        v.addWidget(self._anon_configure_btn)

        self._layout.addWidget(inner)

    def _build_step4_correct(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 4: Proofread",
                "Use an AI service to fix spelling and grammar in the extracted text.\n"
                f"Requires a connected AI service (see {txt.WHERE_AI_MODELS}).",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._correction_enabled = QCheckBox("Enable proofreading")
        self._correction_enabled.setChecked(True)
        self._correction_enabled.toggled.connect(self._update_correction_visibility)
        v.addWidget(self._correction_enabled)

        self._correction_prompt_btn = QPushButton("Instructions…")
        self._correction_prompt_btn.setToolTip(
            "Change the instructions sent to the AI service for proofreading, the language\n"
            "of the explanations, and whether you get explanations at all.\n"
            "Until you change them, the built-in instructions of the document language are used."
        )
        self._correction_prompt_btn.clicked.connect(self._on_configure_correction_prompt)
        v.addWidget(self._correction_prompt_btn)

        self._custom_prompt_note = QLabel(txt.CUSTOM_PROMPT_NOTE)
        self._custom_prompt_note.setWordWrap(True)
        self._custom_prompt_note.setStyleSheet("color: #e67e22; font-size: 8pt;")
        self._custom_prompt_note.setVisible(False)
        v.addWidget(self._custom_prompt_note)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        level_label = QLabel("Language level:")
        level_label.setToolTip(txt.LEVEL_TOOLTIP)
        self._correction_level = QComboBox()
        self._correction_level.addItems(list(LEVELS))
        self._correction_level.setCurrentText(DEFAULTS["correction_level"])
        self._correction_level.setToolTip(txt.LEVEL_TOOLTIP)
        row.addWidget(level_label)
        row.addWidget(self._correction_level)
        row.addStretch()
        v.addLayout(row)

        self._layout.addWidget(inner)

    def _build_step5_evaluate(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 5: Content Review",
                "Let an AI service review the proofread text and generate a content review report.",
            )
        )
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setContentsMargins(0, 2, 0, 4)
        v.setSpacing(6)

        self._evaluation_enabled = QCheckBox("Enable content review")
        self._evaluation_enabled.setChecked(False)
        self._evaluation_enabled.toggled.connect(self._on_evaluation_enabled_changed)
        v.addWidget(self._evaluation_enabled)

        self._evaluation_prompt_btn = QPushButton("Instructions…")
        self._evaluation_prompt_btn.setToolTip(
            "Customize the instructions used to generate the content review report."
        )
        self._evaluation_prompt_btn.clicked.connect(self._on_configure_evaluation_prompt)
        v.addWidget(self._evaluation_prompt_btn)

        self._layout.addWidget(inner)

    def _build_step6_results(self) -> None:
        self._layout.addWidget(
            self._step_header(
                "Step 6: Results",
                "Where the results are saved, and which files a run creates there.",
            )
        )
        inner = QWidget()
        form = QFormLayout(inner)
        form.setContentsMargins(0, 2, 0, 4)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)

        output_row = QWidget()
        orl = QHBoxLayout(output_row)
        orl.setContentsMargins(0, 0, 0, 0)
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("Choose a folder for the results")
        self._output_dir.setToolTip(
            "Folder where Word documents and text files will be saved.\n"
            "Required as soon as a Word document or a text file is switched on."
        )
        self._output_dir.editingFinished.connect(self._on_output_dir_edited)
        self._output_browse = QPushButton("…")
        self._output_browse.setFixedWidth(32)
        self._output_browse.setToolTip("Choose the folder.")
        self._output_browse.clicked.connect(self._on_browse_output_dir)
        orl.addWidget(self._output_dir, stretch=1)
        orl.addWidget(self._output_browse)
        form.addRow("Save to:", output_row)

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

        self._text_enabled = QCheckBox("Text file")
        self._text_enabled.setToolTip(
            "Save the extracted text of each input file as a text file (name.txt)."
        )
        form.addRow(self._text_enabled)

        self._text_anonymized_enabled = QCheckBox("Text file, anonymized")
        self._text_anonymized_enabled.setToolTip(
            "Save the extracted text with names and other personal data replaced by\n"
            "placeholders (name_anonymized.txt). Runs on this computer and needs the\n"
            "privacy add-on (spaCy)."
        )
        form.addRow(self._text_anonymized_enabled)

        self._text_hint = QLabel(
            "Text files need an output folder outside the input folder; otherwise the next "
            "run would read them as new documents."
        )
        self._text_hint.setWordWrap(True)
        form.addRow(self._text_hint)

        self._docx_enabled.toggled.connect(lambda _on: self._on_results_widget_changed())
        self._docx_format.currentTextChanged.connect(lambda _t: self._on_results_widget_changed())
        self._text_enabled.toggled.connect(lambda _on: self._on_results_widget_changed())
        self._text_anonymized_enabled.toggled.connect(lambda _on: self._on_results_widget_changed())
        self._layout.addWidget(inner)
        self._sync_results_widgets()

    # ── results step ──────────────────────────────────────────────────────────────

    def _results_widgets(self) -> tuple[QWidget, ...]:
        return (
            self._output_dir,
            self._docx_enabled,
            self._docx_format,
            self._text_enabled,
            self._text_anonymized_enabled,
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
            self._text_enabled.setChecked(
                bool(settings.get("text_enabled", DEFAULTS["text_enabled"]))
            )
            self._text_anonymized_enabled.setChecked(
                bool(settings.get("text_anonymized_enabled", DEFAULTS["text_anonymized_enabled"]))
            )
        finally:
            for widget in self._results_widgets():
                widget.blockSignals(False)
        self._update_results_state()

    def _update_results_state(self) -> None:
        """Enable the Word layout only for Word; warn when text files have no folder."""
        self._docx_format.setEnabled(self._docx_enabled.isChecked())
        wants_text = self._text_enabled.isChecked() or self._text_anonymized_enabled.isChecked()
        missing_folder = wants_text and not self._output_dir.text().strip()
        color = "#e67e22" if missing_folder else "#888"
        self._text_hint.setStyleSheet(f"color: {color}; font-size: 8pt;")

    def _on_results_widget_changed(self) -> None:
        """Write the Results step into the settings dict and tell the main window."""
        self._settings_dict["docx_enabled"] = self._docx_enabled.isChecked()
        self._settings_dict["docx_format"] = self._docx_format.currentText()
        self._settings_dict["text_enabled"] = self._text_enabled.isChecked()
        self._settings_dict["text_anonymized_enabled"] = self._text_anonymized_enabled.isChecked()
        self._update_results_state()
        if not self._loading:
            self.results_changed.emit()

    def _on_output_dir_edited(self) -> None:
        folder = self._output_dir.text().strip()
        if folder == str(self._settings_dict.get("output_dir", "") or "").strip():
            return  # focus left the field without a change
        self._settings_dict["output_dir"] = folder
        self._update_results_state()
        self.refresh_data_flow()
        if not self._loading:
            self.results_changed.emit()

    def _on_browse_output_dir(self) -> None:
        start = self._output_dir.text().strip() or str(Path.home())
        options = QFileDialog.Option(0)
        if sys.platform == "win32":
            options |= QFileDialog.Option.DontUseNativeDialog
        chosen = QFileDialog.getExistingDirectory(self, "Select output folder", start, options)
        if chosen:
            self._output_dir.setText(chosen)
            self._on_output_dir_edited()

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
        options = QFileDialog.Option(0)
        if sys.platform == "win32":
            options |= QFileDialog.Option.DontUseNativeDialog
        path = QFileDialog.getExistingDirectory(None, "Select Input Folder", start, options)
        if path:
            self._selected_folder = Path(path)
            self._selected_files = []
            self._update_input_label()
            self.paths_changed.emit()

    def _on_select_files(self) -> None:
        start = str(self._selected_folder or Path.home())
        options = QFileDialog.Option(0)
        if sys.platform == "win32":
            options |= QFileDialog.Option.DontUseNativeDialog
        paths, _ = QFileDialog.getOpenFileNames(
            None,
            "Select Input Files",
            start,
            "Supported files (*.pdf *.PDF *.png *.PNG *.jpg *.JPG *.jpeg *.JPEG "
            "*.tiff *.TIFF *.tif *.TIF *.txt *.TXT);;All files (*)",
            options=options,
        )
        if paths:
            self._selected_files = [Path(p) for p in paths]
            self._selected_folder = None
            self._update_input_label()
            self.paths_changed.emit()

    def _update_input_label(self) -> None:
        if self._selected_files:
            n = len(self._selected_files)
            parent = self._selected_files[0].parent
            self._input_selection_label.setText(f"{n} file{'s' if n != 1 else ''} from {parent}")
            self._input_selection_label.setStyleSheet("color: #ddd; font-size: 8pt;")
        elif self._selected_folder is not None:
            self._input_selection_label.setText(f"Folder: {self._selected_folder}")
            self._input_selection_label.setStyleSheet("color: #ddd; font-size: 8pt;")
        else:
            self._input_selection_label.setText("No input selected")
            self._input_selection_label.setStyleSheet("color: #888; font-size: 8pt;")

    # ── prompt dialogs ────────────────────────────────────────────────────────────

    def _correction_dialog_texts(self) -> tuple[str, str, tuple[str, str] | None]:
        """Return the built-in prompt, the read-only note and the built-in answer formats.

        Falls back to the panel's preset table when no service is injected (then there is
        no answer-format editor either).
        """
        language = self._language.currentText().strip()
        builtin = self._preset_prompts.get(language.lower(), self._default_prompt)
        if self._service is None:
            return builtin, "", None
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        return (
            builtin,
            txt.formatted_text_note(service.correction_notation_hint()),
            service.default_answer_formats(),
        )

    def _on_configure_correction_prompt(self) -> None:
        builtin, fixed, formats = self._correction_dialog_texts()
        settings = self._settings_dict
        dlg = _PromptDialog(
            txt.CORRECTION_PROMPT_DIALOG_TITLE,
            self._correction_prompt_text,
            self,
            builtin_text=builtin,
            fixed_text=fixed,
            answer_formats=formats,
            saved_formats=(
                str(settings.get("answer_format_review", "")),
                str(settings.get("answer_format_text", "")),
            ),
            level_aware=True,
            explanations=(
                str(settings.get("explanation_language", DEFAULTS["explanation_language"])),
                str(settings.get("correction_output", DEFAULTS["correction_output"])),
            ),
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.set_correction_prompt(dlg.prompt())
            self.update_settings({**dlg.answer_format_values(), **dlg.explanation_values()})
            self.instructions_changed.emit()

    def _evaluation_fixed_text(self) -> str:
        """Return the read-only note the app always adds to the review instructions."""
        if self._service is None:
            return ""
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        return txt.formatted_text_note(str(service.evaluation_fixed_instructions("markdown")))

    def _on_configure_evaluation_prompt(self) -> None:
        dlg = _PromptDialog(
            txt.EVALUATION_PROMPT_DIALOG_TITLE,
            self._evaluation_prompt_text,
            self,
            builtin_text=str(DEFAULTS["evaluate_prompt"]),
            fixed_text=self._evaluation_fixed_text(),
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._evaluation_prompt_text = dlg.prompt()
            self.instructions_changed.emit()

    # ── language change ───────────────────────────────────────────────────────────

    def _on_language_changed(self, _language: str) -> None:
        """Only update the custom-prompt note; the prompt itself is a reference now.

        An empty prompt keeps meaning "the built-in prompt of the (new) document
        language".  A custom prompt is used for every language, which the note says.
        """
        self._custom_prompt_note.setVisible(bool(self._correction_prompt_text.strip()))
        # The language decides the detection models, so the box and the privacy setting change.
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
        self._data_flow = service.describe_data_flow(self.lookup_config())
        self._data_flow_summary.set_flow(self._data_flow)
        self.data_flow_changed.emit(self._data_flow)

    def data_flow(self) -> DataFlow | None:
        """Return the flow last rendered in the box (``None`` before the first render)."""
        return self._data_flow

    def _update_correction_visibility(self, checked: bool = True) -> None:
        self.refresh_data_flow()
        if not checked:
            self._settings_dict["correction_enabled"] = False
            return
        self._settings_dict["correction_enabled"] = True
        self._correction_prompt_btn.setEnabled(True)
        self._guard_stage("correction")

    def _update_anonymize_visibility(self) -> None:
        pass  # driven by _on_anonymization_changed

    def _on_evaluation_enabled_changed(self, checked: bool) -> None:
        self.refresh_data_flow()
        if not checked:
            self._settings_dict["evaluation_enabled"] = False
            return
        self._settings_dict["evaluation_enabled"] = True
        self._guard_stage("evaluation")

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

    def get_correction_prompt(self) -> str:
        """Return the current correction system prompt text."""
        return self._correction_prompt_text

    def set_correction_prompt(self, text: str) -> None:
        """Update the correction system prompt (as the instructions dialog does on OK)."""
        self._correction_prompt_text = text
        self._custom_prompt_note.setVisible(bool(text.strip()))

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

    def update_settings(self, settings: dict) -> None:
        """Apply values returned by the Settings window (or another source of settings)."""
        self._settings_dict.update(settings)
        self._sync_results_widgets()
        self.refresh_data_flow()

    def _ocr_config_from_settings(self) -> OcrConfig:
        """Build the :class:`OcrConfig` slice from the stored advanced settings."""
        return OcrConfig.from_settings(self._settings_dict)

    def to_config(self) -> Config:
        """Build and return a Config from current field values."""
        input_text = self.get_input_dir()
        output_text = str(self._settings_dict.get("output_dir", "")).strip()
        docx_enabled = bool(self._settings_dict.get("docx_enabled", DEFAULTS["docx_enabled"]))
        text_enabled = bool(self._settings_dict.get("text_enabled", DEFAULTS["text_enabled"]))
        text_anonymized_enabled = bool(
            self._settings_dict.get("text_anonymized_enabled", DEFAULTS["text_anonymized_enabled"])
        )
        if not input_text:
            raise ValueError("Input folder is required.")
        if not output_text and (docx_enabled or text_enabled or text_anonymized_enabled):
            raise ValueError("Output folder is required.")
        input_dir = Path(input_text)
        if not input_dir.is_dir():
            raise ValueError(f"Input folder does not exist: {input_dir}")
        output_dir = Path(output_text) if output_text else input_dir

        return Config(
            input_dir=input_dir,
            output_dir=output_dir,
            # The GUI is intentionally always-recursive; no toggle is exposed.
            recursive=True,
            debug=bool(self._settings_dict.get("debug", DEFAULTS["debug"])),
            ocr=self._ocr_config_from_settings(),
            language=self._language.currentText().strip() or DEFAULTS["language"],
            ollama_url=self._settings_dict.get("ollama_url", DEFAULTS["ollama_url"]),
            ocr_timeout=int(self._settings_dict.get("ocr_timeout", DEFAULTS["ocr_timeout"])),
            correction_enabled=self._correction_enabled.isChecked(),
            anonymization_enabled=self._anonymization_enabled.isChecked(),
            anonymizer_primary_model=self._settings_dict.get(
                "anonymizer_primary_model", DEFAULTS["anonymizer_primary_model"]
            )
            or None,
            anonymizer_secondary_model=self._settings_dict.get(
                "anonymizer_secondary_model", DEFAULTS["anonymizer_secondary_model"]
            )
            or None,
            anonymizer_patterns=self._settings_dict.get("anonymizer_patterns"),
            correction_provider=self._settings_dict.get(
                "correction_provider", DEFAULTS["correction_provider"]
            ),
            correction_model=self._settings_dict.get(
                "correction_model", DEFAULTS["correction_model"]
            ),
            correction_prompt=self._correction_prompt_text,
            correction_level=self._correction_level.currentText(),
            explanation_language=str(
                self._settings_dict.get("explanation_language", DEFAULTS["explanation_language"])
            ),
            correction_output=str(
                self._settings_dict.get("correction_output", DEFAULTS["correction_output"])
            ),
            answer_format_review=str(self._settings_dict.get("answer_format_review", "")),
            answer_format_text=str(self._settings_dict.get("answer_format_text", "")),
            evaluation_enabled=self._evaluation_enabled.isChecked(),
            evaluate_provider=self._settings_dict.get(
                "evaluate_provider", DEFAULTS["evaluate_provider"]
            ),
            evaluate_model=self._settings_dict.get("evaluate_model", DEFAULTS["evaluate_model"]),
            evaluate_prompt=self._evaluation_prompt_text,
            evaluate_temperature=float(
                self._settings_dict.get("evaluate_temperature", DEFAULTS["evaluate_temperature"])
            ),
            correction_temperature=float(
                self._settings_dict.get(
                    "correction_temperature", DEFAULTS["correction_temperature"]
                )
            ),
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

        if "correction_enabled" in values:
            self._correction_enabled.setChecked(bool(values["correction_enabled"]))
        if "anonymization_enabled" in values:
            self._anonymization_enabled.setChecked(bool(values["anonymization_enabled"]))
        if "evaluation_enabled" in values:
            self._evaluation_enabled.setChecked(bool(values["evaluation_enabled"]))

        self._apply_settings_dict_values(values)
        self._sync_results_widgets()

        self._update_correction_visibility(self._correction_enabled.isChecked())

    def _load_prompts_and_language(self, values: dict) -> None:
        """Apply the correction/evaluation prompts and language combo from *values*.

        Sets the prompts BEFORE the language so that when the language combo fires
        ``currentTextChanged -> _on_language_changed``, it can correctly decide
        whether to overwrite the prompt (empty / preset) or keep it (custom text).
        """
        self._language.currentTextChanged.disconnect(self._on_language_changed)
        try:
            if "correction_prompt" in values:
                # Reset preset copies saved by older versions; keep real custom text.
                self._correction_prompt_text = migrate_saved_correction_prompt(
                    str(values["correction_prompt"])
                )
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

    @staticmethod
    def _with_local_ocr_engine(values: dict) -> dict:
        """Return *values* with an unavailable OCR engine (and its model) replaced.

        A settings file from an older version may name an online engine: fall back to the
        default engine and reset the model that went with it.
        """
        if "ocr_engine" not in values:
            return values
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
            "preprocess_method",
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

        if any(key.startswith("answer_format_") for key in values):
            formats = {
                key: values.get(key, self._settings_dict.get(key, DEFAULTS[key]))
                for key in ("answer_format_review", "answer_format_text", "answer_format_version")
            }
            migrate_answer_formats(formats)
            self._settings_dict["answer_format_review"] = str(formats["answer_format_review"])
            self._settings_dict["answer_format_text"] = str(formats["answer_format_text"])
            self._settings_dict["answer_format_version"] = int(formats["answer_format_version"])

        if "correction_level" in values:
            self._correction_level.setCurrentText(normalize_level(str(values["correction_level"])))
        if "correction_output" in values:
            self._settings_dict["correction_output"] = normalize_output_mode(
                str(values["correction_output"])
            )

        if "pdf_text_layer" in values:
            self._settings_dict["pdf_text_layer"] = normalize_pdf_text_layer(
                str(values["pdf_text_layer"])
            )

        for key in ("ocr_timeout", "pdf_render_dpi"):
            if key in values:
                self._settings_dict[key] = int(values[key])

        if "ocr_temperature" in values:
            self._settings_dict["ocr_temperature"] = float(values["ocr_temperature"])

        for key in (
            "preprocess_dewarp",
            "preprocess_deskew",
            "preprocess_border_crop",
            "preprocess_denoise",
            "preprocess_gamma",
            "debug",
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

    def lookup_config(self) -> Config:
        """Return a Config of the live panel values for requirement lookups.

        Unlike :meth:`to_config` it needs no valid input/output folder, so it also works
        at start-up and before a folder was chosen.  Requires :meth:`set_service`.
        """
        from ..application.service import ProcessingApplicationService  # noqa: PLC0415

        # _service is injected as object; narrowed to the concrete service type
        service: ProcessingApplicationService = self._service  # ty: ignore[invalid-assignment]
        config = service.build_lookup_config(
            Path(self.get_input_dir() or "."),
            Path(self._settings_dict.get("output_dir") or "."),
        )
        settings = self._settings_dict
        return dataclasses.replace(
            config,
            ocr=self._ocr_config_from_settings(),
            correction_enabled=self._correction_enabled.isChecked(),
            evaluation_enabled=self._evaluation_enabled.isChecked(),
            anonymization_enabled=self._anonymization_enabled.isChecked(),
            correction_provider=str(
                settings.get("correction_provider", config.correction_provider)
            ),
            correction_model=str(settings.get("correction_model", config.correction_model or "")),
            evaluate_provider=str(settings.get("evaluate_provider", config.evaluate_provider)),
            evaluate_model=str(settings.get("evaluate_model", config.evaluate_model or "")),
            ollama_url=str(settings.get("ollama_url", config.ollama_url)),
            language=self._language.currentText().strip(),
            # What a run would send: "What is sent for this file" and the status box read the
            # anonymizer rules and the instructions from here, so they must be the live ones.
            anonymizer_primary_model=settings.get("anonymizer_primary_model") or None,
            anonymizer_secondary_model=settings.get(
                "anonymizer_secondary_model", DEFAULTS["anonymizer_secondary_model"]
            )
            or SECONDARY_MODEL_NONE,
            anonymizer_patterns=settings.get("anonymizer_patterns"),
            correction_prompt=self._correction_prompt_text,
            correction_level=self._correction_level.currentText(),
            explanation_language=str(
                settings.get("explanation_language", DEFAULTS["explanation_language"])
            ),
            correction_output=str(settings.get("correction_output", DEFAULTS["correction_output"])),
            answer_format_review=str(settings.get("answer_format_review", "")),
            answer_format_text=str(settings.get("answer_format_text", "")),
            evaluate_prompt=self._evaluation_prompt_text,
        )

    @staticmethod
    def _stage_inputs(config: Config, stage: str) -> tuple[str, ...]:
        """Return the config values the requirements of *stage* depend on."""
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
        if error is not None or not isinstance(result, list) or not result:
            return  # all met (or the check itself failed: never block the UI on that)
        issues = [issue for issue in result if isinstance(issue, RequirementIssue)]
        checkbox = self._stage_checkbox(stage)
        if not checkbox.isChecked():
            return  # the user unticked it in the meantime
        if self._stage_inputs(self.lookup_config(), stage) != self._stage_inputs(config, stage):
            return  # the user chose another model/provider/server: this answer is outdated
        checkbox.blockSignals(True)
        checkbox.setChecked(False)
        checkbox.blockSignals(False)
        self.refresh_data_flow()  # signals were blocked: the box must be told itself
        if stage == "correction":
            self._settings_dict["correction_enabled"] = False
        elif stage == "evaluation":
            self._settings_dict["evaluation_enabled"] = False
        else:
            self._settings_dict["anonymization_enabled"] = False

        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415
        from ._model_guidance import PROVIDER_PREFIX  # noqa: PLC0415
        from .settings._page import (  # noqa: PLC0415
            FOCUS_LOCAL_MODELS,
            FOCUS_NAME_DETECTION,
            FOCUS_ONLINE_SERVICES,
        )

        focus = FOCUS_NAME_DETECTION if stage == "anonymizer" else FOCUS_LOCAL_MODELS
        # A missing Ollama model is ticked in Add-ons, as on the "model missing" path of Run.
        preselect: list[str] = []
        if stage in ("correction", "evaluation"):
            provider, model = (
                (config.correction_provider, config.effective_correction_model)
                if stage == "correction"
                else (config.evaluate_provider, config.effective_evaluate_model)
            )
            if provider == "ollama" and model:
                preselect.append(model)
        # A missing provider package is installed on the online-services tab.
        packages = [f"{PROVIDER_PREFIX}{issue.provider}" for issue in issues if issue.provider]
        if packages:
            focus, preselect = FOCUS_ONLINE_SERVICES, packages
        dlg = DependencyGuardDialog(stage, issues, self)
        dlg.open_downloads.connect(lambda: self.open_downloads_requested.emit(focus, preselect))
        dlg.exec()

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
            "correction_enabled": self._correction_enabled.isChecked(),
            "anonymization_enabled": self._anonymization_enabled.isChecked(),
            "correction_prompt": self._correction_prompt_text,
            "correction_level": self._correction_level.currentText(),
            "evaluate_prompt": self._evaluation_prompt_text,
            **self._settings_dict,
        }
        if values.get("anonymizer_secondary_model") is None:
            # None is skipped when writing the file, so an explicit "(none)" would be lost.
            values["anonymizer_secondary_model"] = SECONDARY_MODEL_NONE
        return values
