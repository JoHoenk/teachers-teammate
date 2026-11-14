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

from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...application.correction_options import review_has_own_model
from ...application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    PlannedStep,
    ProofreadingPlace,
    SetupAnswers,
    SetupPlan,
)
from ...config import DEFAULTS
from ...domain.data_location import (
    STAGE_CORRECTION,
    DataLocation,
    DataRegion,
    StageLocation,
)
from ...domain.language import LANGUAGES
from ...domain.model_catalog import DiskVerdict, Suitability
from ...infrastructure.ollama_utils import is_local_ollama_url
from .. import _user_text as txt
from .._background import LatestTask
from .._data_flow_summary import short_place
from .._hardware_profile_selector import HardwareProfileSelector
from .._install_round import PACKAGE_PREFIX, READER_PREFIX, READER_PROGRESS_STATUS
from .._model_guidance import PROVIDER_PREFIX, SPACY_PREFIX
from .._ollama_status_line import OllamaStatusLine
from ..settings._ai_service_section import PROOFREADING, AiServiceSection
from ..settings._server_search_dialog import ServerSearchDialog
from ..settings._speed_panel import SpeedPanel
from ._context import SetupContext
from ._flow import ORDER, SetupPageId, next_page
from ._summary import SummaryRows, summary_rows

if TYPE_CHECKING:
    from ._assistant import SetupAssistant

# ── Welcome ───────────────────────────────────────────────────────────────────

WELCOME_TITLE = "Welcome to Teacher's Teammate"
WELCOME_TEXT = (
    "The app reads your students' handwritten work, can proofread it and review its content, "
    "and writes the results into Word documents.\n\n"
    "This assistant sets it up for this computer. It takes about five minutes; the AI models "
    "then download in the background (a few gigabytes)."
)
WELCOME_POINTS = (
    "• Pages are always read on this computer, or on your school's own server — never online.\n"
    "• You decide whether proofreading may use an online service."
)
WELCOME_FOOTNOTE = (
    f"You can run it again under {txt.WHERE_SETUP}, and change everything under Settings."
)

# ── Your classes ──────────────────────────────────────────────────────────────

CLASSES_TITLE = "Your classes"
CLASSES_LANGUAGE = "Language your students write in:"
CLASSES_LANGUAGE_NOTE = (
    "Proofreading and name detection follow it. You can change it for every batch."
)
CLASSES_QUESTION = "What do they hand in?"
CLASSES_HANDWRITTEN = (
    "Handwritten or scanned pages (photos, scans, PDFs), also mixed with typed work"
)
CLASSES_HANDWRITTEN_NOTE = "The text is read by an AI on this computer."
CLASSES_TYPED = "Only typed texts (text files, PDFs that contain their text)"
CLASSES_TYPED_NOTE = (
    "Nothing has to be read, so no reading model is downloaded.\n"
    "Word files can't be read yet: save them as PDF."
)

# ── Local AI ──────────────────────────────────────────────────────────────────

LOCAL_AI_TITLE = "The AI on this computer"
LOCAL_AI_INTRO = (
    "Teacher's Teammate uses Ollama, a free program that runs AI models on your own computer."
)
LOCAL_AI_QUESTION = "Where does Ollama run?"
LOCAL_AI_HERE = "On this computer"
LOCAL_AI_SERVER = "On a server at my school"
LOCAL_AI_FIND = "Find…"
LOCAL_AI_FIND_TOOLTIP = "Look for an Ollama server on this computer or on your network."
LOCAL_AI_PROFILE_NOTE = "The suggested models follow this."

# ── Proofreading ──────────────────────────────────────────────────────────────

PROOFREADING_TITLE = "Proofreading"
PROOFREADING_INTRO = (
    "The app can proofread the recognised text and explain each mistake. Where should that AI run?"
)
PROOFREADING_LOCAL = "With the AI on this computer — free; the text never leaves this computer"
PROOFREADING_ONLINE = (
    "At an online service — faster on a weak computer; the text is sent over the internet"
)
PROOFREADING_OFF = "Not now — only read the text"
PROOFREADING_ONLINE_NOTE = "You need an account with the service, and it may charge for use."
PROOFREADING_REVIEW_KEPT = "Content review keeps its own model (Settings → AI models)."
PROOFREADING_INSTALL_NOW = "Install now"
PROOFREADING_INSTALL_TOOLTIP = "Download the package this service needs, then check the key."
SUGGESTION = "Suggested: {model} · {fit} · {size}"
SUGGESTION_DOWNLOADED = "Suggested: {model} · {fit} · already downloaded"
SUGGESTION_CHECKING = "Checking this computer…"
SUGGESTION_UNKNOWN = "The model for this computer is chosen when you press Set up."
FIT_WORDS: dict[Suitability, str] = {
    Suitability.BEST: "fits this computer well",
    Suitability.GOOD: "fits this computer well",
    Suitability.WORKS: "works on this computer",
    Suitability.SLOW: "runs slowly here: it doesn't fit the graphics card",
}
FIT_VERY_SLOW = "runs very slowly here; an online service is much faster"

# ── Privacy ───────────────────────────────────────────────────────────────────

PRIVACY_TITLE = "Names and personal information"
PRIVACY_SENT = "Proofreading at {service} sends your students' text over the internet."
PRIVACY_EXPLAIN = (
    "Names and other personal details are replaced on this computer before anything is sent."
)
PRIVACY_NEEDS_DETECTION = (
    "Needs name detection for {language}, which is added to the downloads (small)."
)
PRIVACY_HAS_DETECTION = "Name detection for {language} is installed."
PRIVACY_ALREADY_ON = "Already on."
PRIVACY_SCHOOL_NOTE = (
    "Check with your school that pupils' texts may be sent to this service. Before the first "
    "run that sends text, the app shows what is sent and asks you once more."
)

# ── Summary ───────────────────────────────────────────────────────────────────

SUMMARY_TITLE = "Ready to set up"
SUMMARY_WORKING = "Working out what is needed…"
SUMMARY_FAILED = (
    "The setup plan could not be worked out. You can set everything up under Settings → AI models."
)
SUMMARY_INSTALLING = "A package is still being installed. Set up is possible once it has finished."
SUMMARY_LATER = "You can change all of this later under Settings → AI models."
SUMMARY_NO_DOWNLOADS = "Nothing to download."
SUMMARY_DOWNLOADS_TOTAL = "Downloads: about {needed:.1f} GB · {free:.0f} GB free on this computer"
SUMMARY_DOWNLOADS_NEEDED = "Downloads: about {needed:.1f} GB"
SUMMARY_OLLAMA_STOPPED = (
    "⚠ Ollama isn't running yet, so its models can't be downloaded now. "
    "The main window offers them as soon as it runs."
)
SUMMARY_DISK_SHORT = (
    "Only {free:.0f} GB is free where Ollama keeps its models, and the downloads need about "
    "{needed:.1f} GB. Free some space, or untick a download."
)

# ── Done ──────────────────────────────────────────────────────────────────────

DONE_TITLE = "All set"
DONE_TEXT = "The downloads run in the background; the main window shows how far they are."
DONE_DOWNLOADS_TITLE = "Downloads"
DONE_SPEED_TITLE = "Speed on this computer"
DONE_OLLAMA_GOES_ON = (
    "Ollama downloads go on after you close this window; the main window shows them."
)
DONE_WAITING = "waiting"
DONE_RUNNING = "↓ downloading…"
DONE_DONE = "✓ done"
DONE_FAILED = "✗ {message}"
DONE_PROGRESS = "↓ {done:.1f} of {total:.1f} GB"
DONE_PROGRESS_MB = "↓ {done:.0f} of {total:.0f} MB"
READER_NAME = "ppocr"  # the name of the reader's download row (``PlannedDownload.name``)
DONE_SELECT = "Select documents…"
DONE_SELECT_TOOLTIP = "Close this window and choose the folder with your students' work."

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

    def assistant(self) -> SetupAssistant:
        """Return the wizard this page belongs to."""
        return self._assistant

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
        layout = QVBoxLayout(self)
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
        layout = QVBoxLayout(self)

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

        layout = QVBoxLayout(self)
        intro = QLabel(LOCAL_AI_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(8)
        layout.addWidget(_heading(LOCAL_AI_QUESTION))

        saved = str(context.settings.get("ollama_url") or DEFAULTS["ollama_url"])
        local = is_local_ollama_url(saved, include_wsl_host=True)
        self._here = QRadioButton(LOCAL_AI_HERE)
        self._server = QRadioButton(LOCAL_AI_SERVER)
        self._here.setChecked(local)
        self._server.setChecked(not local)
        layout.addWidget(self._here)

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
        layout.addWidget(self._server)

        self._status_holder = QWidget()
        self._status_layout = QVBoxLayout(self._status_holder)
        self._status_layout.setContentsMargins(22, 4, 0, 0)
        layout.addWidget(self._status_holder)

        self._profile_holder = QWidget()
        self._profile_layout = QVBoxLayout(self._profile_holder)
        self._profile_layout.setContentsMargins(0, 8, 0, 0)
        layout.addWidget(self._profile_holder)
        layout.addWidget(_note(LOCAL_AI_PROFILE_NOTE))
        layout.addStretch(1)

        self._here.toggled.connect(self._on_place_changed)
        self._url.editingFinished.connect(self._on_url_edited)

    # ── answers ───────────────────────────────────────────────────────────────

    def ollama_url(self) -> str:
        """Return the address Ollama runs at, as the page shows it."""
        text = self._url.text().strip()
        if self._here.isChecked() and not is_local_ollama_url(text, include_wsl_host=True):
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
        if not on_server and not is_local_ollama_url(
            self._url.text().strip(), include_wsl_host=True
        ):
            self._url.setText(DEFAULTS["ollama_url"])
        self._follow_address()

    def _on_url_edited(self) -> None:
        self._follow_address()

    def _on_use_server(self, url: str) -> None:
        """Take the server the status line found, because the teacher pressed "Use it"."""
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

        layout = QVBoxLayout(self)
        intro = QLabel(PROOFREADING_INTRO)
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addSpacing(8)

        saved = str(context.settings.get("correction_provider") or DEFAULTS["correction_provider"])
        self._local = QRadioButton(PROOFREADING_LOCAL)
        self._online = QRadioButton(PROOFREADING_ONLINE)
        self._off = QRadioButton(PROOFREADING_OFF)
        # "Not now" sits in an indented row, so Qt would not make it exclusive with the others.
        self._places = QButtonGroup(self)
        for button in (self._local, self._online, self._off):
            self._places.addButton(button)
        self._local.setChecked(saved == "ollama")
        self._online.setChecked(saved not in ("ollama", ""))
        layout.addWidget(self._local)
        self._suggestion = _note(SUGGESTION_CHECKING)
        layout.addWidget(_indent(self._suggestion))
        layout.addWidget(self._online)

        self._online_holder = QWidget()
        self._online_layout = QVBoxLayout(self._online_holder)
        self._online_layout.setContentsMargins(22, 0, 0, 0)
        layout.addWidget(self._online_holder)
        layout.addWidget(_indent(_note(PROOFREADING_ONLINE_NOTE)))

        self._off_holder = _indent(self._off)
        layout.addWidget(self._off_holder)

        self._review_note = _note(PROOFREADING_REVIEW_KEPT)
        self._review_note.setVisible(False)
        layout.addWidget(self._review_note)
        layout.addStretch(1)

        self._local.toggled.connect(self._on_place_changed)
        self._online.toggled.connect(self._on_place_changed)
        self._off.toggled.connect(self._on_place_changed)

    # ── answers ───────────────────────────────────────────────────────────────

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
        self._off_holder.setVisible(handwritten)
        if not handwritten and self._off.isChecked():
            self._local.setChecked(True)
        self._review_note.setVisible(self._review_kept())
        self._on_place_changed()
        self._ask_for_suggestion()

    def _review_kept(self) -> bool:
        """Return whether the saved content review has its own model, which Set up leaves alone."""
        return review_has_own_model(
            self._context.settings, self._context.app_service.effective_model
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
        service = self._context.app_service
        packages = [
            pip_name
            for entry in preselect
            if entry.startswith(PROVIDER_PREFIX)
            for pip_name in [service.missing_provider_package(entry[len(PROVIDER_PREFIX) :])]
            if pip_name
        ]
        if packages:
            self.assistant().install_round().start(packages=packages)

    def _ask_for_suggestion(self) -> None:
        """Ask the setup plan which local model this computer would get, and show how it fits."""
        service = self._context.app_service
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
        self._task.submit(lambda: service.plan_setup(local, current), self._on_suggestion)

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
    """Whether names are replaced before the text goes to an online service."""

    page_id = SetupPageId.PRIVACY

    def __init__(self, context: SetupContext, assistant: SetupAssistant) -> None:
        super().__init__(context, assistant)
        self.setTitle(PRIVACY_TITLE)
        layout = QVBoxLayout(self)
        self._sent = QLabel()
        self._sent.setWordWrap(True)
        layout.addWidget(self._sent)
        layout.addSpacing(8)

        self._remove = QCheckBox(txt.REMOVE_PERSONAL_INFO)
        layout.addWidget(self._remove)
        layout.addWidget(_indent(_note(PRIVACY_EXPLAIN)))
        self._detection = _note("")
        layout.addWidget(_indent(self._detection))
        self._already = _note(PRIVACY_ALREADY_ON)
        self._already.setVisible(False)
        layout.addWidget(_indent(self._already))
        layout.addSpacing(8)
        school = _note(PRIVACY_SCHOOL_NOTE)
        layout.addWidget(school)
        layout.addStretch(1)

    def remove_names(self) -> bool:
        """Return whether names should be replaced before the text is sent."""
        return self._remove.isChecked()

    def initializePage(self) -> None:  # Qt override
        """Name the service, tick the box in advance, and say what name detection needs."""
        answers = self.assistant().answers()
        provider = answers.online_choice[0]
        info = self._context.app_service.get_provider_info(provider)
        service = str(info.get("display_name") or provider)
        self._sent.setText(PRIVACY_SENT.format(service=service))

        already_on = bool(self._context.settings.get("anonymization_enabled"))
        self._remove.setChecked(True)
        self._remove.setEnabled(not already_on)
        self._already.setVisible(already_on)

        detection = self._context.app_service.anonymizer_available(answers.language)
        self._detection.setText(
            PRIVACY_HAS_DETECTION.format(language=answers.language)
            if detection
            else PRIVACY_NEEDS_DETECTION.format(language=answers.language)
        )


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
        layout = QVBoxLayout(self)
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
        service = self._context.app_service
        answers = self.assistant().answers()
        current = dict(self._context.settings)
        self._task.submit(lambda: service.plan_setup(answers, current), self._on_plan)

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
            location = (
                DataLocation.THIS_COMPUTER
                if is_local_ollama_url(url)
                else DataLocation.OTHER_COMPUTER
            )
            return short_place(StageLocation(location=location, service="Ollama"))
        service = self._context.app_service
        info = service.get_provider_info(provider)
        return short_place(
            StageLocation(
                location=DataLocation.ONLINE,
                service=str(info.get("display_name") or provider),
                region=DataRegion(service.get_provider_data_region(provider)),
            )
        )

    def _key_missing(self, plan: SetupPlan) -> set[str]:
        """Return the online providers the setup plan uses that have no key yet."""
        service = self._context.app_service
        return {
            step.provider
            for step in plan.steps
            if step.provider
            and step.provider != "ollama"
            and service.provider_key_missing(step.provider)
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
        layout = QVBoxLayout(self)
        text = QLabel(DONE_TEXT)
        text.setWordWrap(True)
        layout.addWidget(text)
        layout.addWidget(_heading(DONE_DOWNLOADS_TITLE))
        self._rows = _DownloadRows()
        layout.addWidget(self._rows)
        self._goes_on = _note(DONE_OLLAMA_GOES_ON)
        self._goes_on.setVisible(False)
        layout.addWidget(self._goes_on)
        layout.addWidget(_heading(DONE_SPEED_TITLE))
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
        self._rows.set_downloads(downloads)
        self._goes_on.setVisible(
            any(download.kind is DownloadKind.OLLAMA_MODEL for download in downloads)
        )
        self._build_speed()

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
    """One line per download, following the install round and the pull manager.

    The round starts before this page is shown, so the state is kept here and drawn when the
    page is entered: a row that already began says so.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setColumnStretch(1, 1)
        self._labels: dict[str, QLabel] = {}
        self._state: dict[str, str] = {}
        self._pulling = ""  # the Ollama model that is being pulled now

    def set_downloads(self, downloads: Sequence[PlannedDownload]) -> None:
        """Show one line per download, each as far as it has come."""
        self._clear()
        for line, download in enumerate(downloads):
            name = download.name
            label = QLabel(name)
            status = _note(self._state.get(name, DONE_WAITING))
            self._grid.addWidget(label, line, 0)
            self._grid.addWidget(status, line, 1)
            self._labels[name] = status

    def started(self, item: str) -> None:
        """The round began *item*."""
        if _item_name(item) == item:  # no pip / spaCy prefix: an Ollama pull
            self._pulling = item
        self._set(item, DONE_RUNNING)

    def done(self, item: str) -> None:
        """The round finished *item*."""
        if item == self._pulling:
            self._pulling = ""
        self._set(item, DONE_DONE)

    def failed(self, item: str, message: str) -> None:
        """The round could not install *item*."""
        self._set(item, DONE_FAILED.format(message=message))

    def error(self, message: str) -> None:
        """A pull failed; the manager names no model, so every running row says why."""
        for name, status in self._labels.items():
            if self._state.get(name) == DONE_RUNNING:
                self._state[name] = DONE_FAILED.format(message=message)
                status.setText(self._state[name])

    def pull_progress(self, status: str, done: float, total: float) -> None:
        """The running pull reports how far it is, in bytes.

        The signal carries a status text ("pulling sha256:…"), not the model, so the row is
        the one :meth:`started` named last.
        """
        # The built-in reader's download reports through the same signal, under its own name.
        name = READER_NAME if status == READER_PROGRESS_STATUS else self._pulling
        if name not in self._labels or not total:
            return
        self._state[name] = (
            DONE_PROGRESS_MB.format(done=done / _MB, total=total / _MB)
            if total < _GB / 10
            else DONE_PROGRESS.format(done=done / _GB, total=total / _GB)
        )
        self._labels[name].setText(self._state[name])

    def _set(self, item: str, text: str) -> None:
        """Record *text* for *item* and show it when the row is there."""
        name = _item_name(item)
        self._state[name] = text
        label = self._labels.get(name)
        if label is not None:
            label.setText(text)

    def _clear(self) -> None:
        """Drop every line, so a new plan starts from an empty grid."""
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.deleteLater()
        self._labels.clear()


def _item_name(item: str) -> str:
    """Return the download name behind an install round item key."""
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
