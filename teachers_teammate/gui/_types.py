"""GUI-specific type aliases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from ..domain.run_summary import FileOutcome


class StageStatus(StrEnum):
    """Status of a pipeline stage for a single file, mirroring the symbols in the results table."""

    PENDING = "-"
    RUNNING = "▶"
    DONE = "✓"
    ERROR = "✗"


@dataclass(frozen=True)
class FileDoneEvent:
    """One completed file — emitted by ``OCRWorker.file_done`` and rendered by the GUI.

    The run's own result (:class:`~teachers_teammate.domain.run_summary.FileOutcome`) plus the
    two durations the worker measured; the texts are not carried, a row that needs one reads
    the document from the store.
    """

    outcome: FileOutcome
    ocr_s: float
    correction_s: float

    @property
    def total_s(self) -> float:
        """Wall-clock seconds for the whole file (OCR + correction)."""
        return self.ocr_s + self.correction_s


class SettingsDict(TypedDict, total=False):
    """Dict emitted by :meth:`~teachers_teammate.gui._config_panel.ConfigPanel.to_dict`
    and consumed by :meth:`~teachers_teammate.gui._config_panel.ConfigPanel.load_from_dict`."""

    ocr_engine: str
    ocr_model: str
    ollama_url: str
    hardware_profile: str
    ocr_timeout: int
    recognition_enabled: bool
    keep_awake: bool
    check_for_updates: bool
    results_keep_days: int
    log_detail: str
    preprocess_method: str
    pdf_render_dpi: int
    pdf_text_layer: str
    language: str
    correction_provider: str
    correction_model: str
    correction_level: str
    explanation_language: str
    correction_output: str
    correction_notes: str
    evaluate_provider: str
    evaluate_model: str
    evaluate_prompt: str
    output: str
    docx_format: str
    docx_enabled: bool
    text_enabled: bool
    text_anonymized_enabled: bool
    correction_enabled: bool
    evaluation_enabled: bool
    anonymization_enabled: bool
    anonymizer_primary_model: str | None
    anonymizer_secondary_model: str | None
    anonymizer_patterns: list | None
    anonymizer_hide: list | None
    anonymizer_keep: list | None
    debug: bool
