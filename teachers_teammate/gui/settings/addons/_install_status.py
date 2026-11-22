"""The install round shown: the status line, the bar, the log and the install button.

The round itself (:mod:`teachers_teammate.gui._install_round`) does the work and comes from
outside; this pane says what it is doing, in the teacher's words, and hands the click on
*Install / Download Selected* back to the page, which knows what the tabs want installed.

pip prints nothing while it downloads, so the status line and the bar are what tells the teacher
that something is happening at all: the line names the step — and how long it has been running —
and the bar shows a percentage where the round has one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
import re
import time

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ... import _user_text as txt
from ..._install_round import PACKAGE_PREFIX, UPDATE_ADDONS_ITEM, InstallRound
from ..._long_job import ABORTED
from ..._model_guidance import SPACY_PREFIX

#: The status line and the hint under it: small and grey, so the page's content stays first.
_HINT_STYLE = "color: #888; font-size: 11px;"
#: A status line that asks for something (a restart, a failure).
_WARNING_STYLE = "color: #e67e22; font-size: 11px;"
#: How often the elapsed time of a step without a percentage is redrawn.
_ELAPSED_TICK_MS = 1000
#: How long "Copied." stays on the copy button.
_COPIED_MS = 2000

#: pip log lines that name the package it is working on.  Best effort only.
_PIP_HINT_PREFIXES = ("Collecting ", "Downloading ", "Using cached ")
_PIP_HINT_INSTALLING = "Installing collected packages:"


def pip_hint(line: str) -> str:
    """Return the package a pip log line is about, or "" when the line names none.

    pip prints no progress while its output is a pipe, so these lines are the only sign of
    life during a long download.  Best effort: a line that does not match changes nothing,
    and this never decides whether an install worked.
    """
    text = line.strip()
    for prefix in _PIP_HINT_PREFIXES:
        if text.startswith(prefix):
            return _distribution_name(text.removeprefix(prefix).split(" ")[0])
    if text.startswith(_PIP_HINT_INSTALLING):
        names = text.removeprefix(_PIP_HINT_INSTALLING).strip()
        return _distribution_name(names.split(",")[0].strip())
    return ""


_REQUIREMENT_END = re.compile(r"[<>=!~;\[@(]")
_WHEEL_VERSION = re.compile(r"-\d")


def _distribution_name(token: str) -> str:
    """Return the distribution name of a pip token.

    A requirement (``spacy>=3.8,<3.9``) loses its specifier, a URL its folders, and a wheel file
    name (``de_core_news_sm-3.8.0-py3-none-any.whl``) its version.
    """
    name = _REQUIREMENT_END.split(token.rsplit("/", 1)[-1], maxsplit=1)[0]
    match = _WHEEL_VERSION.search(name)
    return name[: match.start()] if match else name


class InstallStatusPane(QWidget):
    """What the teacher sees while an install round runs, and its two buttons."""

    #: The teacher clicked *Install / Download Selected*.
    install_requested = Signal()
    #: A round ended; True when something arrived (the page then looks again).
    finished = Signal(bool)
    #: A step that downloads began: the model tree marks what the manager is pulling again.
    step_shown = Signal()

    def __init__(
        self,
        round_: InstallRound,
        labels: Sequence[Callable[[str], str | None]],
        pending: Callable[[], Sequence[str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._round = round_
        self._labels = labels
        self._pending_packages = pending
        # The one status line: the running step, then a restart, then the last round's result,
        # then what the detection found.
        self._idle_status = txt.ADDONS_DETECTING
        self._idle_warning = False
        self._round_result = ""
        self._round_result_warning = False
        self._round_steps: list[str] = []
        self._status_item = ""
        self._status_parts: tuple[str, str, int, int] | None = None
        self._status_line = ""
        self._status_started = 0.0
        self._done_count = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._build_status_row(layout)
        self._build_log_row(layout)
        self._build_action_row(layout)
        self._build_timers()
        # The round comes from outside and may outlive the page: only its signals arrive here.
        round_.log_line.connect(self._dl_log_append)
        round_.item_started.connect(self._on_round_item_started)
        round_.item_done.connect(self._on_round_item_done)
        round_.pull_progress.connect(self._on_round_pull_progress)
        round_.failed.connect(self._on_round_failed)
        round_.finished.connect(self._on_round_finished)
        self.refresh()

    def _build_status_row(self, layout: QVBoxLayout) -> None:
        """Build the status line, the progress bar and the line pip writes while it works."""
        self._status = QLabel(self._idle_status)
        self._status.setWordWrap(True)
        self._status.setStyleSheet(_HINT_STYLE)
        layout.addWidget(self._status)

        self._dl_progress = QProgressBar()
        self._dl_progress.setRange(0, 100)
        self._dl_progress.setTextVisible(False)
        self._dl_progress.setFixedHeight(10)
        self._dl_progress.hide()
        layout.addWidget(self._dl_progress)

        # Best effort: the package pip is working on now, from its own log lines.
        self._hint = QLabel("")
        self._hint.setStyleSheet(_HINT_STYLE)
        self._hint.hide()
        layout.addWidget(self._hint)

    def _build_log_row(self, layout: QVBoxLayout) -> None:
        """Build the details and copy buttons, and the log they open."""
        details_row = QWidget()
        details_layout = QHBoxLayout(details_row)
        details_layout.setContentsMargins(0, 0, 0, 0)
        self._details_btn = QPushButton(txt.ADDONS_SHOW_DETAILS)
        self._details_btn.setFlat(True)
        self._details_btn.setCheckable(True)
        self._details_btn.toggled.connect(self._on_details_toggled)
        self._copy_btn = QPushButton(txt.ADDONS_COPY_LOG)
        self._copy_btn.setFlat(True)
        self._copy_btn.clicked.connect(self._on_copy_log)
        self._copy_btn.hide()
        details_layout.addWidget(self._details_btn)
        details_layout.addWidget(self._copy_btn)
        details_layout.addStretch(1)
        layout.addWidget(details_row)

        self._dl_log = QPlainTextEdit()
        self._dl_log.setReadOnly(True)
        self._dl_log.setMaximumHeight(100)
        self._dl_log.setPlaceholderText("Install / download log will appear here…")
        self._dl_log.hide()
        layout.addWidget(self._dl_log)

    def _build_action_row(self, layout: QVBoxLayout) -> None:
        """Build the install button and the abort button that appears while a round runs."""
        action_row = QWidget()
        action_layout = QHBoxLayout(action_row)
        action_layout.setContentsMargins(0, 0, 0, 0)
        self._dl_install_btn = QPushButton(txt.ADDONS_INSTALL)
        self._dl_install_btn.clicked.connect(self.install_requested.emit)
        self._dl_abort_btn = QPushButton(txt.ADDONS_ABORT)
        self._dl_abort_btn.setToolTip(txt.ADDONS_ABORT_TOOLTIP)
        self._dl_abort_btn.clicked.connect(self._on_abort)
        self._dl_abort_btn.hide()
        action_layout.addStretch(1)
        action_layout.addWidget(self._dl_abort_btn)
        action_layout.addWidget(self._dl_install_btn)
        layout.addWidget(action_row)

    def _build_timers(self) -> None:
        """Build the timer that moves the elapsed time and the one that rests the copy button."""
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(_ELAPSED_TICK_MS)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._copy_timer = QTimer(self)
        self._copy_timer.setSingleShot(True)
        self._copy_timer.setInterval(_COPIED_MS)
        self._copy_timer.timeout.connect(lambda: self._copy_btn.setText(txt.ADDONS_COPY_LOG))

    # ── what the page sets ───────────────────────────────────────────────────────

    def set_idle(self, text: str, warning: bool = False) -> None:
        """Set the line shown when no step runs (and no restart waits), and show it."""
        self._idle_status = text
        self._idle_warning = warning
        self.refresh()

    def refresh(self) -> None:
        """Show the most important line: the running step, a restart, the result, the idle one."""
        if self._status_parts is not None:
            verb, item, number, total = self._status_parts
            self._set_status(txt.addons_status_step(verb, item, number, total))
            return
        pending = self._pending_packages()
        if pending:
            self._set_status(
                txt.ADDONS_RESTART_NEEDED
                if "spacy" in pending
                else txt.ADDONS_RESTART_NEEDED_GENERIC,
                warning=True,
            )
            return
        if self._round_result:
            self._set_status(self._round_result, warning=self._round_result_warning)
            return
        self._set_status(self._idle_status, warning=self._idle_warning)

    def begin_round(self, steps: Sequence[str]) -> None:
        """Clear the last result, the log and the hint before a round starts."""
        packages = [step for step in steps if step.startswith(PACKAGE_PREFIX)]
        # pip installs all its packages in one command: the round has one step for them.
        self._round_steps = [step for step in steps if step not in packages[1:]]
        self._round_result = ""
        self._round_result_warning = False
        self._status_item = ""
        self._status_parts = None
        self._done_count = 0
        self._dl_log.clear()
        self._hint.clear()
        self._hint.hide()
        self.show_details(False)

    def round_started(self) -> None:
        """Show that a round runs: the button stays where it is, disabled, and says so."""
        self._dl_install_btn.setEnabled(False)
        self._dl_install_btn.setText(txt.ADDONS_INSTALLING)
        self._dl_abort_btn.hide()
        self.refresh()

    def show_followed_pull(self, model: str) -> None:
        """Show the pull that was already running when the page opened."""
        self._dl_log.appendPlainText(f"(pull of {model!r} already in progress…)")
        self._show_step(model, 1)

    def show_details(self, shown: bool) -> None:
        """Open or close the log; the button follows."""
        self._details_btn.setChecked(shown)
        self._on_details_toggled(shown)

    # ── the status line ──────────────────────────────────────────────────────────

    def _set_status(self, text: str, warning: bool = False) -> None:
        """Write the status line; *warning* colours it as something to act on."""
        self._status_line = text
        self._status.setText(text)
        self._status.setStyleSheet(_WARNING_STYLE if warning else _HINT_STYLE)

    def _tick_elapsed(self) -> None:
        """Add the time the current step has been running, so the screen visibly moves."""
        if self._status_parts is None:
            self._elapsed_timer.stop()
            return
        self._status.setText(
            txt.addons_status_elapsed(self._status_line, time.monotonic() - self._status_started)
        )

    def _show_step(self, item: str, total: int) -> None:
        """Put *item* in the status line, with the bar and Abort that belong to it."""
        self._status_item = item
        self._status_parts = (
            self._verb(item),
            self._item_label(item),
            self._step_number(item),
            total,
        )
        self._status_started = time.monotonic()
        self._dl_progress.show()
        abortable = self._round.can_abort()
        self._dl_abort_btn.setEnabled(abortable)
        self._dl_abort_btn.setVisible(abortable)
        if item == UPDATE_ADDONS_ITEM or item.startswith((PACKAGE_PREFIX, SPACY_PREFIX)):
            # pip and the spaCy model download report no percentage: the bar moves on its own
            # and the elapsed time shows that something is happening.
            self._dl_progress.setRange(0, 0)
            self._elapsed_timer.start()
        else:
            self._dl_progress.setRange(0, 100)
            self._dl_progress.setValue(0)
            self._elapsed_timer.stop()
            self.step_shown.emit()
        self.refresh()

    def _step_number(self, item: str) -> int:
        """Return the position of *item* in the running round, counting from 1.

        pip installs all its packages in one command, so they are one step: any package
        has the number of the first.
        """
        if item.startswith(PACKAGE_PREFIX):
            item = next(
                (step for step in self._round_steps if step.startswith(PACKAGE_PREFIX)), item
            )
        try:
            return self._round_steps.index(item) + 1
        except ValueError:
            return 1

    @staticmethod
    def _verb(item: str) -> str:
        """Return what the status line says the round is doing with *item*."""
        if item == UPDATE_ADDONS_ITEM or item.startswith((PACKAGE_PREFIX, SPACY_PREFIX)):
            return txt.ADDONS_VERB_INSTALL
        return txt.ADDONS_VERB_DOWNLOAD

    def _item_label(self, item: str) -> str:
        """Return the name of *item* as the status line writes it."""
        if item == UPDATE_ADDONS_ITEM:
            return txt.ADDONS_ITEM_ADDONS
        for label_of in self._labels:
            label = label_of(item)
            if label is not None:
                return label
        if item.startswith(PACKAGE_PREFIX):
            return item.removeprefix(PACKAGE_PREFIX)
        return item

    def _on_details_toggled(self, shown: bool) -> None:
        """Show the log and its Copy button, or hide them."""
        self._dl_log.setVisible(shown)
        self._copy_btn.setVisible(shown)
        self._details_btn.setText(txt.ADDONS_HIDE_DETAILS if shown else txt.ADDONS_SHOW_DETAILS)

    def _on_copy_log(self) -> None:
        """Put the log on the clipboard, for a bug report."""
        QApplication.clipboard().setText(self._dl_log.toPlainText())
        self._copy_btn.setText(txt.ADDONS_COPIED)
        self._copy_timer.start()

    # ── following the install round ──────────────────────────────────────────────

    def _on_round_item_started(self, item: str) -> None:
        """Show the step in the status line, with the bar that belongs to it."""
        if item.startswith(PACKAGE_PREFIX) and self._status_item.startswith(PACKAGE_PREFIX):
            return  # pip installs all its packages in one command: one step, one line
        self._show_step(item, len(self._round_steps) or 1)

    def _on_round_item_done(self, _item: str) -> None:
        self._done_count += 1

    def _on_round_pull_progress(self, status: str, completed: int, total: int) -> None:
        if self._status_parts is None:
            # A pull the main window started before this page was opened: no step to count.
            self._set_status(status)
            return
        verb, item, number, count = self._status_parts
        if total > 0:
            percent = int(100 * completed / total)
            self._set_status(txt.addons_status_percent(verb, item, number, count, percent))
            self._dl_progress.setValue(percent)
        else:
            self._set_status(txt.addons_status_step(verb, item, number, count))

    def _on_round_failed(self, item: str, message: str) -> None:
        self._elapsed_timer.stop()
        self._status_parts = None
        if message == ABORTED:
            self._round_result = txt.ADDONS_ABORTED
            self._round_result_warning = False
            self._dl_log.appendPlainText(txt.ADDONS_ABORTED)
        else:
            self._round_result = txt.addons_failed(self._item_label(item), message)
            self._round_result_warning = True
            self._dl_log.appendHtml(f'<span style="color:#c0392b;"><b>Error:</b> {message}</span>')
            self.show_details(True)
        self.refresh()

    def _on_round_finished(self, installed_any: bool) -> None:
        self._elapsed_timer.stop()
        self._status_parts = None
        self._status_item = ""
        self._dl_progress.hide()
        self._dl_abort_btn.hide()
        self._hint.hide()
        self._dl_install_btn.setEnabled(True)
        self._dl_install_btn.setText(txt.ADDONS_INSTALL)
        if not self._round_result and installed_any:
            self._round_result = txt.addons_installed(self._done_count)
            self._round_result_warning = False
        self.refresh()
        self.finished.emit(installed_any)

    def _on_abort(self) -> None:
        self._dl_abort_btn.setEnabled(False)
        self._round.abort()

    def _dl_log_append(self, line: str) -> None:
        self._dl_log.appendPlainText(line)
        sb = self._dl_log.verticalScrollBar()
        sb.setValue(sb.maximum())
        hint = pip_hint(line)
        if hint:
            self._hint.setText(hint)
            self._hint.show()
