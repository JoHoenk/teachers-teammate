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

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QWidget

from teachers_teammate.application.service import ProcessingApplicationService
from teachers_teammate.domain.benchmark import PairComparison, compare_pair
from teachers_teammate.domain.model_catalog import (
    OLLAMA_CATALOG,
    DetectionReason,
    HardwareDetection,
    HardwareProfile,
    ModelUses,
)
from teachers_teammate.gui import _anonymizer_config_dialog, _downloads_dialog, _settings_dialog
from teachers_teammate.gui._background import LatestTask
from teachers_teammate.gui._main_window import MainWindow
from teachers_teammate.gui._update_check import UpdateCheckThread
from teachers_teammate.gui._worker import _ConnectionCheckThread

from ._service_stubs import LocalDataFlowStub


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

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        _ = (provider, base_url)

    def is_module_importable(self, module: str) -> bool:
        _ = module
        return True

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

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        _ = refresh
        return self.resolve_hardware_profile("auto", ollama_url)

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        _ = ollama_url, cached_only
        return dict.fromkeys(tags, None)

    def rate_ollama_models(
        self, tags, task, profile, ollama_url, *, cached_only: bool = False
    ) -> list:
        _ = ollama_url, cached_only
        from teachers_teammate.domain.model_catalog import rate_model  # noqa: PLC0415

        return [rate_model(tag, task, profile) for tag in tags]

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
        _settings_dialog._GpuDetectThread,
        _settings_dialog._ModelFetchThread,
        _downloads_dialog._PipInstallThread,
        _anonymizer_config_dialog._SpacyModelFetchThread,
    ):
        monkeypatch.setattr(thread_cls, "start", lambda self: None)

    # Background tasks (hardware detection, installed-model checks, first start)
    # run on a shared pool: do not start any of them in the smoke harness.
    monkeypatch.setattr(LatestTask, "submit", lambda self, fn, on_done: 0)
    monkeypatch.setattr(MainWindow, "_check_llm_status", lambda self: None)
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
