"""Abstract base classes defining the OCR pipeline component contracts.

Each component in the pipeline is defined by one of these interfaces.
Concrete implementations live in their own modules and are wired together in
:mod:`teachers_teammate.infrastructure.pipeline`.

Interfaces:

* :class:`InputProvider`    — loads a source file into raw page images
* :class:`ImagePreprocessor` — transforms raw images before OCR
* :class:`OCRProcessor`      — extracts text from a preprocessed image
* :class:`Corrector`         — proofreads extracted text
* :class:`Evaluator`         — assesses quality of corrected text
* :class:`DocumentCreator`   — writes the pipeline output to a file
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

# Re-export OCRError so callers can do `from .interfaces import OCRError`.
# The canonical source is teachers_teammate.exceptions.
# imported for downstream callers, not for local use.
from .exceptions import OCRError as OCRError  # noqa: PLC0414  # pylint: disable=unused-import

SUPPORTED_SUFFIXES: frozenset[str] = frozenset({".pdf", ".png", ".jpg", ".jpeg", ".txt"})


@runtime_checkable
class LLMProviderModule(Protocol):
    """Structural contract every module in ``infrastructure/providers/`` satisfies.

    Provider modules are discovered at runtime by :mod:`infrastructure.llm_factory`.
    This Protocol makes the implicit duck-typed contract explicit so that the
    type checker (``ty``) can verify call sites.

    Required members
    ----------------
    ``PROVIDER_INFO``
        Metadata dict (``needs_api_key``, ``env_key``, ``needs_base_url``, …).
    ``create(model, **kwargs) -> BaseChatModel``
        Build and return the provider-specific LLM client.

    Optional members (guarded by ``hasattr`` / ``PROVIDER_INFO`` capability flags)
    -------------------------------------------------------------------------------
    ``list_models(**kwargs) -> list[str]``
        Return available model names (live or static).
    ``check_connection(**kwargs) -> tuple[bool, str]``
        Return ``(ok, human-readable message)`` for a lightweight health check.
    """

    PROVIDER_INFO: dict

    def create(self, model: str, **kwargs: object) -> BaseChatModel: ...


class InputProvider(ABC):
    """Loads a source file and returns typed ingestion units."""

    @abstractmethod
    def load(self, file_path: Path) -> InputPayload:
        """Return typed ingestion payload for *file_path*.

        Args:
            file_path: Path to the source file (PDF, image, etc.).

        Returns:
            :class:`InputPayload` containing one or more :class:`InputUnit`
            values and an optional source image to embed in generated documents.
        """


InputUnitKind = Literal["image", "text"]


@dataclass(frozen=True)
class InputUnit:
    """A single ingestion unit produced by an :class:`InputProvider`."""

    kind: InputUnitKind
    image_path: Path | None = None
    text: str | None = None

    @staticmethod
    def image(path: Path) -> InputUnit:
        """Create an image unit from *path*."""
        return InputUnit(kind="image", image_path=path)

    @staticmethod
    def text_content(value: str) -> InputUnit:
        """Create a text unit from *value*."""
        return InputUnit(kind="text", text=value)


@dataclass(frozen=True)
class InputPayload:
    """Provider output: ingestion units plus optional source image for document preview."""

    units: list[InputUnit]
    source_image: Path | None = None


class ImagePreprocessor(ABC):
    """Applies image transformations to prepare a raw image for OCR."""

    @abstractmethod
    def preprocess(self, image_path: Path) -> tuple[Path, list[str]]:
        """Return ``(preprocessed_output_path, step_names_applied)``.

        Args:
            image_path: Path to the raw input image.

        Returns:
            A tuple of the path to the preprocessed image and the list of
            transformation step names that were applied, in order.
        """


class OCRProcessor(ABC):
    """Extracts text from a single preprocessed image."""

    @abstractmethod
    def process_image(self, image_path: Path, language: str = "English") -> str:
        """Extract text from *image_path*.

        Args:
            image_path: Path to the preprocessed image.
            language: Natural language of the document text.

        Returns:
            Extracted text.

        Raises:
            :exc:`~teachers_teammate.exceptions.OCRError`: on failure.
        """


class Corrector(ABC):
    """Proofreads and corrects extracted text.

    Implementations: :class:`~infrastructure.correction.LangChainCorrector`
    (provider-agnostic) and :class:`~infrastructure.correction.NativeOllamaCorrector`.
    To add a non-LangChain corrector, implement this ABC, add a constructor field
    to :class:`~infrastructure.stage_builder.PipelineComponentFactory`, and select it
    in :meth:`~infrastructure.stage_builder.StageBuilder.build_correction_and_anonymizer`.
    """

    @abstractmethod
    def correct(self, raw_text: str, language: str) -> tuple[str, str | None]:
        """Return ``(corrected_text, warning_or_none)``.

        On success, return ``(corrected_text, None)``.
        On recoverable failure (e.g. LLM error), return ``(raw_text, warning_message)``
        so the caller can surface the warning without failing the file.

        Args:
            raw_text: Text to proofread.
            language: Language of the text.

        Returns:
            Tuple of corrected text and an optional warning string.
        """


class Evaluator(ABC):
    """Assesses the quality of corrected OCR text.

    Implementations: :class:`~infrastructure.evaluation.LangChainEvaluator`
    (provider-agnostic) and :class:`~infrastructure.evaluation.NativeOllamaEvaluator`.
    To add a non-LangChain evaluator, implement this ABC, add a constructor field
    to :class:`~infrastructure.stage_builder.PipelineComponentFactory`, and select it
    in :meth:`~infrastructure.stage_builder.StageBuilder.build_evaluation`.
    """

    @abstractmethod
    def evaluate(self, corrected_text: str, language: str) -> tuple[str, str | None]:
        """Return ``(evaluation_report, warning_or_none)``.

        On success, return ``(report, None)``.
        On recoverable failure, return ``("", warning_message)``
        so the caller can surface the warning without failing the file.

        Args:
            corrected_text: The corrected text to assess.
            language: Language of the text.

        Returns:
            Tuple of quality report string (or empty) and an optional warning string.
        """


AnonymizationMap = dict[str, str]
"""Maps placeholder tokens (e.g. ``[PERSON_1]``) back to the original surface text."""


class Anonymizer(ABC):
    """Replaces PII in text with stable placeholders before sending to an LLM."""

    @abstractmethod
    def anonymize(self, text: str) -> tuple[str, AnonymizationMap]:
        """Return *(anonymized_text, mapping)*.

        Every PII span is replaced with a placeholder such as ``[PERSON_1]``.
        *mapping* records the inverse: ``placeholder → original text``.

        Args:
            text: Raw text that may contain PII.

        Returns:
            Tuple of the anonymized text and the restoration mapping.
        """

    @abstractmethod
    def restore(self, text: str, mapping: AnonymizationMap) -> str:
        """Substitute all placeholders in *text* back with their originals.

        Args:
            text: Text containing placeholder tokens.
            mapping: The mapping returned by :meth:`anonymize`.

        Returns:
            Text with placeholders replaced by the original PII values.
        """


class DocumentCreator(ABC):
    """Writes the pipeline output to a document file."""

    @abstractmethod
    def create(
        self,
        raw_text: str,
        corrected_text: str | None,
        out_path: Path,
        title: str,
        *,
        source_image: str | Path | None = None,
    ) -> None:
        """Write output document to *out_path*.

        Args:
            raw_text: Raw OCR output.
            corrected_text: Proofread text, or ``None`` when correction was skipped.
            out_path: Destination file path (parent directory must exist).
            title: Document heading.
            source_image: Optional path to the source image for visual preview.
        """
