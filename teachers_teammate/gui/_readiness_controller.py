"""The line above Run: is a run possible, and what to press if it is not.

It asks the questions in the background (a check contacts Ollama, may import spaCy and looks
for the built-in reader), keeps the last answer, offers the fixes that answer implies, and
quietly looks for a local server when the saved address does not answer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget

from ..application.composition import AppServices
from ..application.run import RunPreflight
from ..domain.ollama_discovery import (
    OLLAMA_DOWNLOAD_URL,
    DiscoveredServer,
    DiscoveryScope,
    ServerKind,
)
from ..domain.requirements import IssueKind, RequirementIssue
from ..domain.stages import Stage
from ..settings_schema import normalize_saved_ollama_url
from ..texts import dialogs as dlg
from ..texts import main_window as txt
from . import _dialogs
from ._background import LatestTask, run_in_background
from ._config_panel import ConfigPanel
from ._fix_targets import (
    COMMAND_CHOOSE_FOLDER,
    COMMAND_GET_OLLAMA,
    COMMAND_RETRY,
    COMMAND_START_OLLAMA,
    COMMAND_TURN_ON_RECOGNITION,
    COMMAND_USE_SERVER,
    RESULTS_NO_FOLDER,
    RESULTS_TEXT_IN_INPUT,
    Fix,
    FixAction,
    fix_for,
    found_server_fix,
    results_fix,
)
from ._readiness_line import ReadinessLine, readiness
from ._results_table import ResultsTable
from .check import CheckPage

#: Whose computer a found server is on, as the readiness line says it.
_SERVER_PLACES = {
    ServerKind.THIS_COMPUTER: "this computer",
    ServerKind.WINDOWS_HOST: "the Windows computer",
    ServerKind.NETWORK: "another computer",
}

#: The kinds that mean "Ollama isn't answering": the line keeps looking while one of them
#: blocks the run, so an Ollama installed or started meanwhile turns it green by itself.
_OLLAMA_KINDS = frozenset(
    {IssueKind.OLLAMA_UNREACHABLE, IssueKind.OLLAMA_NOT_FOUND, IssueKind.OLLAMA_NOT_RUNNING}
)

#: How often an unreachable Ollama is checked again (the server may start after the app).
OLLAMA_RETRY_MS = 10_000
#: How long the readiness check waits after a settings change, so a burst checks once.
READINESS_DEBOUNCE_MS = 400
#: How long a check may take before the line says "Checking…".
CHECKING_DELAY_MS = 300


@dataclass(frozen=True)
class ReadinessHooks:
    """What the readiness check asks of the window it belongs to."""

    open_settings: Callable[..., None]
    """Show the Settings window on a page, focused on a step, with a preselection."""
    save_settings: Callable[..., None]
    """Write the settings file."""
    queue_refresh: Callable[[], None]
    """Fill the queue again: a chosen server changes what a run over it says."""
    provider_name: Callable[[str], str]
    """The name a teacher knows a provider by."""
    log_line: Callable[[str], None]
    """Append a line to the log."""


def _timer(parent: QObject, interval_ms: int, slot: Callable[[], None]) -> QTimer:
    """Return a single-shot timer that calls *slot* once *interval_ms* have passed."""
    timer = QTimer(parent)
    timer.setSingleShot(True)
    timer.setInterval(interval_ms)
    timer.timeout.connect(slot)
    return timer


class ReadinessController(QObject):
    """What the line above Run says, and the fixes it offers."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        table: ResultsTable,
        page: CheckPage,
        hooks: ReadinessHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._table = table
        self._page = page
        self._hooks = hooks
        #: The line itself: the window lays it out, this controller fills it.
        self.line = ReadinessLine()
        #: The last answer of the background check (``None``: nothing blocks).
        self.blocker: tuple[str, RequirementIssue] | None = None
        self._first_check_answered = False
        self._checking_visible = False
        #: Queued scans the last check found a run would leave out (text recognition is off).
        self._skipped_scans: set[Path] = set()
        # A silent search for Ollama on this computer, made when the saved address is
        # unreachable: the server it found (offered, never taken over by itself) and the
        # address the search was made for (one search per address).
        self._found_server: DiscoveredServer | None = None
        self._server_searched_for = ""
        self._readiness_task = LatestTask(self)
        self._server_search_task = LatestTask(self)
        # Settings change in bursts (a checkbox, a model, a language): check once, soon after.
        self._debounce = _timer(self, READINESS_DEBOUNCE_MS, self.start_check)
        # An unreachable Ollama is checked again every few seconds: it may be started after
        # the app.
        self._ollama_retry = _timer(self, OLLAMA_RETRY_MS, self.start_check)
        # "Checking…" shows only when the first answer takes a noticeable moment.
        self._checking = _timer(self, CHECKING_DELAY_MS, self._show_checking)

    # ── Asking ────────────────────────────────────────────────────────────

    def start_check(self, *, fresh: bool = False) -> None:
        """Ask in the background whether a run could start, then update the line.

        *fresh* asks the Ollama server again instead of using the model list it gave a few
        minutes ago: wanted when the teacher asks to look again (a model pulled in a terminal).

        The check contacts Ollama, may import spaCy and looks for the built-in reader, so it
        never runs on the GUI thread.  Only the latest answer counts.  Every place that
        changes what a run needs calls this: Settings, a finished download, a new window
        activation.
        """
        self._debounce.stop()
        if not self._first_check_answered:
            self._checking.start()
        service = self._services
        config = self._panel.lookup_config()
        # The table is read here, on the GUI thread: the task only gets what it was handed.
        files = self._table.files()
        recognised = self._table.recognised_files()
        recognition = service.run.needs_text_recognition(files, recognised=recognised)
        self._readiness_task.submit(
            lambda: service.run.check_run_preflight(
                config,
                for_run=False,
                recognition=recognition,
                fresh=fresh,
                files=files,
                recognised=recognised,
            ),
            self.on_check_done,
        )

    def schedule_check(self) -> None:
        """Check soon, once, however many settings changed in a row."""
        self._debounce.start()

    def on_check_done(self, result: object, error: BaseException | None) -> None:
        """Keep the answer of the background check and say what the line should show."""
        self._checking.stop()
        self._first_check_answered = True
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: say nothing wrong; Run checks again and reports.
            self.blocker = None
            self._skipped_scans = set()
        else:
            self.blocker = result.blocker
            self._skipped_scans = set(result.skipped)
        unreachable = self.blocker is not None and self.blocker[1].kind in _OLLAMA_KINDS
        if unreachable:
            self._ollama_retry.start()
            self._search_local_server()
        else:
            self._ollama_retry.stop()
            self._found_server = None
            self._server_searched_for = ""
        self.update()

    def _search_local_server(self) -> None:
        """Look quietly for Ollama on this computer (and the Windows host of WSL), once.

        Only this computer is asked, never the network.  A hit is offered on the readiness
        line; the saved address changes only when the teacher presses the button.
        """
        url = self._current_ollama_url()
        if self._server_searched_for == url:
            return
        self._server_searched_for = url
        service = self._services
        self._server_search_task.submit(
            lambda: service.ollama.discover_ollama_servers(DiscoveryScope.THIS_COMPUTER),
            lambda result, error: self._on_server_search_done(url, result, error),
        )

    def _on_server_search_done(self, url: str, result: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(result, list) or url != self._current_ollama_url():
            return

        saved = self._services.ollama.normalize(url)
        self._found_server = next(
            (
                server
                for server in result
                if isinstance(server, DiscoveredServer)
                and self._services.ollama.normalize(server.url) != saved
            ),
            None,
        )
        self.update()

    def use_server(self, url: str) -> None:
        """Take over the address of a server the teacher chose on the readiness line."""
        self._found_server = None
        self._panel.update_settings({"ollama_url": url})
        self._hooks.save_settings(notify=False)
        self.start_check(fresh=True)
        self._hooks.queue_refresh()

    def _show_checking(self) -> None:
        self._checking_visible = not self._first_check_answered
        self.update()

    def _current_ollama_url(self) -> str:
        return normalize_saved_ollama_url(self._panel.get_settings_dict().get("ollama_url"))

    # ── Showing ───────────────────────────────────────────────────────────

    def update(self) -> None:
        """Show what the last check and the cheap facts (documents, selection, folder) say.

        Instant and on the GUI thread: it asks no server.  The line keeps the last answer
        while a newer check runs, so it doesn't flicker.
        """
        panel = self._panel
        blocker_fix: Fix | None = None
        if self.blocker is not None:
            stage, issue = self.blocker
            blocker_fix = fix_for(
                stage, issue, self._hooks.provider_name, language=panel.lookup_language()
            )
            if issue.kind in _OLLAMA_KINDS and self._found_server is not None:
                blocker_fix = found_server_fix(
                    _SERVER_PLACES[self._found_server.kind], self._found_server.url
                )
        results = self._results_problem()
        # The Check tab's next-step button proofreads, or sets proofreading up when the last
        # check found the proofreading step unable to run.
        self._page.set_proofreading_ready(
            self.blocker is None or self.blocker[0] != Stage.CORRECTION
        )
        self.show_recognition_state()
        self.line.show_state(
            readiness(
                blocker=blocker_fix,
                results=results,
                inputs_chosen=bool(panel.get_input_dir()),
                files=len(self._table.queued_source_ids()),
                selected=len(self._table.selected_source_ids()),
                checking=self._checking_visible and not self._first_check_answered,
            )
        )

    def show_recognition_state(self) -> None:
        """Say what the "Recognize text" switch does to the queue: the note and the rows.

        Off: the scans the last check found are marked "skipped" and counted under the
        switch.  On: no row is marked, and the note says so when no document needs reading.
        """
        panel = self._panel
        off = not panel.recognition_enabled()
        skipped = self._skipped_scans if off else set()
        self._table.set_skipped_sources(
            {str(path.resolve()) for path in skipped}, txt.RECOGNITION_OFF_REASON
        )
        files = self._table.files()
        panel.show_recognition_note(
            skipped=len(skipped),
            not_needed=bool(files)
            and not self._services.run.needs_text_recognition(
                files, recognised=self._table.recognised_files()
            ),
        )

    def _results_problem(self) -> Fix | None:
        """Return the fix for a Results step that a run would refuse, or ``None``."""
        panel = self._panel
        if panel.results_problem():
            return results_fix(RESULTS_NO_FOLDER)
        if panel.get_input_dir() and self._services.run.output_problem_for_run(
            panel.lookup_config()
        ):
            return results_fix(RESULTS_TEXT_IN_INPUT)
        return None

    # ── The buttons ───────────────────────────────────────────────────────

    def run_fix(self, action: FixAction) -> None:
        """Do what a fix button says: open its Settings page, check again, or choose a folder."""
        if action.page is not None:
            self._hooks.open_settings(action.page, action.focus, list(action.preselect))
        elif action.command == COMMAND_RETRY:
            self.start_check(fresh=True)
        elif action.command == COMMAND_CHOOSE_FOLDER:
            self._panel.choose_output_dir()
        elif action.command == COMMAND_TURN_ON_RECOGNITION:
            self._panel.turn_on_recognition()
        elif action.command == COMMAND_USE_SERVER:
            self.use_server(action.argument)
        elif action.command == COMMAND_GET_OLLAMA:
            QDesktopServices.openUrl(QUrl(OLLAMA_DOWNLOAD_URL))
        elif action.command == COMMAND_START_OLLAMA:
            self.start_ollama()

    def start_ollama(self) -> None:
        """Start the installed Ollama, then look again once it has had time to come up.

        Starting it takes a moment, so the check runs a few seconds later instead of at once;
        the 10 s retry keeps looking until the server answers.
        """
        run_in_background(self._services.ollama.start_ollama, self._on_ollama_started)

    def _on_ollama_started(self, _result: object, error: BaseException | None) -> None:
        """Look again in a few seconds, or say in the log why Ollama did not start."""
        if error is not None:
            self._hooks.log_line(f"NOTICE: Ollama could not be started: {error}\n")
            return
        QTimer.singleShot(3000, lambda: self.start_check(fresh=True))

    def show_blocking(self, result: RunPreflight) -> None:
        """Explain the requirement that stops the run, with a button for each way to fix it."""
        blocker = result.blocker
        if blocker is None:
            return
        from ._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

        stage, issue = blocker
        # A step can miss several things at once; the first one decides the buttons.
        issues = list(result.stage_issues) if result.stage_issues else [issue]
        fix = fix_for(
            stage,
            issue,
            self._hooks.provider_name,
            language=self._panel.lookup_language(),
        )
        guard = DependencyGuardDialog(txt.RUN_BLOCKED_TITLE, issues, fix.actions, self._window)
        guard.action_chosen.connect(self.run_fix)
        guard.exec()
        # The server may have been started while the dialog was open: look again.
        self.blocker = blocker
        self.update()
        self.start_check(fresh=True)

    # ── Downloads on the line ─────────────────────────────────────────────

    def show_download(self, model_name: str) -> None:
        """Take the line over for a pull of *model_name*."""
        self.line.show_download(model_name)

    def set_download_progress(self, completed: int, total: int) -> None:
        """Move the bar of the running pull (nothing to show without a total)."""
        if total <= 0:
            return
        mb_done = completed / 1_048_576
        mb_total = total / 1_048_576
        self.line.set_download_progress(
            int(100 * completed / total), f"{mb_done:.1f} / {mb_total:.1f} MB"
        )

    def end_download(self) -> None:
        """Hand the line back and look again: the model that was missing may be there now."""
        self.line.end_download()
        QTimer.singleShot(0, self.start_check)

    def model_download_failed(self, message: str) -> None:
        """A pull failed: say so, and hand the line back."""
        self.end_download()
        _dialogs.notify(self._window, dlg.model_download_failed(message))

    # ── Closing ───────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Stop the timers and drop what is running (the window is closing)."""
        self._first_check_answered = False
        self._debounce.stop()
        self._ollama_retry.stop()
        self._checking.stop()
        self._readiness_task.cancel()
        self._server_search_task.cancel()
