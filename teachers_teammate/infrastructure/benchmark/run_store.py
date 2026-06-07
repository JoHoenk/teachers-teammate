"""Append-only persistence for benchmark OCR runs.

Unlike the pipeline cache (:class:`~teachers_teammate.infrastructure.state_repository.StateRepository`),
which keeps a single *latest* :class:`DocumentState` per ``(output_dir, source path)``
and overwrites it on every run, this store is an **append-only experiment log**:
many timestamped runs accumulate per document so the benchmark app can compare
OCR configurations against each other over time.

Layout (under ``<storage_root>/benchmark/``)::

    <document_hash>/                       # compute_file_hash(document)
        document.json                      # {document_hash, display_name, last_path}
        <run_id>.json                      # one StoredRun, self-describing
        images/<run_id>.png                # preview image for that run

``<run_id>`` is ``<UTC-timestamp>-<ocr_config_hash[:8]>`` — sortable and unique.

The store is content-keyed (by ``document_hash``) and global, independent of any
output directory.  Growth is bounded by :data:`BENCHMARK_KEEP_LAST_N` (oldest
runs evicted on save) plus explicit user deletion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import uuid

from ...config import OcrConfig
from ..storage_root import resolve_storage_root

BENCHMARK_KEEP_LAST_N = 20
_SCHEMA_VERSION = 1
_DOCUMENT_META = "document.json"


@dataclass(frozen=True)
class NewRunRequest:
    """Caller-supplied data for persisting a new run (the store assigns id/timestamp)."""

    document_hash: str
    document_path: str
    display_name: str
    ocr: OcrConfig
    language: str
    ocr_config_hash: str
    raw_text: str
    elapsed_s: float
    preview_src: Path | None = None


@dataclass(frozen=True)
class StoredRun:
    """One persisted OCR run for a document, self-describing for the UI."""

    schema_version: int
    run_id: str
    document_hash: str
    document_path: str
    display_name: str
    ocr_config_hash: str
    ocr: OcrConfig
    language: str
    raw_text: str
    preview_img: str
    timestamp: str
    elapsed_s: float

    def to_json_dict(self) -> dict:
        """Return a JSON-serializable dict (the nested OcrConfig is flattened to a sub-dict)."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "document_hash": self.document_hash,
            "document_path": self.document_path,
            "display_name": self.display_name,
            "ocr_config_hash": self.ocr_config_hash,
            "ocr": {
                "engine": self.ocr.engine,
                "model": self.ocr.model,
                "provider": self.ocr.provider,
                "preprocess_method": self.ocr.preprocess_method,
                "temperature": self.ocr.temperature,
            },
            "language": self.language,
            "raw_text": self.raw_text,
            "preview_img": self.preview_img,
            "timestamp": self.timestamp,
            "elapsed_s": self.elapsed_s,
        }

    @classmethod
    def from_json_dict(cls, raw: dict) -> StoredRun:
        """Reconstruct a StoredRun from a parsed JSON dict."""
        ocr_raw = raw["ocr"]
        return cls(
            schema_version=raw["schema_version"],
            run_id=raw["run_id"],
            document_hash=raw["document_hash"],
            document_path=raw["document_path"],
            display_name=raw["display_name"],
            ocr_config_hash=raw["ocr_config_hash"],
            ocr=OcrConfig(
                engine=ocr_raw["engine"],
                model=ocr_raw["model"],
                provider=ocr_raw["provider"],
                preprocess_method=ocr_raw["preprocess_method"],
                temperature=ocr_raw["temperature"],
            ),
            language=raw["language"],
            raw_text=raw["raw_text"],
            preview_img=raw["preview_img"],
            timestamp=raw["timestamp"],
            elapsed_s=raw["elapsed_s"],
        )

    def config_summary(self) -> str:
        """Return a short human-readable label of the OCR configuration."""
        ocr = self.ocr
        engine = ocr.engine
        if engine == "langchain":
            engine = f"{ocr.provider}"
        model = ocr.effective_model if engine == ocr.provider else ocr.model
        parts = [engine]
        if model:
            parts.append(model)
        parts.append(ocr.preprocess_method)
        return " · ".join(parts)


class BenchmarkRunStore:
    """Filesystem store for append-only benchmark runs."""

    def __init__(self, root: Path | None = None, *, keep_last: int = BENCHMARK_KEEP_LAST_N) -> None:
        self._root = root or (resolve_storage_root().path / "benchmark")
        self._keep_last = keep_last

    @property
    def root(self) -> Path:
        return self._root

    def _doc_dir(self, document_hash: str) -> Path:
        return self._root / document_hash

    def save(self, request: NewRunRequest) -> StoredRun:
        """Persist a new run and return it; never overwrites.

        Copies ``request.preview_src`` (if given and existing) into
        ``images/<run_id>.png``, records the document metadata, then enforces the
        keep-last-N cap.
        """
        doc_dir = self._doc_dir(request.document_hash)
        doc_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(UTC)
        run_id = f"{now.strftime('%Y%m%dT%H%M%S_%f')}-{request.ocr_config_hash[:8]}-{uuid.uuid4().hex[:4]}"

        preview_img = ""
        if request.preview_src is not None and Path(request.preview_src).is_file():
            images_dir = doc_dir / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            target = images_dir / f"{run_id}.png"
            shutil.copy2(request.preview_src, target)
            preview_img = str(target)

        run = StoredRun(
            schema_version=_SCHEMA_VERSION,
            run_id=run_id,
            document_hash=request.document_hash,
            document_path=request.document_path,
            display_name=request.display_name,
            ocr_config_hash=request.ocr_config_hash,
            ocr=request.ocr,
            language=request.language,
            raw_text=request.raw_text,
            preview_img=preview_img,
            timestamp=now.isoformat(),
            elapsed_s=request.elapsed_s,
        )
        (doc_dir / f"{run_id}.json").write_text(
            json.dumps(run.to_json_dict(), indent=2), encoding="utf-8"
        )
        self._write_document_meta(
            request.document_hash, request.document_path, request.display_name
        )
        self._enforce_retention(request.document_hash)
        return run

    def list_for(self, document_hash: str) -> list[StoredRun]:
        """Return all runs for *document_hash*, newest first."""
        doc_dir = self._doc_dir(document_hash)
        if not doc_dir.is_dir():
            return []
        runs: list[StoredRun] = []
        for path in doc_dir.glob("*.json"):
            if path.name == _DOCUMENT_META:
                continue
            run = self._load_run(path)
            if run is not None:
                runs.append(run)
        runs.sort(key=lambda r: r.timestamp, reverse=True)
        return runs

    def list_documents(self) -> list[tuple[str, str]]:
        """Return ``(document_hash, display_name)`` for every document with runs."""
        if not self._root.is_dir():
            return []
        result: list[tuple[str, str]] = []
        for doc_dir in sorted(self._root.iterdir()):
            if not doc_dir.is_dir():
                continue
            meta = doc_dir / _DOCUMENT_META
            display = doc_dir.name
            if meta.is_file():
                try:
                    display = json.loads(meta.read_text(encoding="utf-8")).get(
                        "display_name", doc_dir.name
                    )
                except (OSError, json.JSONDecodeError):
                    pass
            result.append((doc_dir.name, display))
        return result

    def delete(self, document_hash: str, run_id: str) -> None:
        """Remove a single run's JSON and its preview image."""
        doc_dir = self._doc_dir(document_hash)
        (doc_dir / f"{run_id}.json").unlink(missing_ok=True)
        (doc_dir / "images" / f"{run_id}.png").unlink(missing_ok=True)

    def delete_all_for(self, document_hash: str) -> None:
        """Remove the whole document folder (all runs + images + metadata)."""
        shutil.rmtree(self._doc_dir(document_hash), ignore_errors=True)

    # ── internal ──────────────────────────────────────────────────────────

    def _load_run(self, path: Path) -> StoredRun | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            run = StoredRun.from_json_dict(raw)
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return None
        if run.schema_version != _SCHEMA_VERSION:
            return None
        return run

    def _write_document_meta(
        self, document_hash: str, document_path: str, display_name: str
    ) -> None:
        meta = {
            "document_hash": document_hash,
            "document_path": document_path,
            "display_name": display_name,
        }
        (self._doc_dir(document_hash) / _DOCUMENT_META).write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

    def _enforce_retention(self, document_hash: str) -> None:
        runs = self.list_for(document_hash)  # newest first
        for stale in runs[self._keep_last :]:
            self.delete(document_hash, stale.run_id)
