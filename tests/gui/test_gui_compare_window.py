"""GUI tests for the compare window: its models, its tabs, its footer and the ways in."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
import threading
from typing import cast

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QAbstractButton, QPushButton, QTableWidget

from teachers_teammate.application.model_comparison import (
    Candidate,
    ComparedRun,
    CompareSource,
    JobHooks,
    ModelComparisonService,
)
from teachers_teammate.config import Config
from teachers_teammate.domain.data_location import DataLocation, StageLocation
from teachers_teammate.domain.formatted_text import PLAIN
from teachers_teammate.domain.model_catalog import Suitability
from teachers_teammate.domain.proofreading.compared_result import ComparedResult
from teachers_teammate.domain.proofreading.edits import edits_from_hunks
from teachers_teammate.domain.proofreading.model_comparison import (
    Comparison,
    Option,
    Version,
    compare,
)
from teachers_teammate.domain.proofreading.option_marks import OptionMark, Verdict, identity
from teachers_teammate.domain.proofreading.review import CorrectionReview, EditNote
from teachers_teammate.domain.proofreading.sentences import sentence_spans
from teachers_teammate.domain.proofreading.word_diff import word_diff
from teachers_teammate.gui import _dialogs, _handles
from teachers_teammate.gui._flow_confirm import confirm_comparison_flow
from teachers_teammate.gui.compare._window import CompareHooks, CompareWindow
from teachers_teammate.messages import detail, model_not_compared
from teachers_teammate.texts import compare as txt
from teachers_teammate.texts.models import compact_suitability_text
from teachers_teammate.texts._types import Answer, Question
from tests.conftest import make_config

from ._gui_harness import main_window, no_network_in_gui_tests, stored

#: What the student wrote, and what the two models make of one word each.
SOURCE_TEXT = "das ist ein hund .\nDer hund ist klein ."
FIRST_TEXT = "das ist ein Hund .\nDer hund ist klein ."
SECOND_TEXT = "das ist ein hund .\nDer Hund ist klein ."

#: How long each scripted answer says it took.
FIRST_SECONDS = 2.0
SECOND_SECONDS = 3.5

#: What the first model says about the noun it capitalised.
EXPLANATION = "Nomen werden grossgeschrieben."


class _Service:
    """The comparison service, with the test deciding what each model answers.

    Only what the window asks of it: the text, the models, one model's answer and the
    comparison of what has been stored.  What a real answer costs and where it is stored is
    the service's own business, tested where it lives.
    """

    def __init__(
        self,
        source: CompareSource,
        candidates: tuple[Candidate, ...],
        plan: dict,
        *,
        held: tuple[Candidate, ...] | None = None,
    ) -> None:
        self._source = source
        self._candidates = candidates
        self._plan = plan
        #: What the app holds before the lists are asked for again; ``None``: the same models.
        self._held = held
        #: How often the model lists were asked for again.
        self.refreshed = 0
        self.asked: list[str] = []
        self.results: dict[str, ComparedResult] = {}
        self.repeated: dict[str, list[ComparedResult]] = {}
        self.marked: list[tuple[str, Verdict | None]] = []
        self._marks: list[OptionMark] = []

    def source(self, config: Config, path: Path) -> CompareSource | None:
        """Return the text every model is asked about, whatever the path."""
        return self._source

    def candidates(self, config: Config, source: CompareSource) -> tuple[Candidate, ...]:
        """Return the models the window lists: the held ones until the lists were asked for."""
        if self._held is not None and not self.refreshed:
            return self._held
        return self._candidates

    def refresh(self, config: Config) -> None:
        """Ask for the model lists again, as the window does off its thread."""
        self.refreshed += 1

    def marks(self, source: CompareSource) -> tuple[OptionMark, ...]:
        """Return the verdicts the teacher has given on this text so far."""
        return tuple(self._marks)

    def set_mark(self, source: CompareSource, option: Option, verdict: Verdict | None) -> None:
        """Record the teacher's verdict on one option of this text, or take it back."""
        self.marked.append((option.replacement, verdict))
        judged = identity(source.text_sha, option.start, option.end, option.replacement)
        self._marks = [mark for mark in self._marks if mark.identity != judged]
        if verdict is not None:
            self._marks.append(
                OptionMark(
                    text_sha=source.text_sha,
                    start=option.start,
                    end=option.end,
                    replacement_sha256=judged[3],
                    verdict=verdict,
                )
            )

    def comparison(self, source: CompareSource, keys: Collection[str]) -> Comparison:
        """Return what the answers stored so far differ in, marked as the teacher marked it."""
        return compare(
            source.plain,
            [
                replace(
                    result.version(),
                    repeats=tuple(one.text for one in self.repeated.get(result.key, ())),
                )
                for result in self.results.values()
            ],
            text_sha=source.text_sha,
            marks=self.marks(source),
        )

    def next_repeat(self, source: CompareSource, candidate: Candidate) -> int:
        """Return the run number of the next answer: one past those stored."""
        return 1 + len(self.repeated.get(candidate.key, ()))

    def run(
        self,
        config: Config,
        source: CompareSource,
        candidates: Sequence[Candidate],
        *,
        repeat: int = 0,
        hooks: JobHooks | None = None,
    ) -> tuple[ComparedRun, ...]:
        """Ask the models in order, as the service does: a stop ends the job between two."""
        hooks = hooks or JobHooks()
        attempts: list[ComparedRun] = []
        for candidate in candidates:
            if hooks.stop_event is not None and hooks.stop_event.is_set():
                break
            if hooks.on_start is not None:
                hooks.on_start(candidate)
            ran = self._one(candidate, repeat, hooks.stop_event)
            attempts.append(ran)
            if hooks.on_answer is not None:
                hooks.on_answer(ran)
            if ran.stopped:
                break
        return tuple(attempts)

    def _one(
        self, candidate: Candidate, repeat: int, stop_event: threading.Event | None
    ) -> ComparedRun:
        """Answer as this test scripted it: an answer, a failure, or a stop that stores nothing."""
        self.asked.append(candidate.key)
        plan = self._plan.get(candidate.key, "answer")
        if plan == "wait":  # a model that answers until the teacher presses Stop
            assert stop_event is not None
            stop_event.wait(10)
            return ComparedRun(candidate=candidate, stopped=True)
        if plan == "error":  # a failure that ends the whole job, not one model's row
            raise RuntimeError("the provider cannot be loaded")
        if plan == "stop":
            return ComparedRun(candidate=candidate, stopped=True)
        if plan == "failed":
            problem = model_not_compared(candidate.model, detail("the server refused it"))
            return ComparedRun(candidate=candidate, failure=problem)
        text, seconds = {
            "k-first": (FIRST_TEXT, FIRST_SECONDS),
            "k-second": (SECOND_TEXT, SECOND_SECONDS),
        }[candidate.key]
        result = _result(candidate, text=text, seconds=seconds)
        if repeat:
            self.repeated.setdefault(candidate.key, []).append(result)
        else:
            self.results[candidate.key] = result
        return ComparedRun(candidate=candidate, result=result)


def _review(corrected: str) -> CorrectionReview:
    """Return the review a model would write about its answer *corrected* to the source."""
    found = edits_from_hunks(
        word_diff(SOURCE_TEXT, corrected), SOURCE_TEXT, sentence_spans(SOURCE_TEXT)
    )
    return CorrectionReview(
        edits=tuple(
            EditNote(
                id=edit.id,
                parts=edit.parts,
                sentences=edit.sentences,
                category="grammar",
                explanation=EXPLANATION,
            )
            for edit in found
        )
    )


def _result(candidate: Candidate, *, text: str, seconds: float) -> ComparedResult:
    """Return one stored answer of *candidate*, holding *text* and costing *seconds*."""
    return ComparedResult(
        text_sha="sha-1",
        key=candidate.key,
        provider=candidate.provider,
        model=candidate.model,
        language="German",
        text=text,
        review=_review(text) if candidate.key == "k-first" else None,
        seconds=seconds,
        fell_back=False,
        left_out=0,
        warning="",
        sent=None,
        digest="",
        exchange="",
        app_version="0.0",
        created=0.0,
    )


def _candidate(model: str, key: str) -> Candidate:
    """Return a model of this computer that can answer, rated the best that fits."""
    return Candidate(
        provider="ollama",
        model=model,
        key=key,
        location=StageLocation(DataLocation.THIS_COMPUTER, "Ollama"),
        suitability=Suitability.BEST,
    )


def _source(text_sha: str = "sha-1") -> CompareSource:
    """Return the document every scripted answer is about."""
    return CompareSource(
        text=SOURCE_TEXT,
        plain=SOURCE_TEXT,
        text_format=PLAIN,
        language="German",
        text_sha=text_sha,
        words=len(SOURCE_TEXT.split()),
    )


@dataclass(frozen=True)
class _Open:
    """A window on the scripted comparison, and what it was asked to do."""

    window: CompareWindow
    service: _Service
    said: list[str]
    used: list[tuple[str, str]]
    confirmed: list[tuple[str, ...]]
    """The models of every question about sending the text, in the order they were asked."""


@pytest.fixture
def open_window(qtbot, tmp_path: Path):
    """Return a factory that opens the compare window on the scripted comparison."""

    def _open(
        *,
        plan: dict[str, str] | None = None,
        busy: bool = False,
        agree: bool = True,
        candidates: tuple[Candidate, ...] | None = None,
        source: CompareSource | None = None,
        held: tuple[Candidate, ...] | None = None,
    ) -> _Open:
        service = _Service(
            source or _source(),
            candidates or (_candidate("gemma3:12b", "k-first"), _candidate("qwen3:8b", "k-second")),
            dict(plan or {}),
            held=held,
        )
        said: list[str] = []
        used: list[tuple[str, str]] = []
        confirmed: list[tuple[str, ...]] = []

        def confirm_sending(config: Config, asked: Sequence[Candidate]) -> bool:
            confirmed.append(tuple(candidate.model for candidate in asked))
            return agree

        window = CompareWindow(
            CompareHooks(
                service=cast("ModelComparisonService", service),
                candidates=service.candidates,
                refresh_models=service.refresh,
                config=lambda: make_config(tmp_path),
                documents=lambda: [("a.pdf", "a.pdf"), ("b.pdf", "b.pdf")],
                use_model=lambda provider, model: used.append((provider, model)),
                say=said.append,
                busy=lambda: busy,
                warn_busy=lambda: said.append("busy"),
                confirm_sending=confirm_sending,
            )
        )
        qtbot.addWidget(window)
        window.open_on("a.pdf")
        qtbot.waitUntil(lambda: not window.models_pending() and not window.tables_pending())
        return _Open(window=window, service=service, said=said, used=used, confirmed=confirmed)

    return _open


def _changes(window: CompareWindow) -> QTableWidget:
    """Return the table of the *Changes* tab."""
    return window._changes._table


def _verdict_button(window: CompareWindow, handle: str) -> QPushButton:
    """Return the verdict button *handle* of the row the *Changes* tab shows."""
    layout = window._changes._verdicts
    for index in range(layout.count()):
        row = layout.itemAt(index).widget()
        assert row is not None
        button = row.findChild(QPushButton, handle)
        if button is not None:
            return button
    raise AssertionError(f"no {handle} button under the chosen row")


def _ran(qtbot, window: CompareWindow) -> None:
    """Wait until the window has taken in every answer it asked for.

    The job disables *Run selected* before it starts and the end of the job enables it again, so
    the button says whether the rows and the tables already hold what the models answered; a
    finished thread alone does not, since its signals still wait on the GUI thread.
    """
    qtbot.waitUntil(lambda: window._run_btn.isEnabled())
    qtbot.waitUntil(lambda: not window.tables_pending())


@pytest.mark.gui
def test_models_whose_list_had_expired_are_listed_once_it_is_fetched(open_window) -> None:
    """
    Given  an app whose model lists expired, so it holds no model to offer
    When   the window opens on a document
    Then   it asks for the lists again in the background, and lists the models they hold
    """
    opened = open_window(held=())

    table = opened.window._models._table
    assert opened.service.refreshed == 1
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == [
        "gemma3:12b",
        "qwen3:8b",
    ]


@pytest.mark.gui
def test_a_row_says_the_catalogs_rating_and_an_online_model_says_online(open_window) -> None:
    """
    Given  a local model the catalog rates as working here, one it rates too large, and an
           online model
    When   the window lists them
    Then   each local row says its rating in the catalog's words, the online one says online
    """
    works = replace(_candidate("gemma3:12b", "k-first"), suitability=Suitability.WORKS)
    too_large = replace(_candidate("qwen3:8b", "k-second"), suitability=Suitability.TOO_LARGE)
    online = Candidate(
        provider="mistral",
        model="mistral-large-latest",
        key="k-online",
        location=StageLocation(DataLocation.ONLINE, "Mistral"),
        suitability=Suitability.UNKNOWN,
    )

    opened = open_window(candidates=(works, too_large, online))

    table = opened.window._models._table
    roles = {table.item(row, 0).text(): table.item(row, 1).text() for row in range(3)}
    assert roles == {
        "gemma3:12b": compact_suitability_text(Suitability.WORKS, "general"),
        "qwen3:8b": compact_suitability_text(Suitability.TOO_LARGE, "general"),
        "mistral-large-latest": txt.ONLINE,
    }


@pytest.mark.gui
def test_two_models_fill_the_rows_and_the_columns(open_window, qtbot) -> None:
    """
    Given  a window on a document, with two models ticked and neither of them answered yet
    When   the teacher runs the selected models
    Then   each row says how long its answer took and the three tabs show both answers
    """
    opened = open_window()

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    assert opened.service.asked == ["k-first", "k-second"]
    assert opened.window._models.row_of("k-first").state.text() == txt.done_in(FIRST_SECONDS)
    assert opened.window._models.row_of("k-second").state.text() == txt.done_in(SECOND_SECONDS)
    table = _changes(opened.window)
    assert [table.horizontalHeaderItem(column).text() for column in range(table.columnCount())] == [
        txt.STUDENT_WROTE,
        "gemma3:12b",
        "qwen3:8b",
        txt.AGREE,
    ]
    assert [table.item(row, 0).text() for row in range(table.rowCount())] == ["hund", "hund"]
    assert table.item(0, 1).text() == "Hund"
    assert table.item(0, 2).text() == txt.UNCHANGED
    assert table.item(1, 2).text() == "Hund"
    assert opened.window._overview._table.columnCount() == 2
    assert len(opened.window._side_by_side._views) == 3


@pytest.mark.gui
def test_the_chosen_row_says_what_the_models_explained(open_window, qtbot) -> None:
    """
    Given  a window where one answer explains its change and the other changes nothing there
    When   the teacher picks that row
    Then   the line under the table carries the explanation of the model that made it
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    panel = opened.window._changes
    assert panel._why.text() == txt.WHY_TOOLTIP

    panel._table.setCurrentCell(0, 0)

    assert panel._why.text() == txt.explained_by("gemma3:12b", EXPLANATION, "grammar")


@pytest.mark.gui
def test_a_change_called_right_counts_for_the_model_that_made_it(open_window, qtbot) -> None:
    """
    Given  a window where the first model changed the word the other one left alone
    When   the teacher calls that change right
    Then   the service holds the verdict, the button stays checked, and the Overview counts it
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._changes._table.setCurrentCell(0, 0)
    button = _verdict_button(opened.window, _handles.COMPARE_MARK_RIGHT)
    assert button.text() == txt.MARK_RIGHT

    button.click()
    qtbot.waitUntil(lambda: not opened.window.tables_pending())

    assert opened.service.marked == [("Hund", Verdict.RIGHT)]
    assert _verdict_button(opened.window, _handles.COMPARE_MARK_RIGHT).isChecked()
    overview = opened.window._overview._table
    right = list(txt.OVERVIEW_ROWS).index(txt.RIGHT)
    missed = list(txt.OVERVIEW_ROWS).index(txt.MISSED)
    assert [overview.item(right, column).text() for column in range(2)] == ["1", "0"]
    assert [overview.item(missed, column).text() for column in range(2)] == ["0", "1"]


@pytest.mark.gui
def test_clicking_the_checked_verdict_again_takes_it_back(open_window, qtbot) -> None:
    """
    Given  a change the teacher has already called right
    When   the teacher clicks the same verdict once more
    Then   the verdict is taken back and the Overview counts nothing
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._changes._table.setCurrentCell(0, 0)
    _verdict_button(opened.window, _handles.COMPARE_MARK_RIGHT).click()
    qtbot.waitUntil(lambda: not opened.window.tables_pending())

    _verdict_button(opened.window, _handles.COMPARE_MARK_RIGHT).click()
    qtbot.waitUntil(lambda: not opened.window.tables_pending())

    assert opened.service.marked == [("Hund", Verdict.RIGHT), ("Hund", None)]
    assert not _verdict_button(opened.window, _handles.COMPARE_MARK_RIGHT).isChecked()
    overview = opened.window._overview._table
    right = list(txt.OVERVIEW_ROWS).index(txt.RIGHT)
    assert overview.item(right, 0).text() == "0"


@pytest.mark.gui
def test_a_stopped_model_keeps_its_row_and_stores_nothing(open_window, qtbot) -> None:
    """
    Given  a window with two models ticked, the second of which the teacher stops
    When   the teacher runs them and stops during the second one
    Then   the second row says stopped, nothing of it is stored, and the window is usable again
    """
    opened = open_window(plan={"k-second": "stop"})

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    assert [row.state.text() for row in opened.window._models.rows()] == [
        txt.done_in(FIRST_SECONDS),
        txt.STOPPED,
    ]
    assert opened.service.results.keys() == {"k-first"}
    assert opened.window._line.text() == txt.STOPPED
    assert opened.window._run_btn.isEnabled()
    assert _changes(opened.window).columnCount() == 3


@pytest.mark.gui
def test_a_failed_model_says_why_in_its_row_and_opens_no_dialog(open_window, qtbot) -> None:
    """
    Given  a window whose second model cannot be asked
    When   the two models are run
    Then   its row carries the reason, and the teacher is asked nothing
    """
    opened = open_window(plan={"k-second": "failed"})

    with _dialogs.answering(lambda _asked: None) as asked:
        opened.window.run_selected()
        _ran(qtbot, opened.window)

    assert asked == []
    assert (
        opened.window._models.row_of("k-second")
        .state.text()
        .startswith("Could not compare qwen3:8b")
    )
    assert "the server refused it" in opened.window._models.row_of("k-second").state.text()
    assert opened.service.results.keys() == {"k-first"}


@pytest.mark.gui
def test_use_writes_the_two_settings_keys(open_window, qtbot) -> None:
    """
    Given  a window with an answer from each of two models
    When   the teacher keeps the first one for proofreading and agrees
    Then   its provider and model are written, and the status bar says so
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._models._table.setCurrentCell(0, 0)
    assert opened.window._use_btn.text() == txt.use_model("gemma3:12b")

    with _dialogs.answering(_first_button) as asked:
        opened.window.use_chosen()

    assert [call.call for call in asked] == ["choose"]
    assert opened.used == [("ollama", "gemma3:12b")]
    assert opened.said[-1] == txt.now_proofreading("gemma3:12b")


@pytest.mark.gui
def test_keeping_a_model_asks_first_and_a_refusal_writes_nothing(open_window, qtbot) -> None:
    """
    Given  a window with an answer from a model
    When   the teacher presses *Use … for proofreading* and cancels the question
    Then   no setting is written
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._models._table.setCurrentCell(0, 0)

    with _dialogs.answering(lambda _asked: None):
        opened.window.use_chosen()

    assert opened.used == []


@pytest.mark.gui
def test_run_again_keeps_both_answers_of_the_chosen_model(open_window, qtbot) -> None:
    """
    Given  a window where the chosen model has answered
    When   the teacher presses *Run again*
    Then   the second answer is kept beside the first, and the Overview counts its runs
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._models._table.setCurrentCell(0, 0)

    opened.window.run_again()
    _ran(qtbot, opened.window)

    assert opened.service.asked == ["k-first", "k-second", "k-first"]
    assert len(opened.service.repeated["k-first"]) == 1
    overview = opened.window._overview._table
    in_every_run = list(txt.OVERVIEW_ROWS).index(txt.IN_EVERY_RUN)
    assert [overview.item(in_every_run, column).text() for column in range(2)] == [
        "1",
        txt.UNCHANGED,
    ]


@pytest.mark.gui
def test_a_comparison_started_during_a_run_only_warns(open_window) -> None:
    """
    Given  a window on a document while a run is going
    When   the teacher presses *Run selected*
    Then   the window says the run is busy, and no model is asked
    """
    opened = open_window(busy=True)

    opened.window.run_selected()

    assert opened.service.asked == []
    assert opened.said == ["busy"]


@pytest.mark.gui
def test_the_documents_of_the_header_change_the_text_compared(open_window) -> None:
    """
    Given  a window on one document, with another one listed
    When   the teacher picks the other document
    Then   the window reads the text of that one
    """
    opened = open_window()

    opened.window._document.setCurrentIndex(1)

    assert opened.window._open == "b.pdf"
    assert opened.window._document.currentText() == "b.pdf"


@pytest.mark.gui
def test_the_tools_entry_opens_the_window(main_window) -> None:
    """
    Given  a main window
    When   the teacher picks Tools → *Compare proofreading models…*
    Then   the compare window is shown
    """
    main_window._menu_bar.compare_action.trigger()

    assert main_window._compare.window.isVisible()


@pytest.mark.gui
def test_the_check_tab_opens_the_window_on_its_document(main_window, stored) -> None:
    """
    Given  a proofread document open in the Check tab
    When   the teacher presses *Compare models…* in its header
    Then   the compare window is shown on that document
    """
    source_ids = stored("a.txt", raw="was gelesen wurde", corrected="Was gelesen wurde")
    main_window._check_controller.open_document(source_ids[0])
    button = main_window.findChild(QAbstractButton, _handles.CHECK_COMPARE_BUTTON)
    assert button is not None
    assert not button.isHidden()
    assert not main_window._compare.window.isVisible()

    button.click()

    assert main_window._compare.window.isVisible()
    assert main_window._compare.window._open == source_ids[0]


@pytest.mark.gui
def test_keeping_a_model_writes_it_into_the_settings(main_window, monkeypatch) -> None:
    """
    Given  a main window that proofreads with its own provider and model
    When   the compare window keeps a model for proofreading
    Then   the panel proofreads with that provider and model, and the settings are saved
    """
    saved: list[bool] = []
    monkeypatch.setattr(main_window, "_save_settings", lambda **_kw: saved.append(True))

    main_window._compare.use_model("ollama", "gemma3:12b")

    settings = main_window._config_panel.get_settings_dict()
    assert settings["correction_provider"] == "ollama"
    assert settings["correction_model"] == "gemma3:12b"
    assert saved == [True]


def _first_button(asked: _dialogs.Asked) -> Answer:
    """Answer a question with its first button, as a teacher who goes ahead does."""
    assert isinstance(asked.item, Question)
    return Answer(asked.item.choices[0])


# ── the question before text leaves the computer ───────────────────────────


@pytest.mark.gui
def test_the_teacher_is_asked_before_the_ticked_models_are_asked(open_window, qtbot) -> None:
    """
    Given  a window with two models ticked
    When   the teacher runs the selected models and agrees to the question about sending
    Then   the question named exactly the ticked models, and both were asked
    """
    opened = open_window()

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    assert opened.confirmed == [("gemma3:12b", "qwen3:8b")]
    assert opened.service.asked == ["k-first", "k-second"]


@pytest.mark.gui
def test_a_declined_question_asks_no_model(open_window) -> None:
    """
    Given  a window where the teacher declines the question about sending the text
    When   they press *Run selected*
    Then   no model is asked, nothing is locked and the buttons stay usable
    """
    opened = open_window(agree=False)

    opened.window.run_selected()

    assert opened.confirmed == [("gemma3:12b", "qwen3:8b")]
    assert opened.service.asked == []
    assert not opened.window.running()
    assert opened.window._run_btn.isEnabled()
    assert opened.window._document.isEnabled()


@pytest.mark.gui
def test_running_a_model_again_asks_the_question_too(open_window, qtbot) -> None:
    """
    Given  a window where the chosen model has answered
    When   the teacher presses *Run again*
    Then   the question about sending the text comes again, for that model
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)
    opened.window._models._table.setCurrentCell(0, 0)

    opened.window.run_again()
    _ran(qtbot, opened.window)

    assert opened.confirmed[-1] == ("gemma3:12b",)


@pytest.mark.gui
def test_the_main_window_asks_before_text_goes_to_an_online_model(
    main_window, tmp_path: Path
) -> None:
    """
    Given  a main window and a model of an online provider
    When   a comparison asks to send the text to it, and the teacher declines
    Then   the question about documents leaving the computer was shown and the answer is no
    """
    online = Candidate(
        provider="mistral",
        model="mistral-large-latest",
        key="k-online",
        location=StageLocation(DataLocation.ONLINE, "Mistral"),
        suitability=Suitability.UNKNOWN,
    )

    with _dialogs.answering(lambda asked: None) as asked:
        agreed = confirm_comparison_flow(
            main_window._compare.window,
            main_window._app_service,
            main_window._acknowledgements(),
            make_config(tmp_path),
            [online],
        )

    assert agreed is False
    assert [item.call for item in asked] == ["choose"]


@pytest.mark.gui
def test_the_main_window_does_not_ask_for_a_model_on_this_computer(
    main_window, tmp_path: Path
) -> None:
    """
    Given  a main window and a model of this computer
    When   a comparison asks to send the text to it
    Then   nothing is asked, and the answer is yes
    """
    config = make_config(tmp_path)

    with _dialogs.answering(lambda asked: None) as asked:
        agreed = confirm_comparison_flow(
            main_window._compare.window,
            main_window._app_service,
            main_window._acknowledgements(),
            config,
            [_candidate("gemma3:12b", "k-first")],
        )

    assert agreed is True
    assert asked == []


# ── a comparison and a run exclude each other ──────────────────────────────


@pytest.mark.gui
def test_no_run_starts_while_a_comparison_asks_models(main_window, monkeypatch) -> None:
    """
    Given  a main window whose compare window is asking a model, which counts as a run
    When   the teacher presses Run
    Then   the window says it is busy, and the run's own work is not entered
    """
    monkeypatch.setattr(main_window._compare, "running", lambda: True)
    monkeypatch.setattr(main_window._app_service.run, "busy", lambda: True)
    notices: list[str] = []
    monkeypatch.setattr(main_window._run_controller, "warn_busy", lambda: notices.append("busy"))
    called: list[str] = []
    monkeypatch.setattr(
        main_window._run_start, "_check_requirements_then", lambda *a, **k: called.append("x")
    )

    main_window._run_start.start()

    assert notices == ["busy"]
    assert called == []
    assert main_window._run_controller.busy is False


@pytest.mark.gui
def test_closing_the_window_stops_the_model_being_asked(open_window, qtbot) -> None:
    """
    Given  a window that waits for a model's answer
    When   the window is closed
    Then   the job is stopped and has ended, so nothing asks a model behind a closed window
    """
    opened = open_window(plan={"k-first": "wait", "k-second": "wait"})
    opened.window.run_selected()
    qtbot.waitUntil(lambda: opened.service.asked != [])
    assert opened.window.running()

    opened.window.close()

    assert not opened.window.running()


@pytest.mark.gui
def test_the_document_cannot_change_while_models_are_asked(open_window, qtbot) -> None:
    """
    Given  a window that is asking models about one document, another one listed
    When   the teacher tries to pick the other document, or another document is opened in it
    Then   the header is locked and the window stays on its document until the asking is over
    """
    opened = open_window()

    opened.window.run_selected()

    assert not opened.window._document.isEnabled()
    opened.window.open_on("b.pdf")
    assert opened.window._open == "a.pdf"
    _ran(qtbot, opened.window)
    assert opened.window._document.isEnabled()
    opened.window.open_on("b.pdf")
    assert opened.window._open == "b.pdf"


# ── what a row and a column say ────────────────────────────────────────────


def _own(key: str) -> CompareSource:
    """Return the document with its own proofreading made by the settings of *key*."""
    return replace(_source(), stored=Version(key=key, text=FIRST_TEXT))


@pytest.mark.gui
def test_a_row_says_whose_answer_it_holds(open_window) -> None:
    """
    Given  a model that made the document's own proofreading and one with a stored answer
    When   the window lists the models
    Then   the first says it is the teacher's proofreading, the second only that it answered
    """
    own = replace(_candidate("gemma3:12b", "k-first"), done=True, own=True)
    answered = replace(_candidate("qwen3:8b", "k-second"), done=True)

    opened = open_window(candidates=(own, answered), source=_own("k-first"))

    assert opened.window._models.row_of("k-first").state.text() == txt.YOUR_PROOFREADING
    assert opened.window._models.row_of("k-second").state.text() == txt.ANSWERED


@pytest.mark.gui
def test_the_own_version_is_named_after_the_model_that_made_it(open_window) -> None:
    """
    Given  a document proofread with the model of one row, the settings now on another model
    When   the window names its columns
    Then   the own proofreading carries the name of the model whose key it has
    """
    own = replace(_candidate("qwen3:8b", "k-second"), done=True, own=True)

    opened = open_window(
        candidates=(_candidate("gemma3:12b", "k-first"), own), source=_own("k-second")
    )

    assert opened.window._labels["k-second"] == txt.own_version("qwen3:8b")
    assert opened.window._labels["k-first"] == "gemma3:12b"


@pytest.mark.gui
def test_an_own_version_of_an_unlisted_model_is_not_named_after_another(open_window) -> None:
    """
    Given  a document proofread with settings that no listed model has today
    When   the window names its columns
    Then   the own proofreading is called just that, not by the model now in the settings
    """
    opened = open_window(source=_own("k-gone"))

    assert opened.window._labels["k-gone"] == txt.OWN_VERSION


@pytest.mark.gui
def test_run_again_is_offered_for_an_answer_but_not_for_the_documents_own(open_window) -> None:
    """
    Given  one model whose own proofreading the document holds, and one with a stored answer
    When   the teacher chooses each row in turn
    Then   *Run again* is off for the own proofreading, which is never asked here, and on for
           the other
    """
    own = replace(_candidate("gemma3:12b", "k-first"), done=True, own=True)
    answered = replace(_candidate("qwen3:8b", "k-second"), done=True)
    opened = open_window(candidates=(own, answered), source=_own("k-first"))

    opened.window._models._table.setCurrentCell(0, 0)
    assert not opened.window._again_btn.isEnabled()
    opened.window._models._table.setCurrentCell(1, 0)
    assert opened.window._again_btn.isEnabled()


@pytest.mark.gui
def test_an_answer_without_a_problem_says_none(open_window, qtbot) -> None:
    """
    Given  two clean answers
    When   the Overview is read
    Then   its problems row says there are none, not the word of a column header
    """
    opened = open_window()
    opened.window.run_selected()
    _ran(qtbot, opened.window)

    overview = opened.window._overview._table
    problems = list(txt.OVERVIEW_ROWS).index(txt.PROBLEMS_ROW)

    assert [overview.item(problems, column).text() for column in range(2)] == [
        txt.NO_PROBLEMS,
        txt.NO_PROBLEMS,
    ]


# ── the cells of the Changes tab ───────────────────────────────────────────


def test_a_deleted_word_is_not_a_dash(tmp_path: Path) -> None:
    """
    Given  one model that deleted a word and one that left it
    When   the rows of the Changes tab are worked out
    Then   the first cell says the word was deleted and the second is the dash of "no change"
    """
    from teachers_teammate.gui.compare._rows import change_rows  # noqa: PLC0415

    source = "Er kam heute nicht."
    comparison = compare(
        source, [Version("a", "Er kam nicht."), Version("b", "Er kam heute nicht.")]
    )

    [row] = change_rows(comparison)

    assert row.cells == (txt.DELETED, txt.UNCHANGED)
    assert txt.DELETED != txt.UNCHANGED


def test_every_wording_one_model_wrote_at_a_place_is_in_its_cell() -> None:
    """
    Given  a model whose two runs wrote different wordings at one place
    When   the rows of the Changes tab are worked out
    Then   its cell lists both wordings, each marked as written in one run only
    """
    from teachers_teammate.gui.compare._rows import change_rows  # noqa: PLC0415

    source = "Er gehen nach Hause."
    comparison = compare(
        source,
        [Version("a", "Er geht nach Hause.", repeats=("Er ging nach Hause.",))],
    )

    [row] = change_rows(comparison)

    assert row.cells == (
        txt.OPTION_SEPARATOR.join(
            [txt.cell_wording("geht", once=True), txt.cell_wording("ging", once=True)]
        ),
    )


def test_a_deleted_wording_is_a_word_on_the_judged_line_too() -> None:
    """
    Given  an option that deletes the words
    When   the line the teacher judges it on is worded
    Then   it says the words are deleted, not a dash
    """
    assert txt.option_wording("") == f"→ {txt.DELETED}"


# ── how the asking ends ────────────────────────────────────────────────────


@pytest.mark.gui
def test_models_a_stop_never_reached_are_not_running_any_more(open_window, qtbot) -> None:
    """
    Given  two ticked models, the first of which is stopped
    When   the asking ends
    Then   the first says stopped, the second says it was not run, and the status bar says the
           comparison was stopped, not that it was done
    """
    opened = open_window(plan={"k-first": "stop"})

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    assert opened.window._models.row_of("k-first").state.text() == txt.STOPPED
    assert opened.window._models.row_of("k-second").state.text() == txt.NOT_RUN
    assert opened.said == [txt.compare_stopped("a.pdf")]


@pytest.mark.gui
def test_a_failure_that_ends_the_job_leaves_no_row_running(open_window, qtbot) -> None:
    """
    Given  a job that ends with an error before the second model is asked
    When   the asking ends
    Then   no row says running, the line says why, and the status bar does not say it was done
    """
    opened = open_window(plan={"k-first": "error"})

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    states = [opened.window._models.row_of(key).state.text() for key in ("k-first", "k-second")]
    assert states == [txt.NOT_RUN, txt.NOT_RUN]
    assert opened.window._line.text() != ""
    assert opened.said == []


@pytest.mark.gui
def test_a_finished_comparison_says_it_is_done(open_window, qtbot) -> None:
    """
    Given  two models that both answer
    When   the asking ends
    Then   the status bar says the comparison is done
    """
    opened = open_window()

    opened.window.run_selected()
    _ran(qtbot, opened.window)

    assert opened.said == [txt.compare_done("a.pdf")]


class _TeacherClosesIt(QCloseEvent):
    """A close the teacher asked for with the window's own X, as the window system sends it."""

    def spontaneous(self) -> bool:
        return True


@pytest.mark.gui
def test_the_teacher_is_asked_before_closing_stops_the_models(open_window, qtbot) -> None:
    """
    Given  a window that waits for a model's answer
    When   the teacher closes it with the X, first declining and then agreeing
    Then   the first time nothing stops, the second time the job is stopped and ended
    """
    opened = open_window(plan={"k-first": "wait", "k-second": "wait"})
    opened.window.run_selected()
    qtbot.waitUntil(lambda: opened.service.asked != [])

    declined = _TeacherClosesIt()
    with _dialogs.answering(lambda asked: None) as asked:
        opened.window.closeEvent(declined)

    assert [item.item.title for item in asked] == ["Models are still being asked"]
    assert not declined.isAccepted()
    assert opened.window.running()

    agreed = _TeacherClosesIt()
    with _dialogs.answering(lambda asked: Answer(asked.item.choices[0])):
        opened.window.closeEvent(agreed)

    assert agreed.isAccepted()
    assert not opened.window.running()


@pytest.mark.gui
def test_quitting_during_a_comparison_asks_first(main_window, monkeypatch) -> None:
    """
    Given  a main window whose compare window is asking a model
    When   the teacher chooses File → Quit and declines the question
    Then   the question is about the comparison, and the window stays open
    """
    monkeypatch.setattr(main_window._compare, "running", lambda: True)

    with _dialogs.answering(lambda asked: None) as asked:
        main_window._on_quit_requested()

    assert [item.item.title for item in asked] == ["Models are still being asked"]
    assert main_window._close_confirmed is False
