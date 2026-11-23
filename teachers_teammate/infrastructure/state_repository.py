"""Per-file processing state persistence.

This module owns the JSON-backed key-value store that tracks what has been
computed for each source document.

Responsibilities
----------------
- Define `DocumentState` — the versioned dataclass stored as JSON on disk.
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

from collections.abc import Mapping, Sequence
import dataclasses
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import threading

from ..atomic_file import write_text_atomic
from ..domain.data_location import SentRecord
from ..domain.formatted_text import normalize_text_format
from ..domain.freshness import reset_policy_from_stage, should_invalidate_preview_artifact
from ..domain.proofreading.review import CorrectionReview
from ..domain.stages import Stage, normalize_stage
from ..domain.teacher_work import MachineTexts
from .storage_root import document_stem


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


def compute_config_hash(*parts: str) -> str:
    """Return SHA-256 hash over ordered configuration parts."""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(part.encode("utf-8"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def machine_texts(state: DocumentState) -> MachineTexts:
    """Return what the cache holds for *state*, as the domain reads it.

    The machine's own texts, untouched by the teacher: the merge point
    (``domain.teacher_work.current_texts``) puts their work over them.
    """
    return MachineTexts(
        raw_text=state.raw_text,
        raw_text_format=normalize_text_format(state.raw_text_format),
        corrected_text=state.correction_text,
        review=state.correction_review,
        correction_source_hash=state.correction_source_hash,
    )


@dataclass(frozen=True)
class DocumentState:
    """Versioned processing state record for one source document."""

    schema_version: int
    source_path: str
    source_hash: str
    source_image: str = ""
    raw_text: str = ""
    correction_text: str = ""
    # Explanations and unclear expressions of the proofreading (``CorrectionReview.to_dict``);
    # empty/None when the file was corrected without a review (corrections-only mode).  The
    # offsets are in the plain text of ``raw_text``, crossed-out words included.
    correction_review: dict | None = None
    # Why ``correction_review`` is missing or incomplete (e.g. the fallback to corrections only,
    # or suggestions that could not be placed); replayed when the cached correction is reused.
    correction_warning: str = ""
    # The text the stored proofreading was made from, with its placeholder map, and how it was
    # sent (``SentRecord.to_dict()``).  Only when names were hidden: with hiding off the text
    # that was sent is ``raw_text`` itself, and an edit of ``raw_text`` clears the result, so
    # the two cannot drift apart.  Stored with the result and never overwritten while it
    # exists, so the window can show what really went out after the settings changed.
    correction_sent_text: str = ""
    correction_sent_map: dict[str, str] | None = None
    correction_sent: dict | None = None
    evaluation_text: str = ""
    # What the content review's run reported, replayed when the cached review is reused.
    evaluation_warning: str = ""
    # How the stored content review was sent (``SentRecord.to_dict()``).  Its text is not
    # stored: the content review is being redesigned, and what it sends is stored with that.
    evaluation_sent: dict | None = None
    # Notation of ``raw_text`` and ``correction_text`` (the corrector answers in the notation
    # it was given): "plain" (verbatim) or "markdown".  The default keeps state written
    # before this field existed rendering as plain text.
    raw_text_format: str = "plain"
    ocr_done: bool = False
    correction_done: bool = False
    evaluation_done: bool = False
    ocr_config_hash: str = ""
    correction_config_hash: str = ""
    # ``text_identity`` of the transcription the stored proofreading was made from.  A stored
    # proofreading is stale when that differs from the current transcription, whatever the
    # settings: it guards against a store emptied on one side only (the teacher's transcription
    # gone while the cache still holds a proofreading made from it).  Empty means "made from
    # ``raw_text``", which is what state written before this field existed holds.
    correction_source_hash: str = ""
    eval_config_hash: str = ""
    preview_config_hash: str = ""
    # Every prepared page of the document, in page order.  The first entry is the preview the
    # window shows; a document read before the app kept more than one page has the one page it
    # had (``load`` folds an old ``preview_img`` into this list).
    page_images: list[str] = field(default_factory=list)
    # The page as it was scanned, for every prepared page, in the same order.  Empty when
    # preparation changed nothing.
    original_images: list[str] = field(default_factory=list)
    # Preprocessing steps applied to produce ``preview_img``.  ``None`` means unknown
    # (state written before this field existed); ``[]`` means none were applied.
    preprocess_steps: list[str] | None = None

    @property
    def review(self) -> CorrectionReview | None:
        """Return the stored proofreading review, or ``None`` when there is none to show."""
        return CorrectionReview.from_dict(self.correction_review)

    @property
    def preview_img(self) -> str:
        """Return the prepared page the window shows: the first of :attr:`page_images`."""
        return self.page_images[0] if self.page_images else ""

    @property
    def original_img(self) -> str:
        """Return the scanned page next to the preview: the first of :attr:`original_images`."""
        return self.original_images[0] if self.original_images else ""

    @property
    def pages(self) -> tuple[str, ...]:
        """Return the stored pages in page order."""
        return tuple(self.page_images)

    @property
    def originals(self) -> tuple[str, ...]:
        """Return the stored original pages in page order."""
        return tuple(self.original_images)


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


#: The fields that describe the preview artifact.  They are cleared together: a page image
#: without its steps, or the other way round, says nothing true.
_PREVIEW_FIELDS: Mapping[str, object] = {
    "page_images": (),
    "original_images": (),
    "source_image": "",
    "preprocess_steps": None,
    "preview_config_hash": "",
}

#: What a reset of each stage clears.  One entry per stage, so a field added to
#: ``DocumentState`` for a stage belongs here — ``tests/unit/test_state_repository.py`` fails
#: on a stage field the table does not name.  ``()`` stands for an empty list;
#: :func:`_fresh` makes it a new one.
_CLEARED: Mapping[Stage, Mapping[str, object]] = {
    Stage.OCR: {
        **_PREVIEW_FIELDS,
        "ocr_done": False,
        "ocr_config_hash": "",
        "raw_text": "",
        "raw_text_format": "plain",
    },
    Stage.CORRECTION: {
        "correction_done": False,
        "correction_config_hash": "",
        "correction_source_hash": "",
        "correction_text": "",
        "correction_review": None,
        "correction_warning": "",
        "correction_sent_text": "",
        "correction_sent_map": None,
        "correction_sent": None,
    },
    Stage.EVALUATION: {
        "evaluation_done": False,
        "eval_config_hash": "",
        "evaluation_text": "",
        "evaluation_warning": "",
        "evaluation_sent": None,
    },
}


def _fresh(fields: Mapping[str, object]) -> dict[str, object]:
    """Return *fields* with a new list for every ``()`` placeholder."""
    return {
        name: list(value) if isinstance(value, tuple) else value for name, value in fields.items()
    }


def _cleared(stage: Stage) -> dict[str, object]:
    """Return the fields a reset of *stage* clears, with fresh values."""
    return _fresh(_CLEARED[stage])


def _cleared_preview() -> dict[str, object]:
    """Return the fields that describe the preview artifact, cleared."""
    return _fresh(_PREVIEW_FIELDS)


def reset_fields(stage: Stage) -> dict[str, object]:
    """Return the fields a reset from *stage* onward clears, with fresh values."""
    policy = reset_policy_from_stage(stage)
    cleared: dict[str, object] = {}
    if policy.clear_ocr:
        cleared.update(_cleared(Stage.OCR))
    if policy.clear_correction:
        cleared.update(_cleared(Stage.CORRECTION))
    if policy.clear_evaluation:
        cleared.update(_cleared(Stage.EVALUATION))
    return cleared


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
            known = {field.name for field in dataclasses.fields(DocumentState)}
            # Fields dropped by newer versions are ignored rather than discarding the cache.
            state = DocumentState(**{key: value for key, value in raw.items() if key in known})
            # A record written before the app kept every page has one page and one original:
            # fold them into the lists the rest of the app reads.
            if not state.page_images and raw.get("preview_img"):
                state = dataclasses.replace(state, page_images=[raw["preview_img"]])
            if not state.original_images and raw.get("original_img"):
                state = dataclasses.replace(state, original_images=[raw["original_img"]])
        except OSError, json.JSONDecodeError, TypeError, ValueError:
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
            **_cleared(Stage.CORRECTION),
            **_cleared(Stage.EVALUATION),
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
            **_cleared(Stage.EVALUATION),
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
            updates.update(_cleared_preview())
        updates["preview_config_hash"] = preview_config_hash
        new_state = dataclasses.replace(state, **updates)
        self.save(input_file, new_state)
        return new_state

    def invalidate_preview_artifact(self, input_file: Path, state: DocumentState) -> DocumentState:
        """Clear preview artifact paths while keeping textual stage data intact."""
        new_state = dataclasses.replace(state, **_cleared_preview())
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
        state = self.load(input_file)
        if state is None or state.source_path != str(input_file.resolve()):
            return None
        try:
            if state.source_hash != compute_file_hash_cached(input_file):
                return None
        except OSError:
            return None

        if _inconsistent(state):
            return None
        state = _without_orphan_sent(state)

        # The pages only describe the preview: without one of them on disk the document has
        # the one page it had.
        page_images = (
            list(state.page_images)
            if state.page_images and all(Path(page).exists() for page in state.page_images)
            else []
        )
        if not page_images and state.page_images and Path(state.page_images[0]).exists():
            page_images = [state.page_images[0]]
        source_image = (
            state.source_image
            if not state.source_image or Path(state.source_image).exists()
            else ""
        )
        if not source_image and page_images:
            source_image = page_images[0]
        # The originals describe the preview too, and they are only worth listing when the
        # first one is there: without it the document has the one original it had.
        original_images = (
            list(state.original_images)
            if page_images
            and state.original_images
            and all(Path(page).exists() for page in state.original_images)
            else []
        )
        if (
            not original_images
            and page_images
            and state.original_images
            and Path(state.original_images[0]).exists()
        ):
            original_images = [state.original_images[0]]
        preprocess_steps = state.preprocess_steps if page_images else None
        if (
            page_images != state.page_images
            or source_image != state.source_image
            or original_images != state.original_images
            or preprocess_steps != state.preprocess_steps
        ):
            state = dataclasses.replace(
                state,
                page_images=page_images,
                source_image=source_image,
                original_images=original_images,
                preprocess_steps=preprocess_steps,
            )
        return state

    def invalidate_from_stage(self, input_file: Path, stage: str) -> DocumentState | None:
        state = self.load_valid(input_file)
        if state is None:
            return None
        return self.reset_from_stage(input_file, state, stage)
