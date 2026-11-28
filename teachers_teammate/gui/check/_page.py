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

from ...application.check_document import (
    CheckDocument,
    SentKind,
    SentView,
    sent_view_of,
)
from ...application.check_items import CheckItem, ItemKind, countable_keys, minor_corrections
from ...domain.page_layout import Mark
from ...domain.proofreading.carry import ReadingFix
from ...domain.proofreading.quotes import map_forward
from ...domain.proofreading.review import AlignedReview
from ...domain.proofreading.text_edits import TextEdit
from ...domain.teacher_work import (
    looked_at_progress,
    put_back,
    replace_words,
    take_out,
    use_suggestion,
)
from ...texts import check as check_text
from ...texts import compare as compare_text
from ...texts import review as txt
from .._export_menu import ExportKind
from .._handles import (
    CHECK_COMPARE_BUTTON,
    CHECKED_BUTTON,
    FINAL_VIEW,
    FINAL_VIEW_BUTTON,
    READ_VIEW,
)
from .._qt import present
from ._masking import Masking
from ._run_strip import PRIMARY_STYLE, NextStepStrip, RunState, RunStrip
from ._scan_link import mark_in_corrected, mark_in_transcription
from ._scan_view import ScanPane
from ._side_panel import SidePanel
from ._text_views import CheckTextView, FinalView, MarkedView, ReadView, marks_of

#: The side panel's share of the width, the least it may take, and the width below which the
#: scan folds to a strip.
_PANEL_SHARE = 0.28
_PANEL_MIN_WIDTH = 320
_NARROW_WIDTH = 1200
#: Share of the width the scan takes, on *What was read* and on the other two views.
_SCAN_SHARE_READ = 0.45
_SCAN_SHARE_OTHER = 0.30

#: What the line above the box says, per state of the box.
_SENT_CAPTIONS: dict[SentKind, str] = {
    SentKind.SENT: check_text.SENT_CAPTION,
    SentKind.WILL_BE_SENT: check_text.WILL_BE_SENT_CAPTION,
    SentKind.AS_WRITTEN: check_text.NAMES_SENT_AS_WRITTEN,
    SentKind.HIDDEN_HERE: check_text.NAMES_HIDDEN_HERE_CAPTION,
    SentKind.NAMES_SHOWN: check_text.NAMES_SHOWN_CAPTION,
    SentKind.UNAVAILABLE: check_text.NAMES_NOT_HIDDEN_CAPTION,
}

#: The states in which the box holds a text that is not the transcription: it cannot be typed in.
_SHOWS_SENT = (
    SentKind.SENT,
    SentKind.WILL_BE_SENT,
    SentKind.HIDDEN_HERE,
    SentKind.WORKING,
)


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
    CopyKind.RAW: check_text.COPY_RAW,
    CopyKind.RAW_ANONYMIZED: check_text.COPY_RAW_ANONYMIZED,
    CopyKind.CORRECTED: check_text.COPY_CORRECTED,
    CopyKind.CORRECTED_ANONYMIZED: check_text.COPY_CORRECTED_ANONYMIZED,
}

#: The two Save entries of the Copy menu, in menu order.
_SAVE_KINDS = (ExportKind.TEXT, ExportKind.TEXT_ANONYMIZED)
_SAVE_LABELS = {
    ExportKind.TEXT: check_text.SAVE_RAW,
    ExportKind.TEXT_ANONYMIZED: check_text.SAVE_ANONYMIZED,
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

    def show_message(self, text: str, *, action: str = "", reason: str = "") -> None:
        """Show *text*, with a button labelled *action* when there is something to do about it.

        A *reason* greys the button out and is its tooltip: the strip keeps its height, and the
        button says why it does nothing now.
        """
        self._label.setText(text)
        self._button.setText(action)
        self._button.setVisible(bool(action))
        self._button.setEnabled(not reason)
        self._button.setToolTip(reason)
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


@dataclass(frozen=True)
class CheckLayout:
    """How the Check tab's panes are sized: the three widths, and the card's height."""

    split: tuple[int, ...] = ()
    """The widths of the scan, the text and the side panel; empty for the default ones."""

    card_split: tuple[int, ...] = ()
    """The heights of the card and the list below it; empty for the default ones."""


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
    """An item became the one the card shows: its key."""
    hide_requested = Signal(str)
    """*Hide this* was chosen in *What was read*: the words to hide from now on."""
    keep_requested = Signal(str)
    """*Don't hide this* was chosen in *What was read*: the name to send as written."""
    task_text_again = Signal(bool)
    """The strip of a document's printed text was answered: ``True`` for *Proofread this after
    all*, ``False`` for *Leave it alone again*."""
    layout_changed = Signal(object)
    """The teacher dragged a pane: the :class:`CheckLayout` the tab has now."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._doc: CheckDocument | None = None
        self._masking = Masking()
        self._view = ViewKind.READ
        self._lock = ""
        self._edit_allowed = False
        #: What a reading fix left behind, shown once by the banner.
        self._notice = ""
        self._narrow = False
        self._scan_wanted: bool | None = None
        self._stored_split: tuple[int, ...] = ()

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
        self._banner_task: bool | None = None  # what the strip's button sends, on a task strip
        layout.addWidget(self.banner)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.splitterMoved.connect(self._on_splitter_moved)
        self.scan = ScanPane()
        self._splitter.addWidget(self.scan)

        self._stack = QStackedWidget()
        self.read_view = ReadView()
        self.read_view.setObjectName(READ_VIEW)
        self.marked_view = MarkedView()
        self.final_view = FinalView()
        self.final_view.setObjectName(FINAL_VIEW)
        for view in (self.read_view, self.marked_view, self.final_view):
            self._stack.addWidget(view)
        self._splitter.addWidget(self._build_text_pane())

        self.panel = SidePanel()
        self.panel.setMinimumWidth(_PANEL_MIN_WIDTH)
        self._splitter.addWidget(self.panel)
        layout.addWidget(self._splitter, 1)

        self._connect_views()

        self._undo_action = self._build_history_action(QKeySequence.StandardKey.Undo, self._on_undo)
        self._redo_action = self._build_history_action(QKeySequence.StandardKey.Redo, self._on_redo)
        document = self.final_view.document()
        document.undoAvailable.connect(self._refresh_history_actions)
        document.redoAvailable.connect(self._refresh_history_actions)

    def _build_text_pane(self) -> QWidget:
        """Return the pane of the three texts: the line about the box, then the box itself."""
        pane = QWidget()
        layout = QVBoxLayout(pane)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self._sent_caption = QLabel()
        self._sent_caption.setStyleSheet("color: #888;")
        self._sent_caption.setWordWrap(True)
        self._sent_caption.setVisible(False)
        layout.addWidget(self._sent_caption)
        layout.addWidget(self._stack, 1)
        return pane

    def _connect_views(self) -> None:
        """Pass on what the text views and the side panel report."""
        self.read_view.text_edited.connect(self.raw_text_edited)
        self.read_view.hide_requested.connect(self.hide_requested)
        self.read_view.keep_requested.connect(self.keep_requested)
        self.final_view.text_edited.connect(self.corrected_text_edited)
        for view in (self.read_view, self.marked_view, self.final_view):
            view.item_clicked.connect(self._on_item_clicked)
            view.zoom_changed.connect(self._on_zoomed)
            view.cursorPositionChanged.connect(self._on_cursor_moved)
        self.panel.item_chosen.connect(self._on_item_chosen)
        self.panel.item_shown.connect(self.item_shown)
        self.panel.take_out_requested.connect(self._on_take_out)
        self.panel.put_back_requested.connect(self._on_put_back)
        self.panel.use_suggestion_requested.connect(self._on_use_suggestion)
        self.panel.take_out_all_requested.connect(self._on_take_out_all)
        self.panel.replace_requested.connect(self._on_replace)
        self.panel.card_split_changed.connect(self._on_card_split_changed)

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

    def show_document(
        self,
        doc: CheckDocument,
        masking: Masking,
        *,
        typed: bool = False,
        fix: ReadingFix = ReadingFix.NONE,
    ) -> None:
        """Show *doc*: the scan, the text of the view in front, and the list of its items.

        Every text and the scan are filled from *doc*, also when it is the document that was
        open: a run may have read it again.  *typed* says *doc* is what the store made of the
        transcription the teacher just typed: that text is the one in the view and the scan
        has not changed, so neither is filled again — it would only move the cursor, decode
        every page again and send the scan back to page one.  *fix* is what that typing did to the
        proofreading, for the strip.
        """
        same = self._doc is not None and self._doc.source_id == doc.source_id
        if not same:
            self._edit_allowed = False
        self._notice = self._reading_fix_notice(same and typed, doc, fix)
        self._doc = doc
        self._masking = masking
        if not (typed and same):
            self.scan.set_pages(doc.pages, doc.original_pages, doc.preprocess.steps)
            self.read_view.show_sent(self.sent_view(doc), doc.text_format)
        self.final_view.set_text(masking.mask(doc.corrected_text), doc.text_format)
        self._refresh_marks()
        self.panel.set_document(doc, masking)
        self._refresh()
        self._request_placeholders(doc)

    def sent_view(self, doc: CheckDocument) -> SentView:
        """Return what the box of *What was read* shows for *doc*.

        The rule itself is in the application layer: the body only says what the window knows
        about hiding — whether a run does it, whether it is possible, whether it is on — and
        hands the anonymizer's answer along.
        """
        masking = self._masking
        return sent_view_of(
            doc,
            available=masking.available,
            run_hides=masking.run_hides,
            hiding=masking.enabled,
            preview=masking.preview,
        )

    @property
    def sent_kind(self) -> SentKind | None:
        """Return which text the box of *What was read* holds, or ``None`` without a document."""
        doc = self._doc
        return self.sent_view(doc).kind if doc is not None else None

    def _reading_fix_notice(self, typed_here: bool, doc: CheckDocument, fix: ReadingFix) -> str:
        """Return what the strip says about the proofreading a reading fix took, or ``""``.

        The command that stored the fix knows what became of the proofreading and why; the
        document only says whether it is still there.  The notice stays while the teacher keeps
        typing in a document that has none, so it does not flicker with every pause, and goes
        with the document or with a proofreading that is there again.
        """
        if not typed_here or doc.has_proofreading:
            return ""
        lost = self._doc is not None and self._doc.has_proofreading
        if lost and fix is ReadingFix.TOUCHED_CORRECTION:
            return txt.READING_FIX_FELL_BACK
        if lost and fix is ReadingFix.NOT_CARRIED:
            return txt.READING_FIX_NOT_CARRIED
        return self._notice

    def _request_placeholders(self, doc: CheckDocument) -> None:
        """Ask the window to hide the names of *doc* when they are to be hidden and are not yet."""
        masking = self._masking
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
        are filled again, so the placeholders appear in the text the teacher reads.  The box of
        *What was read* follows the rule for what a request is made from, which is not always a
        masked copy of the transcription.
        """
        self._masking = masking
        doc = self._doc
        if doc is None:
            return
        self.read_view.show_sent(self.sent_view(doc), doc.text_format)
        self.final_view.set_text(masking.mask(doc.corrected_text), doc.text_format)
        self._refresh_marks()
        self.panel.set_document(doc, masking)
        self._refresh()
        self._request_placeholders(doc)

    def set_point_size(self, point_size: int) -> None:
        """Read the texts at *point_size* points."""
        for view in (self.read_view, self.marked_view, self.final_view):
            view.set_point_size(point_size)

    def zoom_by(self, step: int) -> None:
        """Make all three texts *step* points larger or smaller."""
        self._widget_of(self._view).zoom_by(step)

    def _on_zoomed(self, point_size: int) -> None:
        """A text was zoomed: the other two follow, and the window stores the size."""
        self.set_point_size(point_size)
        self.text_zoom_changed.emit(point_size)

    def set_layout(self, layout: CheckLayout) -> None:
        """Give the panes the widths and the card the height the teacher chose last time."""
        self._stored_split = tuple(layout.split)
        self.panel.set_card_split(layout.card_split)
        self._apply_widths()

    @property
    def check_layout(self) -> CheckLayout:
        """How the panes are sized now."""
        return CheckLayout(tuple(self._splitter.sizes()), self.panel.card_split)

    def flush_pending_edit(self) -> None:
        """Store a typed-but-uncommitted edit right now."""
        self.read_view.flush()
        self.final_view.flush()

    def mark_looked_at(self, key: str) -> bool:
        """Record that the teacher looked at *key*; return whether it was new.

        The document is updated in place — the list and the footer say so — and nothing is
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
        self._notice = ""
        self.scan.clear()
        self.panel.clear()
        self.banner.hide_message()
        self._sent_caption.setVisible(False)

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
        self._refresh_scan_mark()
        self._refresh_sent_caption()
        self._refresh_hiding_rules()
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
        self.final_view.set_task_ranges(self._task_in_corrected(doc), plain=plain)
        self.read_view.set_task_ranges(doc.task_segments, plain=doc.raw_plain)
        self.marked_view.show_document(doc, self._masking, chosen=chosen)
        self.panel.set_note("" if self.final_view.mapped else txt.COULD_NOT_MARK)

    def _task_in_corrected(self, doc: CheckDocument) -> tuple[tuple[int, int], ...]:
        """Return the printed text's spans in the corrected text, as *Final text* shows it.

        The spans are offsets in the transcription; a correction moves the words after it, and
        hidden names shift them again, so both are carried the way the marks carry them.
        """
        hunks = tuple(change.hunk for change in doc.aligned.changes) if doc.aligned else ()
        return tuple(
            self._masking.carry(map_forward((start, end), hunks), doc.corrected_plain)
            for start, end in doc.task_segments
        )

    def _on_cursor_moved(self) -> None:
        """The cursor moved in one of the texts: the scan follows the view that is in front."""
        self._refresh_scan_mark()

    def _refresh_scan_mark(self) -> None:
        """Mark on the scan where the sentence under the cursor of the view in front was read."""
        doc = self._doc
        self.scan.view.mark(None if doc is None else self._mark_at_cursor(doc))

    def _mark_at_cursor(self, doc: CheckDocument) -> Mark | None:
        """Return what the scan draws for the sentence the cursor of the view in front is in.

        The two views of the corrected text stand on the words at the same place in the
        transcription; *What was read* stands on a text of its own, which is diffed against the
        transcription once and then remembered.  ``None`` where the place is not sure.
        """
        view = self._widget_of(self._view)
        if self._view is ViewKind.READ:
            offset = view.plain_offset()
            if offset is None:
                return None
            return mark_in_transcription(doc, offset, view.plain)
        offset = view.corrected_offset()
        if offset is None:
            return None
        return mark_in_corrected(doc, offset, self._masking)

    def _refresh_sent_caption(self) -> None:
        """Say above the box what it holds: how the text was sent, or why the names stand in it.

        Only over *What was read*: the other two views always show the proofread text, with the
        placeholders of the anonymizer's map of today, which a stored proofreading made with
        other settings need not share; hovering a placeholder names its original.  A text with
        crossed-out words gets one line more: those words never leave this computer.
        """
        lines: list[str] = []
        doc = self._doc
        if doc is not None and self._view is ViewKind.READ:
            sent = self.sent_view(doc)
            caption = _SENT_CAPTIONS.get(sent.kind, "")
            if caption:
                lines.append(caption)
            if sent.struck:
                lines.append(check_text.CROSSED_OUT_NOT_SENT)
        self._sent_caption.setText("\n".join(lines))
        self._sent_caption.setVisible(bool(lines))

    def _refresh_hiding_rules(self) -> None:
        """Offer *Hide this* and *Don't hide this* only where the next send obeys them."""
        self.read_view.set_rules_open(self._masking.available and self._masking.run_hides)

    def _refresh_editability(self) -> None:
        """Let the teacher type where the state allows it, and say why where it does not.

        The reason is a tooltip on the two texts and on the card's buttons: the banner is for
        what the teacher has to read, and a reason that comes and goes with the 👁 toggle would
        move the texts under the cursor.
        """
        doc = self._doc
        if doc is None:
            self._refresh_history_actions()
            return
        locked = bool(self._lock)
        masked = self._masking.enabled
        sent = self.sent_view(doc).kind in _SHOWS_SENT
        reading_fix = doc.has_proofreading and not self._edit_allowed
        reason = ""
        if locked:
            reason = self._lock
        elif masked:
            reason = txt.SHOW_NAMES_TO_CHANGE
        # The box holds a text that is not the transcription; typing there would store the
        # placeholders in what the teacher wrote.
        read_reason = self._lock if locked else (txt.SHOW_NAMES_TO_CHANGE if sent else "")
        self.read_view.set_editable(
            not (locked or sent or reading_fix),
            reason=read_reason or txt.READING_FIX_WARNING,
        )
        self.final_view.set_editable(not (locked or masked), reason=reason)
        self.panel.set_editable(not (locked or masked), reason=reason)
        self._refresh_history_actions()

    def _refresh_history_actions(self) -> None:
        """Let Ctrl+Z and redo work while the corrected text may be changed and has a history."""
        document = self.final_view.document()
        editable = self._doc is not None and not self.final_view.isReadOnly()
        self._undo_action.setEnabled(editable and document.isUndoAvailable())
        self._redo_action.setEnabled(editable and document.isRedoAvailable())

    def _refresh_banner(self) -> None:
        """Show the one strip that matters most: the lock, a failed hide, a lost proofreading, a
        warning.

        Hiding the names is not a reason of its own: the 👁 toggle shows it, and a strip that
        came and went with it moved the texts.  A hide that failed is one, because the names
        then stand in the text while the toggle says they are hidden.  The notice is what a
        reading fix left behind: it is shown once, until the next document or the next fix.
        """
        doc = self._doc
        self._banner_task = None
        if doc is None:
            self.banner.hide_message()
            return
        if self._lock:
            self.banner.show_message(self._lock)
            return
        if self._masking.error:
            self.banner.show_message(self._masking.note(), action=txt.SHOW_NAMES)
            return
        if self._notice:
            self.banner.show_message(self._notice)
            return
        if doc.has_proofreading and not self._edit_allowed:
            self.banner.show_message(
                txt.READING_FIX_WARNING,
                action=txt.EDIT_ANYWAY,
                reason=txt.SHOW_NAMES_TO_CHANGE if self._masking.enabled else "",
            )
            return
        if doc.warning:
            self.banner.show_message(doc.warning)
            return
        task = check_text.task_text_notice(len(doc.task_segments))
        if task:
            self.banner.show_message(task, action=check_text.PROOFREAD_TASK_TEXT_AGAIN)
            self._banner_task = True
            return
        if doc.task_text_taken_back:
            self.banner.show_message(
                check_text.TASK_TEXT_PROOFREAD, action=check_text.LEAVE_TASK_TEXT_ALONE
            )
            self._banner_task = False
            return
        self.banner.hide_message()

    def _refresh_panel_visibility(self) -> None:
        """Hide the side panel on *What was read*, unless the document has a content review."""
        doc = self._doc
        wanted = self._view is not ViewKind.READ or bool(doc and doc.content_review.strip())
        self.panel.setVisible(wanted)

    def _on_banner_action(self) -> None:
        """Do what the strip's button offers: show the names, edit anyway, or switch the print."""
        if self._masking.error:
            self.display_anonymized_changed.emit(False)
            return
        if self._banner_task is not None:
            self.task_text_again.emit(self._banner_task)
            return
        self._edit_allowed = True
        self._refresh()

    # ── The widths ────────────────────────────────────────────────────────

    def _apply_widths(self) -> None:
        """Give the panes the widths the teacher chose, or the scan its share of the width."""
        self._splitter.setSizes(self._shown_sizes(self._stored_split or self._default_widths()))

    def _default_widths(self) -> tuple[int, int, int]:
        """Return the widths of a window nobody dragged: the panel's share, then the scan's."""
        total = max(self._splitter.width(), _NARROW_WIDTH)
        panel = max(int(total * _PANEL_SHARE), _PANEL_MIN_WIDTH)
        share = _SCAN_SHARE_READ if self._view is ViewKind.READ else _SCAN_SHARE_OTHER
        scan = int((total - panel) * share)
        return scan, max(total - panel - scan, 200), panel

    def _shown_sizes(self, sizes: Sequence[int]) -> list[int]:
        """Return *sizes* with the panes that are hidden set to nothing."""
        hidden = (self.scan.isHidden(), False, self.panel.isHidden())
        return [0 if hide else size for size, hide in zip(sizes, hidden, strict=False)]

    def _on_splitter_moved(self, _position: int, _index: int) -> None:
        """Remember the widths the teacher dragged the panes to."""
        sizes = tuple(self._splitter.sizes())
        if all(size > 0 for size in sizes):
            self._stored_split = sizes
            self.layout_changed.emit(self.check_layout)

    def _on_card_split_changed(self, _sizes: tuple[int, ...]) -> None:
        """The teacher dragged the card's height: tell the window, which remembers it."""
        self.layout_changed.emit(self.check_layout)

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
        """Apply *edits* to the final text, which stores them through the path typing takes.

        The final text is edited where it stands, also while another view is in front: the
        teacher keeps the view they were reading, and the marks of the others come back with
        the stored document.  *Final text* is reached through the switch, never as a side
        effect of an action.
        """
        if not edits:
            return
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
        for item in minor_corrections(self._doc.items):
            edits.extend(self._edits_for(item))
        self._apply(tuple(edits))

    def _on_replace(self, key: str, words: str) -> None:
        """Write *words* in place of the words of *key*, as one plain edit."""
        item = self._item(key)
        if item is not None:
            self._apply(self._replace_edits(item, words))

    def _replace_edits(self, item: CheckItem, words: str) -> tuple[TextEdit, ...]:
        """Return the edit that puts *words* in place of the words of *item*."""
        doc = self._doc
        if doc is None or doc.aligned is None:
            return ()
        return replace_words(
            item.corrected_spans, item.raw_span, doc.aligned, doc.corrected_plain, words
        )

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
    compare_requested = Signal()
    """The teacher wants to see several models on this document's text."""
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
    hide_requested = Signal(str)
    """*Hide this* was chosen in *What was read*: the words to hide from now on."""
    keep_requested = Signal(str)
    """*Don't hide this* was chosen in *What was read*: the name to send as written."""
    task_text_again = Signal(bool)
    """The strip of the document's printed text was answered (see ``CheckBody``)."""
    text_zoom_changed = Signal(int)
    item_shown = Signal(str)
    """An item became the one the card shows: its key."""
    layout_changed = Signal(object)
    """The teacher dragged a pane: the :class:`CheckLayout` the tab has now."""

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
        self._again_btn: QPushButton

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
        self.body.anonymization_needed.connect(self._refresh_hide_names_box)
        self.body.hide_requested.connect(self.hide_requested)
        self.body.keep_requested.connect(self.keep_requested)
        self.body.task_text_again.connect(self.task_text_again)
        self.body.display_anonymized_changed.connect(self.display_anonymized_changed)
        self.body.text_zoom_changed.connect(self.text_zoom_changed)
        self.body.item_shown.connect(self.item_shown)
        self.body.layout_changed.connect(self.layout_changed)

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
        self._previous_btn.setText(check_text.PREVIOUS_ARROW)
        self._previous_btn.setToolTip(check_text.PREVIOUS_DOCUMENT)
        self._previous_btn.setEnabled(False)
        self._previous_btn.clicked.connect(self.previous_requested)
        layout.addWidget(self._previous_btn)

        self._next_btn = QToolButton()
        self._next_btn.setText(check_text.NEXT_ARROW)
        self._next_btn.setToolTip(check_text.NEXT_DOCUMENT)
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self.next_requested)
        layout.addWidget(self._next_btn)

        layout.addWidget(self._build_switch())

        self._checked_btn = QPushButton(txt.MARK_AS_CHECKED)
        self._checked_btn.setObjectName(CHECKED_BUTTON)
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
        self._again_btn = QPushButton(txt.PROOFREAD_AGAIN)
        self._again_btn.setStyleSheet(PRIMARY_STYLE)
        self._again_btn.clicked.connect(self.proofread_requested)
        self._again_btn.setVisible(False)
        layout.addWidget(self._again_btn)

        self._compare_btn = QPushButton(compare_text.COMPARE_MODELS)
        self._compare_btn.setObjectName(CHECK_COMPARE_BUTTON)
        self._compare_btn.setToolTip(compare_text.COMPARE_MODELS_TIP)
        self._compare_btn.clicked.connect(self.compare_requested)
        self._compare_btn.setVisible(False)
        layout.addWidget(self._compare_btn)

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

        for text, tip, step in (
            (check_text.TEXT_ZOOM_OUT, check_text.TEXT_ZOOM_OUT_TIP, -1),
            (check_text.TEXT_ZOOM_IN, check_text.TEXT_ZOOM_IN_TIP, 1),
        ):
            zoom = QToolButton()
            zoom.setText(text)
            zoom.setToolTip(tip)
            zoom.clicked.connect(lambda _checked=False, n=step: self.body.zoom_by(n))
            layout.addWidget(zoom)

        self._copy_btn = QToolButton()
        self._copy_btn.setText(check_text.COPY)
        self._copy_btn.setToolTip(check_text.COPY_TOOLTIP)
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
            if view is ViewKind.FINAL:
                button.setObjectName(FINAL_VIEW_BUTTON)
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
        self._source_action = present(menu.addAction(txt.SHOW_MARKDOWN_SOURCE), "source entry")
        self._source_action.triggered.connect(self._show_source)
        return menu

    def _build_copy_menu(self) -> QMenu:
        menu = QMenu(self)
        # A disabled entry explains itself: the tooltip is the eye button's, which points at
        # Settings → Add-ons, so the teacher knows where names could be hidden.
        menu.setToolTipsVisible(True)
        for kind in CopyKind:
            action = present(menu.addAction(kind.label), "copy entry")
            action.triggered.connect(lambda _checked=False, k=kind: self.copy_requested.emit(k))
            self._copy_actions[kind] = action
        menu.addSeparator()
        for kind in _SAVE_KINDS:
            action = present(menu.addAction(_SAVE_LABELS[kind]), "save entry")
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
        self,
        doc: CheckDocument,
        position: int,
        total: int,
        *,
        typed: bool = False,
        fix: ReadingFix = ReadingFix.NONE,
    ) -> None:
        """Show one document: the header that names it, and the body that shows it.

        *typed* and *fix* are passed on to :meth:`CheckBody.show_document`: the transcription in
        the view is the one the teacher just typed, and *fix* is what that did to the proofreading.
        """
        if self._doc is None or self._doc.source_id != doc.source_id:
            # The placeholders belong to the text of the document that was open.
            self._masking.forget()
        self._doc = doc
        self._show_header(doc.name, doc.source_id, position, total)
        self._switch.setVisible(doc.has_proofreading)
        self._show_next_step(doc)
        self._show_again(doc)
        self._source_action.setEnabled(bool(doc.raw_text.strip()))
        self._refresh_checked()
        self.body.show_document(doc, self._masking, typed=typed, fix=fix)
        self._put_in_front(self._view_for(doc))

    def update_document(self, doc: CheckDocument) -> None:
        """Show a stored edit of the open document, without reloading the text being typed in."""
        self._doc = doc
        self._switch.setVisible(doc.has_proofreading)
        self._show_next_step(doc)
        self._show_again(doc)
        self._refresh_checked()
        self.body.update_document(doc)
        if not doc.has_proofreading and self.body.view is not ViewKind.READ:
            self._put_in_front(ViewKind.READ)

    def _show_again(self, doc: CheckDocument) -> None:
        """Offer *Proofread again* and *Compare models…* over a proofread document."""
        offer = doc.has_proofreading and not self._lock
        self._again_btn.setVisible(offer)
        self._compare_btn.setVisible(offer)

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
            self._show_again(self._doc)
        self._refresh_checked()
        self.body.set_lock(reason)

    def set_anonymization(self, mapping, *, text: str = "", error: str = "") -> None:
        """Hand over the placeholders of the open document and the text they are in, or why not."""
        self._masking.set_result(mapping, text=text, error=error)
        self.body.set_masking(self._masking)
        self._refresh_hide_names_box()

    def forget_anonymization(self) -> None:
        """Drop the placeholders: they belong to a text that is no longer shown."""
        self._masking.forget()
        self.body.set_masking(self._masking)
        self._refresh_hide_names_box()

    @property
    def sent_kind(self) -> SentKind | None:
        """Return which text the box of *What was read* holds, or ``None`` without a document."""
        return self.body.sent_kind

    def set_display_anonymized(self, enabled: bool) -> None:
        """Show the texts with placeholders, or with the names again."""
        self._display_anonymized = bool(enabled)
        self._sync_hide_names_box()
        self._masking.set_enabled(self._display_anonymized)
        self.body.set_masking(self._masking)
        self._refresh_hide_names_box()

    def _sync_hide_names_box(self) -> None:
        """Tick the box for the stored wish, but only while names can be hidden.

        Without the add-on the box is greyed out and shows unticked (the names are shown); the
        wish itself is kept and the box takes it back when the add-on arrives.
        """
        self._hide_names_box.blockSignals(True)
        self._hide_names_box.setChecked(self._display_anonymized and self._anonymized_available)
        self._hide_names_box.blockSignals(False)

    def set_display_anonymized_available(self, available: bool, *, run_hides: bool) -> None:
        """Say whether names can be hidden at all and whether a run hides them.

        Without the add-on the names are shown.  *run_hides* is the run's own setting, which
        the box of *What was read* needs to say what will be sent.
        """
        self._masking.set_run_hides(run_hides)
        self._anonymized_available = bool(available)
        self._hide_names_box.setEnabled(self._anonymized_available)
        self._sync_hide_names_box()
        self._masking.set_available(self._anonymized_available)
        if not self._anonymized_available:
            self._masking.set_enabled(False)
            self.body.set_masking(self._masking)
        self._refresh_hide_names_box()

    def _refresh_hide_names_box(self) -> None:
        """Say on the 👁 toggle what hiding the names is doing: unavailable, working or done.

        It is the toggle and not the strip above the texts, so the texts stay where they are.
        """
        if not self._anonymized_available:
            tip, label = txt.HIDE_PERSONAL_INFO_UNAVAILABLE_TIP, txt.HIDE_PERSONAL_INFO_BUTTON
        elif self._masking.working:
            tip, label = txt.HIDING_PERSONAL_INFO, txt.HIDE_PERSONAL_INFO_BUTTON_WORKING
        else:
            tip, label = txt.HIDE_PERSONAL_INFO_BUTTON_TIP, txt.HIDE_PERSONAL_INFO_BUTTON
        self._hide_names_box.setToolTip(tip)
        self._hide_names_box.setText(label)

    def set_point_size(self, point_size: int) -> None:
        """Read the texts at *point_size* points."""
        self.body.set_point_size(point_size)

    def set_layout(self, layout: CheckLayout) -> None:
        """Give the panes the widths and the card the height the teacher chose last time."""
        self.body.set_layout(layout)

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
        self._again_btn.setVisible(False)
        self._compare_btn.setVisible(False)
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
        self._copied_label.setText(check_text.COPIED)
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
