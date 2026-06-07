"""Per-file processing state persistence.

This module owns the JSON-backed key-value store that tracks what has been
computed for each source document.

Responsibilities
----------------
- Define `DocumentState` — the versioned dataclass stored as JSON on disk.
- Define `DocumentStateView` — the read-only projection consumed by the GUI
  and CLI result tables.
- Implement `StateRepository` — load, validate, save, and invalidate state
  records while delegating all *policy* decisions (which stages become stale,
  what a reset clears) to the domain layer.
- Provide `compute_file_hash` and `compute_config_hash` — SHA-256 helpers
  used by both this module and `CacheReconciliationService`.

What this module does NOT do
-----------------------------
- It does not decide *when* to invalidate stages — that is `domain/freshness.py`.
- It does not manage preview image paths — that is `PreviewImageStore`.
- It does not select the storage root directory — that is `storage_root.py`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from ..domain.freshness import (
    StageHashes,
    first_stale_stage_for_hashes,
    reset_policy_from_stage,
    should_invalidate_preview_artifact,
)
from ..domain.stages import normalize_stage
from .storage_root import compute_cache_key


def compute_file_hash(path: Path) -> str:
    """Return SHA-256 hash for *path* using chunked reads."""
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_config_hash(*parts: str) -> str:
    """Return SHA-256 hash over ordered configuration parts."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


@dataclass(frozen=True)
class DocumentState:
    """Versioned processing state record for one source document."""

    schema_version: int
    source_path: str
    source_hash: str
    source_image: str = ""
    preview_img: str = ""
    raw_text: str = ""
    correction_text: str = ""
    evaluation_text: str = ""
    ocr_done: bool = False
    correction_done: bool = False
    evaluation_done: bool = False
    correction_invalidated: bool = False
    evaluation_invalidated: bool = False
    ocr_prompt: str = ""
    correction_prompt: str = ""
    evaluate_prompt: str = ""
    ocr_config_hash: str = ""
    correction_config_hash: str = ""
    eval_config_hash: str = ""
    preview_config_hash: str = ""


@dataclass(frozen=True)
class DocumentStateView:
    """Read-only UI view of a valid document state record."""

    source_id: str
    name: str
    preview_img: str
    raw_text: str
    correction_text: str
    evaluation_text: str
    ocr_done: bool
    correction_done: bool
    evaluation_done: bool
    cache_status_label: str
    loaded_at: str


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """Config fingerprint passed to `StateRepository.reconcile_runtime_config`.

    Bundles the prompts and per-stage config hashes that together decide which
    cached stages have gone stale relative to the current runtime configuration.
    """

    source_hash: str
    ocr_prompt: str
    correction_prompt: str
    evaluate_prompt: str
    ocr_config_hash: str
    correction_config_hash: str
    eval_config_hash: str
    preview_config_hash: str


@dataclass(frozen=True)
class OcrResultRecord:
    """OCR output payload persisted by `StateRepository.record_ocr_result`."""

    source_hash: str
    raw_text: str
    preview_img: str
    source_image: str
    ocr_prompt: str
    correction_prompt: str
    evaluate_prompt: str
    ocr_config_hash: str
    preview_config_hash: str


class StateRepository:
    """Persistence service for loading, validating, and invalidating state records."""

    _SCHEMA_VERSION = 2

    def __init__(self, state_root: Path) -> None:
        self._state_root = state_root
        self._state_root.mkdir(parents=True, exist_ok=True)

    def state_path_for_input(self, input_file: Path) -> Path:
        key = compute_cache_key(str(input_file.resolve()))
        return self._state_root / f"{input_file.stem}_{key}.json"

    @staticmethod
    def _cache_status_label(state: DocumentState) -> str:
        if state.evaluation_done:
            return "cached: full"
        if state.correction_done:
            return "cache: up to correction"
        if state.ocr_done:
            return "cache: OCR only"
        return ""

    def to_view(self, input_file: Path, state: DocumentState) -> DocumentStateView:
        """Map a validated state object to a read-only UI view."""
        source_id = str(input_file.resolve())
        return DocumentStateView(
            source_id=source_id,
            name=input_file.name,
            preview_img=state.preview_img,
            raw_text=state.raw_text,
            correction_text=state.correction_text,
            evaluation_text=state.evaluation_text,
            ocr_done=state.ocr_done,
            correction_done=state.correction_done,
            evaluation_done=state.evaluation_done,
            cache_status_label=self._cache_status_label(state),
            loaded_at=datetime.now(UTC).isoformat(),
        )

    def load_view(self, input_file: Path) -> DocumentStateView | None:
        """Return normalized view data for *input_file* if valid state exists."""
        state = self.load_valid(input_file)
        if state is None:
            return None
        return self.to_view(input_file, state)

    def load_views(self, input_files: list[Path]) -> dict[str, DocumentStateView]:
        """Return normalized view data for all files with valid state."""
        views: dict[str, DocumentStateView] = {}
        for input_file in input_files:
            view = self.load_view(input_file)
            if view is not None:
                views[view.source_id] = view
        return views

    def create(self, input_file: Path, source_hash: str) -> DocumentState:
        """Create a new empty state record for *input_file*."""
        return DocumentState(
            schema_version=self._SCHEMA_VERSION,
            source_path=str(input_file.resolve()),
            source_hash=source_hash,
        )

    def load_or_create(self, input_file: Path, source_hash: str) -> DocumentState:
        """Return a valid state for *input_file* or a fresh empty record."""
        state = self.load_valid(input_file)
        return state if state is not None else self.create(input_file, source_hash)

    def load(self, input_file: Path) -> DocumentState | None:
        path = self.state_path_for_input(input_file)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            state = DocumentState(**raw)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if state.schema_version != self._SCHEMA_VERSION:
            return None
        return state

    def save(self, input_file: Path, state: DocumentState) -> None:
        path = self.state_path_for_input(input_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(state), indent=2), encoding="utf-8")

    def record_preview_artifact(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        preview_img: str,
        source_image: str,
        preview_config_hash: str,
    ) -> DocumentState:
        """Persist preview artifact paths without changing downstream stage data."""
        new_state = dataclasses.replace(
            state,
            preview_img=preview_img,
            source_image=source_image,
            preview_config_hash=preview_config_hash,
        )
        self.save(input_file, new_state)
        return new_state

    def record_ocr_result(
        self,
        input_file: Path,
        state: DocumentState,
        record: OcrResultRecord,
    ) -> DocumentState:
        """Persist OCR output and invalidate downstream stages."""
        new_state = dataclasses.replace(
            state,
            source_hash=record.source_hash,
            preview_img=record.preview_img,
            source_image=record.source_image,
            raw_text=record.raw_text,
            ocr_done=bool(record.raw_text),
            correction_done=False,
            evaluation_done=False,
            correction_invalidated=state.correction_done,
            evaluation_invalidated=state.evaluation_done,
            ocr_prompt=record.ocr_prompt,
            correction_prompt=record.correction_prompt,
            evaluate_prompt=record.evaluate_prompt,
            ocr_config_hash=record.ocr_config_hash,
            correction_config_hash="",
            eval_config_hash="",
            preview_config_hash=record.preview_config_hash,
            correction_text="",
            evaluation_text="",
        )
        self.save(input_file, new_state)
        return new_state

    def record_correction_result(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        correction_text: str,
        correction_prompt: str,
        correction_config_hash: str,
    ) -> DocumentState:
        """Persist correction output and invalidate evaluation output."""
        new_state = dataclasses.replace(
            state,
            correction_done=bool(correction_text),
            correction_invalidated=False,
            correction_text=correction_text,
            correction_prompt=correction_prompt,
            correction_config_hash=correction_config_hash,
            evaluation_done=False,
            evaluation_invalidated=state.evaluation_done,
            eval_config_hash="",
            evaluation_text="",
        )
        self.save(input_file, new_state)
        return new_state

    def record_evaluation_result(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        evaluation_text: str,
        evaluate_prompt: str,
        eval_config_hash: str,
    ) -> DocumentState:
        """Persist evaluation output without changing earlier stages."""
        new_state = dataclasses.replace(
            state,
            evaluation_done=bool(evaluation_text),
            evaluation_invalidated=False,
            evaluation_text=evaluation_text,
            evaluate_prompt=evaluate_prompt,
            eval_config_hash=eval_config_hash,
        )
        self.save(input_file, new_state)
        return new_state

    def record_manual_ocr_edit(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        raw_text: str,
        preview_img: str,
        ocr_config_hash: str = "",
    ) -> DocumentState:
        """Persist a manual OCR-text edit and invalidate downstream stages.

        *ocr_config_hash* should be the current expected OCR config hash so the
        edit is treated as fresh by reconciliation; otherwise the next run would
        flag it stale and discard it.
        """
        new_state = dataclasses.replace(
            state,
            preview_img=preview_img,
            source_image=preview_img,
            raw_text=raw_text,
            ocr_done=bool(raw_text),
            ocr_config_hash=ocr_config_hash,
            correction_done=False,
            evaluation_done=False,
            correction_invalidated=state.correction_done,
            evaluation_invalidated=state.evaluation_done,
            correction_config_hash="",
            eval_config_hash="",
            correction_text="",
            evaluation_text="",
        )
        self.save(input_file, new_state)
        return new_state

    def record_manual_correction_edit(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        correction_text: str,
        correction_config_hash: str = "",
    ) -> DocumentState:
        """Persist a manual correction edit and invalidate evaluation output.

        *correction_config_hash* should be the current expected correction config
        hash so the edit is treated as fresh by reconciliation; otherwise the
        next run would flag it stale and discard it.
        """
        new_state = dataclasses.replace(
            state,
            correction_done=bool(correction_text),
            correction_invalidated=False,
            correction_text=correction_text,
            correction_config_hash=correction_config_hash,
            evaluation_done=False,
            evaluation_invalidated=state.evaluation_done,
            eval_config_hash="",
            evaluation_text="",
        )
        self.save(input_file, new_state)
        return new_state

    def reconcile_runtime_config(
        self,
        input_file: Path,
        state: DocumentState,
        snapshot: RuntimeConfigSnapshot,
    ) -> DocumentState:
        """Apply config-driven invalidation and persist normalized runtime metadata."""
        state = dataclasses.replace(state, source_hash=snapshot.source_hash)

        stale_stage = first_stale_stage_for_hashes(
            StageHashes(
                ocr_done=state.ocr_done,
                ocr_config_hash=state.ocr_config_hash,
                expected_ocr_config_hash=snapshot.ocr_config_hash,
                correction_done=state.correction_done,
                correction_config_hash=state.correction_config_hash,
                expected_correction_config_hash=snapshot.correction_config_hash,
                evaluation_done=state.evaluation_done,
                eval_config_hash=state.eval_config_hash,
                expected_eval_config_hash=snapshot.eval_config_hash,
            )
        )
        if stale_stage is not None:
            state = self.reset_from_stage(input_file, state, stale_stage)

        if should_invalidate_preview_artifact(
            current_preview_config_hash=state.preview_config_hash,
            expected_preview_config_hash=snapshot.preview_config_hash,
            has_preview_artifact=bool(state.preview_img or state.source_image),
        ):
            state = self.invalidate_preview_artifact(input_file, state)

        final_state = dataclasses.replace(
            state,
            ocr_prompt=snapshot.ocr_prompt,
            correction_prompt=snapshot.correction_prompt,
            evaluate_prompt=snapshot.evaluate_prompt,
            preview_config_hash=snapshot.preview_config_hash,
        )
        self.save(input_file, final_state)
        return final_state

    def invalidate_preview_artifact(self, input_file: Path, state: DocumentState) -> DocumentState:
        """Clear preview artifact paths while keeping textual stage data intact."""
        new_state = dataclasses.replace(
            state,
            preview_img="",
            source_image="",
            preview_config_hash="",
        )
        self.save(input_file, new_state)
        return new_state

    def reset_from_stage(
        self,
        input_file: Path,
        state: DocumentState,
        stage: str,
    ) -> DocumentState:
        """Reset a loaded state from *stage* onward and persist the change.

        Note: artifact files on disk are *not* deleted here.  Callers that hold
        a :class:`~infrastructure.preview_image_store.PreviewImageStore` must
        call :meth:`~PreviewImageStore.delete_preview_artifacts` themselves when
        ``policy.clear_preview_artifacts`` is ``True``.
        """
        stage_name = normalize_stage(stage)
        policy = reset_policy_from_stage(stage_name)

        updates: dict = {}
        if policy.clear_ocr:
            updates.update(
                ocr_done=False,
                ocr_config_hash="",
                preview_config_hash="",
                preview_img="",
                source_image="",
                raw_text="",
            )
        if policy.clear_correction:
            updates.update(
                correction_done=False,
                correction_invalidated=False,
                correction_config_hash="",
                correction_text="",
            )
        if policy.clear_evaluation:
            updates.update(
                evaluation_done=False,
                evaluation_invalidated=False,
                eval_config_hash="",
                evaluation_text="",
            )

        new_state = dataclasses.replace(state, **updates)
        self.save(input_file, new_state)
        return new_state

    def load_valid(self, input_file: Path) -> DocumentState | None:
        state = self.load(input_file)
        if state is None or state.source_path != str(input_file.resolve()):
            return None
        try:
            if state.source_hash != compute_file_hash(input_file):
                return None
        except OSError:
            return None

        if state.ocr_done and not state.raw_text:
            return None
        if state.correction_done and not state.correction_text:
            return None
        if state.evaluation_done and not state.evaluation_text:
            return None
        # Consistency: downstream stages cannot be done if their prerequisite isn't.
        if state.correction_done and not state.ocr_done:
            return None
        if state.evaluation_done and not state.ocr_done:
            return None

        preview_img = (
            state.preview_img if not state.preview_img or Path(state.preview_img).exists() else ""
        )
        source_image = (
            state.source_image
            if not state.source_image or Path(state.source_image).exists()
            else ""
        )
        if not source_image and preview_img:
            source_image = preview_img
        if preview_img != state.preview_img or source_image != state.source_image:
            state = dataclasses.replace(state, preview_img=preview_img, source_image=source_image)
        return state

    def invalidate_from_stage(self, input_file: Path, stage: str) -> DocumentState | None:
        state = self.load_valid(input_file)
        if state is None:
            return None
        return self.reset_from_stage(input_file, state, stage)
