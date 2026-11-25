"""Custom exceptions for the OCR pipeline."""

from __future__ import annotations

from enum import StrEnum
import threading

from .messages import Message


class PipelineError(Exception):
    """A failure that ends one file, not the run: the file is ``FAILED``, the next one runs.

    Every failure a pipeline stage can report derives from this class, so
    :meth:`~teachers_teammate.infrastructure.workflow.file_processor.FileProcessor.process` needs
    one ``except`` and the coordinator around it one safety net.  A stop
    (:exc:`RunStoppedError`) and a file that is left out (:exc:`FileSkipped`) are no failures.
    """


class StageErrorKind(StrEnum):
    """What kind of failure ended a stage; it goes to the log next to the stage."""

    ENGINE = "engine"
    """The reader (text recognition) failed."""
    PROVIDER = "provider"
    """The model did not answer a proofreading request."""
    ANONYMIZER = "anonymizer"
    """Hiding or restoring personal data failed (spaCy, a name model, a text over its limit)."""
    PREPROCESSING = "preprocessing"
    """The pages of the document could not be prepared."""
    STORAGE = "storage"
    """What the app stores about the document could not be read or written."""
    OUTPUT = "output"
    """The output files could not be written."""


class StageError(PipelineError):
    """A stage could not do its work, so the file ends before its outputs are written.

    ``str(exc)`` is the sentence a teacher reads: the row of the file and the dialog show it.
    :attr:`kind` says what failed, :attr:`stage` where.
    """

    def __init__(
        self, message: Message | str = "", *, kind: StageErrorKind, stage: str = ""
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.stage = stage
        """The :class:`~teachers_teammate.domain.stages.Stage` that failed, as its word (``"ocr"``,
        ``"correction"`` or ``"evaluation"``, the last for hiding the personal data of the review);
        empty when the failure happened outside those three (preparing the pages, reading or
        writing a file).  ``exceptions`` is in the package root, so it cannot import the enum."""

    def __str__(self) -> str:
        """Return the sentence a teacher reads."""
        return str(self.args[0]) if self.args else ""


class FileSkipped(PipelineError):
    """The file is left out on purpose — text recognition is off, or the file is gone.

    Deliberately not a :exc:`StageError`: nothing failed, so the file is *skipped* and its row
    says why.  ``str(exc)`` is the sentence the row shows.
    """


class OCRError(StageError):
    """Raised by :class:`~teachers_teammate.interfaces.OCRProcessor` implementations
    when text extraction fails.

    The ``args[0]`` message describes the failure (e.g. timeout, HTTP error,
    empty response).  Callers that want a human-readable string can use
    ``str(exc)`` directly.  ``status_code`` is the HTTP status when the server answered with
    an error (``None`` for connection failures, timeouts and empty responses).
    """

    def __init__(self, message: Message | str = "", *, status_code: int | None = None) -> None:
        super().__init__(message, kind=StageErrorKind.ENGINE, stage="ocr")
        self.status_code = status_code


class AnonymizerError(StageError):
    """Hiding or restoring personal data failed (spaCy, a name model, a text over its limit)."""

    def __init__(self, message: Message | str = "", *, stage: str = "") -> None:
        super().__init__(message, kind=StageErrorKind.ANONYMIZER, stage=stage)


def stop_requested(event: threading.Event | None) -> bool:
    """Return whether *event* (a run's "Stop now" event, or ``None``) is set."""
    return event is not None and event.is_set()


class RunStoppedError(Exception):
    """Raised to leave a stage quickly when the teacher pressed "Stop now".

    Deliberately *not* an :exc:`OCRError`: a stop is never a model failure, so no handler of
    failed requests may treat it as one.  The stop event is the truth; this exception is only a
    fast exit.  The Ollama wrappers in ``_llm_invoke`` re-raise it instead of degrading to a
    warning, so :class:`~teachers_teammate.infrastructure.workflow.file_processor.FileProcessor`
    needs only its ``except RunStoppedError`` handlers.  A new wrapper that catches every
    exception must do the same.
    """


class ModelMissingError(OCRError):
    """Raised when a model file the app runs itself has not been downloaded.

    A subclass of :exc:`OCRError`, so a caller that handles failed reading keeps working; a
    caller that can offer the download (the preflight check, the GUI) can tell the two apart.
    ``str(exc)`` is the sentence a teacher reads.
    """


class ModelDownloadError(RuntimeError):
    """Raised when a model file could not be downloaded.

    ``str(exc)`` is a sentence a teacher (or the IT department) can act on: no connection,
    host blocked, damaged on the way, no space left, folder not writable.
    """


class ModelDownloadCancelledError(ModelDownloadError):
    """Raised when the teacher cancelled a model download; no partial file is left behind."""


class OCRTimeoutError(OCRError):
    """Raised when no output arrives within the first-token timeout.

    Only the wait for the first token is bounded: once the model has begun to answer, the answer
    may take as long as it takes.  A subclass of :exc:`OCRError`, so every caller that handles the
    failure keeps working, and a caller that cares (the evaluation tools) can tell a model that
    did not start from one that failed.
    """


class OllamaUnreachableError(OCRError):
    """Raised when the Ollama server cannot be used: unreachable, an HTTP error, an unknown model.

    A subclass of :exc:`OCRError`, so every caller that handles a failed request keeps working,
    and a caller that must tell an outage from a model that failed (the evaluation tools: an
    outage says nothing about a model) can.  ``str(exc)`` is the sentence a teacher reads.
    """


class OCRDeadlineError(OCRError):
    """Raised when an answer is still being written after the overall deadline a caller set.

    The first-token timeout only bounds the wait for the answer to begin.  A model that loops —
    one that writes without end, or past its window by shifting the context — would otherwise
    keep a caller waiting for as long as it takes (``mistral:latest`` wrote 81,920 tokens in 937
    s).  A caller that sets ``total_timeout`` gets this error instead, and the request is
    abandoned so the server stops generating.
    """


#: The one user-facing sentence for an answer the context window cut off, so the warning a
#: teacher reads and the exception that carries it cannot drift apart.
TRUNCATED_MESSAGE = "The text was too long for the model's working memory."


class ResponseTruncatedError(OCRError):
    """Raised when a model stopped because its context was full (``done_reason == "length"``).

    The answer that arrived is only the beginning of one: the model never reached the end of
    the text it was asked about, so the tail of the text is missing.  Callers must not use such
    an answer — ``str(exc)`` is the user-facing reason, :data:`TRUNCATED_MESSAGE`.
    """

    def __init__(self, message: str = TRUNCATED_MESSAGE) -> None:
        super().__init__(message)


class ConfigFileNotFoundError(FileNotFoundError):
    """Raised by :func:`~teachers_teammate.infrastructure.storage_root.resolve_config_path`
    when an explicit ``--config`` path does not point to an existing file."""


class ConfigFileParseError(ValueError):
    """Raised by :func:`~teachers_teammate.config.load_config_file` when the TOML
    file cannot be parsed or read."""


class StorageResolutionError(RuntimeError):
    """Raised when no candidate storage root is writable."""


class ProviderNotAvailableError(RuntimeError):
    """Raised when a required provider package is not installed or cannot be loaded.

    ``str(exc)`` contains a human-readable message including pip install instructions.
    """


class OnlineOcrNotAllowedError(ValueError):
    """Raised when text recognition would run on an online service.

    Reading a page needs the whole page — names, grades, handwriting — so it must stay on a
    computer the user controls.  ``str(exc)`` explains what to choose instead.
    """


class PipelineInputError(ValueError):
    """Raised when an input file cannot be loaded or yields no usable content.

    Examples: unsupported file suffix, unreadable image, no OCR-able pages.
    """


#: The one sentence for a scan that is left out because text recognition is switched off, so
#: the exception, the queue row and the log say the same.
RECOGNITION_OFF_MESSAGE = "Needs text recognition, which is switched off."

#: What a file says that was queued and is gone when its turn comes.
FILE_GONE_MESSAGE = "The file is no longer there."

#: What a file says that ended on an error no stage expected; the traceback goes to the log.
UNEXPECTED_ERROR_MESSAGE = (
    "An unexpected error occurred; the file was not processed. "
    "The details are in the log file in the app's data folder, under logs."
)


class RecognitionSwitchedOffError(PipelineInputError):
    """Raised for an input that has to be read from page images while recognition is off.

    Not a failure: the pipeline turns it into a *skipped* file.  A subclass of
    :exc:`PipelineInputError`, so a caller that does not know about skipping still treats the
    file as one that cannot be used.
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(f"{name}: {RECOGNITION_OFF_MESSAGE}" if name else RECOGNITION_OFF_MESSAGE)


class InstallFailure(StrEnum):
    """Why an install failed.  The GUI turns each into a sentence (``texts`` owns the wording)."""

    DISK_FULL = "disk_full"
    FILE_IN_USE = "file_in_use"
    OFFLINE = "offline"
    NO_VERSION = "no_version"
    MERGE_FAILED = "merge_failed"
    INSTALL_FAILED = "install_failed"
    DOWNLOAD_FAILED = "download_failed"


class AddonInstallError(RuntimeError):
    """An add-on install failed for a known *reason*; ``str()`` is for the log, not a teacher."""

    def __init__(self, reason: InstallFailure) -> None:
        super().__init__(reason.value)
        self.reason = reason


class AddonUpdateError(RuntimeError):
    """Updating the add-ons failed for some of them; *errors* pairs a requirement with its error.

    *gone* lists the requirements whose old copy was removed from the add-on folder.
    """

    def __init__(
        self, errors: list[tuple[str, RuntimeError]], gone: list[str] | None = None
    ) -> None:
        super().__init__("; ".join(f"{requirement}: {error}" for requirement, error in errors))
        self.errors = errors
        self.gone = gone or []


class ReleaseFeedError(RuntimeError):
    """The newest release could not be read from the feed.

    ``status`` is the HTTP status of a refused answer, or ``None`` when there was no answer at all:
    no network, a timeout, or an answer that is not the JSON of a release.  ``rate_limited`` tells
    GitHub's hourly limit from a refusal that would work later.  No teacher reads this failure —
    the update check stays silent — so it carries no :class:`~teachers_teammate.messages.Message`;
    the log line names the type and the status.
    """

    def __init__(self, status: int | None = None, *, rate_limited: bool = False) -> None:
        super().__init__(f"the release feed answered {status}" if status else "no answer")
        self.status = status
        self.rate_limited = rate_limited
