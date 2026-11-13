"""GUI tests for the assistant's window: Set up, Cancel, and the rules for leaving."""
# pylint: disable=W0212  # protected-access — the tests drive the wizard's widgets directly

from __future__ import annotations

import os
from typing import Any

from PySide6.QtWidgets import QDialog, QMessageBox, QWizard
import pytest

from teachers_teammate.application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    ProofreadingPlace,
    SetupPlan,
)
from teachers_teammate.config import DEFAULTS
from teachers_teammate.gui.setup import SetupAssistant, SetupContext, SetupResult
from teachers_teammate.gui.setup._assistant import CLOSE_BLOCKED, SET_UP
from teachers_teammate.gui.setup._flow import ORDER, SetupPageId

from ._settings_stubs import FakeKeyring, SetupAppService


@pytest.fixture(name="keyring")
def _keyring(monkeypatch) -> FakeKeyring:
    """Give the app a working in-memory keychain, so no test touches the real one."""
    from teachers_teammate.gui import _credential_store  # noqa: PLC0415

    store = FakeKeyring()
    monkeypatch.setattr(_credential_store, "_keyring", lambda: store)
    return store


@pytest.fixture(name="no_keys")
def _no_keys(monkeypatch) -> None:
    """Start every test without the keys the assistant may write."""
    monkeypatch.setattr(
        os,
        "environ",
        {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "MISTRAL_API_KEY")},
    )


def _context(service: SetupAppService, **settings: Any) -> SetupContext:
    """Return a context over *service* with the given settings keys overridden."""
    values: dict[str, Any] = dict(DEFAULTS)
    values.update(settings)
    return SetupContext(settings=values, app_service=service)


def _assistant(qtbot, context: SetupContext) -> SetupAssistant:
    """Build the assistant for *context* and register it with qtbot."""
    assistant = SetupAssistant(context)
    qtbot.addWidget(assistant)
    return assistant


def _plan(assistant: SetupAssistant, service: SetupAppService) -> SetupPlan:
    """Work the setup plan out for the assistant's answers and put it on the summary page."""
    answers = assistant.answers()
    plan = service.plan_setup(answers, dict(assistant._context.settings))
    assistant._pages[SetupPageId.SUMMARY]._on_plan(plan, None)
    return plan


class _ForgettingService(SetupAppService):
    """A service that remembers which provider's model list was thrown away."""

    def __init__(self) -> None:
        super().__init__()
        self.forgotten: list[str] = []

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        """Record the provider whose model list is no longer trusted."""
        _ = base_url
        self.forgotten.append(provider)


# ── the pages the wizard holds ────────────────────────────────────────────────


def test_the_wizard_holds_every_page_in_order(qtbot) -> None:
    """
    Given  the assistant
    When   it is built
    Then   every page sits at its own index and the welcome page is the start
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assert assistant.pageIds() == list(range(len(ORDER)))
    assert assistant.startId() == ORDER.index(SetupPageId.WELCOME)
    for index, page_id in enumerate(ORDER):
        assert assistant.page(index).page_id is page_id


def test_the_wizard_has_one_look_everywhere(qtbot) -> None:
    """
    Given  the assistant
    When   it is built
    Then   it uses the modern style on every platform, so the user guide describes one look
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assert assistant.wizardStyle() is QWizard.WizardStyle.ModernStyle


def test_the_next_button_reads_set_up_on_the_summary(qtbot) -> None:
    """
    Given  the assistant
    When   the summary page is reached
    Then   the Next button reads "Set up", because that is what it does
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assistant.show()
    summary = ORDER.index(SetupPageId.SUMMARY)
    for page_id in ORDER[:summary]:
        assistant.setCurrentId(ORDER.index(page_id))
    assistant.setCurrentId(summary)

    assert assistant.button(QWizard.WizardButton.NextButton).text() == SET_UP

    assistant.back()
    assert assistant.currentId() == ORDER.index(SetupPageId.PRIVACY)
    assert assistant.button(QWizard.WizardButton.NextButton).text() != SET_UP


def test_there_is_no_way_back_from_the_done_page(qtbot) -> None:
    """
    Given  the summary page, where Set up starts the downloads
    When   the wizard is asked about it
    Then   it is the commit page, so Qt takes Back away on the done page and Set up cannot run twice
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assert assistant._pages[SetupPageId.SUMMARY].isCommitPage()


def test_back_still_works_before_set_up(qtbot) -> None:
    """
    Given  the classes page
    When   Back is asked for
    Then   the welcome page is shown again, so the answers can be changed
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assistant.show()
    assistant.setCurrentId(ORDER.index(SetupPageId.CLASSES))
    assistant.back()
    assert assistant.currentId() == ORDER.index(SetupPageId.WELCOME)


# ── Set up ────────────────────────────────────────────────────────────────────


def test_set_up_hands_the_settings_and_the_panel_values_over(
    qtbot, keyring, no_keys, monkeypatch
) -> None:
    """
    Given  a plan for handwritten pages with proofreading on the local AI
    When   Set up is pressed
    Then   the main window is handed the Settings-window keys and the main-window ones
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    monkeypatch.setattr(assistant.install_round(), "start", lambda **_kw: True)
    received: list[SetupResult] = []
    assistant.committed.connect(received.append)
    plan = _plan(assistant, service)

    assert assistant.commit(plan.downloads)

    assert len(received) == 1
    assert received[0].settings == plan.settings
    assert received[0].panel == plan.panel
    assert received[0].panel["language"] == "English"
    assert received[0].panel["recognition_enabled"] is True


def test_set_up_stores_the_keys_once(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  a key typed on the proofreading page
    When   Set up is pressed
    Then   the key is in the keychain, and it stays there when the window closes
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    monkeypatch.setattr(assistant.install_round(), "start", lambda **_kw: True)
    assistant.key_drafts().set("OPENAI_API_KEY", "sk-typed")
    plan = _plan(assistant, service)

    assert assistant.commit(plan.downloads)
    assistant.done(QDialog.DialogCode.Accepted)

    assert keyring.passwords[("TeachersTeammate", "OPENAI_API_KEY")] == "sk-typed"
    assert os.environ["OPENAI_API_KEY"] == "sk-typed"


def test_set_up_starts_the_downloads_by_kind(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  a plan with a package, a name-detection model and an Ollama model
    When   Set up is pressed
    Then   the round gets one list per kind, so pip, spaCy and the pulls run in order
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service, correction_provider="openai"))
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(
        assistant.install_round(), "start", lambda **kwargs: started.append(kwargs) or True
    )
    plan = _plan(assistant, service)
    downloads = (
        _download(DownloadKind.PACKAGE, "langchain-openai"),
        _download(DownloadKind.SPACY_MODEL, "de_core_news_sm"),
        _download(DownloadKind.OLLAMA_MODEL, "qwen3:8b"),
    )

    assert assistant.commit(downloads)

    assert started == [
        {
            "packages": ["langchain-openai"],
            "spacy_models": ["de_core_news_sm"],
            "ollama_models": ["qwen3:8b"],
            "ollama_url": plan.settings.get("ollama_url", DEFAULTS["ollama_url"]),
        }
    ]


def test_an_ollama_model_that_cannot_be_pulled_now_is_left_out(
    qtbot, keyring, no_keys, monkeypatch
) -> None:
    """
    Given  a plan whose Ollama model cannot be downloaded because Ollama is not running
    When   Set up is pressed
    Then   the round is not asked for it, so it does not fail at once
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(
        assistant.install_round(), "start", lambda **kwargs: started.append(kwargs) or True
    )
    _plan(assistant, service)

    assert assistant.commit((_download(DownloadKind.OLLAMA_MODEL, "qwen3:8b", possible=False),))

    assert started[0]["ollama_models"] == []


def test_set_up_hands_over_only_the_ticked_downloads(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  a plan with two downloads, one of which the teacher unticks
    When   Set up is pressed
    Then   only the ticked one is started, so the teacher decides what is fetched
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(
        assistant.install_round(), "start", lambda **kwargs: started.append(kwargs) or True
    )
    _plan(assistant, service)
    summary = assistant._pages[SetupPageId.SUMMARY]
    summary._rows._boxes["gemma4:12b"].setChecked(False)

    assert assistant.commit(assistant.downloads())

    assert started[0]["ollama_models"] == ["hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0"]


def test_set_up_does_nothing_while_the_plan_is_missing(qtbot, keyring, no_keys) -> None:
    """
    Given  a summary page whose plan has not arrived
    When   Set up is pressed
    Then   nothing is committed, so no half-worked-out plan is applied
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    received: list[SetupResult] = []
    assistant.committed.connect(received.append)
    assert not assistant.commit(())
    assert received == []


def test_the_summary_page_is_not_complete_until_the_plan_arrives(qtbot) -> None:
    """
    Given  the summary page
    When   it is shown and then the setup plan arrives
    Then   Set up is disabled meanwhile, so the teacher cannot commit nothing
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    summary = assistant._pages[SetupPageId.SUMMARY]
    summary._plan = None
    assert not summary.isComplete()
    _plan(assistant, service)
    assert summary.isComplete()


# ── Cancel ────────────────────────────────────────────────────────────────────


def test_cancel_puts_the_keys_back(qtbot, keyring, no_keys) -> None:
    """
    Given  a key typed on the proofreading page
    When   the assistant is cancelled before Set up
    Then   the environment is as it was, and nothing was stored
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assistant.key_drafts().set("OPENAI_API_KEY", "sk-typed")

    assistant.done(QDialog.DialogCode.Rejected)

    assert "OPENAI_API_KEY" not in os.environ
    assert keyring.passwords == {}


def test_cancel_forgets_the_model_lists_fetched_with_the_key(qtbot, keyring, no_keys) -> None:
    """
    Given  a key typed on the proofreading page
    When   the assistant is cancelled
    Then   the model lists fetched with that key are forgotten, so the next window asks again
    """
    service = _ForgettingService()
    assistant = _assistant(qtbot, _context(service))
    assistant.key_drafts().set("OPENAI_API_KEY", "sk-typed")

    assistant.done(QDialog.DialogCode.Rejected)

    assert "openai" in service.forgotten


def test_closing_waits_while_a_package_installs(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  a pip install that is still running
    When   the window is closed
    Then   it stays open and says why: the new package must be set up first
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    monkeypatch.setattr(assistant.install_round(), "busy", lambda: True)
    told: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information", staticmethod(lambda _p, _t, text: told.append(text))
    )
    assistant.show()

    assistant.done(QDialog.DialogCode.Rejected)

    assert told == [CLOSE_BLOCKED]
    assert assistant.isVisible()


def test_closing_stops_every_page(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  an assistant whose pages started background work
    When   the window is closed
    Then   every page is stopped, so no check answers into a destroyed window
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    stopped: list[SetupPageId] = []
    for page_id, page in assistant._pages.items():
        monkeypatch.setattr(page, "shutdown", lambda pid=page_id: stopped.append(pid))

    assistant.done(QDialog.DialogCode.Rejected)

    assert sorted(stopped) == sorted(ORDER)


def test_select_documents_finishes_and_asks_for_the_folder(qtbot, keyring, no_keys) -> None:
    """
    Given  the done page
    When   "Select documents…" is pressed
    Then   the window finishes and the main window is asked to choose the folder
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assistant.show()
    assert not assistant.documents_wanted()

    assistant.select_documents()

    assert assistant.documents_wanted()
    assert assistant.result() == QDialog.DialogCode.Accepted


def test_finish_does_not_ask_for_the_folder(qtbot, keyring, no_keys) -> None:
    """
    Given  the done page
    When   Finish is pressed
    Then   the window closes without opening a folder dialog
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    assistant.show()

    assistant.accept()

    assert not assistant.documents_wanted()


# ── what the pages ask the wizard ─────────────────────────────────────────────


def test_set_up_marks_the_assistant_as_committed(qtbot, keyring, no_keys) -> None:
    """
    Given  an assistant that was not set up
    When   Set up is pressed
    Then   it says so, however the window is closed afterwards
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    assert not assistant.is_committed()
    _plan(assistant, service)
    assert assistant.commit(())
    assert assistant.is_committed()


def test_set_up_waits_for_a_running_install(qtbot, monkeypatch) -> None:
    """
    Given  a plan, and an install round that still runs (the "Install now" of a service)
    When   the summary page is asked whether Set up can be pressed
    Then   it cannot until the round has finished, so no download is turned down unseen
    """
    service = SetupAppService()
    assistant = _assistant(qtbot, _context(service))
    summary = assistant._pages[SetupPageId.SUMMARY]
    _plan(assistant, service)
    round_ = assistant.install_round()
    monkeypatch.setattr(round_, "running", lambda: True)
    assert not summary.isComplete()
    monkeypatch.setattr(round_, "running", lambda: False)
    assert summary.isComplete()


def test_a_finished_round_asks_every_page_again(qtbot, keyring, no_keys, monkeypatch) -> None:
    """
    Given  an assistant whose pages list models
    When   the install round finishes
    Then   every page is told, so a package that just arrived is offered at once
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    asked: list[SetupPageId] = []
    for page_id, page in assistant._pages.items():
        monkeypatch.setattr(page, "on_packages_changed", lambda pid=page_id: asked.append(pid))

    assistant.install_round().finished.emit(True)

    assert sorted(asked) == sorted(ORDER)


def test_the_answers_start_from_the_current_settings(qtbot) -> None:
    """
    Given  a settings file with proofreading at OpenAI and text recognition off
    When   the assistant is opened from the Tools menu
    Then   the answers are those settings, so the assistant starts where the teacher is
    """
    assistant = _assistant(
        qtbot,
        _context(SetupAppService(), correction_provider="openai", recognition_enabled=False),
    )
    answers = assistant.answers()
    assert answers.hand_in is HandIn.TYPED
    assert answers.proofreading is ProofreadingPlace.ONLINE


def _download(kind: DownloadKind, name: str, *, possible: bool = True) -> PlannedDownload:
    """Return one planned download of *kind*."""
    return PlannedDownload(kind=kind, name=name, stage="correction", possible=possible)
