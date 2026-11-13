"""Shared helpers for GUI lifecycle/smoke testing.

Provides:

* :class:`FakeGuiService` — one in-memory stand-in implementing every query the
  windows/dialogs/widgets call, so they can be constructed without network,
  subprocess, or a real pipeline.  ``get_cached_models`` returns a list (not
  ``None``) so the model-fetch thread of non-Ollama providers never starts.
* :func:`neutralize_gui_threads` — monkeypatches the few components that start
  background threads/subprocess on construct or show (and the shared
  ``LatestTask`` background pool), so a smoke test never hits the network and
  leaves no thread running.
* :func:`assert_no_running_threads` / :func:`exercise_lifecycle` — drive a widget
  through construct → show → close and assert nothing is left running.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, cast

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from teachers_teammate.application.service import ProcessingApplicationService
from teachers_teammate.domain.benchmark import PairComparison, compare_pair
from teachers_teammate.domain.formatted_text import TextFormat
from teachers_teammate.domain.model_catalog import (
    OLLAMA_CATALOG,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelUses,
)
from teachers_teammate.domain.model_listing import ModelListing
from teachers_teammate.gui import _anonymizer_config_widget, _model_fetch
from teachers_teammate.gui._background import LatestTask
from teachers_teammate.gui._privacy_settings import FlowAcknowledgements
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._update_check import UpdateCheckThread
from teachers_teammate.gui._worker import _ConnectionCheckThread
from teachers_teammate.gui.settings import SettingsContext, SettingsResult
from teachers_teammate.gui.settings import _addons_page

from ._service_stubs import LocalDataFlowStub, context_choice_from


class FakeGuiService(LocalDataFlowStub):
    """Deterministic, offline stand-in for the application services used by the GUI."""

    # ── OCR discovery queries (settings dialogs, OcrConfigSelector) ──────────
    def list_providers(self) -> list[str]:
        return ["ollama", "openai"]

    def list_ocr_engines(self) -> list[str]:
        return ["ollama", "tesseract", "paddleocr"]

    def default_preprocess_for_engine(self, engine: str) -> str:
        return {"ollama": "grayscale", "tesseract": "clahe"}.get(engine, "none")

    def get_provider_info(self, provider: str) -> dict:
        return {
            "models": [f"{provider}-model"],
            "default_model": f"{provider}-model",
            "needs_api_key": False,
            "env_key": "",
        }

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

    def list_provider_packages(self) -> dict[str, tuple[str, str]]:
        return {}

    def list_correction_prompt_presets(self) -> dict[str, str]:
        return {}

    def default_correction_prompt(self) -> str:
        return ""

    def default_answer_formats(self) -> tuple[str, str]:
        from teachers_teammate.domain.answer_format import (  # noqa: PLC0415
            REVIEW_FORMAT,
            TEXT_FORMAT,
        )

        return REVIEW_FORMAT, TEXT_FORMAT

    def correction_notation_hint(self) -> str:
        return ""

    def evaluation_fixed_instructions(self, text_format: TextFormat = "plain") -> str:
        _ = text_format
        return ""

    def default_model_for_task(self, provider: str, task: str) -> str:
        return f"{provider}-{task}"

    def check_connection(self, **kwargs: object) -> tuple[bool, bool, str]:
        _ = kwargs
        return True, True, "✓ OK"

    def list_supported_suffixes(self) -> frozenset[str]:
        return frozenset({".pdf", ".png", ".jpg", ".jpeg", ".txt"})

    def detect_gpus(self, *, refresh: bool = False) -> list:
        _ = refresh
        return []

    # ── hardware profiles & model recommendations ─────────────────────────────
    def ollama_catalog(self) -> tuple:
        return OLLAMA_CATALOG

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        _ = ollama_url
        if setting in ("cpu", "8gb", "16gb"):
            return HardwareDetection(
                profile=cast(HardwareProfile, setting),
                reason=DetectionReason.MANUAL,
            )
        return HardwareDetection(
            profile="8gb",
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
        from teachers_teammate.domain.model_catalog import rate_model  # noqa: PLC0415

        return [
            rate_model(tag, task, profile, system_memory_gb=system_memory_gb, card_gb=card_gb)
            for tag in tags
        ]

    def on_ollama_models_changed(self, ollama_url: str) -> None:
        _ = ollama_url

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

    def anonymize_preview(self, text: str, language: str, config: object | None = None):
        _ = (language, config)
        return text, {}

    # ── benchmark operations ─────────────────────────────────────────────────
    def list_runs(self, source: Path) -> list:
        _ = source
        return []

    def delete_run(self, source: Path, run_id: str) -> None:
        _ = (source, run_id)

    def delete_all_runs(self, source: Path) -> None:
        _ = source

    def compare(self, a: Any, b: Any) -> PairComparison:
        return compare_pair(getattr(a, "raw_text", ""), getattr(b, "raw_text", ""))


def neutralize_gui_threads(monkeypatch: Any) -> None:
    """Patch every construct/show-time background thread + network seam to a no-op.

    Keeps the smoke harness offline and prevents "QThread destroyed while
    running" by ensuring no infrastructure thread starts during the test.
    """
    for thread_cls in (
        UpdateCheckThread,
        _ConnectionCheckThread,
        _model_fetch._GpuDetectThread,
        _model_fetch._ModelFetchThread,
        _addons_page._PipInstallThread,
        _anonymizer_config_widget._SpacyModelFetchThread,
    ):
        monkeypatch.setattr(thread_cls, "start", lambda self: None)

    # Background tasks (hardware detection, installed-model checks, first start)
    # run on a shared pool: do not start any of them in the smoke harness.
    monkeypatch.setattr(LatestTask, "submit", lambda self, fn, on_done: 0)
    monkeypatch.setattr(MainWindow, "_start_readiness_check", lambda self, **_kw: None)
    monkeypatch.setattr(MainWindow, "_load_toml_if_present", lambda self: None)
    monkeypatch.setattr(
        ProcessingApplicationService, "check_connection", lambda *a, **k: (True, True, "✓ OK")
    )
    monkeypatch.setattr(
        ProcessingApplicationService, "check_stage_requirements", lambda *a, **k: []
    )


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
    """Return a :class:`SettingsContext` for tests: fake service, in-memory acknowledgements."""
    store = MemorySettings()
    values: dict[str, Any] = {
        "settings": {},
        "app_service": service if service is not None else FakeGuiService(),
        "language": "English",
        "acknowledgements": FlowAcknowledgements(lambda: store),
    }
    values.update(overrides)
    return SettingsContext(**values)


class ScriptedSettingsWindow(QObject):
    """Stands in for ``SettingsWindow`` in the main window: no UI, a scripted outcome.

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
    """Make the main window open a :class:`ScriptedSettingsWindow` that closes with *accepted*."""
    from teachers_teammate.gui import _main_window  # noqa: PLC0415

    scripted = type(
        "_Scripted",
        (ScriptedSettingsWindow,),
        {
            "code": 1 if accepted else 0,
            "result": result or SettingsResult(settings={}),
            "instances": [],
        },
    )
    monkeypatch.setattr(_main_window, "SettingsWindow", scripted)
    return scripted
