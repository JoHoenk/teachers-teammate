"""Export and copy: Word documents, text files, and the text of one document.

The controller owns the Export button and the File → Export entries, asks where to save them,
and writes them — the text ones off the GUI thread, because hiding names runs spaCy.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import NamedTuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QPushButton, QStyle, QWidget

from ..application.composition import AppServices
from ..application.export import DocxExport, NothingToExport, WouldOverwriteInput
from ..messages import log_failure
from ..texts import dialogs as dlg
from ..texts import main_window as txt
from . import _dialogs, _handles
from ._background import run_in_background
from ._config_panel import ConfigPanel
from ._error_text import spacy_problem
from ._export_menu import ExportKind, build_export_menu
from ._qt import present
from ._results_table import ResultsTable

_logger = logging.getLogger(__name__)


def _export_failure(name: str, exc: Exception) -> str:
    """Return the line that tells the teacher why *name* could not be exported."""
    if isinstance(exc, NothingToExport):
        return dlg.export_no_text(name)
    if isinstance(exc, WouldOverwriteInput):
        return dlg.export_would_overwrite_input(name, exc.target.name)
    log_failure(_logger, "A file could not be exported", exc)
    return f"{name}: {exc}"


class _TextExportOutcome(NamedTuple):
    """Result of a background text export."""

    written: list[Path]
    failed: list[str]


class ExportController(QObject):
    """Everything that writes a result out of the app."""

    log_line = Signal(str)

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        table: ResultsTable,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._table = table
        self._button: QPushButton | None = None
        self._actions: list[QAction] = []
        self._text_export_running = False

    @property
    def running(self) -> bool:
        """Whether a text export is running (a second one would write the same files)."""
        return self._text_export_running

    def create_button(self) -> QPushButton:
        """Return the Export button; its menu offers Word, text and anonymized text."""
        style = present(self._window.style(), "style")
        button = QPushButton(txt.EXPORT_BUTTON)
        button.setObjectName(_handles.EXPORT_BUTTON)
        button.setIcon(
            QIcon.fromTheme(
                "document-export",
                style.standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            )
        )
        button.setMinimumHeight(32)
        button.setEnabled(False)
        button.setToolTip(txt.EXPORT_TOOLTIP)
        menu, kinds = build_export_menu(button)
        menu.triggered.connect(lambda action: self.on_toolbar_action(kinds[action]))
        button.setMenu(menu)
        self._button = button
        return button

    def add_actions(self, actions: list[QAction]) -> None:
        """Enable and disable *actions* together with the Export button (File → Export)."""
        self._actions = list(actions)
        for action in self._actions:
            action.setEnabled(False)

    def set_enabled(self, enabled: bool) -> None:
        """Enable the Export button and the File → Export entries together."""
        if self._button is not None:
            self._button.setEnabled(enabled)
        for action in self._actions:
            action.setEnabled(enabled)

    def on_toolbar_action(self, kind: ExportKind) -> None:
        """Export the selected rows, or the whole queue when nothing is selected."""
        source_ids = self._table.selected_source_ids() or self._table.queued_source_ids()
        if source_ids:
            self.on_requested(source_ids, kind)

    def on_requested(self, source_ids: list[str], kind: ExportKind) -> None:
        if kind.is_text and self._text_export_running:
            _dialogs.notify(self._window, dlg.busy_with_text_export())
            return
        targets = self._pick_targets(source_ids, kind)
        if not targets:
            return
        # One folder for several documents: two inputs with the same name would collide.
        targets, errors = self._drop_colliding_targets(targets)
        if len(source_ids) > 1 and not self._confirm_replace_existing(targets):
            return
        if kind.is_text and not self._confirm_text_export_folder(targets):
            return
        if kind is ExportKind.DOCX:
            self._export_docx(targets, errors)
        else:
            self._export_text(targets, errors, anonymize=kind is ExportKind.TEXT_ANONYMIZED)

    def _pick_targets(self, source_ids: list[str], kind: ExportKind) -> list[tuple[str, Path]]:
        """Ask where to save and return ``(source_id, output path)`` pairs (empty = cancelled)."""
        # Suggested starting directory: last run output dir → config output → input dir
        start_dir = self._table.get_output_dir()
        if start_dir is None:
            d = self._panel.get_output_dir() or self._panel.get_input_dir()
            start_dir = Path(d) if d else Path.home()

        # For a single file show Save As; for multiple files show a folder picker.
        if len(source_ids) == 1:
            suggested = str(start_dir / (Path(source_ids[0]).stem + kind.file_suffix))
            dest = _dialogs.save_file(
                self._window, dlg.save_export(kind.label, kind.file_filter, suggested)
            )
            return [(source_ids[0], dest)] if dest else []
        folder = _dialogs.pick_folder(
            self._window, dlg.save_export_to_folder(kind.label, str(start_dir))
        )
        if folder is None:
            return []
        # Same-named documents from different subfolders keep their subfolders.
        input_dir = self._panel.get_input_dir()
        return [
            (
                sid,
                self._services.export.export_target(
                    Path(sid),
                    Path(input_dir) if input_dir else None,
                    Path(folder),
                    kind.file_suffix,
                ),
            )
            for sid in source_ids
        ]

    @staticmethod
    def _drop_colliding_targets(
        targets: list[tuple[str, Path]],
    ) -> tuple[list[tuple[str, Path]], list[str]]:
        """Keep the first target per output file; report the later ones that would replace it."""
        kept: list[tuple[str, Path]] = []
        errors: list[str] = []
        claimed: set[Path] = set()
        for source_id, out_path in targets:
            if out_path.resolve() in claimed:
                errors.append(txt.export_would_overwrite(Path(source_id).name, out_path.name))
            else:
                claimed.add(out_path.resolve())
                kept.append((source_id, out_path))
        return kept, errors

    def _confirm_replace_existing(self, targets: list[tuple[str, Path]]) -> bool:
        """Ask once before a folder export replaces files (the Save dialog asks for one file)."""
        existing_paths = [out for _sid, out in targets if out.exists()]
        if not existing_paths:
            return True
        # Files of different subfolders can share a name, so show where they are.
        root = Path(os.path.commonpath([out.parent for out in existing_paths]))
        existing = [out.relative_to(root).as_posix() for out in existing_paths]
        shown = "\n".join(existing[:10]) + ("\n…" if len(existing) > 10 else "")
        return _dialogs.ask(self._window, dlg.replace_existing_files(len(existing), shown))

    def _confirm_text_export_folder(self, targets: list[tuple[str, Path]]) -> bool:
        """Warn when text files would land in the input folder, where the next run reads them.

        A one-off export only asks; a run with text files configured there is refused
        (:meth:`~teachers_teammate.application.run.RunService.output_problem_for_run`)
        since it would repeat.
        """
        input_dir = self._panel.get_input_dir()
        # With an explicit file selection a run reads only those files.
        if not input_dir or self._panel.get_selected_files():
            return True
        # Folder mode always scans subfolders (see ConfigPanel.to_config).
        if not any(
            self._services.export.is_in_input_folder(out.parent, Path(input_dir), recursive=True)
            for _sid, out in targets
        ):
            return True
        return _dialogs.ask(self._window, dlg.save_into_input_folder())

    def _export_docx(self, targets: list[tuple[str, Path]], errors: list[str]) -> None:
        settings = self._panel.get_settings_dict()
        docx_format = str(settings.get("docx_format", "table"))
        # "Same as the text" (empty) means the language of the text, as in the pipeline.
        explanation_language = str(
            settings.get("explanation_language") or self._panel.to_dict().get("language", "")
        )
        try:
            config = self._panel.to_config()
        except ValueError as exc:
            errors.append(str(exc))
            _dialogs.notify(self._window, dlg.word_export_failed(errors))
            return
        for source_id, out_path in targets:
            try:
                # The texts, their notation and the review come from the stored document, so the
                # file carries what the Check tab shows.
                self._services.export.export_docx(
                    config,
                    Path(source_id),
                    out_path,
                    options=DocxExport(
                        docx_format=docx_format,
                        labels_language=explanation_language,
                    ),
                )
                self.log_line.emit(txt.docx_saved(out_path))
            except Exception as exc:  # noqa: BLE001  # python-docx/PIL errors vary; collect per-file and continue the batch
                errors.append(_export_failure(Path(source_id).name, exc))

        if errors:
            _dialogs.notify(self._window, dlg.word_export_failed(errors))

    def _export_text(
        self, targets: list[tuple[str, Path]], errors: list[str], *, anonymize: bool
    ) -> None:
        """Write the extracted text of each target; anonymizing is slow, so it runs off-thread."""
        service = self._services
        language = str(self._panel.to_dict().get("language") or "English")
        anon_config = self._panel.build_anonymizer_config() if anonymize else None
        try:
            config = self._panel.to_config()
        except ValueError as exc:
            errors.append(str(exc))
            self._show_text_export_errors(errors)
            return
        # Every input of the run is protected, not just the row's own file: exporting
        # ``essay.png`` as ``essay.txt`` must not replace an ``essay.txt`` that is an input too.
        protected = [Path(sid) for sid in self._table.queued_source_ids()]
        jobs: list[tuple[Path, Path]] = []
        for source_id, out_path in targets:
            # The text and its notation come from the stored document, so the file carries the
            # teacher's transcription where they fixed or checked it.
            view = service.documents.load_view(config, Path(source_id))
            if view is None or not view.raw_text.strip():
                errors.append(dlg.export_no_text(Path(source_id).name))
            else:
                jobs.append((Path(source_id), out_path))
        if not jobs:
            self._show_text_export_errors(errors)
            return
        preflight_errors = list(errors)

        def work() -> _TextExportOutcome:
            if anonymize:
                # Fails once, up front, when spaCy or its model is missing.
                service.anonymization.warm_up_anonymizer(language, anon_config)
            written: list[Path] = []
            failed = list(preflight_errors)
            for source, out_path in jobs:
                try:
                    service.export.export_text(
                        config,
                        source,
                        out_path,
                        anonymize=anonymize,
                        language=language,
                        anonymizer_config=anon_config,
                        protected=protected,
                    )
                    written.append(out_path)
                except Exception as exc:  # noqa: BLE001  # NER/IO errors vary; collect per-file and continue the batch
                    failed.append(_export_failure(source.name, exc))
            return _TextExportOutcome(written, failed)

        def done(result: object, error: BaseException | None) -> None:
            self._text_export_running = False
            if error is not None:
                message = (spacy_problem(error) if anonymize else None) or str(error)
                _dialogs.notify(self._window, dlg.text_export_error(message, preflight_errors))
                return
            if not isinstance(result, _TextExportOutcome):
                return
            for out_path in result.written:
                self.log_line.emit(txt.text_saved(out_path))
            self._show_text_export_errors(result.failed)

        self._text_export_running = True
        run_in_background(work, done, owner=self._window)

    def _show_text_export_errors(self, errors: list[str]) -> None:
        if errors:
            _dialogs.notify(self._window, dlg.text_export_failed(errors))
