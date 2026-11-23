"""The privacy view of the Check tab: names hidden in the texts, and what a file sent.

The window lays the tab out; this controller owns the eye setting and whether it may be
offered at all, the hiding job that runs behind the preview, and the "What is sent for this
file" report.  It talks to the Check tab through :class:`CheckController` and reaches into
no widget of the window.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout, QWidget

from ..application.composition import AppServices
from ..application.sent_text import SentTexts
from ..texts import dialogs as dlg
from ..texts import privacy
from . import _dialogs
from ._background import LatestTask
from ._check_controller import CheckController
from ._config_panel import ConfigPanel
from ._error_text import spacy_problem
from ._privacy_settings import display_anonymized, set_display_anonymized
from ._results_table import ResultsTable
from ._what_is_sent_dialog import WhatIsSentDialog


class PrivacyViewController(QObject):
    """Whether names can be hidden, the hiding of the open text, and what a file sent."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        table: ResultsTable,
        check: CheckController,
        settings_store: Callable[[], Any],
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._table = table
        self._check = check
        self._page = check.page
        self._settings_store = settings_store
        #: The "Hide personal information in texts" view setting (not part of Config).
        self._display_anonymized = display_anonymized(settings_store)
        #: Whether the texts can be hidden for the current document language.
        self._available = True
        #: The text and language of the running hiding job, to not start it twice.
        self._anonymize_key: tuple[str, str] | None = None
        self._anonymize_running = False
        self._anonymize_task = LatestTask(self)
        self._sent_texts_task = LatestTask(self)
        self._last_sent_texts: SentTexts | None = None
        self._page.set_display_anonymized(self._display_anonymized)
        # The eye button and the texts the preview asks about are this controller's own.
        self._page.display_anonymized_changed.connect(self.apply_display_anonymized)
        self._page.anonymization_needed.connect(self.on_anonymization_needed)

    @property
    def display_anonymized(self) -> bool:
        """Whether the texts are shown with the names hidden."""
        return self._display_anonymized

    @property
    def privacy_available(self) -> bool:
        """Whether names can be hidden for the current document language."""
        return self._available

    @property
    def last_sent_texts(self) -> SentTexts | None:
        """The report of the last "What is sent" question, for callers and tests to read."""
        return self._last_sent_texts

    # ── the setting ──────────────────────────────────────────────────────

    def apply_display_anonymized(self, enabled: bool) -> None:
        """Store the view setting and show the texts accordingly.

        Called by the eye button in the preview panel and by Settings → Privacy, so both
        stay in step and the preference survives a restart.  Hiding is only ever turned on
        while it is possible, so the button and the texts never disagree.
        """
        self.store_display_anonymized(bool(enabled) and self._available)

    def store_display_anonymized(self, enabled: bool) -> None:
        """Remember the wish for the view as it is, even before it can be carried out.

        The Setup Assistant sets it while the name detection is still on its way; the view
        follows as soon as hiding is possible (:meth:`refresh_availability`).
        """
        set_display_anonymized(self._settings_store, enabled)
        self._display_anonymized = enabled
        self._page.set_display_anonymized(enabled)

    def refresh_availability(self) -> None:
        """Enable the setting only while personal information can actually be hidden.

        Needs the Privacy add-on and the spaCy models the view would load, resolved the way a
        run resolves them — so a chosen primary model counts.  Asked every time instead of
        cached: the answer is cheap, and a cache is what went stale when a download finished
        after the window that started it had closed.
        """
        available = self._services.anonymization.anonymizer_available(
            self._panel.lookup_language(), self._panel.build_anonymizer_config()
        )
        self._available = available
        self._page.set_display_anonymized_available(available)
        if available:
            # The wish wins over the answer: it was read from the settings before the first
            # check, and a missing add-on must not lose it.
            self._page.set_display_anonymized(self._display_anonymized)
        self._check.refresh_copy_state()

    # ── the hiding behind the preview ────────────────────────────────────

    def on_anonymization_needed(self, raw_text: str) -> None:
        """Start hiding the personal information of the text the preview shows."""
        if not raw_text.strip():
            self._page.set_anonymization({})
            return
        language = self._panel.lookup_language()
        if self._anonymize_running and self._anonymize_key == (raw_text, language):
            return  # already being worked out
        self._anonymize_key = (raw_text, language)
        self._anonymize_running = True
        service = self._services
        anon_config = self._panel.build_anonymizer_config()

        def _anonymize() -> object:
            return service.anonymization.anonymize_text(raw_text, language, anon_config)

        def _on_done(result: object, error: BaseException | None) -> None:
            self._finish_anonymization(result, error)

        # Only the latest request counts: a slow result for a text the preview already left
        # is dropped, and no thread object can be destroyed while it still runs.
        self._anonymize_task.submit(_anonymize, _on_done)

    def _finish_anonymization(self, payload: object, error: BaseException | None) -> None:
        """Hand the placeholders to the Check tab (only the newest request gets here)."""
        self._anonymize_key = None
        self._anonymize_running = False
        if error is not None or not isinstance(payload, tuple) or len(payload) != 2:
            reason = (
                (spacy_problem(error) or str(error)) if error is not None else dlg.UNKNOWN_ERROR
            )
            self._page.set_anonymization({}, error=reason)
            return
        _anonymized, raw_mapping = payload
        mapping = (
            {str(key): str(value) for key, value in raw_mapping.items()}
            if isinstance(raw_mapping, dict)
            else {}
        )
        self._page.set_anonymization(mapping)

    # ── what is sent for one file ────────────────────────────────────────

    def on_what_is_sent_requested(self, source_id: str) -> None:
        """Open "What is sent for this file…" for one row of the results table."""
        row = self._table.row_for(source_id)
        name = row.name if row is not None else Path(source_id).name
        if not self._table.has_text(source_id):
            _dialogs.notify(self._window, privacy.what_is_sent_without_text())
            return

        config = self._panel.lookup_config()

        busy = QDialog(self._window)
        busy.setWindowTitle(privacy.what_is_sent_title(name))
        busy_vbox = QVBoxLayout(busy)
        busy_vbox.addWidget(QLabel(privacy.WHAT_IS_SENT_WORKING))
        service = self._services

        def _describe() -> object:
            # The stored results say what really went out; the report says what would go next.
            view = service.documents.load_view(config, Path(source_id))
            if view is None:
                return None
            return service.documents.describe_sent_texts(config, view)

        def _on_done(result: object, error: BaseException | None) -> None:
            busy.accept()
            if error is not None:
                _dialogs.notify(
                    self._window, privacy.what_is_sent_failed(spacy_problem(error) or str(error))
                )
                return
            if not isinstance(result, SentTexts):
                _dialogs.notify(self._window, privacy.what_is_sent_unreadable())
                return
            self.show_what_is_sent(result, name)

        self._sent_texts_task.submit(_describe, _on_done)
        busy.exec()

    def show_what_is_sent(self, report: SentTexts, name: str) -> None:
        """Show the report for one file, remembering it so tests and callers can read it."""
        self._last_sent_texts = report
        WhatIsSentDialog(report, file_name=name, parent=self._window).exec()

    def shutdown(self) -> None:
        """Drop the background work when the window closes."""
        self._sent_texts_task.cancel()
        self._anonymize_task.cancel()
