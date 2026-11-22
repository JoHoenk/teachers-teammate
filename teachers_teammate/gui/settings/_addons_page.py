"""Settings → Add-ons: install AI models for this computer, the reader, services and models.

Each downloadable category lives on its own tab (:mod:`.addons`); a shared log and a single
"Install / Download Selected" button act across all tabs.  The "AI models for this computer"
tab is first: it groups the curated catalog into reading and writing models and ticks the best
models for the user's computer (see :mod:`teachers_teammate.domain.model_catalog`).

This module holds what belongs to the page as a whole: the four tabs, the page protocol (title,
closing, deep links) and the installation check that tells every tab what is there.  What
happens here happens at once: Cancel does not undo an install.  Ollama pulls belong to the
main window's download manager and keep running when the window closes; a pip install or a
language-model download keeps the window open until it finished (it must make the new
package importable before anything else uses it).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QTabWidget, QVBoxLayout, QWidget

from ...config import DEFAULTS
from ...domain.model_catalog import HardwareProfileSetting, profile_setting
from ...texts import addons as txt
from ...texts import dialogs as dlg
from ...texts import settings as st
from ...texts.settings import TAB_READING_MODELS
from .._background import LatestTask
from .._install_round import (
    PACKAGE_PREFIX,
    PAGE_MODELS_ITEM,
    PAGE_MODELS_PREFIX,
    READER_ITEM,
    READER_PREFIX,
    UPDATE_ADDONS_ITEM,
    InstallRound,
)
from .._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX
from ._page import (
    FOCUS_ADDON_UPDATE,
    FOCUS_LOCAL_MODELS,
    FOCUS_NAME_DETECTION,
    FOCUS_ONLINE_SERVICES,
    FOCUS_READING_MODELS,
    SettingsContext,
    SettingsPage,
    SettingsPageBase,
)
from .addons import (
    InstallStatusPane,
    NameDetectionTab,
    OllamaModelsTab,
    ProvidersTab,
    ReadingModelsTab,
    UpdateBanner,
    detect_installed,
    is_model_tag,
)


class AddonsPage(SettingsPageBase):
    """Install AI models for this computer, handwriting engines, online services and more.

    Every tab is a widget of its own (:mod:`.addons`) and knows only its own rows; this page
    builds them, asks each of them what the teacher ticked, and keeps the one status line the
    install round talks to.
    """

    page = SettingsPage.ADDONS
    title = st.PAGE_ADDONS
    description = st.PAGE_INTRO_ADDONS
    applies_immediately = True
    #: The model tree takes the free height, so the page fills the window instead of scrolling:
    #: the install button must stay visible on a laptop screen.
    fills_window = True

    #: Emitted after a successful install round so callers can refresh model lists.
    packages_changed = Signal()

    #: Tab indexes within the tab widget (the local-models tab is first — what new users need).
    TAB_LOCAL_MODELS = 0
    TAB_ONLINE_SERVICES = 1
    TAB_NAME_DETECTION = 2
    TAB_READING_MODELS = 3

    _FOCUS_TABS: ClassVar[dict[str, int]] = {
        FOCUS_LOCAL_MODELS: TAB_LOCAL_MODELS,
        FOCUS_READING_MODELS: TAB_READING_MODELS,
        FOCUS_ONLINE_SERVICES: TAB_ONLINE_SERVICES,
        FOCUS_NAME_DETECTION: TAB_NAME_DETECTION,
    }

    def __init__(self, context: SettingsContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        settings = context.settings
        self._svc = context.app_service
        self._ollama_url = str(settings.get("ollama_url") or "").strip() or DEFAULTS["ollama_url"]
        self._detect_task = LatestTask(self)
        self._detect_running = False
        self._detect_again = False  # a pull finished while a detection was running
        self._detection_started = False
        self._installed_ready = False
        # The install round does the work; the status pane shows it and the page follows it.
        self._round = InstallRound(self._svc, context.pull_manager, self)

        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(8)

        self.update_banner = UpdateBanner(self._svc.addons.addons_outdated)
        main.addWidget(self.update_banner)

        self._tabs = QTabWidget()
        main.addWidget(self._tabs, stretch=1)
        self.ollama_models = OllamaModelsTab(
            app_service=self._svc,
            hardware_setting=profile_setting(
                settings.get("hardware_profile", DEFAULTS["hardware_profile"])
            ),
            ollama_url=self._ollama_url,
            pull_manager=context.pull_manager,
        )
        self.providers = ProvidersTab(self._svc)
        self.name_detection = NameDetectionTab(self._svc)
        self.reading_models = ReadingModelsTab(self._svc)
        self._tabs.addTab(self.ollama_models, txt.TAB_LOCAL_MODELS)
        self._tabs.addTab(self.providers, txt.TAB_ONLINE_SERVICES)
        self._tabs.addTab(self.name_detection, txt.TAB_NAME_DETECTION)
        self._tabs.addTab(self.reading_models, TAB_READING_MODELS)

        self.status = InstallStatusPane(
            self._round,
            [
                self.reading_models.step_label,
                self.providers.step_label,
                self.name_detection.step_label,
            ],
            self._pending_packages,
        )
        main.addWidget(self.status)
        self.status.install_requested.connect(self._on_download_install)
        self.status.finished.connect(self._on_round_finished)
        self.status.step_shown.connect(self.ollama_models.mark_pulls)
        # The pulls belong to the main window's manager; the tree marks what it is pulling.
        self._round.pull_progress.connect(self.ollama_models.on_pull_progress)
        self.ollama_models.profile_change_requested.connect(self._on_profile_change_requested)
        self.update_banner.update_requested.connect(self._on_update_addons)

        # Enter in a text field must only do what that field does.  A dialog would also
        # "press" its default button (the first push button, e.g. Install Selected).  The
        # window turns its own OK into a non-default button while this page is shown.
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)

    def _on_update_addons(self) -> None:
        """Install the add-ons again with the app's package versions (the teacher clicked)."""
        if self._round.running():
            return
        self.status.begin_round([UPDATE_ADDONS_ITEM])
        self.update_banner.set_installing(True)
        if not self._round.start(update_addons=True):
            self.update_banner.set_installing(False)
            return
        self.status.round_started()

    # ── installing what the tabs ticked ──────────────────────────────────────────

    def _pending_packages(self) -> Sequence[str]:
        """Return the packages that wait for the next start (the status line asks for a restart)."""
        return self._svc.addons.pending_addon_packages()

    def _collect_pip_packages(self) -> list[str]:
        """Return pip package names for all checked, enabled pip-installable checkboxes."""
        return self.name_detection.pip_packages() + self.providers.pip_packages()

    def _on_download_install(self) -> None:
        """Start a round with what every tab has ticked (the install button)."""
        if self._round.running():
            return

        pip_packages = self._collect_pip_packages()
        spacy_models = self.name_detection.spacy_models()
        ollama_models = self.ollama_models.checked_models()
        items = self.reading_models.checked_items()
        reader = READER_ITEM in items
        page_models = PAGE_MODELS_ITEM in items

        if not any((pip_packages, spacy_models, ollama_models, reader, page_models)):
            return

        if ollama_models and not self.ollama_models.confirm_too_large(ollama_models):
            return

        steps = [f"{PACKAGE_PREFIX}{name}" for name in pip_packages]
        steps += [f"{SPACY_PREFIX}{model}" for model in spacy_models]
        if reader:
            steps.append(READER_ITEM)
        if page_models:
            steps.append(PAGE_MODELS_ITEM)
        steps += ollama_models
        self.status.begin_round(steps)
        started = self._round.start(
            packages=pip_packages,
            spacy_models=spacy_models,
            reader=reader,
            page_models=page_models,
            ollama_models=ollama_models,
            ollama_url=self._ollama_url,
        )
        if started:
            self.status.round_started()

    def _on_round_finished(self, installed_any: bool) -> None:
        """The round ended: re-read the banner, and look again at what is installed."""
        self.update_banner.set_installing(False)
        self.update_banner.refresh()
        if not installed_any:
            return
        # Something arrived: the installed list and the classifications are stale.
        self.packages_changed.emit()
        self._start_detection()

    def _on_profile_change_requested(self) -> None:
        """The computer line's "change…" link: the profile is edited on *This computer*."""
        self._navigate(SettingsPage.AI_SETUP)

    # ── detection ────────────────────────────────────────────────────────────────

    def _start_detection(self) -> None:
        """Ask what is installed; a request during a running check is repeated afterwards."""
        if self._detect_running:
            # Something changed while a check was running (e.g. a pull finished):
            # the running check may predate it, so check once more afterwards.
            self._detect_again = True
            return
        self._detect_running = True
        self.status.set_idle(txt.ADDONS_DETECTING)
        svc = self._svc
        url = self._ollama_url
        models = self.ollama_models.all_tags()
        self._detect_task.submit(
            lambda: detect_installed(svc, url, models), self._on_detect_finished
        )

    def _on_detect_finished(self, result: object, error: BaseException | None) -> None:
        self._detect_running = False
        if error is not None or not isinstance(result, dict):
            self.status.set_idle(
                txt.addons_detect_failed(str(error) if error else ""), warning=True
            )
            # Nothing is known to be installed, but the suggested ticks and the
            # requested preselection must not depend on this check (ticking never
            # downloads anything — that needs a click on Install).
            self._installed_ready = True
            self.ollama_models.mark_check_failed()
            self._tick_pending()
        else:
            self._on_detection_done(result)
        if self._detect_again:
            self._detect_again = False
            self._start_detection()

    def _on_detection_done(self, results: dict) -> None:
        """Hand what is installed to every tab, then tick what a deep link asked for."""
        self.status.set_idle(txt.ADDONS_DETECTED)
        self.reading_models.mark_installed(results)
        self.providers.mark_installed(results)
        self.name_detection.mark_installed(results)
        self.ollama_models.mark_installed(results)
        self._installed_ready = True
        self._tick_pending()

    def _tick_pending(self) -> None:
        """Tick what a deep link asked for, now that the installation check has answered."""
        for tab in (self.reading_models, self.providers, self.name_detection, self.ollama_models):
            tab.tick_preselect()

    # ── page protocol ────────────────────────────────────────────────────────────

    def on_shown(self) -> None:
        """Detect what is installed (first time), and follow a pull that is already running."""
        if not self._detection_started:
            self._detection_started = True
            self._start_detection()
        # If a pull is already running in the manager, follow it to show current progress
        model = self._round.follow_running_pull()
        if model:
            self.status.show_followed_pull(model)
        self.ollama_models.mark_pulls()

    def focus(self, focus: str, preselect: Sequence[str] = ()) -> None:
        """Show the tab named *focus*; tick *preselect*.

        Entries are model tags, ``provider:<name>`` (an online service's package),
        ``package:<name>`` (a package of its own, ``package:spacy``) and ``spacy:<model>``
        (a name-detection model).  A model alone does not tick spaCy: only the fix that says
        spaCy is missing does.
        """
        if focus == FOCUS_ADDON_UPDATE:
            self.update_banner.button.setFocus()
        tab = self._FOCUS_TABS.get(focus)
        if any(tag.startswith(PROVIDER_PREFIX) for tag in preselect):
            tab = self.TAB_ONLINE_SERVICES
        elif any(tag.startswith((READER_PREFIX, PAGE_MODELS_PREFIX)) for tag in preselect):
            tab = self.TAB_READING_MODELS
        elif tab is None and any(is_model_tag(tag) for tag in preselect):
            tab = self.TAB_LOCAL_MODELS
        if tab is not None:
            self._tabs.setCurrentIndex(tab)
        # Each tab picks its own entries out of the list: a package can be an online service's
        # and one of the name detector's at once, and only the tab knows how it is shown.
        for page in (
            self.reading_models,
            self.providers,
            self.name_detection,
            self.ollama_models,
        ):
            page.preselect(preselect)
        # The boxes of packages and model files need no answer from the Ollama server, so they
        # are ticked at once: the detection also asks that server, and with Ollama missing or
        # stopped it can take long, while the teacher looks at an empty tab.  Detection ticks
        # them again after it has reset the boxes (an installed one is then disabled instead).
        if self._installed_ready:
            # Detection is done: tick now (otherwise it ticks once detection finished).
            self._tick_pending()

    def set_hardware_setting(self, setting: HardwareProfileSetting) -> None:
        self.ollama_models.set_hardware_setting(setting)

    def set_ollama_url(self, url: str) -> None:
        new_url = url.strip() or DEFAULTS["ollama_url"]
        if new_url == self._ollama_url:
            return
        self._ollama_url = new_url
        self.ollama_models.set_ollama_url(new_url)
        if self._detection_started:
            self._start_detection()

    def close_blocker(self) -> str:
        """A pip install or language-model download must finish before the window closes."""
        if self._round.busy():
            return dlg.INSTALLATION_RUNNING
        return ""

    def shutdown(self) -> None:
        """Stop following the pull manager; a running pull keeps going in the main window."""
        self._round.detach()
