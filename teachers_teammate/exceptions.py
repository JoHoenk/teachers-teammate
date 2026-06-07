"""Custom exceptions for the OCR pipeline."""

from __future__ import annotations


class OCRError(Exception):
    """Raised by :class:`~teachers_teammate.interfaces.OCRProcessor` implementations
    when text extraction fails.

    The ``args[0]`` message describes the failure (e.g. timeout, HTTP error,
    empty response).  Callers that want a human-readable string can use
    ``str(exc)`` directly.
    """


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


class OllamaConnectionError(RuntimeError):
    """Raised when the Ollama service cannot be reached or returns an error.

    Covers: connection refused, timeout, HTTP error, model not found.
    ``str(exc)`` contains a human-readable description suitable for display.
    """


class PipelineInputError(ValueError):
    """Raised when an input file cannot be loaded or yields no usable content.

    Examples: unsupported file suffix, unreadable image, no OCR-able pages.
    """
