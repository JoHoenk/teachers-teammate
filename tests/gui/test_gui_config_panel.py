"""GUI tests for ConfigPanel using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest
from teachers_teammate.gui._config_panel import ConfigPanel


@pytest.mark.gui
def test_config_panel_to_config_maps_values(qtbot, tmp_path: Path) -> None:
    """
    Given  a ConfigPanel with valid input/output folders and user selections
    When   to_config() is called
    Then   the returned Config reflects the current GUI field values
    """
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.set_input_dir(str(input_dir))
    panel._settings_dict["output_dir"] = str(output_dir)
    panel._language.setCurrentText("German")

    cfg = panel.to_config()

    assert cfg.input_dir == input_dir
    assert cfg.output_dir == output_dir
    assert cfg.recursive is True
    assert cfg.language == "German"


@pytest.mark.gui
def test_config_panel_to_config_rejects_missing_input(qtbot, tmp_path: Path) -> None:
    """
    Given  a ConfigPanel without an input folder set
    When   to_config() is called
    Then   a ValueError is raised indicating the missing input folder
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._settings_dict["output_dir"] = str(output_dir)

    with pytest.raises(ValueError, match="Input folder is required"):
        panel.to_config()


@pytest.mark.gui
def test_config_panel_to_config_allows_missing_output_when_docx_disabled(
    qtbot, tmp_path: Path
) -> None:
    """
    Given  a ConfigPanel with input folder set, output empty, and docx_enabled=False
    When   to_config() is called
    Then   no ValueError is raised and output_dir falls back to input_dir
    """
    input_dir = tmp_path / "input"
    input_dir.mkdir()

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.set_input_dir(str(input_dir))
    panel._settings_dict["output_dir"] = ""
    panel._settings_dict["docx_enabled"] = False

    cfg = panel.to_config()

    assert cfg.docx_enabled is False
    assert cfg.output_dir == input_dir


@pytest.mark.gui
def test_config_panel_configure_prompt_buttons_always_enabled(qtbot) -> None:
    """
    Given  a ConfigPanel with correction and evaluation initially disabled
    When   the enable checkboxes are toggled on and off
    Then   the Configure Prompt buttons remain enabled throughout
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert panel._correction_prompt_btn.isEnabled() is True
    assert panel._evaluation_prompt_btn.isEnabled() is True

    panel._evaluation_enabled.setChecked(True)
    panel._correction_enabled.setChecked(True)
    assert panel._correction_prompt_btn.isEnabled() is True
    assert panel._evaluation_prompt_btn.isEnabled() is True

    panel._evaluation_enabled.setChecked(False)
    panel._correction_enabled.setChecked(False)
    assert panel._correction_prompt_btn.isEnabled() is True
    assert panel._evaluation_prompt_btn.isEnabled() is True


@pytest.mark.gui
def test_config_panel_enabling_evaluation_sets_flag(qtbot) -> None:
    """
    Given  a ConfigPanel with evaluation initially disabled
    When   the evaluation checkbox is enabled
    Then   evaluation_enabled is recorded in the settings dict
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel._evaluation_enabled.setChecked(True)

    assert panel._settings_dict["evaluation_enabled"] is True


@pytest.mark.gui
def test_config_panel_set_selected_files_overrides_folder(qtbot, tmp_path: Path) -> None:
    """
    Given  a ConfigPanel with a folder set
    When   set_selected_files() is called with individual files
    Then   get_selected_files() returns those files and get_input_dir() returns their parent
    """
    folder = tmp_path / "docs"
    folder.mkdir()
    f1 = folder / "a.pdf"
    f2 = folder / "b.pdf"
    f1.touch()
    f2.touch()

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.set_input_dir(str(folder))
    assert panel.get_selected_files() is None

    panel.set_selected_files([f1, f2])
    files = panel.get_selected_files()
    assert files == [f1, f2]
    assert panel.get_input_dir() == str(folder)


class _GuardService:
    """Service stub reporting one missing Ollama model for the requirement check."""

    def build_lookup_config(self, input_dir: Path, output_dir: Path):
        from teachers_teammate.config import Config, OcrConfig  # noqa: PLC0415

        return Config(
            input_dir=input_dir,
            output_dir=output_dir,
            recursive=False,
            debug=False,
            ocr=OcrConfig(engine="ollama", model="", preprocess_method="none"),
            language="English",
            ollama_url="http://127.0.0.1:11434",
            correction_enabled=True,
            correction_provider="ollama",
            correction_model="",
        )

    def check_stage_requirements(self, config, stage: str):
        from teachers_teammate.application.service import RequirementIssue  # noqa: PLC0415

        _ = config, stage
        return [RequirementIssue("Model is not downloaded.", fixable_via_downloads=True)]


@pytest.mark.gui
def test_guard_dialog_opens_downloads_on_ollama_tab_with_missing_model_ticked(
    qtbot, monkeypatch
) -> None:
    """
    Given  proofreading with an Ollama model that is not downloaded
    When   the user answers the dependency guard with "Open Downloads…"
    Then   Downloads is requested on the Ollama tab with that model preselected
           (the same result as the "model missing" message before a run)
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415
    from teachers_teammate.gui._downloads_dialog import DownloadsDialog  # noqa: PLC0415

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: self.open_downloads.emit())
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    panel.update_settings({"correction_provider": "ollama", "correction_model": "qwen3.5:9b"})
    requests: list[tuple[int, list[str]]] = []
    panel.open_downloads_requested.connect(lambda tab, tags: requests.append((tab, tags)))

    panel._correction_enabled.setChecked(False)
    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: bool(requests), timeout=3000)

    assert requests == [(DownloadsDialog.TAB_OLLAMA, ["qwen3.5:9b"])]
    assert panel._correction_enabled.isChecked() is False  # unticked: requirement unmet


@pytest.mark.gui
def test_loading_saved_settings_does_not_run_the_requirement_guard(qtbot, monkeypatch) -> None:
    """
    Given  proofreading enabled in the saved settings and Ollama not reachable
    When   the settings are loaded into the panel (as at application start)
    Then   no requirement check runs and no dialog opens, the box stays ticked
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    calls: list[str] = []
    dialogs: list[object] = []

    class _Service(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            calls.append(stage)
            return super().check_stage_requirements(config, stage)

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_Service())

    panel.load_from_dict({"correction_enabled": True, "evaluation_enabled": True})
    qtbot.wait(100)  # a (wrongly) scheduled background check would have finished by now

    assert calls == []
    assert dialogs == []
    assert panel._correction_enabled.isChecked() is True
    assert panel._evaluation_enabled.isChecked() is True


@pytest.mark.gui
def test_ticking_a_stage_checks_requirements_off_the_gui_thread(qtbot, monkeypatch) -> None:
    """
    Given  a requirement check that blocks until released
    When   the user ticks "Enable proofreading"
    Then   the call returns at once (box ticked) and the dialog only appears after
           the check finished and found an unmet requirement
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    release = threading.Event()
    dialogs: list[object] = []

    class _SlowService(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            assert release.wait(timeout=5), "test never released the check"
            return super().check_stage_requirements(config, stage)

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_SlowService())
    panel._correction_enabled.setChecked(False)

    panel._correction_enabled.setChecked(True)

    assert panel._correction_enabled.isChecked() is True  # not blocked, not reverted yet
    assert dialogs == []
    release.set()
    qtbot.waitUntil(lambda: bool(dialogs), timeout=3000)
    assert panel._correction_enabled.isChecked() is False


@pytest.mark.gui
def test_outdated_guard_result_for_a_replaced_model_is_ignored(qtbot, monkeypatch) -> None:
    """
    Given  a requirement check for model X that is still running
    When   the user switches proofreading to model Y before it reports "X is missing"
    Then   the late answer neither unticks the box nor opens the guard dialog
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    release = threading.Event()
    started = threading.Event()
    dialogs: list[object] = []

    class _SlowService(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            started.set()
            assert release.wait(timeout=5), "test never released the check"
            return super().check_stage_requirements(config, stage)

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_SlowService())
    panel.update_settings({"correction_provider": "ollama", "correction_model": "model-x"})
    panel._correction_enabled.setChecked(False)
    panel._correction_enabled.setChecked(True)
    assert started.wait(timeout=3)

    panel.update_settings({"correction_model": "model-y"})
    release.set()
    qtbot.wait(200)  # the (outdated) result was delivered by now

    assert dialogs == []
    assert panel._correction_enabled.isChecked() is True


@pytest.mark.gui
def test_only_the_latest_guard_check_of_a_stage_counts(qtbot, monkeypatch) -> None:
    """
    Given  proofreading was ticked twice, so two checks are in flight
    When   the first (outdated) one finds a problem and the second one finds none
    Then   no dialog is shown and the box stays ticked
    """
    import threading  # noqa: PLC0415

    from teachers_teammate.application.service import RequirementIssue  # noqa: PLC0415
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    gates = [threading.Event(), threading.Event()]
    answers = [[RequirementIssue("Model is not downloaded.", True)], []]
    calls = {"n": 0}
    lock = threading.Lock()
    dialogs: list[object] = []

    class _Service(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            with lock:
                index = calls["n"]
                calls["n"] += 1
            assert gates[index].wait(timeout=5), "test never released the check"
            return answers[index]

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_Service())
    panel._correction_enabled.setChecked(False)
    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: calls["n"] == 1, timeout=3000)
    panel._correction_enabled.setChecked(False)
    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: calls["n"] == 2, timeout=3000)

    gates[1].set()
    qtbot.wait(100)
    gates[0].set()  # the outdated check reports last
    qtbot.wait(200)

    assert dialogs == []
    assert panel._correction_enabled.isChecked() is True
