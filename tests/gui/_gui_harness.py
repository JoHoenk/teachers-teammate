"""Shared helpers for GUI lifecycle/smoke testing.

Provides:

* :class:`FakeGuiService` — one in-memory stand-in implementing every query the
  windows/dialogs/widgets call, so they can be constructed without network,
  subprocess, or a real pipeline.  ``get_cached_models`` returns a list (not
  ``None``) so the model fetch of non-Ollama providers never starts.
* :func:`neutralize_gui_threads` — monkeypatches the few components that start
  background work on construct or show (and the shared background pool), so a
  smoke test never hits the network and leaves nothing running.
* :func:`assert_no_running_threads` / :func:`exercise_lifecycle` — drive a widget
  through construct → show → close and assert nothing is left running.
* The ``main_window`` and ``stored`` fixtures and the helpers around them, which the
  main-window and Check-controller tests share.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import threading
from pathlib import Path
import tempfile
from typing import Any, ClassVar

import pytest
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from teachers_teammate.application.composition import build_app_services
from teachers_teammate.application.composition import AppServices
from teachers_teammate.application.ollama_server import OllamaServerService
from teachers_teammate.application.queue import QueueRow, StageState
from teachers_teammate.application.providers import ProviderService
from teachers_teammate.application.run_checks import RunChecks, RunPreflight
from teachers_teammate.application.stored_results import StoredSummary
from teachers_teammate.domain.anonymization import AnonymizerConfig
from teachers_teammate.domain.hardware_profile import (
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    concrete_profile,
)
from teachers_teammate.domain.model_catalog import OLLAMA_CATALOG, ModelUses
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.domain.proofreading.review import CorrectionReview
from teachers_teammate.domain.providers import ProviderInfo
from teachers_teammate.gui import _addon_installer_dialog, _anonymizer_config_widget, _dialogs
from teachers_teammate.gui import _install_round
from teachers_teammate.gui import _main_window as main_window_module
from teachers_teammate.gui._background import LatestTask
from teachers_teammate.gui._long_job import LongJobThread
from teachers_teammate.gui._privacy_settings import FlowAcknowledgements
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._run_start import RunStartController
from teachers_teammate.gui._readiness_controller import ReadinessController
from teachers_teammate.gui import _preprocess_preview, _update_check
from teachers_teammate.gui.settings import SettingsContext, SettingsResult
from teachers_teammate.infrastructure.state_repository import StateRepository, compute_file_hash
from teachers_teammate.infrastructure.storage_root import resolve_artifact_dir
from teachers_teammate.texts._types import Notice
from tests.conftest import make_config

from ._service_stubs import LocalDataFlowStub, context_choice_from, services_of


class FakeGuiService(LocalDataFlowStub):
    """Deterministic, offline stand-in for the application services used by the GUI."""

    # ── OCR discovery queries (settings dialogs, OcrConfigSelector) ──────────
    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "ppocr"]

    def get_provider_info(self, provider: str) -> ProviderInfo:
        return ProviderInfo(name=provider, display_name=provider, models=(f"{provider}-model",))

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        return [f"{provider}-model"]

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        return self.list_provider_models("ollama", base_url=base_url)

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        # Non-None → the selector/settings dialogs skip starting a fetch thread.
        return [f"{provider}-model"]

    def get_cached_listing(self, provider: str, *, base_url: str = "") -> ModelListing | None:
        return ModelListing([f"{provider}-model"])

    def list_provider_models_checked(self, provider: str, *, base_url: str = "") -> ModelListing:
        return ModelListing(self.list_provider_models(provider, base_url=base_url))

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        _ = (provider, base_url)

    def is_module_importable(self, module: str) -> bool:
        _ = module
        return True

    def missing_provider_package(self, provider: str) -> str | None:
        _ = provider
        return None

    def addons_outdated(self) -> bool:
        return False

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    def pending_addon_packages(self) -> tuple[str, ...]:
        """Nothing waits for a restart unless a test says so."""
        return ()

    def default_model_for_task(self, provider: str, task: str) -> str:
        return f"{provider}-{task}"

    def check_connection(self, **kwargs: object) -> tuple[bool, bool, str]:
        _ = kwargs
        return True, True, "✓ OK"

    def list_supported_suffixes(self) -> frozenset[str]:
        return frozenset({".pdf", ".png", ".jpg", ".jpeg", ".txt"})

    def available_memory_gb(self) -> float | None:
        """Answer with no free figure: the line then names the total memory only."""
        return None

    # ── hardware profiles & model recommendations ─────────────────────────────
    def ollama_catalog(self) -> tuple:
        return OLLAMA_CATALOG

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        _ = ollama_url
        profile = concrete_profile(setting)
        if profile is not None:
            return HardwareDetection(
                profile=profile,
                reason=DetectionReason.MANUAL,
            )
        return HardwareDetection(
            profile=HardwareProfile.CARD_8GB,
            reason=DetectionReason.GPU_DETECTED,
            gpu_name="Test GPU",
            memory_gb=8.0,
        )

    def context_choice(self, settings: Mapping[str, object], card_gb: float | None):
        """Answer with the real rule: the page must not promise a window the run does not use."""
        return context_choice_from(settings, card_gb)

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        _ = refresh
        return self.resolve_hardware_profile("auto", ollama_url)

    def cached_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection | None:
        """Answer as the real service does once it has probed: this stub never probes."""
        return self.resolve_hardware_profile(setting, ollama_url)

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        _ = ollama_url, cached_only
        return dict.fromkeys(tags, None)

    def rate_ollama_models(
        self,
        tags,
        task,
        profile,
        ollama_url,
        *,
        cached_only: bool = False,
        system_memory_gb: float | None = None,
        card_gb: float | None = None,
    ) -> list:
        _ = ollama_url, cached_only
        from teachers_teammate.domain.model_fit import rate_model  # noqa: PLC0415

        return [
            rate_model(tag, task, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
            for tag in tags
        ]

    def rate_for(self, tags, task, detection, setting, ollama_url, *, cached_only: bool = False):
        from teachers_teammate.domain.hardware_profile import active_profile  # noqa: PLC0415

        return self.rate_ollama_models(
            tags,
            task,
            active_profile(detection, setting),
            ollama_url,
            cached_only=cached_only,
            system_memory_gb=detection.system_memory_gb if detection is not None else None,
            card_gb=detection.card_gb if detection is not None else None,
        )

    def addon_packages_dir(self) -> Path:
        return Path(tempfile.gettempdir()) / "teachers-teammate-addons"

    def inject_addon_packages_dir(self) -> None:
        pass

    def first_run_model_settings(self, profile) -> dict[str, str]:
        _ = profile
        return {"ocr_model": "", "correction_model": "", "evaluate_model": ""}

    # ── anonymizer dialog ────────────────────────────────────────────────────
    def spacy_model_for_language(self, language: str) -> str:
        _ = language
        return "xx_ent_wiki_sm"

    def is_addon_available(self, addon: str) -> bool:
        _ = addon
        return True

    def anonymize_text(self, text: str, language: str, config: AnonymizerConfig | None = None):
        _ = (language, config)
        return text, {}

    # ── the results kept for earlier runs (Settings → Privacy) ───────────────
    stored_summary = StoredSummary(documents=0, bytes_taken=0)

    def summary(self) -> StoredSummary:
        """Answer how much is stored."""
        return self.stored_summary

    def delete(self, *, include_work: bool = False) -> None:
        """Forget how much is stored, and remember what was asked to be deleted."""
        self.deleted_with_work.append(include_work)
        self.stored_summary = StoredSummary(documents=0, bytes_taken=0)

    deleted_with_work: ClassVar[list[bool]] = []

    def busy(self) -> bool:
        """Answer whether a run is going (a test sets it)."""
        return self.run_going

    run_going: ClassVar[bool] = False


class FakePullThread(LongJobThread):
    """An Ollama pull whose signals the test emits itself.

    It looks like it is running (a pull outlives the window that asked for it) and reports
    nothing on its own, so a test decides when progress and the end arrive.
    """

    instances: ClassVar[list[FakePullThread]] = []

    def __init__(self, _service: Any, url: str, model: str) -> None:
        super().__init__()
        self.url = url
        self.model = model
        FakePullThread.instances.append(self)

    def work(self) -> None:
        """Never called: :meth:`run` is replaced, so the test alone reports this pull."""

    def run(self) -> None:
        """Report nothing: the test emits this pull's signals."""

    def isRunning(self) -> bool:  # Qt's spelling
        return True


def fake_pull_threads(monkeypatch: Any) -> list[FakePullThread]:
    """Replace the Ollama pull thread with :class:`FakePullThread`; return the ones started."""
    for earlier in FakePullThread.instances:
        earlier.wait(5000)  # letting go of one whose native thread still runs aborts Qt
    FakePullThread.instances = []
    monkeypatch.setattr(_addon_installer_dialog, "_OllamaPullThread", FakePullThread)
    return FakePullThread.instances


def neutralize_gui_threads(monkeypatch: Any) -> None:
    """Patch every construct/show-time background task + network seam to a no-op.

    Keeps the smoke harness offline and starts nothing that would outlive the test.
    """
    monkeypatch.setattr(_install_round._PipInstallThread, "start", lambda self: None)

    # Everything else runs on the shared pool (model lists, spaCy models, the update check,
    # hardware detection, installed-model checks): do not start any of it in the harness.
    monkeypatch.setattr(LatestTask, "submit", lambda self, fn, on_done: 0)
    monkeypatch.setattr(LatestTask, "submit_streaming", lambda self, fn, on_item, on_done: 0)
    monkeypatch.setattr(
        _anonymizer_config_widget.AnonymizerConfigWidget, "_start_spacy_fetch", lambda self: None
    )
    for module in (_update_check, _preprocess_preview):
        monkeypatch.setattr(module, "run_in_background", lambda *a, **k: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ProviderService, "check_connection", lambda *a, **k: (True, True, "✓ OK"))
    monkeypatch.setattr(RunChecks, "check_stage_requirements", lambda *a, **k: [])


def assert_no_running_threads(widget: QWidget) -> None:
    """Assert no :class:`QThread` owned by *widget* is still running."""
    for thread in widget.findChildren(QThread):
        assert not thread.isRunning(), f"QThread still running after close: {thread!r}"


def exercise_lifecycle(qtbot: Any, widget: QWidget) -> None:
    """Construct → show → close *widget* and assert it leaves nothing running."""
    qtbot.addWidget(widget)
    widget.show()
    qtbot.wait(20)  # let show/showEvent run; avoids waitExposed's fragile nested loop offscreen
    assert widget.isVisible()
    widget.close()
    qtbot.wait(20)
    assert_no_running_threads(widget)


class MemorySettings:
    """In-memory ``QSettings`` stand-in (``value`` / ``setValue`` / ``remove``)."""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def value(self, key: str, default: object = None) -> object:
        return self.data.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self.data[key] = value

    def remove(self, key: str) -> None:
        self.data.pop(key, None)


def settings_context(service: Any = None, **overrides: Any) -> SettingsContext:
    """Return a :class:`SettingsContext` for tests: fake service, in-memory acknowledgements.

    A flat stub is handed on as the grouped services the pages ask for.
    """
    store = MemorySettings()
    values: dict[str, Any] = {
        "settings": {},
        "app_service": _as_services(service if service is not None else FakeGuiService()),
        "language": "English",
        "acknowledgements": FlowAcknowledgements(lambda: store),
    }
    values.update(overrides)
    return SettingsContext(**values)


class ScriptedSettingsWindow(QObject):
    """Stands in for ``SettingsWindow`` in the Settings launcher: no UI, a scripted outcome.

    ``exec()`` returns ``code`` and ``values()`` returns ``result``.  Every instance the
    main window creates is kept in :attr:`instances`, with the arguments it was built from.
    """

    preprocess_preview_requested = Signal(object)
    packages_changed = Signal()
    addon_installed = Signal(str)

    code: ClassVar[int] = 0
    result: ClassVar[SettingsResult] = SettingsResult(settings={})
    instances: ClassVar[list[ScriptedSettingsWindow]] = []

    def __init__(self, context, parent=None, *, page, focus="", preselect=()) -> None:
        super().__init__(parent)
        self.context = context
        self.page = page
        self.focus = focus
        self.preselect = list(preselect)
        self.shown: list[tuple[object, str, list[str]]] = []
        type(self).instances.append(self)

    def exec(self) -> int:
        return type(self).code

    def values(self) -> SettingsResult:
        return type(self).result

    def isVisible(self) -> bool:
        return False

    def show_page(self, page, focus="", preselect=()) -> None:
        self.shown.append((page, focus, list(preselect)))


def install_scripted_settings_window(
    monkeypatch: Any, *, accepted: bool = True, result: SettingsResult | None = None
) -> type[ScriptedSettingsWindow]:
    """Make the Settings launcher open a :class:`ScriptedSettingsWindow` that closes with *accepted*."""
    from teachers_teammate.gui import _settings_launcher  # noqa: PLC0415

    scripted = type(
        "_Scripted",
        (ScriptedSettingsWindow,),
        {
            "code": 1 if accepted else 0,
            "result": result or SettingsResult(settings={}),
            "instances": [],
        },
    )
    monkeypatch.setattr(_settings_launcher, "SettingsWindow", scripted)
    return scripted


def _as_services(service: object) -> AppServices:
    """Return *service* as the grouped services, wrapping a flat stub."""
    return service if isinstance(service, AppServices) else services_of(service)


def real_service() -> AppServices:
    """Return the process's fresh services, for a test that needs the real ones."""
    return build_app_services()


# ── The main window, as the window's tests build it ───────────────────────────


def queue_row(
    source_id: str,
    name: str = "",
    *,
    ocr: StageState = StageState.NONE,
    correction: StageState = StageState.NONE,
    evaluation: StageState = StageState.NONE,
    has_text: bool = False,
    has_scan: bool = False,
    **extra: Any,
) -> QueueRow:
    """Return a queue row for *source_id*, named after the file unless told otherwise."""
    return QueueRow(
        source_id=source_id,
        name=name or Path(source_id).name,
        ocr=ocr,
        correction=correction,
        evaluation=evaluation,
        has_text=has_text,
        has_scan=has_scan,
        **extra,
    )


class DummySignal:
    def __init__(self) -> None:
        self._subs: list = []

    def connect(self, fn):
        self._subs.append(fn)

    def disconnect(self) -> None:
        self._subs.clear()

    def emit(self, *args):
        for fn in self._subs:
            fn(*args)


class DummyWorker:
    def __init__(self, _config, files=None, app_service=None) -> None:
        self.file_started = DummySignal()
        self.read_first = DummySignal()
        self.stage_started = DummySignal()
        self.ocr_done = DummySignal()
        self.stage_detail = DummySignal()
        self.first_token = DummySignal()
        self.file_done = DummySignal()
        self.keep_awake_changed = DummySignal()
        self.run_finished = DummySignal()
        self.files = list(files or [])
        self.app_service = app_service
        self.stop_event = threading.Event()
        self.stop_after_file_event = threading.Event()
        self._running = False

    def start(self) -> None:
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def request_stop(self, *, now: bool) -> None:
        self.stop_after_file_event.set()
        if now:
            self.stop_event.set()

    def wait(self, _msecs: int) -> bool:
        self._running = False
        return True


class AlwaysRunning:
    """A worker that reports it is running, for the figures a run shows meanwhile."""

    def isRunning(self) -> bool:
        return True

    def request_stop(self, *, now: bool) -> None:
        pass

    def wait(self, _msecs: int) -> bool:
        return True


def pretend_a_run_is_going(main_window, monkeypatch) -> None:
    """Give the run controller a running worker without starting one."""
    monkeypatch.setattr(main_window._run_controller, "_worker", AlwaysRunning())


def record_notices(monkeypatch) -> list[Notice]:
    """Record every notice the window shows; return the list it appends to."""
    shown: list[Notice] = []
    monkeypatch.setattr(_dialogs, "notify", lambda _parent, notice: shown.append(notice))
    return shown


@pytest.fixture(autouse=True)
def no_network_in_gui_tests(monkeypatch) -> None:
    """Prevent all MainWindow tests from making Ollama/network calls via the dependency guard."""
    monkeypatch.setattr(RunChecks, "check_stage_requirements", lambda *_: [])
    monkeypatch.setattr(RunChecks, "check_run_preflight", lambda *_args, **_kw: RunPreflight())
    # The silent server search must not probe the real computer or its network.
    monkeypatch.setattr(OllamaServerService, "discover_ollama_servers", lambda *_a, **_kw: [])
    # The status row checks its services in the background; never probe a real server.
    monkeypatch.setattr(
        ProviderService, "check_connection", lambda self, **_kw: (False, False, "offline (test)")
    )


@pytest.fixture(autouse=True)
def no_privacy_prompt(monkeypatch) -> None:
    """Skip the "documents will leave this computer" question: tests must never block on it.

    ``make_config`` proofreads with an online provider, so many runs would ask.  The tests
    of the question itself restore the real method through the ``confirm_box`` fixture.
    """
    monkeypatch.setattr(
        RunStartController,
        "_confirm_data_flow_then",
        lambda self, _config, on_ready: on_ready(),
    )


def no_update_check(monkeypatch) -> None:
    """Keep a MainWindow from asking GitHub for a newer release."""
    monkeypatch.setattr(
        main_window_module,
        "check_for_update",
        lambda _newer_release, _on_available, *, owner=None: None,
    )


def record_update_checks(monkeypatch) -> list[object]:
    """Silence the update check and return the list it appends its callback to."""
    asked: list[object] = []
    monkeypatch.setattr(
        main_window_module,
        "check_for_update",
        lambda _newer_release, on_available, *, owner=None: asked.append(on_available),
    )
    return asked


@pytest.fixture
def main_window(qtbot, monkeypatch) -> MainWindow:
    """Create a MainWindow with startup side effects disabled."""
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(ReadinessController, "start_check", lambda self, **_kw: None)
    no_update_check(monkeypatch)
    win = MainWindow(app_service=real_service(), worker_factory=DummyWorker)
    qtbot.addWidget(win)
    return win


def store_document(
    cfg,
    source: Path,
    *,
    raw: str = "",
    corrected: str = "",
    review: CorrectionReview | None = None,
    warning: str = "",
    text_format: str = "plain",
) -> None:
    """Write a stored state for *source*, so the Check tab has something to read."""
    repo = StateRepository(resolve_artifact_dir(cfg.output_dir) / "state")
    state = repo.create(source, compute_file_hash(source))
    repo.save(
        source,
        dataclasses.replace(
            state,
            raw_text=raw,
            correction_text=corrected,
            correction_review=review.to_dict() if review is not None else None,
            correction_warning=warning,
            raw_text_format=text_format,
            ocr_done=bool(raw),
            correction_done=bool(corrected),
        ),
    )


@pytest.fixture
def stored(main_window, monkeypatch, tmp_path: Path):
    """Return a factory that makes real documents with a stored state and queues them."""

    def _make(
        *names: str,
        raw: str = "what was read",
        corrected: str = "",
        review: CorrectionReview | None = None,
        warning: str = "",
        text_format: str = "plain",
    ) -> list[str]:
        cfg = make_config(tmp_path)
        cfg.input_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(main_window._config_panel, "to_config", lambda: cfg)
        source_ids: list[str] = []
        for name in names:
            source = cfg.input_dir / name
            # One essay per file: the work is keyed by content, so two files must differ.
            source.write_text(f"page of {name}", encoding="utf-8")
            store_document(
                cfg,
                source,
                raw=raw,
                corrected=corrected,
                review=review,
                warning=warning,
                text_format=text_format,
            )
            source_ids.append(str(source))
        main_window._results_table.set_queue(
            [
                queue_row(
                    source_id,
                    name,
                    ocr=StageState.DONE if raw else StageState.NONE,
                    correction=StageState.DONE if corrected else StageState.NONE,
                    has_text=bool(raw),
                )
                for source_id, name in zip(source_ids, names, strict=True)
            ]
        )
        main_window._check_controller.update_tab()
        return source_ids

    return _make


class SharedMemorySettings:
    """In-memory QSettings stand-in so tests never touch the user's real settings."""

    store: ClassVar[dict] = {}

    def __init__(self, *_args) -> None:
        pass

    def value(self, key, default=None):
        return self.store.get(key, default)

    def setValue(self, key, value) -> None:
        self.store[key] = value

    def remove(self, key) -> None:
        self.store.pop(key, None)
