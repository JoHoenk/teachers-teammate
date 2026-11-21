"""Unit tests for teachers_teammate.application.service."""
# pylint: disable=W0613  # unused-argument — pytest injects fixtures by parameter name; not all are used in every test

from __future__ import annotations

import contextlib
import dataclasses
import logging
import os
from pathlib import Path
import shutil
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from teachers_teammate.domain.setting_values import PreprocessMethod
from teachers_teammate.application import providers as providers_module
from teachers_teammate.application import service as service_module
from teachers_teammate.config import Config
from teachers_teammate.application.composition import (
    build_app_services,
    inject_addon_packages_dir,
)
from teachers_teammate.application.commands import DocumentCommands, WorkNotSavedError
from teachers_teammate.application.correction_options import proofreading_prompts
from teachers_teammate.application.documents import DocumentQueries
from teachers_teammate.application.model_recommendations import ModelRecommendationService
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.application.reader import ReaderService
from teachers_teammate.application.queue import StageState
from teachers_teammate.application.ollama_server import OllamaStatus
from teachers_teammate.application.service import (
    AnonymizerConfig,
    IssueKind,
    ProcessingApplicationService,
    RequirementIssue,
    RunPreflight,
)
from teachers_teammate.domain.data_location import STAGE_CORRECTION
from teachers_teammate.domain.ollama_discovery import OllamaState
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.domain.teacher_work import (
    CorrectionsWork,
    MachineTexts,
    current_texts,
    kept_over,
    text_identity,
    with_corrected,
    with_looked_at,
    with_transcription,
)
from teachers_teammate.infrastructure.file_discovery import FileDiscovery
from teachers_teammate.infrastructure.onnx.store import ModelStore
from teachers_teammate.infrastructure.power import KEEP_AWAKE_REASON
from teachers_teammate.infrastructure.preview_image_store import PreviewImageStore
from teachers_teammate.infrastructure.state_repository import (
    DocumentState,
    OcrResultRecord,
    StateRepository,
    compute_file_hash,
    machine_texts,
)
from teachers_teammate.infrastructure.teacher_work_store import (
    TeacherWorkStore,
    WorkTooNewError,
)
from teachers_teammate.infrastructure.workflow.cache_service import (
    CacheReconciliationService,
    compute_correction_config_hash,
    sent_record,
)
from teachers_teammate.interfaces import SleepBlocker
from tests.conftest import (
    fake_bundle,
    fake_page_models_store,
    fake_reader_store,
    make_config,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _discovery_of(*names: str) -> MagicMock:
    """File discovery that finds the files called *names* (none by default)."""
    return MagicMock(collect_input_files=MagicMock(return_value=[Path(n) for n in names]))


#: The reader's model files as the tests find them: in place, unless a test passes its own store.
_DEFAULT_STORE: ModelStore | None = None


@pytest.fixture(autouse=True)
def _reader_models_in_place(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every service of this file a store that holds the built-in reader's models."""
    monkeypatch.setattr(
        sys.modules[__name__], "_DEFAULT_STORE", fake_reader_store(tmp_path / "default_reader")
    )


def _build_processing(**overrides) -> ProcessingApplicationService:
    """Return the processing service of services built the way the entry points build them."""
    return build_app_services(**overrides).processing


def _make_svc(
    *,
    pipeline_factory=None,
    state_repository_factory=None,
    discovery=None,
    model_store=None,
) -> ProcessingApplicationService:
    mock_pipeline_factory = pipeline_factory or MagicMock(return_value=MagicMock(run=lambda: 0))
    mock_state_factory = state_repository_factory or MagicMock(return_value=MagicMock())
    mock_discovery = discovery or MagicMock()
    return _build_processing(
        discovery=mock_discovery,
        pipeline_factory=mock_pipeline_factory,
        state_repository_factory=mock_state_factory,
        model_store=model_store or _DEFAULT_STORE,
    )


def _providers_of(svc: ProcessingApplicationService) -> ProviderService:
    """Return the provider service *svc* built for itself (its caches are the ones under test)."""
    return svc._providers


def _reader_of(svc: ProcessingApplicationService) -> ReaderService:
    """Return the reader service *svc* built for itself (its model files are under test)."""
    return svc._reader


def _recommendations_of(svc: ProcessingApplicationService) -> ModelRecommendationService:
    """Return the recommendation service *svc* was built with (its probe is the one under test)."""
    return svc._recommendations


def _minimal_state(tmp_path: Path, source: Path) -> DocumentState:
    return DocumentState(
        schema_version=2,
        source_path=str(source.resolve()),
        source_hash="abc123",
    )


def _svc_with_document(
    tmp_path: Path,
    *,
    raw_text: str = "hello world",
    raw_text_format: str = "plain",
    corrected: str = "",
    review: dict | None = None,
) -> tuple[ProcessingApplicationService, Path]:
    """A service over a real state store holding one document the machine read."""
    source = tmp_path / "scan.png"
    source.write_text("x")
    repo = StateRepository(tmp_path / "state")
    state = repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text=raw_text,
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
            raw_text_format=raw_text_format,
        ),
    )
    if corrected:
        repo.record_correction_result(
            source,
            state,
            correction_text=corrected,
            correction_config_hash="corr",
            correction_review=review,
            source_hash=text_identity(raw_text),
        )
    return _make_svc(state_repository_factory=lambda _path: repo), source


# ── run_selected / run_preview_only ───────────────────────────────────────


@pytest.mark.unit
def test_run_selected_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_selected() is called
    Then   pipeline_factory is called once and the RunSummary of its run() is forwarded
    """
    cfg = make_config(tmp_path)
    summary = RunSummary(succeeded=("a.png",))
    mock_pipeline = MagicMock()
    mock_pipeline.run.return_value = summary
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_selected(cfg, [])

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

    svc.run_selected(make_config(tmp_path), [], stop_event=now, stop_after_file_event=after_file)

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
    svc = _build_processing(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run_selected(make_config(tmp_path), [])

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
    svc = _build_processing(pipeline_factory=factory, sleep_blocker=NullSleepBlocker())

    svc.run_selected(make_config(tmp_path), [])

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
    svc = _build_processing(pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False))

    svc.run_selected(make_config(tmp_path), [])

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
    svc = _build_processing(
        pipeline_factory=MagicMock(return_value=pipeline), sleep_blocker=blocker
    )

    with pytest.raises(RuntimeError, match="boom"):
        svc.run_selected(make_config(tmp_path), [])

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
    svc = _build_processing(pipeline_factory=factory, sleep_blocker=blocker)

    svc.run_selected(make_config(tmp_path, keep_awake=False), [])

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
    svc = _build_processing(pipeline_factory=factory, sleep_blocker=_RecordingBlocker(grants=False))

    svc.run_selected(
        make_config(tmp_path),
        [],
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
    factory = MagicMock(return_value=MagicMock(run=lambda: 0))
    svc = _make_svc(pipeline_factory=factory)
    progress = service_module.ProgressCallbacks(on_stage_started=MagicMock())

    svc.run_selected(make_config(tmp_path), [], progress=progress)

    assert factory.call_args.kwargs["progress"] is progress


@pytest.mark.unit
def test_run_preview_only_delegates_to_pipeline_factory(tmp_path: Path) -> None:
    """
    Given  a ProcessingApplicationService with an injected pipeline_factory
    When   run_preview_only() is called
    Then   pipeline_factory is called and run_preview_only() return value is forwarded
    """
    cfg = make_config(tmp_path)
    mock_pipeline = MagicMock()
    mock_pipeline.run_preview_only.return_value = 0
    factory = MagicMock(return_value=mock_pipeline)
    svc = _make_svc(pipeline_factory=factory)

    result = svc.run_preview_only(cfg, [])

    factory.assert_called_once_with(cfg, [])
    mock_pipeline.run_preview_only.assert_called_once()
    assert result == 0


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


# ── list_supported_suffixes


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
def test_page_models_notice_names_the_flag_only_when_the_automatic_preparation_lacks_them(
    tmp_path: Path,
) -> None:
    """
    Given  a service whose model store has no page models
    When   page_models_notice_for_run() is asked for an automatic run, an "Original page" run
           and a run with recognition off
    Then   only the automatic run that reads pages gets the one-line notice naming the flag
    """
    from teachers_teammate.config import OcrConfig  # noqa: PLC0415

    svc = _build_processing(
        pipeline_factory=MagicMock(),
        state_repository_factory=MagicMock(),
        model_store=ModelStore(tmp_path / "models"),
    )

    auto = make_config(tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.AUTO))
    original = make_config(tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.NONE))
    off = make_config(
        tmp_path, ocr=OcrConfig(preprocess_method=PreprocessMethod.AUTO), recognition_enabled=False
    )

    assert "--download-page-models" in svc.page_models_notice_for_run(auto)
    assert svc.page_models_notice_for_run(original) == ""
    assert svc.page_models_notice_for_run(off) == ""


# ── _invalidate_from_stage (static helper) ────────────────────────────────


@pytest.mark.unit
def test_invalidate_from_stage_static_resets_exactly_the_files_it_is_given(tmp_path: Path) -> None:
    """
    Given  one file and a store
    When   _invalidate_from_stage() is called
    Then   that file is invalidated in the store and nothing failed
    """
    source = tmp_path / "doc.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.return_value = MagicMock()

    failures = DocumentCommands._invalidate_from_stage(
        stage="ocr",
        files=[source],
        store=mock_store,
    )

    mock_store.invalidate_from_stage.assert_called_once_with(source, "ocr")
    assert failures == ()


@pytest.mark.unit
def test_invalidate_from_stage_static_accumulates_oserror_failures(tmp_path: Path) -> None:
    """
    Given  a store whose invalidate_from_stage raises OSError
    When   _invalidate_from_stage() is called
    Then   the failure names the file
    """
    source = tmp_path / "bad.png"
    source.touch()
    mock_store = MagicMock()
    mock_store.invalidate_from_stage.side_effect = OSError("disk full")

    failures = DocumentCommands._invalidate_from_stage(
        stage="ocr",
        files=[source],
        store=mock_store,
    )
    assert "bad.png" in failures[0]


# ── The teacher's texts and decisions ──────────────────────────────────────


def _repo_with_document(
    tmp_path: Path, *, corrected: str = "", review: dict | None = None
) -> tuple[StateRepository, Path]:
    """A state store holding one document the machine read, and proofread when asked."""
    source = tmp_path / "input" / "note.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("hello world")
    repo = StateRepository(tmp_path / "state")
    state = repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text="hello world",
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
        ),
    )
    if corrected:
        repo.record_correction_result(
            source,
            state,
            correction_text=corrected,
            correction_config_hash="corr",
            correction_review=review,
            source_hash=text_identity("hello world"),
        )
    return repo, source


def _commands_for(repo: StateRepository, **kwargs) -> DocumentCommands:
    """The window's write commands over *repo*, with the teacher's work in a temporary folder."""
    return DocumentCommands(
        state_store_factory=lambda _config: repo,
        **kwargs,
    )


@pytest.mark.unit
def test_edit_transcription_stores_the_teachers_text(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the machine read
    When   edit_transcription() is called with the teacher's text
    Then   the view carries that text and the cache keeps the machine's own
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.edit_transcription(
        make_config(tmp_path), source=source, text="hello, world", text_format="plain"
    )

    assert view is not None
    assert view.raw_text == "hello, world"
    assert view.work.transcription_changed is True
    assert repo.load_valid(source).raw_text == "hello world"


@pytest.mark.unit
def test_edit_transcription_resets_the_proofreading_and_drops_the_part(tmp_path: Path) -> None:
    """
    Given  a document with a proofreading the teacher checked
    When   edit_transcription() is called
    Then   the cache's correction stage is reset and the corrections part is gone
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    commands = _commands_for(repo)
    config = make_config(tmp_path)
    assert commands.set_checked(config, source=source, checked=True) is not None

    view = commands.edit_transcription(config, source=source, text="hello, world")

    assert view is not None
    assert view.correction_text == ""
    assert view.work.corrections_checked is False
    assert repo.load_valid(source).correction_done is False


@pytest.mark.unit
def test_edit_transcription_keeps_the_transcription_check(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher checked
    When   edit_transcription() is called
    Then   the check stays: it names the text they were given, and they changed it
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)
    config = make_config(tmp_path)
    assert commands.set_checked(config, source=source, checked=True) is not None

    view = commands.edit_transcription(config, source=source, text="hello, world")

    assert view is not None
    assert view.work.transcription_checked is True


@pytest.mark.unit
def test_edit_corrected_text_without_a_proofreading_returns_none(tmp_path: Path) -> None:
    """
    Given  a document the machine read but did not proofread
    When   edit_corrected_text() is called
    Then   None is returned: there is no answer to bind the corrections to
    """
    repo, source = _repo_with_document(tmp_path)

    view = _commands_for(repo).edit_corrected_text(
        make_config(tmp_path), source=source, text="Hello world."
    )

    assert view is None


@pytest.mark.unit
def test_edit_corrected_text_stores_the_teachers_corrections(tmp_path: Path) -> None:
    """
    Given  a document with a stored proofreading
    When   edit_corrected_text() is called with the teacher's text
    Then   the view carries it as the corrected text and the machine's own next to it
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    commands = _commands_for(repo)

    view = commands.edit_corrected_text(make_config(tmp_path), source=source, text="Hello, world!")

    assert view is not None
    assert view.correction_text == "Hello, world!"
    assert view.machine_correction_text == "Hello world."
    assert view.work.corrections_changed is True
    assert repo.load_valid(source).correction_text == "Hello world."


@pytest.mark.unit
@pytest.mark.use_case("Mark_Document_Checked")
def test_set_checked_on_a_transcription_stores_a_copy(tmp_path: Path) -> None:
    """
    Given  a document the machine read but did not proofread
    When   set_checked() is called
    Then   the transcription is checked and stored as a copy of the text it was given
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.set_checked(make_config(tmp_path), source=source, checked=True)

    assert view is not None
    assert view.work.transcription_checked is True
    assert view.work.checked_label == "text"
    assert view.raw_text == "hello world"


@pytest.mark.unit
def test_a_refused_write_is_reported_and_resets_nothing(tmp_path: Path) -> None:
    """
    Given  a work store that refuses to write (a file saved by a newer version)
    When   edit_transcription() is called for a document that has a proofreading
    Then   WorkNotSavedError says so (too new), and the stored proofreading is untouched
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = WorkTooNewError("newer")
    commands = _commands_for(repo, work_store=work_store)

    with pytest.raises(WorkNotSavedError) as raised:
        commands.edit_transcription(make_config(tmp_path), source=source, text="mine")

    assert raised.value.too_new is True
    assert raised.value.name == source.name
    stored = repo.load_valid(source)
    assert stored is not None
    assert stored.correction_done
    assert stored.correction_text == "Hello world."


@pytest.mark.unit
def test_a_write_that_fails_is_reported_not_too_new(tmp_path: Path) -> None:
    """
    Given  a work store that cannot write (a folder that is not writable)
    When   the teacher marks the document as checked
    Then   WorkNotSavedError says so, and says it is not a newer version's file
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = PermissionError("denied")
    commands = _commands_for(repo, work_store=work_store)

    with pytest.raises(WorkNotSavedError) as raised:
        commands.set_checked(make_config(tmp_path), source=source, checked=True)

    assert raised.value.too_new is False


@pytest.mark.unit
def test_invalidate_from_stage_drops_the_parts_the_stage_takes(tmp_path: Path) -> None:
    """
    Given  a document whose transcription and corrections the teacher worked on
    When   the proofreading is re-run, then the reading
    Then   the corrections part goes first, and the transcription part with the reading
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = TeacherWorkStore(tmp_path / "work")
    commands = _commands_for(repo, work_store=work_store)
    config = make_config(tmp_path)
    commands.edit_transcription(config, source=source, text="hello, world")
    commands.edit_corrected_text(config, source=source, text="Hello, world!")
    source_hash = compute_file_hash(source)

    commands.invalidate_from_stage(config, files=[source], stage="correction")

    work = work_store.load(source_hash)
    assert work is not None
    assert work.corrections is None
    assert work.transcription is not None

    commands.invalidate_from_stage(config, files=[source], stage="ocr")

    assert work_store.load(source_hash) is None


@pytest.mark.unit
def test_a_teachers_transcription_survives_reconcile(tmp_path: Path) -> None:
    """
    Given  a transcription the teacher fixed
    When   the cache is reconciled against the same (unchanged) runtime config
    Then   the reading is not redone and the teacher's text is still the one the view shows
    """
    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path, correction_enabled=False)

    repo = StateRepository(tmp_path / "state")
    work_store = TeacherWorkStore(tmp_path / "work")
    commands = DocumentCommands(
        state_store_factory=lambda _c: repo,
        work_store=work_store,
    )
    repo.record_ocr_result(
        source,
        repo.load_or_create(source, compute_file_hash(source)),
        OcrResultRecord(
            source_hash=compute_file_hash(source),
            raw_text="hello",
            preview_img="",
            source_image="",
            ocr_config_hash="ocr",
            preview_config_hash="",
        ),
    )
    commands.edit_transcription(cfg, source=source, text="MANUAL")

    recon = CacheReconciliationService(
        state_repo=repo,
        artifact_store=PreviewImageStore(tmp_path / "art"),
        config=cfg,
        work_store=work_store,
    )
    ctx = recon.prepare(source)

    assert ctx.state.ocr_done is True
    assert ctx.state.raw_text == "hello"
    assert ctx.work is not None
    assert ctx.work.transcription is not None
    assert ctx.work.transcription.text == "MANUAL"


# ── What was sent ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_a_privacy_setting_change_keeps_the_proofreading_and_says_so(tmp_path: Path) -> None:
    """
    Given  a document proofread with personal info removal off
    When   the setting is switched on and the views are reconciled
    Then   the proofreading stays done (so the next run asks no corrector and runs no
           anonymizer) and the view says it was made with other privacy settings
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    off = make_config(tmp_path, correction_enabled=True, anonymization_enabled=False)
    on = make_config(tmp_path, correction_enabled=True, anonymization_enabled=True)

    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(state_repo=repo, artifact_store=artifacts, config=off)
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(on, [source])[0]

    assert row.correction is StageState.DONE
    assert row.sent[STAGE_CORRECTION].record == sent_record(off, STAGE_CORRECTION)
    assert row.sent[STAGE_CORRECTION].other_privacy_settings is True

    # The next run with the new settings: nothing is invalidated, so the corrector is skipped
    # and the anonymizer is never asked for a text that is not sent again.
    after = CacheReconciliationService(
        state_repo=repo, artifact_store=artifacts, config=on
    ).prepare(source)

    assert after.state.correction_done is True
    assert after.state.correction_text == "hello corrected"
    assert after.state.correction_sent == sent_record(off, STAGE_CORRECTION).to_dict()


@pytest.mark.unit
def test_a_stale_stage_shows_no_line_about_how_it_was_sent(tmp_path: Path) -> None:
    """
    Given  a document proofread with one model
    When   the views are reconciled with another model, so the proofreading is stale
    Then   the view says it is not done and carries no record of how it was sent, since the
           result it describes will be made again
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    made = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:9b")
    now = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        state_repo=repo, artifact_store=PreviewImageStore(tmp_path / "art"), config=made
    )
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(now, [source])[0]

    assert row.correction is StageState.STALE
    assert row.sent == {}


@pytest.mark.unit
def test_a_switched_off_proofreading_stays_done_and_is_kept(tmp_path: Path) -> None:
    """
    Given  a document proofread with the stage on
    When   the queue is reconciled with the stage switched off, and a run with it off follows
    Then   the proofreading still shows as done and the run keeps it: off means "this run does
           not do it", not "throw it away"
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    on = make_config(tmp_path, correction_enabled=True)
    off = make_config(tmp_path, correction_enabled=False)
    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(state_repo=repo, artifact_store=artifacts, config=on)
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_rows(off, [source])[0]

    assert row.correction is StageState.DONE

    after = CacheReconciliationService(
        state_repo=repo, artifact_store=artifacts, config=off
    ).prepare(source)

    assert after.state.correction_done is True
    assert after.state.correction_text == "hello corrected"


@pytest.mark.unit
def test_the_queue_and_the_check_view_agree_after_reconciliation(tmp_path: Path) -> None:
    """
    Given  a document read and proofread with one model, and a store reconciled with another
    When   the queue row and the Check view are read for it
    Then   both say the same thing: the reading is done and has text, the proofreading is gone
           (the reconciliation dropped it, so neither has a result to show)
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    made = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:9b")
    now = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    repo = StateRepository(tmp_path / "state")
    artifacts = PreviewImageStore(tmp_path / "art")
    recon = CacheReconciliationService(state_repo=repo, artifact_store=artifacts, config=made)
    ctx = recon.prepare(source)
    ctx = recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    recon.record_correction(source, ctx, correction_text="hello corrected", source_text="hello")
    CacheReconciliationService(state_repo=repo, artifact_store=artifacts, config=now).prepare(
        source
    )

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_row(now, source)
    view = svc.documents.load_view(now, source)

    assert view is not None
    assert row.has_text is True
    assert row.has_text == bool(view.raw_text.strip())
    assert row.ocr is StageState.DONE and view.ocr_done is True
    assert row.correction is StageState.NONE and view.correction_done is False
    assert view.correction_text == ""
    assert row.evaluation is StageState.NONE and view.evaluation_done is False


@pytest.mark.unit
def test_the_queue_and_the_check_view_agree_about_a_file_without_a_result(tmp_path: Path) -> None:
    """
    Given  a file the store knows nothing about
    When   the queue row and the Check view are read for it
    Then   both say it has no text and no stage is done
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")

    svc = _make_svc(state_repository_factory=lambda _config: repo)
    row = svc.documents.queue_row(cfg, source)

    assert svc.documents.load_view(cfg, source) is None
    assert row.has_text is False
    assert row.ocr is StageState.NONE
    assert row.correction is StageState.NONE
    assert row.evaluation is StageState.NONE


@pytest.mark.unit
def test_views_without_a_stored_result_cost_no_privacy_fingerprint(tmp_path: Path) -> None:
    """
    Given  a document that has recognised text but no proofreading or review
    When   its view is loaded
    Then   the privacy fingerprint is never computed, because nothing is there to compare
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        state_repo=repo, artifact_store=PreviewImageStore(tmp_path / "art"), config=cfg
    )
    ctx = recon.prepare(source)
    recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    with patch(
        "teachers_teammate.application.documents.privacy_fingerprint", side_effect=AssertionError
    ):
        assert svc.documents.load_view(cfg, source) is not None
        assert svc.documents.queue_rows(cfg, [source])


@pytest.mark.unit
def test_check_document_answers_for_a_stored_document_and_none_without_one(
    tmp_path: Path,
) -> None:
    """
    Given  a service over a state store holding one recognized document
    When   check_document() is asked for that file and for a file without state
    Then   the first gives the Check tab's data and the second gives None
    """

    source = tmp_path / "note.txt"
    source.write_text("hello")
    cfg = make_config(tmp_path)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        state_repo=repo, artifact_store=PreviewImageStore(tmp_path / "art"), config=cfg
    )
    ctx = recon.prepare(source)
    recon.record_ocr(source, ctx, raw_text="hello", preview_img="", source_image="")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    doc = svc.documents.check_document(cfg, source)

    assert doc is not None
    assert doc.source_id == str(source.resolve())
    assert doc.raw_text == "hello"
    assert doc.has_proofreading is False
    assert svc.documents.check_document(cfg, tmp_path / "other.txt") is None


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
        DocumentCommands._invalidate_from_stage(
            stage="nonexistent",
            files=[source],
            store=MagicMock(),
        )


@pytest.mark.unit
def test_check_connection_reader_reports_whether_it_is_downloaded(tmp_path: Path) -> None:
    """
    Given  a service with and one without the reader's model files
    When   check_connection(engine='ppocr') is called
    Then   the first is connected, the second says the reader is not downloaded
    """
    ok, _, msg = _make_svc(model_store=fake_reader_store(tmp_path / "a")).check_connection(
        engine="ppocr"
    )
    missing, _, missing_msg = _make_svc(
        model_store=fake_reader_store(tmp_path / "b", installed=False)
    ).check_connection(engine="ppocr")

    assert ok is True and "built-in reader ready" in msg
    assert missing is False and "not downloaded" in missing_msg and "31 MB" in missing_msg


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

    refusal = svc.reader_refusal_for_run(config, files)

    assert "--download-reader" in refusal
    assert svc.reader_refusal_for_run(ollama, files) == ""
    assert svc.reader_refusal_for_run(off, files) == ""


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

    assert svc.reader_refusal_for_run(config, [text]) == ""
    assert "--download-reader" in svc.reader_refusal_for_run(config, [text, scan])


def _empty_store() -> MagicMock:
    """Return a state store that has no result for any file."""
    store = MagicMock()
    store.load_valid.return_value = None
    return store


def _reader_service(tmp_path: Path) -> ProcessingApplicationService:
    """Return a service over an empty reader store that finds the files of a real folder."""
    return _make_svc(
        model_store=fake_reader_store(tmp_path, installed=False),
        discovery=FileDiscovery(),
        state_repository_factory=lambda _path: _empty_store(),
    )


# ── what the recommendation service is asked for ────────────────────────────


@pytest.mark.unit
def test_edit_transcription_stores_the_notation_it_was_given(tmp_path: Path) -> None:
    """
    Given  a Markdown transcription shown in the GUI
    When   edit_transcription() is called with text_format="markdown"
    Then   the view keeps the Markdown notation and the teacher's text
    """
    repo, source = _repo_with_document(tmp_path)
    commands = _commands_for(repo)

    view = commands.edit_transcription(
        make_config(tmp_path), source=source, text="~~a~~ b!", text_format="markdown"
    )

    assert view is not None
    assert (view.raw_text, view.raw_text_format) == ("~~a~~ b!", "markdown")


# ── Run preflight & model-list caching ──────────────────────────────────────

_INSTALL = "teachers_teammate.infrastructure.ollama_install"


def _ollama_config(tmp_path: Path, **overrides):
    """Config whose OCR and proofreading both use Ollama."""
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
    monkeypatch.setattr(
        _recommendations_of(svc), "resolve_hardware_profile", lambda _setting, _url: detection
    )


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
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr", hardware_profile="auto")

    windows = svc.resolved_windows(cfg)
    svc.documents.queue_rows(cfg, [])

    assert probes == []
    assert (windows.proofreading, windows.evaluation) == (BASE_NUM_CTX, BASE_NUM_CTX)
    assert _recommendations_of(svc).cached_hardware_profile("auto", cfg.ollama_url) is None


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
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
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
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr", correction_model="gemma4:12b")

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
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
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

    unknown = _ollama_config(tmp_path, ocr_engine="ppocr", correction_model="my-own:latest")
    remote = _ollama_config(tmp_path, ocr_engine="ppocr", ollama_url="http://gpu-box.lan:11434")
    no_ollama = _ollama_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    assert svc.memory_warning_for_run(unknown) == ""
    assert svc.memory_warning_for_run(remote) == ""
    assert svc.memory_warning_for_run(no_ollama) == ""


@pytest.mark.unit
def test_the_preflight_says_when_another_model_is_in_the_way(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a local Ollama that holds another model while the run's model is not in memory
    When   the run is checked
    Then   the preflight carries a note naming both, so the teacher knows why the first file
           waits — and nothing when the run's own model is there, or the server is another
           computer's
    """
    from teachers_teammate.infrastructure.ollama_utils import (  # noqa: PLC0415
        LoadedModel,
        ServerActivity,
        WaitKind,
    )

    svc = _make_svc()
    cfg = _ollama_config(tmp_path, ocr_engine="ppocr")
    other = LoadedModel("ministral-3:8b", 1, 1)
    monkeypatch.setattr(
        svc._ollama,
        "activity",
        lambda _url, _model="": ServerActivity(loaded=(other,), busy=("ministral-3:8b",)),
    )

    detail = svc.ollama_wait_for_run(cfg)

    assert detail.kind is WaitKind.QUEUED
    assert (detail.model, detail.other) == ("qwen3.5:9b", "ministral-3:8b")

    mine = LoadedModel("qwen3.5:9b", 1, 1)
    monkeypatch.setattr(
        svc._ollama, "activity", lambda _url, _model="": ServerActivity(loaded=(mine,))
    )
    assert not svc.ollama_wait_for_run(cfg)

    remote = _ollama_config(tmp_path, ocr_engine="ppocr", ollama_url="http://gpu-box.lan:11434")
    assert not svc.ollama_wait_for_run(remote)


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
    monkeypatch.setattr(
        _providers_of(svc), "missing_provider_package", lambda _provider: "langchain-mistralai"
    )

    with _server_answers(["qwen3-vl:8b", "qwen3.5:9b"]):
        result = svc.check_run_preflight(cfg)

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
def test_describe_data_flow_uses_provider_metadata(tmp_path: Path) -> None:
    """
    Given  local text recognition and OpenAI proofreading
    When   the service describes the data flow
    Then   proofreading is an online service named by its provider's display name
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="openai",
    )

    flow = _make_svc().data_flow.describe_data_flow(cfg)

    assert [s.stage for s in flow.outbound] == ["correction"]
    assert flow.outbound[0].location.service == "OpenAI (ChatGPT)"


@pytest.mark.unit
def test_describe_data_flow_reports_the_provider_region(tmp_path: Path) -> None:
    """
    Given  proofreading at an EU provider and content review at a provider outside the EU
    When   the service describes the data flow
    Then   each step carries the region the provider declares, and the worst step decides
    """
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=True,
        correction_provider="mistral_eu",
        evaluation_enabled=True,
        evaluate_provider="openai",
    )

    flow = _make_svc().data_flow.describe_data_flow(cfg)

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
    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        correction_enabled=False,
        evaluation_enabled=False,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    assert svc.data_flow.describe_data_flow(cfg).stage("correction") is None
    steps = svc.data_flow.describe_steps(cfg)

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

    cfg = make_config(
        tmp_path,
        ocr_engine="ppocr",
        anonymization_enabled=True,
        correction_enabled=correction,
        evaluation_enabled=evaluation,
        correction_provider="mistral_eu",
        evaluate_provider="openai",
    )
    svc = _make_svc()

    narrowed = ticked_steps(
        svc.data_flow.describe_steps(cfg),
        correction_enabled=correction,
        evaluation_enabled=evaluation,
    )

    assert narrowed == svc.data_flow.describe_data_flow(cfg)


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
    cfg = make_config(tmp_path, ocr_engine="ppocr", text_anonymized_enabled=True)
    svc = _make_svc()

    with patch.object(svc.anonymization, "list_installed_spacy_models", return_value=[]):
        result = svc.check_run_preflight(cfg)

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

    with patch.object(_providers_of(svc), "_importable", lambda *_a: True):
        issues = svc.check_stage_requirements(cfg, "correction")
    with patch.object(_providers_of(svc), "_importable", lambda *_a: False):
        package_first = svc.check_stage_requirements(cfg, "correction")

    assert [(i.kind, i.provider) for i in issues] == [(IssueKind.KEY_MISSING, "mistral_eu")]
    assert "Mistral" in issues[0].message
    assert [i.kind for i in package_first] == [IssueKind.PACKAGE_MISSING]
    monkeypatch.setenv("MISTRAL_API_KEY", "k")
    with patch.object(_providers_of(svc), "_importable", lambda *_a: True):
        assert svc.check_stage_requirements(cfg, "correction") == []


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
        correction = svc.check_stage_requirements(cfg, "correction")
    anonymizer = svc.check_stage_requirements(cfg, "anonymizer")

    assert [i.kind for i in correction] == [IssueKind.ADDONS_OUTDATED]
    assert [i.kind for i in anonymizer] == [IssueKind.ADDONS_OUTDATED]
    assert svc.check_stage_requirements(cfg, "ocr") == []


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

    issues = svc.check_stage_requirements(cfg, "anonymizer")

    assert [i.kind for i in issues] == [IssueKind.ADDON_RESTART_NEEDED]
    assert issues[0].message == "Restart the app to finish installing name detection."


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
        issues = svc.check_stage_requirements(cfg, "correction")

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
    ).check_stage_requirements(cfg, "ocr")
    present = _make_svc(model_store=fake_reader_store(tmp_path / "b")).check_stage_requirements(
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
        assert svc.check_stage_requirements(_ollama_config(tmp_path), "ocr") == []


@pytest.mark.unit
def test_a_missing_reader_blocks_the_preflight_in_the_ocr_stage(tmp_path: Path) -> None:
    """
    Given  the built-in reader chosen but not downloaded
    When   the run preflight is computed
    Then   the run is blocked in stage "ocr", before the other steps are looked at
    """
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)
    svc = _make_svc(model_store=fake_reader_store(tmp_path, installed=False))

    result = svc.check_run_preflight(cfg)

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
    needs = ProcessingApplicationService.needs_text_recognition(
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

    reads_nothing = svc.check_run_preflight(cfg, recognition=False)
    reads_pages = svc.check_run_preflight(cfg)

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
    svc = _make_svc()
    cfg = make_config(tmp_path, ocr_engine="ppocr", correction_enabled=False)

    with patch.object(svc, "memory_warning_for_run", side_effect=AssertionError("asked")):
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
    with patch.object(
        svc.anonymization, "list_installed_spacy_models", return_value=["en_core_web_sm"]
    ):
        name_issue = svc.check_stage_requirements(
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
        model_missing = svc.check_stage_requirements(cfg, "anonymizer")
    with (
        patch.object(svc.anonymization, "list_installed_spacy_models", return_value=[]),
        patch.object(svc.addons, "is_module_importable", lambda *_a: False),
    ):
        spacy_missing = svc.check_stage_requirements(cfg, "anonymizer")

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

    svc.run_selected(make_config(tmp_path), [])

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

    assert len(svc.anonymization._anonymization_cache) == 1


# ── What is sent for this file (Phase 4) ─────────────────────────────────────


@pytest.mark.unit
def test_describe_sent_texts_shows_the_cached_text_that_a_run_sends(tmp_path: Path) -> None:
    """
    Given  a run that anonymized a file's text through the shared cache
    When   describe_sent_texts() is asked what that file's proofreading receives
    Then   it reports the same anonymized text, with one spaCy pass in total
    """
    anonymizer = MagicMock()
    anonymizer.anonymize.return_value = ("[PERSON_1] wrote", {"[PERSON_1]": "Anna"})
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote")
    config = dataclasses.replace(
        make_config(tmp_path),
        correction_enabled=True,
        anonymization_enabled=True,
    )
    anon_config = service_module.AnonymizerConfig()

    with patch(
        "teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer", return_value=anonymizer
    ):
        svc.anonymization.build_cached_anonymizer("English", anon_config).anonymize("Anna wrote")
        view = svc.documents.load_view(config, source)
        assert view is not None
        report = svc.describe_sent_texts(config, view)

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
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote")
    config = dataclasses.replace(
        make_config(tmp_path), correction_enabled=True, anonymization_enabled=False
    )

    with patch("teachers_teammate.infrastructure.anonymizer.SpacyAnonymizer") as ctor:
        view = svc.documents.load_view(config, source)
        assert view is not None
        report = svc.describe_sent_texts(config, view)

    ctor.assert_not_called()
    sent = [section for section in report.sections if section.stage.stage == "correction"]
    assert sent and sent[0].text == "Anna wrote"
    assert sent[0].names_hidden is False


@pytest.mark.unit
def test_describe_sent_texts_carries_the_prompts_the_steps_send(tmp_path: Path) -> None:
    """
    Given  a proofreading step with the fixed prompts of the exchange
    When   describe_sent_texts() builds the report
    Then   the section carries every step's prompt under its title
    """
    svc, source = _svc_with_document(tmp_path, raw_text="Anna schrieb das.")
    config = dataclasses.replace(make_config(tmp_path, language="German"), correction_enabled=True)

    view = svc.documents.load_view(config, source)
    assert view is not None
    report = svc.describe_sent_texts(config, view)

    sent = next(s for s in report.sections if s.stage.stage == "correction")
    for title, prompt in proofreading_prompts(config):
        assert title in sent.prompt
        assert prompt in sent.prompt


@pytest.mark.unit
def test_describe_sent_texts_adds_the_notes_for_the_ai(tmp_path: Path) -> None:
    """
    Given  a proofreading step with the teacher's notes
    When   describe_sent_texts() builds the report
    Then   the notes appear after the prompts, as the requests carry them
    """
    svc, source = _svc_with_document(tmp_path, raw_text="Anna wrote this.")
    config = dataclasses.replace(
        make_config(tmp_path),
        correction_enabled=True,
        correction_notes="Year 9, second foreign language.",
    )

    view = svc.documents.load_view(config, source)
    assert view is not None
    report = svc.describe_sent_texts(config, view)

    sent = next(s for s in report.sections if s.stage.stage == "correction")
    assert sent.prompt.rstrip().endswith("Year 9, second foreign language.")


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
            assert [issue.kind for issue in svc._name_detection_issues("German")] == [
                IssueKind.NAME_DETECTION_MISSING
            ]

            (packages / "de_core_news_sm").mkdir()
            inject_addon_packages_dir()
            # What the install round does after a download.
            svc.anonymization.forget_installed_models()

            assert svc.anonymization.anonymizer_available("German")
            assert svc._name_detection_issues("German") == []
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

    assert svc.data_flow.describe_data_flow(cfg).stage(STAGE_OCR) is None
    assert svc.data_flow.describe_steps(cfg).stage(STAGE_OCR) is not None


def _proofread(
    tmp_path: Path, *names: str, model: str = "qwen3.5:9b"
) -> tuple[Config, StateRepository, list[Path]]:
    """Return a config, a repository and sources with a stored proofreading each."""
    cfg = make_config(tmp_path, correction_enabled=True, correction_model=model)
    repo = StateRepository(tmp_path / "state")
    recon = CacheReconciliationService(
        state_repo=repo, artifact_store=PreviewImageStore(tmp_path / "art"), config=cfg
    )
    sources: list[Path] = []
    for name in names:
        source = tmp_path / name
        source.write_text(f"text of {name}")
        ctx = recon.prepare(source)
        ctx = recon.record_ocr(
            source, ctx, raw_text=f"text of {name}", preview_img="", source_image=""
        )
        recon.record_correction(
            source,
            ctx,
            correction_text=f"corrected {name}",
            source_text=f"text of {name}",
        )
        sources.append(source)
    return cfg, repo, sources


def _change_work(source: Path, change) -> None:
    """Apply *change* to the work of *source*, as the window's commands do."""
    TeacherWorkStore().update(compute_file_hash(source), source.name, change)


def _machine_of(repo: StateRepository, source: Path):
    state = repo.load(source)
    assert state is not None
    return machine_texts(state)


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_lists_a_stale_proofreading_the_teacher_worked_on(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread document whose corrections the teacher changed, and one they only
           looked at
    When   the documents of a run with other proofreading settings are looked at
    Then   the changed one is stale-but-kept and unanswered, and the other is not listed
    """
    _made, repo, [worked, looked] = _proofread(tmp_path, "worked.txt", "looked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    _change_work(
        looked,
        lambda work: with_looked_at(
            work, ["edit:1"], current_texts(_machine_of(repo, looked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(other, [worked, looked], run_proofreads=True)

    assert result.kept_stale == (worked,)
    assert result.unanswered == (worked,)
    assert result.unbound == ()
    assert result.transcribed == ()
    assert result.empty is False


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_asks_nothing_about_a_proofreading_a_run_does_not_replace(
    tmp_path: Path,
) -> None:
    """
    Given  a proofread document whose corrections the teacher changed
    When   the documents of a run that does not proofread are looked at, with other settings
    Then   nothing is stale-but-kept or unanswered: the run replaces no proofreading
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=False, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(other, [worked], run_proofreads=False)

    assert result.kept_stale == ()
    assert result.unanswered == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_does_not_ask_again_after_keep_my_changes(tmp_path: Path) -> None:
    """
    Given  a document whose corrections the teacher changed and then kept over these settings
    When   the documents of a run with the same settings are looked at
    Then   it is still stale-but-kept, but no longer unanswered
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)
    expected = compute_correction_config_hash(other, svc.resolved_windows(other))

    _change_work(
        worked,
        lambda work: kept_over(work, expected, current_texts(_machine_of(repo, worked), work)),
    )
    result = svc.documents.worked_on_documents(other, [worked], run_proofreads=True)

    assert result.kept_stale == (worked,)
    assert result.unanswered == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_names_corrections_that_belong_to_no_stored_answer(
    tmp_path: Path,
) -> None:
    """
    Given  a document whose corrections the teacher changed, and then the results cache is
           emptied
    When   the documents of a run that proofreads are looked at
    Then   the corrections are named as belonging to no stored answer — and not at all when
           the run does not proofread
    """
    made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    shutil.rmtree(tmp_path / "state")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    proofreading = svc.documents.worked_on_documents(made, [worked], run_proofreads=True)
    reading_only = svc.documents.worked_on_documents(made, [worked], run_proofreads=False)

    assert proofreading.unbound == (worked,)
    assert proofreading.kept_stale == ()
    assert reading_only.unbound == ()


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_names_a_transcription_the_teacher_fixed(tmp_path: Path) -> None:
    """
    Given  a document whose transcription the teacher fixed
    When   the documents of a run are looked at
    Then   it is named as one whose reading a re-read would replace
    """
    made, repo, [source] = _proofread(tmp_path, "essay.txt")
    machine = _machine_of(repo, source)
    _change_work(
        source, lambda work: with_transcription(work, "the essay", machine.raw_text_format, machine)
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(made, [source], run_proofreads=True)

    assert result.transcribed == (source,)


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_worked_on_documents_is_empty_without_work(tmp_path: Path) -> None:
    """
    Given  a proofread document the teacher did nothing to
    When   the documents of a run are looked at
    Then   nothing is listed and the result says so
    """
    made, repo, [source] = _proofread(tmp_path, "essay.txt")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    result = svc.documents.worked_on_documents(made, [source], run_proofreads=True)

    assert result.empty is True


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
        result = svc.check_run_preflight(cfg, files=[scan])

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

    with patch.object(svc, "_check_requirements", return_value=RunPreflight()):
        plain = svc.check_run_preflight(made, files=[source])
        asked = svc.check_run_preflight(made, files=[source], ask_about_work=True)

    assert plain.worked_on.empty is True
    assert asked.worked_on.transcribed == (source,)


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_checking_a_document_whose_proofreading_was_kept_leaves_it_kept(tmp_path: Path) -> None:
    """
    Given  a proofreading the teacher worked on, made with other settings than today's
    When   the teacher marks the document as checked
    Then   the view that comes back still has the proofreading done, and says it was kept
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    view = svc.commands.set_checked(other, source=worked, checked=True)

    assert view is not None
    assert view.correction_done is True
    assert view.work.kept_over_settings is True
    assert view.work.corrections_checked is True


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_a_stored_mark_shows_a_stage_stale_by_its_settings_as_not_done(tmp_path: Path) -> None:
    """
    Given  a proofread document the teacher did nothing to, and other proofreading settings
    When   a mark that is not work (an unchecked toggle) is stored
    Then   the view says the proofreading is not done, as the queue and the run would
    """
    _made, repo, [source] = _proofread(tmp_path, "essay.txt")
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    view = svc.commands.set_checked(other, source=source, checked=False)

    assert view is not None
    assert view.correction_done is False
    assert view.work.kept_over_settings is False


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_corrections_of_no_stored_answer_are_dropped_and_not_asked_about_again(
    tmp_path: Path,
) -> None:
    """
    Given  corrections the teacher worked on whose proofreading is gone from the cache
    When   the teacher went on with the run and the corrections are dropped
    Then   the next look at the run no longer names them
    """
    made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    shutil.rmtree(tmp_path / "state")
    svc = _make_svc(state_repository_factory=lambda _config: repo)
    assert svc.documents.worked_on_documents(made, [worked], run_proofreads=True).unbound == (
        worked,
    )

    svc.commands.drop_unbound_corrections(made, files=[worked])

    assert svc.documents.worked_on_documents(made, [worked], run_proofreads=True).unbound == ()
    assert TeacherWorkStore().load(compute_file_hash(worked)) is None


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_corrections_that_apply_are_not_dropped_as_unbound(tmp_path: Path) -> None:
    """
    Given  corrections the teacher worked on whose proofreading is still stored
    When   the unbound corrections are dropped
    Then   these are left alone: they belong to a stored answer
    """
    made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.drop_unbound_corrections(made, files=[worked])

    work = TeacherWorkStore().load(compute_file_hash(worked))
    assert work is not None and work.corrections is not None
    assert work.corrections.text == "corrected by the teacher"


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_keeping_the_changes_stamps_the_settings_on_corrections_that_apply(tmp_path: Path) -> None:
    """
    Given  a document whose corrections apply to the stored proofreading
    When   *Keep my changes* is recorded for it, with the files themselves
    Then   the corrections remember the settings they were kept over
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: with_corrected(
            work, "corrected by the teacher", current_texts(_machine_of(repo, worked), work)
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.keep_teacher_work(other, files=[worked])

    stamped = TeacherWorkStore().load(compute_file_hash(worked))
    assert stamped is not None and stamped.corrections is not None
    assert stamped.corrections.kept_under == compute_correction_config_hash(
        other, svc.resolved_windows(other)
    )


@pytest.mark.unit
@pytest.mark.use_case("Keep_Worked_On_Corrections")
def test_keeping_the_changes_does_not_stamp_corrections_of_another_answer(tmp_path: Path) -> None:
    """
    Given  corrections the teacher worked on, written on an answer that is not the stored one
    When   *Keep my changes* is recorded for the document
    Then   they are not stamped: they are not what the teacher kept
    """
    _made, repo, [worked] = _proofread(tmp_path, "worked.txt")
    _change_work(
        worked,
        lambda work: dataclasses.replace(
            work, corrections=CorrectionsWork(bound_to="another-answer", text="Anders")
        ),
    )
    other = make_config(tmp_path, correction_enabled=True, correction_model="qwen3.5:4b")
    svc = _make_svc(state_repository_factory=lambda _config: repo)

    svc.commands.keep_teacher_work(other, files=[worked])

    stamped = TeacherWorkStore().load(compute_file_hash(worked))
    assert stamped is not None and stamped.corrections is not None
    assert stamped.corrections.kept_under == ""


@pytest.mark.unit
def test_a_rerun_that_cannot_drop_the_work_resets_nothing(tmp_path: Path) -> None:
    """
    Given  a document the teacher worked on, whose work cannot be written
    When   the proofreading is invalidated for a re-run
    Then   the failure is reported, the cache is left as it was, and the document is not run
    """
    repo, source = _repo_with_document(tmp_path, corrected="Hello world.")
    work_store = MagicMock()
    work_store.update.side_effect = PermissionError("denied")

    failures = DocumentCommands._invalidate_from_stage(
        stage="correction",
        files=[source],
        store=repo,
        work_store=work_store,
    )

    assert len(failures) == 1 and source.name in failures[0]
    stored = repo.load_valid(source)
    assert stored is not None and stored.correction_done
