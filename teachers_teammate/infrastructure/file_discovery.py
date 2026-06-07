"""Infrastructure adapter for filesystem input discovery."""

from __future__ import annotations

from pathlib import Path

from ..config import Config
from .input_provider_factory import supported_suffixes


class FileDiscovery:
    """Find supported source files from configured input roots."""

    def collect_input_files(self, config: Config) -> list[Path]:
        """Return sorted source files matching supported suffixes."""
        return self.collect_input_files_from_dir(config.input_dir, config.recursive)

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
        except OSError:
            return []
