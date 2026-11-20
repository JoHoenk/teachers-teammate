"""The assistant's pages: one question each, and the answer it hands back.

Every page is a plain :class:`QWizardPage` that reads its starting point from the context and
hands its answer to the wizard (:meth:`SetupPage.assistant`).  The order is not decided here:
:func:`._flow.next_page` does that from the answers, so a page only says what it asks.

The pages that start background work build it the first time they are shown: the Ollama status
line and the computer profile on *Local AI*, the online service section on *Proofreading*, and
the setup plan on *Summary*.  A page the teacher never reaches therefore asks nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlparse

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...application.correction_options import review_has_own_model
from ...application.setup_plan import (
    DownloadKind,
    HandIn,
    LocalModelChoices,
    ModelChoice,
    PlannedDownload,
    PlannedStep,
    PrivacyCase,
    ProofreadingPlace,
    SetupAnswers,
    SetupPlan,
)
from ...config import DEFAULTS
from ...domain.data_location import (
    STAGE_ANONYMIZATION,
    STAGE_CORRECTION,
    STAGE_OCR,
    DataLocation,
    DataRegion,
    StageLocation,
)
from ...domain.language import LANGUAGES
from ...domain.local_models import PAGE_MODELS_ADDON_ID
from ...domain.model_catalog import DiskVerdict, Suitability, short_model_name
from ...texts.setup import (
    CLASSES_HANDWRITTEN,
    CLASSES_HANDWRITTEN_NOTE,
    CLASSES_LANGUAGE,
    CLASSES_LANGUAGE_NOTE,
    CLASSES_QUESTION,
    CLASSES_TITLE,
    CLASSES_TYPED,
    CLASSES_TYPED_NOTE,
    DONE_DONE,
    DONE_DOWNLOADS_TITLE,
    DONE_FAILED,
    DONE_NAME_PAGE_MODELS,
    DONE_NAME_READER,
    DONE_NOTHING,
    DONE_OLLAMA_GOES_ON,
    DONE_OVERALL,
    DONE_PROGRESS,
    DONE_PROGRESS_MB,
    DONE_PURPOSE_ADDON,
    DONE_PURPOSE_NAMES,
    DONE_PURPOSE_PAGE_MODELS,
    DONE_PURPOSE_PROOFREADING,
    DONE_PURPOSE_READER,
    DONE_PURPOSE_READING,
    DONE_RUNNING,
    DONE_SELECT,
    DONE_SELECT_TOOLTIP,
    DONE_SPEED_TITLE,
    DONE_SPEED_WAITING,
    DONE_TEXT,
    DONE_TITLE,
    DONE_TITLE_PROBLEMS,
    DONE_TITLE_RUNNING,
    DONE_WAITING,
    FIT_VERY_SLOW,
    FIT_WORDS,
    LOCAL_AI_FIND,
    LOCAL_AI_FIND_TOOLTIP,
    LOCAL_AI_HERE,
    LOCAL_AI_INTRO,
    LOCAL_AI_OTHER_PC_HEADING,
    LOCAL_AI_OTHER_PC_TEXT,
    LOCAL_AI_QUESTION,
    LOCAL_AI_SERVER,
    LOCAL_AI_THIS_PC_HEADING,
    LOCAL_AI_THIS_PC_TEXT,
    LOCAL_AI_TITLE,
    MODEL_INSTALLED,
    PRIVACY_ALREADY_ON,
    PRIVACY_EXPLAIN,
    PRIVACY_EXTRA,
    PRIVACY_EXTRA_INSTALLED,
    PRIVACY_EXTRA_NOTE,
    PRIVACY_HAS_DETECTION,
    PRIVACY_HIDE_HERE,
    PRIVACY_HIDE_HERE_NOTE,
    PRIVACY_MULTILINGUAL,
    PRIVACY_NEEDS_DETECTION,
    PRIVACY_SCHOOL_NOTE,
    PRIVACY_SCHOOL_NOTE_NETWORK,
    PRIVACY_SENT,
    PRIVACY_SENT_NETWORK,
    PRIVACY_STAYS,
    PRIVACY_STAYS_NOTE,
    PRIVACY_TITLE,
    PROOFREADING_INSTALL_NOW,
    PROOFREADING_INSTALL_TOOLTIP,
    PROOFREADING_INTRO,
    PROOFREADING_LOCAL,
    PROOFREADING_MODEL,
    PROOFREADING_MODEL_TOOLTIP,
    PROOFREADING_OFF,
    PROOFREADING_ONLINE,
    PROOFREADING_ONLINE_NOTE,
    PROOFREADING_REVIEW_KEPT,
    PROOFREADING_TITLE,
    PROOFREADING_USE_RECOMMENDED,
    READER_NAME,
    SUGGESTION,
    SUGGESTION_CHECKING,
    SUGGESTION_DOWNLOADED,
    SUGGESTION_UNKNOWN,
    SUMMARY_DISK_SHORT,
    SUMMARY_DOWNLOADS_NEEDED,
    SUMMARY_DOWNLOADS_TOTAL,
    SUMMARY_FAILED,
    SUMMARY_INSTALLING,
    SUMMARY_LATER,
    SUMMARY_NO_DOWNLOADS,
    SUMMARY_OLLAMA_STOPPED,
    SUMMARY_TITLE,
    SUMMARY_WORKING,
    WELCOME_FOOTNOTE,
    WELCOME_POINTS,
    WELCOME_TEXT,
    WELCOME_TITLE,
    name_model_label,
)
from .. import _user_text as txt
from .._background import LatestTask
from .._data_flow_summary import short_place
from .._hardware_profile_selector import HardwareProfileSelector
from .._install_round import (
    PACKAGE_PREFIX,
    PAGE_MODELS_ITEM,
    READER_PREFIX,
    READER_PROGRESS_STATUS,
)
from .._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX
from .._model_item_delegate import ModelItem, fill_grouped
from .._model_item_delegate import install as install_model_labels
from .._model_wording import compact_suitability_text
from .._ollama_status_line import OllamaStatusLine
from ..settings._ai_service_section import PROOFREADING, AiServiceSection
from ..settings._server_search_dialog import ServerSearchDialog
from ..settings._speed_panel import SpeedPanel
from ._context import SetupContext
from ._flow import ORDER, SetupPageId, next_page
from ._summary import SummaryRows, size_text, summary_rows

if TYPE_CHECKING:
    from ._assistant import SetupAssistant

#: The overall bar counts in this many steps, so a partly finished download moves it too.
_BAR_STEPS = 1000
#: Bytes in a gigabyte, for the pull progress the manager reports in bytes.
_GB = 1_000_000_000.0
_MB = 1_000_000.0


def _fit_words(step: PlannedStep) -> str:
    """Return the plain-language fit of *step*'s model, or ``""`` when there is nothing to say."""
    rating = step.rating
    if rating is None:
        return ""
    if rating.suitability is Suitability.SLOW and rating.very_slow:
        return FIT_VERY_SLOW
    return FIT_WORDS.get(rating.suitability, "")


def _choice_label(choice: ModelChoice) -> str:
    """Return the label at the right of a model in the list: how it fits, and what it costs."""
    fit = compact_suitability_text(
        choice.rating.suitability, "general", very_slow=choice.rating.very_slow
    )
    if choice.installed:
        return f"{fit} · {MODEL_INSTALLED}"
    if choice.size_gb is not None:
        return f"{fit} · {choice.size_gb:.1f} GB"
    return fit


def _size_words(step: PlannedStep) -> str:
    """Return the download size of *step*'s model, or ``""`` when it is not catalogued."""
    from ...domain.model_catalog import find_spec  # noqa: PLC0415

    spec = find_spec(step.model)
    return f"{spec.download_gb:.1f} GB download" if spec is not None else ""


class SetupPage(QWizardPage):
    """One page of the assistant; see the module docstring."""

    page_id: ClassVar[SetupPageId]

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(assistant)
        self._context = context
        self._assistant = assistant
        self._scroll: QScrollArea | None = None

    def assistant(self) -> SetupAssistant:
        """Return the wizard this page belongs to."""
        return self._assistant

    def _content(self) -> QVBoxLayout:
        """Return the layout the page's widgets go into: inside a frameless scroll area.

        QWizard gives every page the same height, so a page whose content is taller than the
        window would have its widgets squeezed below their size hint (a drop-down cut in half).
        In a scroll area they keep their height and the page scrolls.
        """
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # The page's own background shows through, in the light and the dark theme alike.
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; } "
            "QScrollArea > QWidget > QWidget { background: transparent; }"
        )
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(holder)
        outer.addWidget(scroll)
        self._scroll = scroll
        return layout

    def nextId(self) -> int:  # Qt override
        """Return the page that follows this one for the answers as they are now."""
        following = next_page(self.page_id, self._assistant.answers())
        return -1 if following is None else ORDER.index(following)

    def shutdown(self) -> None:
        """Stop background work before the window is destroyed."""

    def on_packages_changed(self) -> None:
        """A package was installed: ask the service for its models again."""


def _heading(text: str) -> QLabel:
    """Return *text* as a bold heading line."""
    label = QLabel(text)
    font = label.font()
    font.setPointSize(12)
    font.setBold(True)
    label.setFont(font)
    return label


def _note(text: str) -> QLabel:
    """Return *text* as a small grey note under a choice."""
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: #888; font-size: 11px;")
    return label


def _indent(widget: QWidget, pixels: int = 22) -> QWidget:
    """Return *widget* in a row indented by *pixels*, so it reads as part of the choice above."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(pixels, 0, 0, 0)
    layout.addWidget(widget)
    return row


class WelcomePage(SetupPage):
    """What the app does and what this assistant will ask."""

    page_id = SetupPageId.WELCOME

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(WELCOME_TITLE)
        layout = self._content()
        for text in (WELCOME_TEXT, WELCOME_POINTS, WELCOME_FOOTNOTE):
            label = QLabel(text)
            label.setWordWrap(True)
            layout.addWidget(label)
        layout.addStretch(1)


class ClassesPage(SetupPage):
    """The language of the class and what the students hand in."""

    page_id = SetupPageId.CLASSES

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(CLASSES_TITLE)
        layout = self._content()

        layout.addWidget(_heading(CLASSES_LANGUAGE))
        self._language = QComboBox()
        self._language.setEditable(True)
        self._language.addItems(LANGUAGES)
        start = context.language or str(context.settings.get("language") or "")
        if start and start not in LANGUAGES:
            self._language.addItem(start)
        self._language.setCurrentText(start or DEFAULTS["language"])
        layout.addWidget(self._language)
        layout.addWidget(_note(CLASSES_LANGUAGE_NOTE))

        layout.addSpacing(10)
        layout.addWidget(_heading(CLASSES_QUESTION))
        self._handwritten = QRadioButton(CLASSES_HANDWRITTEN)
        self._typed = QRadioButton(CLASSES_TYPED)
        handwritten = bool(context.settings.get("recognition_enabled", True))
        self._handwritten.setChecked(handwritten)
        self._typed.setChecked(not handwritten)
        layout.addWidget(self._handwritten)
        layout.addWidget(_indent(_note(CLASSES_HANDWRITTEN_NOTE)))
        layout.addWidget(self._typed)
        layout.addWidget(_indent(_note(CLASSES_TYPED_NOTE)))
        layout.addStretch(1)

    def language(self) -> str:
        """Return the language the teacher chose."""
        return self._language.currentText().strip() or DEFAULTS["language"]

    def hand_in(self) -> HandIn:
        """Return what the students hand in."""
        return HandIn.HANDWRITTEN if self._handwritten.isChecked() else HandIn.TYPED


class LocalAiPage(SetupPage):
    """Where Ollama runs, and which computer the suggestions are for."""

    page_id = SetupPageId.LOCAL_AI

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(LOCAL_AI_TITLE)
        self._status: OllamaStatusLine | None = None
        self._profile: HardwareProfileSelector | None = None
        self._built = False

        layout = self._content()
        intro = QLabel(LOCAL_AI_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(8)
        layout.addWidget(_heading(LOCAL_AI_QUESTION))

        # What was found comes first: it is what the teacher most often wants, and the choices
        # below say where to look when it is not.
        self._status_holder = QWidget()
        self._status_layout = QVBoxLayout(self._status_holder)
        self._status_layout.setContentsMargins(0, 0, 0, 4)
        layout.addWidget(self._status_holder)

        saved = str(context.settings.get("ollama_url") or DEFAULTS["ollama_url"])
        local = self._context.app_service.ollama.is_local(saved, include_wsl_host=True)
        self._here = QRadioButton(LOCAL_AI_HERE)
        self._server = QRadioButton(LOCAL_AI_SERVER)
        self._here.setChecked(local)
        self._server.setChecked(not local)
        layout.addWidget(self._here)
        layout.addWidget(self._server)

        # The address and the search belong to "another PC", so they sit below it.
        server_row = QWidget()
        server_layout = QHBoxLayout(server_row)
        server_layout.setContentsMargins(22, 0, 0, 0)
        self._url = QLineEdit(saved)
        self._url.setPlaceholderText(DEFAULTS["ollama_url"])
        self._url.setEnabled(not local)
        server_layout.addWidget(self._url, stretch=1)
        self._find = QPushButton(LOCAL_AI_FIND)
        self._find.setToolTip(LOCAL_AI_FIND_TOOLTIP)
        self._find.setEnabled(not local)
        self._find.clicked.connect(self._on_find)
        server_layout.addWidget(self._find)
        layout.addWidget(server_row)

        layout.addSpacing(8)
        self._computer_heading = _heading(LOCAL_AI_THIS_PC_HEADING)
        layout.addWidget(self._computer_heading)
        self._computer_text = QLabel(LOCAL_AI_THIS_PC_TEXT)
        self._computer_text.setWordWrap(True)
        layout.addWidget(self._computer_text)

        self._profile_holder = QWidget()
        self._profile_layout = QVBoxLayout(self._profile_holder)
        self._profile_layout.setContentsMargins(0, 4, 0, 0)
        layout.addWidget(self._profile_holder)
        layout.addStretch(1)

        self._here.toggled.connect(self._on_place_changed)
        self._url.editingFinished.connect(self._on_url_edited)

    # ── answers ───────────────────────────────────────────────────────────────

    def ollama_url(self) -> str:
        """Return the address Ollama runs at, as the page shows it."""
        text = self._url.text().strip()
        if self._here.isChecked() and not self._context.app_service.ollama.is_local(
            text, include_wsl_host=True
        ):
            return DEFAULTS["ollama_url"]
        return text or DEFAULTS["ollama_url"]

    def hardware_profile(self) -> str:
        """Return the computer profile the suggestions follow."""
        if self._profile is not None:
            return self._profile.setting()
        return str(self._context.settings.get("hardware_profile") or DEFAULTS["hardware_profile"])

    # ── life cycle ────────────────────────────────────────────────────────────

    def initializePage(self) -> None:  # Qt override
        """Build the status line and the profile picker the first time the page is shown."""
        if self._built:
            return
        self._built = True
        self._status = OllamaStatusLine(
            app_service=self._context.app_service, url=self.ollama_url(), parent=self
        )
        self._status.use_server_requested.connect(self._on_use_server)
        self._status_layout.addWidget(self._status)
        self._profile = HardwareProfileSelector(
            app_service=self._context.app_service,
            setting=self.hardware_profile(),
            ollama_url=self.ollama_url(),
            parent=self,
        )
        self._profile_layout.addWidget(self._profile)
        self._refresh_computer_text()

    def shutdown(self) -> None:
        """Stop the status line's checks."""
        if self._status is not None:
            self._status.shutdown()

    # ── handlers ──────────────────────────────────────────────────────────────

    def _on_place_changed(self, _here: bool = False) -> None:
        """Follow the choice: the address field belongs to a server elsewhere."""
        on_server = self._server.isChecked()
        self._url.setEnabled(on_server)
        self._find.setEnabled(on_server)
        if not on_server and not self._context.app_service.ollama.is_local(
            self._url.text().strip(), include_wsl_host=True
        ):
            self._url.setText(DEFAULTS["ollama_url"])
        self._follow_address()

    def _on_url_edited(self) -> None:
        self._follow_address()

    def _on_use_server(self, url: str) -> None:
        """Take the server the status line found, because the teacher pressed "Use it"."""
        if self._context.app_service.ollama.is_local(url, include_wsl_host=True):
            self._here.setChecked(True)
        else:
            self._server.setChecked(True)
        self._url.setText(url)
        self._follow_address()

    def _follow_address(self) -> None:
        """Hand the address to the status line and the profile picker."""
        url = self.ollama_url()
        if self._status is not None:
            self._status.set_url(url)
        if self._profile is not None:
            self._profile.set_ollama_url(url)
        self._refresh_computer_text()

    def _refresh_computer_text(self) -> None:
        """Say whose computer the suggestions are for: this one, or the one Ollama runs on."""
        other = not self._context.app_service.ollama.is_local(
            self.ollama_url(), include_wsl_host=True
        )
        self._computer_heading.setText(
            LOCAL_AI_OTHER_PC_HEADING if other else LOCAL_AI_THIS_PC_HEADING
        )
        self._computer_text.setText(LOCAL_AI_OTHER_PC_TEXT if other else LOCAL_AI_THIS_PC_TEXT)

    def _on_find(self) -> None:
        """Open the search dialog; it searches this computer at once and the network on request."""
        dialog = ServerSearchDialog(self._context.app_service, self)
        dialog.server_chosen.connect(self._on_use_server)
        dialog.exec()
        dialog.deleteLater()


class ProofreadingPage(SetupPage):
    """Where the proofreading runs, and which model it uses."""

    page_id = SetupPageId.PROOFREADING

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(PROOFREADING_TITLE)
        self._section: AiServiceSection | None = None
        self._task = LatestTask(self)

        layout = self._content()
        intro = QLabel(PROOFREADING_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(8)

        saved = str(context.settings.get("correction_provider") or DEFAULTS["correction_provider"])
        self._local = QRadioButton(PROOFREADING_LOCAL)
        self._online = QRadioButton(PROOFREADING_ONLINE)
        self._off = QRadioButton(PROOFREADING_OFF)
        # One group, so the three choices exclude each other whatever holds them.
        self._places = QButtonGroup(self)
        for button in (self._local, self._online, self._off):
            self._places.addButton(button)
        self._local.setChecked(saved == "ollama")
        self._online.setChecked(saved not in ("ollama", ""))
        layout.addWidget(self._local)
        self._suggestion = _note(SUGGESTION_CHECKING)
        layout.addWidget(_indent(self._suggestion))
        layout.addWidget(self._build_model_choice())
        layout.addWidget(self._online)

        self._online_holder = QWidget()
        self._online_layout = QVBoxLayout(self._online_holder)
        self._online_layout.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(self._online_holder)
        layout.addWidget(_indent(_note(PROOFREADING_ONLINE_NOTE)))

        # Added like the other two choices: nothing indents it.
        layout.addWidget(self._off)

        self._review_note = _note(PROOFREADING_REVIEW_KEPT)
        self._review_note.setVisible(False)
        layout.addWidget(self._review_note)
        layout.addStretch(1)

        self._local.toggled.connect(self._on_place_changed)
        self._online.toggled.connect(self._on_place_changed)
        self._off.toggled.connect(self._on_place_changed)

    def _build_model_choice(self) -> QWidget:
        """Build the model choice of the local AI: a list, and "use the recommended one"."""
        self._use_recommended = QCheckBox(PROOFREADING_USE_RECOMMENDED)
        self._use_recommended.setChecked(True)
        self._use_recommended.toggled.connect(self._sync_model_box)
        self._model_box = QComboBox()
        self._model_box.setToolTip(PROOFREADING_MODEL_TOOLTIP)
        self._model_box.setEnabled(False)
        install_model_labels(self._model_box)
        self._model_row = QWidget()
        grid = QGridLayout(self._model_row)
        grid.setContentsMargins(22, 0, 0, 0)
        grid.addWidget(self._use_recommended, 0, 0, 1, 2)
        grid.addWidget(QLabel(PROOFREADING_MODEL), 1, 0)
        grid.addWidget(self._model_box, 1, 1)
        grid.setColumnStretch(1, 1)
        self._choices: LocalModelChoices | None = None
        return self._model_row

    # ── answers ───────────────────────────────────────────────────────────────

    def local_model(self) -> str:
        """Return the model the teacher chose, or ``""`` to leave it to the assistant."""
        if self._use_recommended.isChecked():
            return ""
        return str(self._model_box.currentData() or "")

    def place(self) -> ProofreadingPlace:
        """Return where the proofreading should run."""
        if self._online.isChecked():
            return ProofreadingPlace.ONLINE
        if self._off.isChecked():
            return ProofreadingPlace.OFF
        return ProofreadingPlace.LOCAL

    def online_choice(self) -> tuple[str, str]:
        """Return the online service and model, or ``("", "")`` while none was chosen."""
        if self._section is None:
            return ("", "")
        return self._section.choice()

    # ── life cycle ────────────────────────────────────────────────────────────

    def initializePage(self) -> None:  # Qt override
        """Show the options that apply and ask for the suggested local model."""
        handwritten = self.assistant().answers().hand_in is HandIn.HANDWRITTEN
        self._off.setVisible(handwritten)
        if not handwritten and self._off.isChecked():
            self._local.setChecked(True)
        self._review_note.setVisible(self._review_kept())
        self._on_place_changed()
        self._ask_for_suggestion()

    def _review_kept(self) -> bool:
        """Return whether the saved content review has its own model, which Set up leaves alone."""
        return review_has_own_model(
            self._context.settings, self._context.app_service.providers.effective_model
        )

    def shutdown(self) -> None:
        """Stop the suggestion task and the online section's model fetch."""
        self._task.cancel()
        if self._section is not None:
            self._section.shutdown()

    def on_packages_changed(self) -> None:
        """A package was installed: ask the service for its models again."""
        if self._section is not None:
            self._section.on_packages_changed()

    # ── handlers ──────────────────────────────────────────────────────────────

    def _on_place_changed(self, _checked: bool = False) -> None:
        """Show the online section only for the online choice, building it the first time."""
        online = self._online.isChecked()
        if online and self._section is None:
            self._build_section()
        self._online_holder.setVisible(online)
        self._model_row.setVisible(self._local.isChecked())

    def _build_section(self) -> None:
        """Build the online service section: the same list, key row and check as AI models."""
        answers = self.assistant().answers()
        section = AiServiceSection(
            PROOFREADING,
            app_service=self._context.app_service,
            hardware_profile=answers.hardware_profile,
            ollama_url=answers.ollama_url,
            open_downloads=self._install_package,
            key_drafts=self.assistant().key_drafts(),
            same_model_tip=False,
            self_hosted=False,
            install_button_text=PROOFREADING_INSTALL_NOW,
            install_button_tooltip=PROOFREADING_INSTALL_TOOLTIP,
            parent=self._online_holder,
        )
        section.load(self._context.settings)
        self._online_layout.addWidget(section)
        self._section = section

    def _install_package(self, preselect: list[str], _parent: QWidget | None) -> None:
        """Install the service's package right here, then ask it for its models."""
        packages = [
            pip_name
            for entry in preselect
            if entry.startswith(PROVIDER_PREFIX)
            for pip_name in [
                self._context.app_service.providers.missing_provider_package(
                    entry[len(PROVIDER_PREFIX) :]
                )
            ]
            if pip_name
        ]
        if packages:
            self.assistant().install_round().start(packages=packages)

    def _ask_for_suggestion(self) -> None:
        """Ask the setup plan which local model this computer would get, and show how it fits."""
        setup = self._context.app_service.setup
        answers = self.assistant().answers()
        local = SetupAnswers(
            language=answers.language,
            hand_in=answers.hand_in,
            proofreading=ProofreadingPlace.LOCAL,
            ollama_url=answers.ollama_url,
            hardware_profile=answers.hardware_profile,
            local_ai_shown=answers.local_ai_shown,
        )
        current = dict(self._context.settings)
        self._suggestion.setText(SUGGESTION_CHECKING)
        self._task.submit(
            lambda: setup.plan_with_model_choices(local, current), self._on_plan_and_choices
        )

    def _on_plan_and_choices(self, result: object, error: BaseException | None) -> None:
        """Show the suggestion and fill the model list from the one answer."""
        if error is not None or not isinstance(result, tuple) or len(result) != 2:
            self._on_suggestion(None, error)
            return
        plan, choices = result
        self._on_suggestion(plan, None)
        self._on_choices(choices, None)

    def _on_choices(self, result: object, error: BaseException | None) -> None:
        """Fill the model list with what can work here, the recommended one selected."""
        if error is not None or not isinstance(result, LocalModelChoices):
            return
        self._choices = result
        groups: list[list[ModelItem]] = [[], []]
        for choice in result.choices:
            good = choice.rating.suitability in (Suitability.BEST, Suitability.GOOD)
            groups[0 if good else 1].append(
                ModelItem(
                    text=choice.tag,
                    label=_choice_label(choice),
                    data=choice.tag,
                    tooltip=choice.rating.note,
                )
            )
        previous = self._model_box.currentData()
        self._model_box.blockSignals(True)
        fill_grouped(self._model_box, groups)
        self._model_box.blockSignals(False)
        self._select_model(str(previous) if previous else "")
        self._sync_model_box()

    def _select_model(self, tag: str) -> None:
        """Show *tag* in the list when it is there, else the recommended model."""
        for wanted in (tag, self._choices.suggested if self._choices is not None else ""):
            index = self._model_box.findData(wanted) if wanted else -1
            if index >= 0:
                self._model_box.setCurrentIndex(index)
                return

    def _sync_model_box(self, _checked: bool = False) -> None:
        """Follow "use the recommended model": the list shows it and is disabled while it is on."""
        recommended = self._use_recommended.isChecked()
        if recommended and self._choices is not None:
            self._select_model(self._choices.suggested)
        self._model_box.setEnabled(not recommended and self._model_box.count() > 0)

    def _on_suggestion(self, plan: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(plan, SetupPlan):
            self._suggestion.setText(SUGGESTION_UNKNOWN)
            return
        step = next((row for row in plan.steps if row.stage == STAGE_CORRECTION), None)
        if step is None or not step.model:
            self._suggestion.setText(SUGGESTION_UNKNOWN)
            return
        fit = _fit_words(step)
        if step.installed:
            text = SUGGESTION_DOWNLOADED.format(model=step.model, fit=fit)
        else:
            text = SUGGESTION.format(model=step.model, fit=fit, size=_size_words(step))
        self._suggestion.setText(text)


class PrivacyPage(SetupPage):
    """What happens to the names: removed before the text is sent, or hidden in this app."""

    page_id = SetupPageId.PRIVACY

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:  # noqa: PLR0915
        super().__init__(context, assistant)
        self.setTitle(PRIVACY_TITLE)
        self._case: PrivacyCase | None = None
        layout = self._content()

        # Text that leaves this computer — to an online service or to another PC in the network:
        # what is sent, and whether the names are replaced first.
        self._sending = QWidget()
        sending = QVBoxLayout(self._sending)
        sending.setContentsMargins(0, 0, 0, 0)
        self._sent = QLabel()
        self._sent.setWordWrap(True)
        sending.addWidget(self._sent)
        sending.addSpacing(8)
        self._remove = QCheckBox(txt.REMOVE_PERSONAL_INFO)
        sending.addWidget(self._remove)
        sending.addWidget(_indent(_note(PRIVACY_EXPLAIN)))
        self._detection = _note("")
        sending.addWidget(_indent(self._detection))
        self._already = _note(PRIVACY_ALREADY_ON)
        self._already.setVisible(False)
        sending.addWidget(_indent(self._already))
        sending.addSpacing(8)
        self._school = _note(PRIVACY_SCHOOL_NOTE)
        sending.addWidget(self._school)
        layout.addWidget(self._sending)

        # Everything on this computer: nothing is sent, so there is nothing to remove; the names
        # can still be hidden in the texts this app shows.
        self._stays = QWidget()
        stays = QVBoxLayout(self._stays)
        stays.setContentsMargins(0, 0, 0, 0)
        stays_text = QLabel(PRIVACY_STAYS)
        stays_text.setWordWrap(True)
        stays.addWidget(stays_text)
        stays.addWidget(_note(PRIVACY_STAYS_NOTE))
        stays.addSpacing(8)
        self._hide = QCheckBox(PRIVACY_HIDE_HERE)
        stays.addWidget(self._hide)
        stays.addWidget(_indent(_note(PRIVACY_HIDE_HERE_NOTE)))
        self._hide_detection = _note("")
        stays.addWidget(_indent(self._hide_detection))
        self._hide_already = _note(PRIVACY_ALREADY_ON)
        self._hide_already.setVisible(False)
        stays.addWidget(_indent(self._hide_already))
        layout.addWidget(self._stays)

        # One more language to find names in, below whichever of the two the teacher sees.
        self._extra_holder = QWidget()
        extra = QVBoxLayout(self._extra_holder)
        extra.setContentsMargins(0, 8, 0, 0)
        self._extra = QCheckBox(PRIVACY_EXTRA)
        extra.addWidget(self._extra)
        self._extra_model = QComboBox()
        extra.addWidget(_indent(self._extra_model))
        extra.addWidget(_indent(_note(PRIVACY_EXTRA_NOTE)))
        layout.addWidget(self._extra_holder)
        layout.addStretch(1)
        self._extra.toggled.connect(self._sync_extra)
        self._remove.toggled.connect(self._sync_extra)
        self._hide.toggled.connect(self._sync_extra)

    def extra_names_model(self) -> str:
        """Return the spaCy model of the extra language, or ``""`` when none is ticked."""
        if not (self._extra_holder.isVisibleTo(self) and self._extra.isChecked()):
            return ""
        return str(self._extra_model.currentData() or "")

    def _sync_extra(self, _checked: bool = False) -> None:
        """Offer the extra language while names are looked for, and its list while it is ticked."""
        if self._case is PrivacyCase.THIS_COMPUTER:
            looking = self._hide.isChecked()
        else:
            looking = self._remove.isChecked()
        self._extra_holder.setVisible(self._case is not None and looking)
        self._extra_model.setEnabled(self._extra.isChecked())

    def _fill_extra(self, language: str) -> None:
        """List the languages that can come next to *language*, with the saved one selected."""
        service = self._context.app_service.anonymization
        installed = set(service.list_installed_spacy_models())
        saved = str(self._context.settings.get("anonymizer_secondary_model") or "")
        system = service.system_spacy_model()
        # Coming back to the page keeps what the teacher ticked, as long as it still fits.
        before = str(self._extra_model.currentData() or "") if self._extra.isChecked() else ""
        self._extra_model.clear()
        models = service.extra_name_models(language)
        for model in models:
            label = name_model_label(model)
            if model in installed:
                label = f"{label} ({PRIVACY_EXTRA_INSTALLED})"
            self._extra_model.addItem(label, userData=model)
        remembered = before if before in models else saved
        chosen = remembered if remembered in models else (system if system in models else "")
        self._extra_model.setCurrentIndex(max(self._extra_model.findData(chosen), 0))
        # A language that is saved as the extra one stays ticked: the assistant turns nothing off.
        self._extra.setChecked(remembered in models)

    def remove_names(self) -> bool:
        """Return whether names should be replaced before the text is sent."""
        return self._remove.isChecked()

    def hide_names(self) -> bool:
        """Return whether names should be hidden in the texts this app shows."""
        return self._case is PrivacyCase.THIS_COMPUTER and self._hide.isChecked()

    def initializePage(self) -> None:  # Qt override
        """Say where the text goes, and offer what fits: removal when it leaves, hiding when not."""
        answers = self.assistant().answers()
        service = self._context.app_service.anonymization
        case = answers.privacy_case
        self._case = case
        leaves = case is not PrivacyCase.THIS_COMPUTER
        self._sending.setVisible(leaves)
        self._stays.setVisible(not leaves)
        detection = service.anonymizer_available(answers.language)
        model = service.spacy_model_for_language(answers.language)
        detection_text = (PRIVACY_HAS_DETECTION if detection else PRIVACY_NEEDS_DETECTION).format(
            language=answers.language, model=model
        )
        if model == service.spacy_model_for_language(""):
            # A language without a model of its own gets the multilingual one.
            detection_text += " " + PRIVACY_MULTILINGUAL.format(language=answers.language)
        self._fill_extra(answers.language)

        if leaves:
            if case is PrivacyCase.ONLINE:
                info = self._context.app_service.providers.get_provider_info(
                    answers.online_choice[0]
                )
                name = str(info.get("display_name") or answers.online_choice[0])
                self._sent.setText(PRIVACY_SENT.format(service=name))
                self._school.setText(PRIVACY_SCHOOL_NOTE)
            else:
                host = urlparse(answers.ollama_url).hostname or answers.ollama_url
                self._sent.setText(PRIVACY_SENT_NETWORK.format(host=host))
                self._school.setText(PRIVACY_SCHOOL_NOTE_NETWORK)
            already_on = bool(self._context.settings.get("anonymization_enabled"))
            self._remove.setChecked(True)
            self._remove.setEnabled(not already_on)
            self._already.setVisible(already_on)
            self._detection.setText(detection_text)
            self._sync_extra()
            return

        # What the assistant never does is turn something off: a setting that is on stays on,
        # and its box says so.
        wish = self._context.display_anonymized
        self._hide.setChecked(wish)
        self._hide.setEnabled(not (wish and detection))
        self._hide_already.setVisible(wish and detection)
        self._hide_detection.setText("" if detection else detection_text)
        self._hide_detection.setVisible(not detection)
        self._sync_extra()


class SummaryPage(SetupPage):
    """What the assistant will set and download, and the Set up button."""

    page_id = SetupPageId.SUMMARY

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(SUMMARY_TITLE)
        self._plan: SetupPlan | None = None
        self._task = LatestTask(self)
        # The downloads start here and the choices are settled: no Back from the next page, so
        # Set up cannot run twice.
        self.setCommitPage(True)
        assistant.install_round().finished.connect(lambda _installed: self.completeChanged.emit())
        layout = self._content()
        self._headline = QLabel(SUMMARY_WORKING)
        self._headline.setWordWrap(True)
        layout.addWidget(self._headline)
        self._rows = SummaryRows()
        layout.addWidget(self._rows)
        self._total = _note("")
        layout.addWidget(self._total)
        self._disk = _note("")
        self._disk.setVisible(False)
        layout.addWidget(self._disk)
        self._stopped = _note(SUMMARY_OLLAMA_STOPPED)
        self._stopped.setVisible(False)
        layout.addWidget(self._stopped)
        self._later = _note(SUMMARY_LATER)
        self._later.setVisible(False)
        layout.addWidget(self._later)
        self._installing = _note(SUMMARY_INSTALLING)
        self._installing.setVisible(False)
        layout.addWidget(self._installing)
        layout.addStretch(1)

    def plan(self) -> SetupPlan | None:
        """Return the setup plan, or ``None`` while it is being worked out."""
        return self._plan

    def initializePage(self) -> None:  # Qt override
        """Work the setup plan out in the background; Set up stays disabled until it arrives."""
        self._plan = None
        self._headline.setText(SUMMARY_WORKING)
        self._rows.set_rows([])
        self._total.clear()
        self._disk.setVisible(False)
        self._stopped.setVisible(False)
        self._later.setVisible(False)
        self.completeChanged.emit()
        setup = self._context.app_service.setup
        answers = self.assistant().answers()
        current = dict(self._context.settings)
        self._task.submit(lambda: setup.plan_setup(answers, current), self._on_plan)

    def isComplete(self) -> bool:  # Qt override
        """Return whether the setup was worked out and no install runs, so Set up can be pressed.

        Set up hands the downloads to the install round; a round that still runs (the
        Proofreading page's "Install now") would turn them down without a word.
        """
        busy = self.assistant().install_round().running()
        self._installing.setVisible(busy)
        return self._plan is not None and not busy

    def validatePage(self) -> bool:  # Qt override
        """Set up: store the keys, hand the result over and start the downloads."""
        return self.assistant().commit(self.downloads())

    def downloads(self) -> list[PlannedDownload]:
        """Return the downloads the teacher left ticked."""
        return self._rows.downloads()

    def shutdown(self) -> None:
        """Stop waiting for the setup plan."""
        self._task.cancel()

    def _on_plan(self, plan: object, error: BaseException | None) -> None:
        if error is not None or not isinstance(plan, SetupPlan):
            self._headline.setText(SUMMARY_FAILED)
            return
        self._plan = plan
        self._headline.setText(SUMMARY_TITLE)
        self._rows.set_rows(summary_rows(plan, self._place_of, key_missing=self._key_missing(plan)))
        self._show_downloads(plan)
        self._later.setVisible(True)
        self.completeChanged.emit()

    def _show_downloads(self, plan: SetupPlan) -> None:
        """Say what the downloads add up to, and warn when the disk is short."""
        self._total.setText(_downloads_text(plan))
        self._disk.setVisible(plan.disk is DiskVerdict.NOT_ENOUGH)
        if plan.disk is DiskVerdict.NOT_ENOUGH:
            self._disk.setText(
                SUMMARY_DISK_SHORT.format(free=plan.free_disk_gb or 0.0, needed=plan.needed_gb)
            )
        self._stopped.setVisible(
            any(
                download.kind is DownloadKind.OLLAMA_MODEL and not download.possible
                for download in plan.downloads
            )
        )

    def _place_of(self, provider: str) -> str:
        """Return where *provider* runs in the few words the summary has room for."""
        if provider == "ollama":
            url = self.assistant().answers().ollama_url
            return short_place(
                self._context.app_service.providers.describe_llm_location(provider, "", url)
            )
        info = self._context.app_service.providers.get_provider_info(provider)
        return short_place(
            StageLocation(
                location=DataLocation.ONLINE,
                service=str(info.get("display_name") or provider),
                region=DataRegion(
                    self._context.app_service.providers.get_provider_data_region(provider)
                ),
            )
        )

    def _key_missing(self, plan: SetupPlan) -> set[str]:
        """Return the online providers the setup plan uses that have no key yet."""
        return {
            step.provider
            for step in plan.steps
            if step.provider
            and step.provider != "ollama"
            and self._context.app_service.providers.provider_key_missing(step.provider)
        }


def _downloads_text(plan: SetupPlan) -> str:
    """Return the line under the rows: what the downloads add up to, and the free space."""
    if not plan.downloads:
        return SUMMARY_NO_DOWNLOADS
    if plan.free_disk_gb is None:
        return SUMMARY_DOWNLOADS_NEEDED.format(needed=plan.needed_gb)
    return SUMMARY_DOWNLOADS_TOTAL.format(needed=plan.needed_gb, free=plan.free_disk_gb)


class DonePage(SetupPage):
    """The last page: the downloads run, and the teacher can pick documents."""

    page_id = SetupPageId.DONE

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(DONE_TITLE)
        self._speed: SpeedPanel | None = None
        layout = self._content()
        text = QLabel(DONE_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(_heading(DONE_DOWNLOADS_TITLE))
        # One bar for everything that is downloaded, above the line of each download.
        self._overall = QProgressBar()
        self._overall.setRange(0, _BAR_STEPS)
        self._overall.setVisible(False)
        layout.addWidget(self._overall)
        self._rows = _DownloadRows()
        self._rows.changed.connect(self._refresh)
        layout.addWidget(self._rows)
        self._goes_on = _note(DONE_OLLAMA_GOES_ON)
        self._goes_on.setVisible(False)
        layout.addWidget(self._goes_on)
        layout.addWidget(_heading(DONE_SPEED_TITLE))
        self._speed_waits = _note(DONE_SPEED_WAITING)
        self._speed_waits.setVisible(False)
        layout.addWidget(self._speed_waits)
        self._speed_holder = QVBoxLayout()
        layout.addLayout(self._speed_holder)
        layout.addStretch(1)
        self._select = QPushButton(DONE_SELECT)
        self._select.setToolTip(DONE_SELECT_TOOLTIP)
        self._select.clicked.connect(self._on_select)
        row = QHBoxLayout()
        row.addWidget(self._select)
        row.addStretch(1)
        layout.addLayout(row)
        self._follow_downloads()

    def initializePage(self) -> None:  # Qt override
        """List what is being downloaded and let the teacher test the speed."""
        downloads = self.assistant().downloads()
        self._rows.set_downloads(downloads, self.assistant().answers().language)
        self._goes_on.setVisible(
            any(download.kind is DownloadKind.OLLAMA_MODEL for download in downloads)
        )
        self._build_speed()
        self._refresh()

    def _refresh(self) -> None:
        """Let the title, the bar and the speed note follow the rows."""
        rows = self._rows
        if rows.failed_any():
            self.setTitle(DONE_TITLE_PROBLEMS)
        else:
            self.setTitle(DONE_TITLE_RUNNING if rows.pending() else DONE_TITLE)
        self._overall.setVisible(rows.total() > 0)
        self._overall.setValue(round(rows.fraction() * _BAR_STEPS))
        self._overall.setFormat(DONE_OVERALL.format(done=rows.finished(), total=rows.total()))
        self._speed_waits.setVisible(rows.pending_models())

    def on_packages_changed(self) -> None:
        """A package arrived: the speed test may have a model it did not have before."""
        if self._speed is not None:
            self._speed.refresh()

    def shutdown(self) -> None:
        """Stop a running speed test."""
        if self._speed is not None:
            self._speed.shutdown()

    def _follow_downloads(self) -> None:
        """Follow the install round and the pull manager, so every row says how far it is."""
        round_ = self.assistant().install_round()
        round_.item_started.connect(self._rows.started)
        round_.item_done.connect(self._rows.done)
        round_.failed.connect(self._rows.failed)
        round_.pull_progress.connect(self._rows.pull_progress)
        manager = self._context.pull_manager
        if manager is None:
            return
        manager.started.connect(self._rows.started)
        manager.progress.connect(self._rows.pull_progress)
        manager.finished.connect(self._rows.done)
        manager.error.connect(self._rows.error)
        manager.finished.connect(self._on_pull_finished)

    def _on_pull_finished(self, _model: str) -> None:
        """The queue ran empty: the speed test can measure the models that arrived."""
        manager = self._context.pull_manager
        if manager is not None and manager.queued:
            return
        if self._speed is not None:
            self._speed.refresh()

    def _build_speed(self) -> None:
        """Build the speed test once, on the settings the setup plan leaves behind."""
        if self._speed is not None:
            self._speed.refresh()
            return
        selector = HardwareProfileSelector(
            app_service=self._context.app_service,
            setting=str(self._settings().get("hardware_profile", "auto")),
            ollama_url=self._ollama_url(),
            parent=self,
        )
        # The panel only reads the resolution; the selector is in no layout of this page and
        # would otherwise sit in its top-left corner, over the text.
        selector.hide()
        self._speed = SpeedPanel(
            app_service=self._context.app_service,
            settings_provider=self._settings,
            profile_selector=selector,
            ollama_url=self._ollama_url(),
            parent=self,
        )
        self._speed_holder.addWidget(self._speed)

    def _settings(self) -> dict:
        """Return the settings the setup plan leaves behind, as the speed test reads them."""
        plan = self.assistant().plan()
        values = dict(self._context.settings)
        if plan is not None:
            values.update(plan.settings)
            values.update(plan.panel)
        return values

    def _ollama_url(self) -> str:
        """Return the local AI server address the setup plan leaves behind."""
        return str(self._settings().get("ollama_url", ""))

    def _on_select(self) -> None:
        """Finish, then let the main window choose the folder."""
        self.assistant().select_documents()


class _DownloadRows(QWidget):
    """One line per download — what it is for, its name, its size, how far it is.

    Following the install round and the pull manager.  The round starts before this page is
    shown, so the state is kept here and drawn when the page is entered: a row that already began
    says so.
    """

    #: A row's state changed: the page's title, bar and notes follow.
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(3, 1)
        self._grid.setHorizontalSpacing(14)
        self._labels: dict[str, QLabel] = {}
        self._state: dict[str, str] = {}
        self._failed: set[str] = set()  # kept like _state: a failure may precede the row
        self._fraction: dict[str, float] = {}
        self._names: list[str] = []
        self._kinds: dict[str, DownloadKind] = {}
        self._pulling = ""  # the Ollama model that is being pulled now

    def set_downloads(self, downloads: Sequence[PlannedDownload], language: str = "") -> None:
        """Show one line per download, each as far as it has come."""
        self._clear()
        if not downloads:
            self._grid.addWidget(_note(DONE_NOTHING), 0, 0, 1, 4)
            return
        for line, download in enumerate(downloads):
            name = download.name
            purpose, what = _download_words(download, language)
            self._grid.addWidget(QLabel(purpose), line, 0)
            what_label = QLabel(what)
            what_label.setToolTip(name)
            self._grid.addWidget(what_label, line, 1)
            self._grid.addWidget(_note(size_text(download)), line, 2)
            status = _note(self._state.get(name, DONE_WAITING))
            self._grid.addWidget(status, line, 3)
            self._labels[name] = status
            self._names.append(name)
            self._kinds[name] = download.kind
        self.changed.emit()

    def started(self, item: str) -> None:
        """The round began *item*."""
        if _item_name(item) == item:  # no pip / spaCy prefix: an Ollama pull
            self._pulling = item
        self._set(item, DONE_RUNNING)

    def done(self, item: str) -> None:
        """The round finished *item*."""
        if item == self._pulling:
            self._pulling = ""
        self._fraction.pop(_item_name(item), None)
        self._set(item, DONE_DONE)

    def failed(self, item: str, message: str) -> None:
        """The round could not install *item*."""
        self._set(item, DONE_FAILED.format(message=message), failed=True)

    def error(self, message: str) -> None:
        """A pull failed; the manager names no model, so every running Ollama row says why.

        Only Ollama pulls come through the manager: the reader's and the packages' rows are the
        install round's, which reports its own failures by name.
        """
        for name, status in self._labels.items():
            if self._kinds.get(name) is not DownloadKind.OLLAMA_MODEL:
                continue
            if self._state.get(name) == DONE_RUNNING or name in self._fraction:
                self._state[name] = DONE_FAILED.format(message=message)
                self._failed.add(name)
                self._fraction.pop(name, None)
                status.setText(self._state[name])
        self.changed.emit()

    def pull_progress(self, status: str, done: float, total: float) -> None:
        """The running pull reports how far it is, in bytes.

        The signal carries a status text ("pulling sha256:…"), not the model, so the row is
        the one :meth:`started` named last.
        """
        # The built-in reader's download reports through the same signal, under its own name.
        name = READER_NAME if status == READER_PROGRESS_STATUS else self._pulling
        if name not in self._labels or not total:
            return
        self._fraction[name] = min(done / total, 1.0)
        self._state[name] = (
            DONE_PROGRESS_MB.format(done=done / _MB, total=total / _MB)
            if total < _GB / 10
            else DONE_PROGRESS.format(done=done / _GB, total=total / _GB)
        )
        self._labels[name].setText(self._state[name])
        self.changed.emit()

    # ── what the rows add up to ──────────────────────────────────────────────

    def total(self) -> int:
        """Return how many downloads there are."""
        return len(self._names)

    def finished(self) -> int:
        """Return how many downloads are over, done or failed."""
        return sum(1 for name in self._names if self._over(name))

    def pending(self) -> bool:
        """Return whether anything is still waiting or running."""
        return any(not self._over(name) for name in self._names)

    def failed_any(self) -> bool:
        """Return whether a download failed."""
        return any(name in self._failed for name in self._names)

    def pending_models(self) -> bool:
        """Return whether an Ollama model is still to arrive (the speed test needs it)."""
        return any(
            self._kinds[name] is DownloadKind.OLLAMA_MODEL and not self._over(name)
            for name in self._names
        )

    def fraction(self) -> float:
        """Return how far the downloads as a whole are, between 0 and 1."""
        if not self._names:
            return 0.0
        done = sum(
            1.0 if self._over(name) else self._fraction.get(name, 0.0) for name in self._names
        )
        return done / len(self._names)

    def _over(self, name: str) -> bool:
        """Return whether the download *name* is over: done or failed."""
        return name in self._failed or self._state.get(name, DONE_WAITING) == DONE_DONE

    def _set(self, item: str, text: str, *, failed: bool = False) -> None:
        """Record *text* for *item* and show it when the row is there."""
        name = _item_name(item)
        self._state[name] = text
        if failed:
            self._failed.add(name)
        else:
            self._failed.discard(name)
        label = self._labels.get(name)
        if label is not None:
            label.setText(text)
            self.changed.emit()

    def _clear(self) -> None:
        """Drop every line, so a new plan starts from an empty grid."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._labels.clear()
        self._names.clear()
        self._kinds.clear()


def _download_words(download: PlannedDownload, language: str) -> tuple[str, str]:
    """Return what a download is for and what it is called, in words a teacher knows."""
    kind = download.kind
    if kind is DownloadKind.READER_MODEL:
        return DONE_PURPOSE_READER, DONE_NAME_READER
    if kind is DownloadKind.PAGE_MODELS:
        return DONE_PURPOSE_PAGE_MODELS, DONE_NAME_PAGE_MODELS
    if kind is DownloadKind.SPACY_MODEL:
        if download.extra:
            return DONE_PURPOSE_NAMES, name_model_label(download.name)
        return DONE_PURPOSE_NAMES, language or download.name
    if kind is DownloadKind.OLLAMA_MODEL:
        purpose = DONE_PURPOSE_READING if download.stage == STAGE_OCR else DONE_PURPOSE_PROOFREADING
        return purpose, short_model_name(download.name)
    purpose = DONE_PURPOSE_NAMES if download.stage == STAGE_ANONYMIZATION else DONE_PURPOSE_ADDON
    return purpose, download.name


def _item_name(item: str) -> str:
    """Return the download name behind an install round item key."""
    if item == PAGE_MODELS_ITEM:
        # The round says ``page_models:all``; the setup plan's row is named by the add-on id.
        return PAGE_MODELS_ADDON_ID
    for prefix in (PACKAGE_PREFIX, SPACY_PREFIX, READER_PREFIX):
        if item.startswith(prefix):
            return item[len(prefix) :]
    return item


PAGE_CLASSES: dict[SetupPageId, type[SetupPage]] = {
    SetupPageId.WELCOME: WelcomePage,
    SetupPageId.CLASSES: ClassesPage,
    SetupPageId.LOCAL_AI: LocalAiPage,
    SetupPageId.PROOFREADING: ProofreadingPage,
    SetupPageId.PRIVACY: PrivacyPage,
    SetupPageId.SUMMARY: SummaryPage,
    SetupPageId.DONE: DonePage,
}
