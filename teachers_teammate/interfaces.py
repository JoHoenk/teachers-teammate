"""Abstract base classes defining the OCR pipeline component contracts.

Each component in the pipeline is defined by one of these interfaces.
Concrete implementations live in their own modules and are wired together in
:mod:`teachers_teammate.infrastructure.pipeline`.

Interfaces:

* :class:`InputProvider`    — loads a source file into raw page images
* :class:`ImagePreprocessor` — transforms raw images before OCR
* :class:`OCRProcessor`      — extracts text from a preprocessed image
* :class:`OcrOutputParser`   — turns one engine's notation into a :class:`FormattedDocument`
* :class:`TextRenderer`      — renders a :class:`FormattedDocument` as text
* :class:`DocumentFilter`    — drops or rewrites blocks of a parsed document
* :class:`Corrector`         — proofreads extracted text
* :class:`Evaluator`         — assesses quality of corrected text
* :class:`DocumentCreator`   — writes the pipeline output to a file
* :class:`SleepBlocker`      — keeps the computer from going to sleep while work runs
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Literal, NamedTuple, Protocol, runtime_checkable

from .domain.evaluation import EvaluationOutcome
from .domain.formatted_text import FormattedDocument, TextFormat
from .domain.ollama_discovery import WaitDetail
from .domain.proofreading.result import CorrectionOutcome
from .domain.proofreading.review import CorrectionReview
from .domain.providers import ProviderInfo
from .domain.run_summary import FileOutcome, KeepAwakeState

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

#: What reads each input suffix: ``"pdf"``, ``"image"`` or ``"text"``.
INPUT_KINDS: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".txt": "text",
}

#: Every input suffix the app accepts, and so every one a provider is built for.
SUPPORTED_SUFFIXES: frozenset[str] = frozenset(INPUT_KINDS)


@runtime_checkable
class LLMProviderModule(Protocol):
    """Structural contract every module in ``infrastructure/providers/`` satisfies.

    Provider modules are discovered at runtime by :mod:`infrastructure.llm_factory`.
    This Protocol makes the implicit duck-typed contract explicit so that the
    type checker (``ty``) can verify call sites.

    Required members
    ----------------
    ``PROVIDER``
        What the provider is and needs (:class:`~teachers_teammate.domain.providers.ProviderInfo`).
    ``create(model, **kwargs) -> BaseChatModel``
        Build and return the provider-specific LLM client.

    Optional members (guarded by ``hasattr`` / ``PROVIDER`` capability flags)
    ---------------------------------------------------------------------------
    ``list_models(**kwargs) -> list[str]``
        Return available model names (live or static).
    ``check_connection(**kwargs) -> tuple[bool, str]``
        Return ``(ok, human-readable message)`` for a lightweight health check.
    """

    PROVIDER: ProviderInfo

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


@dataclass(frozen=True)
class ImageUnit:
    """One page of a scan, as the file the provider rendered or found."""

    path: Path


@dataclass(frozen=True)
class TextUnit:
    """Text that replaces text recognition: a text file, or a PDF's embedded text."""

    text: str


#: What an :class:`InputProvider` reads a document into.
InputUnit = ImageUnit | TextUnit


TextOrigin = Literal["file", "pdf_text_layer"]
"""Where a payload's text units come from: a plain text file or a PDF's embedded text."""


@dataclass(frozen=True)
class InputPayload:
    """Provider output: ingestion units plus optional source image for document preview."""

    units: tuple[InputUnit, ...]
    source_image: Path | None = None
    text_origin: TextOrigin | None = None
    """Set when the units hold text that replaces OCR; ``None`` for image payloads."""
    text_layer_note: str | None = None
    """For a PDF that fell back to OCR: why its text layer was not used (for the status log)."""
    page_images: tuple[Path, ...] = ()
    """Every page rendered for the scan, in page order, when the units are not images.

    A PDF whose text layer is used has no image units, but the teacher still wants to see its
    pages next to the text.  Empty for a payload whose units are the pages themselves.
    """


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


class OcrOutputParser(ABC):
    """Parses one OCR engine notation (HTML, Markdown, plain text, …) into the document model.

    Parsers are tried in order by
    :class:`~teachers_teammate.infrastructure.ocr_formats.OcrOutputPostprocessor`; the first
    whose :meth:`can_parse` accepts the text wins.  To support a new notation, implement this
    ABC in ``infrastructure/ocr_formats/`` and add the class to ``default_parsers()``.
    """

    name: ClassVar[str]
    """Short identifier, e.g. ``"chandra_html"``; recorded for diagnostics."""

    @abstractmethod
    def can_parse(self, text: str) -> bool:
        """Return ``True`` when *text* is written in this parser's notation."""

    @abstractmethod
    def parse(self, text: str) -> FormattedDocument:
        """Return the :class:`FormattedDocument` described by *text*."""


class TextRenderer(ABC):
    """Renders a :class:`FormattedDocument` to a string notation."""

    @abstractmethod
    def render(self, doc: FormattedDocument) -> str:
        """Return *doc* as text."""


class DocumentFilter(ABC):
    """Rewrites a parsed document, e.g. dropping page headers and footers."""

    @abstractmethod
    def apply(self, doc: FormattedDocument) -> FormattedDocument:
        """Return the filtered document."""


class Corrector(ABC):
    """Proofreads and corrects extracted text.

    The app's implementation is
    :class:`~infrastructure.proofreading.corrector.ExchangeCorrector`, which runs an exchange
    over a model port; :class:`~infrastructure.proofreading.chunking.ChunkingCorrector` wraps it
    so a long text goes out in pieces.  To add another corrector, implement this ABC, add a
    constructor field to :class:`~infrastructure.stage_builder.PipelineComponentFactory`, and
    select it in :meth:`~infrastructure.stage_builder.StageBuilder.build_corrector`.
    """

    @abstractmethod
    def correct(
        self, raw_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> CorrectionOutcome:
        """Return the proofreading result: corrected text, review and warning.

        On success, :attr:`CorrectionOutcome.review` holds the per-change explanations and
        the unclear expressions; it is ``None`` in corrections-only mode.  A partial answer — a
        corrections-only fallback, a piece whose explanations are missing — is a result whose
        :attr:`CorrectionOutcome.warning` says so.  An answer that cannot be used is an outcome
        with :attr:`CorrectionOutcome.failed` set: the unchanged text and the reason.

        Raises:
            :exc:`~teachers_teammate.exceptions.StageError`: when a request never reached the
                model, so no proofread text exists.  The caller ends the file.

        Args:
            raw_text: Text to proofread.
            language: Language of the text.
            text_format: Notation of *raw_text*.  ``"markdown"`` text uses ``~~crossed out~~``
                and ``<sup>inserted</sup>`` marks that the implementation should explain
                to the model and keep intact.

        Returns:
            A :class:`~.domain.proofreading.result.CorrectionOutcome`.
        """

    def max_text_chars(
        self,
        language: str,  # noqa: ARG002  # interface parameter; unbounded by default
        *,
        text_format: TextFormat = "plain",  # noqa: ARG002  # interface parameter
    ) -> int | None:
        """Return how many characters one request can carry, or ``None`` when unbounded.

        Callers wrap a bounded corrector in a chunking decorator that proofreads a long text
        in pieces.  ``None`` means the provider's window is large enough for any text
        (online providers), so no splitting happens.

        Args:
            language: Language of the text.
            text_format: Notation of the text.

        Returns:
            The character budget of one request, or ``None``.
        """
        return None

    def pieces(
        self,
        text: str,  # noqa: ARG002  # interface parameter; one request by default
        language: str,  # noqa: ARG002  # interface parameter
        *,
        text_format: TextFormat = "plain",  # noqa: ARG002  # interface parameter
    ) -> int | None:
        """Return how many requests proofreading *text* takes, or ``None`` when unbounded.

        The one place that counts, so the console line and the run cannot disagree.  ``None`` means the corrector sends any length in one request (an online
        provider); a corrector that splits a long text answers with the number of non-blank
        pieces, because a whitespace-only piece is not sent.

        Args:
            text: The text to proofread.
            language: Language of the text.
            text_format: Notation of the text.

        Returns:
            The number of requests, or ``None`` when the text goes out whole whatever its length.
        """
        return None


class Evaluator(ABC):
    """Assesses the quality of corrected OCR text.

    Implementation: :class:`~infrastructure.evaluation.PortEvaluator`, which asks over the
    run's connection to a model.  To review in another way, implement this ABC and swap the
    :attr:`~infrastructure.stage_builder.PipelineComponentFactory.build_evaluator` field.
    """

    @abstractmethod
    def evaluate(
        self, corrected_text: str, language: str, *, text_format: TextFormat = "plain"
    ) -> EvaluationOutcome:
        """Return the review, with a warning when it could not be written or cover the whole text.

        A review that failed has no text and a warning: the file is saved all the same.

        Args:
            corrected_text: The corrected text to assess.
            language: Language of the text.
            text_format: Notation of *corrected_text* (see :meth:`Corrector.correct`).

        Returns:
            An :class:`~teachers_teammate.domain.evaluation.EvaluationOutcome`.
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
        text_format: TextFormat = "plain",
        review: CorrectionReview | None = None,
    ) -> None:
        """Write output document to *out_path*.

        Args:
            raw_text: Raw OCR output.
            corrected_text: Proofread text, or ``None`` when correction was skipped.
            out_path: Destination file path (parent directory must exist).
            title: Document heading.
            source_image: Optional path to the source image for visual preview.
            text_format: Notation of *raw_text* and *corrected_text*; ``"markdown"`` text
                should be written with its formatting (bold, strikethrough, ...).
            review: Explanations and unclear expressions of the proofreading, or ``None``
                when the file was corrected without a review.
        """


class SleepBlocker(ABC):
    """Keeps the computer from going to sleep on its own while a long job runs."""

    @property
    def available(self) -> bool:
        """Whether this computer has a way to ask for it at all (``False``: nothing is requested)."""
        return True

    @abstractmethod
    def hold(self, reason: str) -> AbstractContextManager[bool]:
        """Block idle sleep until the ``with`` block ends.

        Args:
            reason: Plain-language reason, shown by the system's power tools.

        Returns:
            A context manager that yields whether the system agreed.  It never raises: a
            computer that cannot be kept awake yields ``False`` and the job runs anyway.  Each
            call makes its own request, so overlapping holds need no counting.
        """


# ── The run's narrative: what the pipeline reports, and to whom ─────────────

OnFileStartedFn = Callable[[str, str, int, int], None]  # (source_id, name, idx, total)
OnStageStartedFn = Callable[[str, str], None]  # (source_id, stage)
OnOcrDoneFn = Callable[[str, str], None]  # (source_id, name)
OnStageDetailFn = Callable[[str, WaitDetail], None]  # (source_id, what the server is doing)
OnFirstTokenFn = Callable[[str], None]  # (source_id)
OnFileDoneFn = Callable[[FileOutcome], None]


@runtime_checkable
class Reporter(Protocol):
    """Sink for the pipeline's progress narrative, its warnings and its errors."""

    def status(self, line: str) -> None:
        """Report a normal progress line."""

    def warn(self, line: str) -> None:
        """Report an advisory/warning line."""

    def error(self, line: str) -> None:
        """Report a line about something that went wrong."""


class CallbackReporter:
    """Routes every line (status and warning alike) to a single callback.

    Used by the GUI worker to feed both progress and warnings into the log pane.
    """

    def __init__(self, sink: Callable[[str], None]) -> None:
        self._sink = sink

    def status(self, line: str) -> None:
        self._sink(line)

    def warn(self, line: str) -> None:
        self._sink(line)

    def error(self, line: str) -> None:
        self._sink(f"ERROR: {line}")


@dataclass(frozen=True)
class ProgressCallbacks:
    """Structured per-file progress hooks for adapters (GUI); every hook is optional.

    ``on_file_started(source_id, name, idx, total)`` fires at the start of each file.
    ``on_stage_started(source_id, stage)`` fires when a file enters a
    :class:`~teachers_teammate.domain.stages.ProgressStage` (not when that stage is served from
    the cache).
    ``on_ocr_done(source_id, name)`` fires after OCR, before correction.
    ``on_file_done(outcome)`` fires with a :class:`FileOutcome` after a file completes
    (saved, failed, skipped or stopped).
    ``on_keep_awake(state)`` fires once at the start of a run with a
    :class:`~teachers_teammate.domain.run_summary.KeepAwakeState`.
    ``on_stage_detail(source_id, detail)`` fires while a request to Ollama has no answer yet,
    with what the server is doing instead.  ``on_first_token(source_id)`` fires when the model
    begins answering, which is where the run's clock starts.
    """

    on_file_started: OnFileStartedFn | None = None
    on_stage_started: OnStageStartedFn | None = None
    on_ocr_done: OnOcrDoneFn | None = None
    on_file_done: OnFileDoneFn | None = None
    on_keep_awake: Callable[[KeepAwakeState], None] | None = None
    on_stage_detail: OnStageDetailFn | None = None
    on_first_token: OnFirstTokenFn | None = None


# ── What a check and a preview answer ──────────────────────────────────────


class ConnectionCheck(NamedTuple):
    """What one check of a provider's server answered."""

    connected: bool
    """The server answered at all."""
    model_ok: bool
    """The model the check asked for is there; equal to ``connected`` when none was asked for."""
    message: str
    """The sentence the teacher reads, built in :mod:`teachers_teammate.texts`."""


class PreparedPreview(NamedTuple):
    """The two images a preview shows, and what the preparation did to the page."""

    original: Path
    """The page as it was scanned or rendered."""
    preprocessed: Path
    """The same page after the automatic preparation."""
    steps: list[str]
    """What the preparation did, in the order the teacher reads it."""
