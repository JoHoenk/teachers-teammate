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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..application.composition import AppServices
from ..application.correction_options import (
    evaluation_fixed_instructions,
    proofreading_prompts,
)
from ..application.data_flow import ticked_steps
from ..config import DEFAULTS, Config
from ..domain.correction_policy import LEVELS
from ..domain.correction_prompts import NOTES_LIMIT
from ..domain.data_location import (
    ENGINE_OLLAMA,
    STAGE_ANONYMIZATION,
    DataFlow,
)
from ..domain.output_files import folder_problem
from ..domain.stages import Stage
from ..settings_schema import SECONDARY_MODEL_NONE, normalize_notes, normalize_settings
from ..texts import dialogs as dlg
from ..texts import review as txt
from ..texts.privacy import names_line, same_model_line, uses_line, uses_tooltip
from ..texts.settings import WHERE_ADDONS, WHERE_AI_MODELS
from . import _anonymizer_settings
from ._constants import _LANGUAGES
from ._data_flow_summary import DataFlowSummary, UsesLine
from ._input_selection import InputSelection, SelectionHooks
from ._prompt_editor import PromptEditor
from ._results_step import (
    ResultsHooks,
    ResultsStep,
    ResultsValues,
    start_folder,
)
from ._stage_guard import GuardHooks, StageGuard
from ._step_section import separator, step_header
from ._types import SettingsDict
from .settings._page import FOCUS_OCR, FOCUS_PROOFREADING, FOCUS_REVIEW, SettingsPage

#: The settings keys a widget shows: loading them means setting the widget, not the dict.
_WIDGET_KEYS = frozenset(
    {
        "input",
        "output",
        "language",
        "correction_level",
        "correction_notes",
        "evaluate_prompt",
        "recognition_enabled",
        "correction_enabled",
        "anonymization_enabled",
        "evaluation_enabled",
    }
)

#: Keys a settings file may hold that the panel does not keep: the GUI always searches
#: subfolders and has no toggle for it, so it never writes the key either.
_UNSTORED_KEYS = frozenset({"recursive"})

#: The settings key each stage the requirement guard can untick is stored under.
_STAGE_KEYS = {
    Stage.OCR: "recognition_enabled",
    Stage.CORRECTION: "correction_enabled",
    Stage.EVALUATION: "evaluation_enabled",
    "anonymizer": "anonymization_enabled",
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
        self._service: AppServices | None = None
        # True while load_from_dict applies saved state: stage requirement guards only
        # make sense for a user ticking a box, not for restoring settings at startup.
        self._loading = False
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumWidth(340)
        self.setMaximumWidth(420)

        # The one store of every setting: the widgets write their key here on change, and
        # ``to_dict`` is this dict plus the input folder.
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
            "language": DEFAULTS["language"],
            "correction_provider": DEFAULTS["correction_provider"],
            "correction_model": DEFAULTS["correction_model"],
            "correction_level": DEFAULTS["correction_level"],
            "correction_notes": DEFAULTS["correction_notes"],
            "explanation_language": DEFAULTS["explanation_language"],
            "correction_output": DEFAULTS["correction_output"],
            "evaluate_provider": DEFAULTS["evaluate_provider"],
            "evaluate_model": DEFAULTS["evaluate_model"],
            # "" = the built-in instructions; the default text is never copied here, so a
            # code-side change keeps being recognised.
            "evaluate_prompt": "",
            "output": "",
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

        inner = QWidget()
        self._layout = QVBoxLayout(inner)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._layout.setSpacing(6)

        self._data_flow: DataFlow | None = None
        self._data_flow_summary = DataFlowSummary()
        self._selection = InputSelection(
            SelectionHooks(changed=self.paths_changed.emit, dialog_parent=self), self
        )
        self._results = ResultsStep(self._results_hooks(), self)
        self._stage_guard = StageGuard(self._guard_hooks(), self)
        self._layout.addWidget(self._data_flow_summary)
        self._layout.addWidget(separator())
        self._build_step1_select_input()
        self._layout.addWidget(separator())
        self._build_step2_language()
        self._layout.addWidget(separator())
        self._build_step3_what_should_happen()
        self._layout.addWidget(separator())
        self._build_step4_results()
        self._layout.addStretch()

        self.setWidget(inner)

        self._language.currentTextChanged.connect(self._on_language_changed)
        self._correction_level.currentTextChanged.connect(self._on_level_changed)
        self._on_language_changed(self._language.currentText())

        self._update_correction_visibility()

    # ── hooks ─────────────────────────────────────────────────────────────────────

    def _results_hooks(self) -> ResultsHooks:
        """Return what the Results step asks of the panel."""
        return ResultsHooks(
            changed=self._on_results_widget_changed,
            folder_changed=self._on_output_dir_edited,
            start_dir=lambda: start_folder(self.get_input_dir()),
            dialog_parent=self,
        )

    # ── section builders ──────────────────────────────────────────────────────────

    def _build_step1_select_input(self) -> None:
        self._layout.addWidget(
            step_header(
                "1  Documents",
                "Choose the documents you want to process.\n"
                "You can pick individual files or a whole folder.\n"
                "Subfolders are included automatically.",
            )
        )
        self._layout.addWidget(self._selection.row)

    def _build_step2_language(self) -> None:
        self._layout.addWidget(
            step_header(
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
            step_header(
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
            step_header(
                "4  Results",
                "Where the results are saved, and which files a run creates there.",
            )
        )
        self._layout.addWidget(self._results.root)

    # ── results step ──────────────────────────────────────────────────────────────

    def _on_results_widget_changed(self) -> None:
        """Store the Results choices and tell the main window."""
        self._store_results(self._results.values())
        if not self._loading:
            self.results_changed.emit()

    def _on_output_dir_edited(self) -> None:
        """Store a new results folder, which the data-flow box depends on."""
        values = self._results.values()
        if values.output == str(self._settings_dict.get("output", "") or "").strip():
            return  # the folder was chosen again, nothing to store
        self._store_results(values)
        self.refresh_data_flow()
        if not self._loading:
            self.results_changed.emit()

    def _store_results(self, values: ResultsValues) -> None:
        """Write the Results step into the settings dict, which is the one store."""
        self._settings_dict.update(cast("SettingsDict", values.as_settings()))

    def _settings(self) -> dict[str, Any]:
        """Return the settings dict as a plain dict, for helpers that take one by key."""
        return cast("dict[str, Any]", self._settings_dict)

    def results_problem(self) -> bool:
        """Return whether a file output is on but no folder to write it to is chosen."""
        return self._results.problem()

    def choose_output_dir(self) -> None:
        """Ask for the results folder (the "…" button and the readiness line's "Choose…")."""
        self._results.choose()

    # ── input selection ───────────────────────────────────────────────────────────

    def select_folder(self) -> None:
        """Ask for an input folder (the "Select Folder…" button and File → Open Folder…)."""
        self._selection.choose_folder()

    def select_files(self) -> None:
        """Ask for input files (the "Select Files…" button and File → Open Files…)."""
        self._selection.choose_files()

    # ── prompt dialogs ────────────────────────────────────────────────────────────

    def _proofreading_prompts(self, corrections_only: bool) -> tuple[tuple[str, str], ...]:
        """Return the prompts the AI is asked for, for the dialog's read-only section."""
        config = dataclasses.replace(
            self.lookup_config(),
            correction_output="text" if corrections_only else "review",
        )
        return proofreading_prompts(config)

    def _on_configure_correction_prompt(self) -> None:
        settings = self._settings_dict
        dialog = _ProofreadingDialog(
            txt.CORRECTION_PROMPT_DIALOG_TITLE,
            self.get_correction_notes(),
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
        return txt.formatted_text_note(evaluation_fixed_instructions("markdown"))

    def _on_configure_evaluation_prompt(self) -> None:
        dialog = _PromptDialog(
            txt.EVALUATION_PROMPT_DIALOG_TITLE,
            self.get_evaluation_prompt(),
            self,
            builtin_text=str(DEFAULTS["evaluate_prompt"]),
            fixed_text=self._evaluation_fixed_text(),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_evaluation_prompt(dialog.prompt())
            self.instructions_changed.emit()

    # ── language change ───────────────────────────────────────────────────────────

    def _on_language_changed(self, _language: str) -> None:
        """Store the language and re-render what it decides: the models and the data flow."""
        self._settings_dict["language"] = self._language.currentText().strip()
        self.refresh_data_flow()

    def _on_level_changed(self, level: str) -> None:
        """Store the CEFR level the teacher picked."""
        self._settings_dict["correction_level"] = level

    # ── visibility updates ────────────────────────────────────────────────────────

    def refresh_data_flow(self) -> None:
        """Re-render the "where are my documents processed" box for the live settings.

        Does nothing while settings are being loaded: :meth:`load_from_dict` renders once
        at the end instead of once per restored checkbox.
        """
        if self._service is None or self._loading:
            return
        service = self._service
        config = self.lookup_config()
        # Ask once: the box shows the ticked steps, the lines under the steps show every step.
        steps = service.data_flow.describe_steps(config)
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
        ocr = steps.stage(Stage.OCR)
        # Only Ollama has a model; the built-in reader is named by the engine itself.
        ocr_model = config.ocr.model if config.ocr.engine == ENGINE_OLLAMA else ""
        self._ocr_uses.set_line(
            uses_line(ocr, ocr_model, enabled=config.recognition_enabled) if ocr else "",
            uses_tooltip(ocr) if ocr else "",
        )
        names = steps.stage(STAGE_ANONYMIZATION)
        self._anonymization_uses.set_line(names_line(names) if names else "")

        proofread = steps.stage(Stage.CORRECTION)
        proofread_on = self._correction_enabled.isChecked()
        self._correction_uses.set_line(
            uses_line(proofread, config.effective_correction_model, enabled=proofread_on)
            if proofread
            else "",
            uses_tooltip(proofread) if proofread else "",
        )
        review = steps.stage(Stage.EVALUATION)
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
            self._guard_stage(Stage.OCR)  # switched on: the engine has to be there

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
        self._settings_dict["correction_enabled"] = checked
        self._sync_review_enabled()
        self.refresh_data_flow()
        if not checked:
            return
        self._correction_prompt_btn.setEnabled(True)
        # The review comes back with proofreading, so its requirements count again: they are
        # checked once proofreading's are met (see ``_stage_guard``), so a
        # problem both share is explained once, by proofreading.
        self._guard_stage(Stage.CORRECTION)

    def _sync_review_enabled(self) -> None:
        """Enable "Review content" only while "Proofread" is ticked.

        The pipeline drops a review without proofreading, so the box would look "on" and do
        nothing.  The tick is kept (and saved): the teacher finds it again with proofreading.
        """
        proofreading = self._correction_enabled.isChecked()
        self._evaluation_enabled.setEnabled(proofreading)
        self._evaluation_enabled.setToolTip("" if proofreading else txt.REVIEW_NEEDS_PROOFREADING)

    def _on_evaluation_enabled_changed(self, checked: bool) -> None:
        self._settings_dict["evaluation_enabled"] = checked
        self.refresh_data_flow()
        if checked:
            self._guard_stage(Stage.EVALUATION)

    def set_anonymization_enabled(self, enabled: bool) -> None:
        """Tick or untick "Remove personal info" as the teacher would.

        The normal ``toggled`` path does the rest: the settings dict, the data-flow box and the
        stage guard (which unticks the box again if name detection is missing).
        """
        self._anonymization_enabled.setChecked(enabled)

    def _on_anonymization_changed(self, checked: bool = True) -> None:
        self._settings_dict["anonymization_enabled"] = checked
        self.refresh_data_flow()
        if checked:
            self._guard_stage("anonymizer")

    def build_anonymizer_config(self) -> object:
        """Build an :class:`AnonymizerConfig` from the current settings dict.

        Centralised here so callers (this panel and the main window's privacy
        preview) do not each rebuild the config from raw settings keys.
        """
        return _anonymizer_settings.build(self._settings_dict)

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
        _anonymizer_settings.store(self._settings(), config)

    # ── public API ────────────────────────────────────────────────────────────────

    def get_input_dir(self) -> str:
        """Return the current input directory as a string.

        Returns the selected folder, or the common parent of selected files, or empty string.
        """
        return self._selection.folder()

    def set_input_dir(self, path: str) -> None:
        """Set folder-mode input (e.g. from drag-and-drop). Clears any explicit file selection."""
        self._selection.set_folder(path)
        self.paths_changed.emit()

    def get_selected_files(self) -> list[Path] | None:
        """Return the explicitly selected file list, or None when using folder-based discovery."""
        return self._selection.files()

    def set_selected_files(self, paths: list[Path]) -> None:
        """Set an explicit list of input files (e.g. from drag-and-drop of individual files)."""
        self._selection.set_files(paths)
        self.paths_changed.emit()

    def get_correction_notes(self) -> str:
        """Return the notes the teacher added for the AI."""
        return str(self._settings_dict.get("correction_notes", ""))

    def set_correction_notes(self, text: str) -> None:
        """Update the notes for the AI (as the options dialog does on OK)."""
        self._settings_dict["correction_notes"] = normalize_notes(text)

    def get_evaluation_prompt(self) -> str:
        """Return the current evaluation system prompt text."""
        return str(self._settings_dict.get("evaluate_prompt", ""))

    def set_evaluation_prompt(self, text: str) -> None:
        """Update the evaluation system prompt (as the instructions dialog does on OK)."""
        self._settings_dict["evaluate_prompt"] = text

    def get_output_dir(self) -> str:
        """Return the configured output directory (set in the Results step)."""
        return str(self._settings_dict.get("output", "")).strip()

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
        self._results.apply(ResultsValues.from_settings(self._settings_dict))
        self.refresh_data_flow()

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
        values = self.to_dict()
        input_text = str(values.get("input", ""))
        output_text = str(values.get("output", ""))
        input_dir = Path(input_text) if input_text else Path(".")
        if strict:
            problem = folder_problem(
                input_text,
                output_text,
                input_exists=input_dir.is_dir(),
                writes_output_files=bool(
                    values.get("docx_enabled")
                    or values.get("text_enabled")
                    or values.get("text_anonymized_enabled")
                ),
            )
            if problem is not None:
                raise ValueError(dlg.folder_problem_text(problem, input_text=input_text))
        # A review without proofreading does not run, so the config says so: the data-flow box,
        # the preflight, the model list and the memory check all read this one config.
        values["evaluation_enabled"] = self.review_will_run()
        return Config.from_settings(
            values,
            input_dir=input_dir,
            output_dir=Path(output_text) if output_text else input_dir,
            # The GUI is intentionally always-recursive; no toggle is exposed.
            recursive=True,
        )

    @staticmethod
    def _set_combo(combo: QComboBox, value: str) -> None:
        idx = combo.findText(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif combo.isEditable():
            combo.setCurrentText(value)  # a language typed in by hand

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
        loaded = normalize_settings(values).values
        if "input" in loaded:
            path = Path(str(loaded["input"]))
            if path.is_dir():
                self._selection.set_folder(path)
        if "output" in loaded:
            self._settings_dict["output"] = str(loaded["output"])

        self._load_prompts_and_language(loaded)

        if "recognition_enabled" in loaded:
            self._recognition_enabled.setChecked(bool(loaded["recognition_enabled"]))
            self._settings_dict["recognition_enabled"] = self._recognition_enabled.isChecked()
        if "correction_enabled" in loaded:
            self._correction_enabled.setChecked(bool(loaded["correction_enabled"]))
        if "anonymization_enabled" in loaded:
            self._anonymization_enabled.setChecked(bool(loaded["anonymization_enabled"]))
        if "evaluation_enabled" in loaded:
            self._evaluation_enabled.setChecked(bool(loaded["evaluation_enabled"]))

        self._apply_settings_dict_values(loaded)
        self._results.apply(ResultsValues.from_settings(self._settings_dict))

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
                self._settings_dict["correction_notes"] = values["correction_notes"]
            if "evaluate_prompt" in values:
                # "" = the built-in default; a saved copy of it means the same.
                self._settings_dict["evaluate_prompt"] = values["evaluate_prompt"]
            if "correction_level" in values:
                self._set_combo(self._correction_level, str(values["correction_level"]))
            if "language" in values:
                self._set_combo(self._language, str(values["language"]))
        finally:
            self._language.currentTextChanged.connect(self._on_language_changed)
        self._on_language_changed(self._language.currentText())

    def _apply_settings_dict_values(self, values: dict) -> None:
        """Copy the keys *values* holds into the settings dict, except the ones a widget shows."""
        for key, value in values.items():
            if key in _WIDGET_KEYS or key in _UNSTORED_KEYS:
                continue
            self._settings_dict[key] = value

    def set_service(self, service: AppServices) -> None:
        """Inject the application service so the panel can run requirement checks."""
        self._service = service
        self.refresh_data_flow()

    def _stage_checkbox(self, stage: str) -> QCheckBox:
        """Return the enable checkbox of pipeline *stage*."""
        if stage == Stage.OCR:
            return self._recognition_enabled
        if stage == Stage.CORRECTION:
            return self._correction_enabled
        if stage == Stage.EVALUATION:
            return self._evaluation_enabled
        return self._anonymization_enabled

    def _guard_hooks(self) -> GuardHooks:
        """Return what the requirement guard asks of the panel."""
        return GuardHooks(
            service=lambda: self._service,
            config=self.lookup_config,
            loading=lambda: self._loading,
            checkbox=self._stage_checkbox,
            unticked=self._on_stage_unticked,
            language=self.lookup_language,
            open_settings=self._open_settings_page,
            dialog_parent=self,
        )

    def _open_settings_page(self, page: SettingsPage, focus: str, preselect: list[str]) -> None:
        """Have the main window open *page* on *focus*, with the *preselect* entries chosen."""
        self.open_settings_requested.emit(page, focus, preselect)

    def _guard_stage(self, stage: str) -> None:
        """Check the requirements of *stage* after the user enabled it, off the GUI thread.

        The box stays ticked meanwhile; if a requirement is unmet the guard unticks it and
        explains why.  Nothing happens while saved settings are being loaded.
        """
        self._stage_guard.check(stage)

    def _on_stage_unticked(self, stage: str) -> None:
        """Store the unticked *stage* and re-render what its state decides."""
        self._settings()[_STAGE_KEYS[stage]] = False
        self._sync_review_enabled()
        self.refresh_data_flow()  # signals were blocked: the box must be told itself

    def restore_defaults(self) -> None:
        self.load_from_dict(DEFAULTS)

    def to_dict(self) -> dict:
        """Return current values as a TOML-compatible settings dict.

        The settings dict plus the input folder: every widget writes its key there, so this is
        the whole state of the panel.  Note: the GUI is intentionally always-recursive (see
        :meth:`to_config`), so ``recursive`` is deliberately not persisted — writing it would
        imply a toggle the GUI does not expose.
        """
        values = dict(self._settings_dict)
        values["input"] = self.get_input_dir()
        if values.get("anonymizer_secondary_model") is None:
            # None is skipped when writing the file, so an explicit "(none)" would be lost.
            values["anonymizer_secondary_model"] = SECONDARY_MODEL_NONE
        return values
