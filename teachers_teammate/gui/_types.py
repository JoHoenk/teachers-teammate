"""GUI-specific type aliases."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..domain.correction_review import CorrectionReview


class StageStatus(StrEnum):
    """Status of a pipeline stage for a single file, mirroring the symbols in the results table."""

    PENDING = "-"
    RUNNING = "▶"
    DONE = "✓"
    ERROR = "✗"


@dataclass(frozen=True)
class PreprocessInfo:
    """How a document's preview image was produced, for the preview panel.

    ``original_img`` is the page before preprocessing (``""`` if none was kept).
    ``steps`` lists the applied preprocessing steps: ``None`` = unknown (cache written
    before this was recorded), ``()`` = none applied.
    """

    original_img: str = ""
    steps: tuple[str, ...] | None = None


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
    text_format: str = "plain"
    """Notation of ``raw_txt`` / ``corr_txt``: ``"plain"`` or ``"markdown"``."""
    correction_review: CorrectionReview | None = None
    """Explanations and unclear expressions, ``None`` when the file has no review."""
    correction_warning: str = ""
    """Why the file has no review (fallback to corrections-only); empty when there is one."""
    skipped: bool = False
    """The file was left out on purpose (a scan while text recognition is off), not a failure."""
    stopped: bool = False
    """The run was stopped while the file was processed: not a failure; what was done is kept."""
    stopped_stage: str = ""
    """With ``stopped``: the step the stop cut short (``"ocr"``, ``"correction"`` or
    ``"evaluation"``); empty when the file did not get as far as telling."""

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
    preprocess_method: str
    pdf_render_dpi: int
    pdf_text_layer: str
    correction_provider: str
    correction_model: str
    correction_level: str
    explanation_language: str
    correction_output: str
    answer_format_review: str
    answer_format_text: str
    answer_format_version: int
    evaluate_provider: str
    evaluate_model: str
    output_dir: str
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
    debug: bool
