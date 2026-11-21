"""Text recognition: the engines, the models they read with, and the reader's model files.

One place answers "which engines can read a page, which models does the local server offer
for it, are the reader's model files downloaded, and what does the preparation do to this
page".  The model files are downloaded only on request, never at start-up.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading
import uuid

from ..config import OcrConfig
from ..domain.data_location import StageLocation, is_ollama_cloud_model
from ..domain.local_models import PAGE_MODELS, READER_MODELS
from ..infrastructure.ocr_processor import SUPPORTED_OCR_ENGINES
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.pipeline import preprocess_preview
from ..infrastructure.stage_builder import PipelineComponentFactory
from ..infrastructure.storage_root import resolve_storage_root
from .data_flow import ocr_location, ocr_refusal
from .providers import ProviderService


class ReaderService:
    """The text recognition: the engines, their models, and the reader's model files."""

    def __init__(
        self,
        *,
        model_store: ModelStore | None = None,
        component_factory: PipelineComponentFactory | None = None,
        providers: ProviderService,
    ) -> None:
        self._model_store = model_store or ModelStore()
        # Used only to keep preprocess_preview() in sync with a non-default factory a test
        # swaps a stage builder into.
        self._component_factory = component_factory or PipelineComponentFactory()
        self._providers = providers

    # ── The engines of text recognition ───────────────────────────────────

    def list_ocr_engines(self) -> list[str]:
        """Return supported OCR engine identifiers."""
        return list(SUPPORTED_OCR_ENGINES)

    def list_ocr_models(self, base_url: str = "") -> list[str]:
        """Return the Ollama models usable for text recognition (cloud models are left out)."""
        return [
            model
            for model in self._providers.list_provider_models("ollama", base_url=base_url)
            if not is_ollama_cloud_model(model)
        ]

    def describe_ocr_location(self, engine: str, model: str, ollama_url: str) -> StageLocation:
        """Return where text recognition with these settings runs."""
        return ocr_location(engine, model, ollama_url)

    def describe_ocr_refusal(self, engine: str, model: str, ollama_url: str) -> str:
        """Return why text recognition with these settings is refused, or ``""`` when allowed."""
        return ocr_refusal(engine, model, ollama_url)

    # ── The preview of the automatic image preparation ────────────────────

    def preprocess_preview(
        self,
        source_path: Path,
        ocr: OcrConfig,
        tmp_dir: Path,
    ) -> tuple[Path, Path, list[str]]:
        """Run preprocessing preview for one source file."""
        return preprocess_preview(
            source_path,
            ocr,
            tmp_dir,
            self._component_factory.build_preprocessor,
            self._model_store,
        )

    def resolve_preview_tmp_dir(self) -> Path:
        """Return a fresh unique temporary directory for preview preprocessing."""
        tmp = resolve_storage_root().path / "preview_tmp" / f"pp_preview_{uuid.uuid4().hex}"
        tmp.mkdir(parents=True, exist_ok=True)
        return tmp

    # ── The built-in reader's model files ─────────────────────────────────

    def reader_installed(self) -> bool:
        """Return whether the built-in reader's model files are downloaded."""
        return not self._model_store.missing(READER_MODELS)

    def download_reader(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download the built-in reader's model files (only ever on the teacher's request).

        Raises:
            ModelDownloadError: with a sentence a teacher can act on.
            ModelDownloadCancelledError: when *cancel* was set.
        """
        self._model_store.download(READER_MODELS, on_progress=on_progress, cancel=cancel)

    # ── The page models of the automatic image preparation ────────────────

    def page_models_installed(self) -> bool:
        """Return whether the page models (turn, flatten) are downloaded."""
        return not self._model_store.missing(PAGE_MODELS)

    def download_page_models(
        self,
        on_progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> None:
        """Download the page models (only ever on the teacher's request).

        Raises:
            ModelDownloadError: with a sentence a teacher can act on.
            ModelDownloadCancelledError: when *cancel* was set.
        """
        self._model_store.download(PAGE_MODELS, on_progress=on_progress, cancel=cancel)

    def models_dir(self) -> Path:
        """Return the folder the downloaded model files live in."""
        return self._model_store.root
