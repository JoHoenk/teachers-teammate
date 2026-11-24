"""What a run says about itself: the banner before it and the closing block after it.

The composition root wires services; it does not write prose.  These functions take the
``Config`` a run uses and the ``RunSummary`` it produced, and write to the ``Reporter`` the
adapter chose, so the command line keeps its layout and the GUI keeps its log pane.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..config import Config
from ..domain.data_location import ENGINE_OLLAMA, ENGINE_PPOCR, OCR_ENGINE_NAMES
from ..domain.local_models import READER_MODEL_NAME
from ..domain.run_summary import KeepAwakeState, RunSummary
from ..interfaces import Reporter
from ..run_rules import uses_ollama
from ..texts import report as txt


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

    def row(label: str, value: object) -> None:
        reporter.status(txt.row(label, value))

    reporter.status(txt.LINE)
    reporter.status(txt.TITLE)
    reporter.status(txt.LINE)
    if config_file is not None:
        row(txt.CONFIG_FILE, config_file)
    if not cfg.recognition_enabled:
        row(txt.RECOGNITION, txt.RECOGNITION_DISABLED)
    else:
        if cfg.ocr.engine == ENGINE_PPOCR:
            row(
                txt.OCR_ENGINE,
                txt.reader_engine(OCR_ENGINE_NAMES[ENGINE_PPOCR], READER_MODEL_NAME),
            )
        else:
            row(txt.OCR_ENGINE, cfg.ocr.engine)
        if cfg.ocr.engine == ENGINE_OLLAMA:
            row(txt.OCR_MODEL, cfg.ocr.model)
    row(txt.PREPROCESS, cfg.ocr.preprocess_method)
    row(txt.LANGUAGE, cfg.language)
    if uses_ollama(cfg):
        row(txt.OLLAMA_URL, cfg.ollama_url)
    row(txt.INPUT, cfg.input_dir.resolve())
    row(txt.OUTPUT, cfg.output_dir.resolve())
    row(txt.RECURSIVE, cfg.recursive)
    row(txt.DEBUG, cfg.debug)
    if not cfg.correction_enabled:
        row(txt.CORRECTION, txt.DISABLED)
    else:
        row(
            txt.CORRECTION,
            txt.provider_model(cfg.correction_provider, cfg.effective_correction_model),
        )
    if cfg.anonymization_enabled:
        row(txt.ANONYMIZATION, txt.ANONYMIZATION_ENABLED)
    if not cfg.evaluation_enabled:
        row(txt.EVALUATION, txt.DISABLED)
    else:
        row(
            txt.EVALUATION,
            txt.provider_model(cfg.evaluate_provider, cfg.effective_evaluate_model),
        )
    if not cfg.docx_enabled:
        row(txt.DOCX_OUTPUT, txt.DISABLED)
    else:
        row(txt.DOCX_FORMAT, cfg.docx_format)
    row(
        txt.TEXT_FILES,
        txt.text_files(plain=cfg.text_enabled, anonymized=cfg.text_anonymized_enabled),
    )
    _keep_awake_line(reporter, keep_awake, battery_warning)


def summary(run: RunSummary, reporter: Reporter) -> None:
    """Write the closing block of a run: the counts, then the files that need attention."""
    reporter.status("")
    reporter.status(txt.LINE)
    reporter.status(
        txt.done(
            succeeded=len(run.succeeded),
            failed=len(run.failed),
            skipped=len(run.skipped),
            stopped=len(run.stopped),
            not_started=len(run.not_started),
        )
    )
    if run.failed:
        _file_list(reporter, txt.FAILED_FILES, run.failed)
    if run.skipped:
        _file_list(reporter, txt.SKIPPED_FILES, run.skipped)
    if run.stopped or run.not_started:
        _file_list(reporter, txt.UNFINISHED_FILES, (*run.stopped, *run.not_started))
    reporter.status(txt.LINE)


def preview_banner(config: Config, reporter: Reporter, preview_dir: Path) -> None:
    """Write the opening block of a preview run (image preparation only)."""
    reporter.status(txt.LINE)
    reporter.status(txt.PREVIEW_TITLE)
    reporter.status(txt.LINE)
    reporter.status(txt.row(txt.PREPROCESS, config.ocr.preprocess_method))
    reporter.status(txt.row(txt.INPUT, config.input_dir.resolve()))
    reporter.status(txt.row(txt.PREVIEW_DIR, preview_dir.resolve()))
    reporter.status(txt.LINE)


def preview_summary(run: RunSummary, reporter: Reporter, preview_dir: Path) -> None:
    """Write the closing block of a preview run: what was prepared, and where it went."""
    reporter.status("")
    reporter.status(txt.LINE)
    reporter.status(txt.preview_done(len(run.succeeded), len(run.failed)))
    reporter.status(txt.images_saved(preview_dir.resolve()))
    if run.failed:
        _file_list(reporter, txt.FAILED_FILES, run.failed)
    reporter.status(txt.LINE)


def _file_list(reporter: Reporter, heading: str, names: Iterable[str]) -> None:
    """Write *heading* and one line for each of *names*."""
    reporter.status(heading)
    for name in names:
        reporter.status(txt.file_item(name))


def _keep_awake_line(
    reporter: Reporter, awake: KeepAwakeState | None, battery_warning: str
) -> None:
    """Close the banner with whether the computer is kept awake (and a battery warning)."""
    if awake is not None:
        if not awake.enabled:
            value = txt.AWAKE_OFF
        elif awake.held:
            value = txt.AWAKE_YES
        elif not awake.available:
            value = txt.AWAKE_UNAVAILABLE
        else:
            value = txt.AWAKE_REFUSED
        reporter.status(txt.row(txt.KEEP_AWAKE, value))
    reporter.status(txt.LINE)
    if awake is not None and awake.enabled and awake.on_battery and battery_warning:
        reporter.warn(battery_warning)
