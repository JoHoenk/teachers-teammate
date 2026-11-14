"""Unit tests for teachers_teammate.application.service."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import contextlib
import dataclasses
import os
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.application import service as service_module
from teachers_teammate.application.commands import ApplicationCommands
from teachers_teammate.application.service import (
    AnonymizerConfig,
    DocxExport,
    IssueKind,
    OllamaStatus,
    ProcessingApplicationService,
    RequirementIssue,
    RunPreflight,
)
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.ollama_discovery import OllamaState
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.infrastructure.power import KEEP_AWAKE_REASON
from teachers_teammate.infrastructure.state_repository import DocumentState
from teachers_teammate.interfaces import SleepBlocker


# ── Helpers ────────────────────────────────────────────────────────────────


def _discovery_of(*names: str) -> MagicMock:
    """File discovery that finds the files called *names* (none by default)."""
    return MagicMock(collect_input_files=MagicMock(return_value=[Path(n) for n in names]))


def _as_listing(fake):
    """Wrap a fake that returns model names as the checked listing the service asks for."""
    return lambda provider, **kwargs: ModelListing(list(fake(provider, **kwargs)))


def _make_svc(
    *,
    pipeline_factory=None,
    state_repository_factory=None,
    discovery=None,
) -> ProcessingApplicationService:
    mock_pipeline_factory = pipeline_factory or MagicMock(return_value=MagicMock(run=lambda: 0))
    mock_state_factory = state_repository_factory or MagicMock(return_value=MagicMock())
    mock_discovery = discovery or MagicMock()
    return ProcessingApplicationService(
        discovery=mock_discovery,
        pipeline_factory=mock_pipeline_factory,
        state_repository_factory=mock_state_factory,
    )


def _minimal_state(tmp_path: Path, source: Path) -> DocumentState:
    return DocumentState(
        schema_version=2,
        source_path=str(source.resolve()),
        source_hash="abc123",
    )


# ── run_selected / run_preview_only ───────────────────────────────────────


@pytest.mark.unit
def test_run_selected_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_selected() is called
    Then   pipeline_factory is called once and the RunSummary of its run() is forwarded
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path)
    summary = RunSummary(succeeded=("a.png",))
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = summary
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_selected(cfg)

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
    from tests.conftest import make_config  # noqa: PLC0415

    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = _make_svc(pipeline_factory=factory)
    now, after_file = threading.Event(), threading.Event()

    svc.run_selected(make_config(tmp_path), stop_event=now, stop_after_file_event=after_file)

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
    from tests.conftest import make_config  # noqa: PLC0415

    blocker = _RecordingBlocker()
    order: list[str] = []
    pipeline = MagicMock()
    pipeline.run.side_effect = lambda: order.append("run") or RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = ProcessingApplicationService(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run_selected(make_config(tmp_path))

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
    from tests.conftest import make_config  # noqa: PLC0415

    pipeline = MagicMock()
    pipeline.run.return_value = RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = ProcessingApplicationService(pipeline_factory=factory, sleep_blocker=NullSleepBlocker())

    svc.run_selected(make_config(tmp_path))

    state = factory.call_args.kwargs["dependencies"].keep_awake
    assert (state.enabled, state.held, state.available) == (True, False, False)


@pytest.mark.unit
def test_run_selected_reports_a_refusal_when_a_blocker_exists(tmp_path: Path) -> None:
    """
    Given  a sleep blocker that exists but is refused by the system
    When   run_selected() runs
    Then   the state is not held but available: the system refused
    """
    from tests.conftest import make_config  # noqa: PLC0415

    pipeline = MagicMock()
    pipeline.run.return_value = RunSummary()
    factory = MagicMock(return_value=pipeline)
    svc = ProcessingApplicationService(
        pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False)
    )

    svc.run_selected(make_config(tmp_path))

    state = factory.call_args.kwargs["dependencies"].keep_awake
    assert (state.held, state.available) == (False, True)


@pytest.mark.unit
def test_run_selected_releases_the_computer_when_the_pipeline_raises(tmp_path: Path) -> None:
    """
    Given  a pipeline that raises
    When   run_selected() is called
    Then   the exception propagates and the computer is released nevertheless
    """
    from tests.conftest import make_config  # noqa: PLC0415

    blocker = _RecordingBlocker()
    pipeline = MagicMock()
    pipeline.run.side_effect = RuntimeError("boom")
    svc = ProcessingApplicationService(
        pipeline_factory=MagicMock(return_value=pipeline), sleep_blocker=blocker
    )

    with pytest.raises(RuntimeError, match="boom"):
        svc.run_selected(make_config(tmp_path))

    assert blocker.events[-1] == "release"


@pytest.mark.unit
def test_run_selected_does_not_hold_when_keep_awake_is_off(tmp_path: Path) -> None:
    """
    Given  keep_awake turned off in the config
    When   run_selected() is called
    Then   the blocker is not used and the pipeline is told it is off
    """
    from tests.conftest import make_config  # noqa: PLC0415

    blocker = _RecordingBlocker()
    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = ProcessingApplicationService(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run_selected(make_config(tmp_path, keep_awake=False))

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
    from tests.conftest import make_config  # noqa: PLC0415

    seen: list = []
    factory = MagicMock(return_value=MagicMock(run=lambda: RunSummary()))
    svc = ProcessingApplicationService(
        pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False)
    )

    svc.run_selected(
        make_config(tmp_path),
        progress=service_module.ProgressCallbacks(on_keep_awake=seen.append),
    )

    assert len(seen) == 1
    assert (seen[0].enabled, seen[0].held) == (True, False)


@pytest.mark.unit
def test_run_selected_forwards_progress_callbacks_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_selected() is called with a ProgressCallbacks bundle
    Then   the same bundle is passed to the pipeline factory as ``progress``
    """
    from tests.conftest import make_config  # noqa: PLC0415

    factory = MagicMock(return_value=MagicMock(run=lambda: 0))
    svc = _make_svc(pipeline_factory=factory)
    progress = service_module.ProgressCallbacks(on_stage_started=MagicMock())

    svc.run_selected(make_config(tmp_path), progress=progress)

    assert factory.call_args.kwargs["progress"] is progress


@pytest.mark.unit
def test_run_preview_only_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_preview_only() is called
    Then   pipeline_factory is called and run_preview_only() return value is forwarded
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path)
    mock_pipeline = MagicMock()
    mock_pipeline.run_preview_only.return_value = 0
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_preview_only(cfg)

    factory.assert_called_once_with(cfg)
    mock_pipeline.run_preview_only.assert_called_once()
    assert result == 0


# ── preprocess_preview ─────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.use_case("Preview_Preprocessing")
def test_preprocess_preview_uses_injected_component_factory(
    tmp_path: Path, sample_png: Path
) -> None:
    """
    Given  a ProcessingApplicationService constructed with a custom component_factory
    When   preprocess_preview() is called
    Then   the factory's build_preprocessor is used instead of the module default

    Regression guard: preprocess_preview() used to call
    stage_builder.default_build_preprocessor directly, so the preview silently
    diverged from a pipeline configured with a non-default factory.
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415
    from teachers_teammate.infrastructure.stage_builder import (  # noqa: PLC0415
        PipelineComponentFactory,
    )

    class _StubPreprocessor:
        def preprocess(self, image_path):
            return image_path, ["stub_step"]

    build_preprocessor = MagicMock(return_value=_StubPreprocessor())
    factory = PipelineComponentFactory(build_preprocessor=build_preprocessor)
    svc = ProcessingApplicationService(
        pipeline_factory=MagicMock(),
        state_repository_factory=MagicMock(),
        component_factory=factory,
    )
    ocr = OcrConfig(preprocess_method="grayscale")

    _original, _processed, steps = svc.preprocess_preview(sample_png, ocr, tmp_path)

    assert steps == ["stub_step"]
    build_preprocessor.assert_called_once_with(tmp_path, False, ocr)


# ── check_connection — engine checks ──────────────────────────────────────


@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_ollama_engine_returns_true_when_models_present(monkeypatch) -> None:
    """
    Given  the ollama engine and OllamaClient.check_connection reports one model available
    When   check_connection(engine='ollama') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="ollama")
    assert ok is True
    assert "1" in msg


@pytest.mark.unit
def test_check_connection_ollama_engine_returns_false_when_no_models() -> None:
    """
    Given  Ollama is reachable but has no models pulled
    When   check_connection(engine='ollama') is called
    Then   returns (connected=True, model_ok=False, <message mentioning Ollama>)
    """
    svc = _make_svc()
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    with patch.object(
        ollama_utils.OllamaClient,
        "check_connection",
        return_value=(True, False, "✗ Connected to Ollama but no models are pulled"),
    ):
        connected, model_ok, msg = svc.check_connection(engine="ollama")
    assert connected is True
    assert model_ok is False
    assert "Ollama" in msg or "ollama" in msg.lower()


@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_tesseract_returns_true_when_binary_found(monkeypatch) -> None:
    """
    Given  shutil.which returns a non-None path for 'tesseract'
    When   check_connection(engine='tesseract') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    import shutil  # noqa: PLC0415

    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/tesseract")
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="tesseract")
    assert ok is True
    assert "tesseract" in msg.lower()


@pytest.mark.unit
def test_check_connection_tesseract_returns_false_when_binary_missing(monkeypatch) -> None:
    """
    Given  shutil.which returns None for 'tesseract'
    When   check_connection(engine='tesseract') is called
    Then   returns (connected=False, model_ok=False, <error message>)
    """
    import shutil  # noqa: PLC0415

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="tesseract")
    assert ok is False
    assert "tesseract" in msg.lower()


@pytest.mark.unit
def test_check_connection_paddleocr_returns_true_when_installed(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns a non-None spec for 'paddleocr'
    When   check_connection(engine='paddleocr') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="paddleocr")
    assert ok is True
    assert "paddleocr" in msg.lower()


@pytest.mark.unit
def test_check_connection_paddleocr_returns_false_when_not_installed(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns None for 'paddleocr'
    When   check_connection(engine='paddleocr') is called
    Then   returns (connected=False, model_ok=False, <error message>)
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(engine="paddleocr")
    assert ok is False
    assert "paddleocr" in msg.lower()


# ── check_connection — provider checks ────────────────────────────────────


@pytest.mark.unit
def test_check_connection_provider_ollama_success(monkeypatch) -> None:
    """
    Given  provider='ollama' and OllamaClient.check_connection reports success
    When   check_connection(provider='ollama') is called
    Then   returns (connected=True, model_ok=True, <success message>)
    """
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(
        ollama_utils.OllamaClient,
        "check_connection",
        lambda self, model="": (True, True, "✓ Connected — 1 model(s) available"),
    )
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider="ollama")
    assert ok is True


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("google", "GOOGLE_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("mistral_eu", "MISTRAL_API_KEY"),
        ("cohere", "COHERE_API_KEY"),
    ],
)
@pytest.mark.unit
@pytest.mark.use_case("Service_Availability_Check")
def test_check_connection_provider_returns_true_when_env_var_set(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is set to a non-empty value
    When   check_connection(provider=<name>) is called
    Then   returns (connected=True, model_ok=True, <message containing the env var name>)
    """
    monkeypatch.setenv(env_var, "sk-test")
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider=provider)
    assert ok is True
    assert env_var in msg


@pytest.mark.parametrize(
    ("provider", "env_var"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
    ],
)
@pytest.mark.unit
def test_check_connection_provider_returns_false_when_env_var_missing(
    provider: str, env_var: str, monkeypatch
) -> None:
    """
    Given  a known LLM provider and its API key env var is not set
    When   check_connection(provider=<name>) is called
    Then   returns (connected=False, model_ok=False, <message containing the env var name>)
    """
    monkeypatch.delenv(env_var, raising=False)
    svc = _make_svc()
    ok, _, msg = svc.check_connection(provider=provider)
    assert ok is False
    assert env_var in msg


@pytest.mark.unit
def test_check_connection_no_engine_no_provider_returns_false() -> None:
    """
    Given  neither engine nor provider are supplied
    When   check_connection() is called with no arguments
    Then   returns (connected=False, model_ok=False, <message>)
    """
    svc = _make_svc()
    ok, _, msg = svc.check_connection()
    assert ok is False
    assert len(msg) > 0


# ── list_ocr_engines / list_supported_suffixes / default_preprocess_for_engine


@pytest.mark.unit
def test_list_ocr_engines_returns_known_engines() -> None:
    """
    Given  a ProcessingApplicationService
    When   list_ocr_engines() is called
    Then   the returned list includes 'ollama' and 'tesseract'
    """
    svc = _make_svc()
    engines = svc.list_ocr_engines()
    assert "ollama" in engines
    assert "tesseract" in engines


@pytest.mark.unit
def test_list_supported_suffixes_returns_frozenset_with_images() -> None:
    """
    Given  a ProcessingApplicationService
    When   list_supported_suffixes() is called
    Then   the returned frozenset includes .png, .pdf, and .txt
    """
    svc = _make_svc()
    suffixes = svc.list_supported_suffixes()
    assert ".png" in suffixes
    assert ".pdf" in suffixes
    assert ".txt" in suffixes


@pytest.mark.unit
def test_default_preprocess_for_engine_returns_string() -> None:
    """
    Given  a known OCR engine name
    When   default_preprocess_for_engine() is called
    Then   a non-empty string is returned
    """
    svc = _make_svc()
    result = svc.default_preprocess_for_engine("tesseract")
    assert isinstance(result, str)
    assert len(result) > 0


# ── _invalidate_from_stage (static helper) ────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_matches_by_resolved_source_id(tmp_path: Path) -> None:
    """
    Given  a list of files and selected_source_ids matching one file by resolved path
    When   _invalidate_from_stage() is called
    Then   the matched file is invalidated in the store and its source_id is returned
    """
    source = tmp_path / "doc.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.return_value = MagicMock()

    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=[str(source.resolve())],
        stage="ocr",
        files=[source],
        store=mock_store,
    )

    mock_store.invalidate_from_stage.assert_called_once_with(source, "ocr")
    assert str(source.resolve()) in run_ids
    assert failures == []
    assert nothing_matched is False


@pytest.mark.unit
def test_invalidate_from_stage_static_returns_nothing_matched_when_no_files_provided() -> None:
    """
    Given  an empty files list
    When   _invalidate_from_stage() is called
    Then   empty lists are returned and nothing_matched is True
    """
    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=["any"],
        stage="correction",
        files=[],
        store=MagicMock(),
    )
    assert run_ids == []
    assert failures == []
    assert nothing_matched is True


@pytest.mark.unit
def test_invalidate_from_stage_static_accumulates_oserror_failures(tmp_path: Path) -> None:
    """
    Given  a store whose invalidate_from_stage raises OSError
    When   _invalidate_from_stage() is called
    Then   the failure is recorded in the failures list and run_ids still contains the source
    """
    source = tmp_path / "bad.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.side_effect = OSError("disk full")

    run_ids, failures, nothing_matched = ApplicationCommands._invalidate_from_stage(
        selected_source_ids=[str(source.resolve())],
        stage="ocr",
        files=[source],
        store=mock_store,
    )
    assert "bad.png" in failures[0]
    assert str(source.resolve()) in run_ids


# ── record_manual_ocr_edit ────────────────────────────────────────────────


@pytest.mark.unit
def test_record_manual_ocr_edit_delegates_to_store(tmp_path: Path) -> None:
    """
    Given  a valid source file and a store that returns a state
    When   record_manual_ocr_edit() is called
    Then   the store receives the edited text and the updated state is returned
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "page.png"
    source.write_text("x")
    state = _minimal_state(tmp_path, source)
    updated = MagicMock()
    mock_store = MagicMock()
    mock_store.load_or_create.return_value = state
    mock_store.record_manual_ocr_edit.return_value = updated

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        return_value="abc123",
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: mock_store,
        )
        result = svc.record_manual_ocr_edit(
            make_config(tmp_path),
            source=source,
            edited_text="hello world",
            preview_img="/tmp/preview.png",
        )

    mock_store.record_manual_ocr_edit.assert_called_once()
    assert result is updated


@pytest.mark.unit
def test_record_manual_ocr_edit_returns_none_on_oserror(tmp_path: Path) -> None:
    """
    Given  compute_file_hash raises OSError (file unreadable)
    When   record_manual_ocr_edit() is called
    Then   None is returned without raising
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "missing.png"

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        side_effect=OSError("no such file"),
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: MagicMock(),
        )
        result = svc.record_manual_ocr_edit(
            make_config(tmp_path),
            source=source,
            edited_text="text",
            preview_img="",
        )

    assert result is None


# ── record_manual_correction_edit ─────────────────────────────────────────


@pytest.mark.unit
def test_record_manual_correction_edit_persists_text(tmp_path: Path) -> None:
    """
    Given  a valid source file and a store holding an existing OCR-done state
    When   record_manual_correction_edit() is called
    Then   the store records the correction and the updated state is returned
    """
    from tests.conftest import make_config  # noqa: PLC0415

    source = tmp_path / "page.png"
    source.write_text("x")
    state = dataclasses.replace(_minimal_state(tmp_path, source), ocr_done=True, raw_text="raw")
    updated = MagicMock()
    mock_store = MagicMock()
    mock_store.load_or_create.return_value = state
    mock_store.record_manual_correction_edit.return_value = updated

    with patch(
        "teachers_teammate.application.commands.compute_file_hash",
        return_value="abc123",
    ):
        svc = ProcessingApplicationService(
            state_repository_factory=lambda _: mock_store,
        )
        result = svc.record_manual_correction_edit(
            make_config(tmp_path),
            source=source,
            raw_text="raw",
            preview_img="",
            edited_text="corrected",
        )

    mock_store.record_manual_correction_edit.assert_called_once()
    assert result is updated


@pytest.mark.unit
def test_manual_ocr_edit_survives_reconcile(tmp_path: Path) -> None:
    """
    Given  a manual OCR edit recorded for a source file
    When   the cache is reconciled against the same (unchanged) runtime config
    Then   the edited text is preserved rather than flagged stale and wiped
    """
    from tests.conftest import make_config  # noqa: PLC0415

    from teachers_teammate.infrastructure.file_discovery import (  # noqa: PLC0415
        FileDiscovery,
    )
    from teachers_teammate.infrastructure.preview_image_store import (  # noqa: PLC0415
        PreviewImageStore,
    )
    from teachers_teammate.infrastructure.state_repository import (  # noqa: PLC0415
        StateRepository,
    )
    from teachers_teammate.infrastructure.workflow.cache_service import (  # noqa: PLC0415
        CacheReconciliationService,
    )

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path, correction_enabled=False)

    repo = StateRepository(tmp_path / "state")
    commands = ApplicationCommands(discovery=FileDiscovery(), state_store_factory=lambda _c: repo)
    commands.record_manual_ocr_edit(cfg, source=source, edited_text="MANUAL", preview_img="")

    recon = CacheReconciliationService(
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
    )
    ctx = recon.prepare(source)

    assert ctx.state.ocr_done is True
    assert ctx.state.raw_text == "MANUAL"


# ── _invalidate_from_stage ValueError ────────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_invalid_stage_propagates_value_error(tmp_path: Path) -> None:
    """
    Given  a non-empty files list and an invalid stage name
    When   _invalidate_from_stage() is called
    Then   ValueError is raised (normalize_stage rejects the unknown stage name)
    """
    source = tmp_path / "doc.png"
    source.touch()

    with pytest.raises(ValueError, match="Invalid stage"):
        ApplicationCommands._invalidate_from_stage(
            selected_source_ids=[str(source.resolve())],
            stage="nonexistent",
            files=[source],
            store=MagicMock(),
        )


# ── clear_anonymizer_cache ────────────────────────────────────────────────────


@pytest.mark.unit
def test_clear_anonymizer_cache_empties_dict(tmp_path: Path) -> None:
    """
    Given  a service with cached anonymizer entries
    When   clear_anonymizer_cache() is called
    Then   the internal cache is empty
    """
    svc = _make_svc()
    svc._anonymizer_cache[("English", object())] = MagicMock()
    assert len(svc._anonymizer_cache) == 1

    svc.clear_anonymizer_cache()

    assert len(svc._anonymizer_cache) == 0


# ── New service passthroughs ──────────────────────────────────────────────────


@pytest.mark.unit
def test_detect_gpus_delegates_to_infrastructure(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService
    When   detect_gpus() is called
    Then   it delegates to gpu_detector.detect_gpus and returns the result
    """
    from teachers_teammate.infrastructure.gpu_detector import GpuInfo  # noqa: PLC0415

    fake_gpus = [GpuInfo("nvidia", "Test GPU")]
    svc = _make_svc()

    with patch("teachers_teammate.infrastructure.gpu_detector.detect_gpus", return_value=fake_gpus):
        result = svc.detect_gpus()

    assert result == fake_gpus


@pytest.mark.unit
def test_resolve_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  a config file exists at a known path
    When   resolve_config_path() is called with that explicit path as a string
    Then   it returns the resolved Path without error
    """
    cfg = tmp_path / "ocr.toml"
    cfg.write_text("[settings]\n", encoding="utf-8")
    svc = _make_svc()

    result = svc.resolve_config_path(str(cfg))

    assert result == cfg


@pytest.mark.unit
def test_default_config_path_delegates(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService and a patched storage root
    When   default_config_path() is called
    Then   it delegates to storage_root.default_config_path and returns a Path
    """
    expected = tmp_path / "ocr.toml"
    svc = _make_svc()

    with patch(
        "teachers_teammate.application.service._infra_default_config_path",
        return_value=expected,
    ):
        result = svc.default_config_path()

    assert result == expected


@pytest.mark.unit
def test_addon_packages_dir_delegates(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService
    When   addon_packages_dir() is called
    Then   it delegates to addon_manager.get_packages_dir and returns a Path
    """
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.addon_manager.get_packages_dir",
        return_value=tmp_path,
    ):
        result = svc.addon_packages_dir()

    assert result == tmp_path


@pytest.mark.unit
def test_get_installation_status_reports_tesseract_and_paddleocr() -> None:
    """
    Given  a ProcessingApplicationService and patched shutil.which + importlib.util.find_spec
    When   get_installation_status() is called
    Then   'tesseract' is True when which returns a path and 'paddleocr' is False when spec is None
    """
    svc = _make_svc()

    with (
        patch(
            "teachers_teammate.application.service.shutil.which", return_value="/usr/bin/tesseract"
        ),
        patch("teachers_teammate.application.service.importlib.util.find_spec", return_value=None),
    ):
        status = svc.get_installation_status()

    assert status["tesseract"] is True
    assert status["paddleocr"] is False


@pytest.mark.unit
def test_is_module_importable_returns_true_when_spec_found(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns a non-None spec for a module name
    When   is_module_importable() is called with that module name
    Then   True is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: object())
    svc = _make_svc()
    assert svc.is_module_importable("some_package") is True


@pytest.mark.unit
def test_is_module_importable_returns_false_when_spec_missing(monkeypatch) -> None:
    """
    Given  importlib.util.find_spec returns None (module not installed)
    When   is_module_importable() is called
    Then   False is returned
    """
    import importlib.util  # noqa: PLC0415

    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)
    svc = _make_svc()
    assert svc.is_module_importable("missing_package") is False


@pytest.mark.unit
def test_get_installation_status_includes_langchain_packages(monkeypatch) -> None:
    """
    Given  get_installation_status() is called with a langchain_packages dict
    When   importlib.util.find_spec returns a spec for one module and None for another
    Then   the result contains 'langchain:{key}' entries matching the spec outcomes
    """
    import importlib.util  # noqa: PLC0415

    present_modules = {"langchain_core"}
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name: object() if name in present_modules else None,
    )
    svc = _make_svc()
    status = svc.get_installation_status(
        langchain_packages={"core": "langchain_core", "openai": "langchain_openai"},
    )
    assert status["langchain:core"] is True
    assert status["langchain:openai"] is False


@pytest.mark.unit
def test_list_provider_models_uses_ttl_cache() -> None:
    """
    Given  a ProcessingApplicationService whose list_provider_models is called once
    When   list_provider_models() is called a second time within the TTL window
    Then   the underlying infrastructure function is called only once (cache hit)
    """
    svc = _make_svc()
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a", "model-b"]

    with patch(
        "teachers_teammate.application.service.list_provider_models_checked",
        side_effect=_as_listing(_fake_list),
    ):
        first = svc.list_provider_models("openai")
        second = svc.list_provider_models("openai")

    assert first == ["model-a", "model-b"]
    assert second == ["model-a", "model-b"]
    assert call_count == 1


@pytest.mark.unit
def test_invalidate_model_cache_forces_refetch() -> None:
    """
    Given  a ProcessingApplicationService with a cached model list for 'openai'
    When   invalidate_model_cache('openai') is called and then list_provider_models again
    Then   the underlying infrastructure function is called a second time
    """
    svc = _make_svc()
    call_count = 0

    def _fake_list(provider, **_kwargs):
        nonlocal call_count
        call_count += 1
        return ["model-a"]

    with patch(
        "teachers_teammate.application.service.list_provider_models_checked",
        side_effect=_as_listing(_fake_list),
    ):
        svc.list_provider_models("openai")
        svc.invalidate_model_cache("openai")
        svc.list_provider_models("openai")

    assert call_count == 2


# ── hardware profiles & model recommendations ───────────────────────────────


@pytest.mark.use_case("Hardware_Profile_Override")
def test_resolve_hardware_profile_manual_setting_skips_detection(monkeypatch) -> None:
    """
    Given  a concrete hardware_profile setting ("16gb")
    When   resolve_hardware_profile is called
    Then   a MANUAL detection is returned and no detection runs
    """
    svc = _make_svc()
    calls = []
    monkeypatch.setattr(
        service_module.ProcessingApplicationService,
        "detect_hardware",
        lambda self, url, **kw: calls.append(url),  # pylint: disable=unnecessary-lambda
    )
    detection = svc.resolve_hardware_profile("16gb", "http://127.0.0.1:11434")
    assert detection.profile == "16gb"
    from teachers_teammate.domain.model_catalog import DetectionReason  # noqa: PLC0415

    assert detection.reason is DetectionReason.MANUAL
    assert calls == []


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_hardware_probes_this_computer_once(monkeypatch) -> None:
    """
    Given  a service with patched GPU detection
    When   detect_hardware is called repeatedly, also with differently written URLs
    Then   the detector runs once (process-lifetime probe); refresh re-runs it
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import DetectionReason, HardwareDetection  # noqa: PLC0415

    calls = []

    def fake_detect_gpus():
        calls.append("gpu")
        return [gpu_detector.GpuInfo("nvidia", "RTX 4060", 8188)]

    monkeypatch.setattr(gpu_detector, "detect_gpus", fake_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)

    svc = _make_svc()
    first = svc.detect_hardware("http://127.0.0.1:11434")
    second = svc.detect_hardware("http://127.0.0.1:11434")
    # Editing the URL (trailing slash, host spelling) must not probe again.
    third = svc.detect_hardware("http://localhost:11434/")
    assert first.profile == "8gb"
    assert first.reason is DetectionReason.GPU_DETECTED
    assert second == first
    assert third == first
    assert calls == ["gpu"]

    refreshed = svc.detect_hardware("http://127.0.0.1:11434", refresh=True)
    assert isinstance(refreshed, HardwareDetection)
    assert calls == ["gpu", "gpu"]


@pytest.mark.use_case("Hardware_Profile_Override")
def test_detect_hardware_remote_url_skips_local_gpus(monkeypatch) -> None:
    """
    Given  an Ollama URL pointing at another computer
    When   detect_hardware is called
    Then   the REMOTE profile is returned without consulting local GPUs
    """
    from teachers_teammate.domain.model_catalog import DetectionReason  # noqa: PLC0415

    def exploding_detect_gpus():
        raise AssertionError("local GPU detection must not run for remote URLs")

    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_gpus", exploding_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", exploding_detect_gpus)

    svc = _make_svc()
    detection = svc.detect_hardware("http://192.168.1.50:11434")
    assert detection.profile == "8gb"
    assert detection.reason is DetectionReason.REMOTE_SERVER
    assert detection.host == "192.168.1.50"
    assert detection.gpu_name is None


@pytest.mark.use_case("Hardware_Profile_Override")
def test_resolve_hardware_profile_unknown_setting_treated_as_auto(monkeypatch) -> None:
    """
    Given  an invalid hardware_profile setting value
    When   resolve_hardware_profile is called
    Then   the value is treated as "auto" and detection runs
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import DetectionReason  # noqa: PLC0415

    monkeypatch.setattr(
        gpu_detector, "detect_gpus", lambda: [gpu_detector.GpuInfo("nvidia", "RTX 4060", 8188)]
    )
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)

    svc = _make_svc()
    detection = svc.resolve_hardware_profile("bogus", "http://127.0.0.1:11434")
    assert detection.profile == "8gb"
    assert detection.reason is DetectionReason.GPU_DETECTED


@pytest.mark.use_case("Model_Category_Guidance")
def test_classify_ollama_models_uses_catalog_and_caches_capabilities(monkeypatch) -> None:
    """
    Given  a mix of catalogued and custom model tags
    When   classify_ollama_models is called twice
    Then   catalogued tags resolve from the catalog, unknown tags via
           model_capabilities, and the result is cached (no second server call)
    """
    svc = _make_svc()
    caps_calls = []

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            caps_calls.append(tag)
            return frozenset({"vision"}) if "vl" in tag else frozenset({"completion"})

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)

    tags = ["qwen3-vl:8b", "my-custom-vl:latest", "my-custom-text:latest"]
    result = svc.classify_ollama_models(tags, "http://127.0.0.1:11434")
    assert result["qwen3-vl:8b"] == frozenset({"ocr"})
    # A model that can read images may be used for reading AND writing.
    assert result["my-custom-vl:latest"] == frozenset({"ocr", "general"})
    assert result["my-custom-text:latest"] == frozenset({"general"})
    assert sorted(caps_calls) == ["my-custom-text:latest", "my-custom-vl:latest"]

    # Second call is fully cached — the server is not asked again.
    svc.classify_ollama_models(tags, "http://127.0.0.1:11434")
    assert len(caps_calls) == 2

    # invalidate_model_cache clears the classification cache.
    svc.invalidate_model_cache("ollama", "http://127.0.0.1:11434")
    svc.classify_ollama_models(tags, "http://127.0.0.1:11434")
    assert len(caps_calls) == 4

    # ... also when the URL is written differently (trailing slash).
    svc.on_ollama_models_changed("http://127.0.0.1:11434/")
    svc.classify_ollama_models(tags, "http://127.0.0.1:11434")
    assert len(caps_calls) == 6


@pytest.mark.use_case("Recommended_Models_On_First_Start")
@pytest.mark.parametrize(
    "profile",
    ["cpu", "8gb", "16gb"],
)
def test_first_run_model_settings_return_best_models(profile: str) -> None:
    """
    Given  a hardware profile
    When   first_run_model_settings is called
    Then   ocr_model is the BEST reading model and correction/evaluate are the
           BEST writing model for that profile
    """
    from teachers_teammate.domain.model_catalog import best_model  # noqa: PLC0415

    from typing import cast  # noqa: PLC0415

    from teachers_teammate.domain.model_catalog import HardwareProfile  # noqa: PLC0415

    svc = _make_svc()
    settings = svc.first_run_model_settings(cast(HardwareProfile, profile))
    assert settings["ocr_model"] == best_model("ocr", cast(HardwareProfile, profile))
    assert settings["correction_model"] == best_model("general", cast(HardwareProfile, profile))
    assert settings["evaluate_model"] == best_model("general", cast(HardwareProfile, profile))


@pytest.mark.use_case("Model_Category_Guidance")
def test_unknown_classification_is_not_cached(monkeypatch) -> None:
    """
    Given  a custom model whose capabilities could not be read (None)
    When   it is classified twice
    Then   the server is asked both times — "unknown" is never cached, so a
           model that was not pulled yet is recognised as soon as it is
    """
    svc = _make_svc()
    answers = iter([None, frozenset({"vision"})])
    asked: list[str] = []

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            asked.append(tag)
            return next(answers)

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)
    url = "http://127.0.0.1:11434"
    assert svc.classify_ollama_models(["my-model:latest"], url) == {"my-model:latest": None}
    assert svc.classify_ollama_models(["my-model:latest"], url) == {
        "my-model:latest": frozenset({"ocr", "general"})
    }
    assert asked == ["my-model:latest", "my-model:latest"]


@pytest.mark.use_case("Model_Category_Guidance")
def test_unreachable_server_is_asked_once_not_per_model(monkeypatch) -> None:
    """
    Given  several custom models and a server that cannot be reached
    When   they are classified
    Then   the server is contacted only for the first model (one timeout, not one per
           model), every model maps to None, and a later call asks again
    """
    from teachers_teammate.exceptions import OllamaConnectionError  # noqa: PLC0415
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    asked: list[str] = []
    timeouts: list[float] = []

    class _DownClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url
            timeouts.append(timeout)

        def model_capabilities(self, tag: str):
            asked.append(tag)
            raise OllamaConnectionError("down")

    monkeypatch.setattr(ollama_utils, "OllamaClient", _DownClient)
    svc = _make_svc()
    tags = ["a:latest", "b:latest", "c:latest"]
    assert svc.classify_ollama_models(tags, "http://127.0.0.1:11434") == dict.fromkeys(tags)
    assert asked == ["a:latest"]
    assert timeouts and all(0 < t <= 5 for t in timeouts)  # a short, bounded wait

    svc.classify_ollama_models(tags, "http://127.0.0.1:11434")
    assert asked == ["a:latest", "a:latest"]  # nothing was cached


@pytest.mark.use_case("Model_Category_Guidance")
def test_cached_only_classification_never_contacts_the_server(monkeypatch) -> None:
    """
    Given  a custom model that is not classified yet
    When   classify_ollama_models is called with cached_only=True
    Then   the server is not contacted and the model maps to None
    """
    svc = _make_svc()

    def exploding_client(url, **_kw):
        raise AssertionError("cached_only must not contact the server")

    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    monkeypatch.setattr(ollama_utils, "OllamaClient", exploding_client)
    result = svc.classify_ollama_models(
        ["qwen3-vl:8b", "custom:latest"], "http://x:1", cached_only=True
    )
    assert result == {"qwen3-vl:8b": frozenset({"ocr"}), "custom:latest": None}


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_rate_ollama_models_rates_catalog_and_custom_models(monkeypatch) -> None:
    """
    Given  a catalogued model and a custom vision model on the server
    When   rate_ollama_models is called for the reading task on 8 GB
    Then   the catalog model is BEST with its memory/note and the custom one is
           a WORKS model for reading (the single rating path of the widgets)
    """
    from teachers_teammate.domain.model_catalog import Suitability  # noqa: PLC0415
    from teachers_teammate.infrastructure import ollama_utils  # noqa: PLC0415

    class _FakeClient:
        def __init__(self, url: str, *, timeout: float = 0.0) -> None:
            _ = url, timeout

        def model_capabilities(self, tag: str):
            _ = tag
            return frozenset({"vision"})

    monkeypatch.setattr(ollama_utils, "OllamaClient", _FakeClient)
    svc = _make_svc()
    ratings = svc.rate_ollama_models(
        ["qwen3-vl:8b", "my-vision:latest"], "ocr", "8gb", "http://127.0.0.1:11434"
    )
    best, custom = ratings
    assert best.suitability is Suitability.GOOD  # BEST on 8 GB is the default reading model
    assert best.memory_gb == 7.0
    assert best.note
    assert custom.suitability is Suitability.WORKS
    assert custom.model_uses == frozenset({"ocr", "general"})
    assert custom.memory_gb is None


@pytest.mark.use_case("Model_Category_Guidance")
def test_ollama_model_cache_key_ignores_url_spelling(monkeypatch) -> None:
    """
    Given  the model list cached under "http://host:11434/"
    When   it is invalidated through "http://host:11434"
    Then   the entry is gone (both spellings share one cache entry)
    """
    calls: list[str] = []

    def fake_list(provider: str, **_kw) -> list[str]:
        calls.append(provider)
        return ["a:latest"]

    monkeypatch.setattr(service_module, "list_provider_models_checked", _as_listing(fake_list))
    svc = _make_svc()
    svc.list_provider_models("ollama", base_url="http://host:11434/")
    svc.list_provider_models("ollama", base_url="http://host:11434")
    assert len(calls) == 1  # second call was a cache hit
    svc.invalidate_model_cache("ollama", "http://host:11434")
    svc.list_provider_models("ollama", base_url="http://host:11434/")
    assert len(calls) == 2


@pytest.mark.use_case("Model_Category_Guidance")
def test_on_ollama_models_changed_evicts_list_and_classification(monkeypatch) -> None:
    """
    Given  a cached model list and cached classifications
    When   on_ollama_models_changed is called (a pull finished)
    Then   the next lookup asks the server again
    """
    installed = ["a:latest"]
    monkeypatch.setattr(
        service_module,
        "list_provider_models_checked",
        _as_listing(lambda p, **kw: list(installed)),
    )
    svc = _make_svc()
    url = "http://127.0.0.1:11434"
    assert svc.list_provider_models("ollama", base_url=url) == ["a:latest"]
    installed.append("b:latest")  # a pull finished
    assert svc.list_provider_models("ollama", base_url=url) == ["a:latest"]  # stale cache
    svc.on_ollama_models_changed(url)
    assert svc.list_provider_models("ollama", base_url=url) == ["a:latest", "b:latest"]


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_installation_status_compares_normalised_tags(monkeypatch) -> None:
    """
    Given  an installed model "llama3.2:latest"
    When   the installation status asks for "llama3.2" (no suffix)
    Then   it counts as installed
    """
    monkeypatch.setattr(
        service_module,
        "list_provider_models_checked",
        _as_listing(lambda p, **kw: ["llama3.2:latest"]),
    )
    svc = _make_svc()
    status = svc.get_installation_status(
        ollama_models=["llama3.2", "other:1b"], ollama_url="http://127.0.0.1:11434"
    )
    assert status["ollama:llama3.2"] is True
    assert status["ollama:other:1b"] is False


@pytest.mark.use_case("Model_Recommendation_By_Hardware")
def test_detect_gpus_is_served_from_the_shared_probe(monkeypatch) -> None:
    """
    Given  hardware detection that already probed this computer
    When   detect_gpus() is called afterwards (the "Detect GPU" button)
    Then   the machine is not probed a second time
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    calls: list[str] = []

    def fake_detect_gpus():
        calls.append("gpu")
        return [gpu_detector.GpuInfo("nvidia", "RTX 4060", 8188)]

    monkeypatch.setattr(gpu_detector, "detect_gpus", fake_detect_gpus)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    svc = _make_svc()
    svc.detect_hardware("http://127.0.0.1:11434")
    gpus = svc.detect_gpus()
    assert [g.name for g in gpus] == ["RTX 4060"]
    assert calls == ["gpu"]


@pytest.mark.unit
def test_export_docx_keeps_markdown_formatting(tmp_path: Path) -> None:
    """
    Given  cached Markdown OCR text with a crossed-out word
    When   export_docx() is called with text_format="markdown"
    Then   the DOCX run for that word is struck through and no markers are written
    """
    from docx import Document  # noqa: PLC0415

    svc = _make_svc()
    out = tmp_path / "out" / "doc.docx"

    svc.export_docx(
        tmp_path / "doc.png",
        "my ~~backpack~~ bag",
        "",
        "",
        out,
        options=DocxExport(text_format="markdown"),
    )

    cells = Document(str(out)).tables[0].rows[1].cells
    runs = [run for para in cells[1].paragraphs for run in para.runs]
    assert any(run.text == "backpack" and run.font.strike for run in runs)
    assert "~~" not in cells[1].text


@pytest.mark.unit
def test_export_docx_defaults_to_plain_text(tmp_path: Path) -> None:
    """
    Given  cached plain text that contains Markdown-looking characters
    When   export_docx() is called without a text_format
    Then   the characters are written verbatim
    """
    from docx import Document  # noqa: PLC0415

    svc = _make_svc()
    out = tmp_path / "doc.docx"

    svc.export_docx(tmp_path / "doc.png", "~~keep~~", "", "", out)

    assert "~~keep~~" in Document(str(out)).tables[0].rows[1].cells[1].text


@pytest.mark.unit
def test_manual_correction_edit_stores_the_notation_of_a_synthesised_ocr_text(
    tmp_path: Path,
) -> None:
    """
    Given  no state yet and a Markdown OCR text shown in the GUI
    When   the user edits the correction (record_manual_correction_edit)
    Then   the synthesised state stores the OCR text as Markdown, not plain
    """
    from tests.conftest import make_config  # noqa: PLC0415

    from teachers_teammate.infrastructure.file_discovery import (  # noqa: PLC0415
        FileDiscovery,
    )
    from teachers_teammate.infrastructure.state_repository import (  # noqa: PLC0415
        StateRepository,
    )

    source = tmp_path / "note.txt"
    source.write_text("hello")
    repo = StateRepository(tmp_path / "state")
    commands = ApplicationCommands(discovery=FileDiscovery(), state_store_factory=lambda _c: repo)

    state = commands.record_manual_correction_edit(
        make_config(tmp_path),
        source=source,
        raw_text="~~a~~ b",
        preview_img="",
        edited_text="~~a~~ b!",
        raw_text_format="markdown",
    )

    assert state is not None
    assert (state.raw_text, state.raw_text_format) == ("~~a~~ b", "markdown")
    assert state.correction_text == "~~a~~ b!"


# ── Run preflight & model-list caching ──────────────────────────────────────

_INSTALL = "teachers_teammate.infrastructure.ollama_install"


def _ollama_config(tmp_path: Path, **overrides):
    """Config whose OCR and proofreading both use Ollama."""
    from tests.conftest import make_config  # noqa: PLC0415

    values: dict = {
        "ocr_engine": "ollama",
        "ocr_model": "qwen3-vl:8b",
        "correction_enabled": True,
        "correction_provider": "ollama",
        "correction_model": "qwen3.5:9b",
    }
    values.update(overrides)
    return make_config(tmp_path, **values)


def _server_answers(models: list[str], error: str = ""):
    """Patch the single Ollama request that lists models with ``(models, error)``."""
    return patch(
        "teachers_teammate.infrastructure.ollama_utils.OllamaClient.list_models_or_error",
        return_value=(models, error),
    )


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


def _computer(svc, monkeypatch, *, profile, free_gb, card_gb=None, reason=None) -> None:
    """Make *svc* believe it runs on a computer of *profile* with *free_gb* of free memory."""
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DetectionReason,
        HardwareDetection,
    )
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_available_memory_gb", lambda: free_gb)
    detection = HardwareDetection(
        profile=profile,
        reason=reason or DetectionReason.GPU_DETECTED,
        memory_gb=card_gb,
        system_memory_gb=64.0,
    )
    monkeypatch.setattr(svc, "resolve_hardware_profile", lambda _setting, _url: detection)


@pytest.mark.unit
def test_expected_cache_keys_never_probe_this_computer(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a service that has not probed this computer, and a local Ollama with "auto" hardware
    When   the GUI asks for the context windows and the reconciled views
    Then   no graphics tool is run (that would freeze the window), and the base windows are used
    """
    from teachers_teammate.domain.context_budget import BASE_NUM_CTX  # noqa: PLC0415
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    probes: list[str] = []
    monkeypatch.setattr(gpu_detector, "detect_gpus", lambda: probes.append("gpu") or [])
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="tesseract", hardware_profile="auto")

    windows = svc.resolved_windows(cfg)
    svc.load_views_reconciled(cfg, [])

    assert probes == []
    assert (windows.proofreading, windows.evaluation) == (BASE_NUM_CTX, BASE_NUM_CTX)
    assert svc.cached_hardware_profile("auto", cfg.ollama_url) is None


@pytest.mark.unit
def test_a_run_is_refused_when_the_model_would_have_to_swap(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a computer without a graphics card and 6 GB of free memory, and qwen3.5:9b (7.2 GB)
    When   the run is checked
    Then   the problem names the model and the memory, so the run does not start and swap
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="tesseract")
    _computer(svc, monkeypatch, profile="cpu", free_gb=6.0)

    problem = svc.memory_warning_for_run(cfg)

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
    cfg = _ollama_config(tmp_path, ocr_engine="tesseract")
    _computer(svc, monkeypatch, profile="cpu", free_gb=12.0)

    assert svc.memory_warning_for_run(cfg) == ""


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
    cfg = _ollama_config(tmp_path, ocr_engine="tesseract", correction_model="gemma4:12b")

    _computer(svc, monkeypatch, profile="8gb", free_gb=4.0, card_gb=8.0 * 0.85)
    assert "gemma4:12b" in svc.memory_warning_for_run(cfg)
    _computer(svc, monkeypatch, profile="8gb", free_gb=6.0, card_gb=8.0 * 0.85)
    assert svc.memory_warning_for_run(cfg) == ""


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
    cfg = _ollama_config(tmp_path, ocr_engine="tesseract")
    _computer(
        svc,
        monkeypatch,
        profile="8gb",
        free_gb=6.0,
        card_gb=16.0,
        reason=DetectionReason.APPLE_UNIFIED_MEMORY,
    )

    assert "qwen3.5:9b" in svc.memory_warning_for_run(cfg)


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

    unknown = _ollama_config(tmp_path, ocr_engine="tesseract", correction_model="my-own:latest")
    remote = _ollama_config(tmp_path, ocr_engine="tesseract", ollama_url="http://gpu-box.lan:11434")
    no_ollama = _ollama_config(tmp_path, ocr_engine="tesseract", correction_enabled=False)

    assert svc.memory_warning_for_run(unknown) == ""
    assert svc.memory_warning_for_run(remote) == ""
    assert svc.memory_warning_for_run(no_ollama) == ""


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
        result = svc.check_run_preflight(cfg)

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
    monkeypatch.setattr(svc, "missing_provider_package", lambda _provider: True)

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        result = svc.check_run_preflight(cfg)

    assert result.blocked is True
    assert result.stage == "correction"
    assert result.memory_warning == ""


@pytest.mark.unit
def test_empty_model_list_is_not_cached(tmp_path: Path) -> None:
    """
    Given  an Ollama server that is unreachable (empty model list) at first
    When   it is started and the list is requested again
    Then   the second call asks the server again instead of serving the cached failure
    """
    svc = _make_svc()
    answers = [[], ["qwen3-vl:8b"]]

    with patch(
        "teachers_teammate.application.service.list_provider_models_checked",
        side_effect=_as_listing(lambda *_a, **_k: answers.pop(0)),
    ):
        first = svc.list_provider_models("ollama", base_url="http://127.0.0.1:11434")
        second = svc.list_provider_models("ollama", base_url="http://127.0.0.1:11434")

    assert first == []
    assert second == ["qwen3-vl:8b"]


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
        result = svc.check_run_preflight(cfg)

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
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path, ocr_engine="langchain")

    result = _make_svc(discovery=_discovery_of("page.png")).check_run_preflight(cfg)

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

    result = svc.check_run_preflight(cfg)

    assert "Ollama's servers" in result.ocr_not_allowed


@pytest.mark.unit
def test_ocr_refusal_for_run_is_empty_for_a_local_model(tmp_path: Path) -> None:
    """
    Given  OCR settings with a model downloaded to Ollama
    When   ocr_refusal_for_run is asked
    Then   nothing is refused
    """
    cfg = _ollama_config(tmp_path, ocr_model="qwen3-vl:8b")

    assert _make_svc().ocr_refusal_for_run(cfg) == ""


@pytest.mark.unit
def test_list_ocr_models_leaves_out_ollama_cloud_models() -> None:
    """
    Given  an Ollama server with a downloaded model and a cloud model
    When   the models usable for text recognition are listed
    Then   only the downloaded model is offered, since the cloud model would receive the pages
    """
    svc = _make_svc()

    with patch.object(
        ProcessingApplicationService,
        "list_provider_models",
        return_value=["qwen3-vl:8b", "qwen3-vl:235b-cloud"],
    ):
        assert svc.list_ocr_models("http://127.0.0.1:11434") == ["qwen3-vl:8b"]


@pytest.mark.unit
def test_describe_ocr_refusal_names_the_reason_for_cloud_models_only() -> None:
    """
    Given  a cloud model and a downloaded model on the local Ollama server
    When   the refusal for each is asked for
    Then   only the cloud model has a reason, which says it runs on Ollama's servers
    """
    svc = _make_svc()
    url = "http://127.0.0.1:11434"

    assert "Ollama's servers" in svc.describe_ocr_refusal("ollama", "qwen3-vl:235b-cloud", url)
    assert svc.describe_ocr_refusal("ollama", "qwen3-vl:8b", url) == ""
    assert svc.describe_ocr_refusal("tesseract", "", url) == ""


@pytest.mark.unit
def test_describe_data_flow_uses_provider_metadata(tmp_path: Path) -> None:
    """
    Given  local text recognition and OpenAI proofreading
    When   the service describes the data flow
    Then   proofreading is an online service named by its provider's display name
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        correction_provider="openai",
    )

    flow = _make_svc().describe_data_flow(cfg)

    assert [s.stage for s in flow.outbound] == ["correction"]
    assert flow.outbound[0].location.service == "OpenAI (ChatGPT)"


@pytest.mark.unit
def test_describe_data_flow_reports_the_provider_region(tmp_path: Path) -> None:
    """
    Given  proofreading at an EU provider and content review at a provider outside the EU
    When   the service describes the data flow
    Then   each step carries the region the provider declares, and the worst step decides
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=True,
        correction_provider="mistral_eu",
        evaluation_enabled=True,
        evaluate_provider="openai",
    )

    flow = _make_svc().describe_data_flow(cfg)

    assert flow.stage("correction").location.data_region.value == "eu"
    assert flow.stage("evaluation").location.data_region.value == "non_eu"
    assert flow.level.value == "names_non_eu"


@pytest.mark.unit
def test_describe_steps_lists_every_step_although_none_is_ticked(tmp_path: Path) -> None:
    """
    Given  a config with proofreading and review both off
    When   the service describes the steps
    Then   proofreading and the review are listed with the place they would run at
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        correction_enabled=False,
        evaluation_enabled=False,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    assert svc.describe_data_flow(cfg).stage("correction") is None
    steps = svc.describe_steps(cfg)

    assert steps.stage("correction").location.data_region.value == "eu"
    assert steps.stage("evaluation").location.data_region.value == "non_eu"
    assert steps.stage("ocr") is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("correction", "evaluation"), [(False, False), (False, True), (True, False), (True, True)]
)
def test_ticked_steps_of_all_steps_equal_the_flow_of_the_ticked_config(
    tmp_path: Path, correction: bool, evaluation: bool
) -> None:
    """
    Given  a config with proofreading and review ticked or not (a review needs proofreading)
    When   the all-steps flow is narrowed to the ticked steps
    Then   it equals the flow the service describes for that very config
    """
    from teachers_teammate.application.data_flow import ticked_steps  # noqa: PLC0415
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(
        tmp_path,
        ocr_engine="tesseract",
        anonymization_enabled=True,
        correction_enabled=correction,
        evaluation_enabled=evaluation,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    narrowed = ticked_steps(
        svc.describe_steps(cfg), correction_enabled=correction, evaluation_enabled=evaluation
    )

    assert narrowed == svc.describe_data_flow(cfg)


@pytest.mark.unit
def test_describe_anonymizer_models_is_empty_without_personal_info_removal(
    tmp_path: Path,
) -> None:
    """
    Given  a config with personal-info removal off
    When   the anonymizer models of the status box are asked for
    Then   there are none, so no model is loaded just to draw the box
    """
    from tests.conftest import make_config  # noqa: PLC0415

    assert _make_svc().describe_anonymizer_models(make_config(tmp_path)) == []


@pytest.mark.unit
def test_describe_anonymizer_models_names_the_resolved_models(
    tmp_path: Path, monkeypatch, mocker
) -> None:
    """
    Given  personal-info removal on, a German document and a system language of English
    When   the anonymizer models of the status box are asked for
    Then   the document model and the system model are named, as a run would load them
    """
    from tests.conftest import make_config  # noqa: PLC0415

    mocker.patch(
        "teachers_teammate.infrastructure.spacy_models.installed_spacy_models",
        return_value=["de_core_news_sm", "en_core_web_sm"],
    )
    mocker.patch(
        "teachers_teammate.infrastructure.system_locale.system_language",
        return_value="English",
    )
    cfg = make_config(tmp_path, language="German", anonymization_enabled=True)

    assert _make_svc().describe_anonymizer_models(cfg) == [
        "de_core_news_sm",
        "en_core_web_sm",
    ]


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
        result = svc.check_run_preflight(cfg)

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
        result = svc.check_run_preflight(cfg)

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
        result = svc.check_run_preflight(cfg)

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
        result = svc.check_run_preflight(cfg)

    blocker = result.blocker
    assert blocker is not None
    assert blocker[1].kind is IssueKind.OLLAMA_UNREACHABLE


@pytest.mark.unit
def test_ollama_status_reports_the_version_and_the_models_it_has() -> None:
    """
    Given  a server that answers with a version and two models
    When   ollama_status is asked
    Then   it says running, with the version and the count, and offers no button
    """
    from teachers_teammate.infrastructure.ollama_discovery import ServerInfo  # noqa: PLC0415

    svc = _make_svc()
    info = ServerInfo(version="0.12.3", model_count=2)

    with patch("teachers_teammate.infrastructure.ollama_discovery.probe_server", return_value=info):
        status = svc.ollama_status("http://127.0.0.1:11434")

    assert status.state is OllamaState.RUNNING
    assert (status.version, status.model_count, status.can_start) == ("0.12.3", 2, False)


@pytest.mark.unit
def test_ollama_status_looks_for_the_program_only_when_nothing_answered() -> None:
    """
    Given  a local address that does not answer, and an installed program
    When   ollama_status is asked
    Then   it says not running and offers to start it, and the version stays empty
    """
    svc = _make_svc()

    with (
        patch("teachers_teammate.infrastructure.ollama_discovery.probe_server", return_value=None),
        patch(f"{_INSTALL}.find_ollama_program", return_value=Path("/usr/bin/ollama")),
        patch(f"{_INSTALL}.can_start_ollama", return_value=True),
    ):
        status = svc.ollama_status("http://127.0.0.1:11434")

    assert status.state is OllamaState.NOT_RUNNING
    assert (status.version, status.model_count, status.can_start) == ("", None, True)


@pytest.mark.unit
def test_start_ollama_does_nothing_without_a_program() -> None:
    """
    Given  a computer where no Ollama program was found
    When   start_ollama is called
    Then   nothing is started, so no error is raised for a teacher who has none
    """
    svc = _make_svc()

    with (
        patch(f"{_INSTALL}.find_ollama_program", return_value=None),
        patch(f"{_INSTALL}.start_ollama") as start,
    ):
        svc.start_ollama()

    start.assert_not_called()


@pytest.mark.unit
def test_start_ollama_starts_the_program_where_the_platform_allows_it() -> None:
    """
    Given  an installed Ollama program on a platform where the app may start it
    When   start_ollama is called
    Then   the program is started
    """
    svc = _make_svc()
    program = Path("/Applications/Ollama.app")

    with (
        patch(f"{_INSTALL}.find_ollama_program", return_value=program),
        patch(f"{_INSTALL}.can_start_ollama", return_value=True),
        patch(f"{_INSTALL}.start_ollama") as start,
    ):
        svc.start_ollama()

    start.assert_called_once_with(program)


@pytest.mark.unit
def test_the_setup_plan_comes_from_the_facts_and_the_ratings() -> None:
    """
    Given  a computer with a running Ollama, no models and 100 GB of free disk space
    When   plan_setup is asked for handwritten pages and local proofreading
    Then   the facts are gathered once and the setup plan names the recommended models
    """
    from teachers_teammate.application.setup_plan import (  # noqa: PLC0415
        HandIn,
        ProofreadingPlace,
        SetupAnswers,
    )
    from teachers_teammate.config import DEFAULTS  # noqa: PLC0415
    from teachers_teammate.domain.model_catalog import (  # noqa: PLC0415
        DetectionReason,
        HardwareDetection,
        ModelRating,
        Suitability,
    )

    svc = _make_svc()
    answers = SetupAnswers(
        language="German",
        hand_in=HandIn.HANDWRITTEN,
        proofreading=ProofreadingPlace.LOCAL,
        ollama_url="http://127.0.0.1:11434",
        hardware_profile="auto",
        local_ai_shown=True,
    )
    hardware = HardwareDetection(profile="8gb", reason=DetectionReason.MANUAL)
    recommended = {"ocr": "qwen2.5vl:7b", "general": "gemma4:e4b"}
    asked_cached_only: list[bool] = []

    def rate(tags, task, profile, url, **kwargs):
        asked_cached_only.append(kwargs["cached_only"])
        return [
            ModelRating(
                tag=tag,
                task=task,
                model_uses=None,
                suitability=Suitability.BEST,
                memory_gb=None,
                note="",
            )
            for tag in tags
        ]

    with (
        patch.object(svc, "ollama_status", return_value=OllamaStatus(OllamaState.RUNNING)),
        patch.object(svc, "list_provider_models", return_value=[]),
        patch.object(svc, "resolve_hardware_profile", return_value=hardware),
        patch.object(svc, "anonymizer_available", return_value=False),
        patch.object(svc, "is_module_importable", return_value=True),
        patch.object(svc, "spacy_model_for_language", return_value="de_core_news_sm"),
        patch.object(svc, "missing_provider_package", return_value=None),
        patch.object(svc, "rate_ollama_models", side_effect=rate),
        patch.object(
            svc,
            "best_ollama_model",
            side_effect=lambda task, profile, **kwargs: recommended[task],
        ),
        patch(f"{_INSTALL}.free_disk_gb", return_value=100.0),
    ):
        plan = svc.plan_setup(answers, dict(DEFAULTS))

    assert plan.settings["ocr_model"] == "qwen2.5vl:7b"
    assert plan.settings["correction_model"] == "gemma4:e4b"
    assert plan.panel["recognition_enabled"] is True
    assert [download.name for download in plan.downloads] == ["qwen2.5vl:7b", "gemma4:e4b"]
    # Background thread: an installed model outside the catalog is classified by the server.
    assert asked_cached_only
    assert not any(asked_cached_only)


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
        result = svc.check_run_preflight(cfg)

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
        result = svc.check_run_preflight(cfg)

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
        result = svc.check_run_preflight(cfg)

    assert result.blocked is False


@pytest.mark.unit
def test_preflight_needs_no_ollama_for_other_engines(tmp_path: Path) -> None:
    """
    Given  a config that uses Tesseract and no Ollama stage
    When   the run preflight is computed
    Then   the Ollama server is never contacted and the run may start
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="tesseract", correction_enabled=False)

    with (
        patch("teachers_teammate.application.service.shutil.which", return_value="/usr/bin/t"),
        patch(
            "teachers_teammate.application.service.list_provider_models_checked",
            side_effect=AssertionError("Ollama must not be contacted"),
        ),
    ):
        result = svc.check_run_preflight(cfg)

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
        issues = svc.check_stage_requirements(cfg, "correction")

    assert [i.message for i in issues] == ["No model is downloaded in Ollama yet."]
    assert issues[0].kind is IssueKind.NO_OLLAMA_MODEL

    with _server_answers([], "✗ Cannot connect to Ollama") as request:
        issues = svc.check_stage_requirements(cfg, "correction")

    request.assert_called_once()
    assert [i.message for i in issues] == ["Ollama is not running. Start it with `ollama serve`."]
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
        assert svc.check_run_preflight(cfg).blocked is False
        assert svc.check_stage_requirements(cfg, "correction") == []

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
        assert svc.check_run_preflight(cfg).ollama_unreachable
    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        assert svc.check_run_preflight(cfg).blocked is False


# ── GPU monitoring add-on ─────────────────────────────────────────────────────

_AM = "teachers_teammate.infrastructure.addon_manager"


@pytest.mark.unit
def test_pending_gpu_monitoring_addon_names_the_missing_package() -> None:
    """
    Given  a frozen build, an NVIDIA GPU and no pynvml
    When   pending_gpu_monitoring_addon is called
    Then   the NVIDIA add-on is returned and nothing is installed
    """
    from teachers_teammate.infrastructure.gpu_detector import GpuInfo  # noqa: PLC0415

    svc = _make_svc()
    with (
        patch(f"{_AM}.can_install_addons", return_value=True),
        patch.object(svc, "detect_gpus", return_value=[GpuInfo("nvidia", "RTX", 8188)]),
        patch(f"{_AM}.is_addon_importable", return_value=False),
        patch(f"{_AM}.install_addon_subprocess") as install,
    ):
        assert svc.pending_gpu_monitoring_addon() == "nvidia"
    install.assert_not_called()


@pytest.mark.unit
def test_pending_gpu_monitoring_addon_is_none_outside_frozen_builds() -> None:
    """
    Given  a source checkout (add-ons cannot be installed)
    When   pending_gpu_monitoring_addon is called
    Then   None is returned and the hardware is not even probed
    """
    svc = _make_svc()
    with (
        patch(f"{_AM}.can_install_addons", return_value=False),
        patch.object(svc, "detect_gpus", side_effect=AssertionError("no probe expected")),
    ):
        assert svc.pending_gpu_monitoring_addon() is None


@pytest.mark.unit
def test_pending_gpu_monitoring_addon_is_none_when_present_or_no_gpu() -> None:
    """
    Given  a frozen build where the package already imports, or no GPU is found
    When   pending_gpu_monitoring_addon is called
    Then   None is returned
    """
    from teachers_teammate.infrastructure.gpu_detector import GpuInfo  # noqa: PLC0415

    svc = _make_svc()
    with patch(f"{_AM}.can_install_addons", return_value=True):
        with (
            patch.object(svc, "detect_gpus", return_value=[GpuInfo("nvidia", "RTX", 8188)]),
            patch(f"{_AM}.is_addon_importable", return_value=True),
        ):
            assert svc.pending_gpu_monitoring_addon() is None
        with patch.object(svc, "detect_gpus", return_value=[]):
            assert svc.pending_gpu_monitoring_addon() is None


@pytest.mark.unit
def test_install_gpu_monitoring_installs_with_timeout_and_refreshes_imports(
    tmp_path: Path,
) -> None:
    """
    Given  the NVIDIA add-on is to be installed unattended
    When   install_gpu_monitoring is called
    Then   pip runs with the background network timeout and the import caches are refreshed
    """
    svc = _make_svc()
    with (
        patch(f"{_AM}.get_packages_dir", return_value=tmp_path),
        patch(f"{_AM}.install_addon_subprocess") as install,
        patch(f"{_AM}.inject_packages_dir") as inject,
        patch(f"{_AM}.clear_stale_modules") as clear,
    ):
        svc.install_gpu_monitoring("nvidia")

    assert install.call_args.args[:2] == ("nvidia", tmp_path)
    assert install.call_args.kwargs["network_timeout"] > 0
    inject.assert_called_once()
    clear.assert_called_once_with("nvidia")


@pytest.mark.unit
def test_install_gpu_monitoring_propagates_pip_failure() -> None:
    """
    Given  pip fails (for example offline)
    When   install_gpu_monitoring is called
    Then   the RuntimeError reaches the caller, which reports it
    """
    svc = _make_svc()
    with (
        patch(f"{_AM}.install_addon_subprocess", side_effect=RuntimeError("pip exited")),
        patch(f"{_AM}.inject_packages_dir") as inject,
        pytest.raises(RuntimeError, match="pip exited"),
    ):
        svc.install_gpu_monitoring("nvidia")
    inject.assert_not_called()


# ── export_text / is_in_input_folder ──────────────────────────────────────


@pytest.mark.unit
def test_export_text_writes_plain_text_unchanged(tmp_path: Path) -> None:
    """
    Given  extracted plain text for a scan
    When   export_text() is called without anonymization
    Then   the file holds exactly that text, UTF-8 encoded, and the folder is created
    """
    out = tmp_path / "out" / "scan.txt"

    _make_svc().export_text(tmp_path / "scan.png", "Grüße, Anna", out)

    assert out.read_text(encoding="utf-8") == "Grüße, Anna"


@pytest.mark.unit
def test_export_text_writes_markdown_as_plain_text(tmp_path: Path) -> None:
    """
    Given  extracted text stored as Markdown
    When   export_text() is called
    Then   the file holds the text without Markdown markup
    """
    out = tmp_path / "scan.txt"

    _make_svc().export_text(tmp_path / "scan.png", "my **bold** bag", out, text_format="markdown")

    assert out.read_text(encoding="utf-8") == "my bold bag"


@pytest.mark.unit
def test_export_text_anonymizes_with_the_given_language_and_config(tmp_path: Path) -> None:
    """
    Given  an anonymizer that replaces "Anna" with a placeholder
    When   export_text() is called with anonymize=True
    Then   the anonymized text is written, the anonymizer got the language and config,
           and the placeholder mapping appears nowhere in the file
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("Dear [PERSON_1]", {"[PERSON_1]": "Anna"})
    config = service_module.AnonymizerConfig()
    out = tmp_path / "scan_anonymized.txt"
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        svc.export_text(
            tmp_path / "scan.png",
            "Dear Anna",
            out,
            anonymize=True,
            language="German",
            anonymizer_config=config,
        )

    ctor.assert_called_once_with("German", config)
    anonymizer.anonymize.assert_called_once_with("Dear Anna")
    assert out.read_text(encoding="utf-8") == "Dear [PERSON_1]"
    assert "Anna" not in out.read_text(encoding="utf-8")


@pytest.mark.unit
def test_export_text_refuses_to_overwrite_the_source(tmp_path: Path) -> None:
    """
    Given  a .txt input document
    When   export_text() is asked to write to the source file itself
    Then   a ValueError is raised and the source keeps its content
    """
    source = tmp_path / "essay.txt"
    source.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="input document"):
        _make_svc().export_text(source, "extracted", source)

    assert source.read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_export_text_refuses_to_overwrite_another_input_document(tmp_path: Path) -> None:
    """
    Given  scan.png and scan.txt are both inputs of the same run
    When   export_text() for scan.png targets scan.txt, with scan.txt listed as protected
    Then   a ValueError is raised and scan.txt keeps its content
    """
    sibling = tmp_path / "scan.txt"
    sibling.write_text("original", encoding="utf-8")

    with pytest.raises(ValueError, match="input document"):
        _make_svc().export_text(
            tmp_path / "scan.png", "extracted", sibling, protected=[sibling, tmp_path / "x.pdf"]
        )

    assert sibling.read_text(encoding="utf-8") == "original"


@pytest.mark.unit
def test_export_text_anonymizes_markdown_before_converting_it_to_plain_text(
    tmp_path: Path,
) -> None:
    """
    Given  Markdown text and an anonymizer that hides "Anna"
    When   export_text() is called with anonymize=True
    Then   the anonymizer sees the stored Markdown (as in the privacy preview) and the file
           holds the anonymized text without markup, with the placeholder intact
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("my **[PERSON_1]** bag", {"[PERSON_1]": "Anna"})
    out = tmp_path / "scan_anonymized.txt"

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        _make_svc().export_text(
            tmp_path / "scan.png", "my **Anna** bag", out, text_format="markdown", anonymize=True
        )

    anonymizer.anonymize.assert_called_once_with("my **Anna** bag")
    assert out.read_text(encoding="utf-8") == "my [PERSON_1] bag"


@pytest.mark.unit
def test_anonymizer_is_never_used_by_two_threads_at_once() -> None:
    """
    Given  a shared anonymizer and several threads anonymizing through the service
    When   the threads run at the same time
    Then   the anonymizer is never entered by more than one thread at a time
    """
    import time  # noqa: PLC0415

    lock = threading.Lock()
    state = {"inside": 0, "max": 0}

    def _slow_anonymize(text: str):
        with lock:
            state["inside"] += 1
            state["max"] = max(state["max"], state["inside"])
        time.sleep(0.02)
        with lock:
            state["inside"] -= 1
        return text, {}

    anonymizer = MagicMock()
    anonymizer.anonymize.side_effect = _slow_anonymize
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        threads = [
            threading.Thread(target=svc.anonymize_preview, args=("text", "English"))
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert state["max"] == 1
    ctor.assert_called_once()


@pytest.mark.unit
def test_export_text_reports_a_missing_spacy_model(tmp_path: Path) -> None:
    """
    Given  a spaCy model that is not downloaded
    When   export_text() is called with anonymize=True
    Then   the OSError reaches the caller and no file is written
    """
    out = tmp_path / "scan_anonymized.txt"

    with (
        patch(
            "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer",
            side_effect=OSError("model missing"),
        ),
        pytest.raises(OSError, match="model missing"),
    ):
        _make_svc().export_text(tmp_path / "scan.png", "text", out, anonymize=True)

    assert not out.exists()


@pytest.mark.unit
def test_warm_up_anonymizer_builds_it_once_for_later_exports(tmp_path: Path) -> None:
    """
    Given  a service whose anonymizer was warmed up
    When   anonymize_preview() is called with the same language and config
    Then   the cached anonymizer is reused instead of being built again
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("x", {})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        svc.warm_up_anonymizer("English")
        svc.anonymize_preview("text", "English")

    ctor.assert_called_once()


@pytest.mark.unit
def test_is_in_input_folder(tmp_path: Path) -> None:
    """
    Given  an input folder with a subfolder, and a sibling folder
    When   is_in_input_folder() is asked about each
    Then   the folder itself always counts, a subfolder only when scanning is recursive,
           and a sibling never
    """
    inputs = tmp_path / "in"
    (inputs / "sub").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    check = ProcessingApplicationService.is_in_input_folder

    assert check(inputs, inputs, recursive=False)
    assert check(inputs / "sub", inputs, recursive=True)
    assert not check(inputs / "sub", inputs, recursive=False)
    assert not check(tmp_path / "out", inputs, recursive=True)


@pytest.mark.unit
def test_export_target_keeps_input_subfolder(tmp_path: Path) -> None:
    """
    Given  two same-named documents in different subfolders of the input folder
    When   export_target() places them in one export folder
    Then   each keeps its subfolder, so they do not collide
    """
    inputs = tmp_path / "in"
    target = ProcessingApplicationService.export_target

    first = target(inputs / "class1" / "essay.png", inputs, tmp_path / "out", ".docx")
    second = target(inputs / "class2" / "essay.png", inputs, tmp_path / "out", ".docx")

    assert first == tmp_path / "out" / "class1" / "essay.docx"
    assert second == tmp_path / "out" / "class2" / "essay.docx"


@pytest.mark.unit
def test_output_refusal_for_text_files_in_input_folder(tmp_path: Path) -> None:
    """
    Given  text output enabled with the output folder inside the input folder
    When   output_refusal_for_run() is asked
    Then   the run is refused; outside the input folder, or without text files, it is fine
    """
    from tests.conftest import make_config  # noqa: PLC0415

    inputs = tmp_path / "in"
    (inputs / "out").mkdir(parents=True)
    (tmp_path / "out").mkdir()
    refusal = ProcessingApplicationService.output_refusal_for_run
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
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path, ocr_engine="tesseract", text_anonymized_enabled=True)
    svc = _make_svc()

    with (
        patch.object(svc, "list_installed_spacy_models", return_value=[]),
        patch("teachers_teammate.application.service.shutil.which", return_value="/usr/bin/t"),
    ):
        result = svc.check_run_preflight(cfg)

    assert result.stage == "anonymizer"


# ── provider packages and model-list source ───────────────────────────────


@pytest.mark.unit
def test_failed_listing_is_asked_again_while_a_live_one_is_remembered() -> None:
    """
    Given  a provider whose first answer is a failed listing (no key) and then a live one
    When   the models are listed three times
    Then   the failed answer is not remembered (the second call asks again, and so notices a
           key entered meanwhile), the live one is, and the cache reports its source
    """
    svc = _make_svc()
    answers = [
        ModelListing(["built-in"], "failed", "no Mistral key is set"),
        ModelListing(["glm-live"], "live"),
    ]
    calls = 0

    def fake(_provider: str, **_kw: str) -> ModelListing:
        nonlocal calls
        calls += 1
        return answers.pop(0)

    with patch("teachers_teammate.application.service.list_provider_models_checked", fake):
        first = svc.list_provider_models_checked("mistral")
        second = svc.list_provider_models_checked("mistral")
        third = svc.list_provider_models_checked("mistral")

    assert (first.source, second.source, third.source) == ("failed", "live", "live")
    assert calls == 2
    cached = svc.get_cached_listing("mistral")
    assert cached is not None and cached.models == ["glm-live"]


@pytest.mark.unit
def test_missing_provider_package_names_the_pip_package_only_when_absent() -> None:
    """
    Given  a service where langchain_mistralai is absent but langchain_openai is installed
    When   missing_provider_package() is asked for each, and for Ollama
    Then   Mistral (global and EU) names its pip package, the installed one and Ollama (whose
           connector is optional) name none
    """
    svc = _make_svc()
    absent = {"langchain_mistralai"}

    with patch.object(
        ProcessingApplicationService,
        "is_module_importable",
        lambda _self, module: module not in absent,
    ):
        assert svc.missing_provider_package("mistral") == "langchain-mistralai"
        assert svc.missing_provider_package("mistral_eu") == "langchain-mistralai"
        assert svc.missing_provider_package("openai") is None
        assert svc.missing_provider_package("ollama") is None


@pytest.mark.unit
def test_provider_packages_list_a_shared_package_once() -> None:
    """
    Given  Mistral global and EU, which use one package
    When   list_provider_packages() is read
    Then   the package appears once, and every other provider has its own entry
    """
    packages = _make_svc().list_provider_packages()

    pip_names = [pip for _module, pip in packages.values()]
    assert pip_names.count("langchain-mistralai") == 1
    assert len(pip_names) == len(set(pip_names))
    assert {"openai", "anthropic", "google", "cohere", "ollama"} <= set(packages)


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

    with patch.object(ProcessingApplicationService, "is_module_importable", lambda *_a: False):
        issues = svc.check_stage_requirements(cfg, "correction")

    assert [(i.message, i.kind, i.provider) for i in issues] == [
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

    with patch.object(ProcessingApplicationService, "is_module_importable", lambda *_a: True):
        issues = svc.check_stage_requirements(cfg, "correction")
    with patch.object(ProcessingApplicationService, "is_module_importable", lambda *_a: False):
        package_first = svc.check_stage_requirements(cfg, "correction")

    assert [(i.kind, i.provider) for i in issues] == [(IssueKind.KEY_MISSING, "mistral_eu")]
    assert "Mistral" in issues[0].message
    assert [i.kind for i in package_first] == [IssueKind.PACKAGE_MISSING]
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    with patch.object(ProcessingApplicationService, "is_module_importable", lambda *_a: True):
        assert svc.check_stage_requirements(cfg, "correction") == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("engine", "module"), [("tesseract", "tesseract"), ("paddleocr", "paddleocr")]
)
def test_the_ocr_stage_reports_a_missing_engine(tmp_path: Path, engine: str, module: str) -> None:
    """
    Given  Tesseract or PaddleOCR chosen but not installed
    When   the requirements of the "ocr" stage are checked
    Then   an ENGINE_MISSING issue names the engine; once installed there is none
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine=engine)

    with (
        patch("teachers_teammate.application.service.shutil.which", return_value=None),
        patch("teachers_teammate.application.service.importlib.util.find_spec", return_value=None),
    ):
        issues = svc.check_stage_requirements(cfg, "ocr")
    with (
        patch("teachers_teammate.application.service.shutil.which", return_value="/usr/bin/t"),
        patch(
            "teachers_teammate.application.service.importlib.util.find_spec", return_value=object()
        ),
    ):
        present = svc.check_stage_requirements(cfg, "ocr")

    assert [(i.kind, i.engine) for i in issues] == [(IssueKind.ENGINE_MISSING, engine)]
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
        "teachers_teammate.application.service.list_provider_models_checked",
        side_effect=AssertionError("Ollama must not be contacted"),
    ):
        assert svc.check_stage_requirements(_ollama_config(tmp_path), "ocr") == []


@pytest.mark.unit
def test_a_missing_engine_blocks_the_preflight_in_the_ocr_stage(tmp_path: Path) -> None:
    """
    Given  Tesseract chosen but not installed
    When   the run preflight is computed
    Then   the run is blocked in stage "ocr", before the other steps are looked at
    """
    from tests.conftest import make_config  # noqa: PLC0415

    cfg = make_config(tmp_path, ocr_engine="tesseract", correction_enabled=False)

    with patch("teachers_teammate.application.service.shutil.which", return_value=None):
        result = _make_svc().check_run_preflight(cfg)

    assert result.blocked is True
    assert result.blocker is not None
    assert result.blocker[0] == "ocr"
    assert result.blocker[1].kind is IssueKind.ENGINE_MISSING


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
    needs = ProcessingApplicationService.needs_text_recognition(
        [Path(f) for f in files], recognised={Path(f) for f in recognised}
    )

    assert needs is expected


@pytest.mark.unit
def test_a_run_that_reads_no_page_is_not_blocked_by_a_missing_engine(tmp_path: Path) -> None:
    """
    Given  Tesseract chosen but not installed, and a run over text files only
    When   the preflight runs with recognition=False
    Then   the run is not blocked, while the same preflight with recognition blocks it
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="tesseract", correction_enabled=False)

    with patch("teachers_teammate.application.service.shutil.which", return_value=None):
        reads_nothing = svc.check_run_preflight(cfg, recognition=False)
        reads_pages = svc.check_run_preflight(cfg)

    assert reads_nothing.blocked is False
    assert reads_pages.blocker is not None
    assert reads_pages.blocker[1].kind is IssueKind.ENGINE_MISSING


@pytest.mark.unit
def test_a_missing_paddleocr_is_looked_up_with_fresh_import_caches(tmp_path: Path) -> None:
    """
    Given  PaddleOCR chosen, and a service whose module lookup is recorded
    When   the "ocr" stage is checked
    Then   the lookup goes through is_module_importable (which refreshes the import caches),
           so a package installed from Add-ons meanwhile is seen without a restart
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="paddleocr")
    asked: list[str] = []

    def importable(_self, module: str) -> bool:
        asked.append(module)
        return True

    with patch.object(ProcessingApplicationService, "is_module_importable", importable):
        assert svc.check_stage_requirements(cfg, "ocr") == []

    assert asked == ["paddleocr"]


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
        assert svc.check_run_preflight(cfg).missing_models == ("qwen3.5:9b",)
    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]) as request:
        assert svc.check_run_preflight(cfg).missing_models == ("qwen3.5:9b",)
        request.assert_not_called()
        assert svc.check_run_preflight(cfg, fresh=True).blocked is False
        request.assert_called_once()


@pytest.mark.unit
def test_preflight_without_memory_never_asks_the_memory_question(tmp_path: Path) -> None:
    """
    Given  a config the readiness line checks
    When   the preflight runs with memory=False
    Then   memory_warning_for_run is never called, so a bare readiness question stays cheap
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="tesseract", correction_enabled=False)

    with (
        patch("teachers_teammate.application.service.shutil.which", return_value="/usr/bin/t"),
        patch.object(svc, "memory_warning_for_run", side_effect=AssertionError("asked")),
    ):
        result = svc.check_run_preflight(cfg, memory=False)

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
    issue = RequirementIssue("No key.", IssueKind.KEY_MISSING, provider="openai")

    def blocker(**fields) -> tuple[str, RequirementIssue]:
        found = RunPreflight(**fields).blocker
        assert found is not None
        return found

    assert RunPreflight().blocker is None
    stage, found = blocker(ocr_not_allowed="Cloud model.")
    assert (stage, found.kind, found.message) == (
        "ocr",
        IssueKind.OCR_NOT_ALLOWED,
        "Cloud model.",
    )
    stage, found = blocker(ollama_unreachable="✗ down")
    assert (stage, found.kind) == ("", IssueKind.OLLAMA_UNREACHABLE)
    stage, found = blocker(missing_models=("a:1", "b:2"))
    assert (stage, found.kind, found.models) == ("", IssueKind.MODEL_MISSING, ("a:1", "b:2"))
    assert "'a:1', 'b:2'" in found.message
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
        model_issue = svc.check_stage_requirements(cfg, "correction")[0]
    with patch.object(svc, "list_installed_spacy_models", return_value=["en_core_web_sm"]):
        name_issue = svc.check_stage_requirements(
            dataclasses.replace(cfg, language="German"), "anonymizer"
        )[0]

    assert model_issue.kind is IssueKind.MODEL_MISSING
    assert model_issue.models == (cfg.effective_correction_model,)
    assert name_issue.kind is IssueKind.NAME_DETECTION_MISSING
    assert name_issue.models == (svc.spacy_model_for_language("German"),)


@pytest.mark.unit
def test_is_module_importable_refreshes_the_import_caches() -> None:
    """
    Given  a package installed while the app is running
    When   is_module_importable() is asked
    Then   the import caches are invalidated first, so the new package is found at once
    """
    with patch("teachers_teammate.application.service.importlib.invalidate_caches") as refresh:
        _make_svc().is_module_importable("json")

    refresh.assert_called_once()


# ── this computer's memory in hardware detection and ratings ──────────────


@pytest.mark.unit
def test_detect_hardware_reports_the_computers_memory_from_the_probe(monkeypatch) -> None:
    """
    Given  a computer with a 16 GB card and 32 GB of memory
    When   detect_hardware is called for the local server
    Then   the detection carries both, and a model too big for the card is rated slow with it
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(
        gpu_detector, "detect_gpus", lambda: [gpu_detector.GpuInfo("nvidia", "RTX 3080", 16384)]
    )
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 32.0)
    svc = _make_svc()

    detection = svc.detect_hardware("http://127.0.0.1:11434")

    assert (detection.profile, detection.memory_gb, detection.system_memory_gb) == (
        "16gb",
        16.0,
        32.0,
    )
    rating = svc.rate_ollama_models(
        ["gpt-oss:20b"], "general", "8gb", "http://127.0.0.1:11434", system_memory_gb=32.0
    )[0]
    assert rating.suitability.value == "slow"


@pytest.mark.unit
def test_a_chosen_profile_carries_the_memory_only_for_a_local_server(monkeypatch) -> None:
    """
    Given  a chosen profile ("8gb") and a computer with 32 GB of memory
    When   it is resolved for the local server and for a server on another computer
    Then   the local one reports the memory without probing the graphics card; the remote one
           reports none (that memory belongs to another computer)
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    def no_graphics_probe(*_a, **_k):
        raise AssertionError("a chosen profile must not probe the graphics card")

    monkeypatch.setattr(gpu_detector, "detect_gpus", no_graphics_probe)
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 32.0)
    svc = _make_svc()

    local = svc.resolve_hardware_profile("8gb", "http://127.0.0.1:11434")
    remote = svc.resolve_hardware_profile("8gb", "http://192.168.1.50:11434")

    assert local.system_memory_gb == 32.0
    assert remote.system_memory_gb is None


@pytest.mark.unit
def test_a_chosen_profile_on_apple_silicon_carries_no_separate_memory(monkeypatch) -> None:
    """
    Given  an Apple Silicon computer (its graphics share is part of the shared memory)
    When   a profile is chosen by hand for the local server
    Then   no separate memory figure is reported, so a model is not offloaded into memory
           the profile already counts
    """
    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: 16.0)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 16.0)

    detection = _make_svc().resolve_hardware_profile("8gb", "http://127.0.0.1:11434")

    assert detection.system_memory_gb is None


@pytest.mark.unit
def test_hardware_detection_is_logged(monkeypatch, caplog) -> None:
    """
    Given  a probe of this computer
    When   detect_hardware is called
    Then   the probe and the resulting profile are logged, so a wrong suggestion can be traced
    """
    import logging  # noqa: PLC0415

    from teachers_teammate.infrastructure import gpu_detector  # noqa: PLC0415

    monkeypatch.setattr(
        gpu_detector, "detect_gpus", lambda: [gpu_detector.GpuInfo("nvidia", "RTX 3080", None)]
    )
    monkeypatch.setattr(gpu_detector, "detect_apple_unified_memory_gb", lambda: None)
    monkeypatch.setattr(gpu_detector, "detect_system_memory_gb", lambda: 16.0)

    with caplog.at_level(logging.INFO):
        _make_svc().detect_hardware("http://127.0.0.1:11434")

    text = caplog.text
    assert "Probed this computer" in text and "RTX 3080" in text
    assert "Hardware profile" in text and "gpu_memory_unknown" in text


def test_correction_instructions_matches_what_the_corrector_sends(monkeypatch) -> None:
    """
    Given  the service method that builds the dialog's "policy" and "fixed" parts
    When   a review request is built with the same settings
    Then   both parts are exactly the pieces of the system message the corrector sends
    """
    from langchain_core.language_models.fake_chat_models import (  # noqa: PLC0415
        FakeListChatModel,
    )

    from teachers_teammate.infrastructure import (  # noqa: PLC0415
        correction as correction_module,
    )
    from teachers_teammate.infrastructure._llm_invoke import (  # noqa: PLC0415
        StructuredResult,
    )
    from teachers_teammate.infrastructure.proofreading import (  # noqa: PLC0415
        port as port_module,
    )
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        GERMAN_PROMPT,
        CorrectionSettings,
        LangChainCorrector,
    )
    from teachers_teammate.infrastructure.correction_schema import (  # noqa: PLC0415
        fill_placeholders,
    )

    captured: list[str] = []

    def fake(_llm, **kwargs):
        captured.append(kwargs["system"])
        return StructuredResult(error="(stop)", failure="transport")

    monkeypatch.setattr(port_module, "invoke_langchain_structured", fake)
    LangChainCorrector(
        FakeListChatModel(responses=[]),
        settings=CorrectionSettings(level="B2", explanation_language=""),
    ).correct("x", "German")
    system = correction_module._instructions(
        "",
        "German",
        level="B2",
        text_format="plain",
        mode="review",
    )
    assert system == captured[0]

    policy, fixed = ProcessingApplicationService().correction_instructions(
        prompt="",
        language="German",
        level="B2",
        output_mode="review",
        text_format="plain",
    )
    assert system == f"{policy}\n\n{fixed}"
    assert "Answer with a JSON object" in fixed
    assert fill_placeholders(GERMAN_PROMPT, level="B2") == policy


# ── Shared anonymization cache (Phase 3) ─────────────────────────────────────


@pytest.mark.unit
def test_build_cached_anonymizer_shares_one_result_between_run_and_preview() -> None:
    """
    Given  a service whose anonymizer is built once
    When   the cached anonymizer and anonymize_preview() are asked about the same text
    Then   spaCy runs once, so a run and the "What is sent" view agree
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        from_run = svc.build_cached_anonymizer("English", service_module.AnonymizerConfig())
        from_run.anonymize("Anna wrote")
        from_view = svc.anonymize_preview("Anna wrote", "English")

    anonymizer.anonymize.assert_called_once_with("Anna wrote")
    assert from_view == ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})


@pytest.mark.unit
def test_run_then_view_analyses_the_text_once() -> None:
    """
    Given  a pipeline run that anonymized a text through the service cache
    When   the privacy preview asks for the same text afterwards
    Then   only one spaCy pass happened
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.build_cached_anonymizer("English", service_module.AnonymizerConfig()).anonymize("text")
        svc.anonymize_preview("text", "English")

    assert anonymizer.anonymize.call_count == 1


@pytest.mark.unit
def test_changed_anonymizer_settings_analyse_the_text_again() -> None:
    """
    Given  a text already anonymized with one set of patterns
    When   the preview asks for it with different patterns
    Then   the text is analysed again, because the sent text would differ
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymize_preview("text", "English", service_module.AnonymizerConfig(patterns=()))
        svc.anonymize_preview(
            "text", "English", service_module.AnonymizerConfig(patterns=(("ID", r"\d+"),))
        )

    assert anonymizer.anonymize.call_count == 2


@pytest.mark.unit
def test_changed_language_analyses_the_text_again(tmp_path: Path) -> None:
    """
    Given  a text already anonymized for English
    When   the same text is asked for in German
    Then   the text is analysed again with the German model
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ) as ctor:
        svc.anonymize_preview("text", "English")
        svc.anonymize_preview("text", "German")

    assert ctor.call_count == 2
    assert anonymizer.anonymize.call_count == 2


@pytest.mark.unit
def test_clear_anonymizer_cache_forgets_the_anonymized_texts() -> None:
    """
    Given  a service with a cached anonymized text
    When   clear_anonymizer_cache() is called
    Then   the result cache is empty as well, so no hidden text outlives the reset
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})
    svc = _make_svc()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymize_preview("text", "English")
        assert len(svc._anonymization_cache) == 1

        svc.clear_anonymizer_cache()

    assert len(svc._anonymization_cache) == 0


@pytest.mark.unit
def test_run_selected_passes_a_component_factory_with_the_shared_anonymizer(tmp_path: Path) -> None:
    """
    Given  a service with its own component factory
    When   run_selected() builds the pipeline
    Then   the dependencies carry a factory whose build_anonymizer is the service's
           cached builder, so the run fills the same cache a preview reads
    """
    from tests.conftest import make_config  # noqa: PLC0415

    factory = MagicMock(return_value=MagicMock(run=lambda: 0))
    svc = _make_svc(pipeline_factory=factory)

    svc.run_selected(make_config(tmp_path))

    dependencies = factory.call_args.kwargs["dependencies"]
    assert dependencies is not None
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("hidden", {})

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        built = dependencies.component_factory.build_anonymizer(
            "English", service_module.AnonymizerConfig()
        )
        built.anonymize("Anna wrote")

    assert len(svc._anonymization_cache) == 1


# ── What is sent for this file (Phase 4) ─────────────────────────────────────


@pytest.mark.unit
def test_describe_sent_texts_shows_the_cached_text_that_a_run_sends(tmp_path: Path) -> None:
    """
    Given  a run that anonymized a file's text through the shared cache
    When   describe_sent_texts() is asked what that file's proofreading receives
    Then   it reports the same anonymized text, with one spaCy pass in total
    """
    from tests.conftest import make_config  # noqa: PLC0415

    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    svc = _make_svc()
    config = dataclasses.replace(
        make_config(tmp_path),
        correction_enabled=True,
        anonymization_enabled=True,
    )
    anon_config = service_module.AnonymizerConfig()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.build_cached_anonymizer("English", anon_config).anonymize("Anna wrote")
        report = svc.describe_sent_texts(config, "Anna wrote")

    sent = [section for section in report.sections if section.stage.stage == "correction"]
    assert sent, "a correction step that hides names is part of the report"
    assert sent[0].text == "[PERSON_1] wrote"
    assert sent[0].placeholders == ("[PERSON_1]",)
    anonymizer.anonymize.assert_called_once_with("Anna wrote")


@pytest.mark.unit
def test_describe_sent_texts_without_anonymization_sends_names_as_written(
    tmp_path: Path,
) -> None:
    """
    Given  a run with personal-info removal switched off
    When   describe_sent_texts() is asked what is sent
    Then   the text is the extracted text, and no spaCy model is loaded
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    config = dataclasses.replace(
        make_config(tmp_path), correction_enabled=True, anonymization_enabled=False
    )

    with patch("teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer") as ctor:
        report = svc.describe_sent_texts(config, "Anna wrote")

    ctor.assert_not_called()
    sent = [section for section in report.sections if section.stage.stage == "correction"]
    assert sent and sent[0].text == "Anna wrote"
    assert sent[0].names_hidden is False


@pytest.mark.unit
def test_describe_sent_texts_carries_the_prompt_the_stage_sends(tmp_path: Path) -> None:
    """
    Given  a correction step with the built-in German prompt
    When   describe_sent_texts() builds the report
    Then   each section carries the same instructions the stage sends
    """
    from tests.conftest import make_config  # noqa: PLC0415

    svc = _make_svc()
    config = dataclasses.replace(make_config(tmp_path, language="German"), correction_enabled=True)

    report = svc.describe_sent_texts(config, "Anna schrieb das.")

    sent = next(s for s in report.sections if s.stage.stage == "correction")
    policy, fixed = svc.correction_instructions(
        prompt=config.correction_prompt,
        language=config.language,
        level=config.correction_level,
        output_mode=config.correction_output,
    )
    assert sent.prompt == f"{policy}\n\n{fixed}"


@pytest.mark.unit
def test_installed_models_are_listed_once_until_forgotten() -> None:
    """
    Given  a service that resolves anonymizer settings on every preview
    When   the settings are resolved several times, then models are forgotten
    Then   spaCy is asked for its models once per remembered state, and the missing-model
           warning is printed once
    """
    svc = ProcessingApplicationService()
    config = AnonymizerConfig(secondary_model="auto:system")
    with (
        patch.object(svc, "list_installed_spacy_models", return_value=[]) as listing,
        patch(
            "teachers_teammate.infrastructure.system_locale.system_language",
            return_value="German",
        ),
        patch("sys.stderr") as stderr,
    ):
        for _ in range(3):
            svc.resolve_anonymizer_config("English", config)
        assert listing.call_count == 1
        assert stderr.write.call_count > 0
        first = stderr.write.call_count

        svc.resolve_anonymizer_config("English", config)
        assert stderr.write.call_count == first  # same warning is not repeated

        svc.forget_installed_models()
        svc.resolve_anonymizer_config("English", config)

        assert listing.call_count == 2


@pytest.mark.parametrize(
    ("info", "key_set", "missing"),
    [
        ({"needs_api_key": True, "env_key": "TT_TEST_KEY"}, False, True),
        ({"needs_api_key": True, "env_key": "TT_TEST_KEY"}, True, False),
        ({"needs_api_key": False, "env_key": ""}, False, False),
        ({}, False, False),
    ],
)
def test_provider_key_missing_follows_the_provider_info_and_environment(
    monkeypatch, info: dict, key_set: bool, missing: bool
) -> None:
    """
    Given  a provider that does or does not need an API key, with the key set or not
    When   the service is asked whether the key is missing
    Then   it is missing only for a provider that needs a key while none is set
    """
    monkeypatch.delenv("TT_TEST_KEY", raising=False)
    if key_set:
        monkeypatch.setenv("TT_TEST_KEY", "k")
    monkeypatch.setattr(service_module, "get_provider_info", lambda _provider: info)

    assert _make_svc().provider_key_missing("any") is missing


@pytest.mark.unit
def test_the_dialog_shows_the_request_of_the_chosen_model() -> None:
    """
    Given  a request profile that asks for compact JSON, and one that does not
    When   the dialog's instructions are built for each
    Then   only the first one ends with the compact-answer rule, so the dialog shows what is sent
    """
    from teachers_teammate.infrastructure.correction import COMPACT_ANSWER_RULE  # noqa: PLC0415

    svc = ProcessingApplicationService()

    def fixed_of(compact_json: bool) -> str:
        return svc.correction_instructions(
            prompt="",
            language="German",
            level="B2",
            output_mode="review",
            compact_json=compact_json,
        )[1]

    assert fixed_of(True).endswith(COMPACT_ANSWER_RULE)
    assert COMPACT_ANSWER_RULE not in fixed_of(False)


# ── estimate_text_size: the pieces counter after OCR (CW2) ─────────────────


def _long_german_text(repeats: int = 400) -> str:
    """Return a German text of about *repeats* sentences, long enough for several pieces."""
    return "Dies ist ein Satz mit ein paar Wörtern. " * repeats


@pytest.mark.unit
def test_a_short_text_is_proofread_in_one_piece(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a short text and a local model at the base window
    When   the size is estimated
    Then   it is one piece, so the GUI shows nothing
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)

    estimate = svc.estimate_text_size(cfg, "Ein kurzer Satz.")

    assert estimate.pieces == 1
    assert estimate.words == 3


@pytest.mark.unit
def test_the_pieces_count_matches_the_requests_correct_really_makes(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a long German text and a local model at the base window
    When   the size is estimated and the same corrector proofreads the text
    Then   the estimate equals the number of requests correct() really sends
    """
    from teachers_teammate.domain.correction_review import CorrectionOutcome  # noqa: PLC0415
    from teachers_teammate.infrastructure.correction import NativeOllamaCorrector  # noqa: PLC0415
    from teachers_teammate.infrastructure.stage_builder import (  # noqa: PLC0415
        PipelineComponentFactory,
        StageBuilder,
    )

    svc = _make_svc()
    cfg = _ollama_config(tmp_path)
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)
    text = _long_german_text()

    estimate = svc.estimate_text_size(cfg, text)

    sent: list[str] = []

    def fake_correct(self, raw_text, language, *, text_format="plain"):
        sent.append(raw_text)
        return CorrectionOutcome(text=raw_text)

    monkeypatch.setattr(NativeOllamaCorrector, "correct", fake_correct)
    corrector = StageBuilder(cfg, PipelineComponentFactory()).build_corrector()
    assert corrector is not None
    corrector.correct(text, cfg.language)

    assert estimate.pieces is not None and estimate.pieces > 1
    assert estimate.pieces == len(sent)


@pytest.mark.unit
def test_an_online_provider_has_no_piece_count(tmp_path: Path, monkeypatch) -> None:
    """
    Given  an online proofreading provider, which handles any length in one request
    When   the size is estimated
    Then   there is no piece count, because nothing is split
    """
    import sys  # noqa: PLC0415

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, correction_provider="openai", correction_model="gpt-4o-mini")
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)
    fake_mod = MagicMock()
    fake_mod.ChatOpenAI = MagicMock(return_value=MagicMock())

    with patch.dict(sys.modules, {"langchain_openai": fake_mod}):
        estimate = svc.estimate_text_size(cfg, _long_german_text())

    assert estimate.pieces is None
    assert estimate.words_per_piece is None


@pytest.mark.unit
def test_proofreading_off_has_no_piece_count(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a config with proofreading switched off
    When   the size is estimated
    Then   there is no piece count, because nothing is proofread
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, correction_enabled=False)
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)

    estimate = svc.estimate_text_size(cfg, _long_german_text())

    assert estimate.pieces is None
    assert estimate.content_review_fits is None


@pytest.mark.unit
def test_markdown_words_are_counted_without_the_markup(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a Markdown text with headings and emphasis
    When   the size is estimated
    Then   the word count is the words of the text, not of its markup
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path)
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)

    estimate = svc.estimate_text_size(cfg, "# Titel\n\nEin **wichtiger** Satz.", "markdown")

    assert estimate.words == 4


@pytest.mark.unit
def test_a_text_longer_than_the_review_window_does_not_fit_the_review(
    tmp_path: Path, monkeypatch
) -> None:
    """
    Given  a text longer than one content review can read, and a review switched on
    When   the size is estimated
    Then   the review is reported as not fitting, because it cannot be split
    """
    svc = _make_svc()
    cfg = _ollama_config(tmp_path, evaluation_enabled=True, evaluate_provider="ollama")
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)

    estimate = svc.estimate_text_size(cfg, "Ein Satz. " * 20_000)

    assert estimate.content_review_fits is False


@pytest.mark.unit
def test_an_online_provider_is_not_built_to_estimate_the_size(tmp_path: Path, monkeypatch) -> None:
    """
    Given  an OpenAI proofreading config and a factory whose build_llm fails the test if called
    When   the size is estimated
    Then   the estimate has no piece count and no client was built: an online provider brings
           its own request limit, and the GUI thread must not import LangChain for it
    """
    from teachers_teammate.infrastructure import stage_builder  # noqa: PLC0415

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, correction_provider="openai", correction_model="gpt-4o-mini")
    _computer(svc, monkeypatch, profile="cpu", free_gb=32.0)

    def refuse(*_args, **_kwargs):
        raise AssertionError("build_llm must not be called for an online provider")

    monkeypatch.setattr(stage_builder, "build_llm", refuse)

    estimate = svc.estimate_text_size(cfg, _long_german_text())

    assert estimate.pieces is None
    assert estimate.words_per_piece is None
    assert estimate.content_review_fits is None
    assert estimate.words > 0


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
        plan = svc.plan_recognition(files, recognised={tmp_path / "done.jpg"})

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
        plan = svc.plan_recognition([broken])

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
        result = svc.check_run_preflight(cfg, files=scans)

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
        result = svc.check_run_preflight(cfg, files=[Path("notes.txt"), Path("scan.png")])

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
        result = svc.check_run_preflight(cfg, files=[Path("notes.txt"), Path("scan.png")])

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

    assert svc.ocr_refusal_for_run(cfg) == ""
    assert svc.configured_ollama_models(cfg) == []


@pytest.mark.unit
def test_the_data_flow_has_no_recognition_step_while_it_is_off_but_the_steps_view_has(
    tmp_path: Path,
) -> None:
    """
    Given  a config with text recognition switched off
    When   the data flow and the all-steps view are described
    Then   the flow omits text recognition (no page leaves anywhere), the all-steps view
           still lists it so the line under the step can say what ticking it would use
    """
    from teachers_teammate.domain.data_location import STAGE_OCR  # noqa: PLC0415

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, recognition_enabled=False, correction_enabled=False)

    assert svc.describe_data_flow(cfg).stage(STAGE_OCR) is None
    assert svc.describe_steps(cfg).stage(STAGE_OCR) is not None


@pytest.mark.use_case("Recommended_Models_On_First_Start")
def test_first_run_model_settings_respect_the_main_memory() -> None:
    """
    Given  a computer without a graphics card and 12 GB of main memory
    When   first_run_model_settings is called with those figures
    Then   the writing steps get gemma4:e4b, the only writing model the app offers without a
           card: the smaller one it used to offer (qwen3:4b) fails gate 1
    """
    svc = _make_svc()
    settings = svc.first_run_model_settings("cpu", system_memory_gb=12.0)
    assert settings["correction_model"] == "gemma4:e4b"
    assert settings["evaluate_model"] == "gemma4:e4b"
