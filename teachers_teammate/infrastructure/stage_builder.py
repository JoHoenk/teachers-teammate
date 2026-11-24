"""Stage construction for the OCR pipeline.

Separates the responsibility of building individual pipeline stages
from the orchestration logic in :mod:`infrastructure.pipeline`.

:class:`PipelineComponentFactory` holds the constructor callables used
to build each stage, each typed by a :class:`~typing.Protocol` whose parameters are
keyword-only.  Swap any callable to inject a test double or alternative implementation.

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
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from .ocr_formats import OcrOutputPostprocessor

from ..config import Config, OcrConfig
from ..domain import correction_policy
from ..domain.anonymization import AnonymizerConfig, resolve_anonymizer_config
from ..domain.context_budget import RunWindows
from ..domain.data_location import (
    ENGINE_PPOCR,
    PROVIDER_OLLAMA,
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
    Reporter,
)
from ..messages import message_of, personal_info_setup_failed
from ..run_rules import resolve_run_windows
from ..texts import report as report_text
from ..texts.addons import anonymizer_notice
from ..texts.dialogs import cli_warning
from ..texts.run import personal_info_not_removed
from .docx_builder import DocxDocumentCreator
from .evaluation import PortEvaluator
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
from .reporting import StdoutReporter
from .spacy_models import SpacyModels, installed_spacy_models
from .system_locale import system_language
from .workflow.preprocess_service import PreprocessService

# ── Default constructors ───────────────────────────────────────────────────


def default_build_preprocessor(
    *, tmp_dir: Path, save_steps: bool, ocr: OcrConfig, store: ModelStore
) -> ImagePreprocessor:
    """Build the preprocessor for *ocr*'s settings; shared by real runs and the GUI preview.

    *store* is where the page models of the automatic preparation are.
    """
    return StepPipeline(steps_for(ocr, store), tmp_dir=tmp_dir, save_steps=save_steps)


def _default_build_ppocr_ocr(
    *, store: ModelStore, stop_event: threading.Event | None
) -> OCRProcessor:
    return PpOcrProcessor(store, stop_event=stop_event)


def _default_build_ollama_ocr(
    *, model: str, client: OllamaClient, timeout: int, temperature: float
) -> OCRProcessor:
    return OllamaOCRProcessor(
        model_name=model, client=client, timeout=timeout, temperature=temperature
    )


def _default_build_llm(
    *, provider: str, model: str, base_url: str, temperature: float
) -> BaseChatModel:
    return build_llm(provider, model, base_url=base_url, temperature=temperature)


@dataclass(frozen=True)
class LlmBuilderWithKeys:
    """Builds the chat model of a provider with the key the app holds for it.

    The key comes in as ``key_for``, so this layer knows nothing of where keys are kept; the
    composition root hands over the one place they are.
    """

    key_for: Callable[[str], str | None]

    def __call__(
        self, *, provider: str, model: str, base_url: str, temperature: float
    ) -> BaseChatModel:
        """Return the chat model of *provider*, built with the key it is given."""
        return build_llm(
            provider,
            model,
            base_url=base_url,
            temperature=temperature,
            api_key=self.key_for(provider) or "",
        )


def _default_build_corrector(
    *,
    port: ModelPort,
    settings: ProofreadingSettings,
    profile: RequestProfile,
    cut_off_tries: int,
) -> Corrector:
    return build_exchange_corrector(
        port, profile=profile, settings=settings, cut_off_tries=cut_off_tries
    )


def _default_build_evaluator(
    *, port: ModelPort, prompt: str, temperature: float, think: bool | str = False
) -> Evaluator:
    return PortEvaluator(port, prompt, temperature=temperature, think=think)


def _default_build_doc_creator(*, fmt: str, labels_language: str = "") -> DocumentCreator:
    return DocxDocumentCreator(fmt=fmt, labels_language=labels_language)


def _default_build_anonymizer(*, language: str, config: AnonymizerConfig) -> Anonymizer:
    return SpacyModels().anonymizer(language, config)


def _default_check_ocr_model(*, url: str, model: str, reporter: Reporter) -> None:
    """Ask the Ollama server at *url* whether *model* is there; raise when it is not."""
    OllamaClient(url).check_model(model, reporter)


def _default_build_ocr_postprocessor() -> OcrOutputPostprocessor:
    from .ocr_formats import OcrOutputPostprocessor  # noqa: PLC0415

    return OcrOutputPostprocessor()


def _is_self_hosted(provider: str) -> bool:
    """Return whether *provider* runs on a server the teacher names, not at a vendor."""
    info = get_provider_info(provider)
    return info is not None and info.self_hosted


def _strict_json_schema(provider: str) -> bool:
    """Return whether *provider* answers the review schema only in its strict mode."""
    info = get_provider_info(provider)
    return info is not None and info.strict_json_schema


def _chunked(corrector: Corrector, language: str) -> Corrector:
    """Wrap *corrector* so a text longer than one request is proofread in pieces.

    Only a corrector with a bounded request window is wrapped; an online provider reports
    ``None`` and keeps handling any length in one request.  The wrapper asks for the budget
    again on every call, because it depends on the text's notation.
    """
    if corrector.max_text_chars(language) is None:
        return corrector
    return ChunkingCorrector(corrector)


# ── Factory ────────────────────────────────────────────────────────────────


class PreprocessorBuilder(Protocol):
    """Builds the image preprocessor of a run."""

    def __call__(
        self, *, tmp_dir: Path, save_steps: bool, ocr: OcrConfig, store: ModelStore
    ) -> ImagePreprocessor:
        """Return the preprocessor for *ocr*'s settings."""
        ...


class ReaderBuilder(Protocol):
    """Builds the built-in reader."""

    def __call__(self, *, store: ModelStore, stop_event: threading.Event | None) -> OCRProcessor:
        """Return the reader that reads pages with *store*'s models."""
        ...


class OllamaOcrBuilder(Protocol):
    """Builds the OCR processor that reads pages with an Ollama vision model."""

    def __call__(
        self, *, model: str, client: OllamaClient, timeout: int, temperature: float
    ) -> OCRProcessor:
        """Return the processor for *model* on *client*."""
        ...


class LlmBuilder(Protocol):
    """Builds the chat model of an online provider."""

    def __call__(
        self, *, provider: str, model: str, base_url: str, temperature: float
    ) -> BaseChatModel:
        """Return the chat model of *provider*."""
        ...


class CorrectorBuilder(Protocol):
    """Builds the corrector of an exchange on a connection."""

    def __call__(
        self,
        *,
        port: ModelPort,
        settings: ProofreadingSettings,
        profile: RequestProfile,
        cut_off_tries: int,
    ) -> Corrector:
        """Return the corrector of *profile*'s exchange on *port*."""
        ...


class EvaluatorBuilder(Protocol):
    """Builds the content review over a connection to a model."""

    def __call__(
        self,
        *,
        port: ModelPort,
        prompt: str,
        temperature: float,
        think: bool | str = False,
    ) -> Evaluator:
        """Return the evaluator that asks *port* with *prompt*."""
        ...


class DocCreatorBuilder(Protocol):
    """Builds the Word document writer."""

    def __call__(self, *, fmt: str, labels_language: str = "") -> DocumentCreator:
        """Return the writer for *fmt*."""
        ...


class AnonymizerBuilder(Protocol):
    """Builds the anonymizer of a language."""

    def __call__(self, *, language: str, config: AnonymizerConfig) -> Anonymizer:
        """Return the anonymizer for *language*."""
        ...


class OcrPostprocessorBuilder(Protocol):
    """Builds the notation detector applied to every page of OCR output."""

    def __call__(self) -> OcrOutputPostprocessor:
        """Return the postprocessor."""
        ...


class OcrModelCheck(Protocol):
    """Asks the Ollama server whether the model a run reads pages with is there."""

    def __call__(self, *, url: str, model: str, reporter: Reporter) -> None:
        """Raise when *model* is not on the server at *url*."""
        ...


@dataclass(frozen=True)
class PipelineComponentFactory:
    """Callable set for pipeline stage construction.

    Every field is a constructor callable with a fixed signature, and every parameter of it is
    keyword-only, so two values of the same type cannot be swapped unnoticed.  Swap any callable
    in tests or adapters to inject alternative implementations without subclassing.
    """

    build_preprocessor: PreprocessorBuilder = default_build_preprocessor
    build_ppocr_ocr: ReaderBuilder = _default_build_ppocr_ocr
    build_ollama_ocr: OllamaOcrBuilder = _default_build_ollama_ocr
    build_llm: LlmBuilder = _default_build_llm
    build_corrector: CorrectorBuilder = _default_build_corrector
    build_evaluator: EvaluatorBuilder = _default_build_evaluator
    build_doc_creator: DocCreatorBuilder = _default_build_doc_creator
    build_anonymizer: AnonymizerBuilder = _default_build_anonymizer
    build_ocr_postprocessor: OcrPostprocessorBuilder = _default_build_ocr_postprocessor
    #: The check a run makes before it reads pages with Ollama: is the server there, and is the
    #: model?  A seam, so a test can answer without a server.
    check_ocr_model: OcrModelCheck = _default_check_ocr_model


# ── Builder ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StageBuilder:
    """Builds concrete pipeline stage instances from a Config and a factory.

    Separates stage-construction logic from pipeline orchestration so that
    each stage can be built and tested independently.
    """

    config: Config
    factory: PipelineComponentFactory
    #: Where the built-in reader's model files are looked for.  The application layer passes
    #: the store its readiness check uses, so a run reads the folder that was checked.
    model_store: ModelStore
    reporter: Reporter = field(default_factory=StdoutReporter)
    #: The hardware of the computer that runs Ollama, resolved by the application layer.  Only
    #: needed to size the context window of the local models; ``None`` means "not known" and
    #: keeps the base window.
    hardware: HardwareDetection | None = None

    def _build_ollama_client(self) -> OllamaClient:
        """Create the shared OllamaClient for all Ollama-backed stages."""
        return OllamaClient(self.config.ollama_url)

    def _model_port(
        self,
        provider: str,
        model: str,
        *,
        num_ctx: int,
        temperature: float,
        ollama_client: OllamaClient | None = None,
    ) -> ModelPort:
        """Return the connection to *model* on *provider*: the server's own API, or LangChain.

        One place builds it, so a proofreading request and a content review are sent the same way
        on the same provider.  Building contacts no server: the Ollama client is a thin wrapper
        and the LangChain model is built from the settings.

        Args:
            num_ctx: The window a local model is asked for; a LangChain provider brings its own.
            temperature: The temperature of the requests *this* stage sends; a provider builds
                its model with it, so a stage with its own temperature needs its own port.
        """
        if provider == PROVIDER_OLLAMA:
            client = ollama_client or self._build_ollama_client()
            return OllamaPort(
                client,
                model,
                timeout=float(self.config.ocr_timeout),
                num_ctx=num_ctx,
            )
        return LangChainPort(
            self.factory.build_llm(
                provider=provider,
                model=model,
                base_url=self.config.ollama_url,
                temperature=temperature,
            ),
            strict=_strict_json_schema(provider),
        )

    def resolved_windows(self) -> RunWindows:
        """Return the context windows of this run (see :func:`~teachers_teammate.run_rules.resolve_run_windows`)."""
        return resolve_run_windows(self.config, self.hardware)

    def build_preprocessor(self, output_dir: Path) -> ImagePreprocessor:
        return self.factory.build_preprocessor(
            tmp_dir=output_dir,
            save_steps=self.config.debug,
            ocr=self.config.ocr,
            store=self.model_store,
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
            return factory.build_ppocr_ocr(store=self.model_store, stop_event=stop_event)
        client = ollama_client or self._build_ollama_client()
        return factory.build_ollama_ocr(
            model=ocr.model, client=client, timeout=cfg.ocr_timeout, temperature=ocr.temperature
        )

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
        tries = correction_policy.cut_off_tries(
            online=self._correction_is_online(), temperature=profile.temperature
        )
        port = self._model_port(
            cfg.correction_provider,
            cfg.effective_correction_model,
            num_ctx=self.resolved_windows().proofreading,
            temperature=cfg.correction_temperature,
            ollama_client=ollama_client,
        )
        return _chunked(
            factory.build_corrector(
                port=port, settings=settings, profile=profile, cut_off_tries=tries
            ),
            cfg.language,
        )

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
                self.reporter.warn(cli_warning(personal_info_not_removed(exc)))

        return correction, anonymizer

    def _correction_settings(self) -> ProofreadingSettings:
        """Collect what the teacher set for the proofreading of this run."""
        cfg = self.config
        return ProofreadingSettings(
            level=cfg.correction_level,
            explanation_language=cfg.explanation_language,
            teacher_notes=cfg.correction_notes,
            corrections_only=cfg.correction_output == correction_policy.CorrectionOutput.TEXT,
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
            self_hosted=_is_self_hosted(cfg.correction_provider),
            model=cfg.effective_correction_model,
            url_is_local=server.is_this_computer,
            host=server.host,
            host_is_private=server.is_private,
        )
        return where is DataLocation.ONLINE

    def _build_anonymizer(self) -> Anonymizer:
        cfg = self.config
        try:
            anon_config, notice = resolve_anonymizer_config(
                cfg.anonymizer_config,
                document_language=cfg.language,
                system_language=system_language(),
                installed_models=installed_spacy_models(),
            )
            if notice is not None:
                self.reporter.warn(anonymizer_notice(notice))
            return self.factory.build_anonymizer(language=cfg.language, config=anon_config)
        except (ImportError, OSError) as exc:
            raise ProviderNotAvailableError(personal_info_setup_failed(message_of(exc))) from exc

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
        if not cfg.evaluation_enabled:
            return None
        if correction is None:
            self.reporter.warn(report_text.EVALUATION_NEEDS_CORRECTION)
            return None
        return self.factory.build_evaluator(
            port=self._model_port(
                cfg.evaluate_provider,
                cfg.effective_evaluate_model,
                num_ctx=self.resolved_windows().evaluation,
                temperature=cfg.evaluate_temperature,
                ollama_client=ollama_client,
            ),
            prompt=cfg.evaluate_prompt,
            temperature=cfg.evaluate_temperature,
            think=cfg.evaluate_think,
        )

    def build_ocr_postprocessor(self) -> OcrOutputPostprocessor:
        """Build the notation detector/parser applied to every page of OCR output."""
        return self.factory.build_ocr_postprocessor()

    def build_doc_creator(self) -> DocumentCreator | None:
        if not self.config.docx_enabled:
            return None
        cfg = self.config
        return self.factory.build_doc_creator(
            fmt=cfg.docx_format, labels_language=cfg.explanation_language or cfg.language
        )
