"""The Setup Assistant: a wizard that asks what only the teacher knows, then sets up.

The wizard owns the three things the pages share — the API key drafts, the install round and the
plan — and the rules for leaving: Set up stores the keys and hands the result to the main window,
any other way out puts the keys back.  The pages themselves are in :mod:`._pages` and the order
in :mod:`._flow`; nothing here decides what to ask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QMessageBox, QWidget, QWizard

from ...application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    ProofreadingPlace,
    SetupAnswers,
    SetupPlan,
)
from .._install_round import InstallRound
from ..settings._credentials import ApiKeyDrafts, discard_key_drafts, store_key_drafts
from ._context import SetupContext, SetupResult
from ._flow import ORDER, SetupPageId
from ._pages import (
    PAGE_CLASSES,
    ClassesPage,
    LocalAiPage,
    PrivacyPage,
    ProofreadingPage,
    SetupPage,
    SummaryPage,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

TITLE = "Set up Teacher's Teammate"
SET_UP = "Set up"
CANNOT_CLOSE_TITLE = "Please wait"
CLOSE_BLOCKED = (
    "An installation is still running. Please wait until it has finished — the new package "
    "must be set up before the app can use it."
)

#: The page class a caller asks for; :meth:`SetupAssistant._page` returns that type.
_PageT = TypeVar("_PageT", bound=SetupPage)


class SetupAssistant(QWizard):
    """The assistant's window; see the module docstring."""

    #: Set up was pressed: the settings to apply, already stored.
    committed = Signal(object)  # SetupResult

    def __init__(self, context: SetupContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self._committed = False
        self._documents_wanted = False
        self._next_text: str | None = None
        self._drafts = ApiKeyDrafts(context.app_service, parent=self)
        self._round = InstallRound(context.app_service, context.pull_manager, self)
        self._pages: dict[SetupPageId, SetupPage] = {}

        self.setWindowTitle(TITLE)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.NoCancelButtonOnLastPage, True)
        self.setMinimumSize(640, 480)

        for index, page_id in enumerate(ORDER):
            page = PAGE_CLASSES[page_id](context, self)
            self._pages[page_id] = page
            self.setPage(index, page)
        self.setStartId(ORDER.index(SetupPageId.WELCOME))

        self.currentIdChanged.connect(self._sync_next_button)
        self._round.finished.connect(self._on_round_finished)

    # ── what the pages ask ────────────────────────────────────────────────────

    def answers(self) -> SetupAnswers:
        """Return the answers as the pages show them now."""
        classes = self._page(SetupPageId.CLASSES, ClassesPage)
        local = self._page(SetupPageId.LOCAL_AI, LocalAiPage)
        proofreading = self._page(SetupPageId.PROOFREADING, ProofreadingPage)
        privacy = self._page(SetupPageId.PRIVACY, PrivacyPage)
        hand_in = classes.hand_in()
        place = proofreading.place()
        return SetupAnswers(
            language=classes.language(),
            hand_in=hand_in,
            proofreading=place,
            ollama_url=local.ollama_url(),
            hardware_profile=local.hardware_profile(),
            local_ai_shown=hand_in is HandIn.HANDWRITTEN or place is ProofreadingPlace.LOCAL,
            online_choice=proofreading.online_choice(),
            remove_names=privacy.remove_names(),
        )

    def _page(self, page_id: SetupPageId, kind: type[_PageT]) -> _PageT:
        """Return the page of *page_id*, typed as *kind* (the wizard built it from PAGE_CLASSES)."""
        page = self._pages[page_id]
        assert isinstance(page, kind)  # the class comes from PAGE_CLASSES
        return page

    def key_drafts(self) -> ApiKeyDrafts:
        """Return the keys typed here, shared by every page that asks for one."""
        return self._drafts

    def install_round(self) -> InstallRound:
        """Return the round that installs what a page needs before it can go on."""
        return self._round

    def plan(self) -> SetupPlan | None:
        """Return the setup plan the summary page worked out, or ``None`` while it is on its way."""
        return self._page(SetupPageId.SUMMARY, SummaryPage).plan()

    def downloads(self) -> list[PlannedDownload]:
        """Return the downloads the teacher left ticked on the summary page."""
        return self._page(SetupPageId.SUMMARY, SummaryPage).downloads()

    # ── Set up ────────────────────────────────────────────────────────────────

    def commit(self, downloads: Sequence[PlannedDownload]) -> bool:
        """Store the keys, hand the result to the main window and start the downloads."""
        plan = self.plan()
        if plan is None:
            return False
        store_key_drafts(self._drafts, self)
        self._committed = True
        self.committed.emit(SetupResult(settings=dict(plan.settings), panel=dict(plan.panel)))
        self._start_downloads(downloads)
        return True

    def _start_downloads(self, downloads: Sequence[PlannedDownload]) -> None:
        """Hand the ticked downloads to the install round, one list per kind."""
        self._round.start(
            packages=[item.name for item in downloads if item.kind is DownloadKind.PACKAGE],
            spacy_models=[item.name for item in downloads if item.kind is DownloadKind.SPACY_MODEL],
            reader=any(item.kind is DownloadKind.READER_MODEL for item in downloads),
            ollama_models=[
                item.name
                for item in downloads
                if item.kind is DownloadKind.OLLAMA_MODEL and item.possible
            ],
            ollama_url=self.answers().ollama_url,
        )

    def _on_round_finished(self, _installed: bool) -> None:
        """A package may have arrived: every page that lists models asks again."""
        for page in self._pages.values():
            page.on_packages_changed()

    # ── leaving ───────────────────────────────────────────────────────────────

    def select_documents(self) -> None:
        """Finish, and ask the main window to choose the folder once this window is gone."""
        self._documents_wanted = True
        self.accept()

    def is_committed(self) -> bool:
        """Return whether Set up was pressed, however the window was closed afterwards."""
        return self._committed

    def documents_wanted(self) -> bool:
        """Return whether the teacher left through "Select documents…"."""
        return self._documents_wanted

    def done(self, result: int) -> None:
        """Refuse while an install runs; otherwise stop the pages and put unused keys back."""
        if self._round.busy():
            QMessageBox.information(self, CANNOT_CLOSE_TITLE, CLOSE_BLOCKED)
            return
        for page in self._pages.values():
            page.shutdown()
        self._round.detach()
        if not self._committed:
            discard_key_drafts(self._drafts, self._context.app_service)
        super().done(result)

    # ── the Next button ───────────────────────────────────────────────────────

    def showEvent(self, event: QShowEvent) -> None:  # Qt override
        """Remember the wizard's own Next wording before the first page can change it."""
        self._remember_next_text()
        super().showEvent(event)

    def _remember_next_text(self) -> None:
        """Keep the wizard's own Next wording, so the commit page can give it back."""
        if self._next_text is not None:
            return
        button = self.button(QWizard.WizardButton.NextButton)
        if button is not None:
            self._next_text = button.text()

    def _sync_next_button(self, page_id: int) -> None:
        """Read "Set up" on the commit page and the wizard's own wording everywhere else."""
        self._remember_next_text()
        button = self.button(QWizard.WizardButton.NextButton)
        if button is None:
            return
        if page_id == ORDER.index(SetupPageId.SUMMARY):
            button.setText(SET_UP)
        elif self._next_text is not None:
            button.setText(self._next_text)
