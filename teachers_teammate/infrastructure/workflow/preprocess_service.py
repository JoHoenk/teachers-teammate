"""Input loading and preprocessing stage service."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ...exceptions import PipelineInputError
from ...interfaces import ImagePreprocessor, InputPayload, InputProvider
from ..input_provider_factory import get_input_provider


class PreprocessService:
    """Loads input units and prepares OCR-ready images or raw text hints."""

    def __init__(
        self,
        *,
        tmp_dir: Path,
        preprocessor: ImagePreprocessor,
        provider_factory: Callable[[str, Path], InputProvider] | None = None,
    ) -> None:
        self._tmp_dir = tmp_dir
        self._preprocessor = preprocessor
        self._provider_factory = provider_factory or (
            lambda suffix, tmp_dir: get_input_provider(suffix, tmp_dir=tmp_dir)
        )

    def load_input(self, file: Path) -> InputPayload:
        """Resolve and execute the proper input provider for *file*."""
        provider = self._provider_factory(file.suffix, self._tmp_dir)
        return provider.load(file)

    def preprocess_input(
        self,
        file: Path,
    ) -> tuple[list[Path], list[str], Path | None, str | None]:
        """Return ``(paths_for_ocr, steps_applied, source_image_for_docx, raw_text_hint)``."""
        payload = self.load_input(file)
        if not payload.units:
            raise PipelineInputError(f"Input '{file.name}' produced no units.")

        preprocessed: list[Path] = []
        steps: list[str] = []
        text_parts: list[str] = []
        for unit in payload.units:
            if unit.kind == "image":
                if unit.image_path is None:
                    raise PipelineInputError(
                        f"Input '{file.name}' contains an image unit without image_path."
                    )
                proc_path, page_steps = self._preprocessor.preprocess(unit.image_path)
                preprocessed.append(proc_path)
                if not steps:
                    steps = page_steps
            elif unit.kind == "text":
                if unit.text:
                    text_parts.append(unit.text)
            else:
                raise PipelineInputError(
                    f"Unsupported input unit kind '{unit.kind}' for '{file.name}'."
                )

        raw_text_hint = "\n\n".join(text_parts).strip() if text_parts else None
        if not preprocessed and raw_text_hint is None:
            raise PipelineInputError(
                f"Input '{file.name}' produced no usable image or text content."
            )
        return preprocessed, steps, payload.source_image, raw_text_hint
