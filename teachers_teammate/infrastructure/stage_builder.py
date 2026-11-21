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
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from .ocr_formats import OcrOutputPostprocessor

from ..config import Config, OcrConfig
from ..domain.context_budget import (
    BASE_NUM_CTX,
    RunWindows,
    stage_windows,
)
from ..domain.correction_policy import cut_off_tries
from ..domain.data_location import (
    ENGINE_PPOCR,
    DataLocation,
    llm_stage_location,
    online_ocr_reason,
)
from ..domain.model_catalog import HardwareDetection, RequestProfile
from ..exceptions import OnlineOcrNotAllowedError, ProviderNotAvailableError
from ..interfaces import (
    Anonymizer,
    Corrector,
    DocumentCreator,
    Evaluator,
    ImagePreprocessor,
    OCRProcessor,
)
from .anonymizer import (
    AnonymizerConfig,
    anonymizer_config_from,
    resolve_anonymizer_config,
)
from .docx_builder import DocxDocumentCreator
from .evaluation import LangChainEvaluator, NativeOllamaEvaluator
from .llm_factory import build_llm, get_provider_info
from .ocr_processor import OllamaOCRProcessor
from .ollama_utils import OllamaClient, ollama_server
from .onnx.store import ModelStore
from .ppocr.engine import PpOcrProcessor
from .preprocessing.recipes import steps_for
from .preprocessing.steps import StepPipeline
from .proofreading.chunking import ChunkingCorrector
from .proofreading.corrector import ProofreadingSettings, build_exchange_corrector
from .proofreading.port import LangChainPort, ModelPort, OllamaPort
from .reporting import Reporter, StdoutReporter
from .spacy_models import installed_spacy_models
from .system_locale import system_language
from .workflow.preprocess_service import PreprocessService

# ── Default constructors ───────────────────────────────────────────────────


def default_build_preprocessor(
    tmp_dir: Path, save_steps: bool, ocr: OcrConfig, store: ModelStore | None = None
) -> ImagePreprocessor:
    """Build the preprocessor for *ocr*'s settings; shared by real runs and the GUI preview.

    *store* is where the page models of the automatic preparation are (``None`` = the default
    folder).
    """
    return StepPipeline(
        steps_for(ocr, store or ModelStore()), tmp_dir=tmp_dir, save_steps=save_steps
    )


def _default_build_ppocr_ocr(store: ModelStore, stop_event: threading.Event | None) -> OCRProcessor:
    return PpOcrProcessor(store, stop_event=stop_event)


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


def _default_build_corrector(
    port: ModelPort,
    settings: ProofreadingSettings,
    profile: RequestProfile,
    tries: int,
) -> Corrector:
    return build_exchange_corrector(port, profile=profile, settings=settings, cut_off_tries=tries)


def _default_build_evaluator(llm: BaseChatModel, prompt: str) -> Evaluator:
    return LangChainEvaluator(llm, prompt)


def _default_build_native_ollama_evaluator(
    model: str,
    client: OllamaClient,
    prompt: str,
    timeout: float,
    temperature: float,
    num_ctx: int,
    think: bool | str = False,
) -> Evaluator:
    return NativeOllamaEvaluator(
        model=model,
        client=client,
        prompt=prompt,
        timeout=timeout,
        temperature=temperature,
        num_ctx=num_ctx,
        think=think,
    )


def _default_build_doc_creator(fmt: str, labels_language: str = "") -> DocumentCreator:
    return DocxDocumentCreator(fmt=fmt, labels_language=labels_language)


def _default_build_anonymizer(language: str, config: AnonymizerConfig) -> Anonymizer:
    from .anonymizer import SpacyAnonymizer  # noqa: PLC0415

    return SpacyAnonymizer(language, config)


def _default_check_ocr_model(url: str, model: str, reporter: Reporter) -> None:
    """Ask the Ollama server at *url* whether *model* is there; raise when it is not."""
    OllamaClient(url).check_model(model, reporter)


def _default_build_ocr_postprocessor() -> OcrOutputPostprocessor:
    from .ocr_formats import OcrOutputPostprocessor  # noqa: PLC0415

    return OcrOutputPostprocessor()


def _chunked(corrector: Corrector, language: str) -> Corrector:
    """Wrap *corrector* so a text longer than one request is proofread in pieces.

    Only a corrector with a bounded request window is wrapped; an online provider reports
    ``None`` and keeps handling any length in one request.  The wrapper asks for the budget
    again on every call, because it depends on the text's notation.
    """
    if corrector.max_text_chars(language) is None:
        return corrector
    return ChunkingCorrector(corrector)


def resolve_run_windows(config: Config, hardware: HardwareDetection | None) -> RunWindows:
    """Return the context windows of a run, resolved once from the settings and the computer.

    The window is a property of the computer and the models, not of the file, so it is
    resolved once and passed down: a window that changed per file would make Ollama reload the
    model for every file.  Each stage is sized from its own provider and model; an online
    provider brings its own window, which the app does not set.  *hardware* is ``None`` when
    the computer is not known, which keeps the base window.  The rules live in
    :func:`~teachers_teammate.domain.context_budget.stage_windows`.
    """
    return stage_windows(
        config.proofreading_context,
        proofreading_model=config.effective_correction_model,
        evaluation_model=config.effective_evaluate_model,
        proofreading_local=config.correction_provider == "ollama",
        evaluation_local=config.evaluate_provider == "ollama",
        evaluation_runs=config.evaluation_enabled,
        card_gb=hardware.card_gb if hardware is not None else None,
    )


def window_of_model(config: Config, windows: RunWindows, tag: str) -> int:
    """Return the largest window a run gives the Ollama model *tag*.

    A model that only reads scans keeps the base window: OCR requests do not set one.
    """
    sizes = [BASE_NUM_CTX]
    if (
        config.correction_enabled
        and config.correction_provider == "ollama"
        and config.effective_correction_model == tag
    ):
        sizes.append(windows.proofreading)
    if (
        config.evaluation_enabled
        and config.evaluate_provider == "ollama"
        and config.effective_evaluate_model == tag
    ):
        sizes.append(windows.evaluation)
    return max(sizes)


# ── Factory ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PipelineComponentFactory:
    """Callable set for pipeline stage construction.

    Every field is a constructor callable with a fixed signature.
    Swap any callable in tests or adapters to inject alternative
    implementations without subclassing.
    """

    build_preprocessor: Callable[[Path, bool, OcrConfig, ModelStore | None], ImagePreprocessor] = (
        default_build_preprocessor
    )
    build_ppocr_ocr: Callable[[ModelStore, threading.Event | None], OCRProcessor] = (
        _default_build_ppocr_ocr
    )
    build_ollama_ocr: Callable[[str, OllamaClient, int, float], OCRProcessor] = (
        _default_build_ollama_ocr
    )
    build_llm: Callable[[str, str, str, float], BaseChatModel] = _default_build_llm
    build_corrector: Callable[[ModelPort, ProofreadingSettings, RequestProfile, int], Corrector] = (
        _default_build_corrector
    )
    build_evaluator: Callable[[BaseChatModel, str], Evaluator] = _default_build_evaluator
    build_native_ollama_evaluator: Callable[
        [str, OllamaClient, str, float, float, int, bool | str], Evaluator
    ] = _default_build_native_ollama_evaluator
    build_doc_creator: Callable[[str, str], DocumentCreator] = _default_build_doc_creator
    build_anonymizer: Callable[[str, AnonymizerConfig], Anonymizer] = _default_build_anonymizer
    build_ocr_postprocessor: Callable[[], OcrOutputPostprocessor] = _default_build_ocr_postprocessor
    #: The check a run makes before it reads pages with Ollama: is the server there, and is the
    #: model?  A seam, so a test can answer without a server.
    check_ocr_model: Callable[[str, str, Reporter], None] = _default_check_ocr_model


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
    #: The hardware of the computer that runs Ollama, resolved by the application layer.  Only
    #: needed to size the context window of the local models; ``None`` means "not known" and
    #: keeps the base window.
    hardware: HardwareDetection | None = None
    #: Where the built-in reader's model files are looked for.  The application layer passes
    #: the store its readiness check uses, so a run reads the folder that was checked;
    #: ``None`` means the default folder.
    model_store: ModelStore | None = None

    def _build_ollama_client(self) -> OllamaClient:
        """Create the shared OllamaClient for all Ollama-backed stages."""
        return OllamaClient(self.config.ollama_url)

    def resolved_windows(self) -> RunWindows:
        """Return the context windows of this run (see :func:`resolve_run_windows`)."""
        return resolve_run_windows(self.config, self.hardware)

    def build_preprocessor(self, output_dir: Path) -> ImagePreprocessor:
        return self.factory.build_preprocessor(
            output_dir, self.config.debug, self.config.ocr, self.model_store
        )

    def build_preprocessor_service(self, output_dir: Path) -> PreprocessService:
        """Build the input-loading/preprocessing service."""
        from .input_provider_factory import InputOptions, get_input_provider  # noqa: PLC0415

        options = InputOptions.from_ocr(self.config.ocr)
        return PreprocessService(
            tmp_dir=output_dir,
            preprocessor=self.build_preprocessor(output_dir),
            provider_factory=lambda suffix, tmp_dir: get_input_provider(
                suffix, tmp_dir=tmp_dir, options=options
            ),
        )

    def build_ocr(
        self,
        ollama_client: OllamaClient | None = None,
        stop_event: threading.Event | None = None,
    ) -> OCRProcessor:
        """Build the OCR processor; refuse settings that would read pages online.

        Raises:
            OnlineOcrNotAllowedError: the engine or model would receive the page images
                on a computer the user does not control.
        """
        cfg = self.config
        ocr = cfg.ocr
        factory = self.factory
        server = ollama_server(cfg.ollama_url)
        reason = online_ocr_reason(
            ocr.engine,
            ocr.model,
            server.host,
            url_is_local=server.is_this_computer,
            host_is_private=server.is_private,
        )
        # With recognition off no page is read, so a model that would read pages online is moot.
        if reason and cfg.recognition_enabled:
            raise OnlineOcrNotAllowedError(reason)
        if ocr.engine == ENGINE_PPOCR:
            return factory.build_ppocr_ocr(self.model_store or ModelStore(), stop_event)
        client = ollama_client or self._build_ollama_client()
        return factory.build_ollama_ocr(ocr.model, client, cfg.ocr_timeout, ocr.temperature)

    def build_corrector(self, ollama_client: OllamaClient | None = None) -> Corrector | None:
        """Build the corrector of this run, or ``None`` when proofreading is off.

        The one place that decides which corrector a run uses, so a run and the pieces
        estimate of the GUI build the same one.  Building is cheap: neither the Ollama client
        nor the corrector contacts the server, and no anonymizer is built here.

        The corrector is the exchange over the connection of the chosen provider: a local
        server is asked through its own API (which knows the window), an online provider
        through its LangChain model.  Only a connection with a bounded window is wrapped in
        the chunking corrector, so an online provider keeps handling any length in one request.
        """
        cfg = self.config
        factory = self.factory
        if not cfg.correction_enabled:
            return None
        profile = cfg.correction_profile
        settings = self._correction_settings()
        # Step ① is the step whose answer is as long as the text, so its temperature decides
        # how often a cut-off answer is asked again.
        tries = cut_off_tries(online=self._correction_is_online(), temperature=profile.temperature)
        if cfg.correction_provider == "ollama":
            client = ollama_client or self._build_ollama_client()
            port: ModelPort = OllamaPort(
                client,
                cfg.effective_correction_model,
                timeout=float(cfg.ocr_timeout),
                num_ctx=self.resolved_windows().proofreading,
            )
        else:
            port = LangChainPort(
                factory.build_llm(
                    cfg.correction_provider,
                    cfg.effective_correction_model,
                    cfg.ollama_url,
                    cfg.correction_temperature,
                ),
                strict=cfg.correction_provider == "openai",
            )
        return _chunked(factory.build_corrector(port, settings, profile, tries), cfg.language)

    def build_correction_and_anonymizer(
        self, ollama_client: OllamaClient | None = None
    ) -> tuple[Corrector | None, Anonymizer | None]:
        """Build the corrector and the anonymizer, each only when it is enabled.

        Anonymization does not depend on proofreading: with "Remove personal info" ticked the
        anonymizer is built even when nothing is sent anywhere, so the run can fill the
        session cache and the anonymized text file has something to write.
        """
        cfg = self.config
        correction = self.build_corrector(ollama_client)
        anonymizer: Anonymizer | None = None

        if cfg.anonymization_enabled:
            try:
                anonymizer = self._build_anonymizer()
            except ProviderNotAvailableError as exc:
                if cfg.correction_enabled:
                    raise  # text would be sent unprotected: the run must not start
                # Nothing is sent anywhere, so the missing add-on only costs the preview cache.
                self.reporter.warn(f"WARNING: {exc}; personal info is not removed in this run.")

        return correction, anonymizer

    def _correction_settings(self) -> ProofreadingSettings:
        """Collect what the teacher set for the proofreading of this run."""
        cfg = self.config
        return ProofreadingSettings(
            level=cfg.correction_level,
            explanation_language=cfg.explanation_language,
            teacher_notes=cfg.correction_notes,
            corrections_only=cfg.correction_output == "text",
        )

    def _correction_is_online(self) -> bool:
        """Return whether proofreading is processed by an online service.

        The same rule the privacy badge uses (:func:`llm_stage_location`), so a self-hosted
        provider is shown as local and also gets the local number of tries.  An Ollama server on
        the internet is online: it is not one of the teacher's own computers.
        """
        cfg = self.config
        server = ollama_server(cfg.ollama_url)
        where = llm_stage_location(
            self_hosted=bool(get_provider_info(cfg.correction_provider).get("self_hosted")),
            model=cfg.effective_correction_model,
            url_is_local=server.is_this_computer,
            host=server.host,
            host_is_private=server.is_private,
        )
        return where is DataLocation.ONLINE

    def _build_anonymizer(self) -> Anonymizer:
        cfg = self.config
        try:
            anon_config = resolve_anonymizer_config(
                anonymizer_config_from(cfg),
                document_language=cfg.language,
                system_language=system_language(),
                installed_models=installed_spacy_models(),
                warn=self.reporter.warn,
            )
            return self.factory.build_anonymizer(cfg.language, anon_config)
        except (ImportError, OSError) as exc:
            raise ProviderNotAvailableError(f"Cannot initialise anonymizer: {exc}") from exc

    def build_text_anonymizer(self, shared: Anonymizer | None = None) -> Anonymizer | None:
        """Return the anonymizer for anonymized text files, or ``None`` when they are off.

        Reuses *shared* (the anonymizer that protects the LLM stages) when there is one.
        """
        if not self.config.text_anonymized_enabled:
            return None
        return shared or self._build_anonymizer()

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
                self.resolved_windows().evaluation,
                cfg.evaluate_think,
            )
        llm = factory.build_llm(
            cfg.evaluate_provider,
            cfg.effective_evaluate_model,
            cfg.ollama_url,
            cfg.evaluate_temperature,
        )
        return factory.build_evaluator(llm, cfg.evaluate_prompt)

    def build_ocr_postprocessor(self) -> OcrOutputPostprocessor:
        """Build the notation detector/parser applied to every page of OCR output."""
        return self.factory.build_ocr_postprocessor()

    def build_doc_creator(self) -> DocumentCreator | None:
        if not self.config.docx_enabled:
            return None
        cfg = self.config
        return self.factory.build_doc_creator(
            cfg.docx_format, cfg.explanation_language or cfg.language
        )
