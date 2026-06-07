"""Dialog for configuring the anonymizer's NER passes and regex patterns."""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
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

from ..application.service import ProcessingApplicationService

_INVALID_BG = QColor("#ffd0d0")
_DETECTING = "Detecting…"
_AUTO_PRIMARY = "Auto (language-specific)"
_NO_SECONDARY = "(none — skip secondary pass)"


class _SpacyModelFetchThread(QThread):
    """Fetches installed spaCy model names off the main thread."""

    models_ready = Signal(list)
    error = Signal(str)

    def run(self) -> None:
        try:
            self.models_ready.emit(ProcessingApplicationService().list_installed_spacy_models())
        except Exception as exc:  # noqa: BLE001  # spaCy discovery may raise anything; report via signals and emit an empty list
            self.error.emit(str(exc))
            self.models_ready.emit([])


class AnonymizerConfigDialog(QDialog):
    """Configure the anonymizer's NER passes and regex patterns.

    * **Primary model** — the spaCy model used for the first NER pass.  Defaults
      to the language-specific model derived from the document language; can be
      overridden with any installed model (e.g. ``xx_ent_wiki_sm``).
    * **Secondary model** — an optional additional pass to catch names the
      primary model misses.  Leave as ``(none)`` to skip.
    * **Regex patterns** — applied after NER (collapsed by default; expand to
      customise IBAN / EMAIL / PHONE or add your own).

    Args:
        language: Document language — used to derive the primary NER model label
            and as input to the preview.
        anonymizer_config: Current
            :class:`~teachers_teammate.infrastructure.anonymizer.AnonymizerConfig`.
        app_service: Application service used to run the preview.
        parent: Parent widget.
        sample_text: Optional text to pre-fill the preview input.
    """

    def __init__(
        self,
        language: str,
        anonymizer_config: object,
        app_service: ProcessingApplicationService,
        parent: QWidget | None = None,
        *,
        sample_text: str = "",
    ) -> None:
        from ..application.service import AnonymizerConfig  # noqa: PLC0415

        super().__init__(parent)
        self._language = language
        self._app_service = app_service
        self._AnonymizerConfig = AnonymizerConfig
        self.setWindowTitle("Anonymizer Settings")
        self.setMinimumSize(700, 680)

        config: AnonymizerConfig = (
            anonymizer_config
            if isinstance(anonymizer_config, AnonymizerConfig)
            else AnonymizerConfig()
        )
        # Store configured model names so _on_spacy_models_fetched can re-select them
        self._configured_primary = config.primary_model
        self._configured_secondary = config.secondary_model

        root = QVBoxLayout(self)
        root.setSpacing(8)

        accuracy_warning = QLabel(
            "<b>Note:</b> Anonymization is a detection aid, not a guarantee. "
            "NER models and regex patterns may miss PII — always review the output "
            "before uploading documents to a cloud provider. "
            "You remain responsible for compliance with data-protection regulations."
        )
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

        self._build_detection_models(top_layout)

        # ── Preview (lives on the Detection tab) ───────────────────────────────
        self._build_preview(top_layout)

        self._tabs.addTab(detection_tab, "Detection")
        self._tabs.addTab(self._build_regex_tab(), "Regex Patterns")

        # ── Dialog buttons ─────────────────────────────────────────────────────
        self._buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        root.addWidget(self._buttons)

        # Populate patterns table from config
        self._loading = True
        for tag, pattern in config.patterns:
            self._append_row(tag, pattern)
        self._loading = False
        self._revalidate_all()

        if sample_text:
            self._sample_input.setPlainText(sample_text)

        # Start async spaCy model discovery — populates both combos off the main thread
        self._spacy_fetch_thread = _SpacyModelFetchThread(self)
        self._spacy_fetch_thread.models_ready.connect(self._on_spacy_models_fetched)
        self._spacy_fetch_thread.error.connect(self._on_spacy_fetch_error)
        self._spacy_fetch_thread.start()

    def _build_detection_models(self, layout: QVBoxLayout) -> None:
        """Build the primary/secondary spaCy model rows on the Detection tab's *layout*."""
        # Primary model row
        primary_row = QHBoxLayout()
        primary_row.addWidget(QLabel("<b>Primary detection model:</b>"))
        self._primary_model = QComboBox()
        self._primary_model.setEnabled(False)
        self._primary_model.addItem(_DETECTING, userData="__loading__")
        primary_row.addWidget(self._primary_model, stretch=1)
        layout.addLayout(primary_row)

        primary_hint = QLabel(
            "Primary spaCy NER model for person-name detection.  "
            "<i>Auto</i> picks the language-specific model automatically; "
            "choose a specific model to override (e.g. force the multilingual model)."
        )
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
        secondary_row.addWidget(QLabel("<b>Extra detection model (optional):</b>"))
        self._secondary_model = QComboBox()
        self._secondary_model.setEnabled(False)
        self._secondary_model.addItem(_DETECTING, userData="__loading__")
        secondary_row.addWidget(self._secondary_model, stretch=1)
        layout.addLayout(secondary_row)

        secondary_hint = QLabel(
            "An extra spaCy model run after the primary one to catch names it misses "
            "(e.g. German names in English text). Leave as <i>(none)</i> if not needed."
        )
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

        regex_layout.addWidget(
            QLabel("Patterns applied after NER (clear all rows to disable regex anonymization):")
        )

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Tag", "Regex pattern"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellChanged.connect(self._on_cell_changed)
        regex_layout.addWidget(self._table, stretch=1)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add row")
        add_btn.clicked.connect(self._add_row)
        remove_btn = QPushButton("Remove selected")
        remove_btn.clicked.connect(self._remove_selected)
        restore_btn = QPushButton("Restore defaults")
        restore_btn.setToolTip("Reset patterns to the built-in defaults (IBAN, Email, Phone).")
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

        layout.addWidget(
            QLabel("<b>Preview</b> — paste sample OCR text to test all active patterns:")
        )

        preview_cols = QHBoxLayout()

        input_col = QVBoxLayout()
        input_col.addWidget(QLabel("Sample text:"))
        self._sample_input = QPlainTextEdit()
        self._sample_input.setPlaceholderText(
            "Paste text here, e.g.:\nAlice sent an invoice to bob@example.com (STU-123456)."
        )
        input_col.addWidget(self._sample_input, stretch=1)
        preview_cols.addLayout(input_col)

        output_col = QVBoxLayout()
        output_col.addWidget(QLabel("Anonymized output:"))
        self._sample_output = QPlainTextEdit()
        self._sample_output.setReadOnly(True)
        output_col.addWidget(self._sample_output, stretch=1)
        preview_cols.addLayout(output_col)

        layout.addLayout(preview_cols, stretch=1)

        run_row = QHBoxLayout()
        self._run_btn = QPushButton("Run preview")
        self._run_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._run_btn.clicked.connect(self._run_preview)
        run_row.addWidget(self._run_btn)
        run_row.addStretch()
        layout.addLayout(run_row)

        layout.addWidget(QLabel("Replacement map:"))
        self._mapping_table = QTableWidget(0, 2)
        self._mapping_table.setHorizontalHeaderLabels(["Placeholder", "Original value"])
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
        self._spacy_status_lbl.setText(f"Could not detect installed spaCy models: {msg}")
        self._spacy_status_lbl.setVisible(True)

    def _on_spacy_models_fetched(self, models: list[str]) -> None:
        """Populate the primary and secondary model combos after the background fetch."""
        # Derive the auto label for the primary combo
        try:
            auto_model = ProcessingApplicationService().spacy_model_for_language(self._language)
        except Exception:  # noqa: BLE001  # model lookup is best-effort; fall back to the generic auto label
            auto_model = ""
        auto_label = f"{_AUTO_PRIMARY}: {auto_model}" if auto_model else _AUTO_PRIMARY

        # Primary combo: Auto + installed models
        self._primary_model.clear()
        self._primary_model.addItem(auto_label, userData=None)
        for m in models:
            self._primary_model.addItem(m, userData=m)
        if self._configured_primary and self._configured_primary in models:
            idx = self._primary_model.findData(self._configured_primary)
            self._primary_model.setCurrentIndex(idx)
        self._primary_model.setEnabled(True)

        # Secondary combo: (none) + installed models
        self._secondary_model.clear()
        self._secondary_model.addItem(_NO_SECONDARY, userData=None)
        for m in models:
            self._secondary_model.addItem(m, userData=m)
        if self._configured_secondary and self._configured_secondary in models:
            idx = self._secondary_model.findData(self._configured_secondary)
            self._secondary_model.setCurrentIndex(idx)
        self._secondary_model.setEnabled(True)

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
        from ..application.service import DEFAULT_ANONYMIZER_PATTERNS  # noqa: PLC0415

        self._loading = True
        self._table.setRowCount(0)
        for tag, pattern in DEFAULT_ANONYMIZER_PATTERNS:
            self._append_row(tag, pattern)
        self._loading = False
        self._revalidate_all()

    # ── Validation ─────────────────────────────────────────────────────────────

    def _on_cell_changed(self, row: int, col: int) -> None:
        if self._loading:
            return
        if col == 1:
            self._validate_row(row)
            self._update_error_summary()
            self._update_ok_button()

    def _validate_row(self, row: int) -> bool:
        item = self._table.item(row, 1)
        if item is None:
            return True
        pattern_text = item.text()
        if not pattern_text:
            item.setBackground(self._table.palette().base())
            item.setToolTip("")
            return True
        try:
            re.compile(pattern_text)
            item.setBackground(self._table.palette().base())
            item.setToolTip("")
            return True
        except re.error as exc:
            item.setBackground(_INVALID_BG)
            item.setToolTip(f"Invalid regex: {exc}")
            return False

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
            self._error_label.setText(
                f"{n} pattern{'s' if n > 1 else ''} {'have' if n > 1 else 'has'} errors — fix before saving."
            )
            self._error_label.setVisible(True)
        else:
            self._error_label.setVisible(False)

    def _update_ok_button(self) -> None:
        ok = self._buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok:
            ok.setEnabled(self._invalid_count() == 0)

    # ── Preview ────────────────────────────────────────────────────────────────

    def _run_preview(self) -> None:
        self._preview_error.setVisible(False)
        self._sample_output.clear()
        self._mapping_table.setRowCount(0)

        text = self._sample_input.toPlainText()
        if not text.strip():
            return

        try:
            anonymized, mapping = self._app_service.anonymize_preview(
                text, self._language, self.anonymizer_config
            )
        except ImportError as exc:
            self._preview_error.setText(
                f"spaCy is not installed: {exc}\n"
                'Install it via the "Install Privacy Addon" button in the main window.'
            )
            self._preview_error.setVisible(True)
            return
        except OSError:
            from ._addon_installer_dialog import SpacyModelDownloadDialog  # noqa: PLC0415

            needed = self._app_service.spacy_model_for_language(self._language)
            if SpacyModelDownloadDialog.is_available():
                self._open_download_dialog(needed)
            else:
                self._preview_error.setText(
                    f"spaCy model '{needed}' is not downloaded.\n"
                    f"Run: python -m spacy download {needed}"
                )
                self._preview_error.setVisible(True)
            return
        except Exception as exc:  # noqa: BLE001  # anonymization preview may raise anything; show the error in the panel
            self._preview_error.setText(f"Preview failed: {exc}")
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
        from ..application.service import ADDON_PRIVACY  # noqa: PLC0415
        from ._addon_installer_dialog import SpacyModelDownloadDialog  # noqa: PLC0415

        if not self._app_service.is_addon_available(ADDON_PRIVACY):
            from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

            QMessageBox.information(
                self,
                "spaCy not installed",
                "spaCy must be installed before you can download a model.\n\n"
                "Use the Add-on Installer (Settings → Add-ons) to install the "
                "Privacy Add-on first.",
            )
            return

        dlg = SpacyModelDownloadDialog(needed_model, self)
        dlg.model_downloaded.connect(self._on_model_downloaded)
        dlg.exec()

    def _on_model_downloaded(self, _model: str) -> None:
        """Refresh model combos after a successful download and re-run the preview."""
        # Refresh combos from a fresh background fetch
        self._primary_model.setEnabled(False)
        self._secondary_model.setEnabled(False)
        self._configured_secondary = self._secondary_model.currentData()
        self._configured_primary = self._primary_model.currentData()
        refresh = _SpacyModelFetchThread(self)
        refresh.models_ready.connect(self._on_spacy_models_fetched)
        refresh.error.connect(self._on_spacy_fetch_error)
        self._spacy_fetch_thread = refresh
        refresh.start()
        # Re-run the preview once the fetch completes (deferred so exec() has returned)
        QTimer.singleShot(50, self._run_preview)

    # ── Public properties ──────────────────────────────────────────────────────

    @property
    def anonymizer_config(self) -> object:
        """Return an :class:`~teachers_teammate.infrastructure.anonymizer.AnonymizerConfig`
        reflecting the current dialog state."""
        primary = (
            self._primary_model.currentData()
        )  # None for "Auto" item, "__loading__" during fetch
        secondary = self._secondary_model.currentData()  # None for "(none)" item
        # Treat the loading sentinel as "not yet set" → use None
        if primary == "__loading__":
            primary = self._configured_primary
        if secondary == "__loading__":
            secondary = self._configured_secondary
        return self._AnonymizerConfig(
            primary_model=primary,
            secondary_model=secondary,
            patterns=tuple(self.patterns),
        )

    @property
    def patterns(self) -> list[tuple[str, str]]:
        """Return validated ``(tag, regex)`` pairs from the table, skipping blank rows."""
        result = []
        for row in range(self._table.rowCount()):
            tag_item = self._table.item(row, 0)
            pat_item = self._table.item(row, 1)
            tag = tag_item.text().strip() if tag_item else ""
            pattern = pat_item.text().strip() if pat_item else ""
            if tag and pattern:
                result.append((tag, pattern))
        return result
