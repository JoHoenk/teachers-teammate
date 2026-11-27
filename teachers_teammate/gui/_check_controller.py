"""The Check tab: one document, what the run does with it, and what the teacher does to it.

The window lays the tab out; this controller owns the document the tab shows — which one it
is, whether the run locks it, what the strip above the text says, what the two tabs are
called, and what a typed edit or the *Checked* mark does to the store.  What it needs of the
window (open Settings, run a stage) comes in through :class:`CheckHooks`, looked up when it is
asked, so the controller never reaches into it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QTabWidget, QWidget

from ..application.check_document import CheckDocument
from ..application.commands import WorkNotSavedError
from ..application.composition import AppServices
from ..config import Config
from ..domain.stages import Stage
from ..messages import message_of
from ..texts import check as check_text
from ..texts import dialogs as dlg
from ..texts import review as txt
from ..texts import run as run_text
from . import _handles
from ._background import run_in_background
from ._config_panel import ConfigPanel
from ._error_text import spacy_problem
from ._errors import show_failure, show_notice
from ._export_menu import ExportKind
from ._results_table import ResultsTable
from ._run_session import RunSession, StopStage
from ._view_settings import (
    check_card_split,
    check_split,
    set_check_card_split,
    set_check_split,
    set_text_point_size,
    text_point_size,
)
from .check import CheckLayout, CheckPage, CopyKind, CopyState, RunState
from .settings import SettingsPage
from .settings._page import FOCUS_OCR, FOCUS_PROOFREADING

_logger = logging.getLogger(__name__)

#: How much of a document's name the Check tab label shows before it is shortened.
TAB_NAME_CHARS = 28

#: Which part of the teacher's work a typed edit writes.
_RAW = "raw"
_CORRECTED = "corrected"

#: A drag of a pane reports every pixel: the sizes are written once the drag settles.
LAYOUT_DEBOUNCE_MS = 400


def _debounce_timer(parent: QWidget, interval_ms: int, slot: Callable[[], None]) -> QTimer:
    """Return a single-shot timer that calls *slot* once *interval_ms* have passed."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(slot)
    return timer


@dataclass(frozen=True)
class CheckHooks:
    """What the Check tab asks of the window it belongs to, as the window hands it over."""

    sync_row: Callable[[str], None]
    """Show what the store says about one document in its row."""
    run_busy: Callable[[], bool]
    """Whether a run or a requirement check is going."""
    warn_busy: Callable[[], None]
    """Tell the teacher that the run is busy and nothing was started."""
    run_stage: Callable[[list[str], str], None]
    """Run one stage over the given rows."""
    open_settings: Callable[..., None]
    """Open the Settings window on a page, focused on one control."""
    privacy_available: Callable[[], bool]
    """Whether names can be hidden for the current document language."""
    export: Callable[[list[str], ExportKind], None]
    """Save the texts of these documents the way the window saves them."""


class CheckController(QObject):
    """The document in the Check tab and everything the window shows about it."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        table: ResultsTable,
        session: RunSession,
        tabs: QTabWidget,
        settings_store: Callable[[], Any],
        hooks: CheckHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._table = table
        self._session = session
        self._tabs = tabs
        self._settings_store = settings_store
        self._hooks = hooks
        #: The document the tab shows; empty when none is open.
        self._open = ""
        #: What the Copy menu offers for the open document, so a privacy change can redraw it.
        self._copy_state: CopyState | None = None
        #: The pane sizes of the last drag, written once it settles.
        self._pending_layout: CheckLayout | None = None
        self._layout_debounce = _debounce_timer(window, LAYOUT_DEBOUNCE_MS, self.flush_layout)
        self._page = CheckPage()
        self._page.setObjectName(_handles.CHECK_PAGE)
        self._page.set_point_size(text_point_size(settings_store))
        self._page.set_layout(
            CheckLayout(
                split=check_split(settings_store),
                card_split=check_card_split(settings_store),
            )
        )
        self._connect_page()

    @property
    def page(self) -> CheckPage:
        """The page the window puts in its second tab."""
        return self._page

    @property
    def current(self) -> str:
        """The document the tab shows, or ``""`` when none is open."""
        return self._open

    def _connect_page(self) -> None:
        """Wire what this controller answers itself: moving through the queue, the header."""
        self._page.previous_requested.connect(self.previous_document)
        self._page.next_requested.connect(self.next_document)
        self._page.back_requested.connect(self.show_documents)
        self._page.proofread_requested.connect(self.on_proofread_requested)
        self._page.proofreading_setup_requested.connect(
            lambda: self._hooks.open_settings(SettingsPage.AI_MODELS, FOCUS_PROOFREADING)
        )
        self._page.read_settings_requested.connect(
            lambda: self._hooks.open_settings(SettingsPage.AI_MODELS, FOCUS_OCR)
        )
        self._page.raw_text_edited.connect(self.on_raw_text_edited)
        self._page.corrected_text_edited.connect(self.on_corrected_text_edited)
        self._page.checked_toggled.connect(self.on_checked_toggled)
        self._page.item_shown.connect(self.on_item_shown)
        self._page.copy_requested.connect(self.on_copy_requested)
        self._page.save_requested.connect(self.on_save_requested)
        self._page.text_zoom_changed.connect(self.on_text_zoom)
        self._page.layout_changed.connect(self.on_layout_changed)
        # Ctrl+1 / Ctrl+2 reach the tabs from anywhere in the window; Esc is the page's.
        QShortcut(QKeySequence("Ctrl+1"), self._window, self.show_documents)
        QShortcut(QKeySequence("Ctrl+2"), self._window, self.show_check)

    # ── The document ─────────────────────────────────────────────────────

    def load(self, source_id: str) -> bool:
        """Show *source_id* in the Check tab, with its texts and the items of its review.

        The loader never switches tabs. A finished file reloads the open document, and only
        the teacher (or a run start, which goes to Documents) decides which tab is in front.
        Returns whether the document was shown; when it was not, the teacher was told why and
        what was open before stays open.
        """
        doc = self._document(source_id)
        if doc is None:
            show_notice(self._window, dlg.check_without_state(Path(source_id).name))
            # What was open stays open; a lock the run put on it must still lift with the run.
            self.apply_lock()
            self.update_tab()
            return False
        self._open = source_id
        self._page.show_document(doc, *self.position(source_id))
        self.show_copy_state(doc.raw_text, doc.corrected_text)
        self._hooks.sync_row(source_id)
        self.apply_lock()
        self.update_tab()
        return True

    def open_document(self, source_id: str) -> None:
        """Show *source_id* in the Check tab: what a double-click, Enter or the menu asks for.

        The row is selected too, so the queue shows the document that is open.  Clicking the
        tab itself does not do this: a selection is how rows are chosen for a run, and reading
        it on every tab click would swap the essay under the teacher.
        """
        self.load(source_id)
        self._table.select_source(source_id)
        self._tabs.setCurrentIndex(1)

    def close_document(self) -> None:
        """Forget the open document and go back to Documents."""
        self._open = ""
        self._page.clear()
        self._tabs.setCurrentIndex(0)

    def sync_with_queue(self) -> None:
        """Close the Check tab when its document is no longer in the queue."""
        if not self._open:
            return
        if self._table.row_for(self._open) is not None:
            return
        self.close_document()

    def on_tab_changed(self, index: int) -> None:
        """Opening the Check tab with nothing open shows a document; the tab is never empty."""
        if index == 0:
            self._session.from_check = False  # a request that never started a run is forgotten
        if index != 1 or self._open:
            return
        target = self._target()
        if target:
            self.load(target)

    def show_documents(self) -> None:
        """Go back to the queue: Ctrl+1, or Esc in the Check tab."""
        self._tabs.setCurrentIndex(0)

    def show_check(self) -> None:
        """Go to the Check tab, opening a document when none is open (Ctrl+2)."""
        if self._tabs.isTabEnabled(1):
            self._tabs.setCurrentIndex(1)  # on_tab_changed opens a document when none is

    def previous_document(self) -> None:
        """Show the document before the open one in the queue."""
        self._step(-1)

    def next_document(self) -> None:
        """Show the document after the open one in the queue."""
        self._step(1)

    def _step(self, step: int) -> None:
        """Move *step* documents on and select the row that is now shown.

        The selection follows the open document, because a run takes its documents from the
        selected rows: the teacher moving through the set keeps the queue in step.
        """
        openable = self._table.checkable_source_ids()
        if self._open not in openable:
            return
        index = openable.index(self._open) + step
        if not 0 <= index < len(openable):
            return
        target = openable[index]
        self.load(target)
        self._table.select_source(target)

    def _document(self, source_id: str) -> CheckDocument | None:
        """Return what the Check tab shows about *source_id*, or ``None`` without a state."""
        return self._services.documents.check_document(self._config(), Path(source_id))

    def _config(self) -> Config:
        """Return the settings to read the store with, also while the panel is incomplete."""
        try:
            return self._panel.to_config()
        except ValueError:
            # A setting that is not valid yet (no output folder) must not hide stored results:
            # reading them needs no more than the lookup config does.
            return self._panel.lookup_config()

    def position(self, source_id: str) -> tuple[int, int]:
        """Return where *source_id* sits among the documents the Check tab can open."""
        openable = self._table.checkable_source_ids()
        position = openable.index(source_id) + 1 if source_id in openable else 0
        return position, len(openable)

    def _target(self) -> str:
        """The document Check opens when none is open: the current row, else the first."""
        openable = self._table.checkable_source_ids()
        selected = self._table.selected_source_ids()
        current = selected[0] if selected else ""
        if current in openable:
            return current
        return openable[0] if openable else ""

    def _open_name(self) -> str:
        """The name of the document the Check tab shows, or ``""`` when none is open."""
        row = self._table.row_for(self._open)
        return row.name if row is not None else ""

    # ── The run over the open document ───────────────────────────────────

    def apply_lock(self) -> None:
        """Make the open document read-only while the run works on it, or free it again."""
        if not self._open:
            return
        if self._open == self._session.current:
            self._page.set_lock(txt.BEING_PROOFREAD)
        elif self._open in self._session.pending:
            self._page.set_lock(txt.WAITING_FOR_RUN)
        else:
            self._page.set_lock(None)
        self.refresh_run()

    def refresh_run(self) -> None:
        """Tell the Check tab what the run does with the open document: working, waiting, or not.

        Fed from what the window already follows (the run's file, step and pace, and the Stop
        stage), so the teacher who proofreads from Check sees the run without leaving the tab.
        """
        if not self._open or not (
            self._open == self._session.current or self._open in self._session.pending
        ):
            self._page.set_run(None)
            return
        stop_text = {
            StopStage.RUNNING: run_text.STOP,
            StopStage.AFTER_FILE: run_text.STOP_NOW,
            StopStage.NOW: run_text.STOP_NOW,
        }.get(self._session.stop_stage, "")
        stop_enabled = self._session.stop_stage in (StopStage.RUNNING, StopStage.AFTER_FILE)
        if self._open != self._session.current:
            self._page.set_run(
                RunState(
                    check_text.RUN_WAITING,
                    busy=False,
                    stop_text=stop_text,
                    stop_enabled=stop_enabled,
                )
            )
            return
        step = self._session.step or (
            check_text.run_proofreading(self._session.model)
            if self._session.model
            else check_text.RUN_READING
        )
        snapshot = self._session.snapshot
        self._page.set_run(
            RunState(
                step,
                time_left=snapshot.note,
                time_left_tip=snapshot.tooltip,
                stop_text=stop_text,
                stop_enabled=stop_enabled,
            )
        )

    def on_proofread_requested(self) -> None:
        """Proofread the open document: the strip's button and the header's *Proofread again*."""
        if not self._open:
            return
        if self._hooks.run_busy():
            self._hooks.warn_busy()  # nothing starts, so the tab must not be marked as the origin
            return
        self._session.from_check = True
        self._hooks.run_stage([self._open], Stage.CORRECTION)

    # ── The two tab labels ───────────────────────────────────────────────

    def update_tab(self) -> None:
        """Label both tabs and enable Check only while a document can be opened."""
        self._tabs.setTabEnabled(1, bool(self._table.checkable_source_ids()))
        self._tabs.setTabText(0, self._documents_label())
        self._tabs.setTabText(1, self._check_label())
        self._tabs.setTabToolTip(0, check_text.TAB_DOCUMENTS_TIP)
        self._tabs.setTabToolTip(1, self._check_tooltip())

    def _documents_label(self) -> str:
        """``Documents``, or how far the running batch has got."""
        if not self._hooks.run_busy() or not self._session.source_ids:
            return check_text.TAB_DOCUMENTS
        return check_text.tab_documents_progress(
            len(self._session.names), len(self._session.source_ids)
        )

    def _check_label(self) -> str:
        """``Check``, or the name of the open document."""
        name = self._open_name()
        if not name:
            return check_text.TAB_CHECK
        if len(name) > TAB_NAME_CHARS:
            name = name[: TAB_NAME_CHARS - 1] + "\u2026"
        return check_text.tab_check_named(name)

    def _check_tooltip(self) -> str:
        """The full name of the open document above the shortcut, so a short label stays clear."""
        name = self._open_name()
        return check_text.tab_check_tip_named(name) if name else check_text.TAB_CHECK_TIP

    # ── What the teacher types and marks ─────────────────────────────────

    def on_item_shown(self, key: str) -> None:
        """An item became the one the card shows: record that the teacher looked at it.

        Stored at once, a few KB, and never undone.  Looking at an item is not work a run has to
        keep, so it does not make the proofreading one to keep.
        """
        if not self._open or not self._page.mark_looked_at(key):
            return
        self._services.commands.mark_looked_at(self._config(), source=Path(self._open), keys=[key])

    def on_checked_toggled(self, checked: bool) -> None:
        """The teacher pressed *Checked*: store the decision and show what the store says.

        The toggle marks what the document has now — the corrections with a proofreading, the
        transcription without one — and is its own undo.  It does not move to the next document;
        ▶ does.  A typed edit is stored first, so the mark names the text the teacher sees.
        """
        if not self._open:
            return
        self._page.flush_pending_edit()
        source = Path(self._open)
        try:
            config = self._panel.to_config()
        except ValueError as exc:
            # Without folders there is no store to write to: the button goes back to what the
            # document says, not to what was pressed.
            show_failure(self._window, dlg.checked_not_saved(), exc)
            self._page.refresh_checked()
            return
        try:
            view = self._services.commands.set_checked(config, source=source, checked=checked)
        except WorkNotSavedError as exc:
            # The command logged the refusal; it is an expected event, not a bug to log again.
            show_failure(self._window, dlg.work_not_saved(exc.name, too_new=exc.too_new))
            self._page.refresh_checked()
            return
        if view is None:
            show_failure(self._window, dlg.checked_not_saved())
            # The button goes back to what the store says, not to what was pressed.
            self.load(self._open)
            return
        doc = self._services.documents.check_document(config, source)
        self._hooks.sync_row(self._open)
        if doc is not None:
            self._page.update_document(doc)
        self.update_tab()

    def on_raw_text_edited(self, edited_text: str) -> None:
        """The transcription was typed into: store it and show what the store now says."""
        self._store_edit(_RAW, edited_text)

    def on_corrected_text_edited(self, edited_text: str) -> None:
        """The final text was typed into: store it and show what the store now says."""
        self._store_edit(_CORRECTED, edited_text)

    def _store_edit(self, kind: str, edited_text: str) -> None:
        """Store one typed edit of the open document, then show the document it left behind.

        One handler for both texts: they differ only in which part of the teacher's work the
        store writes, and everything after it — the row, the page, the tab label — is the same.
        """
        selected = self._edit_target()
        if selected is None:
            return
        source, has_text = selected
        if kind == _CORRECTED and not has_text:
            return
        try:
            config = self._panel.to_config()
        except ValueError as exc:
            _logger.warning("Cannot store the edit of %s: %s", source.name, exc)
            return
        source_id = str(source.resolve())
        text_format = self._table.text_format_for_source(source_id)
        try:
            if kind == _RAW:
                view = self._services.commands.edit_transcription(
                    config, source=source, text=edited_text, text_format=text_format
                )
            else:
                view = self._services.commands.edit_corrected_text(
                    config, source=source, text=edited_text
                )
        except WorkNotSavedError as exc:
            # What was typed stays in the text view, so the teacher can still copy it.  The
            # command logged the refusal.
            show_failure(self._window, dlg.work_not_saved(exc.name, too_new=exc.too_new))
            return
        if view is None:
            return
        # One read of the stored state serves the row and the page.
        doc = self._services.documents.check_document(config, source)
        self._hooks.sync_row(source_id)
        if doc is None:
            return
        if kind == _RAW:
            # The proofreading went with the transcription: the page falls back to it.
            self._page.show_document(doc, *self.position(source_id), typed=True)
            self.show_copy_state(doc.raw_text, doc.corrected_text)
        else:
            self._page.update_document(doc)
        self.update_tab()

    def _edit_target(self) -> tuple[Path, bool] | None:
        """Resolve the open document to its input path and whether it has a transcription.

        An edit belongs to the document the Check tab shows, not to the selected row: the
        selection is how a run picks its documents, and the two may differ.
        """
        if not self._open:
            return None
        source = Path(self._open)
        if not source.exists():
            return None
        return source, self._table.has_text(self._open)

    # ── The Copy menu of the open document ───────────────────────────────

    def show_copy_state(self, raw_text: str, corrected_text: str) -> None:
        """Say what the Copy menu may offer for a document with these texts."""
        self._copy_state = CopyState(
            has_raw=bool(raw_text.strip()),
            has_corrected=bool(corrected_text.strip()),
            can_hide_names=self._hooks.privacy_available(),
        )
        self._page.set_copy_state(self._copy_state)

    def refresh_copy_state(self) -> None:
        """Bring the Copy menu of the open document up to date (names may now be hideable)."""
        if self._copy_state is None:
            return
        self._page.set_copy_state(
            CopyState(
                has_raw=self._copy_state.has_raw,
                has_corrected=self._copy_state.has_corrected,
                can_hide_names=self._hooks.privacy_available(),
            )
        )

    def on_copy_requested(self, kind: CopyKind) -> None:
        """Put the open document's text on the clipboard, as the Copy menu asked."""
        if not self._open:
            return
        self._page.flush_pending_edit()  # a typed edit must be in the text that is copied
        try:
            config = self._panel.to_config()
        except ValueError as exc:
            _logger.warning("Cannot copy the text of %s: %s", Path(self._open).name, exc)
            return
        service = self._services
        # The text comes from the stored document, so a copy carries what the Check tab shows.
        view = service.documents.load_view(config, Path(self._open))
        if view is None:
            return
        if not kind.is_anonymized:
            QApplication.clipboard().setText(
                service.export.text_for_copy(view, corrected=kind.is_corrected)
            )
            self._page.show_copied()
            return
        language = self._panel.lookup_language()
        anon_config = self._panel.build_anonymizer_config()

        def _copy() -> object:
            return service.export.text_for_copy(
                view,
                corrected=kind.is_corrected,
                anonymize=True,
                language=language,
                anonymizer_config=anon_config,
            )

        def _on_done(result: object, error: BaseException | None) -> None:
            if error is not None:
                show_failure(
                    self._window,
                    dlg.copy_failed(spacy_problem(error) or str(message_of(error))),
                    error,
                )
                return
            QApplication.clipboard().setText(str(result))
            self._page.show_copied()

        # Hiding names runs spaCy, which is not a job for the GUI thread.
        run_in_background(_copy, _on_done, owner=self._window)

    def on_save_requested(self, kind: ExportKind) -> None:
        """Save the open document's text through the window's own export path."""
        if not self._open:
            return
        self._page.flush_pending_edit()
        self._hooks.export([self._open], kind)

    # ── What the teacher resized ─────────────────────────────────────────

    def on_text_zoom(self, point_size: int) -> None:
        """Remember the text size the teacher zoomed to, for the next document and restart."""
        set_text_point_size(self._settings_store, point_size)

    def on_layout_changed(self, layout: CheckLayout) -> None:
        """Note how the teacher sized the tab's panes; stored once the drag settles."""
        self._pending_layout = layout
        self._layout_debounce.start()

    def flush_layout(self) -> None:
        """Remember the last pane sizes, for the next restart.  The window calls this too."""
        self._layout_debounce.stop()
        layout, self._pending_layout = self._pending_layout, None
        if layout is None:
            return
        set_check_split(self._settings_store, layout.split)
        set_check_card_split(self._settings_store, layout.card_split)
