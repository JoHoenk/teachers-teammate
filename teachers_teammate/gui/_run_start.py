"""The way from the Run button to a started run: the requirements, the questions, then the worker.

The window holds the widgets and the controllers; this one asks the teacher what a run would
replace before it starts anything, and hands the files to :class:`RunController`.  A controller
gets the widgets it works on and a frozen dataclass of what it asks of the window, looked up
when it gets there, so it never reaches into the window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QPushButton, QWidget

from ..application.composition import AppServices
from ..application.documents import WorkedOn
from ..application.run import RunPreflight
from ..config import Config
from ..domain.requirements import MemoryShortfall
from ..domain.stages import Stage
from ..texts import app as app_text
from ..texts import dialogs as dlg
from ..texts.privacy import advice_text, level_text, stage_badge
from . import _dialogs
from ._background import run_in_background
from ._check_controller import CheckController
from ._config_panel import ConfigPanel
from ._log_widget import LogWidget
from ._privacy_settings import FlowAcknowledgements
from ._readiness_controller import ReadinessController
from ._results_table import ResultsTable
from ._run_controller import RunController
from ._run_session import RunSession, wait_detail_text


@dataclass(frozen=True)
class RunStartView:
    """The widgets a run is started from: what it reads, and what it disables while it checks."""

    #: The window a question belongs to, and the owner of the background work.
    window: QWidget
    config_panel: ConfigPanel
    results_table: ResultsTable
    run_button: QPushButton
    log: LogWidget


@dataclass(frozen=True)
class RunStartHooks:
    """What starting a run asks of the window and of the parts already there."""

    run_controller: RunController
    readiness_controller: ReadinessController
    check_controller: CheckController
    run_session: RunSession
    #: The files the rows stand for: *source_ids* of ``None`` means the whole queue.
    queued_files: Callable[[list[str] | None], list[Path]]
    recognised_files: Callable[[], set[Path]]
    #: Whether a run over these rows reads pages.
    recognition_needed: Callable[[list[str] | None], bool]
    #: Whether a requirement check or a worker is already going.
    run_in_progress: Callable[[], bool]
    #: The remembered "don't send this again" answers.
    acknowledgements: Callable[[], FlowAcknowledgements]


class RunStartController(QObject):
    """The way from the Run button to a started run: requirements, questions, then the worker."""

    def __init__(
        self, app_service: AppServices, *, view: RunStartView, hooks: RunStartHooks
    ) -> None:
        super().__init__(view.window)
        self._app_service = app_service
        self._view = view
        self._hooks = hooks
        #: True while the requirement check of a Run / stage re-run is in flight.
        self._preflight_running = False

    @property
    def preflight_running(self) -> bool:
        """Whether the requirement check that was started here is still in flight."""
        return self._preflight_running

    def start(self) -> None:
        """Check what the queued rows need, then start a run over the selected ones."""
        if self._hooks.run_in_progress():
            return
        self._hooks.run_session.from_check = False
        try:
            config = self._view.config_panel.to_config()
        except ValueError as exc:
            _dialogs.notify(self._view.window, dlg.configuration_error(str(exc)))
            return
        selected = self._view.results_table.selected_source_ids()
        if not selected and self._view.results_table.queued_source_ids():
            return _dialogs.notify(self._view.window, dlg.no_files_selected())
        self._check_requirements_then(
            config,
            lambda preflight: self._confirm_teacher_work_then(
                config, preflight.worked_on, lambda: self._continue_run(config)
            ),
            recognition=self._hooks.recognition_needed(selected or None),
            files=self._hooks.queued_files(selected or None),
            ask_about_work=True,
        )

    def _check_requirements_then(
        self,
        config: Config,
        on_ready: Callable[[RunPreflight], None],
        *,
        recognition: bool = True,
        files: list[Path] | None = None,
        ask_about_work: bool = False,
    ) -> None:
        """Check the requirements of *config* off the GUI thread, then call *on_ready*.

        *on_ready* receives the preflight result, so a caller can ask about what the run would
        replace before it starts.

        *recognition* says whether the run reads pages; without it the text recognition
        engine is not required (see ``check_run_preflight``).  *files* are the documents the
        run takes: with text recognition switched off, a run over scans only is refused.
        *ask_about_work* also finds the documents whose corrections the teacher worked on (it
        reads the files), which only a check before a run needs.

        The checks contact Ollama, so they must not block the GUI.  Until the result is
        in, Run is disabled and :attr:`preflight_running` reports busy, so no second
        worker can be started from the Run button or the right-click menu.
        """
        problem = self._app_service.run.output_problem_for_run(config)
        if problem is not None:
            _dialogs.notify(self._view.window, dlg.output_folder_refused(problem))
            return
        self._preflight_running = True
        self._view.run_button.setEnabled(False)
        self._hooks.run_controller.show_checking()
        service = self._app_service
        recognised = self._hooks.recognised_files()
        run_in_background(
            lambda: service.run.check_run_preflight(
                config,
                recognition=recognition,
                files=files,
                recognised=recognised,
                ask_about_work=ask_about_work,
            ),
            lambda result, error: self._on_preflight_done(
                lambda preflight: self._confirm_data_flow_then(config, lambda: on_ready(preflight)),
                result,
                error,
            ),
            owner=self,
        )

    def _on_preflight_done(
        self,
        on_ready: Callable[[RunPreflight], None],
        result: object,
        error: BaseException | None,
    ) -> None:
        """Show the first unmet requirement, or continue with *on_ready*."""
        self._preflight_running = False
        self._view.run_button.setEnabled(True)
        self._hooks.run_controller.show_idle()
        if error is not None or not isinstance(result, RunPreflight):
            # The check itself failed: do not block; the pipeline reports real problems.
            on_ready(RunPreflight())
            return
        if result.blocked:
            self._hooks.readiness_controller.show_blocking(result)
            return
        note = wait_detail_text(result.ollama_wait)
        if note:
            # A note, not a question: the run works, the first file just waits.
            self._view.log.append_text(note + "\n")
        if result.memory_shortfall is not None:
            self._confirm_memory_warning(result.memory_shortfall, lambda: on_ready(result))
            return
        on_ready(result)

    def _confirm_memory_warning(
        self, shortfall: MemoryShortfall, on_ready: Callable[[], None]
    ) -> None:
        """Ask before a run whose models do not fit the memory that is free now.

        A warning rather than a refusal, because free memory changes from run to run — so
        there is no "don't ask again" here, and Cancel is the default button: Enter must not
        start a run that could make the computer swap.
        """
        if _dialogs.ask(self._view.window, dlg.not_enough_memory(shortfall)):
            on_ready()

    def _confirm_data_flow_then(self, config: Config, on_ready: Callable[[], None]) -> None:
        """Ask once before a run that sends content away from this computer."""
        flow = self._app_service.data_flow.describe_data_flow(config)
        if (
            not flow.leaves_computer
            or flow.fingerprint in self._hooks.acknowledgements().fingerprints()
        ):
            on_ready()
            return
        question = dlg.documents_leave_computer(
            level_text(flow.level),
            [(app_text.stage_title(stage.stage), stage_badge(stage)) for stage in flow.outbound],
            [advice_text(tip) for tip in flow.advice],
        )
        answer = _dialogs.choose(self._view.window, question)
        if not answer.accepted:
            return
        if answer.checked:
            self._hooks.acknowledgements().remember(flow)
        on_ready()

    def _confirm_teacher_work_then(
        self, config: Config, worked_on: WorkedOn, on_ready: Callable[[], None]
    ) -> None:
        """Ask what to do with corrections the teacher worked on before a run replaces them.

        *Keep my changes* is the default: it records the answer for these settings, so the same
        settings never ask again and the run keeps the proofreading.  *Proofread again* drops
        those proofreadings, and with them the corrections written on them.
        """
        if not worked_on.unanswered and not worked_on.unbound:
            on_ready()
            return
        question = dlg.worked_on_before_run(
            [path.name for path in worked_on.unanswered],
            [path.name for path in worked_on.unbound],
        )
        answer = _dialogs.choose(self._view.window, question)
        if answer.choice is None or answer.choice.declines:
            return
        service = self._app_service
        if answer.choice.id == dlg.KEEP_MY_CHANGES:
            service.commands.keep_teacher_work(config, files=worked_on.unanswered)
        elif answer.choice.id == dlg.PROOFREAD_AGAIN:
            failures = service.commands.invalidate_from_stage(
                config, files=worked_on.unanswered, stage=Stage.CORRECTION
            )
            if failures:
                _dialogs.notify(self._view.window, dlg.cache_invalidation_failed(list(failures)))
                return
        # The teacher was told that a new proofreading will not contain these corrections and
        # went on: they belong to no stored answer, so they are not asked about again.
        service.commands.drop_unbound_corrections(config, files=worked_on.unbound)
        on_ready()

    def _continue_run(self, config: Config) -> None:
        """Queue the files and start the worker once all requirements are met."""
        queued_source_ids = self._view.results_table.queued_source_ids()
        queue_was_empty = not queued_source_ids
        if queue_was_empty:
            queued_files = self._app_service.run.collect_input_files(config)
            queued_source_ids = [str(file.resolve()) for file in queued_files]
            self._view.results_table.set_queue(
                self._app_service.documents.queue_rows(config, queued_files)
            )

        if not queued_source_ids:
            _dialogs.notify(self._view.window, dlg.no_files_found(str(config.input_dir)))
            return

        selected_source_ids = self._view.results_table.selected_source_ids()
        if not selected_source_ids and queue_was_empty:
            selected_source_ids = queued_source_ids
        if not selected_source_ids:
            _dialogs.notify(self._view.window, dlg.no_files_selected())
            return

        self._hooks.run_controller.start(config, selected_source_ids)

    def start_stage(self, selected_source_ids: list[str], stage: str) -> None:
        """Check the requirements, then invalidate the cache from *stage* onward and run the rows."""
        if self._hooks.run_in_progress():
            self._hooks.run_controller.warn_busy()
            return
        try:
            config = self._view.config_panel.to_config()
        except ValueError as exc:
            _dialogs.notify(self._view.window, dlg.configuration_error(str(exc)))
            return

        # One stage by hand: the service builds that config, so the GUI holds no variant of
        # its own that could drift from what a run does.
        config = self._app_service.run.config_for_stage_run(config, stage)

        # Check before touching the cache: a re-run that cannot work (Ollama stopped, model
        # missing) must not throw away the results that are there.
        # Re-running recognition reads the pages again; the later stages work on the stored text.
        self._check_requirements_then(
            config,
            lambda _preflight: self._run_stage(config, selected_source_ids, stage),
            recognition=stage == Stage.OCR or self._hooks.recognition_needed(selected_source_ids),
            files=self._hooks.queued_files(selected_source_ids),
        )

    def _run_stage(self, config: Config, selected_source_ids: list[str], stage: str) -> None:
        """Ask what an explicit re-run would drop, then invalidate the cache and start the worker."""
        # A typed edit is stored first: after the invalidation it would be stored on top of the
        # state the run is about to work on, and a run would redo or skip the wrong thing.
        self._hooks.check_controller.page.flush_pending_edit()
        if stage == Stage.EVALUATION:
            # A content review is made from the corrected text; nothing of the teacher's goes.
            self._invalidate_and_run(config, selected_source_ids, stage)
            return
        # The question needs the files hashed, which is not work for the GUI thread.
        files = self._hooks.queued_files(selected_source_ids)
        service = self._app_service
        run_in_background(
            lambda: service.documents.worked_on_documents(
                config, files, run_proofreads=stage == Stage.CORRECTION
            ),
            lambda result, error: self._confirm_rerun_then(
                config, selected_source_ids, stage, result, error
            ),
            owner=self,
        )

    def _confirm_rerun_then(
        self,
        config: Config,
        selected_source_ids: list[str],
        stage: str,
        result: object,
        error: BaseException | None,
    ) -> None:
        """Ask before an explicit re-run drops what the teacher worked on, then run it.

        If what they worked on cannot be found out, nothing is dropped: the question cannot be
        asked, so the answer cannot be taken for granted.
        """
        if error is not None or not isinstance(result, WorkedOn):
            _dialogs.notify(self._view.window, dlg.could_not_check_work())
            return
        if stage == Stage.CORRECTION:
            names = result.worked_on
        else:
            # Reading again takes both parts of the work: the transcription and the corrections.
            names = tuple(dict.fromkeys((*result.transcribed, *result.worked_on)))
        if names:
            question = (
                dlg.proofread_again([path.name for path in names])
                if stage == Stage.CORRECTION
                else dlg.read_again([path.name for path in names])
            )
            if not _dialogs.ask(self._view.window, question):
                return
        self._invalidate_and_run(config, selected_source_ids, stage)

    def _invalidate_and_run(
        self, config: Config, selected_source_ids: list[str], stage: str
    ) -> None:
        """Invalidate the cache from *stage* onward and start the worker on the rows."""
        files = self._hooks.queued_files(selected_source_ids)
        if not files:
            _dialogs.notify(self._view.window, dlg.selection_mismatch())
            return

        failures = self._app_service.commands.invalidate_from_stage(
            config, files=files, stage=stage
        )
        if failures:
            _dialogs.notify(self._view.window, dlg.cache_invalidation_failed(list(failures)))
            return

        self._hooks.run_controller.start(config, [str(file.resolve()) for file in files], files)
