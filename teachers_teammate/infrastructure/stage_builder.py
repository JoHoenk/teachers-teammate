"""Stage construction for the OCR pipeline.

Separates the responsibility of building individual pipeline stages
from the orchestration logic in :mod:`infrastructure.pipeline`.

:class:`PipelineComponentFactory` holds the constructor callables used
to build each stage.  Swap any callable to inject a test double or
alternative implementation.

:class:`StageBuilder` applies a :class:`PipelineComponentFactory` to a
:class:`~teachers_teammate.config.Config` to produce concrete service
instances.  It is instantiated by :class:`~infrastructure.pipeline.OCRPipeline`
and can also be used in tests to verify stage wiring without running a
full pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

from ..config import Config
from ..exceptions import ProviderNotAvailableError
from ..interfaces import (
    Anonymizer,
    Corrector,
    DocumentCreator,
    Evaluator,
    ImagePreprocessor,
    OCRProcessor,
)
from .anonymizer import DEFAULT_PATTERNS, AnonymizerConfig
from .correction import LangChainCorrector, NativeOllamaCorrector
from .docx_builder import DocxDocumentCreator
from .evaluation import LangChainEvaluator, NativeOllamaEvaluator
from .image_preprocessor import HandwritingPreprocessor
from .llm_factory import build_llm
from .ocr_processor import (
    LangChainOCRProcessor,
    OllamaOCRProcessor,
    PaddleOCRProcessor,
    TesseractOCRProcessor,
)
from .ollama_utils import OllamaClient
from .reporting import Reporter, StdoutReporter
from .workflow.preprocess_service import PreprocessService

# ── Default constructors ───────────────────────────────────────────────────


def _default_build_preprocessor(tmp_dir: Path, save_steps: bool, method: str) -> ImagePreprocessor:
    return HandwritingPreprocessor(tmp_dir=tmp_dir, save_steps=save_steps, method=method)


def _default_build_tesseract_ocr() -> OCRProcessor:
    return TesseractOCRProcessor()


def _default_build_paddle_ocr(language: str) -> OCRProcessor:
    return PaddleOCRProcessor(language=language)


def _default_build_langchain_ocr(llm: BaseChatModel) -> OCRProcessor:
    return LangChainOCRProcessor(llm)


def _default_build_ollama_ocr(
    model_name: str, client: OllamaClient, timeout: int, temperature: float
) -> OCRProcessor:
    return OllamaOCRProcessor(
        model_name=model_name, client=client, timeout=timeout, temperature=temperature
    )


def _default_build_llm(
    provider: str, model: str, base_url: str, temperature: float
) -> BaseChatModel:
    return build_llm(provider, model, base_url=base_url, temperature=temperature)


def _default_build_corrector(llm: BaseChatModel, prompt: str) -> Corrector:
    return LangChainCorrector(llm, prompt)


def _default_build_evaluator(llm: BaseChatModel, prompt: str) -> Evaluator:
    return LangChainEvaluator(llm, prompt)


def _default_build_native_ollama_corrector(
    model: str, client: OllamaClient, prompt: str, timeout: float, temperature: float
) -> Corrector:
    return NativeOllamaCorrector(
        model=model, client=client, prompt=prompt, timeout=timeout, temperature=temperature
    )


def _default_build_native_ollama_evaluator(
    model: str, client: OllamaClient, prompt: str, timeout: float, temperature: float
) -> Evaluator:
    return NativeOllamaEvaluator(
        model=model, client=client, prompt=prompt, timeout=timeout, temperature=temperature
    )


def _default_build_doc_creator(fmt: str) -> DocumentCreator:
    return DocxDocumentCreator(fmt=fmt)


def _default_build_anonymizer(language: str, config: AnonymizerConfig) -> Anonymizer:
    from .anonymizer import SpacyAnonymizer  # noqa: PLC0415

    return SpacyAnonymizer(language, config)


# ── Factory ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineComponentFactory:
    """Callable set for pipeline stage construction.

    Every field is a constructor callable with a fixed signature.
    Swap any callable in tests or adapters to inject alternative
    implementations without subclassing.
    """

    build_preprocessor: Callable[[Path, bool, str], ImagePreprocessor] = _default_build_preprocessor
    build_tesseract_ocr: Callable[[], OCRProcessor] = _default_build_tesseract_ocr
    build_paddle_ocr: Callable[[str], OCRProcessor] = _default_build_paddle_ocr
    build_langchain_ocr: Callable[[BaseChatModel], OCRProcessor] = _default_build_langchain_ocr
    build_ollama_ocr: Callable[[str, OllamaClient, int, float], OCRProcessor] = (
        _default_build_ollama_ocr
    )
    build_llm: Callable[[str, str, str, float], BaseChatModel] = _default_build_llm
    build_corrector: Callable[[BaseChatModel, str], Corrector] = _default_build_corrector
    build_evaluator: Callable[[BaseChatModel, str], Evaluator] = _default_build_evaluator
    build_native_ollama_corrector: Callable[[str, OllamaClient, str, float, float], Corrector] = (
        _default_build_native_ollama_corrector
    )
    build_native_ollama_evaluator: Callable[[str, OllamaClient, str, float, float], Evaluator] = (
        _default_build_native_ollama_evaluator
    )
    build_doc_creator: Callable[[str], DocumentCreator] = _default_build_doc_creator
    build_anonymizer: Callable[[str, AnonymizerConfig], Anonymizer] = _default_build_anonymizer


# ── Builder ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageBuilder:
    """Builds concrete pipeline stage instances from a Config and a factory.

    Separates stage-construction logic from pipeline orchestration so that
    each stage can be built and tested independently.
    """

    config: Config
    factory: PipelineComponentFactory
    reporter: Reporter = field(default_factory=StdoutReporter)

    def _build_ollama_client(self) -> OllamaClient:
        """Create the shared OllamaClient for all Ollama-backed stages."""
        return OllamaClient(self.config.ollama_url)

    def build_preprocessor(self, output_dir: Path) -> ImagePreprocessor:
        return self.factory.build_preprocessor(
            output_dir, self.config.debug, self.config.ocr.preprocess_method
        )

    def build_preprocessor_service(self, output_dir: Path) -> PreprocessService:
        return PreprocessService(
            tmp_dir=output_dir,
            preprocessor=self.build_preprocessor(output_dir),
        )

    def build_ocr(self, ollama_client: OllamaClient | None = None) -> OCRProcessor:
        cfg = self.config
        ocr = cfg.ocr
        factory = self.factory
        if ocr.engine == "tesseract":
            return factory.build_tesseract_ocr()
        if ocr.engine == "paddleocr":
            return factory.build_paddle_ocr(cfg.language)
        if ocr.engine == "langchain":
            if not ocr.provider:
                raise ValueError(
                    "ocr_provider must be set when ocr_engine is 'langchain' "
                    "(e.g. --ocr-provider openai)"
                )
            llm = factory.build_llm(
                ocr.provider, ocr.effective_model, cfg.ollama_url, ocr.temperature
            )
            return factory.build_langchain_ocr(llm)
        client = ollama_client or self._build_ollama_client()
        return factory.build_ollama_ocr(ocr.model, client, cfg.ocr_timeout, ocr.temperature)

    def build_correction_and_anonymizer(
        self, ollama_client: OllamaClient | None = None
    ) -> tuple[Corrector | None, Anonymizer | None]:
        cfg = self.config
        factory = self.factory
        correction: Corrector | None = None
        anonymizer: Anonymizer | None = None

        if cfg.correction_enabled:
            if cfg.correction_provider == "ollama":
                client = ollama_client or self._build_ollama_client()
                correction = factory.build_native_ollama_corrector(
                    cfg.effective_correction_model,
                    client,
                    cfg.correction_prompt,
                    float(cfg.ocr_timeout),
                    cfg.correction_temperature,
                )
            else:
                llm = factory.build_llm(
                    cfg.correction_provider,
                    cfg.effective_correction_model,
                    cfg.ollama_url,
                    cfg.correction_temperature,
                )
                correction = factory.build_corrector(llm, cfg.correction_prompt)

        if cfg.anonymization_enabled:
            if not cfg.correction_enabled:
                self.reporter.warn(
                    "WARNING: Anonymization requires correction to be enabled; "
                    "disabling anonymization."
                )
            else:
                try:
                    anon_config = AnonymizerConfig(
                        secondary_model=cfg.anonymizer_secondary_model,
                        patterns=tuple(cfg.anonymizer_patterns)
                        if cfg.anonymizer_patterns is not None
                        else tuple(DEFAULT_PATTERNS),
                    )
                    anonymizer = factory.build_anonymizer(cfg.language, anon_config)
                except (ImportError, OSError) as exc:
                    raise ProviderNotAvailableError(f"Cannot initialise anonymizer: {exc}") from exc

        return correction, anonymizer

    def build_evaluation(
        self,
        correction: Corrector | None,
        ollama_client: OllamaClient | None = None,
    ) -> Evaluator | None:
        cfg = self.config
        factory = self.factory
        if not cfg.evaluation_enabled:
            return None
        if correction is None:
            self.reporter.warn("WARNING: Evaluation requires correction; disabling evaluation.")
            return None
        if cfg.evaluate_provider == "ollama":
            client = ollama_client or self._build_ollama_client()
            return factory.build_native_ollama_evaluator(
                cfg.effective_evaluate_model,
                client,
                cfg.evaluate_prompt,
                float(cfg.ocr_timeout),
                cfg.evaluate_temperature,
            )
        llm = factory.build_llm(
            cfg.evaluate_provider,
            cfg.effective_evaluate_model,
            cfg.ollama_url,
            cfg.evaluate_temperature,
        )
        return factory.build_evaluator(llm, cfg.evaluate_prompt)

    def build_doc_creator(self) -> DocumentCreator | None:
        if not self.config.docx_enabled:
            return None
        return self.factory.build_doc_creator(self.config.docx_format)
