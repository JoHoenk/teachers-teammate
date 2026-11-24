"""Infrastructure adapter for filesystem input discovery."""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from .input_provider_factory import supported_suffixes

_logger = logging.getLogger(__name__)


class FileDiscovery:
    """Find supported source files from configured input roots."""

    def collect_input_files(self, config: Config) -> list[Path]:
        """Return sorted source files matching supported suffixes."""
        return self.collect_input_files_from_dir(config.input_dir, config.recursive)

    def supported_suffixes(self) -> frozenset[str]:
        """Return the input suffixes this discovery collects."""
        return supported_suffixes()

    def collect_input_files_from_dir(self, input_dir: Path, recursive: bool) -> list[Path]:
        """Return sorted source files in *input_dir* without requiring a full Config."""
        glob_fn = input_dir.rglob if recursive else input_dir.glob
        try:
            suffixes = supported_suffixes()
            return sorted(
                file_path
                for file_path in glob_fn("*")
                if file_path.is_file() and file_path.suffix.lower() in suffixes
            )
        except OSError as exc:
            _logger.warning("Cannot read the input folder %s: %s", input_dir, exc)
            return []
