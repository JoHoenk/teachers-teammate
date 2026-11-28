"""Unit tests for the run: the files it runs, the keep-awake hold and the preview."""

from __future__ import annotations

from __future__ import annotations
import contextlib
from pathlib import Path
import threading
from unittest.mock import MagicMock, patch
import pytest
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.run_summary import RunSummary
from teachers_teammate.infrastructure.power import KEEP_AWAKE_REASON
from teachers_teammate.interfaces import ProgressCallbacks, SleepBlocker
from tests.conftest import make_config
from ._service_helpers import _build_services, _make_svc, reader_models_in_place


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

    factory.assert_called_once()
    assert factory.call_args.args == (cfg, [])
    assert factory.call_args.kwargs["dependencies"].samples is svc.run._samples
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
