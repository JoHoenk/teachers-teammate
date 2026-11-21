"""The requirement check that runs after a stage was switched on.

The check contacts Ollama or imports spaCy, so it runs in the background, and its answer may
arrive late: the guard compares what the stage's requirements depend on before it untickes
anything, so a box the teacher ticked again with another model stays ticked.  It holds the
per-stage task and nothing else — the settings dict stays with the panel.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QCheckBox, QWidget

from ..application.composition import AppServices
from ..application.run import RequirementIssue
from ..config import Config
from . import _user_text as txt
from ._background import LatestTask
from ._dependency_guard_dialog import DependencyGuardDialog
from ._fix_targets import FixAction, fix_for
from .settings._page import SettingsPage

#: The step the teacher sees for each stage the requirement guard can untick.
_STAGE_STEP_TITLES = {
    "ocr": txt.RECOGNIZE_TEXT,
    "correction": txt.PROOFREAD,
    "evaluation": txt.REVIEW_CONTENT,
    "anonymizer": txt.REMOVE_PERSONAL_INFO,
}


@dataclasses.dataclass(frozen=True)
class GuardHooks:
    """What the requirement guard asks its owner for."""

    #: The application service, or ``None`` before one was injected: no service, no check.
    service: Callable[[], AppServices | None]
    #: The config of the live panel values, for the check and for its answer's age.
    config: Callable[[], Config]
    #: ``True`` while saved settings are being restored: a restored box is not a teacher's tick.
    loading: Callable[[], bool]
    #: The enable checkbox of a stage.
    checkbox: Callable[[str], QCheckBox]
    #: Store that a stage was unticked and re-render what its state decides.
    unticked: Callable[[str], None]
    #: The document language, which the wording of a fix depends on.
    language: Callable[[], str]
    #: Open a Settings page: the page, the focus value on it, entries to preselect.
    open_settings: Callable[[SettingsPage, str, list[str]], None]
    #: Where the dialog opens over: the widget that asked for the check.
    dialog_parent: QWidget


class StageGuard(QObject):
    """The requirement check of one stage, run in the background after the teacher enabled it."""

    def __init__(self, hooks: GuardHooks, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hooks = hooks
        # One per stage: ticking a box must only make *its own* earlier check stale.
        self._tasks: dict[str, LatestTask] = {}

    def check(self, stage: str) -> None:
        """Check the requirements of *stage* and untick it with an explanation if unmet.

        Nothing is checked while saved settings are being restored, for a box the teacher
        unticked meanwhile, or before a service was injected.
        """
        hooks = self._hooks
        if hooks.loading() or not hooks.checkbox(stage).isChecked():
            return
        service = hooks.service()
        if service is None:
            return
        config = hooks.config()
        task = self._tasks.setdefault(stage, LatestTask(self))
        task.submit(
            lambda: service.run.check_stage_requirements(config, stage),
            lambda result, error: self._on_result(config, stage, result, error),
        )

    def _on_result(
        self, config: Config, stage: str, result: object, error: BaseException | None
    ) -> None:
        """Untick *stage* and explain when its background requirement check failed."""
        hooks = self._hooks
        issues = (
            [issue for issue in result if isinstance(issue, RequirementIssue)]
            if error is None and isinstance(result, list)
            else []
        )
        if not issues:
            # All met (or the check itself failed: never block the UI on that).  A review that
            # was ticked before proofreading came back is checked now.
            if stage == "correction" and hooks.checkbox("evaluation").isChecked():
                self.check("evaluation")
            return
        checkbox = hooks.checkbox(stage)
        if not checkbox.isChecked():
            return  # the user unticked it in the meantime
        if stage == "evaluation" and not hooks.checkbox("correction").isChecked():
            return  # no review without proofreading: its tick stays for when it comes back
        if _stage_inputs(hooks.config(), stage) != _stage_inputs(config, stage):
            return  # the user chose another model/provider/server: this answer is outdated
        checkbox.blockSignals(True)
        checkbox.setChecked(False)
        checkbox.blockSignals(False)
        hooks.unticked(stage)

        title = txt.GUARD_TITLE.format(step=_STAGE_STEP_TITLES.get(stage, stage))
        service = hooks.service()
        if service is None:
            return  # no service, no check that could have failed
        fix = fix_for(
            stage,
            issues[0],
            lambda provider: str(
                service.providers.get_provider_info(provider).get("display_name", provider)
            ),
            language=hooks.language(),
        )
        dialog = DependencyGuardDialog(title, issues, fix.actions, hooks.dialog_parent)
        dialog.action_chosen.connect(lambda action: _open_fix(hooks, action))
        dialog.exec()


def _open_fix(hooks: GuardHooks, action: FixAction) -> None:
    """Open the Settings place a fix button names."""
    if action.page is not None:
        hooks.open_settings(action.page, action.focus, list(action.preselect))


def _stage_inputs(config: Config, stage: str) -> tuple[str, ...]:
    """Return the config values the requirements of *stage* depend on."""
    if stage == "ocr":
        return (config.ocr.engine,)
    if stage == "correction":
        return (
            config.correction_provider,
            config.effective_correction_model,
            config.ollama_url,
        )
    if stage == "evaluation":
        return (config.evaluate_provider, config.effective_evaluate_model, config.ollama_url)
    return (config.language,)
