"""Per-file processing state persistence.

The JSON-backed key-value store that tracks what has been computed for each source document: it
loads, validates, saves and invalidates the records, and turns them into JSON and back.

What this module does NOT do
-----------------------------
- It does not define what a record holds — that is ``domain/document_state.py``.
- It does not decide *when* to invalidate stages — that is `domain/freshness.py`.
- It does not manage preview image paths — that is `PreviewImageStore`.
- It does not select the storage root directory — that is `storage_root.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from dataclasses import asdict, dataclass
import hashlib
import json
import logging
from pathlib import Path
import threading

from ..atomic_file import write_text_atomic
from ..domain.data_location import SentRecord
from ..domain.document_state import (
    DocumentState,
    cleared_fields,
    cleared_preview_fields,
    reset_fields,
)
from ..domain.freshness import should_invalidate_preview_artifact
from ..domain.stages import Stage, normalize_stage
from .storage_root import document_identity, document_stem

_logger = logging.getLogger(__name__)


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


#: The hash of a file that did not change, by what ``stat`` says about it.  The window asks
#: for the hash of the open document on every card it shows and every mark it sets; reading
#: the file each time would be work for the GUI thread to do over and over.
_HASH_MEMO: dict[tuple[str, int, int, int], str] = {}
_HASH_MEMO_LIMIT = 512
_HASH_MEMO_LOCK = threading.Lock()


def compute_file_hash_cached(path: Path) -> str:
    """Return :func:`compute_file_hash` of *path*, read again only when the file changed.

    "Changed" is the file's size, modification time and identity: a file replaced by one of
    the same size within the clock's resolution is not noticed, which is why a run (the code
    that decides what a result belongs to) always reads the file with
    :func:`compute_file_hash`.
    """
    info = path.stat()
    key = (str(path.resolve()), info.st_size, info.st_mtime_ns, info.st_ino)
    with _HASH_MEMO_LOCK:
        known = _HASH_MEMO.get(key)
    if known is not None:
        return known
    digest = compute_file_hash(path)
    with _HASH_MEMO_LOCK:
        if len(_HASH_MEMO) >= _HASH_MEMO_LIMIT:
            _HASH_MEMO.clear()
        _HASH_MEMO[key] = digest
    return digest


@dataclass(frozen=True)
class OcrResultRecord:
    """OCR output payload persisted by `StateRepository.record_ocr_result`."""

    source_hash: str
    raw_text: str
    preview_img: str
    source_image: str
    ocr_config_hash: str
    preview_config_hash: str
    raw_text_format: str = "plain"


def _inconsistent(state: DocumentState) -> bool:
    """Return whether *state* contradicts itself, so it must not be served as a result.

    A stage marked done without its text, or a downstream stage done without its prerequisite:
    each means the file has to be read (and sent) again rather than shown as finished.  A
    record of what was sent without its result is *not* in this list: it is only dropped
    (:func:`_without_orphan_sent`), because the expensive text recognition is still good.
    """
    if state.ocr_done and not state.raw_text:
        return True
    if state.correction_done and not state.correction_text:
        return True
    if state.evaluation_done and not state.evaluation_text:
        return True
    # Downstream stages cannot be done if their prerequisite isn't.
    return (state.correction_done or state.evaluation_done) and not state.ocr_done


def _is_current(state: DocumentState, input_file: Path) -> bool:
    """Return whether *state* was stored for exactly this file, as it is now."""
    if state.source_path != document_identity(input_file):
        return False
    try:
        return state.source_hash == compute_file_hash_cached(input_file)
    except OSError as exc:
        _logger.debug("Cannot hash %s, no state matches it: %s", input_file, exc)
        return False


def _repaired_paths(state: DocumentState) -> DocumentState:
    """Return *state* with the previews that are no longer on disk dropped.

    The pages, the source image and the originals describe the preview: a path that is gone
    says nothing, so it is dropped, and *state* itself comes back when every path is there.
    """
    pages = _pages_still_there(state.page_images)
    source = _source_still_there(state.source_image, pages)
    # The originals are only worth listing for a preview that is there to compare them with.
    originals = _pages_still_there(state.original_images) if pages else []
    steps = state.preprocess_steps if pages else None
    if (pages, source, originals, steps) == (
        state.page_images,
        state.source_image,
        state.original_images,
        state.preprocess_steps,
    ):
        return state
    return dataclasses.replace(
        state,
        page_images=pages,
        source_image=source,
        original_images=originals,
        preprocess_steps=steps,
    )


def _pages_still_there(pages: Sequence[str]) -> list[str]:
    """Return the pages a teacher can still see: all of them, the first one, or none."""
    if pages and all(Path(page).exists() for page in pages):
        return list(pages)
    if pages and Path(pages[0]).exists():
        return [pages[0]]
    return []


def _source_still_there(source: str, pages: list[str]) -> str:
    """Return what the preview shows: the source image when it is there, else the first page."""
    if source and Path(source).exists():
        return source
    return pages[0] if pages else ""


def _without_orphan_sent(state: DocumentState) -> DocumentState:
    """Return *state* without the sent text and records whose result is not stored.

    The text that was sent and the record of how it was sent belong to their result.  Without
    it they say nothing true, so they are dropped on load; the recognised text next to them
    stays valid and is not read (or sent) again.
    """
    updates: dict[str, object] = {}
    if not state.correction_done:
        if state.correction_sent_text:
            updates["correction_sent_text"] = ""
        if state.correction_sent_map is not None:
            updates["correction_sent_map"] = None
        if state.correction_sent is not None:
            updates["correction_sent"] = None
    if not state.evaluation_done and state.evaluation_sent is not None:
        updates["evaluation_sent"] = None
    return dataclasses.replace(state, **updates) if updates else state


class StateRepository:
    """Persistence service for loading, validating, and invalidating state records."""

    _SCHEMA_VERSION = 3

    def __init__(self, state_root: Path) -> None:
        self._state_root = state_root
        self._state_root.mkdir(parents=True, exist_ok=True)

    def state_path_for_input(self, input_file: Path) -> Path:
        return self._state_root / f"{document_stem(input_file)}.json"

    def create(self, input_file: Path, source_hash: str) -> DocumentState:
        """Create a new empty state record for *input_file*."""
        return DocumentState(
            schema_version=self._SCHEMA_VERSION,
            source_path=document_identity(input_file),
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
            known = {field.name for field in dataclasses.fields(DocumentState)}
            # Fields dropped by newer versions are ignored rather than discarding the cache.
            state = DocumentState(**{key: value for key, value in raw.items() if key in known})
            # A record written before the app kept every page has one page and one original:
            # fold them into the lists the rest of the app reads.
            if not state.page_images and raw.get("preview_img"):
                state = dataclasses.replace(state, page_images=[raw["preview_img"]])
            if not state.original_images and raw.get("original_img"):
                state = dataclasses.replace(state, original_images=[raw["original_img"]])
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            _logger.warning("Cannot read %s, the document is processed from scratch: %s", path, exc)
            return None
        if state.schema_version != self._SCHEMA_VERSION:
            return None
        if "raw_text_format" not in raw and self._is_unrecognised_markup(state.raw_text):
            # Written before notations were tracked: this OCR text is raw model markup that
            # would be shown (and corrected) as literal text.  Treat it as missing so the
            # document is re-processed; plain-looking legacy text stays valid.
            return None
        return state

    @staticmethod
    def _is_unrecognised_markup(raw_text: str) -> bool:
        if not raw_text:
            return False
        from .ocr_formats import looks_like_markup  # noqa: PLC0415

        return looks_like_markup(raw_text)

    def save(self, input_file: Path, state: DocumentState) -> None:
        write_text_atomic(
            self.state_path_for_input(input_file), json.dumps(asdict(state), indent=2)
        )

    def record_preview_artifact(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        preview_img: str,
        source_image: str,
        preview_config_hash: str,
        preprocess_steps: Sequence[str] | None = None,
        page_images: Sequence[str] = (),
        original_images: Sequence[str] = (),
    ) -> DocumentState:
        """Persist preview artifact paths without changing downstream stage data.

        *preview_img* is the first of *page_images*: the two cannot disagree.  Without
        *page_images* only *preview_img* is stored as a page.
        """
        pages = list(page_images) or ([preview_img] if preview_img else [])
        new_state = dataclasses.replace(
            state,
            source_image=source_image,
            preview_config_hash=preview_config_hash,
            preprocess_steps=None if preprocess_steps is None else list(preprocess_steps),
            page_images=pages,
            original_images=list(original_images),
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
        pages = list(state.page_images)
        if record.preview_img:
            if pages:
                pages[0] = record.preview_img
            else:
                pages = [record.preview_img]
        new_state = dataclasses.replace(
            state,
            source_hash=record.source_hash,
            source_image=record.source_image,
            raw_text=record.raw_text,
            raw_text_format=record.raw_text_format,
            ocr_done=bool(record.raw_text),
            ocr_config_hash=record.ocr_config_hash,
            preview_config_hash=record.preview_config_hash,
            page_images=pages,
            **cleared_fields(Stage.CORRECTION),
            **cleared_fields(Stage.EVALUATION),
        )
        self.save(input_file, new_state)
        return new_state

    def record_correction_result(  # noqa: PLR0913  # one keyword per fact of the result
        self,
        input_file: Path,
        state: DocumentState,
        *,
        correction_text: str,
        correction_config_hash: str,
        correction_review: dict | None = None,
        correction_warning: str = "",
        sent_text: str = "",
        sent_map: dict[str, str] | None = None,
        sent: SentRecord | None = None,
        source_hash: str = "",
    ) -> DocumentState:
        """Persist correction output and invalidate evaluation output.

        The corrector answers in the notation of the text it was given, so the correction
        shares the state's ``raw_text_format``.  *correction_review* is the review payload of
        a structured proofreading answer, or ``None`` in corrections-only mode;
        *correction_warning* says why a file has no review or an incomplete one.

        *sent_text* and *sent_map* are the text the proofreading was made from and its
        placeholder map (only when names were hidden); *sent* says how it was sent.
        *source_hash* is the ``text_identity`` of the transcription it was made from, so a
        proofreading made from a text the document no longer has is stale.
        """
        new_state = dataclasses.replace(
            state,
            correction_done=bool(correction_text),
            correction_text=correction_text,
            correction_review=correction_review,
            correction_warning=correction_warning,
            correction_sent_text=sent_text,
            correction_sent_map=sent_map,
            correction_sent=sent.to_dict() if sent is not None else None,
            correction_config_hash=correction_config_hash,
            correction_source_hash=source_hash,
            **cleared_fields(Stage.EVALUATION),
        )
        self.save(input_file, new_state)
        return new_state

    def record_evaluation_result(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        evaluation_text: str,
        eval_config_hash: str,
        evaluation_warning: str = "",
        sent: SentRecord | None = None,
    ) -> DocumentState:
        """Persist evaluation output without changing earlier stages.

        *sent* says how the reviewed text was sent; it is stored with the result.
        """
        new_state = dataclasses.replace(
            state,
            evaluation_done=bool(evaluation_text),
            evaluation_text=evaluation_text,
            evaluation_warning=evaluation_warning,
            evaluation_sent=sent.to_dict() if sent is not None else None,
            eval_config_hash=eval_config_hash,
        )
        self.save(input_file, new_state)
        return new_state

    def reconcile(
        self,
        input_file: Path,
        state: DocumentState,
        *,
        source_hash: str,
        stale_stage: Stage | None,
        preview_config_hash: str,
    ) -> DocumentState:
        """Apply the freshness verdict to *state* and save it once.

        *stale_stage* is the earliest stage the current settings read again
        (:func:`~teachers_teammate.domain.freshness.first_stale_stage_for_hashes`), worked out
        by the caller from the teacher's work; *preview_config_hash* is the hash the current
        image preparation asks for.  Both are applied in memory, so one reconcile writes the
        file once.
        """
        updates: dict[str, object] = {"source_hash": source_hash}
        if stale_stage is not None:
            updates.update(reset_fields(stale_stage))
        if should_invalidate_preview_artifact(
            current_preview_config_hash=state.preview_config_hash,
            expected_preview_config_hash=preview_config_hash,
            has_preview_artifact=bool(state.preview_img or state.source_image),
        ):
            updates.update(cleared_preview_fields())
        updates["preview_config_hash"] = preview_config_hash
        new_state = dataclasses.replace(state, **updates)
        self.save(input_file, new_state)
        return new_state

    def reset_from_stage(
        self,
        input_file: Path,
        state: DocumentState,
        stage: Stage | str,
    ) -> DocumentState:
        """Reset a loaded state from *stage* onward and persist the change.

        Note: artifact files on disk are *not* deleted here.  Callers that hold
        a :class:`~infrastructure.preview_image_store.PreviewImageStore` must
        call :meth:`~PreviewImageStore.delete_preview_artifacts` themselves when
        ``policy.clear_preview_artifacts`` is ``True``.
        """
        new_state = dataclasses.replace(state, **reset_fields(normalize_stage(stage)))
        self.save(input_file, new_state)
        return new_state

    def load_valid(self, input_file: Path) -> DocumentState | None:
        """Return the stored state of *input_file*, or ``None`` when it does not describe it."""
        state = self.load(input_file)
        if state is None or not _is_current(state, input_file):
            return None
        if _inconsistent(state):
            return None
        return _repaired_paths(_without_orphan_sent(state))

    def invalidate_from_stage(self, input_file: Path, stage: str) -> DocumentState | None:
        state = self.load_valid(input_file)
        if state is None:
            return None
        return self.reset_from_stage(input_file, state, stage)
