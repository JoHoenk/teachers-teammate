"""The anonymizer's NER passes and regex patterns, as a widget for the Privacy settings page."""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..application.composition import AppServices
from ..domain.anonymization import (
    SECONDARY_MODEL_AUTO,
    SECONDARY_MODEL_NONE,
    AnonymizerConfig,
    pattern_problem,
)
from ..domain.local_models import PRIVACY_ADDON_ID
from ..messages import log_failure
from ..texts import dialogs as dlg
from ..texts import privacy
from ._background import LatestTask
from ._errors import show_notice

_logger = logging.getLogger(__name__)

_INVALID_BG = QColor("#ffd0d0")


class AnonymizerConfigWidget(QWidget):
    """Configure the anonymizer's NER passes and regex patterns.

    * **Primary model** — the spaCy model used for the first NER pass.  Defaults
      to the language-specific model derived from the document language; can be
      overridden with any installed model (e.g. ``xx_ent_wiki_sm``).
    * **Secondary model** — an optional additional pass to catch names the
      primary model misses.  Leave as ``(none)`` to skip.
    * **Regex patterns** — applied after NER (customise the built-in patterns or add
      your own).

    Invalid patterns cannot be saved: :attr:`validity_changed` tells the owner (the
    Settings window disables its OK button meanwhile).

    Args:
        language: Document language — used to derive the primary NER model label
            and as input to the preview.
        anonymizer_config: Current
            :class:`~teachers_teammate.domain.anonymization.AnonymizerConfig`.
        app_service: Application service used to run the preview.
        parent: Parent widget.
        sample_text: Optional text to pre-fill the preview input.
    """

    #: ``True`` when every pattern is a valid regex (the settings can be saved).
    validity_changed = Signal(bool)

    def __init__(
        self,
        language: str,
        anonymizer_config: AnonymizerConfig | None,
        app_service: AppServices,
        parent: QWidget | None = None,
        *,
        sample_text: str = "",
    ) -> None:
        super().__init__(parent)
        self._language = language
        self._app_service = app_service
        self._valid = True

        config: AnonymizerConfig = (
            anonymizer_config
            if isinstance(anonymizer_config, AnonymizerConfig)
            else AnonymizerConfig()
        )
        # Only the newest discovery counts; shutdown() drops the answers still on their way.
        self._spacy_task = LatestTask(self)
        # Store configured model names so _on_spacy_models_fetched can re-select them
        self._configured_primary = config.primary_model
        self._configured_secondary = config.secondary_model

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        accuracy_warning = QLabel(privacy.ANONYMISATION_NOTE)
        accuracy_warning.setWordWrap(True)
        accuracy_warning.setTextFormat(Qt.TextFormat.RichText)
        accuracy_warning.setStyleSheet(
            "background: #fff3cd; color: #664d03; border: 1px solid #ffca2c; "
            "border-radius: 4px; padding: 6px; font-size: 11px;"
        )
        root.addWidget(accuracy_warning)

        self._tabs = QTabWidget()
        root.addWidget(self._tabs, stretch=1)

        # ── Detection tab: model selection + preview ───────────────────────────
        detection_tab = QWidget()
        top_layout = QVBoxLayout(detection_tab)
        top_layout.setContentsMargins(6, 6, 6, 6)
        top_layout.setSpacing(6)
        # The Privacy page puts its own rows above the detection models.
        self._detection_layout = top_layout

        self._build_detection_models(top_layout)

        # ── Preview (lives on the Detection tab) ───────────────────────────────
        self._build_preview(top_layout)

        self._tabs.addTab(detection_tab, privacy.DETECTION_TAB)
        self._tabs.addTab(self._build_regex_tab(), privacy.PATTERNS_TAB)

        # Populate patterns table from config
        self._set_patterns(config.patterns)

        if sample_text:
            self._sample_input.setPlainText(sample_text)

        # Start async spaCy model discovery — populates both combos off the main thread
        self._start_spacy_fetch()

    def _build_detection_models(self, layout: QVBoxLayout) -> None:
        """Build the primary/secondary spaCy model rows on the Detection tab's *layout*."""
        # Primary model row
        primary_row = QHBoxLayout()
        primary_row.addWidget(QLabel(privacy.PRIMARY_MODEL_LABEL))
        self._primary_model = QComboBox()
        self._primary_model.setEnabled(False)
        self._primary_model.addItem(privacy.MODEL_DETECTING, userData="__loading__")
        primary_row.addWidget(self._primary_model, stretch=1)
        layout.addLayout(primary_row)

        primary_hint = QLabel(privacy.PRIMARY_MODEL_HINT)
        primary_hint.setWordWrap(True)
        primary_hint.setTextFormat(Qt.TextFormat.RichText)
        primary_hint.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(primary_hint)

        sep0 = QFrame()
        sep0.setFrameShape(QFrame.Shape.HLine)
        sep0.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep0)

        # Secondary model row
        secondary_row = QHBoxLayout()
        secondary_row.addWidget(QLabel(privacy.EXTRA_MODEL_LABEL))
        self._secondary_model = QComboBox()
        self._secondary_model.setEnabled(False)
        self._secondary_model.addItem(privacy.MODEL_DETECTING, userData="__loading__")
        secondary_row.addWidget(self._secondary_model, stretch=1)
        layout.addLayout(secondary_row)

        secondary_hint = QLabel(privacy.EXTRA_MODEL_HINT)
        secondary_hint.setWordWrap(True)
        secondary_hint.setTextFormat(Qt.TextFormat.RichText)
        secondary_hint.setStyleSheet("color: #555; font-size: 11px;")
        layout.addWidget(secondary_hint)

        self._spacy_status_lbl = QLabel()
        self._spacy_status_lbl.setStyleSheet("color: #c0392b; font-size: 11px;")
        self._spacy_status_lbl.setVisible(False)
        layout.addWidget(self._spacy_status_lbl)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep1)

    def _build_regex_tab(self) -> QWidget:
        """Build and return the Regex Patterns tab widget."""
        regex_tab = QWidget()
        regex_layout = QVBoxLayout(regex_tab)
        regex_layout.setContentsMargins(6, 6, 6, 6)
        regex_layout.setSpacing(4)

        regex_layout.addWidget(QLabel(privacy.PATTERNS_INTRO))

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(privacy.PATTERN_COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellChanged.connect(self._on_cell_changed)
        regex_layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton(privacy.PATTERNS_ADD_ROW)
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton(privacy.PATTERNS_REMOVE_SELECTED)
        remove_btn.clicked.connect(self._remove_selected)
        restore_btn = QPushButton(privacy.PATTERNS_RESTORE_DEFAULTS)
        restore_btn.setToolTip(privacy.PATTERNS_RESTORE_TOOLTIP)
        restore_btn.clicked.connect(self._restore_default_patterns)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        btn_row.addWidget(restore_btn)
        regex_layout.addLayout(btn_row)

        self._error_label = QLabel()
        self._error_label.setStyleSheet("color: #c0392b; font-size: 11px;")
        self._error_label.setVisible(False)
        regex_layout.addWidget(self._error_label)
        return regex_tab

    def _build_preview(self, layout: QVBoxLayout) -> None:
        """Build the sample-text preview panel on the Detection tab's *layout*."""
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep2)

        layout.addWidget(QLabel(privacy.PREVIEW_INTRO))

        preview_cols = QHBoxLayout()

        input_col = QVBoxLayout()
        input_col.addWidget(QLabel(privacy.PREVIEW_SAMPLE_LABEL))
        self._sample_input = QPlainTextEdit()
        self._sample_input.setPlaceholderText(privacy.PREVIEW_SAMPLE_PLACEHOLDER)
        input_col.addWidget(self._sample_input, stretch=1)
        preview_cols.addLayout(input_col)

        output_col = QVBoxLayout()
        output_col.addWidget(QLabel(privacy.PREVIEW_OUTPUT_LABEL))
        self._sample_output = QPlainTextEdit()
        self._sample_output.setReadOnly(True)
        output_col.addWidget(self._sample_output, stretch=1)
        preview_cols.addLayout(output_col)

        layout.addLayout(preview_cols, stretch=1)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton(privacy.PREVIEW_RUN)
        self._run_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._run_btn.clicked.connect(self._run_preview)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        layout.addLayout(run_row)

        layout.addWidget(QLabel(privacy.PREVIEW_MAP_LABEL))
        self._mapping_table = QTableWidget(0, 2)
        self._mapping_table.setHorizontalHeaderLabels(privacy.MAP_COLUMNS)
        self._mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._mapping_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._mapping_table.setMaximumHeight(120)
        layout.addWidget(self._mapping_table)

        self._preview_error = QLabel()
        self._preview_error.setStyleSheet("color: #c0392b;")
        self._preview_error.setWordWrap(True)
        self._preview_error.setVisible(False)
        layout.addWidget(self._preview_error)

    # ── Async model population ─────────────────────────────────────────────────

    def _on_spacy_fetch_error(self, msg: str) -> None:
        """Show a warning label when spaCy model discovery fails."""
        self._spacy_status_lbl.setText(privacy.spacy_fetch_failed(msg))
        self._spacy_status_lbl.setVisible(True)

    def _on_spacy_models_fetched(self, models: list[str]) -> None:
        """Populate the primary and secondary model combos after the background fetch."""
        # Derive the auto label for the primary combo
        try:
            auto_model = self._app_service.anonymization.spacy_model_for_language(self._language)
        except Exception:  # noqa: BLE001
            # model lookup is best-effort; fall back to the generic auto label
            auto_model = ""
        auto_label = privacy.model_auto_primary(auto_model)

        # Primary combo: Auto + installed models
        self._primary_model.clear()
        self._primary_model.addItem(auto_label, userData=None)
        for m in models:
            self._primary_model.addItem(m, userData=m)
        if self._configured_primary and self._configured_primary in models:
            idx = self._primary_model.findData(self._configured_primary)
            self._primary_model.setCurrentIndex(idx)
        self._primary_model.setEnabled(True)

        # Secondary combo: auto (system language), (none), then the installed models
        self._secondary_model.clear()
        system_model = self._system_spacy_model()
        auto_secondary = privacy.model_auto_secondary(system_model, auto_model)
        self._secondary_model.addItem(auto_secondary, userData=SECONDARY_MODEL_AUTO)
        # An explicit "(none)" is stored as no model at all, so an earlier choice is honoured.
        self._secondary_model.addItem(privacy.MODEL_NO_SECONDARY, userData=None)
        for m in models:
            self._secondary_model.addItem(m, userData=m)
        self._select_secondary(self._configured_secondary, models)
        self._secondary_model.setEnabled(True)

    def _system_spacy_model(self) -> str | None:
        """Return the spaCy model for the language this computer is set to (if it is known)."""
        try:
            return self._app_service.anonymization.system_spacy_model()
        except Exception:  # noqa: BLE001  # a missing spaCy must not break the dialog
            return None

    def _select_secondary(self, configured: str | None, models: list[str]) -> None:
        """Select the combo entry that *configured* stands for (auto, none or a model)."""
        if configured == SECONDARY_MODEL_AUTO:
            self._secondary_model.setCurrentIndex(0)  # "Auto (system language)"
            return
        if configured and configured != SECONDARY_MODEL_NONE and configured in models:
            self._secondary_model.setCurrentIndex(self._secondary_model.findData(configured))
            return
        # None, "none", or a model that is no longer installed: no extra pass.
        self._secondary_model.setCurrentIndex(self._secondary_model.findData(None))

    # ── Table helpers ──────────────────────────────────────────────────────────

    def _append_row(self, tag: str = "", pattern: str = "") -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._table.setItem(row, 0, QTableWidgetItem(tag))
        self._table.setItem(row, 1, QTableWidgetItem(pattern))

    def _add_row(self) -> None:
        self._append_row()
        self._table.scrollToBottom()
        self._table.setCurrentCell(self._table.rowCount() - 1, 0)
        self._table.editItem(self._table.currentItem())

    def _remove_selected(self) -> None:
        rows = sorted({idx.row() for idx in self._table.selectedIndexes()}, reverse=True)
        for row in rows:
            self._table.removeRow(row)
        self._revalidate_all()

    def _restore_default_patterns(self) -> None:
        from ..domain.anonymization import DEFAULT_PATTERNS  # noqa: PLC0415

        self._set_patterns(DEFAULT_PATTERNS)

    def _set_patterns(self, patterns) -> None:
        """Replace the table rows with *patterns* (``(tag, regex)`` pairs) and validate them."""
        self._loading = True
        self._table.setRowCount(0)
        for tag, pattern in patterns:
            self._append_row(tag, pattern)
        self._loading = False
        self._revalidate_all()

    # ── Validation ─────────────────────────────────────────────────────────────

    def _on_cell_changed(self, _row: int, _col: int) -> None:
        if self._loading:
            return
        self._revalidate_all()

    def _row_texts(self, row: int) -> tuple[str, str]:
        """Return the stripped ``(tag, pattern)`` texts of *row*."""
        tag_item = self._table.item(row, 0)
        pattern_item = self._table.item(row, 1)
        return (
            tag_item.text().strip() if tag_item else "",
            pattern_item.text().strip() if pattern_item else "",
        )

    def _validate_row(self, row: int) -> bool:
        """Mark *row* as valid or not; a row with a blank tag or pattern is skipped on save."""
        items = [self._table.item(row, col) for col in (0, 1)]
        tag, pattern = self._row_texts(row)
        problem = None
        if tag and pattern:
            others = {
                other_tag
                for other in range(self._table.rowCount())
                if other != row
                for other_tag, other_pattern in [self._row_texts(other)]
                if other_tag and other_pattern
            }
            problem = pattern_problem(tag, pattern, others)
        for item in items:
            if item is not None:
                item.setBackground(_INVALID_BG if problem else self._table.palette().base())
                item.setToolTip(privacy.invalid_pattern_row(problem) if problem else "")
        return problem is None

    def _revalidate_all(self) -> None:
        self._loading = True
        for row in range(self._table.rowCount()):
            self._validate_row(row)
        self._loading = False
        self._update_error_summary()
        self._update_ok_button()

    def _invalid_count(self) -> int:
        count = 0
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 1)
            if item and item.background().color() == _INVALID_BG:
                count += 1
        return count

    def _update_error_summary(self) -> None:
        n = self._invalid_count()
        if n:
            self._error_label.setText(privacy.invalid_patterns(n))
            self._error_label.setVisible(True)
        else:
            self._error_label.setVisible(False)

    def _update_ok_button(self) -> None:
        """Tell the owner whether the patterns can be saved (no invalid regex)."""
        valid = self._invalid_count() == 0
        if valid != self._valid:
            self._valid = valid
            self.validity_changed.emit(valid)

    # ── Preview ────────────────────────────────────────────────────────────────

    def _run_preview(self) -> None:
        self._preview_error.setVisible(False)
        self._sample_output.clear()
        self._mapping_table.setRowCount(0)

        text = self._sample_input.toPlainText()
        if not text.strip():
            return

        try:
            anonymized, mapping = self._app_service.anonymization.anonymize_text(
                text, self._language, self.anonymizer_config
            )
        except ImportError as exc:
            self._preview_error.setText(
                privacy.spacy_missing(str(exc), installed_app=bool(getattr(sys, "frozen", False)))
            )
            self._preview_error.setVisible(True)
            return
        except OSError:
            from ._addon_installer_dialog import SpacyModelDownloadDialog  # noqa: PLC0415

            needed = self._app_service.anonymization.spacy_model_for_language(self._language)
            if SpacyModelDownloadDialog.is_available():
                self._open_download_dialog(needed)
            else:
                self._preview_error.setText(privacy.spacy_model_not_downloaded(needed))
                self._preview_error.setVisible(True)
            return
        except Exception as exc:  # noqa: BLE001
            # anonymization preview may raise anything; show the error in the panel
            log_failure(_logger, "The anonymization preview failed", exc)
            self._preview_error.setText(privacy.preview_failed(str(exc)))
            self._preview_error.setVisible(True)
            return

        self._sample_output.setPlainText(anonymized)

        self._mapping_table.setRowCount(len(mapping))
        for row, (placeholder, original) in enumerate(mapping.items()):
            self._mapping_table.setItem(row, 0, QTableWidgetItem(placeholder))
            self._mapping_table.setItem(row, 1, QTableWidgetItem(original))

    # ── spaCy model download ───────────────────────────────────────────────────

    def _open_download_dialog(self, needed_model: str) -> None:
        """Open the spaCy model download dialog for *needed_model*.

        If the privacy addon (spaCy) is not yet installed, shows an info message
        instead.  On successful download the model combos are refreshed and the
        preview is re-run automatically.
        """
        from ._addon_installer_dialog import SpacyModelDownloadDialog  # noqa: PLC0415

        if not self._app_service.addons.is_addon_available(PRIVACY_ADDON_ID):
            show_notice(self.window(), dlg.spacy_not_installed())
            return

        dialog = SpacyModelDownloadDialog(needed_model, self.window(), service=self._app_service)
        dialog.model_downloaded.connect(self._on_model_downloaded)
        dialog.exec()

    def _on_model_downloaded(self, _model: str) -> None:
        """Refresh model combos after a successful download and re-run the preview."""
        self.reload_models()
        # Re-run the preview once the fetch completes (deferred so exec() has returned)
        QTimer.singleShot(50, self._run_preview)

    def _start_spacy_fetch(self) -> None:
        """Ask for the installed spaCy models off the GUI thread.

        An answer that arrives after the widget was closed is dropped with it.
        """

        def fetched() -> list[str]:
            return self._app_service.anonymization.list_installed_spacy_models()

        def arrived(models: object, error: BaseException | None) -> None:
            if error is not None:
                self._on_spacy_fetch_error(str(error))
                self._on_spacy_models_fetched([])
                return
            self._on_spacy_models_fetched(list(models) if isinstance(models, list) else [])

        self._spacy_task.submit(fetched, arrived)

    # ── Public API ─────────────────────────────────────────────────────────────

    def reload_models(self) -> None:
        """Look for installed spaCy models again, keeping the current choices."""
        self._primary_model.setEnabled(False)
        self._secondary_model.setEnabled(False)
        self._configured_secondary = self._loaded_choice(
            self._secondary_model.currentData(), self._configured_secondary
        )
        self._configured_primary = self._loaded_choice(
            self._primary_model.currentData(), self._configured_primary
        )
        self._start_spacy_fetch()

    @staticmethod
    def _loaded_choice(data: object, configured: str | None) -> str | None:
        """Return the combo's choice, or the configured one while the combo is still loading."""
        if data == "__loading__":
            return configured
        return data if isinstance(data, str) else None

    def set_config(self, config: AnonymizerConfig | None) -> None:
        """Show *config*: models and patterns."""
        if not isinstance(config, AnonymizerConfig):
            return
        self._configured_primary = config.primary_model
        self._configured_secondary = config.secondary_model
        self._set_patterns(config.patterns)
        if self._primary_model.isEnabled():
            # The models are known already: select the new choices among them.
            models = [
                str(self._primary_model.itemData(i)) for i in range(1, self._primary_model.count())
            ]
            self._on_spacy_models_fetched(models)

    def is_valid(self) -> bool:
        """Return whether every pattern is a valid regex."""
        return self._valid

    def tabs(self) -> QTabWidget:
        """Return the tab widget (Detection, Patterns), so the owner can add or rename tabs."""
        return self._tabs

    def detection_layout(self) -> QVBoxLayout:
        """Return the Detection tab's layout, so the owner can add rows above the models."""
        return self._detection_layout

    def shutdown(self) -> None:
        """Stop showing spaCy models; the answer of a running discovery is dropped."""
        self._spacy_task.cancel()

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def anonymizer_config(self) -> AnonymizerConfig | None:
        """Return an :class:`~teachers_teammate.domain.anonymization.AnonymizerConfig`
        reflecting the current dialog state."""
        primary = (
            self._primary_model.currentData()
        )  # None for "Auto" item, "__loading__" during fetch
        # "auto:system", "none" or a model name; None/"" when nothing is selected
        secondary = self._secondary_model.currentData()
        # Treat the loading sentinel as "not yet set" → use the saved choice
        if primary == "__loading__":
            primary = self._configured_primary
        if secondary == "__loading__":
            secondary = self._configured_secondary
        if secondary == "":
            secondary = None
        return AnonymizerConfig(
            primary_model=primary,
            secondary_model=secondary,
            patterns=tuple(self.patterns),
        )

    @property
    def patterns(self) -> list[tuple[str, str]]:
        """Return validated ``(tag, regex)`` pairs from the table, skipping blank rows."""
        result = []
        for row in range(self._table.rowCount()):
            tag, pattern = self._row_texts(row)
            if tag and pattern:
                result.append((tag, pattern))
        return result
