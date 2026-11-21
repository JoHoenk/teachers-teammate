"""Workflow coordinator for per-file batch execution orchestration."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading

from ...domain.proofreading.review import align, review_summary
from ...domain.run_summary import FileOutcome, FileStatus, RunSummary
from ...interfaces import OnFileDoneFn, OnFileStartedFn, Reporter
from ..reporting import StdoutReporter
from .file_processor import ProcessingResult

_logger = logging.getLogger(__name__)

ProcessFileFn = Callable[[Path], ProcessingResult]
CleanupFn = Callable[[], None]


def run_files(
    files: list[Path],
    *,
    process_file: ProcessFileFn,
    cleanup_tmp: CleanupFn,
    stop_event: threading.Event | None = None,
    stop_after_file_event: threading.Event | None = None,
    on_file_started: OnFileStartedFn | None = None,
    on_file_done: OnFileDoneFn | None = None,
    reporter: Reporter | None = None,
) -> RunSummary:
    """Iterate *files*, calling *process_file* for each, and return the names by outcome.

    Two events end a run.  *stop_after_file_event* keeps new files from starting: the one being
    processed finishes completely.  *stop_event* ("Stop now") also makes the file being
    processed stop at its next step (see ``FileProcessor``); both leave the files that never
    started in ``not_started``.
    """
    reporter = reporter or StdoutReporter()
    total = len(files)
    succeeded: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    stopped: list[str] = []
    not_started: list[str] = []
    try:
        for idx, file in enumerate(files, start=1):
            source_id = str(file.resolve())
            if (stop_event and stop_event.is_set()) or (
                stop_after_file_event and stop_after_file_event.is_set()
            ):
                not_started = [f.name for f in files[idx - 1 :]]
                reporter.status(f"\nStopped by user: {len(not_started)} file(s) not started.")
                break
            reporter.status(f"[{idx}/{total}] Processing: {file.name}")
            if on_file_started:
                on_file_started(source_id, file.name, idx, total)
            result = process_file(file)
            outcome = _outcome(source_id, file.name, result)
            if on_file_done:
                on_file_done(outcome)
            if outcome.status is FileStatus.STOPPED:
                reporter.status(f"       → {result.message}")
                stopped.append(file.name)
            elif outcome.status is FileStatus.SKIPPED:
                _logger.info("%s", result.message)
                skipped.append(file.name)
            elif outcome.status is FileStatus.SAVED:
                reporter.status(f"       → Saved: {result.message}")
                summary = _review_line(result)
                if summary:
                    reporter.status(f"       → {summary}")
                for warning in outcome.warnings:
                    _logger.warning("%s", warning)
                succeeded.append(file.name)
            else:
                _logger.error("%s", result.message)
                failed.append(file.name)
    finally:
        cleanup_tmp()
    return RunSummary(
        succeeded=tuple(succeeded),
        failed=tuple(failed),
        skipped=tuple(skipped),
        stopped=tuple(stopped),
        not_started=tuple(not_started),
    )


def _outcome(source_id: str, name: str, result: ProcessingResult) -> FileOutcome:
    """Return what happened to one file, as the adapters read it."""
    if result.stopped:
        return FileOutcome(
            source_id=source_id,
            name=name,
            status=FileStatus.STOPPED,
            error=result.message,
            stopped_stage=result.stopped_stage,
        )
    if result.skipped:
        return FileOutcome(
            source_id=source_id, name=name, status=FileStatus.SKIPPED, error=result.message
        )
    if result.ok:
        return FileOutcome(
            source_id=source_id, name=name, status=FileStatus.SAVED, warnings=result.warnings
        )
    return FileOutcome(
        source_id=source_id, name=name, status=FileStatus.FAILED, error=result.message
    )


def _review_line(result: ProcessingResult) -> str:
    """Return ``"7 changes, 2 unclear"`` for *result*, counted on the diff of the saved texts."""
    if result.correction_review is None:
        return ""
    from ..ocr_formats import plain_text_of  # noqa: PLC0415

    aligned = align(
        plain_text_of(result.raw_text, result.text_format),
        plain_text_of(result.correction_text, result.text_format),
        result.correction_review,
    )
    return review_summary(aligned)
