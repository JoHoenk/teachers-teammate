"""Unit tests for the run: its files, its requirements and its summary."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import contextlib
import dataclasses
from pathlib import Path
import sys
import threading
from unittest.mock import MagicMock, patch
import pytest
from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.application.composition import inject_addon_packages_dir
from teachers_teammate.application.run import RunPreflight, RunService
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.requirements import IssueKind, RequirementIssue
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.domain.teacher_work import MachineTexts, with_transcription
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.power import KEEP_AWAKE_REASON
from teachers_teammate.interfaces import ProgressCallbacks, SleepBlocker
from teachers_teammate.texts.issues import issue_text
from tests.conftest import fake_reader_store, make_config
from ._service_helpers import (
    _INSTALL,
    _build_services,
    _change_work,
    _computer,
    _discovery_of,
    _machine_of,
    _make_svc,
    _ollama_config,
    _proofread,
    _reader_service,
    _server_answers,
    reader_models_in_place,
)


@pytest.fixture(autouse=True)
def _reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service of this file a store that holds the built-in reader's models."""
    reader_models_in_place(tmp_path, monkeypatch)


@pytest.mark.unit
def test_run_selected_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  the application's services with an injected pipeline_factory
    When   run_selected() is called
    Then   pipeline_factory is called once and the RunSummary of its run() is forwarded
    """
    cfg = make_config(tmp_path)
    summary = RunSummary(succeeded=("a.png",))
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = summary
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run.run_selected(cfg, [])

    factory.assert_called_once()
    mock_pipeline.run.assert_called_once()
    assert result is summary


@pytest.mark.unit
def test_run_selected_passes_both_stop_events_to_the_pipeline(tmp_path: Path) -> None:
    """
    Given  a stop event ("Stop now") and a stop-after-file event
    When   run_selected() is called with them
    Then   the pipeline factory receives both
    """
    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = _make_svc(pipeline_factory=factory)
    now, after_file = threading.Event(), threading.Event()

    svc.run.run_selected(
        make_config(tmp_path), [], stop_event=now, stop_after_file_event=after_file
    )

    assert factory.call_args.kwargs["stop_event"] is now
    assert factory.call_args.kwargs["stop_after_file_event"] is after_file


class _RecordingBlocker(SleepBlocker):
    """Records when the computer is held and released, and what it was told."""

    def __init__(self, *, grants: bool = True) -> None:
        self.events: list[str] = []
        self._grants = grants

    def hold(self, reason: str):
        events = self.events

        @contextlib.contextmanager
        def held():
            events.append(f"hold:{reason}")
            try:
                yield self._grants
            finally:
                events.append("release")

        return held()


@pytest.mark.unit
def test_run_selected_holds_the_computer_awake_around_the_whole_run(tmp_path: Path) -> None:
    """
    Given  a service with a sleep blocker and keep_awake on (the default)
    When   run_selected() runs the pipeline
    Then   the computer is held before the pipeline runs and released after it, and the
           pipeline gets to know whether the system agreed
    """
    blocker = _RecordingBlocker()
    order: list[str] = []
    pipeline = MagicMock()
    pipeline.run.side_effect = lambda: order.append("run") or RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = _build_services(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run.run_selected(make_config(tmp_path), [])

    assert blocker.events == [f"hold:{KEEP_AWAKE_REASON}", "release"]
    assert order == ["run"]
    assert factory.call_args.kwargs["dependencies"].keep_awake.held is True


@pytest.mark.unit
def test_run_selected_reports_that_nothing_could_be_requested_without_a_blocker(
    tmp_path: Path,
) -> None:
    """
    Given  a computer with no way to ask for staying awake (WSL, a platform without a tool)
    When   run_selected() runs with keep_awake on
    Then   the state says it is enabled, not held and not available: nothing was refused
    """
    from teachers_teammate.infrastructure.power import NullSleepBlocker  # noqa: PLC0415

    pipeline = MagicMock()
    pipeline.run.return_value = RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = _build_services(pipeline_factory=factory, sleep_blocker=NullSleepBlocker())

    svc.run.run_selected(make_config(tmp_path), [])

    state = factory.call_args.kwargs["dependencies"].keep_awake
    assert (state.enabled, state.held, state.available) == (True, False, False)


@pytest.mark.unit
def test_run_selected_reports_a_refusal_when_a_blocker_exists(tmp_path: Path) -> None:
    """
    Given  a sleep blocker that exists but is refused by the system
    When   run_selected() runs
    Then   the state is not held but available: the system refused
    """
    pipeline = MagicMock()
    pipeline.run.return_value = RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = _build_services(pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False))

    svc.run.run_selected(make_config(tmp_path), [])

    state = factory.call_args.kwargs["dependencies"].keep_awake
    assert (state.held, state.available) == (False, True)


@pytest.mark.unit
def test_run_selected_releases_the_computer_when_the_pipeline_raises(tmp_path: Path) -> None:
    """
    Given  a pipeline that raises
    When   run_selected() is called
    Then   the exception propagates and the computer is released nevertheless
    """
    blocker = _RecordingBlocker()
    pipeline = MagicMock()
    pipeline.run.side_effect = RuntimeError("boom")
    svc = _build_services(pipeline_factory=MagicMock(return_value=pipeline), sleep_blocker=blocker)

    with pytest.raises(RuntimeError, match="boom"):
        svc.run.run_selected(make_config(tmp_path), [])

    assert blocker.events[-1] == "release"


@pytest.mark.unit
def test_run_selected_does_not_hold_when_keep_awake_is_off(tmp_path: Path) -> None:
    """
    Given  keep_awake turned off in the config
    When   run_selected() is called
    Then   the blocker is not used and the pipeline is told it is off
    """
    blocker = _RecordingBlocker()
    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = _build_services(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run.run_selected(make_config(tmp_path, keep_awake=False), [])

    assert blocker.events == []
    state = factory.call_args.kwargs["dependencies"].keep_awake
    assert (state.enabled, state.held) == (False, False)


@pytest.mark.unit
def test_run_selected_reports_the_keep_awake_state_and_a_refusal(tmp_path: Path) -> None:
    """
    Given  a blocker the system refuses, and a progress bundle with on_keep_awake
    When   run_selected() is called
    Then   the callback gets enabled=True, held=False once, before the pipeline is created
    """
    seen: list = []
    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = _build_services(pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False))

    svc.run.run_selected(
        make_config(tmp_path),
        [],
        progress=ProgressCallbacks(on_keep_awake=seen.append),
    )

    assert len(seen) == 1
    assert (seen[0].enabled, seen[0].held) == (True, False)


@pytest.mark.unit
def test_run_selected_forwards_progress_callbacks_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  the application's services with an injected pipeline_factory
    When   run_selected() is called with a ProgressCallbacks bundle
    Then   the same bundle is passed to the pipeline factory as ``progress``
    """
    factory = MagicMock(return_value=MagicMock(run=lambda: 0))
    svc = _make_svc(pipeline_factory=factory)
    progress = ProgressCallbacks(on_stage_started=MagicMock())

    svc.run.run_selected(make_config(tmp_path), [], progress=progress)

    assert factory.call_args.kwargs["progress"] is progress


@pytest.mark.unit
def test_run_preview_only_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  the application's services with an injected pipeline_factory
    When   run_preview_only() is called
    Then   pipeline_factory is called and run_preview_only() return value is forwarded
    """
    cfg = make_config(tmp_path)
    mock_pipeline = MagicMock()
    mock_pipeline.run_preview_only.return_value = 0
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run.run_preview_only(cfg, [])

    factory.assert_called_once_with(cfg, [])
    mock_pipeline.run_preview_only.assert_called_once()
    assert result == 0


# ── check_connection — engine checks ──────────────────────────────────────


@pytest.mark.unit
def test_list_supported_suffixes_returns_frozenset_with_images() -> None:
    """
    Given  the application's services
    When   list_supported_suffixes() is called
    Then   the returned frozenset includes .png, .pdf, and .txt
    """
    svc = _make_svc()
    suffixes = svc.run.list_supported_suffixes()
    assert ".png" in suffixes
    assert ".pdf" in suffixes
    assert ".txt" in suffixes


@pytest.mark.unit
def test_page_models_are_reported_skipped_only_for_an_automatic_run_that_reads_pages(
    tmp_path: Path,
) -> None:
    """
    Given  a service whose model store has no page models
    When   page_models_skipped() is asked for an automatic run, an "Original page" run and a
           run with recognition off
    Then   only the automatic run that reads pages reports the page models as skipped
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    svc = _build_services(
        pipeline_factory=MagicMock(),
        state_repository_factory=MagicMock(),
        model_store=ModelStore(tmp_path / "models"),
    )

    auto = make_config(tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.AUTO))
    original = make_config(tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.NONE))
    off = make_config(
        tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.AUTO), recognition_enabled=False
    )

    assert svc.run.page_models_skipped(auto) is True
    assert svc.run.page_models_skipped(original) is False
    assert svc.run.page_models_skipped(off) is False


# ── _invalidate_from_stage (static helper) ────────────────────────────────


@pytest.mark.unit
def test_reader_refusal_names_the_download_flag(tmp_path: Path) -> None:
    """
    Given  a run with the built-in reader and no model files, and one with Ollama
    When   reader_refusal_for_run() is asked
    Then   the first is refused with a sentence that names --download-reader, the second is not
    """
    svc = _reader_service(tmp_path)
    config = make_config(tmp_path / "r", ocr_engine="ppocr")
    config.input_dir.mkdir(parents=True, exist_ok=True)
    (config.input_dir / "scan.png").write_bytes(b"x")

    ollama = make_config(tmp_path / "o", ocr_engine="ollama")
    off = make_config(tmp_path / "s", ocr_engine="ppocr", recognition_enabled=False)
    files = [config.input_dir / "scan.png"]

    refusal = svc.run.reader_refusal_for_run(config, files)

    assert "--download-reader" in refusal
    assert svc.run.reader_refusal_for_run(ollama, files) == ""
    assert svc.run.reader_refusal_for_run(off, files) == ""


@pytest.mark.unit
def test_reader_refusal_lets_a_run_over_text_files_start(tmp_path: Path) -> None:
    """
    Given  the built-in reader without model files and an input folder with only a text file
    When   reader_refusal_for_run() is asked
    Then   the run over the text file is not refused (no page is read), but it is once the image is in
    """
    svc = _reader_service(tmp_path)
    config = make_config(tmp_path / "r", ocr_engine="ppocr")
    config.input_dir.mkdir(parents=True, exist_ok=True)
    (config.input_dir / "essay.txt").write_text("Hello", encoding="utf-8")

    text = config.input_dir / "essay.txt"
    scan = config.input_dir / "scan.png"
    scan.write_bytes(b"x")

    assert svc.run.reader_refusal_for_run(config, [text]) == ""
    assert "--download-reader" in svc.run.reader_refusal_for_run(config, [text, scan])


@pytest.fixture(autouse=True)
def _no_real_memory_reading(monkeypatch) -> None:
    """Keep the run-start memory check off the real computer: its memory cannot be read.

    Tests of the check itself set the figures they need.
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_available_memory_gb", lambda: None)
    # Not an Apple computer: that decides whether the whole model counts against the memory.
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)


@pytest.fixture(autouse=True)
def _no_real_ollama_program(monkeypatch) -> None:
    """Keep the Ollama program search off the real computer, so the kind is the same everywhere.

    A test that needs a program found patches it itself.
    """
    from teachers_teammate.infrastructure import ollama_install  # noqa: PLC0415

    monkeypatch.setattr(ollama_install, "find_ollama_program", lambda: None)
    monkeypatch.setattr(ollama_install, "can_start_ollama", lambda: False)


@pytest.mark.unit
def test_a_run_is_refused_when_the_model_would_have_to_swap(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a computer without a graphics card and 6 GB of free memory, and qwen3.5:9b (7.2 GB)
    When   the run is checked
    Then   the problem names the model and the memory, so the run does not start and swap
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    _computer(svc, monkeypatch, profile="cpu", free_gb=6.0)

    problem = svc.run.memory_warning_for_run(cfg)

    assert "qwen3.5:9b" in problem and "7 GB of free memory" in problem
    assert "6 GB are free" in problem and "smaller model" in problem


@pytest.mark.unit
def test_a_run_is_allowed_when_enough_memory_is_free(tmp_path: Path, monkeypatch) -> None:
    """
    Given  the same model and 12 GB of free memory (7.2 + the 2 GB margin fit)
    When   the run is checked
    Then   there is no problem
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    _computer(svc, monkeypatch, profile="cpu", free_gb=12.0)

    assert svc.run.memory_warning_for_run(cfg) == ""


@pytest.mark.unit
def test_only_what_does_not_fit_the_card_has_to_fit_the_free_memory(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  gemma4:12b (9.5 GB) on an 8 GB card (6.8 GB usable): 2.7 GB run on the processor
    When   4 GB and 6 GB of memory are free
    Then   4 GB is not enough (2.7 + 2 margin), 6 GB is
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr", correction_model="gemma4:12b")

    _computer(svc, monkeypatch, profile="8gb", free_gb=4.0, card_gb=8.0 * 0.85)
    assert "gemma4:12b" in svc.run.memory_warning_for_run(cfg)
    _computer(svc, monkeypatch, profile="8gb", free_gb=6.0, card_gb=8.0 * 0.85)
    assert svc.run.memory_warning_for_run(cfg) == ""


@pytest.mark.unit
def test_apple_silicon_needs_the_whole_model_from_the_shared_memory(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  Apple Silicon (the graphics share is carved out of the same memory), 6 GB free
    When   a 7.2 GB model is checked
    Then   all of it counts against the free memory, so it does not fit
    """
    from teachers_teammate.domain.model_catalog import DetectionReason  # noqa: PLC0415

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    _computer(
        svc,
        monkeypatch,
        profile="8gb",
        free_gb=6.0,
        card_gb=16.0,
        reason=DetectionReason.APPLE_UNIFIED_MEMORY,
    )

    assert "qwen3.5:9b" in svc.run.memory_warning_for_run(cfg)


@pytest.mark.unit
def test_the_memory_check_leaves_other_cases_alone(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a model that is not in the catalog, an Ollama on another computer, and a run
           without Ollama
    When   the memory is checked with almost nothing free
    Then   none of them is refused: the size is unknown, the memory is another computer's, or
           no model is loaded
    """
    svc = _make_svc()
    _computer(svc, monkeypatch, profile="cpu", free_gb=0.5)

    unknown = _ollama_config(tmp_path, ocr_engine="ppocr", correction_model="my-own:latest")
    remote = _ollama_config(tmp_path, ocr_engine="ppocr", ollama_url="http://gpu-box.lan:11434")
    no_ollama = _ollama_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    assert svc.run.memory_warning_for_run(unknown) == ""
    assert svc.run.memory_warning_for_run(remote) == ""
    assert svc.run.memory_warning_for_run(no_ollama) == ""


@pytest.mark.unit
def test_the_preflight_says_when_another_model_is_in_the_way(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a local Ollama that holds another model while the run's model is not in memory
    When   the run is checked
    Then   the preflight carries a note naming both, so the teacher knows why the first file
           waits — and nothing when the run's own model is there, or the server is another
           computer's
    """
    from teachers_teammate.domain.model_catalog import LoadedModel  # noqa: PLC0415
    from teachers_teammate.domain.ollama_discovery import WaitKind  # noqa: PLC0415
    from teachers_teammate.infrastructure.ollama_utils import ServerActivity  # noqa: PLC0415

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    other = LoadedModel("ministral-3:8b", 1, 1)
    monkeypatch.setattr(
        svc.ollama,
        "activity",
        lambda _url, _model="": ServerActivity(loaded=(other,), busy=("ministral-3:8b",)),
    )

    detail = svc.run._ollama_wait(cfg)

    assert detail.kind is WaitKind.QUEUED
    assert (detail.model, detail.other) == ("qwen3.5:9b", "ministral-3:8b")

    mine = LoadedModel("qwen3.5:9b", 1, 1)
    monkeypatch.setattr(
        svc.ollama, "activity", lambda _url, _model="": ServerActivity(loaded=(mine,))
    )
    assert not svc.run._ollama_wait(cfg)

    remote = _ollama_config(tmp_path, ocr_engine="ppocr", ollama_url="http://gpu-box.lan:11434")
    assert not svc.run._ollama_wait(remote)


@pytest.mark.unit
def test_preflight_reports_missing_memory_as_a_warning_the_user_may_accept(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  all models downloaded but too little free memory
    When   the run preflight is computed
    Then   nothing blocks the run, but it needs the user's confirmation (the memory warning)
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)
    _computer(svc, monkeypatch, profile="cpu", free_gb=6.0)

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        result = svc.run.check_run_preflight(cfg)

    assert result.blocked is False
    assert "free memory" in result.memory_warning


@pytest.mark.unit
def test_a_stage_issue_wins_over_the_memory_warning(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a computer with too little free memory and a proofreading provider package that is
           not installed
    When   the run preflight is computed
    Then   the stage issue is reported (and blocks the run); the memory is not looked at all,
           so a "Run anyway" could not start a run whose stages were never checked
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, correction_provider="mistral")
    _computer(svc, monkeypatch, profile="cpu", free_gb=6.0)
    monkeypatch.setattr(
        svc.providers, "missing_provider_package", lambda _provider: "langchain-mistralai"
    )

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        result = svc.run.check_run_preflight(cfg)

    assert result.blocked is True
    assert result.stage == "correction"
    assert result.memory_warning == ""


@pytest.mark.unit
def test_preflight_blocks_ollama_cloud_ocr_model_without_contacting_the_server(
    tmp_path: Path,
) -> None:
    """
    Given  an OCR model that runs on Ollama's servers
    When   the run preflight is computed
    Then   the run is refused first, with the reason, and Ollama is never asked
    """
    svc = _make_svc(discovery=_discovery_of("page.png"))
    cfg = _ollama_config(tmp_path, ocr_model="qwen3-vl:235b-cloud")

    with _server_answers([]) as request:
        result = svc.run.check_run_preflight(cfg)

    request.assert_not_called()
    assert result.blocked is True
    assert "Ollama's servers" in result.ocr_not_allowed


@pytest.mark.unit
def test_preflight_blocks_online_ocr_engine(tmp_path: Path) -> None:
    """
    Given  a config whose OCR engine is an online vision model
    When   the run preflight is computed
    Then   the run is refused because pages cannot be anonymised before they are read
    """
    cfg = make_config(tmp_path, ocr_engine="langchain")

    result = _make_svc(discovery=_discovery_of("page.png")).run.check_run_preflight(cfg)

    assert result.blocked is True
    assert "whole page" in result.ocr_not_allowed


@pytest.mark.unit
def test_preflight_refuses_cloud_ocr_settings_whatever_the_files(tmp_path: Path) -> None:
    """
    Given  OCR settings that would send pages to Ollama's servers and a folder of .txt files
    When   the run preflight is computed
    Then   OCR is still refused: the rule belongs to the settings, not to which files are run
    """
    svc = _make_svc(discovery=_discovery_of("a.txt", "b.TXT"))
    cfg = _ollama_config(tmp_path, ocr_model="qwen3-vl:235b-cloud", correction_enabled=False)

    result = svc.run.check_run_preflight(cfg)

    assert "Ollama's servers" in result.ocr_not_allowed


@pytest.mark.unit
def test_ocr_refusal_for_run_is_empty_for_a_local_model(tmp_path: Path) -> None:
    """
    Given  OCR settings with a model downloaded to Ollama
    When   ocr_refusal_for_run is asked
    Then   nothing is refused
    """
    cfg = _ollama_config(tmp_path, ocr_model="qwen3-vl:8b")

    assert _make_svc().run.ocr_refusal_for_run(cfg) == ""


@pytest.mark.unit
@pytest.mark.unit
def test_preflight_reports_unreachable_ollama(tmp_path: Path) -> None:
    """
    Given  a config that needs Ollama and a server that cannot be reached
    When   the run preflight is computed
    Then   it carries the connection message and nothing else
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers([], "✗ Cannot connect to Ollama") as request:
        result = svc.run.check_run_preflight(cfg)

    request.assert_called_once()  # one timeout, not one per question
    assert result.blocked is True
    assert result.ollama_unreachable == "✗ Cannot connect to Ollama"
    assert result.missing_models == ()


@pytest.mark.unit
def test_a_local_address_without_a_program_says_ollama_was_not_found(tmp_path: Path) -> None:
    """
    Given  a local Ollama address that does not answer and no Ollama program on the computer
    When   the run preflight is computed
    Then   the blocker says Ollama wasn't found, so the fix offers the download page
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with (
        _server_answers([], "✗ Cannot connect to Ollama"),
        patch(f"{_INSTALL}.find_ollama_program", return_value=None),
    ):
        result = svc.run.check_run_preflight(cfg)

    blocker = result.blocker
    assert blocker is not None
    assert blocker[1].kind is IssueKind.OLLAMA_NOT_FOUND
    assert blocker[1].startable is False


@pytest.mark.unit
@pytest.mark.parametrize(("platform", "startable"), [("win32", True), ("linux", False)])
def test_a_local_address_with_a_program_says_ollama_is_not_running(
    tmp_path: Path, platform: str, startable: bool
) -> None:
    """
    Given  a local Ollama address that does not answer, but the program is installed
    When   the run preflight is computed
    Then   the blocker says Ollama isn't running, and only Windows and macOS offer to start it
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with (
        _server_answers([], "✗ Cannot connect to Ollama"),
        patch(f"{_INSTALL}.find_ollama_program", return_value=Path("/usr/bin/ollama")),
        patch(f"{_INSTALL}.can_start_ollama", return_value=startable),
    ):
        result = svc.run.check_run_preflight(cfg)

    blocker = result.blocker
    assert blocker is not None
    assert blocker[1].kind is IssueKind.OLLAMA_NOT_RUNNING
    assert blocker[1].startable is startable


@pytest.mark.unit
def test_an_address_on_another_computer_is_only_unreachable(tmp_path: Path) -> None:
    """
    Given  an Ollama address on another computer that does not answer
    When   the run preflight is computed
    Then   the blocker stays "unreachable": nothing can be installed or started here
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ollama_url="http://192.168.1.20:11434")

    with (
        _server_answers([], "✗ Cannot connect to Ollama"),
        patch(f"{_INSTALL}.find_ollama_program", return_value=Path("/usr/bin/ollama")),
    ):
        result = svc.run.check_run_preflight(cfg)

    blocker = result.blocker
    assert blocker is not None
    assert blocker[1].kind is IssueKind.OLLAMA_UNREACHABLE


@pytest.mark.unit
def test_preflight_lists_missing_models(tmp_path: Path) -> None:
    """
    Given  a reachable server that only has the reading model downloaded
    When   the run preflight is computed
    Then   the proofreading model is reported as missing
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers(["qwen3-vl:8b"]):
        result = svc.run.check_run_preflight(cfg)

    assert result.missing_models == ("qwen3.5:9b",)
    assert result.ollama_unreachable == ""


@pytest.mark.unit
def test_preflight_connected_server_without_models_reports_all_missing(tmp_path: Path) -> None:
    """
    Given  a reachable Ollama server with no model downloaded at all
    When   the run preflight is computed
    Then   it is not called "unreachable": both configured models are missing
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers([]):
        result = svc.run.check_run_preflight(cfg)

    assert result.ollama_unreachable == ""
    assert result.missing_models == ("qwen3-vl:8b", "qwen3.5:9b")


@pytest.mark.unit
def test_preflight_ok_when_everything_is_available(tmp_path: Path) -> None:
    """
    Given  a reachable server with all configured models downloaded
    When   the run preflight is computed
    Then   the run may start
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        result = svc.run.check_run_preflight(cfg)

    assert result.blocked is False


@pytest.mark.unit
def test_preflight_needs_no_ollama_for_other_engines(tmp_path: Path) -> None:
    """
    Given  a config that uses the built-in reader and no Ollama stage
    When   the run preflight is computed
    Then   the Ollama server is never contacted and the run may start
    """
    svc = _make_svc(model_store=fake_reader_store(tmp_path))
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    with (
        patch(
            "teachers_teammate.application.providers.list_provider_models_checked",
            side_effect=AssertionError("Ollama must not be contacted"),
        ),
    ):
        result = svc.run.check_run_preflight(cfg)

    assert result.blocked is False


@pytest.mark.unit
def test_stage_requirements_distinguish_no_models_from_not_running(tmp_path: Path) -> None:
    """
    Given  a server that answers but has no model downloaded
    When   the proofreading requirements are checked
    Then   the issue says no model is downloaded (a download fixes it), not "not running"
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers([]):
        issues = svc.run.check_stage_requirements(cfg, "correction")

    assert [issue_text(i) for i in issues] == ["No model is downloaded in Ollama yet."]
    assert issues[0].kind is IssueKind.NO_OLLAMA_MODEL

    with _server_answers([], "✗ Cannot connect to Ollama") as request:
        issues = svc.run.check_stage_requirements(cfg, "correction")

    request.assert_called_once()
    assert [issue_text(i) for i in issues] == ["✗ Cannot connect to Ollama"]
    assert issues[0].kind is IssueKind.OLLAMA_UNREACHABLE


@pytest.mark.unit
def test_preflight_asks_an_installed_models_server_only_once(tmp_path: Path) -> None:
    """
    Given  a reachable server whose model list was fetched by an earlier check
    When   the run preflight and the stage requirements are computed within the cache TTL
    Then   the server is not asked again
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]) as request:
        assert svc.run.check_run_preflight(cfg).blocked is False
        assert svc.run.check_stage_requirements(cfg, "correction") == []

    request.assert_called_once()


@pytest.mark.unit
def test_unreachable_server_is_asked_again_on_the_next_check(tmp_path: Path) -> None:
    """
    Given  a server that was unreachable at the first preflight
    When   it has been started and the preflight is computed again
    Then   the failure was not cached and the run may start
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers([], "✗ Cannot connect to Ollama"):
        assert svc.run.check_run_preflight(cfg).ollama_unreachable
    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        assert svc.run.check_run_preflight(cfg).blocked is False


@pytest.mark.unit
def test_output_refusal_for_text_files_in_input_folder(tmp_path: Path) -> None:
    """
    Given  text output enabled with the output folder inside the input folder
    When   output_refusal_for_run() is asked
    Then   the run is refused; outside the input folder, or without text files, it is fine
    """
    inputs = tmp_path / "in"
    (inputs / "out").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    refusal = RunService.output_refusal_for_run
    inside = make_config(
        tmp_path, input_dir=inputs, output_dir=inputs / "out", recursive=True, text_enabled=True
    )

    assert "input folder" in refusal(inside)
    assert refusal(dataclasses.replace(inside, text_enabled=False, docx_enabled=True)) == ""
    assert refusal(dataclasses.replace(inside, output_dir=tmp_path / "out")) == ""
    assert refusal(dataclasses.replace(inside, text_enabled=False, text_anonymized_enabled=True))


@pytest.mark.unit
def test_preflight_checks_anonymizer_for_anonymized_text_files(tmp_path: Path) -> None:
    """
    Given  anonymized text output enabled (anonymization before proofreading off)
           and no spaCy model installed
    When   check_run_preflight() runs
    Then   the anonymizer requirement is reported
    """
    cfg = make_config(tmp_path, ocr_engine="ppocr", text_anonymized_enabled=True)
    svc = _make_svc()

    with patch.object(svc.anonymization, "list_installed_spacy_models", return_value=[]):
        result = svc.run.check_run_preflight(cfg)

    assert result.stage == "anonymizer"


# ── provider packages and model-list source ───────────────────────────────


@pytest.mark.unit
def test_stage_requirements_report_a_missing_provider_package_before_asking_the_server(
    tmp_path: Path,
) -> None:
    """
    Given  proofreading with Mistral while langchain_mistralai is not installed
    When   the stage requirements are checked
    Then   one issue says Mistral support is not installed, is of the package kind, and names
           the provider so the app can open the providers tab with it ticked
    """
    svc = _make_svc()
    cfg = dataclasses.replace(
        _ollama_config(tmp_path), correction_provider="mistral", correction_model="m"
    )

    with patch.object(svc.addons, "is_module_importable", lambda *_a: False):
        issues = svc.run.check_stage_requirements(cfg, "correction")

    assert [(issue_text(i), i.kind, i.provider) for i in issues] == [
        ("Mistral support is not installed yet.", IssueKind.PACKAGE_MISSING, "mistral")
    ]


@pytest.mark.unit
def test_stage_requirements_report_a_missing_api_key_after_the_package_check(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  proofreading with an online provider that needs a key
    When   the stage requirements are checked without the key, then with the package missing
    Then   the first gives a KEY_MISSING issue naming the service, the second only the
           package (installing comes first)
    """
    svc = _make_svc()
    cfg = dataclasses.replace(
        _ollama_config(tmp_path), correction_provider="mistral_eu", correction_model="m"
    )
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    with patch.object(svc.providers, "_importable", lambda *_a: True):
        issues = svc.run.check_stage_requirements(cfg, "correction")
    with patch.object(svc.providers, "_importable", lambda *_a: False):
        package_first = svc.run.check_stage_requirements(cfg, "correction")

    assert [(i.kind, i.provider) for i in issues] == [(IssueKind.KEY_MISSING, "mistral_eu")]
    assert "Mistral" in issue_text(issues[0])
    assert [i.kind for i in package_first] == [IssueKind.PACKAGE_MISSING]
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    with patch.object(svc.providers, "_importable", lambda *_a: True):
        assert svc.run.check_stage_requirements(cfg, "correction") == []


@pytest.mark.unit
def test_stage_requirements_ask_for_the_add_on_update_after_an_app_update(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  spaCy and langchain-mistralai in an add-on folder that an app update made outdated
    When   removing names and proofreading with Mistral are checked
    Then   both report ADDONS_OUTDATED instead of "not installed"; a stage that needs no
           add-on (reading) is unaffected
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    monkeypatch.setattr(addon_manager, "addons_outdated", lambda: True)
    monkeypatch.setattr(addon_manager, "addon_in_folder", lambda _name: True)
    svc = _make_svc(model_store=fake_reader_store(tmp_path / "models"))
    cfg = dataclasses.replace(
        _ollama_config(tmp_path), correction_provider="mistral", correction_model="m"
    )

    with patch.object(svc.addons, "is_module_importable", lambda *_a: False):
        correction = svc.run.check_stage_requirements(cfg, "correction")
    anonymizer = svc.run.check_stage_requirements(cfg, "anonymizer")

    assert [i.kind for i in correction] == [IssueKind.ADDONS_OUTDATED]
    assert [i.kind for i in anonymizer] == [IssueKind.ADDONS_OUTDATED]
    assert svc.run.check_stage_requirements(cfg, "ocr") == []


@pytest.mark.unit
def test_stage_requirements_ask_for_a_restart_while_a_package_waits(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  spaCy waiting in packages.pending/ after an install that could not replace a file
    When   removing names is checked
    Then   the issue is ADDON_RESTART_NEEDED and says what a restart finishes
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    monkeypatch.setattr(addon_manager, "addon_waits_for_restart", lambda _name: True)
    svc = _make_svc(model_store=fake_reader_store(tmp_path / "models"))
    cfg = _ollama_config(tmp_path)

    issues = svc.run.check_stage_requirements(cfg, "anonymizer")

    assert [i.kind for i in issues] == [IssueKind.ADDON_RESTART_NEEDED]
    assert issue_text(issues[0]) == "Restart the app to finish installing name detection."


@pytest.mark.unit
def test_an_outdated_folder_without_the_needed_add_on_changes_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  an outdated add-on folder that holds no Mistral package
    When   proofreading with Mistral is checked
    Then   the package is reported missing as before (installing it is the fix)
    """
    from teachers_teammate.infrastructure import addon_manager  # noqa: PLC0415

    monkeypatch.setattr(addon_manager, "addons_outdated", lambda: True)
    monkeypatch.setattr(addon_manager, "addon_in_folder", lambda _name: False)
    svc = _make_svc()
    cfg = dataclasses.replace(
        _ollama_config(tmp_path), correction_provider="mistral", correction_model="m"
    )

    with patch.object(svc.addons, "is_module_importable", lambda *_a: False):
        issues = svc.run.check_stage_requirements(cfg, "correction")

    assert [i.kind for i in issues] == [IssueKind.PACKAGE_MISSING]


@pytest.mark.unit
def test_the_ocr_stage_reports_a_missing_reader(tmp_path: Path) -> None:
    """
    Given  the built-in reader chosen, once without and once with its model files
    When   the requirements of the "ocr" stage are checked
    Then   a READER_MODEL_MISSING issue is reported without them and none with them
    """
    cfg = make_config(tmp_path, ocr_engine="ppocr")

    issues = _make_svc(
        model_store=fake_reader_store(tmp_path / "a", installed=False)
    ).run.check_stage_requirements(cfg, "ocr")
    present = _make_svc(model_store=fake_reader_store(tmp_path / "b")).run.check_stage_requirements(
        cfg, "ocr"
    )

    assert [i.kind for i in issues] == [IssueKind.READER_MODEL_MISSING]
    assert present == []


@pytest.mark.unit
def test_the_ocr_stage_asks_nothing_for_ollama(tmp_path: Path) -> None:
    """
    Given  Ollama as the text-recognition engine
    When   the "ocr" stage is checked
    Then   there is no issue and no server is contacted (the preflight checks the server before)
    """
    svc = _make_svc()

    with patch(
        "teachers_teammate.application.providers.list_provider_models_checked",
        side_effect=AssertionError("Ollama must not be contacted"),
    ):
        assert svc.run.check_stage_requirements(_ollama_config(tmp_path), "ocr") == []


@pytest.mark.unit
def test_a_missing_reader_blocks_the_preflight_in_the_ocr_stage(tmp_path: Path) -> None:
    """
    Given  the built-in reader chosen but not downloaded
    When   the run preflight is computed
    Then   the run is blocked in stage "ocr", before the other steps are looked at
    """
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)
    svc = _make_svc(model_store=fake_reader_store(tmp_path, installed=False))

    result = svc.run.check_run_preflight(cfg)

    assert result.blocked is True
    assert result.blocker is not None
    assert result.blocker[0] == "ocr"
    assert result.blocker[1].kind is IssueKind.READER_MODEL_MISSING


@pytest.mark.unit
@pytest.mark.parametrize(
    ("files", "recognised", "expected"),
    [
        (["a.txt", "b.TXT"], [], False),
        (["scan.png"], [], True),
        (["scan.png"], ["scan.png"], False),
        (["scan.png", "notes.txt"], ["notes.txt"], True),
        (["scan.pdf"], [], True),
        ([], [], True),
    ],
    ids=["text-only", "image", "image-recognised", "image-and-text", "pdf", "nothing-queued"],
)
def test_needs_text_recognition_only_for_pages_that_are_not_read_yet(
    files: list[str], recognised: list[str], expected: bool
) -> None:
    """
    Given  queued files, some of them text files or already recognised
    When   the service is asked whether a run over them reads pages
    Then   only an image or PDF without stored text counts (nothing queued counts too)
    """
    needs = RunService.needs_text_recognition(
        [Path(f) for f in files], recognised={Path(f) for f in recognised}
    )

    assert needs is expected


@pytest.mark.unit
def test_a_run_that_reads_no_page_is_not_blocked_by_a_missing_reader(tmp_path: Path) -> None:
    """
    Given  the reader chosen but not downloaded, and a run over text files only
    When   the preflight runs with recognition=False
    Then   the run is not blocked, while the same preflight with recognition blocks it
    """
    svc = _make_svc(model_store=fake_reader_store(tmp_path, installed=False))
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    reads_nothing = svc.run.check_run_preflight(cfg, recognition=False)
    reads_pages = svc.run.check_run_preflight(cfg)

    assert reads_nothing.blocked is False
    assert reads_pages.blocker is not None
    assert reads_pages.blocker[1].kind is IssueKind.READER_MODEL_MISSING


@pytest.mark.unit
def test_a_fresh_preflight_sees_a_model_pulled_since_the_list_was_cached(tmp_path: Path) -> None:
    """
    Given  a model list cached without the proofreading model, which was pulled afterwards
    When   the preflight runs once as usual and once with fresh=True
    Then   the first still reports the model missing and the second asks the server again
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers(["qwen3-vl:8b"]):
        assert svc.run.check_run_preflight(cfg).missing_models == ("qwen3.5:9b",)
    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]) as request:
        assert svc.run.check_run_preflight(cfg).missing_models == ("qwen3.5:9b",)
        request.assert_not_called()
        assert svc.run.check_run_preflight(cfg, fresh=True).blocked is False
        request.assert_called_once()


@pytest.mark.unit
def test_preflight_without_memory_never_asks_the_memory_question(tmp_path: Path) -> None:
    """
    Given  a config the readiness line checks
    When   the preflight runs with memory=False
    Then   memory_warning_for_run is never called, so a bare readiness question stays cheap
    """
    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    with patch.object(svc.run, "memory_warning_for_run", side_effect=AssertionError("asked")):
        result = svc.run.check_run_preflight(cfg, memory=False)

    assert result.blocked is False
    assert result.memory_warning == ""


@pytest.mark.unit
def test_the_blocker_is_the_first_unmet_requirement_in_one_shape() -> None:
    """
    Given  a preflight result for each kind of refusal, and one without any
    When   the blocker is read
    Then   each gives (stage, issue) with the matching kind and message, and a clear run gives
           None
    """
    issue = RequirementIssue(IssueKind.KEY_MISSING, provider="openai")

    def blocker(**fields) -> tuple[str, RequirementIssue]:
        found = RunPreflight(**fields).blocker
        assert found is not None
        return found

    assert RunPreflight().blocker is None
    stage, found = blocker(ocr_not_allowed="Cloud model.")
    assert (stage, found.kind) == ("ocr", IssueKind.OCR_NOT_ALLOWED)
    assert issue_text(found) == "Cloud model."
    stage, found = blocker(ollama_unreachable="✗ down")
    assert (stage, found.kind) == ("", IssueKind.OLLAMA_UNREACHABLE)
    stage, found = blocker(missing_models=("a:1", "b:2"))
    assert (stage, found.kind, found.models) == ("", IssueKind.MODEL_MISSING, ("a:1", "b:2"))
    assert issue_text(found) == "Models 'a:1', 'b:2' are not downloaded."
    assert blocker(stage="evaluation", stage_issues=(issue,)) == ("evaluation", issue)


@pytest.mark.unit
def test_stage_requirements_carry_the_models_to_download(tmp_path: Path) -> None:
    """
    Given  a model that is not downloaded, and a language without its name-detection model
    When   the requirements are checked
    Then   the issues carry the Ollama tag and the spaCy model, so "Download…" can tick them
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)

    with _server_answers(["other:1"]):
        model_issue = svc.run.check_stage_requirements(cfg, "correction")[0]
    with patch.object(
        svc.anonymization, "list_installed_spacy_models", return_value=["en_core_web_sm"]
    ):
        name_issue = svc.run.check_stage_requirements(
            dataclasses.replace(cfg, language="German"), "anonymizer"
        )[0]

    assert model_issue.kind is IssueKind.MODEL_MISSING
    assert model_issue.models == (cfg.effective_correction_model,)
    assert name_issue.kind is IssueKind.NAME_DETECTION_MISSING
    assert name_issue.models == (svc.anonymization.spacy_model_for_language("German"),)


@pytest.mark.unit
def test_a_missing_spacy_is_told_apart_from_a_missing_model(tmp_path: Path) -> None:
    """
    Given  no name detection model installed, once with spaCy importable and once without
    When   removing names is checked
    Then   the first asks for the model (NAME_DETECTION_MISSING), the second for spaCy itself
           (PRIVACY_ADDON_MISSING), and both carry the model the fix ticks
    """
    svc = _make_svc()
    cfg = dataclasses.replace(_ollama_config(tmp_path), language="german")

    with (
        patch.object(svc.anonymization, "list_installed_spacy_models", return_value=[]),
        patch.object(svc.addons, "is_module_importable", lambda *_a: True),
    ):
        model_missing = svc.run.check_stage_requirements(cfg, "anonymizer")
    with (
        patch.object(svc.anonymization, "list_installed_spacy_models", return_value=[]),
        patch.object(svc.addons, "is_module_importable", lambda *_a: False),
    ):
        spacy_missing = svc.run.check_stage_requirements(cfg, "anonymizer")

    assert [i.kind for i in model_missing] == [IssueKind.NAME_DETECTION_MISSING]
    assert [i.kind for i in spacy_missing] == [IssueKind.PRIVACY_ADDON_MISSING]
    assert spacy_missing[0].models == (svc.anonymization.spacy_model_for_language("german"),)


# ── Shared anonymization cache (Phase 3) ─────────────────────────────────────


@pytest.mark.unit
@pytest.mark.unit
@pytest.mark.unit
def test_run_selected_passes_a_component_factory_with_the_shared_anonymizer(tmp_path: Path) -> None:
    """
    Given  a service with its own component factory
    When   run_selected() builds the pipeline
    Then   the dependencies carry a factory whose build_anonymizer is the service's
           cached builder, so the run fills the same cache a preview reads
    """
    factory = MagicMock(return_value=MagicMock(run=lambda: 0))
    svc = _make_svc(pipeline_factory=factory)

    svc.run.run_selected(make_config(tmp_path), [])

    dependencies = factory.call_args.kwargs["dependencies"]
    assert dependencies is not None
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        built = dependencies.component_factory.build_anonymizer(
            language="English", config=AnonymizerConfig()
        )
        built.anonymize("Anna wrote")

    assert len(svc.anonymization._anonymization_cache) == 1


# ── What is sent for this file (Phase 4) ─────────────────────────────────────


@pytest.mark.unit
def test_a_spacy_model_downloaded_while_the_app_runs_is_found_at_once(tmp_path: Path) -> None:
    """
    Given  an app that is running with spaCy in its add-on folder and no German model, so name
           detection is reported as not downloaded
    When   the model arrives in that folder and the add-on folder is injected again (what the
           install round does after a download)
    Then   name detection is available and the requirement check no longer asks for the model,
           without a restart
    """
    packages = tmp_path / "packages"
    (packages / "spacy").mkdir(parents=True)
    svc = _make_svc()
    original = sys.path.copy()
    try:
        with patch(
            "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
            return_value=packages,
        ):
            inject_addon_packages_dir()
            assert not svc.anonymization.anonymizer_available("German")
            assert [issue.kind for issue in svc.run._name_detection_issues("German")] == [
                IssueKind.NAME_DETECTION_MISSING
            ]

            (packages / "de_core_news_sm").mkdir()
            inject_addon_packages_dir()
            # What the install round does after a download.
            svc.anonymization.forget_installed_models()

            assert svc.anonymization.anonymizer_available("German")
            assert svc.run._name_detection_issues("German") == []
    finally:
        sys.path[:] = original


# ── Text recognition switched off ────────────────────────────────────────────


@pytest.mark.unit
def test_recognition_plan_separates_files_with_text_from_scans(tmp_path: Path) -> None:
    """
    Given  a text file, an image, a recognised image, and two PDFs (one with a text layer)
    When   the service plans a run with text recognition switched off
    Then   the text file, the recognised image and the text PDF are readable; the image and
           the scanned PDF are the scans a run leaves out
    """
    svc = _make_svc()
    files = [tmp_path / n for n in ("a.txt", "scan.png", "done.jpg", "typed.pdf", "scanned.pdf")]
    for file in files:
        file.write_bytes(b"x")
    has_text = {"typed.pdf"}

    with patch(
        "teachers_teammate.infrastructure.input_providers.pdf_has_text_layer",
        side_effect=lambda path, mode: path.name in has_text,
    ):
        plan = svc.run._plan_recognition(files, recognised={tmp_path / "done.jpg"})

    assert [p.name for p in plan.readable] == ["a.txt", "done.jpg", "typed.pdf"]
    assert [p.name for p in plan.skipped] == ["scan.png", "scanned.pdf"]


@pytest.mark.unit
def test_recognition_plan_treats_an_unreadable_pdf_as_readable(tmp_path: Path) -> None:
    """
    Given  a PDF that cannot be opened to look for a text layer
    When   the service plans a run with text recognition switched off
    Then   the PDF is not guessed to be a scan: the run reports the real problem
    """
    svc = _make_svc()
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    with patch(
        "teachers_teammate.infrastructure.input_providers.pdf_has_text_layer",
        side_effect=RuntimeError("corrupt"),
    ):
        plan = svc.run._plan_recognition([broken])

    assert plan.skipped == ()


@pytest.mark.unit
def test_preflight_with_recognition_off_and_only_scans_has_nothing_to_read(tmp_path: Path) -> None:
    """
    Given  recognition switched off and a queue of images only
    When   the run preflight is computed with the queued files
    Then   the run is blocked with NOTHING_TO_READ and lists the scans
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, recognition_enabled=False, correction_enabled=False)
    scans = [Path("a.png"), Path("b.jpg")]

    with _server_answers([]) as request:
        result = svc.run.check_run_preflight(cfg, files=scans)

    request.assert_not_called()
    assert result.blocked is True
    assert result.blocker is not None
    assert result.blocker[1].kind is IssueKind.NOTHING_TO_READ
    assert list(result.skipped) == scans


@pytest.mark.unit
def test_preflight_with_recognition_off_runs_the_text_and_lists_the_skipped_scans(
    tmp_path: Path,
) -> None:
    """
    Given  recognition switched off, an Ollama OCR model that is not downloaded, and a queue of
           a text file and an image
    When   the run preflight is computed with the queued files
    Then   the run is not blocked (no OCR model is needed to read no page), no engine is
           checked, and the image is reported as skipped
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, recognition_enabled=False, correction_enabled=False)

    with _server_answers([]) as request:
        result = svc.run.check_run_preflight(cfg, files=[Path("notes.txt"), Path("scan.png")])

    request.assert_not_called()
    assert result.blocked is False
    assert [p.name for p in result.skipped] == ["scan.png"]


@pytest.mark.unit
def test_preflight_with_recognition_on_lists_no_skipped_scans(tmp_path: Path) -> None:
    """
    Given  recognition switched on and a queue of a text file and an image
    When   the run preflight is computed with the queued files
    Then   nothing is skipped
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, correction_enabled=False)

    with _server_answers(["qwen3-vl:8b"]):
        result = svc.run.check_run_preflight(cfg, files=[Path("notes.txt"), Path("scan.png")])

    assert result.skipped == ()
    assert result.blocked is False


@pytest.mark.unit
def test_cloud_ocr_settings_are_not_refused_while_recognition_is_off(tmp_path: Path) -> None:
    """
    Given  an OCR model that would read pages on Ollama's servers, but recognition switched off
    When   the refusal for the run is asked
    Then   nothing is refused: no page is read, so no page can leave the computer
    """
    svc = _make_svc()
    cfg = _ollama_config(
        tmp_path,
        ocr_model="qwen3-vl:235b-cloud",
        recognition_enabled=False,
        correction_enabled=False,
    )

    assert svc.run.ocr_refusal_for_run(cfg) == ""
    assert svc.run._configured_ollama_models(cfg) == []


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_preflight_reads_a_scan_the_teacher_transcribed_with_recognition_off(
    tmp_path: Path,
) -> None:
    """
    Given  recognition switched off, a scan whose transcription the teacher stored, and the
           results cache emptied
    When   the run preflight is computed with the queued file
    Then   the scan is not left out and the run is not blocked: its text is the teacher's
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, recognition_enabled=False, correction_enabled=False)
    scan = tmp_path / "scan.png"
    scan.write_bytes(b"page")
    _change_work(
        scan,
        lambda work: with_transcription(
            work,
            "what the teacher read",
            "plain",
            MachineTexts(raw_text="", raw_text_format="plain"),
        ),
    )

    with _server_answers([]):
        result = svc.run.check_run_preflight(cfg, files=[scan])

    assert result.blocked is False
    assert result.skipped == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_preflight_looks_at_the_teachers_work_only_when_asked(tmp_path: Path) -> None:
    """
    Given  a proofread document whose transcription the teacher fixed
    When   the preflight is computed without, and then with, the question about their work
    Then   only the second names the document, so a check that is not a run reads no file
    """
    made, repo, [source] = _proofread(tmp_path, "essay.txt")
    machine = _machine_of(repo, source)
    _change_work(
        source, lambda work: with_transcription(work, "the essay", machine.raw_text_format, machine)
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    with patch.object(svc.run, "_check_requirements", return_value=RunPreflight()):
        plain = svc.run.check_run_preflight(made, files=[source])
        asked = svc.run.check_run_preflight(made, files=[source], ask_about_work=True)

    assert plain.worked_on.empty is True
    assert asked.worked_on.transcribed == (source,)


@pytest.mark.unit
def test_the_memory_check_does_not_probe_the_graphics_cards(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a profile chosen by hand and a computer whose graphics probe must not run
    When   the memory is checked before a run
    Then   the check still refuses the model, and never asked for the graphics cards
    """
    from teachers_teammate.domain.model_catalog import DetectionReason  # noqa: PLC0415
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    def _no_probe() -> list:
        raise AssertionError("the graphics cards were probed")

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    _computer(svc, monkeypatch, profile="8gb", free_gb=0.5, reason=DetectionReason.MANUAL)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_gpus", _no_probe)

    assert svc.run.memory_warning_for_run(cfg) != ""
