"""The Check tab: a header that says which document is open, and the body that shows it.

The page owns no document state.  The window pushes the name, the position in the queue, the
document itself and what the Copy menu may offer, and listens for what the teacher asks for — so a
document that is open and a row that is selected can never drift apart.

The body is three panes: the scan of every stored page, one text with the switch *What was read* /
*With corrections* / *Final text*, and the side panel with the card of the chosen item above the
list of all of them.  A card action is an edit of the final text in plain offsets, applied in one
edit block, so one Ctrl+Z takes it back and the stored text comes from the path typing takes.
Ctrl+Z and redo are the final text's own history, and the body owns them, so they work from the
list and the card too, not only while the teacher types.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import Enum

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QFontMetrics, QKeyEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...application.check_document import CheckDocument
from ...application.check_items import CheckItem, ItemKind, countable_keys, group_minor
from ...domain.proofreading.review import AlignedReview
from ...domain.teacher_work import (
    TextEdit,
    looked_at_progress,
    put_back,
    take_out,
    use_suggestion,
)
from ...texts import check as check_text
from .. import _user_text as txt
from .._export_menu import ExportKind
from ._masking import Masking
from ._run_strip import NextStepStrip, RunState, RunStrip
from ._scan_view import ScanPane
from ._side_panel import SidePanel
from ._text_views import CheckTextView, FinalView, MarkedView, ReadView, marks_of

#: Width of the side panel, and the width below which the scan folds to a strip.
_PANEL_WIDTH = 320
_NARROW_WIDTH = 1200
#: Share of the width the scan takes, on *What was read* and on the other two views.
_SCAN_SHARE_READ = 0.45
_SCAN_SHARE_OTHER = 0.30


class CopyKind(Enum):
    """What one entry of the Copy menu puts on the clipboard."""

    RAW = "raw"
    RAW_ANONYMIZED = "raw_anonymized"
    CORRECTED = "corrected"
    CORRECTED_ANONYMIZED = "corrected_anonymized"

    @property
    def label(self) -> str:
        """Menu entry text."""
        return _COPY_LABELS[self]

    @property
    def is_corrected(self) -> bool:
        """Whether this copies the corrected text instead of what was read."""
        return self in (CopyKind.CORRECTED, CopyKind.CORRECTED_ANONYMIZED)

    @property
    def is_anonymized(self) -> bool:
        """Whether the text is anonymized before it is copied."""
        return self in (CopyKind.RAW_ANONYMIZED, CopyKind.CORRECTED_ANONYMIZED)


_COPY_LABELS = {
    CopyKind.RAW: "Copy what was read",
    CopyKind.RAW_ANONYMIZED: "Copy what was read, names hidden",
    CopyKind.CORRECTED: "Copy corrected text",
    CopyKind.CORRECTED_ANONYMIZED: "Copy corrected text, names hidden",
}

#: The two Save entries of the Copy menu, in menu order.
_SAVE_KINDS = (ExportKind.TEXT, ExportKind.TEXT_ANONYMIZED)
_SAVE_LABELS = {
    ExportKind.TEXT: "Save what was read as text file\u2026",
    ExportKind.TEXT_ANONYMIZED: "Save it with names hidden\u2026",
}

#: How long the "Copied" note stays next to the button.
_COPIED_MS = 3000
#: Width the document name is elided to; the header never grows with a long file name.
_NAME_WIDTH = 320


class ViewKind(Enum):
    """Which of the three texts is in front."""

    READ = "read"
    MARKED = "marked"
    FINAL = "final"

    @property
    def label(self) -> str:
        """The switch's label for this view."""
        return _VIEW_LABELS[self]


_VIEW_LABELS = {
    ViewKind.READ: txt.VIEW_READ,
    ViewKind.MARKED: txt.VIEW_MARKED,
    ViewKind.FINAL: txt.VIEW_FINAL,
}


@dataclass(frozen=True)
class CopyState:
    """What the Copy menu can offer for the open document."""

    has_raw: bool = False
    has_corrected: bool = False
    can_hide_names: bool = False

    def allows(self, kind: CopyKind) -> bool:
        """Whether *kind* can be copied: the text exists and, if asked, names can be hidden."""
        has_text = self.has_corrected if kind.is_corrected else self.has_raw
        return has_text and (self.can_hide_names or not kind.is_anonymized)

    def allows_save(self, kind: ExportKind) -> bool:
        """Whether *kind* can be written: the text exists and, if asked, names can be hidden."""
        return self.has_raw and (self.can_hide_names or kind is not ExportKind.TEXT_ANONYMIZED)


def _escape_is_for_the_focus(widget: QWidget | None) -> bool:
    """Whether Esc belongs to *widget* instead of meaning "back to Documents".

    Esc closes an open menu, and in a field the teacher can type in it is their own key
    (leaving a completion, dropping a selection). Only outside those does it leave the tab.
    """
    if QApplication.activePopupWidget() is not None:
        return True
    if isinstance(widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
        return not widget.isReadOnly()
    if isinstance(widget, QComboBox):
        return widget.isEditable()
    return False


def _item_at(items: Sequence[CheckItem], offset: int) -> str:
    """Return the key of the item whose corrected words hold *offset*, or ``""``."""
    for item in items:
        if any(start <= offset <= end for start, end in item.corrected_spans):
            return item.key
    return ""


class _Banner(QWidget):
    """One strip above the text: why it cannot be edited, or what it costs to edit it."""

    action_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)
        self._label = QLabel()
        self._label.setWordWrap(True)
        layout.addWidget(self._label, 1)
        self._button = QPushButton()
        self._button.clicked.connect(self.action_clicked)
        layout.addWidget(self._button)
        self.setVisible(False)

    def show_message(self, text: str, *, action: str = "") -> None:
        """Show *text*, with a button labelled *action* when there is something to do about it."""
        self._label.setText(text)
        self._button.setText(action)
        self._button.setVisible(bool(action))
        self.setVisible(True)

    def hide_message(self) -> None:
        """Take the strip away."""
        self.setVisible(False)

    @property
    def text(self) -> str:
        """What the strip says."""
        return self._label.text()

    @property
    def action(self) -> str:
        """The label of the strip's button, or ``""``."""
        return self._button.text() if not self._button.isHidden() else ""


class _SourceDialog(QDialog):
    """The stored text of the open view, read-only."""

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(txt.SOURCE_DIALOG_TITLE)
        self.resize(700, 500)
        layout = QVBoxLayout(self)
        self.view = QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setPlainText(text)
        layout.addWidget(self.view)
        close = QPushButton(check_text.SOURCE_CLOSE)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        layout.addLayout(row)


class CheckBody(QWidget):
    """The scan, the text and the side panel of one document."""

    raw_text_edited = Signal(str)
    """The transcription was typed into: the new text, in the notation of the document."""
    corrected_text_edited = Signal(str)
    """The final text was typed into: the new text, in the notation of the document."""
    anonymization_needed = Signal(str)
    """The names of this text have to be hidden, and the window has not asked yet."""
    display_anonymized_changed = Signal(bool)
    """The banner offered to show the names, and the teacher took it."""
    text_zoom_changed = Signal(int)
    """Ctrl+wheel changed the text size; the window stores it for the next document."""
    item_shown = Signal(str)
    """An item became the one the card shows: its key, or ``"minor"`` for the group row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: CheckDocument | None = None
        self._masking = Masking()
        self._view = ViewKind.READ
        self._lock = ""
        self._edit_allowed = False
        self._narrow = False
        self._scan_wanted: bool | None = None
        self._splitter_dragged = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self._show_scan_btn = QToolButton()
        self._show_scan_btn.setText(txt.SHOW_SCAN)
        self._show_scan_btn.clicked.connect(self._on_show_scan)
        self._show_scan_btn.setVisible(False)
        layout.addWidget(self._show_scan_btn, 0, Qt.AlignmentFlag.AlignLeft)

        self.banner = _Banner()
        self.banner.action_clicked.connect(self._on_banner_action)
        layout.addWidget(self.banner)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self.scan = ScanPane()
        self._splitter.addWidget(self.scan)

        self._stack = QStackedWidget()
        self.read_view = ReadView()
        self.marked_view = MarkedView()
        self.final_view = FinalView()
        for view in (self.read_view, self.marked_view, self.final_view):
            self._stack.addWidget(view)
        self._splitter.addWidget(self._stack)

        self.panel = SidePanel()
        self.panel.setMinimumWidth(_PANEL_WIDTH)
        self._splitter.addWidget(self.panel)
        layout.addWidget(self._splitter, 1)

        self.read_view.text_edited.connect(self.raw_text_edited)
        self.final_view.text_edited.connect(self.corrected_text_edited)
        for view in (self.read_view, self.marked_view, self.final_view):
            view.item_clicked.connect(self._on_item_clicked)
            view.zoom_changed.connect(self.text_zoom_changed)
        self.panel.item_chosen.connect(self._on_item_chosen)
        self.panel.item_shown.connect(self.item_shown)
        self.panel.take_out_requested.connect(self._on_take_out)
        self.panel.put_back_requested.connect(self._on_put_back)
        self.panel.use_suggestion_requested.connect(self._on_use_suggestion)
        self.panel.take_out_all_requested.connect(self._on_take_out_all)
        self.panel.change_requested.connect(self._on_change)

        self._undo_action = self._build_history_action(QKeySequence.StandardKey.Undo, self._on_undo)
        self._redo_action = self._build_history_action(QKeySequence.StandardKey.Redo, self._on_redo)
        document = self.final_view.document()
        document.undoAvailable.connect(self._refresh_history_actions)
        document.redoAvailable.connect(self._refresh_history_actions)

    def _build_history_action(self, key: QKeySequence.StandardKey, slot) -> QAction:
        """Return an action of the page for one of the corrected text's own history steps.

        The action belongs to the body, so it fires wherever the focus is inside it — the list,
        the card, the scan — and not only while the teacher types.  A text view that can be typed
        in keeps the keys for itself (Qt's shortcut override), which is what makes Ctrl+Z in
        *What was read* undo the transcription and not the corrected text.
        """
        action = QAction(self)
        action.setShortcuts(QKeySequence.keyBindings(key))
        action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        action.triggered.connect(slot)
        self.addAction(action)
        return action

    # ── What the page pushes ──────────────────────────────────────────────

    def show_document(self, doc: CheckDocument, masking: Masking, *, typed: bool = False) -> None:
        """Show *doc*: the scan, the text of the view in front, and the list of its items.

        Every text and the scan are filled from *doc*, also when it is the document that was
        open: a run may have read it again.  *typed* says *doc* is what the store made of the
        transcription the teacher just typed: that text is the one in the view and the scan
        has not changed, so neither is filled again — it would only move the cursor, decode
        every page again and send the scan back to page one.
        """
        same = self._doc is not None and self._doc.source_id == doc.source_id
        if not same:
            self._edit_allowed = False
        self._doc = doc
        self._masking = masking
        if not (typed and same):
            self.scan.set_pages(doc.pages, doc.original_pages, doc.preprocess.steps)
            self.read_view.set_text(masking.mask(doc.raw_text), doc.text_format)
        self.final_view.set_text(masking.mask(doc.corrected_text), doc.text_format)
        self._refresh_marks()
        self.panel.set_document(doc, masking)
        self._refresh()
        if masking.enabled and masking.needs_request(doc.raw_text):
            masking.mark_requested(doc.raw_text)
            self.anonymization_needed.emit(doc.raw_text)

    def update_document(self, doc: CheckDocument) -> None:
        """Show a stored edit of *doc*: the marks, the list and the marked text, never the text."""
        self._doc = doc
        self._refresh_marks()
        self.panel.set_document(doc, self._masking)
        self._refresh()

    def set_view(self, view: ViewKind) -> None:
        """Put one of the three texts in front, and the panel's tab that belongs to it."""
        self._view = view
        self._stack.setCurrentWidget(self._widget_of(view))
        if view is ViewKind.READ:
            self.panel.show_content()
        else:
            self.panel.show_corrections()
        self._refresh()

    def set_lock(self, reason: str | None) -> None:
        """Make the texts read-only while the run works on this document, or free them again."""
        self.flush_pending_edit()
        self._lock = reason or ""
        self._refresh()

    def set_masking(self, masking: Masking) -> None:
        """Show the texts with the placeholders of *masking*, or with the names again.

        Hiding the names is a change of what is shown, not of the text: both editable views
        are filled again, so the placeholders appear in the text the teacher reads.
        """
        self._masking = masking
        doc = self._doc
        if doc is None:
            return
        self.read_view.set_text(masking.mask(doc.raw_text), doc.text_format)
        self.final_view.set_text(masking.mask(doc.corrected_text), doc.text_format)
        self._refresh_marks()
        self.panel.set_document(doc, masking)
        self._refresh()

    def set_point_size(self, point_size: int) -> None:
        """Read the texts at *point_size* points."""
        for view in (self.read_view, self.marked_view, self.final_view):
            view.set_point_size(point_size)

    def flush_pending_edit(self) -> None:
        """Store a typed-but-uncommitted edit right now."""
        self.read_view.flush()
        self.final_view.flush()

    def mark_looked_at(self, key: str) -> bool:
        """Record that the teacher looked at *key*; return whether it was new.

        The document is updated in place — the list icons and the footer say so — and nothing is
        reloaded: the teacher is reading, not waiting for the disk.
        """
        doc = self._doc
        if doc is None or key in doc.work.looked_at:
            return False
        work = replace(doc.work, looked_at=doc.work.looked_at | {key})
        progress = looked_at_progress(countable_keys(doc.items), work.looked_at)
        self._doc = replace(doc, work=work, looked_at_progress=progress)
        self.panel.set_looked_at(work.looked_at, progress)
        return True

    def clear(self) -> None:
        """Empty the body: no document is open."""
        self._doc = None
        self._edit_allowed = False
        self._lock = ""
        self.scan.clear()
        self.panel.clear()
        self.banner.hide_message()

    # ── What the body shows ───────────────────────────────────────────────

    @property
    def view(self) -> ViewKind:
        """Which of the three texts is in front."""
        return self._view

    @property
    def chosen(self) -> str:
        """The key of the chosen item, or ``""``."""
        return self.panel.chosen

    def _widget_of(self, view: ViewKind) -> CheckTextView:
        """Return the widget that shows *view*."""
        return {
            ViewKind.READ: self.read_view,
            ViewKind.MARKED: self.marked_view,
            ViewKind.FINAL: self.final_view,
        }[view]

    def _refresh(self) -> None:
        """Bring the marks, the editability, the banners and the widths in line with the state."""
        self._refresh_marks()
        self._refresh_editability()
        self._refresh_banner()
        self._refresh_panel_visibility()
        self._apply_widths()

    def _refresh_marks(self) -> None:
        """Draw the marks of the review in the two views that carry them."""
        doc = self._doc
        if doc is None:
            return
        marks = marks_of(doc, self._masking) if doc.aligned is not None else ()
        plain = self._masking.mask(doc.corrected_plain)
        chosen = self.panel.chosen
        self.final_view.set_marks(marks, plain=plain, chosen=chosen)
        self.marked_view.show_document(doc, self._masking, chosen=chosen)
        self.panel.set_note("" if self.final_view.mapped else txt.COULD_NOT_MARK)

    def _refresh_editability(self) -> None:
        """Let the teacher type where the state allows it, and say why where it does not."""
        doc = self._doc
        if doc is None:
            self._refresh_history_actions()
            return
        locked = bool(self._lock)
        masked = self._masking.enabled
        reading_fix = doc.has_proofreading and not self._edit_allowed
        self.read_view.set_editable(not (locked or masked or reading_fix))
        self.final_view.set_editable(not (locked or masked))
        reason = ""
        if locked:
            reason = self._lock
        elif masked:
            reason = txt.SHOW_NAMES_TO_CHANGE
        self.panel.set_editable(not (locked or masked), reason=reason)
        self._refresh_history_actions()

    def _refresh_history_actions(self) -> None:
        """Let Ctrl+Z and redo work while the corrected text may be changed and has a history."""
        document = self.final_view.document()
        editable = self._doc is not None and not self.final_view.isReadOnly()
        self._undo_action.setEnabled(editable and document.isUndoAvailable())
        self._redo_action.setEnabled(editable and document.isRedoAvailable())

    def _refresh_banner(self) -> None:
        """Show the one strip that matters most: the lock, the names, the reading fix, a warning."""
        doc = self._doc
        if doc is None:
            self.banner.hide_message()
            return
        if self._lock:
            self.banner.show_message(self._lock)
            return
        if self._masking.enabled:
            self.banner.show_message(self._masking.note(), action=txt.SHOW_NAMES)
            return
        if doc.has_proofreading and not self._edit_allowed:
            self.banner.show_message(txt.READING_FIX_WARNING, action=txt.EDIT_ANYWAY)
            return
        if doc.warning:
            self.banner.show_message(doc.warning)
            return
        self.banner.hide_message()

    def _refresh_panel_visibility(self) -> None:
        """Hide the side panel on *What was read*, unless the document has a content review."""
        doc = self._doc
        wanted = self._view is not ViewKind.READ or bool(doc and doc.content_review.strip())
        self.panel.setVisible(wanted)

    def _on_banner_action(self) -> None:
        """Do what the strip's button offers."""
        if self._masking.enabled:
            self.display_anonymized_changed.emit(False)
            return
        self._edit_allowed = True
        self._refresh()

    # ── The widths ────────────────────────────────────────────────────────

    def _apply_widths(self) -> None:
        """Give the scan its share of the width, unless the teacher dragged the splitter."""
        if self._splitter_dragged:
            return
        total = max(self._splitter.width(), _NARROW_WIDTH)
        panel = 0 if self.panel.isHidden() else _PANEL_WIDTH
        share = _SCAN_SHARE_READ if self._view is ViewKind.READ else _SCAN_SHARE_OTHER
        scan = 0 if self.scan.isHidden() else int((total - panel) * share)
        self._splitter.setSizes([scan, max(total - panel - scan, 200), panel])

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        """Remember that the teacher chose the widths themselves."""
        self._splitter_dragged = True

    def resizeEvent(self, event) -> None:
        """Fold the scan below the narrow width, and open it again above it."""
        super().resizeEvent(event)
        narrow = event.size().width() < _NARROW_WIDTH
        if narrow != self._narrow:
            self._narrow = narrow
            self._scan_wanted = None
            self._update_scan_visibility()
            self._apply_widths()

    def _update_scan_visibility(self) -> None:
        """Show the scan unless the window is narrow and the teacher did not ask for it."""
        show = self._scan_wanted if self._scan_wanted is not None else not self._narrow
        self.scan.setVisible(show)
        self._show_scan_btn.setVisible(not show)

    def _on_show_scan(self) -> None:
        """Open the scan in a narrow window."""
        self._scan_wanted = True
        self._update_scan_visibility()
        self._apply_widths()

    # ── The items ─────────────────────────────────────────────────────────

    def _on_item_chosen(self, key: str) -> None:
        """A row of the list was chosen: mark it stronger and scroll the text to it."""
        self._refresh_marks()
        self._widget_of(self._view).reveal(key)

    def _on_item_clicked(self, key: str) -> None:
        """A mark in the text was clicked: choose its row."""
        self.panel.set_chosen(key)
        self._refresh_marks()

    def _item(self, key: str) -> CheckItem | None:
        """Return the item of *key*, or ``None``."""
        if self._doc is None:
            return None
        return next((item for item in self._doc.items if item.key == key), None)

    def _edits_for(self, item: CheckItem) -> tuple[TextEdit, ...]:
        """Return the edits that take out, put back or use the suggestion of *item*."""
        doc = self._doc
        if doc is None or doc.aligned is None:
            return ()
        aligned: AlignedReview = doc.aligned
        number = item.key.partition(":")[2]
        if item.kind in (ItemKind.CORRECTION, ItemKind.CHANGED):
            group = [change for change in aligned.changes if change.edit_id == int(number)]
            return take_out(group, doc.raw_plain, doc.corrected_plain)
        if item.kind in (ItemKind.OWN, ItemKind.UNEXPLAINED):
            start, _, end = number.partition("-")
            span = (int(start), int(end))
            group = [
                change
                for change in aligned.changes
                if change.edit_id is None and (change.hunk.raw_start, change.hunk.raw_end) == span
            ]
            return take_out(group, doc.raw_plain, doc.corrected_plain)
        if item.kind is ItemKind.TAKEN_OUT:
            note = next((note for note in aligned.orphan_notes if note.id == int(number)), None)
            return put_back(note, aligned, doc.raw_plain, doc.corrected_plain) if note else ()
        if item.kind is ItemKind.UNCLEAR:
            unclear = next(
                (entry for entry in aligned.unclear if entry.number == int(number)), None
            )
            return (
                use_suggestion(unclear, aligned, doc.raw_plain, doc.corrected_plain)
                if unclear
                else ()
            )
        return ()

    def _apply(self, edits: tuple[TextEdit, ...]) -> None:
        """Apply *edits* to the final text, which stores them through the path typing takes."""
        if not edits:
            return
        self.set_view(ViewKind.FINAL)
        self.final_view.apply_plain_edits(edits)

    def _on_take_out(self, key: str) -> None:
        """Put the student's words back for the item of *key*."""
        item = self._item(key)
        if item is not None:
            self._apply(self._edits_for(item))

    def _on_put_back(self, key: str) -> None:
        """Put the correction of *key* back into the text."""
        item = self._item(key)
        if item is not None:
            self._apply(self._edits_for(item))

    def _on_use_suggestion(self, key: str) -> None:
        """Write the suggested wording of the unclear passage of *key* into the text."""
        item = self._item(key)
        if item is not None:
            self._apply(self._edits_for(item))

    def _on_take_out_all(self) -> None:
        """Put the student's words back for every spelling and punctuation correction."""
        if self._doc is None:
            return
        edits: list[TextEdit] = []
        for item in group_minor(self._doc.items):
            edits.extend(self._edits_for(item))
        self._apply(tuple(edits))

    def _on_change(self, key: str) -> None:
        """Go to the words of *key* in the final text and select them."""
        self.set_view(ViewKind.FINAL)
        self.final_view.reveal(key)
        self.final_view.setFocus()

    # ── The corrected text's own history ──────────────────────────────────

    def _on_undo(self) -> None:
        """Undo the last change of the corrected text, from wherever the focus is."""
        self.final_view.document().undo()
        self.final_view.flush()
        self._choose_at_cursor()

    def _on_redo(self) -> None:
        """Redo the last undone change of the corrected text, from wherever the focus is."""
        self.final_view.document().redo()
        self.final_view.flush()
        self._choose_at_cursor()

    def _choose_at_cursor(self) -> None:
        """Choose the item whose words hold the cursor, so the teacher sees what came back."""
        doc = self._doc
        offset = self.final_view.plain_offset()
        if doc is None or offset is None:
            return
        key = _item_at(doc.items, offset)
        if key:
            self.panel.set_chosen(key)
            self._refresh_marks()


class CheckPage(QWidget):
    """One document, full width: the header above, the body below."""

    previous_requested = Signal()
    next_requested = Signal()
    copy_requested = Signal(object)  # CopyKind
    save_requested = Signal(object)  # ExportKind
    back_requested = Signal()
    raw_text_edited = Signal(str)
    corrected_text_edited = Signal(str)
    proofread_requested = Signal()
    proofreading_setup_requested = Signal()
    """Proofreading is not set up, and the teacher pressed the strip's button."""
    read_settings_requested = Signal()
    """The teacher wants to change how the pages are read."""
    stop_requested = Signal()
    """*Stop* in the run strip."""
    checked_toggled = Signal(bool)
    """The teacher pressed *Checked*: the state the document should have now."""
    display_anonymized_changed = Signal(bool)
    anonymization_needed = Signal(str)
    text_zoom_changed = Signal(int)
    item_shown = Signal(str)
    """An item became the one the card shows: its key, or ``"minor"`` for the group row."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._copy_actions: dict[CopyKind, QAction] = {}
        self._save_actions: dict[ExportKind, QAction] = {}
        self._doc: CheckDocument | None = None
        self._choice: ViewKind | None = None
        self._masking = Masking()
        self._lock = ""
        self._display_anonymized = True
        self._anonymized_available = False
        # Built by the header helpers below; declared here so the class owns them.
        self._hide_names_box: QCheckBox
        self._more_btn: QToolButton
        self._copy_btn: QToolButton
        self._copied_label: QLabel
        self._copied_timer: QTimer
        self._switch_group: QButtonGroup
        self._switch_buttons: dict[ViewKind, QPushButton]
        self._switch: QWidget
        self._source_action: QAction
        self._again_action: QAction

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._build_header())
        self._run_strip = RunStrip()
        self._run_strip.stop_requested.connect(self.stop_requested)
        layout.addWidget(self._run_strip)
        self._next_step = NextStepStrip()
        self._next_step.proofread_requested.connect(self.proofread_requested)
        self._next_step.set_up_requested.connect(self.proofreading_setup_requested)
        self._next_step.read_settings_requested.connect(self.read_settings_requested)
        # The strip's primary button: what a transcription waits for.
        self._proofread_btn = self._next_step.button
        layout.addWidget(self._next_step)
        self.body = CheckBody()
        layout.addWidget(self.body, 1)
        self.body.raw_text_edited.connect(self.raw_text_edited)
        self.body.corrected_text_edited.connect(self.corrected_text_edited)
        self.body.anonymization_needed.connect(self.anonymization_needed)
        self.body.display_anonymized_changed.connect(self.display_anonymized_changed)
        self.body.text_zoom_changed.connect(self.text_zoom_changed)
        self.body.item_shown.connect(self.item_shown)

    # ── Header ────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(6, 2, 6, 2)
        layout.setSpacing(8)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-weight: bold;")
        self._name_label.setMaximumWidth(_NAME_WIDTH)
        layout.addWidget(self._name_label)

        self._position_label = QLabel()
        self._position_label.setStyleSheet("color: #888;")
        self._position_label.setVisible(False)
        layout.addWidget(self._position_label)

        self._previous_btn = QToolButton()
        self._previous_btn.setText("\u25c0")
        self._previous_btn.setToolTip("Previous document in the queue")
        self._previous_btn.setEnabled(False)
        self._previous_btn.clicked.connect(self.previous_requested)
        layout.addWidget(self._previous_btn)

        self._next_btn = QToolButton()
        self._next_btn.setText("\u25b6")
        self._next_btn.setToolTip("Next document in the queue")
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self.next_requested)
        layout.addWidget(self._next_btn)

        layout.addWidget(self._build_switch())

        self._checked_btn = QPushButton(txt.MARK_AS_CHECKED)
        self._checked_btn.setCheckable(True)
        self._checked_btn.setToolTip(txt.CHECKED_TOOLTIP)
        self._checked_btn.setEnabled(False)
        self._checked_btn.clicked.connect(self.checked_toggled)
        layout.addWidget(self._checked_btn)

        layout.addStretch()
        self._build_header_tools(layout)
        return header

    def _build_header_tools(self, layout: QHBoxLayout) -> None:
        """Add what the header offers about the document: the names, more, and Copy."""
        self._hide_names_box = QCheckBox(txt.HIDE_PERSONAL_INFO_BUTTON)
        self._hide_names_box.setToolTip(txt.HIDE_PERSONAL_INFO_BUTTON_TIP)
        self._hide_names_box.setChecked(True)
        self._hide_names_box.toggled.connect(self._on_hide_names_toggled)
        layout.addWidget(self._hide_names_box)

        self._more_btn = QToolButton()
        self._more_btn.setText(check_text.MORE)
        self._more_btn.setToolTip(check_text.MORE_TIP)
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_btn.setMenu(self._build_more_menu())
        layout.addWidget(self._more_btn)

        self._copy_btn = QToolButton()
        self._copy_btn.setText("Copy")
        self._copy_btn.setToolTip("Copy the text of this document, or save it as a text file")
        self._copy_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._copy_btn.setMenu(self._build_copy_menu())
        layout.addWidget(self._copy_btn)

        self._copied_label = QLabel()
        self._copied_label.setStyleSheet("color: #888;")
        self._copied_label.setVisible(False)
        layout.addWidget(self._copied_label)

        self._copied_timer = QTimer(self)
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(_COPIED_MS)
        self._copied_timer.timeout.connect(lambda: self._copied_label.setVisible(False))

    def _build_switch(self) -> QWidget:
        """Return the three flat buttons that choose which text is in front."""
        switch = QWidget()
        layout = QHBoxLayout(switch)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._switch_group = QButtonGroup(self)
        self._switch_group.setExclusive(True)
        self._switch_buttons: dict[ViewKind, QPushButton] = {}
        for view in ViewKind:
            button = QPushButton(view.label)
            button.setCheckable(True)
            button.setFlat(True)
            button.clicked.connect(lambda _checked=False, v=view: self.set_view(v))
            self._switch_group.addButton(button)
            self._switch_buttons[view] = button
            layout.addWidget(button)
        self._switch = switch
        switch.setVisible(False)
        return switch

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)
        self._source_action = menu.addAction(txt.SHOW_MARKDOWN_SOURCE)
        assert self._source_action is not None
        self._source_action.triggered.connect(self._show_source)
        self._again_action = menu.addAction(txt.PROOFREAD_AGAIN)
        assert self._again_action is not None
        self._again_action.triggered.connect(self.proofread_requested)
        return menu

    def _build_copy_menu(self) -> QMenu:
        menu = QMenu(self)
        # A disabled entry explains itself: the tooltip is the eye button's, which points at
        # Settings → Add-ons, so the teacher knows where names could be hidden.
        menu.setToolTipsVisible(True)
        for kind in CopyKind:
            action = menu.addAction(kind.label)
            assert action is not None
            action.triggered.connect(lambda _checked=False, k=kind: self.copy_requested.emit(k))
            self._copy_actions[kind] = action
        menu.addSeparator()
        for kind in _SAVE_KINDS:
            action = menu.addAction(_SAVE_LABELS[kind])
            assert action is not None
            action.triggered.connect(lambda _checked=False, k=kind: self.save_requested.emit(k))
            self._save_actions[kind] = action
        return menu

    # ── What the window pushes ────────────────────────────────────────────

    def _show_header(self, name: str, source_id: str, position: int, total: int) -> None:
        """Write the name, the position and the two arrows of the header."""
        self._name_label.setToolTip(source_id)
        metrics = QFontMetrics(self._name_label.font())
        self._name_label.setText(
            metrics.elidedText(name, Qt.TextElideMode.ElideMiddle, _NAME_WIDTH)
        )
        self._position_label.setText(f"{position} of {total}" if total else "")
        self._position_label.setVisible(bool(total))
        self._previous_btn.setEnabled(position > 1)
        self._next_btn.setEnabled(0 < position < total)

    def show_document(
        self, doc: CheckDocument, position: int, total: int, *, typed: bool = False
    ) -> None:
        """Show one document: the header that names it, and the body that shows it.

        *typed* is passed on to :meth:`CheckBody.show_document`: the transcription in the view
        is the one the teacher just typed.
        """
        if self._doc is None or self._doc.source_id != doc.source_id:
            # The placeholders belong to the text of the document that was open.
            self._masking.forget()
        self._doc = doc
        self._show_header(doc.name, doc.source_id, position, total)
        self._switch.setVisible(doc.has_proofreading)
        self._show_next_step(doc)
        self._again_action.setEnabled(doc.has_proofreading)
        self._source_action.setEnabled(bool(doc.raw_text.strip()))
        self._refresh_checked()
        self.body.show_document(doc, self._masking, typed=typed)
        self._put_in_front(self._view_for(doc))

    def update_document(self, doc: CheckDocument) -> None:
        """Show a stored edit of the open document, without reloading the text being typed in."""
        self._doc = doc
        self._switch.setVisible(doc.has_proofreading)
        self._show_next_step(doc)
        self._again_action.setEnabled(doc.has_proofreading)
        self._refresh_checked()
        self.body.update_document(doc)
        if not doc.has_proofreading and self.body.view is not ViewKind.READ:
            self._put_in_front(ViewKind.READ)

    def _show_next_step(self, doc: CheckDocument) -> None:
        """Offer the next step over a transcription that has not been proofread yet.

        Not while a run holds the document: the run strip says what goes on, and a second
        request would only be refused.
        """
        self._next_step.setVisible(
            not doc.has_proofreading and bool(doc.raw_text.strip()) and not self._lock
        )

    def set_proofreading_ready(self, ready: bool) -> None:
        """Say whether proofreading is set up: the next-step button proofreads or sets it up."""
        self._next_step.set_ready(ready)

    def set_run(self, state: RunState | None) -> None:
        """Show what the run does with the open document, or nothing when it is not in a run."""
        self._run_strip.show_state(state)

    def _refresh_checked(self) -> None:
        """Show *Checked* as the document's work says, and let it be pressed where it may.

        The toggle marks what the document has now: with a proofreading the corrections, without
        one the transcription.  It is disabled while the run holds the document and while there
        is no text to check; hiding the names does not stop it, since it changes no text.
        """
        doc = self._doc
        label = doc.work.checked_label if doc is not None else ""
        self._checked_btn.setText(txt.checked_button_label(label))
        self._checked_btn.setChecked(bool(label))
        self._checked_btn.setEnabled(
            doc is not None and not self._lock and bool(doc.raw_text.strip())
        )

    def set_view(self, view: ViewKind) -> None:
        """Put one of the three texts in front, and remember the choice for the next document.

        A document without a proofreading has only one text to show; that is not a choice, so
        it is not remembered, and the next proofread document opens on the teacher's view.
        """
        if self._doc is None or self._doc.has_proofreading:
            self._choice = view
        self._put_in_front(view)

    def _put_in_front(self, view: ViewKind) -> None:
        """Show *view* (the only text there is for a document without a proofreading)."""
        if self._doc is not None and not self._doc.has_proofreading:
            view = ViewKind.READ
        self._switch_buttons[view].setChecked(True)
        self.body.set_view(view)

    def set_lock(self, reason: str | None) -> None:
        """Make the texts read-only while the run works on this document, or free them again."""
        self._lock = reason or ""
        if self._doc is not None:
            self._show_next_step(self._doc)
        self._refresh_checked()
        self.body.set_lock(reason)

    def set_anonymization(self, mapping, *, error: str = "") -> None:
        """Hand over the placeholders of the open document, or why there are none."""
        self._masking.set_result(mapping, error=error)
        self.body.set_masking(self._masking)

    def forget_anonymization(self) -> None:
        """Drop the placeholders: they belong to a text that is no longer shown."""
        self._masking.forget()
        self.body.set_masking(self._masking)

    def set_display_anonymized(self, enabled: bool) -> None:
        """Show the texts with placeholders, or with the names again."""
        self._display_anonymized = bool(enabled)
        self._sync_hide_names_box()
        self._masking.set_enabled(self._display_anonymized)
        self.body.set_masking(self._masking)

    def _sync_hide_names_box(self) -> None:
        """Tick the box for the stored wish, but only while names can be hidden.

        Without the add-on the box is greyed out and shows unticked (the names are shown); the
        wish itself is kept and the box takes it back when the add-on arrives.
        """
        self._hide_names_box.blockSignals(True)
        self._hide_names_box.setChecked(self._display_anonymized and self._anonymized_available)
        self._hide_names_box.blockSignals(False)

    def set_display_anonymized_available(self, available: bool) -> None:
        """Say whether names can be hidden at all; without the add-on they are shown."""
        self._anonymized_available = bool(available)
        self._hide_names_box.setEnabled(self._anonymized_available)
        self._sync_hide_names_box()
        self._hide_names_box.setToolTip(
            txt.HIDE_PERSONAL_INFO_BUTTON_TIP
            if self._anonymized_available
            else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
        )
        self._masking.set_available(self._anonymized_available)
        if not self._anonymized_available:
            self._masking.set_enabled(False)
            self.body.set_masking(self._masking)

    def set_point_size(self, point_size: int) -> None:
        """Read the texts at *point_size* points."""
        self.body.set_point_size(point_size)

    def flush_pending_edit(self) -> None:
        """Store a typed-but-uncommitted edit right now."""
        self.body.flush_pending_edit()

    def mark_looked_at(self, key: str) -> bool:
        """Record that the teacher looked at *key*; return whether it was new.

        Nothing is recorded while the run holds the document: the window writes only for
        documents the run does not hold.
        """
        if self._lock:
            return False
        return self.body.mark_looked_at(key)

    def refresh_checked(self) -> None:
        """Show *Checked* as the document says again, e.g. after a press that was not stored."""
        self._refresh_checked()

    def clear(self) -> None:
        """Empty the page: no document is open."""
        self._doc = None
        self._name_label.setText("")
        self._name_label.setToolTip("")
        self._position_label.setText("")
        self._position_label.setVisible(False)
        self._previous_btn.setEnabled(False)
        self._next_btn.setEnabled(False)
        self._switch.setVisible(False)
        self._next_step.setVisible(False)
        self._run_strip.show_state(None)
        self._refresh_checked()
        self.body.clear()
        self.set_copy_state(CopyState())

    def set_copy_state(self, state: CopyState) -> None:
        """Say what the Copy menu may offer for the open document."""
        for kind, action in self._copy_actions.items():
            action.setEnabled(state.allows(kind))
            action.setToolTip(
                ""
                if state.can_hide_names or not kind.is_anonymized
                else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
            )
        for kind, action in self._save_actions.items():
            action.setEnabled(state.allows_save(kind))
            action.setToolTip(
                ""
                if state.can_hide_names or kind is not ExportKind.TEXT_ANONYMIZED
                else txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP
            )

    def show_copied(self) -> None:
        """Confirm a copy next to the button, for a few seconds."""
        self._copied_label.setText("Copied")
        self._copied_label.setVisible(True)
        self._copied_timer.start()

    # ── The switch, the names and the source ──────────────────────────────

    def _view_for(self, doc: CheckDocument) -> ViewKind:
        """Return the view a document opens on: the remembered one, else *With corrections*."""
        if not doc.has_proofreading:
            return ViewKind.READ
        if self._choice is not None and self._choice is not ViewKind.READ:
            return self._choice
        return ViewKind.MARKED if self._choice is None else ViewKind.READ

    def _on_hide_names_toggled(self, checked: bool) -> None:
        """The teacher ticked or unticked *Hide names*."""
        self._display_anonymized = checked
        self.display_anonymized_changed.emit(checked)

    def _show_source(self) -> None:
        """Show the stored text of the view in front, read-only."""
        if self._doc is None:
            return
        text = (
            self._doc.corrected_text if self.body.view is not ViewKind.READ else self._doc.raw_text
        )
        _SourceDialog(text, self).exec()

    # ── Keys ──────────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Esc leaves the tab, unless the focused widget has its own use for it."""
        if event.key() == Qt.Key.Key_Escape and not _escape_is_for_the_focus(
            QApplication.focusWidget()
        ):
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
