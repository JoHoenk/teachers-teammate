"""The Setup Assistant, and what a first start leaves behind when it is cancelled.

The assistant asks what only a teacher knows and hands back a plan; a first start that is
cancelled still gets the best models this computer can run.  Both are the same flow — a
background hardware check, the model choice it leads to, and one save at the end — so they
live here instead of in the window.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QWidget

from ..application.composition import AppServices
from ..config import DEFAULTS
from ..domain.data_location import ENGINE_OLLAMA, PROVIDER_OLLAMA
from ..domain.model_catalog import HardwareDetection
from ..texts.models import first_start_log
from ._background import LatestTask
from ._config_panel import ConfigPanel
from ._log_widget import LogWidget
from ._privacy_view_controller import PrivacyViewController
from ._pull_manager import PullManager

if TYPE_CHECKING:
    from .setup import SetupResult


@dataclass(frozen=True)
class SetupHooks:
    """What the Setup Assistant asks of the window."""

    #: Write the settings file.
    save: Callable[[], None]
    #: Ask again what a run needs, and show what is cached for the chosen folder.
    applied: Callable[[], None]
    #: A round of installs ended (``installed_any``): a package may have arrived.
    install_round_finished: Callable[[bool], None]
    #: The assistant's last page offers to choose the documents now.
    select_folder: Callable[[], None]


class SetupLauncher(QObject):
    """Opens the one Setup Assistant and applies what it agreed to."""

    def __init__(
        self,
        window: QWidget,
        *,
        services: AppServices,
        panel: ConfigPanel,
        pull_manager: PullManager,
        privacy: PrivacyViewController,
        log: LogWidget,
        hooks: SetupHooks,
    ) -> None:
        super().__init__(window)
        self._window = window
        self._services = services
        self._panel = panel
        self._pull_manager = pull_manager
        self._privacy = privacy
        self._log = log
        self._hooks = hooks
        #: Whether the assistant is open, so a second click cannot open a second one.
        self._open_assistant = False
        #: The first-start hardware check, so closing the window drops its answer.
        self._first_run_task = LatestTask(self)

    def open(self, *, first_start: bool = False) -> None:
        """Ask the questions a first start needs, then apply the setup plan the teacher agreed to.

        On a first start the assistant starts on the system language, because there is no
        saved one yet; from the Tools menu it starts on the settings as they are.  Cancel on a
        first start leaves today's setup in place (the best models for this computer and the
        system language); from the Tools menu it changes nothing.
        """
        if self._open_assistant:
            return
        from .setup import SetupAssistant, SetupContext  # noqa: PLC0415  # loaded on demand

        language = ""
        if first_start:
            language = self._services.setup.system_language() or self._panel.lookup_language()
        context = SetupContext(
            settings=self._panel.get_settings_dict(),
            app_service=self._services,
            pull_manager=self._pull_manager,
            language=language,
            privacy_available=self._privacy.privacy_available,
            first_start=first_start,
            display_anonymized=self._privacy.display_anonymized,
        )
        assistant = SetupAssistant(context, self._window)
        assistant.committed.connect(self._apply_result)
        # The round outlives the assistant: a download that finishes after the window closed
        # must still tell this window that packages changed.
        install_round = assistant.install_round()
        install_round.finished.connect(self._hooks.install_round_finished)
        self._open_assistant = True
        try:
            assistant.exec()
        finally:
            self._open_assistant = False
        # The round was handed to this window so it can finish after the assistant: it goes
        # when it is over, or at once when nothing is running.
        if install_round.running():
            install_round.finished.connect(install_round.deleteLater)
        else:
            install_round.deleteLater()
        wants_documents = assistant.documents_wanted()
        # Set up decides, not how the window closed: the X on the done page is a reject too.
        was_set_up = assistant.is_committed()
        assistant.deleteLater()
        if first_start and not was_set_up:
            self._apply_first_start_defaults()
        if wants_documents:
            self._hooks.select_folder()

    def shutdown(self) -> None:
        """Drop the background hardware check when the window closes."""
        self._first_run_task.cancel()

    def _apply_first_start_defaults(self) -> None:
        """Leave a first start with a working setup when the assistant was cancelled.

        The system language goes into the panel, because no settings file names one yet; then
        today's first start runs: the best models for this computer, once detection finishes.
        """
        language = self._services.setup.system_language()
        if language:
            self._panel.load_from_dict({"language": language})
            self._hooks.save()
        self._apply_first_start_model_settings()

    def _apply_result(self, result: SetupResult) -> None:
        """Take over what the assistant set up, then save once.

        The main-window keys go through ``load_from_dict``, which skips the stage guard: the
        models are still downloading, so a guard would untick Proofread and open a dialog.
        """
        self._panel.update_settings(result.settings)
        self._panel.load_from_dict(result.panel)
        if result.display_anonymized is not None:
            # The name detection may still be on its way: the wish is stored as it is, and the
            # view follows it as soon as hiding is possible (refresh_availability).
            self._privacy.store_display_anonymized(result.display_anonymized)
        self._hooks.save()
        self._hooks.applied()

    def _apply_first_start_model_settings(self) -> None:
        """Start first-start hardware detection in the background."""
        service = self._services
        ollama_url = str(self._panel.get_settings_dict().get("ollama_url", DEFAULTS["ollama_url"]))
        # Detection must never get in the way of starting the app: any failure
        # simply leaves the static defaults in place.
        self._first_run_task.submit(
            lambda: service.recommendations.detect_hardware(ollama_url),
            self._on_first_start_detection,
        )

    def _on_first_start_detection(self, detection: object, error: BaseException | None) -> None:
        """Replace untouched static defaults with the best models for the profile."""
        if error is not None or not isinstance(detection, HardwareDetection):
            return
        settings = self._panel.get_settings_dict()
        best = self._services.recommendations.first_run_model_settings(
            detection.profile,
            system_memory_gb=detection.system_memory_gb,
            card_gb=detection.card_gb,
        )
        apply: dict[str, str] = {}
        if (
            settings.get("ocr_engine") == ENGINE_OLLAMA
            and settings.get("ocr_model") == DEFAULTS["ocr_model"]
        ):
            apply["ocr_model"] = best["ocr_model"]
        if (
            settings.get("correction_provider") == PROVIDER_OLLAMA
            and settings.get("correction_model") == DEFAULTS["correction_model"]
        ):
            apply["correction_model"] = best["correction_model"]
        if (
            settings.get("evaluate_provider") == PROVIDER_OLLAMA
            and settings.get("evaluate_model") == DEFAULTS["evaluate_model"]
        ):
            apply["evaluate_model"] = best["evaluate_model"]
        if not apply:
            return
        self._panel.update_settings(apply)
        ocr_model = apply.get("ocr_model", str(settings.get("ocr_model", "")))
        general_model = apply.get("correction_model") or apply.get(
            "evaluate_model", best["correction_model"]
        )
        self._log.append_text(first_start_log(ocr_model, general_model, detection.profile))
        self._hooks.save()
        self._hooks.applied()
