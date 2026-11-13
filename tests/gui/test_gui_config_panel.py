"""GUI tests for ConfigPanel using pytest-qt."""

from __future__ import annotations

from pathlib import Path

import pytest
from teachers_teammate.gui._config_panel import ConfigPanel
from teachers_teammate.gui.settings import SettingsPage

from ._service_stubs import LocalDataFlowStub


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
def test_content_review_is_disabled_while_proofreading_is_off(qtbot) -> None:
    """
    Given  a panel with "Review content" ticked
    When   "Proofread" is unticked
    Then   the review box is disabled with a tooltip and keeps its tick, and the settings
           still say it is ticked (so it is saved)
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._evaluation_enabled.setChecked(True)

    panel._correction_enabled.setChecked(False)

    assert panel._evaluation_enabled.isEnabled() is False
    assert panel._evaluation_enabled.isChecked() is True
    assert "only together with proofreading" in panel._evaluation_enabled.toolTip()
    assert panel.to_dict()["evaluation_enabled"] is True
    panel._correction_enabled.setChecked(True)
    assert panel._evaluation_enabled.isEnabled() is True
    assert panel._evaluation_enabled.toolTip() == ""


@pytest.mark.gui
def test_a_review_without_proofreading_counts_as_off_in_every_config(qtbot, tmp_path) -> None:
    """
    Given  "Review content" ticked and "Proofread" unticked
    When   the panel builds its run config and its lookup config
    Then   both report the review as off, so no check counts a review the run would drop
    """
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))
    panel._evaluation_enabled.setChecked(True)
    panel._correction_enabled.setChecked(True)
    panel.set_service(_GuardService())
    assert panel.to_config().evaluation_enabled is True
    assert panel.lookup_config().evaluation_enabled is True

    panel._correction_enabled.setChecked(False)

    assert panel.to_config().correction_enabled is False
    assert panel.to_config().evaluation_enabled is False
    assert panel.lookup_config().evaluation_enabled is False


@pytest.mark.gui
def test_the_guard_unticking_proofreading_disables_the_review(qtbot, monkeypatch) -> None:
    """
    Given  proofreading and review ticked, and an unmet proofreading requirement
    When   the background guard unticks "Proofread"
    Then   "Review content" is disabled too (the guard unticks with signals blocked)
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: 0)
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._evaluation_enabled.setChecked(True)
    panel._correction_enabled.setChecked(False)
    panel.set_service(_GuardService())

    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: not panel._correction_enabled.isChecked(), timeout=3000)

    assert panel._evaluation_enabled.isEnabled() is False


@pytest.mark.gui
def test_ticking_proofreading_again_guards_the_ticked_review_too(qtbot, monkeypatch) -> None:
    """
    Given  "Review content" ticked and "Proofread" unticked
    When   "Proofread" is ticked
    Then   the requirements of both stages are checked, because the review is back
    """
    checked: list[str] = []

    class _Service(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            checked.append(stage)
            return []

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_Service())
    panel._evaluation_enabled.setChecked(True)
    qtbot.waitUntil(lambda: checked == ["evaluation"], timeout=3000)  # ticking the review
    panel._correction_enabled.setChecked(False)
    checked.clear()

    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: sorted(checked) == ["correction", "evaluation"], timeout=3000)


@pytest.mark.gui
def test_a_problem_shared_by_proofreading_and_review_is_explained_once(qtbot, monkeypatch) -> None:
    """
    Given  "Review content" ticked, "Proofread" unticked, and a missing model both need
    When   "Proofread" is ticked
    Then   one dialog explains it: proofreading is unticked and the review keeps its tick
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    dialogs: list[object] = []
    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._evaluation_enabled.setChecked(True)  # before the service: nothing is checked yet
    panel._correction_enabled.setChecked(False)
    panel.set_service(_GuardService())

    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: not panel._correction_enabled.isChecked(), timeout=3000)
    qtbot.wait(300)  # a second dialog for the review would have come by now

    assert len(dialogs) == 1
    assert panel._evaluation_enabled.isChecked() is True
    assert panel.to_dict()["evaluation_enabled"] is True


@pytest.mark.gui
def test_a_late_review_answer_is_ignored_once_proofreading_is_off(qtbot, monkeypatch) -> None:
    """
    Given  a requirement check of the review that is still running, and proofreading unticked
    When   the check answers that the review's model is missing
    Then   no dialog opens and the review keeps its tick (it cannot run without proofreading)
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
    panel._correction_enabled.setChecked(False)
    panel.set_service(_SlowService())
    panel._evaluation_enabled.setChecked(True)  # starts the review's check, which waits

    release.set()
    qtbot.wait(300)

    assert dialogs == []
    assert panel._evaluation_enabled.isChecked() is True


@pytest.mark.gui
def test_the_level_sits_in_step_two_and_reaches_the_config(qtbot, tmp_path) -> None:
    """
    Given  the panel
    When   the level is chosen in step 2 next to the language
    Then   the level combo shares a row with the language combo and lands in correction_level
    """
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))

    panel._correction_level.setCurrentText("B1")

    assert panel._correction_level.parentWidget() is panel._language.parentWidget()
    assert panel.to_config().correction_level == "B1"


@pytest.mark.gui
def test_the_lookup_config_is_recursive_like_the_run_config(qtbot, tmp_path) -> None:
    """
    Given  a panel whose service builds a non-recursive lookup config
    When   the lookup config and the run config are built
    Then   both are recursive, so "text files would land in the documents folder" gets the
           same answer for the readiness line as for Run
    """
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))
    panel.set_service(_GuardService())

    assert panel.lookup_config().recursive is True
    assert panel.to_config().recursive is True


@pytest.mark.gui
def test_the_lookup_config_carries_the_results_step(qtbot) -> None:
    """
    Given  a panel with only "Text file, anonymized" ticked, a profile and a context chosen
    When   the lookup config for the requirement checks is built
    Then   it carries those, so the preflight checks name detection for that file alone
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    panel.update_settings({"hardware_profile": "16gb", "proofreading_context": "16384"})
    panel._docx_enabled.setChecked(False)
    panel._text_enabled.setChecked(False)
    panel._text_anonymized_enabled.setChecked(True)

    config = panel.lookup_config()

    assert config.text_anonymized_enabled is True
    assert config.docx_enabled is False
    assert config.text_enabled is False
    assert config.hardware_profile == "16gb"
    assert config.proofreading_context == "16384"


@pytest.mark.gui
def test_the_results_problem_is_a_file_output_without_a_folder(qtbot) -> None:
    """
    Given  the Results step
    When   a file output is ticked without a folder, and then a folder is chosen
    Then   results_problem() is true only in between
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._docx_enabled.setChecked(False)
    assert panel.results_problem() is False

    panel._text_enabled.setChecked(True)
    assert panel.results_problem() is True

    panel._output_dir.setText("/tmp/results")
    assert panel.results_problem() is False


@pytest.mark.gui
def test_the_options_buttons_open_dialogs_with_the_new_titles(qtbot, monkeypatch) -> None:
    """
    Given  the panel
    When   both "Options…" buttons are clicked
    Then   the dialogs are titled "Proofreading options" and "Content review options"
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    from teachers_teammate.gui._config_panel import _PromptDialog  # noqa: PLC0415

    titles: list[str] = []

    def reject(dlg: _PromptDialog) -> QDialog.DialogCode:
        titles.append(dlg.windowTitle())
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(_PromptDialog, "exec", reject)
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel._correction_prompt_btn.click()
    panel._evaluation_prompt_btn.click()

    assert titles == ["Proofreading options", "Content review options"]
    assert panel._correction_prompt_btn.text() == "Options…"
    assert panel._anon_configure_btn.text() == "Rules…"


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


class _GuardService(LocalDataFlowStub):
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
        from teachers_teammate.application.service import (  # noqa: PLC0415
            IssueKind,
            RequirementIssue,
        )

        _ = config, stage
        return [
            RequirementIssue(
                "Model is not downloaded.", IssueKind.MODEL_MISSING, models=("qwen3.5:9b",)
            )
        ]


@pytest.mark.gui
def test_guard_dialog_opens_addons_on_local_models_with_missing_model_ticked(
    qtbot, monkeypatch
) -> None:
    """
    Given  proofreading with an Ollama model that is not downloaded
    When   the user answers the dependency guard with its "Download…" button
    Then   Add-ons is requested on its local-models tab with that model preselected
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415
    from teachers_teammate.gui.settings._page import FOCUS_LOCAL_MODELS  # noqa: PLC0415

    monkeypatch.setattr(
        DependencyGuardDialog, "exec", lambda self: self.action_chosen.emit(self.fix_actions[0])
    )
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    panel.update_settings({"correction_provider": "ollama", "correction_model": "qwen3.5:9b"})
    requests: list[tuple[object, str, list[str]]] = []
    panel.open_settings_requested.connect(
        lambda page, focus, tags: requests.append((page, focus, tags))
    )

    panel._correction_enabled.setChecked(False)
    panel._correction_enabled.setChecked(True)
    qtbot.waitUntil(lambda: bool(requests), timeout=3000)

    assert requests == [(SettingsPage.ADDONS, FOCUS_LOCAL_MODELS, ["qwen3.5:9b"])]
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
    When   the user ticks "Proofread"
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

    from teachers_teammate.application.service import IssueKind, RequirementIssue  # noqa: PLC0415
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    gates = [threading.Event(), threading.Event()]
    answers = [[RequirementIssue("Model is not downloaded.", IssueKind.MODEL_MISSING)], []]
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


@pytest.mark.gui
def test_config_panel_to_config_applies_preprocess_and_pdf_settings(qtbot, tmp_path: Path) -> None:
    """
    Given  a ConfigPanel whose settings disable dewarp/deskew/denoise, enable border crop and
           gamma, and set 150 DPI and the "never" PDF text layer
    When   to_config() is called
    Then   the OcrConfig carries those values instead of the defaults
    """
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))
    panel.update_settings(
        {
            "preprocess_dewarp": False,
            "preprocess_deskew": False,
            "preprocess_denoise": False,
            "preprocess_border_crop": True,
            "preprocess_gamma": True,
            "pdf_render_dpi": 150,
            "pdf_text_layer": "never",
        }
    )

    ocr = panel.to_config().ocr

    assert not (ocr.dewarp or ocr.deskew or ocr.denoise)
    assert ocr.border_crop and ocr.gamma
    assert ocr.pdf_render_dpi == 150
    assert ocr.pdf_text_layer == "never"


def test_load_from_dict_restores_advanced_ocr_settings(qtbot, tmp_path: Path) -> None:
    """
    Given  a settings dict (as read from ocr.toml) with the PDF text layer, render DPI,
           provider and pre-step flags set
    When   load_from_dict() and then to_config() are called
    Then   the OcrConfig carries those values instead of falling back to the defaults
    """
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))

    panel.load_from_dict(
        {
            "pdf_text_layer": "Never",
            "pdf_render_dpi": 150,
            "preprocess_dewarp": False,
            "preprocess_gamma": True,
        }
    )

    ocr = panel.to_config().ocr
    assert ocr.pdf_text_layer == "never"
    assert ocr.pdf_render_dpi == 150
    assert ocr.dewarp is False
    assert ocr.gamma is True
    assert panel.to_dict()["pdf_text_layer"] == "never"


class _FlowService(_GuardService):
    """Reports online proofreading, with names hidden only when the "Remove personal info" box is ticked.

    The panel asks for every step once (``describe_steps``) and narrows it to the ticked ones.
    """

    def check_stage_requirements(self, config, stage: str):
        _ = config, stage
        return []  # nothing missing, so ticking a box never opens a dialog

    def describe_steps(self, config):
        from teachers_teammate.domain.data_location import (  # noqa: PLC0415
            DataFlow,
            DataFlowStage,
            DataLocation,
            StageLocation,
        )

        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        cloud = StageLocation(DataLocation.ONLINE, "OpenAI")
        return DataFlow(
            (
                DataFlowStage("ocr", here, "page images"),
                DataFlowStage("correction", cloud, "text", config.anonymization_enabled),
            )
        )


@pytest.mark.gui
def test_summary_follows_the_step_3_checkbox(qtbot) -> None:
    """
    Given  a panel whose proofreading is online
    When   "Remove personal info" is ticked
    Then   the summary no longer advises hiding names and says only anonymized text leaves
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._correction_enabled.setChecked(True)  # before the service: nothing is checked yet
    panel.set_service(_FlowService())
    assert "Remove personal info" in panel._data_flow_summary._body.text()

    panel._anonymization_enabled.setChecked(True)

    assert "Remove personal info" not in panel._data_flow_summary._body.text()
    assert (
        "Anonymized text is sent to a service outside the EU."
        in panel._data_flow_summary._body.text()
    )


class _StepsService(_FlowService):
    """Answers ``describe_steps`` with proofreading and review at an EU service."""

    def describe_steps(self, config):
        from teachers_teammate.domain.data_location import (  # noqa: PLC0415
            DataFlow,
            DataFlowStage,
            DataLocation,
            DataRegion,
            StageLocation,
        )

        here = StageLocation(DataLocation.THIS_COMPUTER, "Ollama")
        eu = StageLocation(DataLocation.ONLINE, "Mistral (EU)", region=DataRegion.EU)
        return DataFlow(
            (
                DataFlowStage("ocr", here, "page images"),
                DataFlowStage("anonymization", here, "text", detail="On (spaCy de)"),
                DataFlowStage("correction", eu, "text"),
                DataFlowStage("evaluation", eu, "text"),
            )
        )


_MISTRAL = {
    "ocr_model": "qwen2.5vl:7b",
    "correction_provider": "mistral_eu",
    "correction_model": "mistral-small-latest",
    "evaluate_provider": "mistral_eu",
    "evaluate_model": "mistral-small-latest",
}


@pytest.mark.gui
def test_every_step_shows_what_it_uses(qtbot) -> None:
    """
    Given  proofreading and review at Mistral (EU), text recognition with a local model and
           name removal on
    When   the panel shows its data flow
    Then   each step has a line with its model and place, and name removal says what it does
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_StepsService())

    panel.update_settings({**_MISTRAL, "anonymization_enabled": True})

    assert "qwen2.5vl:7b · this computer" in panel._ocr_uses.text()
    assert "On (spaCy de)" in panel._anonymization_uses.text()
    assert "mistral-small-latest · Mistral (EU) · EU" in panel._correction_uses.text()
    assert "Same model as proofreading" in panel._evaluation_uses.text()


@pytest.mark.gui
def test_a_program_engine_is_named_by_the_engine_not_by_the_ollama_model(qtbot) -> None:
    """
    Given  Tesseract chosen as the engine, while the saved model is still an Ollama tag
    When   the panel shows what text recognition uses
    Then   the line does not name the Ollama model, which is not what is used
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_StepsService())

    panel.update_settings({**_MISTRAL, "ocr_engine": "tesseract"})

    assert "qwen2.5vl:7b" not in panel._ocr_uses.text()
    assert "this computer" in panel._ocr_uses.text()


@pytest.mark.gui
def test_the_review_line_names_its_own_model_when_it_differs(qtbot) -> None:
    """
    Given  a review model that is not the proofreading model
    When   the panel shows its data flow
    Then   the review line names its model instead of "Same model as proofreading"
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_StepsService())

    panel.update_settings({**_MISTRAL, "evaluate_model": "mistral-large-latest"})

    assert "mistral-large-latest · Mistral (EU) · EU" in panel._evaluation_uses.text()
    assert "Same model" not in panel._evaluation_uses.text()


@pytest.mark.gui
def test_an_unticked_step_keeps_its_line_in_grey(qtbot) -> None:
    """
    Given  proofreading ticked with its line in colour
    When   "Proofread" is unticked
    Then   the line still names the model, now in grey, and the review line follows
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_StepsService())
    panel.update_settings(_MISTRAL)
    panel._evaluation_enabled.setChecked(True)
    assert "#888" not in panel._correction_uses.text()

    panel._correction_enabled.setChecked(False)

    assert "mistral-small-latest" in panel._correction_uses.text()
    assert "#888" in panel._correction_uses.text()
    assert "#888" in panel._evaluation_uses.text()  # the review cannot run without proofreading


@pytest.mark.gui
@pytest.mark.parametrize(
    ("line", "focus"),
    [("_ocr_uses", "ocr"), ("_correction_uses", "proofreading"), ("_evaluation_uses", "review")],
)
def test_change_opens_ai_models_on_that_step(qtbot, line, focus) -> None:
    """
    Given  the lines under the steps
    When   "change…" is clicked on one of them
    Then   the panel asks for the AI models page, focused on that step
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    requests: list[tuple[object, str, list[str]]] = []
    panel.open_settings_requested.connect(lambda *args: requests.append(args))

    getattr(panel, line).change_link.linkActivated.emit("change")

    assert requests == [(SettingsPage.AI_MODELS, focus, [])]


@pytest.mark.gui
def test_name_removal_has_no_change_link(qtbot) -> None:
    """
    Given  the line under "Remove personal info"
    When   the panel is built
    Then   it has no "change…" link: that step has its "Rules…" button instead
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert panel._anonymization_uses.change_link.isHidden()


@pytest.mark.gui
def test_the_model_name_is_not_part_of_what_the_teacher_confirmed(qtbot) -> None:
    """
    Given  an online step the teacher confirmed once ("don't ask again")
    When   another model of the same service is chosen
    Then   the data flow keeps the same fingerprint, so the answer stays valid
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_StepsService())
    panel.update_settings(_MISTRAL)
    before = panel.data_flow().fingerprint

    panel.update_settings({"correction_model": "mistral-large-latest"})

    assert panel.data_flow().fingerprint == before


class _FlowServiceMissingPrivacy(_FlowService):
    """Like :class:`_FlowService`, but the privacy add-on is missing."""

    def check_stage_requirements(self, config, stage: str):
        from teachers_teammate.application.service import (  # noqa: PLC0415
            IssueKind,
            RequirementIssue,
        )

        _ = config, stage
        return [
            RequirementIssue(
                "Privacy add-on is missing.",
                IssueKind.NAME_DETECTION_MISSING,
                models=("de_core_news_sm",),
            )
        ]


@pytest.mark.gui
def test_summary_follows_a_stage_the_guard_unticked(qtbot, monkeypatch) -> None:
    """
    Given  online proofreading and a missing privacy add-on
    When   "Remove personal info" is ticked and the requirement check unticks it again
    Then   the summary shows the unhidden state again and advises hiding names
    """
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: 0)
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._correction_enabled.setChecked(True)  # before the service: nothing is checked yet
    panel.set_service(_FlowServiceMissingPrivacy())

    panel._anonymization_enabled.setChecked(True)
    qtbot.waitUntil(lambda: not panel._anonymization_enabled.isChecked(), timeout=3000)

    assert "Remove personal info" in panel._data_flow_summary._body.text()


@pytest.mark.gui
def test_summary_refreshes_after_settings_dialog_changes(qtbot) -> None:
    """
    Given  a panel showing its data flow
    When   the settings dialog hands back new values
    Then   the summary is rendered again from the live settings
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    calls: list[int] = []

    class _CountingService(_FlowService):
        def describe_steps(self, config):
            calls.append(1)
            return super().describe_steps(config)

    panel.set_service(_CountingService())
    calls.clear()

    panel.update_settings({"correction_provider": "openai"})

    assert calls == [1]


@pytest.mark.gui
def test_loading_settings_renders_the_summary_once(qtbot) -> None:
    """
    Given  a panel showing its data flow
    When   saved settings that flip several checkboxes are loaded
    Then   the summary is rendered once, from the final settings, not once per checkbox
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    calls: list[int] = []

    class _CountingService(_FlowService):
        def describe_steps(self, config):
            calls.append(1)
            return super().describe_steps(config)

    panel.set_service(_CountingService())
    calls.clear()

    panel.load_from_dict(
        {
            "correction_enabled": True,
            "evaluation_enabled": True,
            "anonymization_enabled": True,
        }
    )

    assert calls == [1]


@pytest.mark.gui
def test_saved_online_ocr_engine_falls_back_to_the_local_default(qtbot) -> None:
    """
    Given  saved settings from an older version with an online OCR engine
    When   they are loaded into the panel
    Then   the engine is the local default
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict({"ocr_engine": "langchain", "ocr_provider": "openai"})

    assert panel.get_settings_dict()["ocr_engine"] == "ollama"
    assert "ocr_provider" not in panel.to_dict()


@pytest.mark.gui
def test_config_panel_language_change_keeps_prompt_empty(qtbot) -> None:
    """
    Given  a ConfigPanel with no custom correction prompt ("" = built-in)
    When   the language is changed
    Then   the prompt stays empty — "" is a reference to the built-in prompt
           of the new language, not a stored copy — and no custom note shows
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel._language.setCurrentText("German")
    assert panel.get_correction_prompt() == ""
    assert not panel._custom_prompt_note.isVisibleTo(panel)

    panel._language.setCurrentText("French")
    assert panel.get_correction_prompt() == ""


@pytest.mark.gui
def test_config_panel_language_change_notes_custom_prompt(qtbot) -> None:
    """
    Given  a ConfigPanel with a custom correction prompt
    When   the language is changed
    Then   the "used for every language" note is shown
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.set_correction_prompt("Fix my students' homework at {level}.")
    panel._language.setCurrentText("German")

    assert panel._custom_prompt_note.isVisibleTo(panel)


@pytest.mark.gui
def test_config_panel_load_resets_saved_preset_copy(qtbot) -> None:
    """
    Given  settings values holding the preset text saved by an older version
    When   load_from_dict() applies them
    Then   the prompt is reset to "" (built-in), for correction and evaluation
    """
    from teachers_teammate.config import DEFAULTS  # noqa: PLC0415
    from teachers_teammate.infrastructure.correction import LEGACY_PRESET_PROMPTS  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict(
        {
            "language": "German",
            "correction_prompt": LEGACY_PRESET_PROMPTS["german"],
            "evaluate_prompt": DEFAULTS["evaluate_prompt"],
        }
    )

    assert panel.get_correction_prompt() == ""
    assert panel.get_evaluation_prompt() == ""
    assert not panel._custom_prompt_note.isVisibleTo(panel)


@pytest.mark.gui
def test_config_panel_load_keeps_custom_prompt(qtbot) -> None:
    """
    Given  settings values holding a custom correction prompt
    When   load_from_dict() applies them
    Then   the custom text is kept and the custom note is shown
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict(
        {"language": "German", "correction_prompt": "Fix my students' homework at {level}."}
    )

    assert panel.get_correction_prompt() == "Fix my students' homework at {level}."
    assert panel._custom_prompt_note.isVisibleTo(panel)


@pytest.mark.gui
def test_config_panel_round_trips_the_answer_formats(qtbot) -> None:
    """
    Given  a teacher's own answer formats
    When   they are applied to the panel, saved with to_dict() and loaded into a new panel
    Then   the formats reach the Config the pipeline uses
    """
    from teachers_teammate.domain.answer_format import ANSWER_FORMAT_VERSION  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.update_settings(
        {
            "answer_format_review": "R",
            "answer_format_text": "T",
            "answer_format_version": ANSWER_FORMAT_VERSION,
        }
    )

    saved = panel.to_dict()
    other = ConfigPanel()
    qtbot.addWidget(other)
    other.load_from_dict(saved)
    config = other.to_config()

    assert (config.answer_format_review, config.answer_format_text) == ("R", "T")


@pytest.mark.gui
def test_config_panel_drops_answer_formats_of_another_version(qtbot, capsys) -> None:
    """
    Given  saved settings whose answer formats were written for an older version
    When   load_from_dict() applies them
    Then   the built-in formats are used and the reset is announced
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict(
        {"answer_format_review": "old", "answer_format_text": "old", "answer_format_version": 0}
    )

    settings = panel.get_settings_dict()
    assert settings["answer_format_review"] == ""
    assert settings["answer_format_text"] == ""
    assert "answer format" in capsys.readouterr().err


@pytest.mark.gui
def test_lookup_config_carries_the_anonymizer_rules_and_instructions(qtbot) -> None:
    """
    Given  a panel with an own extra model, no regex patterns and a custom prompt
    When   lookup_config() is read (the "What is sent" dialog and the status box use it)
    Then   the config holds those settings instead of the defaults
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    panel.update_settings(
        {
            "anonymizer_primary_model": "de_core_news_sm",
            "anonymizer_secondary_model": None,
            "anonymizer_patterns": [],
            "explanation_language": "German",
        }
    )
    panel._correction_prompt_text = "Mein eigener Auftrag."

    config = panel.lookup_config()

    assert config.anonymizer_primary_model == "de_core_news_sm"
    assert config.anonymizer_secondary_model == "none"
    assert config.anonymizer_patterns == []
    assert config.correction_prompt == "Mein eigener Auftrag."
    assert config.explanation_language == "German"


@pytest.mark.gui
def test_changing_the_document_language_refreshes_the_data_flow(qtbot) -> None:
    """
    Given  a panel with a service
    When   the document language changes
    Then   the data flow is described again and announced (models and availability depend on it)
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    announced: list[object] = []
    panel.data_flow_changed.connect(announced.append)

    panel._language.setCurrentText("German")

    assert announced


# ── Step 6: Results ───────────────────────────────────────────────────────────


@pytest.mark.gui
def test_results_step_shows_and_saves_the_output_choices(qtbot, tmp_path: Path) -> None:
    """
    Given  saved settings with an output folder, Word in "comments" layout and text files
    When   they are loaded into the panel, then the user unticks Word and ticks anonymized text
    Then   the Results step shows them, and to_dict() / load_from_dict() round-trip the change
           with the same keys as the old Output Settings dialog
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.load_from_dict(
        {
            "output": str(tmp_path),
            "docx_enabled": True,
            "docx_format": "comments",
            "text_enabled": True,
            "text_anonymized_enabled": False,
        }
    )

    assert panel._output_dir.text() == str(tmp_path)
    assert panel._docx_enabled.isChecked()
    assert panel._docx_format.currentText() == "comments"
    assert panel._text_enabled.isChecked()
    assert not panel._text_anonymized_enabled.isChecked()

    panel._docx_enabled.setChecked(False)
    panel._text_anonymized_enabled.setChecked(True)
    saved = panel.to_dict()

    assert saved["output"] == str(tmp_path)
    assert saved["docx_enabled"] is False
    assert saved["docx_format"] == "comments"
    assert saved["text_enabled"] is True
    assert saved["text_anonymized_enabled"] is True

    reloaded = ConfigPanel()
    qtbot.addWidget(reloaded)
    reloaded.load_from_dict(saved)
    assert not reloaded._docx_enabled.isChecked()
    assert reloaded._text_anonymized_enabled.isChecked()
    assert reloaded.get_output_dir() == str(tmp_path)


@pytest.mark.gui
def test_word_layout_is_only_enabled_while_word_is_ticked(qtbot) -> None:
    """
    Given  the Results step with Word documents ticked
    When   Word is unticked and ticked again
    Then   the layout choice is disabled while Word is off and enabled again with it
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._docx_enabled.setChecked(True)
    assert panel._docx_format.isEnabled()

    panel._docx_enabled.setChecked(False)
    assert not panel._docx_format.isEnabled()

    panel._docx_enabled.setChecked(True)
    assert panel._docx_format.isEnabled()


@pytest.mark.gui
def test_changing_the_results_step_tells_the_main_window(qtbot, tmp_path: Path) -> None:
    """
    Given  the Results step
    When   the user types another folder (and leaves the field) or ticks text files
    Then   results_changed is emitted each time and the settings hold the new values;
           loading saved settings emits nothing
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    changes: list[bool] = []
    panel.results_changed.connect(lambda: changes.append(True))

    panel.load_from_dict({"output": str(tmp_path / "loaded"), "text_enabled": False})
    assert changes == []

    panel._output_dir.setText(str(tmp_path / "typed"))
    panel._output_dir.editingFinished.emit()
    assert changes == [True]
    assert panel.get_output_dir() == str(tmp_path / "typed")

    panel._output_dir.editingFinished.emit()  # focus left again without a change
    assert changes == [True]

    panel._text_enabled.setChecked(True)
    assert changes == [True, True]
    assert panel.get_settings_dict()["text_enabled"] is True


@pytest.mark.gui
def test_text_files_without_a_folder_turn_the_hint_orange(qtbot) -> None:
    """
    Given  the Results step without an output folder
    When   text files are ticked
    Then   the hint that text files need a folder is shown as a warning
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.update_settings({"output_dir": ""})
    assert "#888" in panel._text_hint.styleSheet()

    panel._text_enabled.setChecked(True)

    assert "#e67e22" in panel._text_hint.styleSheet()


@pytest.mark.gui
def test_settings_from_the_settings_window_reach_the_results_step(qtbot, tmp_path: Path) -> None:
    """
    Given  the panel
    When   update_settings() brings another output folder and Word layout (e.g. Restore defaults)
    Then   the Results step shows them without reporting a user change
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    changes: list[bool] = []
    panel.results_changed.connect(lambda: changes.append(True))

    panel.update_settings({"output_dir": str(tmp_path), "docx_format": "comments"})

    assert panel._output_dir.text() == str(tmp_path)
    assert panel._docx_format.currentText() == "comments"
    assert changes == []


# ── Explanations in the proofreading instructions dialog ──────────────────────


@pytest.mark.gui
def test_prompt_dialog_returns_the_explanation_options_with_the_old_keys(qtbot) -> None:
    """
    Given  the proofreading instructions dialog opened with German explanations and reviews
    When   the user picks "Same as the text" and ticks "Corrections only"
    Then   explanation_values() returns explanation_language "" and correction_output "text"
    """
    from teachers_teammate.gui import _user_text as txt  # noqa: PLC0415
    from teachers_teammate.gui._config_panel import _PromptDialog  # noqa: PLC0415

    dlg = _PromptDialog("Proofreading", "", explanations=("German", "review"))
    qtbot.addWidget(dlg)
    assert dlg._explanation_language is not None
    assert dlg._corrections_only is not None
    assert dlg._explanation_language.currentText() == "German"
    assert not dlg._corrections_only.isChecked()
    assert dlg.explanation_values() == {
        "explanation_language": "German",
        "correction_output": "review",
    }

    dlg._explanation_language.setCurrentIndex(dlg._explanation_language.findText(txt.SAME_AS_TEXT))
    dlg._corrections_only.setChecked(True)

    assert dlg.explanation_values() == {"explanation_language": "", "correction_output": "text"}


@pytest.mark.gui
def test_review_instructions_have_no_explanation_options(qtbot) -> None:
    """
    Given  the content review instructions dialog (no explanation options)
    When   its values are read
    Then   there is no Explanations group and explanation_values() is empty
    """
    from teachers_teammate.gui._config_panel import _PromptDialog  # noqa: PLC0415

    dlg = _PromptDialog("Content Review", "")
    qtbot.addWidget(dlg)

    assert dlg._explanation_language is None
    assert dlg.explanation_values() == {}


@pytest.mark.gui
@pytest.mark.parametrize("button", ["_correction_prompt_btn", "_evaluation_prompt_btn"])
def test_confirming_an_instructions_dialog_tells_the_main_window(
    qtbot, monkeypatch, button
) -> None:
    """
    Given  the panel, so the main window can save what an Instructions dialog changed
    When   the proofreading or the content review instructions dialog is accepted, or cancelled
    Then   instructions_changed is emitted only for the accepted one
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    from teachers_teammate.gui._config_panel import _PromptDialog  # noqa: PLC0415

    answers = [QDialog.DialogCode.Rejected, QDialog.DialogCode.Accepted]
    monkeypatch.setattr(_PromptDialog, "exec", lambda self: answers.pop(0))
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    emitted: list[bool] = []
    panel.instructions_changed.connect(lambda: emitted.append(True))

    getattr(panel, button).click()
    assert emitted == []
    getattr(panel, button).click()

    assert emitted == [True]


@pytest.mark.gui
def test_accepting_the_proofreading_instructions_saves_the_explanation_options(
    qtbot, monkeypatch
) -> None:
    """
    Given  the panel with explanations in French
    When   the proofreading instructions dialog is accepted with "Corrections only" ticked
    Then   the panel's settings hold correction_output "text" and the French language
    """
    from PySide6.QtWidgets import QDialog  # noqa: PLC0415

    from teachers_teammate.gui._config_panel import _PromptDialog  # noqa: PLC0415

    def accept_with_corrections_only(dlg: _PromptDialog) -> QDialog.DialogCode:
        assert dlg._corrections_only is not None
        dlg._corrections_only.setChecked(True)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(_PromptDialog, "exec", accept_with_corrections_only)
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.update_settings({"explanation_language": "French", "correction_output": "review"})

    panel._correction_prompt_btn.click()

    settings = panel.get_settings_dict()
    assert settings["correction_output"] == "text"
    assert settings["explanation_language"] == "French"


@pytest.mark.gui
def test_the_output_folder_field_does_not_promise_a_blank_folder_works(qtbot) -> None:
    """
    Given  the Results step (a run with outputs but without a folder is refused)
    When   its folder field is empty
    Then   the placeholder asks for a folder instead of promising "save next to your documents"
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    assert panel._output_dir.placeholderText() == "Choose a folder for the results"
    assert "next to your documents" not in panel._output_dir.placeholderText()


# ── "Recognize text" is a real step ──────────────────────────────────────────


@pytest.mark.gui
def test_recognize_text_is_a_ticked_checkbox_that_every_config_reports(qtbot, tmp_path) -> None:
    """
    Given  a fresh panel
    When   "Recognize text" is left ticked and then unticked
    Then   it is a checkbox, on by default, and the run config, the lookup config and the
           saved settings all follow it
    """
    from PySide6.QtWidgets import QCheckBox  # noqa: PLC0415

    input_dir = tmp_path / "in"
    input_dir.mkdir()
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_input_dir(str(input_dir))
    panel.set_service(_GuardService())
    assert isinstance(panel._recognition_enabled, QCheckBox)
    assert panel._recognition_enabled.isChecked() is True
    assert panel.to_config().recognition_enabled is True

    panel._recognition_enabled.setChecked(False)

    assert panel.to_config().recognition_enabled is False
    assert panel.lookup_config().recognition_enabled is False
    assert panel.to_dict()["recognition_enabled"] is False
    assert panel.recognition_enabled() is False


@pytest.mark.gui
def test_recognize_text_is_restored_from_saved_settings_and_reset_by_defaults(qtbot) -> None:
    """
    Given  saved settings that switched text recognition off
    When   they are loaded, and later the defaults are restored
    Then   the box is unticked after loading and ticked again after the reset
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)

    panel.load_from_dict({"recognition_enabled": False})
    assert panel._recognition_enabled.isChecked() is False

    panel.restore_defaults()
    assert panel._recognition_enabled.isChecked() is True


@pytest.mark.gui
def test_switching_recognition_off_takes_it_out_of_the_data_flow_box(qtbot) -> None:
    """
    Given  a panel whose box lists text recognition (every step on this computer)
    When   "Recognize text" is unticked
    Then   the box no longer lists text recognition (no page is read), while the line under
           the step keeps naming what ticking it would use
    """
    from teachers_teammate.domain.data_location import STAGE_OCR  # noqa: PLC0415

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.set_service(_GuardService())
    assert panel.data_flow() is not None
    assert panel.data_flow().stage(STAGE_OCR) is not None

    panel._recognition_enabled.setChecked(False)

    assert panel.data_flow().stage(STAGE_OCR) is None
    assert panel._ocr_uses.text() != ""


@pytest.mark.gui
def test_ticking_recognition_checks_the_engine_and_unticks_when_it_is_missing(
    qtbot, monkeypatch
) -> None:
    """
    Given  recognition switched off, and an OCR engine that is not installed
    When   "Recognize text" is ticked
    Then   the requirement guard unticks it again and explains why, like any other step
    """
    from teachers_teammate.application.service import IssueKind, RequirementIssue  # noqa: PLC0415
    from teachers_teammate.gui._dependency_guard_dialog import DependencyGuardDialog  # noqa: PLC0415

    dialogs: list[object] = []
    monkeypatch.setattr(DependencyGuardDialog, "exec", lambda self: dialogs.append(self))

    class _Service(_GuardService):
        def check_stage_requirements(self, config, stage: str):
            assert stage == "ocr"
            return [RequirementIssue("Tesseract is missing.", IssueKind.ENGINE_MISSING)]

    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel._recognition_enabled.setChecked(False)
    panel.set_service(_Service())

    panel._recognition_enabled.setChecked(True)
    qtbot.waitUntil(lambda: not panel._recognition_enabled.isChecked(), timeout=3000)

    assert len(dialogs) == 1
    assert panel.to_dict()["recognition_enabled"] is False


@pytest.mark.gui
def test_the_recognition_note_counts_skipped_scans_or_says_nothing_is_needed(qtbot) -> None:
    """
    Given  a panel
    When   the main window reports 3 scans while recognition is off, then a text-only queue
           while it is on, then neither
    Then   the note says "3 scans will be skipped", then "Not needed", then it is hidden
    """
    panel = ConfigPanel()
    qtbot.addWidget(panel)
    panel.show()

    panel._recognition_enabled.setChecked(False)
    panel.show_recognition_note(skipped=3)
    assert panel._recognition_note.isVisible()
    assert "3 scans will be skipped" in panel._recognition_note.text()

    panel._recognition_enabled.setChecked(True)
    panel.show_recognition_note(not_needed=True)
    assert "Not needed" in panel._recognition_note.text()
    assert panel._recognition_note.isVisible()

    panel.show_recognition_note()
    assert not panel._recognition_note.isVisible()
