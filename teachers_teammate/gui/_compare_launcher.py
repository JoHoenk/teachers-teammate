"""Opens the one compare window and applies the model the teacher keeps from it.

The window is parentless, as the System Monitor is: the teacher arranges it beside the main
window, and the main window closes it with itself.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from ..application.composition import AppServices
from ._config_panel import ConfigPanel
from ._flow_confirm import confirm_comparison_flow
from ._privacy_settings import FlowAcknowledgements
from .compare import CompareHooks, CompareWindow

__all__ = ["CompareLauncher", "LauncherHooks"]


@dataclass(frozen=True)
class LauncherHooks:
    """What the compare window needs of the main window, looked up when it is needed."""

    documents: Callable[[], Sequence[tuple[str, str]]]
    """``(source_id, name)`` of every document that has been read, in the order of the queue."""

    say: Callable[[str], None]
    """Say a line in the main window's status bar."""

    busy: Callable[[], bool]
    """Whether a requirement check, a run or a comparison is going."""

    warn_busy: Callable[[], None]
    """Tell the teacher that the run is busy and nothing was started."""

    acknowledgements: Callable[[], FlowAcknowledgements]
    """The remembered answers to the question before text leaves this computer."""

    save_settings: Callable[[], None]
    """Write the settings file, without a line in the status bar."""

    settings_applied: Callable[[], None]
    """Look again at what a run needs, as after any change of the settings."""


class CompareLauncher:
    """The compare window, and the settings it writes through the main window's panel."""

    def __init__(self, *, services: AppServices, panel: ConfigPanel, hooks: LauncherHooks) -> None:
        self._panel = panel
        self._hooks = hooks
        self._window = CompareWindow(
            CompareHooks(
                service=services.comparison,
                candidates=services.compare_candidates.candidates,
                refresh_models=services.compare_candidates.refresh,
                config=panel.lookup_config,
                documents=hooks.documents,
                use_model=self.use_model,
                say=hooks.say,
                busy=hooks.busy,
                warn_busy=hooks.warn_busy,
                confirm_sending=lambda config, candidates: confirm_comparison_flow(
                    self._window, services, hooks.acknowledgements(), config, candidates
                ),
            )
        )

    @property
    def window(self) -> CompareWindow:
        """The one compare window."""
        return self._window

    def open(self, source_id: str) -> None:
        """Show the window on *source_id*, or bring it to the front while it asks models."""
        self._window.open_on(source_id)

    def running(self) -> bool:
        """Return whether the window is asking models."""
        return self._window.running()

    def close(self) -> None:
        """Close the window, ending the job it runs."""
        self._window.close()

    def use_model(self, provider: str, model: str) -> None:
        """Proofread with the model the teacher kept: the two keys Settings → AI models owns."""
        self._panel.update_settings({"correction_provider": provider, "correction_model": model})
        self._hooks.save_settings()
        self._hooks.settings_applied()
