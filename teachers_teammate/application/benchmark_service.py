"""Application service for the benchmark app.

Composes a :class:`~teachers_teammate.application.service.ProcessingApplicationService`
for all OCR discovery/availability queries (so no discovery logic is duplicated)
and adds benchmark-specific operations: running an OCR config over a document,
persisting/listing/deleting runs, and comparing two runs.

The GUI talks only to this service — it never touches the run store, the runner,
or the domain metrics directly.
"""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from pathlib import Path
import threading

from ..config import DEFAULTS, Config, OcrConfig, load_config_file
from ..domain.benchmark import PairComparison, compare_pair
from ..domain.model_catalog import (
    HardwareDetection,
    HardwareProfile,
    ModelCategory,
    ModelRating,
    ModelUses,
)
from ..infrastructure.benchmark.run_store import BenchmarkRunStore, NewRunRequest, StoredRun
from ..infrastructure.benchmark.runner import OcrRunResult, run_ocr
from ..infrastructure.ocr_processor import get_ocr_prompt
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.state_repository import compute_file_hash
from ..infrastructure.workflow.cache_service import compute_ocr_config_hash
from .formatting import TextFormatter
from .service import GpuInfo, ProcessingApplicationService

_Runner = Callable[..., OcrRunResult]


class BenchmarkRunError(RuntimeError):
    """Raised when an OCR run fails so the GUI worker can surface it."""


class BenchmarkApplicationService:
    """App-facing API for running, storing, and comparing benchmark OCR runs."""

    def __init__(
        self,
        *,
        processing_service: ProcessingApplicationService | None = None,
        run_store: BenchmarkRunStore | None = None,
        component_factory: PipelineComponentFactory | None = None,
        runner: _Runner | None = None,
    ) -> None:
        self._processing = processing_service or ProcessingApplicationService()
        self._store = run_store or BenchmarkRunStore()
        self._component_factory = component_factory or PipelineComponentFactory()
        self._runner = runner or run_ocr

    # ── Delegated queries (reuse the pipeline app service) ──────────────────

    def list_providers(self) -> list[str]:
        return self._processing.list_providers()

    def list_ocr_engines(self) -> list[str]:
        return self._processing.list_ocr_engines()

    def default_preprocess_for_engine(self, engine: str) -> str:
        return self._processing.default_preprocess_for_engine(engine)

    def load_saved_settings(self) -> dict:
        """Return the settings the main app saved (``ocr.toml``), or ``{}``.

        Benchmark has no settings of its own: it follows the main app's Ollama
        server and "My computer" choice.  A missing or unreadable file is not an
        error — the defaults apply.
        """
        try:
            path = self._processing.resolve_config_path(None)
            return load_config_file(path) if path is not None else {}
        except Exception:  # noqa: BLE001  # a broken settings file must never keep Benchmark from opening
            return {}

    def get_provider_info(self, provider: str) -> dict:
        return self._processing.get_provider_info(provider)

    def list_provider_models(self, provider: str, *, base_url: str = "") -> list[str]:
        return self._processing.list_provider_models(provider, base_url=base_url)

    def get_cached_models(self, provider: str, *, base_url: str = "") -> list[str] | None:
        return self._processing.get_cached_models(provider, base_url=base_url)

    def invalidate_model_cache(self, provider: str, base_url: str = "") -> None:
        self._processing.invalidate_model_cache(provider, base_url)

    def is_module_importable(self, module: str) -> bool:
        return self._processing.is_module_importable(module)

    def resolve_hardware_profile(self, setting: str, ollama_url: str) -> HardwareDetection:
        """Return the hardware detection for *setting* (delegated)."""
        return self._processing.resolve_hardware_profile(setting, ollama_url)

    def detect_hardware(self, ollama_url: str, *, refresh: bool = False) -> HardwareDetection:
        """Detect the hardware profile (delegated)."""
        return self._processing.detect_hardware(ollama_url, refresh=refresh)

    def detect_gpus(self, *, refresh: bool = False) -> list[GpuInfo]:
        """Return the GPUs of this computer (delegated, served from the shared probe)."""
        return self._processing.detect_gpus(refresh=refresh)

    def classify_ollama_models(
        self, tags: list[str], ollama_url: str, *, cached_only: bool = False
    ) -> dict[str, ModelUses | None]:
        """Classify Ollama models into reading/writing models (delegated)."""
        return self._processing.classify_ollama_models(tags, ollama_url, cached_only=cached_only)

    def rate_ollama_models(
        self,
        tags: list[str],
        task: ModelCategory,
        profile: HardwareProfile,
        ollama_url: str,
        *,
        cached_only: bool = False,
    ) -> list[ModelRating]:
        """Rate Ollama models for a task on a hardware profile (delegated)."""
        return self._processing.rate_ollama_models(
            tags, task, profile, ollama_url, cached_only=cached_only
        )

    def default_model_for_task(self, provider: str, task: str) -> str:
        return self._processing.default_model_for_task(provider, task)

    def list_supported_suffixes(self) -> frozenset[str]:
        return self._processing.list_supported_suffixes()

    def check_connection(self, **kwargs: object) -> tuple[bool, bool, str]:
        return self._processing.check_connection(**kwargs)  # ty: ignore[invalid-argument-type]

    # ── Benchmark operations ────────────────────────────────────────────────

    def build_config(
        self,
        source: Path,
        *,
        ocr: OcrConfig,
        language: str,
        ollama_url: str = DEFAULTS["ollama_url"],
    ) -> Config:
        """Return an OCR-only Config for *source* (correction/evaluation/DOCX disabled)."""
        return Config(
            input_dir=source.parent,
            output_dir=source.parent,
            recursive=False,
            debug=False,
            ocr=ocr,
            language=language,
            ollama_url=ollama_url,
            correction_enabled=False,
            correction_provider=DEFAULTS["correction_provider"],
            correction_model=DEFAULTS["correction_model"],
            evaluation_enabled=False,
            docx_enabled=False,
        )

    def list_runs(self, source: Path) -> list[StoredRun]:
        """Return all stored runs for *source*, newest first."""
        return self._store.list_for(compute_file_hash(source))

    def run_and_store(
        self,
        source: Path,
        *,
        ocr: OcrConfig,
        language: str,
        ollama_url: str = DEFAULTS["ollama_url"],
        stop_event: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> StoredRun:
        """Run OCR for *source* with *ocr* and persist the result as a new run.

        Raises:
            BenchmarkRunError: if the OCR run fails (engine missing, IO, etc.).
        """
        # Always OCR: embedded PDF text would make every engine return the same text.
        ocr = dataclasses.replace(ocr, pdf_text_layer="never")
        config = self.build_config(source, ocr=ocr, language=language, ollama_url=ollama_url)
        if on_progress is not None:
            on_progress(f"Running OCR · {ocr.engine} · {ocr.effective_model or ocr.model}")
        result = self._runner(
            config, source, component_factory=self._component_factory, stop_event=stop_event
        )
        if result.error is not None:
            raise BenchmarkRunError(result.error)

        ocr_config_hash = compute_ocr_config_hash(config, get_ocr_prompt(language), source)
        if on_progress is not None:
            on_progress("Storing run…")
        return self._store.save(
            NewRunRequest(
                document_hash=compute_file_hash(source),
                document_path=str(source.resolve()),
                display_name=source.name,
                ocr=ocr,
                language=language,
                ocr_config_hash=ocr_config_hash,
                raw_text=result.raw_text,
                elapsed_s=result.elapsed_s,
                preview_src=result.preview_img,
                raw_text_format=result.raw_text_format,
            )
        )

    def delete_run(self, source: Path, run_id: str) -> None:
        """Delete a single stored run for *source*."""
        self._store.delete(compute_file_hash(source), run_id)

    def delete_all_runs(self, source: Path) -> None:
        """Delete all stored runs for *source*."""
        self._store.delete_all_for(compute_file_hash(source))

    def compare(self, a: StoredRun, b: StoredRun) -> PairComparison:
        """Return the pairwise comparison of two stored runs.

        Markup is stripped first so formatting notation never counts as a text difference.
        """
        return compare_pair(
            TextFormatter.plain_text(a.raw_text, a.raw_text_format),
            TextFormatter.plain_text(b.raw_text, b.raw_text_format),
        )
