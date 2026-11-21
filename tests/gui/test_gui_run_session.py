"""Tests for what a run of the main window is doing (teachers_teammate.gui._run_session)."""

from __future__ import annotations

from pathlib import Path

from teachers_teammate.domain.performance_estimate import TimeRange
from teachers_teammate.domain.run_summary import FileOutcome, FileStatus
from teachers_teammate.domain.stages import (
    PROGRESS_STAGE_CORRECTION,
    PROGRESS_STAGE_OCR,
    PROGRESS_STAGE_PREPROCESSING,
)
from teachers_teammate.gui._run_session import RunSession, StopStage
from teachers_teammate.gui._types import FileDoneEvent
from tests.conftest import make_config


def _done(source_id: str, name: str) -> FileDoneEvent:
    """Return the event of a file that was read and corrected."""
    return FileDoneEvent(
        outcome=FileOutcome(source_id=source_id, name=name, status=FileStatus.SAVED),
        ocr_s=2.0,
        correction_s=3.0,
    )


def test_a_run_counts_two_steps_per_file_when_it_proofreads(tmp_path: Path) -> None:
    """
    Given  a configuration that reads and proofreads, and two files
    When   a run starts
    Then   the bar counts two steps per file, shows the start, and the Stop button is armed
    """
    config = make_config(tmp_path, correction_enabled=True, correction_model="gemma4:e4b")
    session = RunSession()

    session.start(config, ["/q/a.png", "/q/b.png"])

    assert session.steps_per_file == 2
    assert session.steps_total == 4
    assert session.source_ids == ["/q/a.png", "/q/b.png"]
    assert session.pending == {"/q/a.png", "/q/b.png"}
    assert session.progress_base == "Selected 2 file(s) — Starting…"
    assert session.stop_stage is StopStage.RUNNING


def test_a_run_that_only_reads_counts_one_step_per_file(tmp_path: Path) -> None:
    """
    Given  a configuration without proofreading
    When   a run starts
    Then   every file is one step, and no model is named for the Check tab
    """
    config = make_config(tmp_path, correction_enabled=False)
    session = RunSession()

    session.start(config, ["/q/a.png"])

    assert session.steps_per_file == 1
    assert session.steps_total == 1
    assert session.model == ""
    assert session.waits_for_ollama is False


def test_a_proofreading_on_an_ollama_server_waits_for_the_first_token(tmp_path: Path) -> None:
    """
    Given  a run whose proofreading waits for an Ollama server
    When   the proofreading of the file starts and its first token arrives
    Then   the clock is held during the wait and starts at the first token
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_model="gemma4:e4b",
        correction_provider="ollama",
    )
    session = RunSession()
    session.start(config, ["/q/a.png"])
    session.file_started("/q/a.png", "a.png", 1, 1)

    session.stage_started("a.png", PROGRESS_STAGE_CORRECTION, 1, 1, 10.0)

    assert session.waits_for_ollama is True
    assert session.pace_waits_for_first_token is True
    assert session.pace._work_started_at is None

    session.first_token("/q/a.png", 12.0)

    assert session.pace_waits_for_first_token is False
    assert session.pace._work_started_at == 12.0


def test_a_reading_starts_the_clock_at_once(tmp_path: Path) -> None:
    """
    Given  a run
    When   the reading of its file starts
    Then   the clock runs from that moment and the line names the reading
    """
    config = make_config(tmp_path, correction_enabled=True, correction_model="gemma4:e4b")
    session = RunSession()
    session.start(config, ["/q/a.png"])

    text = session.stage_started("a.png", PROGRESS_STAGE_OCR, 1, 1, 10.0)

    assert session.pace._work_started_at == 10.0
    assert session.pace_waits_for_first_token is False
    assert text is not None
    assert text.endswith(session.stage_label)
    assert session.stage_label


def test_a_wait_note_replaces_the_step_until_the_answer_begins(tmp_path: Path) -> None:
    """
    Given  a proofreading whose file waits for the server
    When   a wait detail arrives and then the first token
    Then   the detail is shown, and the step of the stage comes back
    """
    config = make_config(
        tmp_path,
        correction_enabled=True,
        correction_model="gemma4:e4b",
        correction_provider="ollama",
    )
    session = RunSession()
    session.start(config, ["/q/a.png"])
    session.file_started("/q/a.png", "a.png", 1, 1)
    session.stage_started("a.png", PROGRESS_STAGE_CORRECTION, 1, 1, 10.0)
    stage_step = session.stage_step

    detail = session.stage_detail("/q/a.png", "still working with other:8b")

    assert detail is not None
    assert session.wait_shown is True
    assert session.step == "still working with other:8b"

    text = session.first_token("/q/a.png", 20.0)

    assert session.wait_shown is False
    assert session.step == stage_step
    assert text is not None


def test_a_detail_of_another_file_is_ignored(tmp_path: Path) -> None:
    """
    Given  a run that works on one file
    When   a detail and a first token arrive for another one
    Then   the run says nothing about it
    """
    config = make_config(tmp_path, correction_enabled=True, correction_model="gemma4:e4b")
    session = RunSession()
    session.start(config, ["/q/a.png", "/q/b.png"])
    session.file_started("/q/a.png", "a.png", 1, 2)

    assert session.stage_detail("/q/b.png", "loading model") is None
    assert session.first_token("/q/b.png", 10.0) is None
    assert session.wait_shown is False


def test_a_finished_file_counts_and_frees_its_row(tmp_path: Path) -> None:
    """
    Given  a run that works on the first of two files
    When   that file is done
    Then   its name and times are kept, its row is no longer pending and the bar moved one step
    """
    config = make_config(tmp_path, correction_enabled=False)
    session = RunSession()
    session.start(config, ["/q/a.png", "/q/b.png"])
    session.file_started("/q/a.png", "a.png", 1, 2)

    session.file_done(_done("/q/a.png", "a.png"))

    assert session.names == ["a.png"]
    assert session.ocr_times == [2.0]
    assert session.correction_times == [3.0]
    assert session.pending == {"/q/b.png"}
    assert session.current == ""
    assert session.steps_done() == 1


def test_a_finished_run_leaves_nothing_behind(tmp_path: Path) -> None:
    """
    Given  a run with one finished file
    When   it ends
    Then   no file is pending, no document is current and the Stop button is idle
    """
    config = make_config(tmp_path, correction_enabled=True, correction_model="gemma4:e4b")
    session = RunSession()
    session.start(config, ["/q/a.png"])
    session.start_pace(TimeRange(1.0, 2.0), 1)
    session.file_done(_done("/q/a.png", "a.png"))

    session.finished()

    assert session.pending == set()
    assert session.current == ""
    assert session.source_ids == []
    assert session.stop_stage is StopStage.IDLE
    assert session.pace_waits_for_first_token is False


def test_a_new_run_starts_its_pace_from_the_estimate(tmp_path: Path) -> None:
    """
    Given  a window that knows how long a page takes, and the page counts of the run before
    When   a run starts
    Then   the pace holds the estimate and the files, and no page of the last run is left
    """
    config = make_config(tmp_path, correction_enabled=False)
    session = RunSession()
    session.pages = {"/q/old.png": 3}
    estimate = TimeRange(10.0, 20.0)

    session.start(config, ["/q/a.png"])
    session.start_pace(estimate, 1)

    assert session.pages == {}
    assert session.pace.estimate == estimate
    # Without page counts the bar counts files, so the pace knows no fraction yet.
    assert session.pace.snapshot(0.0).fraction is None


def test_the_preprocessing_names_the_step_of_the_check_strip(tmp_path: Path) -> None:
    """
    Given  a run whose file is being prepared
    When   that stage starts
    Then   the strip says the run is preparing the file
    """
    config = make_config(tmp_path, correction_enabled=False)
    session = RunSession()
    session.start(config, ["/q/a.png"])

    session.stage_started("a.png", PROGRESS_STAGE_PREPROCESSING, 1, 1, 10.0)

    assert session.step == session.stage_step
    assert session.step != ""
