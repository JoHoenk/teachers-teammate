"""What a run says about itself: the banner before it and the closing block after it.

The composition root wires services; it does not write prose.  These functions take the
``Config`` a run uses and the ``RunSummary`` it produced, and write to the ``Reporter`` the
adapter chose, so the command line keeps its layout and the GUI keeps its log pane.
"""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from ..domain.data_location import ENGINE_PPOCR, OCR_ENGINE_NAMES
from ..domain.local_models import READER_MODEL_NAME
from ..domain.run_summary import RunSummary
from .reporting import KeepAwakeState, Reporter

LINE = "=" * 64


def banner(
    config: Config,
    reporter: Reporter,
    *,
    config_file: Path | None = None,
    keep_awake: KeepAwakeState | None = None,
    battery_warning: str = "",
) -> None:
    """Write the block a run starts with: what it will do, and with which settings."""
    cfg = config
    reporter.status(LINE)
    reporter.status("Teacher's Teammate")
    reporter.status(LINE)
    if config_file is not None:
        reporter.status(f"  Config file   : {config_file}")
    if not cfg.recognition_enabled:
        reporter.status("  Recognition   : disabled (scans are skipped)")
    else:
        if cfg.ocr.engine == ENGINE_PPOCR:
            reporter.status(
                f"  OCR engine    : {OCR_ENGINE_NAMES[ENGINE_PPOCR]} ({READER_MODEL_NAME})"
            )
        else:
            reporter.status(f"  OCR engine    : {cfg.ocr.engine}")
        if cfg.ocr.engine == "ollama":
            reporter.status(f"  OCR model     : {cfg.ocr.model}")
    reporter.status(f"  Preprocess    : {cfg.ocr.preprocess_method}")
    reporter.status(f"  Language      : {cfg.language}")
    if (
        (cfg.recognition_enabled and cfg.ocr.engine == "ollama")
        or (cfg.correction_enabled and cfg.correction_provider == "ollama")
        or (cfg.evaluation_enabled and cfg.evaluate_provider == "ollama")
    ):
        reporter.status(f"  Ollama URL    : {cfg.ollama_url}")
    reporter.status(f"  Input         : {cfg.input_dir.resolve()}")
    reporter.status(f"  Output        : {cfg.output_dir.resolve()}")
    reporter.status(f"  Recursive     : {cfg.recursive}")
    reporter.status(f"  Debug         : {cfg.debug}")
    if not cfg.correction_enabled:
        reporter.status("  Correction    : disabled")
    else:
        reporter.status(
            f"  Correction    : {cfg.correction_provider} / {cfg.effective_correction_model}"
        )
    if cfg.anonymization_enabled:
        reporter.status("  Anonymization : enabled (PII removed after text recognition)")
    if not cfg.evaluation_enabled:
        reporter.status("  Evaluation    : disabled")
    else:
        reporter.status(
            f"  Evaluation    : {cfg.evaluate_provider} / {cfg.effective_evaluate_model}"
        )
    if not cfg.docx_enabled:
        reporter.status("  DOCX output   : disabled")
    else:
        reporter.status(f"  DOCX format   : {cfg.docx_format}")
    text_files = [
        name
        for name, enabled in (
            ("plain", cfg.text_enabled),
            ("anonymized", cfg.text_anonymized_enabled),
        )
        if enabled
    ]
    reporter.status(f"  Text files    : {', '.join(text_files) or 'disabled'}")
    _keep_awake_line(reporter, keep_awake, battery_warning)


def summary(run: RunSummary, reporter: Reporter) -> None:
    """Write the closing block of a run: the counts, then the files that need attention."""
    reporter.status("")
    reporter.status(LINE)
    counts = [
        f"{len(run.succeeded)} succeeded",
        f"{len(run.failed)} failed",
    ]
    if run.skipped:
        counts.append(f"{len(run.skipped)} skipped")
    if run.stopped:
        counts.append(f"{len(run.stopped)} stopped")
    if run.not_started:
        counts.append(f"{len(run.not_started)} not started")
    reporter.status(f"Done: {', '.join(counts)}.")
    if run.failed:
        reporter.status("Failed files:")
        for name in run.failed:
            reporter.status(f"  - {name}")
    if run.skipped:
        reporter.status("Skipped files (see the lines above for why):")
        for name in run.skipped:
            reporter.status(f"  - {name}")
    if run.stopped or run.not_started:
        reporter.status("Unfinished files (stopped by user; what was done is kept):")
        for name in (*run.stopped, *run.not_started):
            reporter.status(f"  - {name}")
    reporter.status(LINE)


def preview_banner(config: Config, reporter: Reporter, preview_dir: Path) -> None:
    """Write the opening block of a preview run (image preparation only)."""
    reporter.status(LINE)
    reporter.status("Teacher's Teammate — Preview (preprocess only)")
    reporter.status(LINE)
    reporter.status(f"  Preprocess    : {config.ocr.preprocess_method}")
    reporter.status(f"  Input         : {config.input_dir.resolve()}")
    reporter.status(f"  Preview dir   : {preview_dir.resolve()}")
    reporter.status(LINE)


def preview_summary(run: RunSummary, reporter: Reporter, preview_dir: Path) -> None:
    """Write the closing block of a preview run: what was prepared, and where it went."""
    reporter.status("")
    reporter.status(LINE)
    reporter.status(f"Done: {len(run.succeeded)} preprocessed, {len(run.failed)} failed.")
    reporter.status(f"Images saved to: {preview_dir.resolve()}")
    if run.failed:
        reporter.status("Failed files:")
        for name in run.failed:
            reporter.status(f"  - {name}")
    reporter.status(LINE)


def _keep_awake_line(
    reporter: Reporter, awake: KeepAwakeState | None, battery_warning: str
) -> None:
    """Close the banner with whether the computer is kept awake (and a battery warning)."""
    if awake is not None:
        if not awake.enabled:
            reporter.status("  Keep awake    : off (the computer may go to sleep)")
        elif awake.held:
            reporter.status("  Keep awake    : yes")
        elif not awake.available:
            reporter.status("  Keep awake    : no (not available on this computer)")
        else:
            reporter.status("  Keep awake    : no (the system did not allow it)")
    reporter.status(LINE)
    if awake is not None and awake.enabled and awake.on_battery and battery_warning:
        reporter.warn(battery_warning)
