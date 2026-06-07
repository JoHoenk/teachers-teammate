"""GUI-specific type aliases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict


class StageStatus(StrEnum):
    """Status of a pipeline stage for a single file, mirroring the symbols in the results table."""

    PENDING = "-"
    RUNNING = "▶"
    DONE = "✓"
    ERROR = "✗"


@dataclass(frozen=True)
class FileDoneEvent:
    """One completed file's result — emitted by ``OCRWorker.file_done`` and rendered by the GUI.

    Bundling the fields into a single payload keeps the ``file_done`` signal and
    its slots from carrying ten positional arguments.
    """

    source_id: str
    name: str
    ok: bool
    message: str
    ocr_s: float
    correction_s: float
    preview_img: str = ""
    raw_txt: str = ""
    corr_txt: str = ""
    eval_txt: str = ""

    @property
    def total_s(self) -> float:
        """Wall-clock seconds for the whole file (OCR + correction)."""
        return self.ocr_s + self.correction_s


class SettingsDict(TypedDict, total=False):
    """Dict emitted by :meth:`~teachers_teammate.gui._config_panel.ConfigPanel.to_dict`
    and consumed by :meth:`~teachers_teammate.gui._config_panel.ConfigPanel.load_from_dict`."""

    ocr_engine: str
    ocr_provider: str
    ocr_model: str
    ollama_url: str
    ocr_timeout: int
    preprocess_method: str
    correction_provider: str
    correction_model: str
    evaluate_provider: str
    evaluate_model: str
    output_dir: str
    docx_format: str
    docx_enabled: bool
    correction_enabled: bool
    evaluation_enabled: bool
    anonymization_enabled: bool
    anonymizer_secondary_model: str | None
    anonymizer_patterns: list | None
    debug: bool
