"""Custom exceptions for the OCR pipeline."""

from __future__ import annotations

import threading


class OCRError(Exception):
    """Raised by :class:`~teachers_teammate.interfaces.OCRProcessor` implementations
    when text extraction fails.

    The ``args[0]`` message describes the failure (e.g. timeout, HTTP error,
    empty response).  Callers that want a human-readable string can use
    ``str(exc)`` directly.  ``status_code`` is the HTTP status when the server answered with
    an error (``None`` for connection failures, timeouts and empty responses).
    """

    def __init__(self, *args: object, status_code: int | None = None) -> None:
        super().__init__(*args)
        self.status_code = status_code


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


class OCRTimeoutError(OCRError):
    """Raised when no output arrives within the first-token timeout.

    Only the wait for the first token is bounded: once the model has begun to answer, the answer
    may take as long as it takes.  A subclass of :exc:`OCRError`, so every caller that handles the
    failure keeps working, and a caller that cares (the evaluation tools) can tell a model that
    did not start from one that failed.
    """


class OllamaUnreachableError(OCRError):
    """Raised by a chat request when the Ollama server cannot be reached at all.

    A subclass of :exc:`OCRError`, so every caller that handles a failed request keeps working,
    and a caller that must tell an unreachable server from a model that failed (the evaluation
    tools: an outage says nothing about a model) can.
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


class OllamaConnectionError(RuntimeError):
    """Raised when the Ollama service cannot be reached or returns an error.

    Covers: connection refused, timeout, HTTP error, model not found.
    ``str(exc)`` contains a human-readable description suitable for display.
    """


class PipelineInputError(ValueError):
    """Raised when an input file cannot be loaded or yields no usable content.

    Examples: unsupported file suffix, unreadable image, no OCR-able pages.
    """


#: The one sentence for a scan that is left out because text recognition is switched off, so
#: the exception, the queue row and the log say the same.
RECOGNITION_OFF_MESSAGE = "Needs text recognition, which is switched off."


class RecognitionSwitchedOffError(PipelineInputError):
    """Raised for an input that has to be read from page images while recognition is off.

    Not a failure: the pipeline turns it into a *skipped* file.  A subclass of
    :exc:`PipelineInputError`, so a caller that does not know about skipping still treats the
    file as one that cannot be used.
    """

    def __init__(self, name: str = "") -> None:
        super().__init__(f"{name}: {RECOGNITION_OFF_MESSAGE}" if name else RECOGNITION_OFF_MESSAGE)
