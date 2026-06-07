"""Workflow coordinator for per-file batch execution orchestration."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading

from ..reporting import Reporter, StdoutReporter
from .file_processor import ProcessingResult

_logger = logging.getLogger(__name__)

ProcessFileFn = Callable[[Path], ProcessingResult]
OnFileStartedFn = Callable[[str, str, int, int], None]
OnFileDoneFn = Callable[[str, str, bool, str, str, str, str, str], None]
CleanupFn = Callable[[], None]


def run_files(
    files: list[Path],
    *,
    process_file: ProcessFileFn,
    cleanup_tmp: CleanupFn,
    stop_event: threading.Event | None = None,
    on_file_started: OnFileStartedFn | None = None,
    on_file_done: OnFileDoneFn | None = None,
    reporter: Reporter | None = None,
) -> tuple[list[str], list[str]]:
    """Iterate *files*, calling *process_file* for each, and return ``(succeeded, failed)`` names."""
    reporter = reporter or StdoutReporter()
    total = len(files)
    succeeded: list[str] = []
    failed: list[str] = []
    try:
        for idx, file in enumerate(files, start=1):
            source_id = str(file.resolve())
            if stop_event and stop_event.is_set():
                reporter.status("\nStopped by user.")
                break
            reporter.status(f"[{idx}/{total}] Processing: {file.name}")
            if on_file_started:
                on_file_started(source_id, file.name, idx, total)
            result = process_file(file)
            if on_file_done:
                # When ok=True, result.message is the saved filename (never shown in the GUI).
                # Replace it with the joined stage warnings so the GUI can mark the correction
                # column with ⚠ on partial failure; empty string means all stages succeeded.
                msg = "; ".join(result.warnings) if result.ok else result.message
                on_file_done(
                    source_id,
                    file.name,
                    result.ok,
                    msg,
                    result.preview_img,
                    result.raw_text,
                    result.correction_text,
                    result.evaluation_text,
                )
            if result.ok:
                reporter.status(f"       → Saved: {result.message}")
                for warning in result.warnings:
                    _logger.warning("%s", warning)
                succeeded.append(file.name)
            else:
                _logger.error("%s", result.message)
                failed.append(file.name)
    finally:
        cleanup_tmp()
    return succeeded, failed
