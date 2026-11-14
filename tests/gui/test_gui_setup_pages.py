"""GUI tests for the assistant's pages: what each one asks and what it hands back."""
# pylint: disable=W0212  # protected-access — the tests drive the pages' widgets directly

from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QWidget
import pytest

from teachers_teammate.application.setup_plan import (
    DownloadKind,
    HandIn,
    PlannedDownload,
    PlannedStep,
    ProofreadingPlace,
    SetupPlan,
)
from teachers_teammate.config import DEFAULTS
from teachers_teammate.domain.data_location import DataRegion
from teachers_teammate.domain.model_catalog import (
    DEFAULT_OCR_MODEL,
    DiskVerdict,
    ModelRating,
    Suitability,
)
from teachers_teammate.gui import _user_text as txt
from teachers_teammate.gui._data_flow_summary import PLACE_THIS_COMPUTER
from teachers_teammate.gui.setup import SetupAssistant, SetupContext
from teachers_teammate.gui.setup._flow import SetupPageId
from teachers_teammate.gui.setup._pages import (
    FIT_VERY_SLOW,
    FIT_WORDS,
    PRIVACY_ALREADY_ON,
    PRIVACY_HAS_DETECTION,
    PRIVACY_NEEDS_DETECTION,
    SUGGESTION,
    SUGGESTION_DOWNLOADED,
    SUMMARY_DISK_SHORT,
    SUMMARY_DOWNLOADS_TOTAL,
    SUMMARY_FAILED,
    SUMMARY_NO_DOWNLOADS,
    DONE_DONE,
    DONE_FAILED,
    DONE_PROGRESS,
    DONE_RUNNING,
    DONE_WAITING,
    _fit_words,
)
from teachers_teammate.gui.setup._summary import (
    CLASSES_NAME,
    HANDWRITTEN,
    ROW_CLASSES,
    ROW_DOWNLOADED,
    ROW_INSTALLED,
    ROW_KEEPS_OWN,
    ROW_KEPT,
    ROW_KEY_MISSING,
    ROW_OFF,
    ROW_SAME_MODEL,
    SIZE_GB,
    STEP_NAMES,
    TYPED,
    SummaryRow,
    SummaryRows,
    summary_rows,
)

from ._settings_stubs import SetupAppService


def _context(
    service: SetupAppService,
    *,
    start_language: str = "",
    first_start: bool = False,
    **settings: Any,
) -> SetupContext:
    """Return a context over *service* with the given settings keys overridden.

    *start_language* is what the main window hands in (the system language on a first start);
    a ``language`` keyword is the saved setting instead.
    """
    values: dict[str, Any] = dict(DEFAULTS)
    values.update(settings)
    return SetupContext(
        settings=values,
        app_service=service,
        language=start_language,
        first_start=first_start,
    )


def _assistant(qtbot, context: SetupContext) -> SetupAssistant:
    """Build the assistant for *context* and register it with qtbot."""
    assistant = SetupAssistant(context)
    qtbot.addWidget(assistant)
    return assistant


def _page(assistant: SetupAssistant, page_id: SetupPageId):
    """Return the page of *page_id*."""
    return assistant._pages[page_id]


def _summary(qtbot, service: SetupAppService):
    """Return the assistant and its summary page over *service*."""
    assistant = _assistant(qtbot, _context(service))
    return assistant, _page(assistant, SetupPageId.SUMMARY)


def _show_plan(page, assistant: SetupAssistant) -> SetupPlan:
    """Work the setup plan out for the assistant's answers and put it on *page*."""
    plan = assistant._context.app_service.plan_setup(
        assistant.answers(), dict(assistant._context.settings)
    )
    page._on_plan(plan, None)
    return plan


def _choose_online(assistant: SetupAssistant) -> None:
    """Walk to the privacy page the way the wizard does: proofreading at an online service."""
    proofreading = _page(assistant, SetupPageId.PROOFREADING)
    proofreading.initializePage()
    proofreading._online.setChecked(True)


# ── Your classes ──────────────────────────────────────────────────────────────


def test_the_classes_page_starts_on_the_system_language_on_a_first_start(qtbot) -> None:
    """
    Given  a first start, where the main window found the system language
    When   the classes page is built
    Then   it shows that language, because no settings file names one yet
    """
    assistant = _assistant(
        qtbot, _context(SetupAppService(), start_language="German", first_start=True)
    )
    assert _page(assistant, SetupPageId.CLASSES).language() == "German"


def test_the_classes_page_starts_on_the_saved_language_otherwise(qtbot) -> None:
    """
    Given  a settings file that names French
    When   the assistant is opened from the Tools menu
    Then   the classes page shows French, so the answers start from the current settings
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), language="French"))
    assert _page(assistant, SetupPageId.CLASSES).language() == "French"


def test_the_classes_page_starts_on_what_the_students_hand_in(qtbot) -> None:
    """
    Given  a settings file with text recognition off
    When   the classes page is built
    Then   "Only typed texts" is chosen, so the answers start from the current settings
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=False))
    assert _page(assistant, SetupPageId.CLASSES).hand_in() is HandIn.TYPED


# ── The AI on this computer ───────────────────────────────────────────────────


def test_the_local_ai_page_hides_the_address_for_this_computer(qtbot) -> None:
    """
    Given  Ollama on this computer
    When   the local AI page is built
    Then   the address field and "Find…" are disabled, because there is nothing to type
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), ollama_url="http://127.0.0.1:11434"))
    page = _page(assistant, SetupPageId.LOCAL_AI)
    assert page.ollama_url() == "http://127.0.0.1:11434"
    assert not page._url.isEnabled()
    assert not page._find.isEnabled()


def test_the_local_ai_page_shows_the_address_of_a_server_elsewhere(qtbot) -> None:
    """
    Given  Ollama on a server at school
    When   the local AI page is built
    Then   the address field and "Find…" are usable, and the address is the saved one
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), ollama_url="http://gpu-box:11434"))
    page = _page(assistant, SetupPageId.LOCAL_AI)
    assert page.ollama_url() == "http://gpu-box:11434"
    assert page._url.isEnabled()
    assert page._find.isEnabled()


def test_find_opens_the_search_dialog(qtbot, monkeypatch) -> None:
    """
    Given  the local AI page with a server elsewhere
    When   "Find…" is pressed
    Then   the search dialog opens, and it searches this computer only until its own
           network button is pressed
    """
    from teachers_teammate.gui.setup import _pages  # noqa: PLC0415

    opened: list[dict[str, Any]] = []

    class _Dialog(QDialog):
        """Records how it was opened; the real dialog searches this computer at once."""

        server_chosen = Signal(str)

        def __init__(self, service, parent, **kwargs) -> None:
            super().__init__(parent)
            opened.append({"service": service, "parent": parent, **kwargs})

        def exec(self) -> int:
            return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_pages, "ServerSearchDialog", _Dialog)
    assistant = _assistant(qtbot, _context(SetupAppService(), ollama_url="http://gpu-box:11434"))
    _page(assistant, SetupPageId.LOCAL_AI)._find.click()
    assert len(opened) == 1
    assert "start_search" not in opened[0]  # the dialog's own default: this computer only


# ── Proofreading ──────────────────────────────────────────────────────────────


def test_the_fit_wording_says_how_the_model_runs_here() -> None:
    """
    Given  a rating for each suitability
    When   the fit wording is asked for
    Then   it names the fit in plain words, and a model that spills to the processor
           says it runs very slowly
    """
    for suitability, words in FIT_WORDS.items():
        step = PlannedStep(
            stage="correction",
            provider="ollama",
            model="m",
            rating=ModelRating(
                tag="m",
                task="general",
                model_uses=None,
                suitability=suitability,
                memory_gb=None,
                note="",
            ),
        )
        assert _fit_words(step) == words
    spilling = PlannedStep(
        stage="correction",
        provider="ollama",
        model="m",
        rating=ModelRating(
            tag="m",
            task="general",
            model_uses=None,
            suitability=Suitability.SLOW,
            memory_gb=None,
            note="",
            very_slow=True,
        ),
    )
    assert _fit_words(spilling) == FIT_VERY_SLOW


def test_the_fit_wording_is_empty_without_a_rating() -> None:
    """
    Given  a step that runs no model of ours
    When   the fit wording is asked for
    Then   there is none, so the page says nothing about a fit
    """
    assert _fit_words(PlannedStep(stage="ocr", provider="ppocr", model="")) == ""


def test_the_proofreading_page_offers_not_now_only_with_handwriting(qtbot) -> None:
    """
    Given  typed texts, where nothing has to be read
    When   the proofreading page is shown
    Then   "Not now" is hidden: there is no reading to fall back on
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=False))
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    assert not page._off_holder.isVisible()
    assert page.place() is ProofreadingPlace.LOCAL


def test_the_proofreading_page_offers_not_now_with_handwriting(qtbot) -> None:
    """
    Given  handwritten pages
    When   the proofreading page is shown
    Then   "Not now — only read the text" is offered, and choosing it says so
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=True))
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    assert page._off_holder.isVisibleTo(page)
    page._off.setChecked(True)
    assert page.place() is ProofreadingPlace.OFF


def test_the_online_section_offers_no_ollama(qtbot) -> None:
    """
    Given  the proofreading page
    When   the online option is chosen
    Then   the service list has no Ollama entry, because the local AI is the other option
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    page._online.setChecked(True)
    section = page._section
    assert section is not None
    offered = [section._provider.itemData(index) for index in range(section._provider.count())]
    assert "ollama" not in offered
    assert "openai" in offered


def test_install_now_installs_the_package_through_the_round(qtbot, monkeypatch) -> None:
    """
    Given  an online service whose package is missing
    When   "Install now" is pressed
    Then   the round installs that package, so the key can be checked afterwards
    """
    service = SetupAppService()
    service.missing["openai"] = "langchain-openai"
    assistant = _assistant(qtbot, _context(service))
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(
        assistant.install_round(), "start", lambda **kwargs: started.append(kwargs) or True
    )
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    page._install_package(["provider:openai"], None)
    assert started == [{"packages": ["langchain-openai"]}]


def test_install_now_does_nothing_when_the_package_is_there(qtbot, monkeypatch) -> None:
    """
    Given  an online service whose package is installed
    When   "Install now" is pressed
    Then   no round starts, because there is nothing to install
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    started: list[dict[str, Any]] = []
    monkeypatch.setattr(
        assistant.install_round(), "start", lambda **kwargs: started.append(kwargs) or True
    )
    _page(assistant, SetupPageId.PROOFREADING)._install_package(["provider:openai"], None)
    assert started == []


def test_the_suggestion_names_the_model_and_its_fit(qtbot) -> None:
    """
    Given  a plan whose proofreading model is not downloaded yet
    When   the suggestion arrives
    Then   the page names the model, how it fits and how large the download is
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    page = _page(assistant, SetupPageId.PROOFREADING)
    step = PlannedStep(
        stage="correction",
        provider="ollama",
        model=DEFAULT_OCR_MODEL,
        rating=ModelRating(
            tag=DEFAULT_OCR_MODEL,
            task="general",
            model_uses=None,
            suitability=Suitability.BEST,
            memory_gb=None,
            note="",
        ),
    )
    plan = SetupPlan(
        settings={},
        panel={},
        steps=(step,),
        downloads=(),
        review_kept=False,
        disk=DiskVerdict.ENOUGH,
    )
    page._on_suggestion(plan, None)
    assert page._suggestion.text() == SUGGESTION.format(
        model=DEFAULT_OCR_MODEL, fit=FIT_WORDS[Suitability.BEST], size="5.5 GB download"
    )


def test_the_suggestion_says_when_the_model_is_already_there(qtbot) -> None:
    """
    Given  a plan whose proofreading model is installed
    When   the suggestion arrives
    Then   the page says it is already downloaded, so nothing is promised twice
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    page = _page(assistant, SetupPageId.PROOFREADING)
    step = PlannedStep(
        stage="correction",
        provider="ollama",
        model=DEFAULT_OCR_MODEL,
        rating=ModelRating(
            tag=DEFAULT_OCR_MODEL,
            task="general",
            model_uses=None,
            suitability=Suitability.GOOD,
            memory_gb=None,
            note="",
        ),
        installed=True,
    )
    plan = SetupPlan(
        settings={},
        panel={},
        steps=(step,),
        downloads=(),
        review_kept=False,
        disk=DiskVerdict.ENOUGH,
    )
    page._on_suggestion(plan, None)
    assert page._suggestion.text() == SUGGESTION_DOWNLOADED.format(
        model=DEFAULT_OCR_MODEL, fit=FIT_WORDS[Suitability.GOOD]
    )


# ── Names and personal information ────────────────────────────────────────────


def test_the_privacy_page_starts_ticked(qtbot) -> None:
    """
    Given  "Remove personal info" is off
    When   the privacy page is shown
    Then   the box is ticked in advance, because text is about to go online
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), anonymization_enabled=False))
    page = _page(assistant, SetupPageId.PRIVACY)
    page.initializePage()
    assert page.remove_names()
    assert page._remove.isEnabled()
    assert not page._already.isVisibleTo(page)


def test_the_privacy_page_says_already_on_when_it_is(qtbot) -> None:
    """
    Given  "Remove personal info" is on already
    When   the privacy page is shown
    Then   the box is ticked and disabled, with "Already on." under it: nothing is turned off
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), anonymization_enabled=True))
    page = _page(assistant, SetupPageId.PRIVACY)
    page.initializePage()
    assert page.remove_names()
    assert not page._remove.isEnabled()
    assert page._already.isVisibleTo(page)
    assert page._already.text() == PRIVACY_ALREADY_ON


def test_the_privacy_page_names_the_service_and_what_name_detection_needs(qtbot) -> None:
    """
    Given  proofreading at OpenAI, whose name detection is not installed
    When   the privacy page is shown
    Then   it names the service and says the detection model joins the downloads
    """
    service = SetupAppService()
    service.anonymizer_ready = False
    assistant = _assistant(qtbot, _context(service, correction_provider="openai"))
    _choose_online(assistant)
    page = _page(assistant, SetupPageId.PRIVACY)
    page.initializePage()
    assert "OpenAI (ChatGPT)" in page._sent.text()
    assert page._detection.text() == PRIVACY_NEEDS_DETECTION.format(language="English")


def test_the_privacy_page_says_when_name_detection_is_installed(qtbot) -> None:
    """
    Given  name detection that is installed for the document language
    When   the privacy page is shown
    Then   it says so, instead of promising a download that will not happen
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), correction_provider="openai"))
    _choose_online(assistant)
    page = _page(assistant, SetupPageId.PRIVACY)
    page.initializePage()
    assert page._detection.text() == PRIVACY_HAS_DETECTION.format(language="English")


def test_the_privacy_page_uses_the_shared_step_name(qtbot) -> None:
    """
    Given  the privacy page
    When   it is built
    Then   its checkbox says what the main window's checkbox says
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    page = _page(assistant, SetupPageId.PRIVACY)
    assert page._remove.text() == txt.REMOVE_PERSONAL_INFO


# ── The answers the pages hand back ───────────────────────────────────────────


def test_the_answers_follow_the_pages(qtbot) -> None:
    """
    Given  a teacher who chose German, typed texts and proofreading at OpenAI
    When   the assistant collects the answers
    Then   they carry every choice, and the local AI page counts as not shown
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=False))
    classes = _page(assistant, SetupPageId.CLASSES)
    classes._language.setCurrentText("German")
    proofreading = _page(assistant, SetupPageId.PROOFREADING)
    proofreading.initializePage()
    proofreading._online.setChecked(True)
    answers = assistant.answers()
    assert answers.language == "German"
    assert answers.hand_in is HandIn.TYPED
    assert answers.proofreading is ProofreadingPlace.ONLINE
    assert not answers.local_ai_shown


def test_the_local_ai_page_counts_as_shown_with_handwriting(qtbot) -> None:
    """
    Given  handwritten pages
    When   the assistant collects the answers
    Then   the local AI page counts as shown, so its address and profile are written
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=True))
    assert assistant.answers().local_ai_shown


def test_the_local_ai_page_counts_as_shown_for_local_proofreading(qtbot) -> None:
    """
    Given  typed texts whose proofreading runs on the local AI
    When   the assistant collects the answers
    Then   the local AI page counts as shown, because the path goes through it
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=False))
    assert assistant.answers().local_ai_shown


def test_the_answers_are_a_mapping_of_the_settings(qtbot) -> None:
    """
    Given  a context whose settings are a plain dict
    When   the assistant is built
    Then   it never writes into them: the context is the main window's copy
    """
    values: Mapping[str, Any] = dict(DEFAULTS)
    before = dict(values)
    context = SetupContext(settings=values, app_service=SetupAppService())
    _assistant(qtbot, context)
    assert dict(values) == before


@pytest.mark.parametrize("language", ["German", "English"])
def test_the_classes_page_offers_every_language(qtbot, language: str) -> None:
    """
    Given  a document language
    When   the classes page is built
    Then   the combo shows it, so the teacher can change it without leaving the assistant
    """
    assistant = _assistant(
        qtbot, _context(SetupAppService(), start_language=language, first_start=True)
    )
    page = _page(assistant, SetupPageId.CLASSES)
    assert page._language.currentText() == language


# ── Ready to set up ───────────────────────────────────────────────────────────


def _plan(
    *steps: PlannedStep,
    downloads: tuple[PlannedDownload, ...] = (),
    **fields: Any,
) -> SetupPlan:
    """Return a setup plan with *steps* and *downloads* and nothing else."""
    values: dict[str, Any] = {
        "settings": {},
        "panel": {"language": "English", "recognition_enabled": True},
        "steps": steps,
        "downloads": downloads,
        "review_kept": False,
        "disk": DiskVerdict.ENOUGH,
    }
    values.update(fields)
    return SetupPlan(**values)


def _rows(plan: SetupPlan, *, key_missing: Collection[str] = ()) -> list[SummaryRow]:
    """Return the summary's lines for *plan*, with every place named "this computer"."""
    return summary_rows(plan, lambda _provider: PLACE_THIS_COMPUTER, key_missing=key_missing)


def _step(
    stage: str, provider: str = "ollama", model: str = "gemma4:12b", **fields: Any
) -> PlannedStep:
    """Return one planned step of *stage*."""
    return PlannedStep(stage=stage, provider=provider, model=model, **fields)


def _download(stage: str, name: str = "gemma4:12b", **fields: Any) -> PlannedDownload:
    """Return one planned Ollama download for *stage*."""
    return PlannedDownload(
        kind=DownloadKind.OLLAMA_MODEL, name=name, stage=stage, size_gb=8.0, **fields
    )


def test_the_summary_starts_with_what_the_students_hand_in() -> None:
    """
    Given  a plan for handwritten German pages
    When   the summary's lines are worked out
    Then   the first line says the language and that the pages are handwritten
    """
    plan = _plan(_step("correction"), panel={"language": "German", "recognition_enabled": True})
    assert _rows(plan)[0] == SummaryRow(
        name=CLASSES_NAME, detail=ROW_CLASSES.format(language="German", hand_in=HANDWRITTEN)
    )


def test_the_summary_says_typed_texts_when_recognition_is_off() -> None:
    """
    Given  a plan whose text recognition is off
    When   the summary's lines are worked out
    Then   the first line says the students hand in typed texts
    """
    plan = _plan(_step("correction"), panel={"language": "English", "recognition_enabled": False})
    assert _rows(plan)[0].detail == ROW_CLASSES.format(language="English", hand_in=TYPED)


def test_the_summary_names_the_model_and_where_it_runs() -> None:
    """
    Given  a plan whose proofreading runs on this computer
    When   the summary's lines are worked out
    Then   the row names the model and the place, so the teacher sees both at once
    """
    plan = _plan(_step("correction", model="gemma4:12b"))
    assert _rows(plan)[1] == SummaryRow(
        name=STEP_NAMES["correction"],
        detail=f"gemma4:12b · {PLACE_THIS_COMPUTER}",
        downloads=(),
        note="",
    )


def test_the_summary_says_when_the_review_uses_the_proofreading_model() -> None:
    """
    Given  a plan whose content review runs the same model as the proofreading
    When   the summary's lines are worked out
    Then   the review's row says so instead of naming the model twice
    """
    plan = _plan(_step("correction"), _step("evaluation"))
    assert _rows(plan)[2].detail == ROW_SAME_MODEL


def test_the_summary_says_when_a_step_keeps_what_it_had() -> None:
    """
    Given  a plan whose reading keeps the built-in reader and whose review keeps its own model
    When   the summary's lines are worked out
    Then   both rows say what is kept (the reader by its name), so nothing looks replaced
    """
    plan = _plan(
        _step("ocr", provider="ppocr", model="", kept=True),
        _step("evaluation", kept=True),
    )
    rows = _rows(plan)
    assert rows[1] == SummaryRow(
        name=STEP_NAMES["ocr"], detail=ROW_KEPT.format(engine="Built-in reader"), note=ROW_INSTALLED
    )
    assert rows[2] == SummaryRow(
        name=STEP_NAMES["evaluation"], detail=ROW_KEEPS_OWN, note=ROW_INSTALLED
    )


def test_the_summary_lists_the_reader_download_in_megabytes() -> None:
    """
    Given  a plan that keeps the built-in reader and downloads its model files
    When   the summary's lines are worked out
    Then   the reading row names the reader, lists one download of "31 MB" and has no "installed"
    """
    from teachers_teammate.gui.setup._summary import size_text  # noqa: PLC0415

    reader = PlannedDownload(
        kind=DownloadKind.READER_MODEL, name="ppocr", stage="ocr", size_gb=0.031
    )
    plan = _plan(
        _step("ocr", provider="ppocr", model="PP-OCRv6 small", kept=True), downloads=(reader,)
    )

    row = _rows(plan)[1]

    assert row.detail == ROW_KEPT.format(engine="Built-in reader")
    assert row.downloads == (reader,)
    assert row.note == ""
    assert size_text(reader) == "31 MB"


def test_the_summary_says_when_a_step_is_off() -> None:
    """
    Given  a plan whose proofreading is not set up
    When   the summary's lines are worked out
    Then   the row says it is off and where to turn it on, so nothing is promised
    """
    plan = _plan(_step("correction", provider="", model=""))
    assert _rows(plan)[1] == SummaryRow(name=STEP_NAMES["correction"], detail=ROW_OFF)


def test_the_summary_says_when_a_model_is_already_downloaded() -> None:
    """
    Given  a plan whose model is installed and needs no download
    When   the summary's lines are worked out
    Then   the row says it is downloaded, so the teacher knows nothing is fetched
    """
    plan = _plan(_step("correction", installed=True))
    assert _rows(plan)[1].note == ROW_DOWNLOADED


def test_the_summary_says_when_a_key_is_missing() -> None:
    """
    Given  a plan whose proofreading runs at an online service with no key
    When   the summary's lines are worked out
    Then   the row says the key is missing, so the teacher is not surprised later
    """
    plan = _plan(_step("correction", provider="openai", model="gpt-4o-mini"))
    assert _rows(plan, key_missing={"openai"})[1].note == ROW_KEY_MISSING


def test_the_summary_puts_every_download_on_its_own_line() -> None:
    """
    Given  a plan with two downloads for one step
    When   the rows are drawn
    Then   each download has its own ticked box, so either can be left out
    """
    rows = SummaryRows()
    rows.set_rows(
        _rows(
            _plan(
                _step("correction"),
                downloads=(
                    _download("correction", "gemma4:12b"),
                    _download("correction", "qwen3:8b"),
                ),
            )
        )
    )
    assert [box.text() for box in rows._boxes.values()] == [SIZE_GB.format(size=8.0)] * 2
    assert all(box.isChecked() for box in rows._boxes.values())
    assert [item.name for item in rows.downloads()] == ["gemma4:12b", "qwen3:8b"]


def test_the_summary_hands_back_only_the_ticked_downloads() -> None:
    """
    Given  a plan with two downloads
    When   the teacher unticks one
    Then   only the other is handed to the install round
    """
    rows = SummaryRows()
    rows.set_rows(
        _rows(
            _plan(
                _step("correction"),
                downloads=(
                    _download("correction", "gemma4:12b"),
                    _download("correction", "qwen3:8b"),
                ),
            )
        )
    )
    rows._boxes["qwen3:8b"].setChecked(False)
    assert [item.name for item in rows.downloads()] == ["gemma4:12b"]


def test_the_summary_says_what_the_downloads_add_up_to(qtbot) -> None:
    """
    Given  a plan that needs 13.5 GB with 200 GB free
    When   the setup plan arrives on the summary page
    Then   the line under the rows says both figures, so the teacher can judge the space
    """
    assistant, page = _summary(qtbot, SetupAppService())
    _show_plan(page, assistant)
    assert page._total.text() == SUMMARY_DOWNLOADS_TOTAL.format(needed=13.5, free=200.0)


def test_the_summary_says_nothing_to_download_when_there_is_nothing(qtbot) -> None:
    """
    Given  a plan whose models are all installed
    When   the setup plan arrives on the summary page
    Then   the line says there is nothing to download
    """
    assistant, page = _summary(qtbot, SetupAppService())
    page._on_plan(_plan(_step("correction", installed=True)), None)
    assert page._total.text() == SUMMARY_NO_DOWNLOADS


def test_the_summary_warns_when_the_disk_is_short(qtbot) -> None:
    """
    Given  a plan whose downloads do not fit on the disk
    When   the setup plan arrives on the summary page
    Then   the warning names both figures, so the teacher can free space or untick a download
    """
    assistant, page = _summary(qtbot, SetupAppService())
    page._on_plan(
        _plan(
            _step("correction"),
            downloads=(_download("correction"),),
            disk=DiskVerdict.NOT_ENOUGH,
            needed_gb=13.5,
            free_disk_gb=4.0,
        ),
        None,
    )
    assert page._disk.isVisibleTo(page)
    assert page._disk.text() == SUMMARY_DISK_SHORT.format(free=4.0, needed=13.5)


def test_the_summary_says_when_ollama_is_not_running(qtbot) -> None:
    """
    Given  a plan whose Ollama model cannot be pulled because Ollama is not running
    When   the setup plan arrives on the summary page
    Then   the page says the main window offers it later, so the gap is explained
    """
    assistant, page = _summary(qtbot, SetupAppService())
    page._on_plan(
        _plan(_step("correction"), downloads=(_download("correction", possible=False),)), None
    )
    assert page._stopped.isVisibleTo(page)


def test_the_summary_says_the_setup_plan_could_not_be_worked_out(qtbot) -> None:
    """
    Given  a summary page whose plan failed
    When   the failure arrives
    Then   the page says so and points at Settings, and Set up stays disabled
    """
    assistant, page = _summary(qtbot, SetupAppService())
    page._on_plan(None, RuntimeError("no facts"))
    assert page._headline.text() == SUMMARY_FAILED
    assert not page.isComplete()


def test_the_summary_says_where_an_online_service_runs(qtbot) -> None:
    """
    Given  a plan whose proofreading runs at OpenAI
    When   the summary names that place
    Then   it names the service and its region, as the main window's lines do
    """
    assistant, page = _summary(qtbot, SetupAppService())
    assert page._place_of("openai") == f"OpenAI (ChatGPT) · {DataRegion.NON_EU.label}"


def test_the_summary_says_where_ollama_runs(qtbot) -> None:
    """
    Given  the default local AI server address
    When   the summary names that place
    Then   it says "this computer", because the address is a local one
    """
    assistant, page = _summary(qtbot, SetupAppService())
    assert page._place_of("ollama") == PLACE_THIS_COMPUTER


# ── All set ───────────────────────────────────────────────────────────────────


class _FakeSpeed(QWidget):
    """Stands in for the speed panel: records what the done page asks of it."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(kwargs.get("parent"))
        self.kwargs = kwargs
        self.refreshed = 0
        self.stopped = 0

    def refresh(self) -> None:
        """Count a redraw."""
        self.refreshed += 1

    def shutdown(self) -> None:
        """Count a shutdown."""
        self.stopped += 1


class _FakePullManager(QObject):
    """Stands in for the main window's download manager."""

    started = Signal(str)
    progress = Signal(str, float, float)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.queued: list[str] = []


def _done(qtbot, service: SetupAppService, manager: _FakePullManager | None = None):
    """Return the assistant, its summary page and its done page over *service*."""
    values: dict[str, Any] = dict(DEFAULTS)
    context = SetupContext(
        settings=values, app_service=service, pull_manager=manager or _FakePullManager()
    )
    assistant = _assistant(qtbot, context)
    return (
        assistant,
        _page(assistant, SetupPageId.SUMMARY),
        _page(assistant, SetupPageId.DONE),
    )


def test_not_now_and_the_other_places_exclude_each_other(qtbot) -> None:
    """
    Given  handwritten pages, where "Not now" sits in its own indented row
    When   "Not now" and then "With the AI on this computer" are clicked
    Then   only the last one is chosen, so Set up never turns proofreading off by mistake
    """
    assistant = _assistant(qtbot, _context(SetupAppService(), recognition_enabled=True))
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()

    page._off.setChecked(True)
    page._local.setChecked(True)

    assert not page._off.isChecked()
    assert page.place() is ProofreadingPlace.LOCAL


def test_the_review_note_shows_on_the_first_visit(qtbot) -> None:
    """
    Given  saved settings whose content review has a model of its own
    When   the proofreading page is shown for the first time, before any plan exists
    Then   it says that the review keeps its own model
    """
    assistant = _assistant(
        qtbot,
        _context(SetupAppService(), evaluate_provider="openai", correction_provider="ollama"),
    )
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    assert page._review_note.isVisibleTo(page)


def test_the_review_note_stays_hidden_without_an_own_review_model(qtbot) -> None:
    """
    Given  settings where the review uses the proofreading model
    When   the proofreading page is shown
    Then   the note stays hidden
    """
    assistant = _assistant(qtbot, _context(SetupAppService()))
    page = _page(assistant, SetupPageId.PROOFREADING)
    page.initializePage()
    assert not page._review_note.isVisibleTo(page)


def _fake_speed(monkeypatch) -> None:
    """Make the done page build a recording stand-in instead of the real speed panel."""
    monkeypatch.setattr("teachers_teammate.gui.setup._pages.SpeedPanel", _FakeSpeed)
    monkeypatch.setattr(
        "teachers_teammate.gui.setup._pages.HardwareProfileSelector",
        lambda **_kwargs: QWidget(),
    )


def test_the_done_page_lists_the_downloads_it_started(qtbot, monkeypatch) -> None:
    """
    Given  a plan with two downloads
    When   the done page is shown
    Then   each download has a line, and each says it is still waiting
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    _show_plan(summary, assistant)

    done.initializePage()

    assert list(done._rows._labels) == ["hf.co/prithivMLmods/chandra-ocr-2-GGUF:Q8_0", "gemma4:12b"]
    assert [label.text() for label in done._rows._labels.values()] == [DONE_WAITING] * 2


def test_the_done_page_follows_the_install_round(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose round is installing a package
    When   the round starts, finishes and fails an item
    Then   the line says so, so the teacher sees the progress without the log
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    _show_plan(summary, assistant)
    done.initializePage()
    round_ = assistant.install_round()

    round_.item_started.emit("package:langchain-openai")
    assert done._rows._state["langchain-openai"] == DONE_RUNNING
    round_.item_done.emit("package:langchain-openai")
    assert done._rows._state["langchain-openai"] == DONE_DONE
    round_.failed.emit("spacy:de_core_news_sm", "no network")
    assert done._rows._state["de_core_news_sm"] == DONE_FAILED.format(message="no network")


def test_the_done_page_follows_a_pull_in_gigabytes(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose Ollama model is being pulled
    When   the manager reports how far it is, in bytes
    Then   the line says how many gigabytes are done, so the figure is readable
    """
    _fake_speed(monkeypatch)
    manager = _FakePullManager()
    assistant, summary, done = _done(qtbot, SetupAppService(), manager)
    _show_plan(summary, assistant)
    done.initializePage()

    manager.started.emit("gemma4:12b")
    # What Ollama reports is a status text, not the model: the row is the one that started.
    manager.progress.emit("pulling sha256:0a1b2c", 4_000_000_000.0, 8_000_000_000.0)

    assert done._rows._labels["gemma4:12b"].text() == DONE_PROGRESS.format(done=4.0, total=8.0)


def test_the_done_page_says_why_a_pull_failed(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose Ollama model is being pulled
    When   the manager reports an error
    Then   every running line says why, because the manager names no model
    """
    _fake_speed(monkeypatch)
    manager = _FakePullManager()
    assistant, summary, done = _done(qtbot, SetupAppService(), manager)
    _show_plan(summary, assistant)
    done.initializePage()
    manager.started.emit("gemma4:12b")

    manager.error.emit("connection lost")

    assert done._rows._labels["gemma4:12b"].text() == DONE_FAILED.format(message="connection lost")


def test_the_done_page_says_when_an_ollama_download_goes_on_afterwards(qtbot, monkeypatch) -> None:
    """
    Given  a plan whose downloads include an Ollama model
    When   the done page is shown
    Then   it says the pull goes on after the window closes, so the teacher is not surprised
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    _show_plan(summary, assistant)

    done.initializePage()

    assert done._goes_on.isVisibleTo(done)


def test_the_done_page_offers_the_speed_test_on_the_plan_s_settings(qtbot, monkeypatch) -> None:
    """
    Given  a plan that leaves a local AI server address behind
    When   the done page is shown
    Then   the speed test is built on that address, so it measures the server just set up
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    plan = _show_plan(summary, assistant)

    done.initializePage()

    assert done._speed is not None
    assert done._speed.kwargs["ollama_url"] == plan.settings["ollama_url"]


def test_the_speed_test_is_drawn_again_when_the_queue_runs_empty(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose speed test was drawn before the models arrived
    When   the last pull finishes
    Then   the test is drawn again, so the new models can be measured
    """
    _fake_speed(monkeypatch)
    manager = _FakePullManager()
    assistant, summary, done = _done(qtbot, SetupAppService(), manager)
    _show_plan(summary, assistant)
    done.initializePage()
    done._speed.refreshed = 0

    manager.finished.emit("gemma4:12b")

    assert done._speed.refreshed == 1


def test_the_speed_test_waits_while_more_pulls_are_queued(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose manager still has models queued
    When   one pull finishes
    Then   the test is not drawn again yet, so it does not measure a half-downloaded set
    """
    _fake_speed(monkeypatch)
    manager = _FakePullManager()
    assistant, summary, done = _done(qtbot, SetupAppService(), manager)
    _show_plan(summary, assistant)
    done.initializePage()
    done._speed.refreshed = 0
    manager.queued.append("qwen3:8b")

    manager.finished.emit("gemma4:12b")

    assert done._speed.refreshed == 0


def test_select_documents_closes_the_window_and_asks_for_the_folder(qtbot, monkeypatch) -> None:
    """
    Given  the done page
    When   "Select documents…" is pressed
    Then   the window finishes and the main window is asked to choose the folder
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    _show_plan(summary, assistant)
    done.initializePage()
    assistant.show()

    done._select.click()

    assert assistant.documents_wanted()
    assert assistant.result() == QDialog.DialogCode.Accepted


def test_the_done_page_stops_the_speed_test_when_the_window_closes(qtbot, monkeypatch) -> None:
    """
    Given  a done page whose speed test is running
    When   the window closes
    Then   the test is stopped, so no measurement answers into a destroyed window
    """
    _fake_speed(monkeypatch)
    assistant, summary, done = _done(qtbot, SetupAppService())
    _show_plan(summary, assistant)
    done.initializePage()

    done.shutdown()

    assert done._speed.stopped == 1


def test_the_done_page_follows_the_reader_download_in_megabytes(qtbot) -> None:
    """
    Given  a download list with the built-in reader
    When   the round starts it, reports how far it is, and finishes it
    Then   the reader's line says running, then megabytes (not "0.0 of 0.0 GB"), then done
    """
    from teachers_teammate.gui.setup._pages import DONE_PROGRESS_MB, _DownloadRows  # noqa: PLC0415

    rows = _DownloadRows()
    qtbot.addWidget(rows)
    reader = PlannedDownload(kind=DownloadKind.READER_MODEL, name="ppocr", stage="ocr")
    rows.set_downloads([reader])

    rows.started("reader:ppocr")
    assert rows._state["ppocr"] == DONE_RUNNING
    rows.pull_progress("Built-in reader", 12_000_000.0, 31_000_000.0)
    assert rows._labels["ppocr"].text() == DONE_PROGRESS_MB.format(done=12, total=31)
    rows.done("reader:ppocr")

    assert rows._labels["ppocr"].text() == DONE_DONE
