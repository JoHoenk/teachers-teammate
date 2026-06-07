"""Application-layer write commands for state transitions."""

from __future__ import annotations

from collections.abc import Callable
import dataclasses
from pathlib import Path

from ..config import Config
from ..domain.stages import normalize_stage
from ..infrastructure.file_discovery import FileDiscovery
from ..infrastructure.ocr_processor import get_ocr_prompt
from ..infrastructure.state_repository import (
    DocumentState,
    StateRepository,
    compute_file_hash,
)
from ..infrastructure.workflow.cache_service import (
    compute_correction_config_hash,
    compute_ocr_config_hash,
)


class ApplicationCommands:
    """Encapsulates the three write-ops that mutate document state."""

    def __init__(
        self,
        *,
        discovery: FileDiscovery,
        state_store_factory: Callable[[Config], StateRepository],
    ) -> None:
        self._discovery = discovery
        self._state_store_factory = state_store_factory

    def invalidate_from_stage(
        self,
        config: Config,
        *,
        selected_source_ids: list[str],
        stage: str,
    ) -> tuple[list[str], list[str], bool]:
        """Invalidate state for *selected_source_ids* from *stage* onwards."""
        files = self._discovery.collect_input_files(config)
        return self._invalidate_from_stage(
            selected_source_ids=selected_source_ids,
            stage=stage,
            files=files,
            store=self._state_store_factory(config),
        )

    def record_manual_ocr_edit(
        self,
        config: Config,
        *,
        source: Path,
        edited_text: str,
        preview_img: str,
    ) -> DocumentState | None:
        """Persist a manually edited OCR result for *source*."""
        ocr_config_hash = compute_ocr_config_hash(config, get_ocr_prompt(config.language))
        return self._record_manual_ocr_edit(
            self._state_store_factory(config),
            source=source,
            edited_text=edited_text,
            preview_img=preview_img,
            ocr_config_hash=ocr_config_hash,
        )

    def record_manual_correction_edit(
        self,
        config: Config,
        *,
        source: Path,
        raw_text: str,
        preview_img: str,
        edited_text: str,
    ) -> DocumentState | None:
        """Persist a manually edited correction result for *source*."""
        ocr_config_hash = compute_ocr_config_hash(config, get_ocr_prompt(config.language))
        correction_config_hash = compute_correction_config_hash(config)
        return self._record_manual_correction_edit(
            self._state_store_factory(config),
            source=source,
            raw_text=raw_text,
            preview_img=preview_img,
            edited_text=edited_text,
            ocr_config_hash=ocr_config_hash,
            correction_config_hash=correction_config_hash,
        )

    # ── Private helpers ───────────────────────────────────────────────────

    @staticmethod
    def _load_or_create_state(store: StateRepository, source: Path) -> DocumentState | None:
        try:
            source_hash = compute_file_hash(source)
        except OSError:
            return None
        return store.load_or_create(source, source_hash)

    @staticmethod
    def _invalidate_from_stage(
        *,
        selected_source_ids: list[str],
        stage: str,
        files: list[Path],
        store: StateRepository,
    ) -> tuple[list[str], list[str], bool]:
        stage_name = normalize_stage(stage)
        if not files:
            return [], [], True

        files_by_source_id = {str(file.resolve()): file for file in files}
        matched = [
            files_by_source_id[source_id]
            for source_id in selected_source_ids
            if source_id in files_by_source_id
        ]
        if not matched:
            return [], [], False

        failures: list[str] = []
        for file in matched:
            try:
                store.invalidate_from_stage(file, stage_name)
            except OSError as exc:
                failures.append(f"{file.name}: {exc}")

        run_source_ids = [str(file.resolve()) for file in matched]
        return run_source_ids, failures, False

    @classmethod
    def _record_manual_ocr_edit(
        cls,
        store: StateRepository,
        *,
        source: Path,
        edited_text: str,
        preview_img: str,
        ocr_config_hash: str = "",
    ) -> DocumentState | None:
        state = cls._load_or_create_state(store, source)
        if state is None:
            return None
        try:
            return store.record_manual_ocr_edit(
                source,
                state,
                raw_text=edited_text,
                preview_img=preview_img,
                ocr_config_hash=ocr_config_hash,
            )
        except OSError:
            return None

    @classmethod
    def _record_manual_correction_edit(
        cls,
        store: StateRepository,
        *,
        source: Path,
        raw_text: str,
        preview_img: str,
        edited_text: str,
        ocr_config_hash: str = "",
        correction_config_hash: str = "",
    ) -> DocumentState | None:
        state = cls._load_or_create_state(store, source)
        if state is None:
            return None

        updates: dict = {}
        if not state.preview_img:
            updates["preview_img"] = preview_img
            updates["source_image"] = preview_img
        if not state.raw_text:
            updates["raw_text"] = raw_text
            updates["ocr_done"] = bool(raw_text)
            # The synthesised OCR text is fresh under the current config; stamp
            # its hash so reconciliation does not flag it stale on the next run.
            updates["ocr_config_hash"] = ocr_config_hash
        if updates:
            state = dataclasses.replace(state, **updates)

        try:
            return store.record_manual_correction_edit(
                source,
                state,
                correction_text=edited_text,
                correction_config_hash=correction_config_hash,
            )
        except OSError:
            return None
