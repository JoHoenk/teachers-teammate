"""The Setup Assistant walked end to end: the real wizard, the real service, a real server.

What is real here: :class:`~teachers_teammate.gui.setup.SetupAssistant` and every page,
:class:`~teachers_teammate.application.setup_plan.SetupService` (``setup_facts``,
``plan_setup``, ``ollama_status``, model listing and rating), the ``LatestTask`` thread pool,
:class:`~teachers_teammate.gui._install_round.InstallRound`,
:class:`~teachers_teammate.gui._pull_manager.PullManager`, the ``ollama`` client over
loopback HTTP, :class:`~teachers_teammate.gui._main_window.MainWindow` and ``ocr.toml`` on disk.

What is replaced, each at the narrowest seam:

* Ollama — :class:`~tests.fakes.fake_ollama.FakeOllama` on one loopback port, so a server on the
  developer's computer cannot answer instead.
* pip, spaCy, the reader and the page models — recording stubs on the service's
  ``install_packages`` / ``download_page_models`` / ``download_reader`` and on
  ``download_spacy_model`` (the round's spaCy thread builds its own service): the real downloads
  have their own tests and need the network.
* What the model store holds — a :class:`ModelStore` under ``tmp_path`` with a ``bundled_root``
  that does not exist, so a ``_models`` folder left by a local build cannot change the setup plan.
* The computer — ``hardware_profile = "16gb"``, a fixed free disk and a fixed memory, so the
  plan never depends on the runner's graphics card, RAM or disk.
* The keychain and the background network threads — the autouse fixtures in ``conftest.py``.

``TEACHERS_TEAMMATE_TMPDIR`` points at ``tmp_path``, so the cache, the config root and the data
root are one folder and a window without an ``ocr.toml`` is a first start.

No pixel automation: ``qtbot`` drives the same widgets a teacher clicks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
import socket
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWizard
import pytest

from teachers_teammate.application import providers as providers_module
from teachers_teammate.application.anonymization import AnonymizationService
from teachers_teammate.application.composition import AppServices, build_app_services
from teachers_teammate.application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    ProofreadingPlace,
    SetupPlan,
    SetupService,
)
from teachers_teammate.config import dump_config_file, load_config_file
from teachers_teammate.settings_schema import DEFAULTS
from teachers_teammate.domain.hardware_profile import HardwareProfile
from teachers_teammate.domain.model_catalog import ModelCategory
from teachers_teammate.domain.model_fit import best_model
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.gui import _dialogs
from teachers_teammate.gui._background import wait_for_background_tasks
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui.setup import SetupAssistant
from teachers_teammate.gui.setup._flow import ORDER, SetupPageId
from teachers_teammate.texts.main_window import READINESS_CHECKING
from teachers_teammate.texts.servers import STATUS_CHECKING
from teachers_teammate.texts.setup import DONE_DONE, DONE_WAITING
from teachers_teammate.gui.setup._pages import (
    ClassesPage,
    DonePage,
    LocalAiPage,
    PrivacyPage,
    ProofreadingPage,
    SummaryPage,
)
from teachers_teammate.infrastructure import gpu_detector, ollama_install
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.texts import dialogs as dlg
from teachers_teammate.texts._types import Notice

from ..fakes.fake_ollama import FakeOllama
from ._settings_stubs import commit_key, select_provider

#: How long a wait may take: a wizard plus real threads needs more than the default.
WAIT_MS = 10_000
#: The free space the setup plan sees, so no test depends on the runner's disk.
FREE_DISK_GB = 200.0
#: The memory the setup plan sees, so no test depends on the runner's RAM.
SYSTEM_MEMORY_GB = 32.0
#: The profile the setup plan sees, so no test depends on the runner's graphics card.
PROFILE: HardwareProfile = HardwareProfile.CARD_16GB
#: The language the walkthroughs answer with, so the expected spaCy model is known.
LANGUAGE = "English"
#: The online service S7 picks, and the model it offers.
PROVIDER = "mistral"
MODEL = "mistral-small-latest"
#: The key S7 types, and the environment variable the service reads it from.
KEY = "k-test"


def wait_until(qtbot: Any, condition: Callable[[], bool], message: str) -> None:
    """Wait for *condition*, failing with *message* when it never comes true.

    ``qtbot.waitUntil`` takes no message of its own, so the condition asserts it: a timeout
    then reports what the test was waiting for instead of a bare ``False``.
    """

    def check() -> None:
        assert condition(), message

    qtbot.waitUntil(check, timeout=WAIT_MS)


class Downloads:
    """What the install round asked for, instead of really downloading it.

    The real pip installs, spaCy models and model files have their own tests and need the
    network; here only *that* they were asked for matters.
    """

    def __init__(self) -> None:
        #: The pip package lists the round installed, in order.
        self.packages: list[list[str]] = []
        #: The spaCy models the round downloaded, in order.
        self.spacy_models: list[str] = []
        #: How often the page models were downloaded.
        self.page_models = 0
        #: How often the built-in reader was downloaded.
        self.reader = 0

    def install_packages(self, packages: list[str], progress_cb: Callable[[str], None]) -> None:
        """Record the packages and report them as installed."""
        self.packages.append(list(packages))
        progress_cb(f"installed {', '.join(packages)}")

    def download_spacy_model(
        self, model: str, target: Path, progress_cb: Callable[[str], None]
    ) -> None:
        """Record the spaCy model; the package is never fetched."""
        _ = target
        self.spacy_models.append(model)
        progress_cb(f"downloaded {model}")

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: Any = None,
    ) -> None:
        """Record the download; the files are never fetched."""
        _ = on_progress, cancel
        self.page_models += 1

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: Any = None,
    ) -> None:
        """Record the download; the files are never fetched."""
        _ = on_progress, cancel
        self.reader += 1


class Driver:
    """The answers a teacher gives, one method per page, and the pages they really saw."""

    def __init__(self, qtbot: Any, assistant: SetupAssistant) -> None:
        self._qtbot = qtbot
        self.assistant = assistant
        #: The round that installs what the setup plan asked for.
        self.round = assistant.install_round()
        #: The main window's pull manager, which outlives the wizard.
        self.pull_manager = assistant._context.pull_manager
        #: Every page the wizard really showed, in order.
        self.visited: list[SetupPageId] = []
        #: The Done page's lines, read while the wizard was still there.
        self.rows: dict[str, str] = {}
        start = assistant.currentId()
        if start >= 0:
            self.visited.append(ORDER[start])
        assistant.currentIdChanged.connect(self._on_page_changed)

    def _on_page_changed(self, page_id: int) -> None:
        """Remember the page the wizard moved to; -1 means it is closing, not a page."""
        if page_id < 0:
            return
        page = ORDER[page_id]
        if not self.visited or self.visited[-1] is not page:
            self.visited.append(page)

    def page(self, page_id: SetupPageId) -> Any:
        """Return the page of *page_id*."""
        return self.assistant._pages[page_id]

    # ── the answers ───────────────────────────────────────────────────────────

    def classes(self, language: str = LANGUAGE, hand_in: HandIn = HandIn.HANDWRITTEN) -> None:
        """Answer the language and what the students hand in."""
        page = self.page(SetupPageId.CLASSES)
        assert isinstance(page, ClassesPage)
        page._language.setCurrentText(language)
        if hand_in is HandIn.HANDWRITTEN:
            page._handwritten.setChecked(True)
        else:
            page._typed.setChecked(True)

    def local_ai(self, url: str | None = None, profile: str | None = None) -> None:
        """Leave "on this computer", or pick a server elsewhere and type its address.

        *profile* is the computer the suggestions follow, as the picker on the page offers it.
        """
        page = self.page(SetupPageId.LOCAL_AI)
        assert isinstance(page, LocalAiPage)
        if url is None:
            page._here.setChecked(True)
        else:
            page._server.setChecked(True)
            page._url.setText(url)
            page._url.editingFinished.emit()
        if profile is not None:
            assert page._profile is not None, "the profile picker is built when the page is shown"
            page._profile.set_setting(profile)

    def proofreading(
        self,
        place: ProofreadingPlace = ProofreadingPlace.LOCAL,
        online: tuple[str, str] | None = None,
        key: str = "",
    ) -> None:
        """Answer where the proofreading runs, and which service when it runs online.

        *online* is the service and its model; *key* is typed into the key row the service
        asks for, as a teacher pastes it.
        """
        page = self.page(SetupPageId.PROOFREADING)
        assert isinstance(page, ProofreadingPage)
        if place is ProofreadingPlace.LOCAL:
            page._local.setChecked(True)
            return
        if place is ProofreadingPlace.OFF:
            page._off.setChecked(True)
            return
        page._online.setChecked(True)
        if online is not None:
            provider, model = online
            section = page._section
            assert section is not None, "the online section is built when the choice is made"
            select_provider(section, provider)
            section._model.setCurrentText(model)
            if key:
                commit_key(section.key_row(), key)

    def local_ai_note(self) -> str:
        """Wait for the Local AI page's status line to answer, and return what it says."""
        page = self.page(SetupPageId.LOCAL_AI)
        assert isinstance(page, LocalAiPage)
        assert page._status is not None, "the status line is built when the page is shown"
        wait_until(
            self._qtbot,
            lambda: page._status._label.text() != STATUS_CHECKING,
            "the status line was left on its first line",
        )
        return page._status._label.text()

    def privacy(self, remove_names: bool = True) -> None:
        """Answer whether names are replaced before the text goes online."""
        page = self.page(SetupPageId.PRIVACY)
        assert isinstance(page, PrivacyPage)
        page._remove.setChecked(remove_names)

    # ── moving on ─────────────────────────────────────────────────────────────

    def next(self) -> None:
        """Press Next, waiting first for the summary page to be ready to commit."""
        before = self.assistant.currentId()
        # The summary page is the commit page: its button is the CommitButton ("Set up").
        which = (
            QWizard.WizardButton.CommitButton
            if ORDER[before] is SetupPageId.SUMMARY
            else QWizard.WizardButton.NextButton
        )
        button = self.assistant.button(which)
        assert button is not None
        if ORDER[before] is SetupPageId.SUMMARY:
            wait_until(
                self._qtbot,
                button.isEnabled,
                "Set up never became possible: the setup plan did not arrive",
            )
        self._qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        self._set_up_waits_for_the_plan(button)
        wait_until(
            self._qtbot,
            lambda: self.assistant.currentId() != before or not self.assistant.isVisible(),
            f"the wizard stayed on {ORDER[before]}",
        )

    def _set_up_waits_for_the_plan(self, button: Any) -> None:
        """Set up must not be offered before the summary page has a plan.

        The plan is worked out on a background thread and arrives through the event loop, so
        right after the click that enters the summary page it cannot be there yet.
        """
        if self.assistant.currentId() != ORDER.index(SetupPageId.SUMMARY):
            return
        summary = self.page(SetupPageId.SUMMARY)
        assert isinstance(summary, SummaryPage)
        if summary.plan() is None:
            assert not button.isEnabled(), "Set up was offered before the setup plan arrived"

    def wait_for_downloads(self) -> None:
        """Wait until the round and the pull manager have nothing left to do.

        The round hands the pulls over to the manager, so neither half alone says when the
        work is over: the round is still running while the manager pulls, and the manager is
        idle until the round has installed the page models.  A row that never starts — a model
        the teacher left unticked, or one whose pull failed — stays "waiting", so the rows
        cannot say it either.  The rows are read here, while the wizard still exists.
        """
        wait_until(
            self._qtbot,
            lambda: not self.round.running() and not self.pull_manager.is_active,
            "the downloads never finished",
        )
        self.rows = self.done_rows()

    def cancel(self) -> None:
        """Press Cancel, as a teacher who changes their mind does."""
        button = self.assistant.button(QWizard.WizardButton.CancelButton)
        assert button is not None
        self._qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    def walk(self, *pages: SetupPageId) -> None:
        """Press Next until the wizard shows the last of *pages*."""
        for page in pages:
            assert self.visited[-1] is page, f"expected {page}, the wizard shows {self.visited[-1]}"
            self.next()

    # ── what the pages say ────────────────────────────────────────────────────

    def plan(self) -> SetupPlan:
        """Wait for the setup plan the summary page works out, and return it."""
        page = self.page(SetupPageId.SUMMARY)
        assert isinstance(page, SummaryPage)
        wait_until(
            self._qtbot,
            lambda: page.plan() is not None,
            "the setup plan never arrived",
        )
        plan = page.plan()
        assert plan is not None
        return plan

    def summary_rows(self) -> list[str]:
        """Return the summary page's lines as the teacher reads them."""
        page = self.page(SetupPageId.SUMMARY)
        assert isinstance(page, SummaryPage)
        return page._rows.row_texts()

    def done_rows(self) -> dict[str, str]:
        """Return what the Done page says about each download, by name."""
        page = self.page(SetupPageId.DONE)
        assert isinstance(page, DonePage)
        return {name: label.text() for name, label in page._rows._labels.items()}


class Walkthrough:
    """One real ``MainWindow``, one fake Ollama server, and the answers a teacher gives."""

    def __init__(
        self,
        qtbot: Any,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        settings: Mapping[str, Any] | None = None,
        first_start: bool = False,
        ollama_url: str | None = None,
    ) -> None:
        self.qtbot = qtbot
        self.tmp_path = tmp_path
        self.first_start = first_start
        self.fake = FakeOllama()
        self.downloads = Downloads()
        #: Every driver the wizard handed out, in order.
        self.drivers: list[Driver] = []
        self._script: Callable[[Driver], None] | None = None

        # One folder for the cache, the config root and the data root.
        monkeypatch.setenv("TEACHERS_TEAMMATE_TMPDIR", str(tmp_path))
        # The computer: a fixed disk and a fixed memory, so the setup plan is the same everywhere.
        monkeypatch.setattr(ollama_install, "free_disk_gb", lambda _path: FREE_DISK_GB)
        monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: SYSTEM_MEMORY_GB)
        monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
        monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: ())

        self.services = build_app_services(
            model_store=ModelStore(tmp_path / "models", bundled_root=tmp_path / "no-bundle")
        )
        self.service = self.services.run
        monkeypatch.setattr(
            self.services.addons, "install_packages", self.downloads.install_packages
        )
        monkeypatch.setattr(
            self.services.reader,
            "download_page_models",
            self.downloads.download_page_models,
        )
        monkeypatch.setattr(self.services.reader, "download_reader", self.downloads.download_reader)
        # The computer's language is not the machine's: the extra names language is the same everywhere.
        monkeypatch.setattr(AnonymizationService, "system_spacy_model", lambda _service: None)
        # The round's spaCy thread builds a service of its own, so the stub goes on the class.
        monkeypatch.setattr(
            AnonymizationService,
            "download_spacy_model",
            lambda _service, model, target, progress_cb: self.downloads.download_spacy_model(
                model, target, progress_cb
            ),
        )

        self.settings: dict[str, Any] = dict(DEFAULTS)
        self.settings.update(settings or {})
        self.settings["ollama_url"] = ollama_url or self.fake.url
        self.settings["hardware_profile"] = PROFILE
        if not first_start:
            self.write_settings()

        # A plain function on the class, so ``assistant.exec()`` passes the assistant in.
        monkeypatch.setattr(
            SetupAssistant, "exec", lambda assistant: self._scripted_exec(assistant)
        )
        self.win = MainWindow(app_service=self.services)
        qtbot.addWidget(self.win)

    # ── the settings file ─────────────────────────────────────────────────────

    @property
    def config_path(self) -> Path:
        """Return where the app keeps its settings."""
        return self.tmp_path / "ocr.toml"

    def write_settings(self) -> Path:
        """Write the settings file the window loads, as an earlier session left it."""
        self.config_path.write_text(dump_config_file(self.settings), encoding="utf-8")
        return self.config_path

    def saved_settings(self) -> dict[str, Any]:
        """Return the settings file as the app reads it back."""
        return load_config_file(self.config_path).values

    # ── running the wizard ────────────────────────────────────────────────────

    def _scripted_exec(self, assistant: SetupAssistant) -> int:
        """Show the assistant, run the script against it, close it — production's sequence.

        ``exec()`` would run a nested modal loop; this runs the same steps without one, so a
        failing assertion is reported instead of being swallowed by the loop.
        """
        script = self._script
        assert script is not None, "the wizard opened without a script"
        driver = Driver(self.qtbot, assistant)
        self.drivers.append(driver)
        assistant.show()
        try:
            script(driver)
        finally:
            if assistant.isVisible():
                wait_until(
                    self.qtbot,
                    lambda: not assistant.install_round().busy(),
                    "the install round never finished, so the wizard cannot close",
                )
                assistant.reject()
        return assistant.result()

    def run(self, script: Callable[[Driver], None]) -> Driver:
        """Open the assistant from the Tools menu and run *script* against it."""
        self._script = script
        self.win._menu_bar.setup_action.trigger()
        assert self.drivers, "the Tools menu never opened the assistant"
        return self.drivers[-1]

    def run_first_start(self, script: Callable[[Driver], None]) -> Driver:
        """Let the first-start timer open the assistant, then run *script* against it."""
        self._script = script
        # The window schedules the assistant with a zero timer; one round of events fires it.
        QApplication.processEvents()
        assert self.drivers, "the first-start timer never opened the assistant"
        return self.drivers[-1]

    # ── what is left behind ───────────────────────────────────────────────────

    def readiness_note(self) -> str:
        """Wait for the main window's readiness line to answer, and return what it says."""
        wait_until(
            self.qtbot,
            lambda: self.win._readiness_line.label.text() not in ("", READINESS_CHECKING),
            "the readiness line was left on its first line",
        )
        return self.win._readiness_line.label.text()

    def close(self) -> None:
        """Stop the pulls, the background tasks and the fake server, in that order.

        A pull thread that is still running when Qt destroys its window aborts the whole
        process, and the real failure is lost.
        """
        try:
            self.win._pull_manager.abort()
            wait_until(
                self.qtbot,
                lambda: not self.win._pull_manager.is_active,
                "a pull thread was still running at teardown",
            )
            wait_for_background_tasks()
        finally:
            self.fake.close()


@pytest.fixture(name="walkthrough")
def _walkthrough(qtbot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Return a factory that builds one walkthrough per call and tears it down afterwards."""
    built: list[Walkthrough] = []

    def build(**kwargs: Any) -> Walkthrough:
        walk = Walkthrough(qtbot, tmp_path, monkeypatch, **kwargs)
        built.append(walk)
        return walk

    yield build
    for walk in reversed(built):
        walk.close()


@pytest.fixture(name="modal_boxes", autouse=True)
def _modal_boxes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every message box, and fail the test on the one that means a busy close.

    ``SetupAssistant.done()`` refuses to close while an install runs and says so in a box; a
    test that reached that point would hang instead of reporting why.  Every other box is
    recorded rather than shown: the app telling the teacher that a download failed is what a
    scenario may well expect.
    """
    shown: list[tuple[str, str]] = []

    def box(_parent: object, notice: Notice) -> None:
        shown.append((notice.title, notice.body))
        assert notice != dlg.installation_running(), "the wizard was closed while it was busy"

    monkeypatch.setattr(_dialogs, "notify", box)
    return shown


# ── what the setup plan should say ────────────────────────────────────────────


def closed_port_url() -> str:
    """Return an address nothing listens on: a port that was free a moment ago."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = int(sock.getsockname()[1])
    return f"http://127.0.0.1:{port}"


def expected_tag(category: ModelCategory) -> str:
    """Return the model the setup plan should pick for *category* on this fixed computer."""
    return best_model(category, PROFILE, system_memory_gb=SYSTEM_MEMORY_GB)


def ollama_downloads(plan: SetupPlan) -> list[str]:
    """Return the Ollama tags the setup plan wants to pull, in order."""
    return [
        download.name
        for download in plan.downloads
        if download.kind is DownloadKind.OLLAMA_MODEL and download.possible
    ]


def downloads_of(plan: SetupPlan, kind: DownloadKind) -> list[PlannedDownload]:
    """Return the setup plan's downloads of *kind*."""
    return [download for download in plan.downloads if download.kind is kind]


def names(downloads: Sequence[PlannedDownload]) -> list[str]:
    """Return the names of *downloads*, in order."""
    return [download.name for download in downloads]


# ── the scripts a teacher's answers follow ────────────────────────────────────


def _handwritten_local(driver: Driver) -> None:
    """Answer handwritten pages with local proofreading, and press Set up."""
    driver.next()  # Welcome → Your classes
    driver.classes(LANGUAGE, HandIn.HANDWRITTEN)
    driver.next()  # → The AI on this computer
    driver.local_ai()
    driver.next()  # → Proofreading
    driver.proofreading(ProofreadingPlace.LOCAL)
    driver.next()  # → Names and personal information
    driver.next()  # → Ready to set up
    driver.next()  # Set up → All set
    driver.wait_for_downloads()


# ── S1: the whole way through ─────────────────────────────────────────────────


def test_s1_handwritten_pages_are_set_up_on_this_computer(walkthrough) -> None:
    """
    Given  a first run from the Tools menu, an empty Ollama server and a 16 GB computer
    When   the teacher answers handwritten pages and local proofreading and presses Set up
    Then   the wizard walks Welcome → Classes → Local AI → Proofreading → Privacy → Summary → Done, the
           ticked downloads are pulled in order, and ocr.toml holds the answers
    """
    walk = walkthrough()

    driver = walk.run(_handwritten_local)

    assert driver.visited == [
        SetupPageId.WELCOME,
        SetupPageId.CLASSES,
        SetupPageId.LOCAL_AI,
        SetupPageId.PROOFREADING,
        SetupPageId.PRIVACY,
        SetupPageId.SUMMARY,
        SetupPageId.DONE,
    ]
    plan = driver.plan()
    assert names(downloads_of(plan, DownloadKind.OLLAMA_MODEL)) == [
        expected_tag("ocr"),
        expected_tag("general"),
    ]
    assert walk.fake.pulled == ollama_downloads(plan)
    assert walk.downloads.page_models == 1
    assert walk.downloads.spacy_models == names(downloads_of(plan, DownloadKind.SPACY_MODEL))

    saved = walk.saved_settings()
    assert saved["language"] == LANGUAGE
    assert saved["ocr_model"] == expected_tag("ocr")
    assert saved["correction_model"] == expected_tag("general")
    assert saved["ollama_url"] == walk.fake.url
    assert saved["hardware_profile"] == PROFILE
    assert saved["recognition_enabled"] is True

    assert driver.rows, "the Done page lists no download"
    assert set(driver.rows.values()) == {DONE_DONE}


# ── S2: a working choice is kept ──────────────────────────────────────────────


def test_s2_running_the_assistant_again_changes_nothing(walkthrough) -> None:
    """
    Given  a window that was just set up, so the server now holds the models it pulled
    When   the teacher runs the assistant again and answers the same
    Then   nothing is pulled again and the settings file is the same as before
    """
    walk = walkthrough()
    walk.run(_handwritten_local)
    before = walk.saved_settings()
    pulled_before = list(walk.fake.pulled)

    driver = walk.run(_handwritten_local)

    assert ollama_downloads(driver.plan()) == []
    assert walk.fake.pulled == pulled_before
    assert walk.saved_settings() == before


# ── S3: a first start, typed texts, the address typed on the Local AI page ────


def test_s3_first_start_with_typed_texts_asks_for_the_writing_model_only(walkthrough) -> None:
    """
    Given  a computer without an ocr.toml, so the first-start timer opens the assistant
    When   the teacher answers typed texts and local proofreading and types the server address
    Then   the wizard walks Classes → Proofreading → Local AI → Privacy → Summary, text recognition stays
           off, only the writing model is pulled and no page models are downloaded
    """
    walk = walkthrough(first_start=True)
    url = walk.fake.url

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.TYPED)
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.LOCAL)
        driver.next()  # → The AI on this computer
        driver.local_ai(url, profile=PROFILE)
        driver.next()  # → Names and personal information
        driver.next()  # → Ready to set up
        driver.next()  # Set up → All set
        driver.wait_for_downloads()

    driver = walk.run_first_start(script)

    assert driver.visited == [
        SetupPageId.WELCOME,
        SetupPageId.CLASSES,
        SetupPageId.PROOFREADING,
        SetupPageId.LOCAL_AI,
        SetupPageId.PRIVACY,
        SetupPageId.SUMMARY,
        SetupPageId.DONE,
    ]
    assert walk.fake.pulled == [expected_tag("general")]
    assert walk.downloads.page_models == 0

    saved = walk.saved_settings()
    assert saved["recognition_enabled"] is False
    assert saved["correction_enabled"] is True
    assert saved["correction_model"] == expected_tag("general")
    assert saved["ollama_url"] == url
    assert saved["language"] == LANGUAGE


# ── S6: no Ollama on this computer ────────────────────────────────────────────


def test_s6_a_stopped_server_is_left_out_and_nothing_is_pulled(walkthrough, monkeypatch) -> None:
    """
    Given  an address nothing answers on and no Ollama program on this computer
    When   the teacher answers handwritten pages and local proofreading and presses Set up
    Then   the page says Ollama is not there, the setup plan leaves the Ollama downloads out, no pull
           is attempted and the page models are downloaded all the same
    """
    monkeypatch.setattr(ollama_install, "find_ollama_program", lambda: None)
    walk = walkthrough(ollama_url=closed_port_url())
    notes: list[str] = []

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.HANDWRITTEN)
        driver.next()  # → The AI on this computer
        driver.local_ai()
        notes.append(driver.local_ai_note())
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.LOCAL)
        driver.next()  # → Names and personal information
        driver.next()  # → Ready to set up
        driver.next()  # Set up → All set
        driver.wait_for_downloads()

    driver = walk.run(script)

    assert notes, "the Local AI page never said anything about the server"
    assert "Ollama" in notes[0], f"the page said {notes[0]!r} instead of naming Ollama"

    plan = driver.plan()
    ollama = downloads_of(plan, DownloadKind.OLLAMA_MODEL)
    assert ollama, "the setup plan should still name the models it would use"
    assert not any(download.possible for download in ollama)
    assert walk.fake.pulled == []
    assert walk.downloads.page_models == 1

    # A row that never started stays "waiting": nothing was pulled from the closed port.
    for download in ollama:
        assert driver.rows[download.name] == DONE_WAITING


# ── S4: handwritten pages, no proofreading for now ────────────────────────────


def test_s4_not_now_leaves_the_proofreading_off(walkthrough) -> None:
    """
    Given  a first run from the Tools menu and an empty Ollama server
    When   the teacher answers handwritten pages and "Not now" for the proofreading
    Then   proofreading stays off, only the reading model is pulled, and the page models are
           downloaded all the same
    """
    walk = walkthrough()

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.HANDWRITTEN)
        driver.next()  # → The AI on this computer
        driver.local_ai()
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.OFF)
        driver.next()  # → Names and personal information
        driver.next()  # → Ready to set up
        driver.next()  # Set up → All set
        driver.wait_for_downloads()

    driver = walk.run(script)

    assert walk.fake.pulled == [expected_tag("ocr")]
    assert walk.downloads.page_models == 1

    saved = walk.saved_settings()
    assert saved["recognition_enabled"] is True
    assert saved["correction_enabled"] is False
    assert saved["ocr_model"] == expected_tag("ocr")
    assert set(driver.rows.values()) == {DONE_DONE}


# ── S5: a pull that fails ─────────────────────────────────────────────────────


def test_s5_a_failed_pull_drops_the_queue_and_leaves_the_window_usable(
    walkthrough, modal_boxes
) -> None:
    """
    Given  an Ollama server that refuses the reading model
    When   the teacher answers handwritten pages and local proofreading and presses Set up
    Then   the reading row says why, the writing model is never asked for, and the window still
           answers: the readiness line is not left on its first line
    """
    walk = walkthrough()
    reading = expected_tag("ocr")
    writing = expected_tag("general")
    walk.fake.fail_pull = {reading}

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.HANDWRITTEN)
        driver.next()  # → The AI on this computer
        driver.local_ai()
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.LOCAL)
        driver.next()  # → Names and personal information
        driver.next()  # → Ready to set up
        driver.next()  # Set up → All set
        driver.wait_for_downloads()

    driver = walk.run(script)

    # Only the reading model was asked for: the failure dropped the queue behind it.
    asked = [r.body.get("model") for r in walk.fake.requests if r.path == "/api/pull"]
    assert asked == [reading]
    assert walk.fake.pulled == []

    assert driver.rows[reading].startswith("✗"), driver.rows[reading]
    assert driver.rows[writing] == DONE_WAITING

    assert walk.win._menu_bar.setup_action.isEnabled(), "the window stopped answering"
    assert walk.readiness_note() not in ("", READINESS_CHECKING)
    assert any("failed" in title.lower() for title, _text in modal_boxes), modal_boxes


# ── S8: changing one's mind ───────────────────────────────────────────────────


def held_keys(services: AppServices) -> dict[str, str | None]:
    """Return what the app holds for each key the providers read, by environment variable."""
    providers = services.providers
    env_keys = {providers.env_key(provider) for provider in providers.list_providers()}
    return {env_key: services.keys.held(env_key) for env_key in env_keys if env_key}


def test_s8_cancel_on_the_summary_changes_nothing(walkthrough) -> None:
    """
    Given  a window that was set up before, so ocr.toml holds a working choice
    When   the teacher walks to the summary and presses Cancel
    Then   the settings file is untouched, nothing is downloaded and no key is left behind
    """
    walk = walkthrough()
    before = walk.config_path.read_text(encoding="utf-8")
    keys = held_keys(walk.services)

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.HANDWRITTEN)
        driver.next()  # → The AI on this computer
        driver.local_ai()
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.LOCAL)
        driver.next()  # → Names and personal information
        driver.next()  # → Ready to set up
        driver.plan()  # the setup plan arrived, so Cancel really leaves a finished summary
        driver.cancel()

    driver = walk.run(script)

    assert driver.visited[-1] is SetupPageId.SUMMARY
    assert walk.config_path.read_text(encoding="utf-8") == before
    assert walk.fake.pulled == []
    assert walk.downloads.page_models == 0
    assert held_keys(walk.services) == keys


def test_s8_cancel_on_a_first_start_leaves_a_working_setup(walkthrough, monkeypatch) -> None:
    """
    Given  a computer without an ocr.toml
    When   the teacher cancels the assistant the first-start timer opened
    Then   the settings file holds the system language and the best models for this computer
    """
    monkeypatch.setattr(SetupService, "system_language", lambda self: LANGUAGE)
    walk = walkthrough(first_start=True)
    profile = walk.services.recommendations.resolve_hardware_profile("auto", walk.fake.url).profile

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.cancel()

    walk.run_first_start(script)

    wait_until(
        walk.qtbot,
        lambda: walk.saved_settings().get("ocr_model") == best_model("ocr", profile),
        "the first start never saved the best models for this computer",
    )
    saved = walk.saved_settings()
    assert saved["language"] == LANGUAGE
    assert saved["correction_model"] == best_model("general", profile)
    assert walk.fake.pulled == []


# ── S7: an online service whose package is missing ────────────────────────────


def test_s7_an_online_service_installs_its_package_and_keeps_the_key(
    walkthrough, monkeypatch, credential_file
) -> None:
    """
    Given  a computer without the package the online proofreading needs
    When   the teacher answers typed texts, picks that service, types its key and ticks
           "remove names"
    Then   the package is installed, the service and its model are saved, and the key is kept
           in the credential store
    """
    walk = walkthrough()
    monkeypatch.setattr(
        walk.services.providers,
        "missing_provider_package",
        lambda provider: "langchain-mistralai" if provider == "mistral" else None,
    )
    monkeypatch.setattr(
        walk.services.anonymization,
        "anonymizer_available",
        lambda _language, _config=None: True,  # name detection is there: no spaCy download
    )
    monkeypatch.setattr(
        providers_module,
        "list_provider_models_checked",
        lambda _provider, **_kwargs: ModelListing(models=[MODEL]),
    )

    def script(driver: Driver) -> None:
        driver.next()  # Welcome → Your classes
        driver.classes(LANGUAGE, HandIn.TYPED)
        driver.next()  # → Proofreading
        driver.proofreading(ProofreadingPlace.ONLINE, online=(PROVIDER, MODEL), key=KEY)
        driver.next()  # → Privacy
        driver.privacy(remove_names=True)
        driver.next()  # → Ready to set up
        driver.next()  # Set up → All set
        driver.wait_for_downloads()

    driver = walk.run(script)

    assert walk.downloads.packages == [["langchain-mistralai"]]
    saved = walk.saved_settings()
    assert saved["correction_provider"] == PROVIDER
    assert saved["correction_model"] == MODEL
    assert saved["anonymization_enabled"] is True
    assert credential_file.get("credentials/MISTRAL_API_KEY") == KEY
    assert set(driver.rows.values()) == {DONE_DONE}
