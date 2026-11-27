"""The window that compares proofreading models on one text: its models, its tabs, its footer.

The teacher opens it from the Tools menu or from the Check tab's *Compare models…*, on a
document they have read.  It asks the ticked models one after another, shows where their answers
differ and what each of them cost, and lets the teacher keep one of them for proofreading — the
document's own proofreading is not touched.

The window decides nothing about a comparison: which models can answer, what is stored and what a
model's answer came to are the service's (``application/model_comparison.py``).  It shows what it
is given, and writes the two settings keys through the main window, which owns them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...application.model_comparison import (
    Candidate,
    ComparedRun,
    CompareSource,
    ModelComparisonService,
)
from ...config import Config
from ...domain.proofreading.model_comparison import Comparison, Option
from ...domain.proofreading.option_marks import Verdict
from ...interfaces import LoggingReporter
from ...messages import log_failure
from ...texts import compare as txt
from ...texts import dialogs as dlg
from ...texts.messages import render
from .. import _dialogs
from .._background import LatestTask
from .._errors import show_failure
from .._handles import (
    COMPARE_AGAIN_BUTTON,
    COMPARE_DOCUMENT,
    COMPARE_MODELS_LIST,
    COMPARE_RUN_BUTTON,
    COMPARE_STOP_BUTTON,
    COMPARE_USE_BUTTON,
    COMPARE_WINDOW,
)
from .._long_job import ABORTED
from ._changes import ChangesPanel
from ._job import CompareJob
from ._models import ModelsPanel
from ._overview import OverviewPanel
from ._side_by_side import SideBySidePanel

__all__ = ["CompareHooks", "CompareWindow"]

_logger = logging.getLogger(__name__)

#: How long closing the window waits for the model being asked to let go.
_CLOSE_WAIT_MS = 5000


@dataclass(frozen=True)
class CompareHooks:
    """What the window asks of the main window, as the main window hands it over."""

    service: ModelComparisonService
    """The text, the answers of the ticked models, and the comparison of what is stored."""

    candidates: Callable[[Config, CompareSource], Sequence[Candidate]]
    """The models that can answer about a text, the best fitting first, from the lists held."""

    refresh_models: Callable[[Config], None]
    """Ask again for the model lists :attr:`candidates` reads; may contact a server."""

    config: Callable[[], Config]
    """The teacher's settings as they stand, read when the window needs them."""

    documents: Callable[[], Sequence[tuple[str, str]]]
    """``(source_id, name)`` of every document that has been read, in the order of the queue."""

    use_model: Callable[[str, str], None]
    """Store *provider* and *model* as the settings that proofread; the window owns the keys."""

    say: Callable[[str], None]
    """Say a line in the main window's status bar."""

    busy: Callable[[], bool]
    """Whether a run is going, so a comparison must not start."""

    warn_busy: Callable[[], None]
    """Tell the teacher that the run is busy and nothing was started."""

    confirm_sending: Callable[[Config, Sequence[Candidate]], bool]
    """Ask before the text goes to the online models among these, as a run asks before it sends;
    ``False`` when the teacher declines, so nothing is asked."""


class _Reporter(QObject):
    """Carries the narrative of a proofreading to the window's progress line.

    The unit reports from the job's thread; the line lives on the GUI thread, so the signal is
    queued.  Warnings and errors go to the log, as a run's do: a model's row says what became of
    it, and the log file keeps the rest.
    """

    said = Signal(str)

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._log = LoggingReporter()

    def status(self, line: str) -> None:
        """Show a progress line of the answer that is being made."""
        self.said.emit(line)

    def warn(self, line: str) -> None:
        """Write a warning of the answer down."""
        self._log.warn(line)

    def error(self, line: str) -> None:
        """Write a failure of the answer down."""
        self._log.error(line)


class CompareWindow(QWidget):
    """The models of this computer, the answers stored for one text, and how they differ."""

    def __init__(self, hooks: CompareHooks, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self._hooks = hooks
        self._open = ""
        self._source: CompareSource | None = None
        self._labels: dict[str, str] = {}
        self._job: CompareJob | None = None
        #: The tables are built off the GUI thread: a long text with several models takes seconds.
        self._tables = LatestTask(self)
        self._tables_pending = False
        #: The model lists expire, so they are asked for again whenever a document is opened.
        self._lists = LatestTask(self, network=True)
        self._models_pending = False
        self._listed: tuple[Candidate, ...] = ()
        self._reporter = _Reporter(self)
        self._reporter.said.connect(self._on_line)
        self.setWindowTitle(txt.TITLE)
        self.setObjectName(COMPARE_WINDOW)
        self._build()
        self.resize(1180, 760)

    # ── what the window is made of ─────────────────────────────────────────────

    def _build(self) -> None:
        """Build the header, the models, the three tabs and the footer."""
        self._build_header()
        self._models = ModelsPanel()
        self._models.setObjectName(COMPARE_MODELS_LIST)
        self._models.row_chosen.connect(self._refresh_footer)
        self._run_btn = QPushButton(txt.RUN_SELECTED)
        self._run_btn.setObjectName(COMPARE_RUN_BUTTON)
        self._run_btn.clicked.connect(self.run_selected)
        self._again_btn = QPushButton(txt.RUN_AGAIN)
        self._again_btn.setObjectName(COMPARE_AGAIN_BUTTON)
        self._again_btn.setEnabled(False)
        self._again_btn.clicked.connect(self.run_again)
        self._stop_btn = QPushButton(txt.STOP)
        self._stop_btn.setObjectName(COMPARE_STOP_BUTTON)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self.stop)
        buttons = QHBoxLayout()
        buttons.addWidget(self._run_btn)
        buttons.addWidget(self._again_btn)
        buttons.addWidget(self._stop_btn)
        buttons.addStretch()

        self._line = QLabel()
        self._line.setStyleSheet("color: #888;")

        self._changes = ChangesPanel()
        self._changes.mark_requested.connect(self._on_mark_requested)
        self._side_by_side = SideBySidePanel()
        self._overview = OverviewPanel()
        self._tabs = QTabWidget()
        self._tabs.addTab(self._changes, txt.TAB_CHANGES)
        self._tabs.addTab(self._side_by_side, txt.TAB_SIDE_BY_SIDE)
        self._tabs.addTab(self._overview, txt.TAB_OVERVIEW)

        self._use_btn = QPushButton(txt.use_model(""))
        self._use_btn.setObjectName(COMPARE_USE_BUTTON)
        self._use_btn.setEnabled(False)
        self._use_btn.clicked.connect(self.use_chosen)
        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(self._use_btn)

        layout = QVBoxLayout(self)
        layout.addLayout(self._row)
        layout.addWidget(self._models, stretch=1)
        layout.addLayout(buttons)
        layout.addWidget(self._line)
        layout.addWidget(self._tabs, stretch=3)
        layout.addLayout(footer)

    def _build_header(self) -> None:
        """Build the line that says which document is compared, and what it costs."""
        self._document = QComboBox()
        self._document.setObjectName(COMPARE_DOCUMENT)
        self._document.currentIndexChanged.connect(lambda _index: self._chosen())
        self._summary = QLabel()
        self._summary.setStyleSheet("color: #888;")
        self._row = QHBoxLayout()
        self._row.addWidget(QLabel(txt.DOCUMENT_LABEL))
        self._row.addWidget(self._document)
        self._row.addWidget(self._summary, stretch=1)

    # ── the document the window is about ───────────────────────────────────────

    def open_on(self, source_id: str) -> None:
        """Fill the window for the document *source_id*, which the teacher has read.

        While models are being asked the window stays on its document: the answers that come in
        belong to it.
        """
        if self.running():
            self.show_and_raise()
            return
        self._fill_documents(source_id)
        self.show_and_raise()
        self.reload()

    def show_and_raise(self) -> None:
        """Show the window, or bring it to the front when it is already open."""
        self.show()
        self.raise_()
        self.activateWindow()

    def _fill_documents(self, chosen: str) -> None:
        """List the documents that can be compared, with *chosen* in front."""
        known = list(self._hooks.documents())
        if chosen and all(source_id != chosen for source_id, _name in known):
            known.append((chosen, Path(chosen).name))
        self._document.blockSignals(True)
        try:
            self._document.clear()
            for source_id, name in known:
                self._document.addItem(name, source_id)
            index = next(
                (at for at, (source_id, _name) in enumerate(known) if source_id == chosen), -1
            )
            self._document.setCurrentIndex(index if known else -1)
        finally:
            self._document.blockSignals(False)
        self._open = self._document.currentData() or ""

    def _chosen(self) -> None:
        """Another document was picked in the header."""
        self._open = self._document.currentData() or ""
        self.reload()

    def reload(self) -> None:
        """Read the document, the models and the stored answers, and show them.

        The models come from the lists the app holds; fresh ones are asked for in the background
        and replace them when they differ.
        """
        config = self._hooks.config()
        self._source = self._hooks.service.source(config, Path(self._open)) if self._open else None
        if self._source is None:
            self._labels = {}
            self._listed = ()
            self._models.show_candidates((), {})
            self._lists.cancel()
            self._models_pending = False
            self._tables.cancel()
            self._tables_pending = False
            self._show_tables(None)
            self._summary.setText(txt.NOT_READ)
            self._refresh_footer()
            return
        self._show_candidates(tuple(self._hooks.candidates(config, self._source)))
        self._models_pending = True
        self._lists.submit(lambda: self._hooks.refresh_models(config), self._on_lists_refreshed)

    def _show_candidates(self, candidates: tuple[Candidate, ...]) -> None:
        """List *candidates* and show the stored answers of the open document."""
        source = self._source
        if source is None:
            return
        self._listed = candidates
        self._labels = {candidate.key: candidate.model for candidate in candidates}
        stored = source.stored
        if stored is not None:
            # The model that made the document's proofreading is the one whose key it carries; with
            # other settings than today's it may be none of the listed ones.
            made_by = self._labels.get(stored.key)
            self._labels[stored.key] = (
                txt.own_version(made_by) if made_by is not None else txt.OWN_VERSION
            )
        self._models.show_candidates(candidates, self._labels)
        self._summary.setText(
            txt.summary(self._name(), source.words, source.language, len(self._models.ticked()))
        )
        self._refresh_tables()
        self._refresh_footer()

    def _on_lists_refreshed(self, _result: object, error: BaseException | None) -> None:
        """List the models again when the fresh lists differ from the ones shown.

        Not while models are asked: the rows say what became of them.  A list that could not be
        fetched leaves the shown ones as they are.
        """
        self._models_pending = False
        if error is not None:
            log_failure(_logger, "The model lists could not be fetched", error)
            return
        if self._source is None or self.running():
            return
        candidates = tuple(self._hooks.candidates(self._hooks.config(), self._source))
        if candidates != self._listed:
            self._show_candidates(candidates)

    def models_pending(self) -> bool:
        """Return whether the fresh model lists are still being asked for."""
        return self._models_pending

    def _name(self) -> str:
        """Return the name of the open document, as the queue shows it."""
        return self._document.currentText() or self._open

    def tables_pending(self) -> bool:
        """Return whether the tabs still wait for the comparison that is being built."""
        return self._tables_pending

    def _refresh_tables(self) -> None:
        """Build the comparison of what is stored and give it to the tabs when it is ready.

        Only the last request counts: a verdict clicked while an earlier build runs shows the
        build that knows it.
        """
        source = self._source
        if source is None:
            return
        service = self._hooks.service
        keys = frozenset(self._labels)
        self._tables_pending = True
        self._tables.submit(lambda: service.comparison(source, keys), self._on_comparison)

    def _on_comparison(self, result: object, error: BaseException | None) -> None:
        """Show the comparison that was built, or keep what the tabs show and log why not."""
        self._tables_pending = False
        if error is not None or not isinstance(result, Comparison):
            if error is not None:
                log_failure(_logger, "The comparison could not be built", error)
            return
        self._show_tables(result)

    def _show_tables(self, comparison: Comparison | None) -> None:
        """Give *comparison* to the three tabs."""
        self._changes.show_comparison(comparison, self._source, self._labels)
        self._side_by_side.show_comparison(comparison, self._labels)
        self._overview.show_comparison(comparison, self._labels)

    # ── asking the models ──────────────────────────────────────────────────────

    def run_selected(self) -> None:
        """Ask the ticked models that have not answered yet, one after another."""
        if self._source is None:
            self._hooks.say(txt.NOT_READ)
            return
        if self._refuse_busy():
            return
        candidates = self._models.ticked()
        if not candidates:
            self._line.setText(txt.TICK_MODELS)
            return
        self._start(candidates)

    def run_again(self) -> None:
        """Ask the chosen model again and keep both answers, so chance shows as chance."""
        chosen = self._models.chosen()
        source = self._source
        if chosen is None or source is None:
            return
        if self._refuse_busy():
            return
        self._start((chosen,), repeat=self._hooks.service.next_repeat(source, chosen))

    def closeEvent(self, event: QCloseEvent) -> None:
        """End the job when the window closes: nothing may keep asking a model behind no window.

        The teacher is asked first when they closed it themselves; a close the main window makes
        has asked already.
        """
        if self.running():
            if event.spontaneous() and not _dialogs.ask(self, dlg.stop_comparison()):
                event.ignore()
                return
            job = self._job
            if job is not None:
                job.stop()
                job.wait(_CLOSE_WAIT_MS)
        self._lists.cancel()
        self._tables.cancel()
        super().closeEvent(event)

    def _refuse_busy(self) -> bool:
        """Return whether something is already going, so no model may be asked now."""
        if self.running():
            return True  # the buttons are disabled while the job asks; nothing to add
        if self._hooks.busy():
            self._hooks.warn_busy()
            return True
        return False

    def running(self) -> bool:
        """Return whether this window's job is still asking a model."""
        return self._job is not None and self._job.isRunning()

    def _start(self, candidates: Sequence[Candidate], *, repeat: int = 0) -> None:
        """Start the job that asks *candidates* in order, and lock the buttons while it runs."""
        source = self._source
        if source is None:
            return
        config = self._hooks.config()
        if not self._hooks.confirm_sending(config, candidates):
            return
        self._run_btn.setEnabled(False)
        self._again_btn.setEnabled(False)
        self._document.setEnabled(False)
        self._stop_btn.setEnabled(True)
        self._models.set_running([candidate.key for candidate in candidates])
        self._job = CompareJob(
            self._hooks.service,
            config,
            source,
            candidates,
            repeat=repeat,
            reporter=self._reporter,
            parent=self,
        )
        self._job.line.connect(self._on_line)
        self._job.row_done.connect(self._on_row_done)
        self._job.finished_ok.connect(lambda _result: self._on_job_done())
        self._job.finished_err.connect(self._on_job_failed)
        self._job.start()

    def stop(self) -> None:
        """Ask the job to end; a local model being asked is interrupted and stores nothing."""
        self._stop_btn.setEnabled(False)
        if self._job is not None:
            self._job.stop()

    def _on_row_done(self, ran: ComparedRun) -> None:
        """Say what became of one model, and show the answers stored so far."""
        if ran.failure is not None:
            self._models.set_state(ran.candidate.key, render(ran.failure))
        elif ran.stopped:
            self._models.set_state(ran.candidate.key, txt.STOPPED)
        elif ran.own:
            self._models.mark_done(ran.candidate.key)
            self._models.set_state(ran.candidate.key, txt.YOUR_PROOFREADING)
        else:
            self._models.mark_done(ran.candidate.key)
            self._models.set_state(ran.candidate.key, txt.done_in(_seconds(ran)))
        if ran.result is not None:
            # Made with the settings of the start of the job: they may differ from the list's.
            self._labels.setdefault(ran.result.key, ran.candidate.model)
        self._refresh_tables()

    def _on_job_done(self) -> None:
        """Say in the status bar that every model that was ticked has been asked."""
        self._end_job()
        self._hooks.say(txt.compare_done(self._name()))

    def _on_job_failed(self, text: str) -> None:
        """Show why the job ended early; the teacher's stop is not a failure of the app."""
        self._end_job()
        if text == ABORTED:
            self._line.setText(txt.STOPPED)
            self._hooks.say(txt.compare_stopped(self._name()))
        else:
            self._line.setText(text)

    def _end_job(self) -> None:
        """Let the window be used again: the models that were not asked say so."""
        self._models.settle()
        self._run_btn.setEnabled(True)
        self._document.setEnabled(True)
        self._stop_btn.setEnabled(False)
        self._line.clear()
        self._refresh_footer()

    def _on_line(self, line: str) -> None:
        """Show the unit's own line under the buttons."""
        self._line.setText(line)

    # ── the teacher's verdicts ─────────────────────────────────────────────────

    def _on_mark_requested(self, option: Option, verdict: Verdict | None) -> None:
        """Store the teacher's verdict on one option and show what it changes."""
        source = self._source
        if source is None:
            return
        try:
            self._hooks.service.set_mark(source, option, verdict)
        except OSError as exc:
            show_failure(self, dlg.marks_not_saved(), exc)
        self._refresh_tables()

    # ── keeping a model ────────────────────────────────────────────────────────

    def _refresh_footer(self) -> None:
        """Offer the chosen model as the one that proofreads, and to be asked again."""
        chosen = self._models.chosen()
        running = self.running()
        self._use_btn.setEnabled(chosen is not None)
        self._use_btn.setText(
            txt.use_model(chosen.model) if chosen is not None else txt.use_model("")
        )
        # The document's own proofreading is never asked here, so there is nothing to run again.
        self._again_btn.setEnabled(
            chosen is not None
            and self._models.answered(chosen.key)
            and not self._models.own(chosen.key)
            and not running
        )

    def use_chosen(self) -> None:
        """Store the chosen model as the proofreading model, when the teacher agrees."""
        chosen = self._models.chosen()
        if chosen is None:
            return
        if not _dialogs.ask(self, dlg.use_compared_model(chosen.model)):
            return
        self._hooks.use_model(chosen.provider, chosen.model)
        self._hooks.say(txt.now_proofreading(chosen.model))


def _seconds(ran: ComparedRun) -> float:
    """Return how long the answer of *ran* took; ``0`` when it stored nothing."""
    return ran.result.seconds if ran.result is not None else 0.0
