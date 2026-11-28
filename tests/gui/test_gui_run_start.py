"""One answer to "can this run start?": the window and the command line say the same thing.

The window shows the blocked run through :class:`DependencyGuardDialog`, the command line
prints it to stderr.  Both read the sentence from ``texts/issues.py``; these tests keep them
from drifting apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from teachers_teammate.application.run_checks import RunChecks, RunPreflight
from teachers_teammate.cli import run_cli
from teachers_teammate.domain.data_location import DataFlow
from teachers_teammate.domain.requirements import IssueKind, RequirementIssue
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.domain.stages import Stage
from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog
from teachers_teammate.texts.issues import issue_text
from tests.conftest import make_config
from tests.fakes.fake_ollama import FakeOllama

from ._gui_harness import main_window, queue_row
from ._service_stubs import services_of


def _missing_key() -> RequirementIssue:
    """Return the issue of an online proofreading provider whose key is not set."""
    return RequirementIssue(IssueKind.KEY_MISSING, provider="openai", name="OpenAI")


class _BlockedRun:
    """An application service whose run preflight answers one blocker, and nothing else."""

    def __init__(self, issue: RequirementIssue) -> None:
        self._issue = issue

    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def resolve_config_path(self, explicit: str | None) -> Path | None:
        return None

    def collect_input_files(self, config) -> list[Path]:
        return [Path("page.png")]

    def check_run_preflight(self, config, **kwargs) -> RunPreflight:
        return RunPreflight(stage=Stage.CORRECTION, stage_issues=(self._issue,))

    def env_key(self, provider: str) -> str:
        return "OPENAI_API_KEY"

    def describe_data_flow(self, config) -> DataFlow:
        raise AssertionError("a blocked run announces no data flow")

    def run_selected(self, config, files, /, **kwargs) -> RunSummary:
        raise AssertionError("a blocked run must not start")


@pytest.mark.gui
def test_the_window_states_the_blocker_in_the_words_of_texts(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a run preflight blocked by a proofreading provider without an API key
    When   Run is pressed
    Then   the dialog lists exactly the sentence ``texts/issues.py`` words for the issue
    """
    issue = _missing_key()
    blocked = RunPreflight(stage=Stage.CORRECTION, stage_issues=(issue,))
    monkeypatch.setattr(main_window._config_panel, "to_config", lambda: make_config(tmp_path))
    monkeypatch.setattr(RunChecks, "check_run_preflight", lambda self, config, **_kw: blocked)
    shown: list[DependencyGuardDialog] = []
    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: shown.append(self))
    main_window._results_table.set_queue([queue_row(str(tmp_path / "a.png"), "a.png")])
    main_window._results_table.selectRow(0)

    main_window._run_start.start()
    qtbot.waitUntil(lambda: bool(shown), timeout=3000)

    labels = [label.text() for label in shown[0].findChildren(QLabel)]
    assert any(issue_text(issue) in text for text in labels)
    assert main_window._run_controller._worker is None


@pytest.mark.gui
def test_the_run_button_asks_the_server_again_which_models_it_has(
    qtbot, main_window, monkeypatch, tmp_path: Path
) -> None:
    """
    Given  a server that answered a moment ago, and a run that proofreads on Ollama
    When   Run is pressed
    Then   the server is asked again, so a run does not start on a list from minutes ago
    """
    with FakeOllama() as fake:
        fake.install("qwen3:8b")
        config = make_config(
            tmp_path,
            ollama_url=fake.url,
            correction_enabled=True,
            correction_provider="ollama",
            correction_model="qwen3:8b",
        )
        monkeypatch.setattr(main_window._config_panel, "to_config", lambda: config)
        monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: 0)
        # What the app holds from a moment ago: a model pulled since would be in neither.
        main_window._app_service.ollama.snapshot(fake.url)
        main_window._results_table.set_queue([queue_row(str(tmp_path / "a.png"), "a.png")])
        main_window._results_table.selectRow(0)

        main_window._run_start.start()

        qtbot.waitUntil(lambda: fake.paths().count("/api/tags") == 2, timeout=5000)


@pytest.mark.unit
def test_the_command_line_states_the_blocker_in_the_words_of_texts(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    Given  the same blocked run preflight, asked by the command line
    When   run_cli() is called
    Then   stderr carries the same sentence, and the run does not start
    """
    issue = _missing_key()

    rc = run_cli(
        argv=["-i", str(tmp_path), "--no-docx"],
        app_service=services_of(_BlockedRun(issue)),
    )

    err = capsys.readouterr().err
    assert rc == 1
    assert issue_text(issue) in err
