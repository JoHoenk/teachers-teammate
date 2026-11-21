"""What the store says about the teacher's documents: the queue, the Check tab, the work.

One query per question, all of them read-only.  The texts a document has are never copied into
a row: an action that needs them asks for the view, so the queue cannot drift from the store.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
import dataclasses
from dataclasses import dataclass
import functools
from pathlib import Path

from ..config import Config
from ..domain.context_budget import RunWindows
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.state_repository import (
    DocumentState,
    StateRepository,
    compute_file_hash_cached,
    machine_texts,
)
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..infrastructure.workflow.cache_service import (
    ExpectedHashes,
    compute_correction_config_hash,
    expected_hashes,
    privacy_fingerprint,
    stage_hashes,
)
from .check_document import CheckDocument, check_document_of
from .document_view import DocumentStateView, to_view
from .merged_view import merged_view, reconciled_view
from .queue import QueueRow, StoredStages, queue_row


@dataclass(frozen=True)
class WorkedOn:
    """The documents of a run whose corrections or transcription the teacher worked on.

    Worked out before a run starts, so the window can ask what to do with them: a run keeps a
    proofreading the teacher worked on (it is not stale by its settings), and only an explicit
    answer replaces it.
    """

    kept_stale: tuple[Path, ...] = ()
    """The proofreading is stale only by its settings and was worked on: a run keeps it."""
    unanswered: tuple[Path, ...] = ()
    """Of those, the ones not yet answered for these settings: the run-start question lists them."""
    unbound: tuple[Path, ...] = ()
    """Worked-on corrections that belong to no stored proofreading: a new one will not have them."""
    transcribed: tuple[Path, ...] = ()
    """The teacher fixed or checked the transcription: reading the pages again replaces it."""
    worked_on: tuple[Path, ...] = ()
    """The corrections part applies and was worked on: an explicit re-run would drop it."""

    @property
    def empty(self) -> bool:
        """Whether the teacher worked on nothing this run touches."""
        return not (
            self.kept_stale or self.unanswered or self.unbound or self.transcribed or self.worked_on
        )


class DocumentQueries:
    """Read the store: the queue's rows, the Check tab's document, the work a run would touch."""

    def __init__(
        self,
        *,
        state_store_factory: Callable[[Config], StateRepository],
        work_store: TeacherWorkStore,
        model_store: ModelStore,
        windows_for: Callable[[Config], RunWindows],
    ) -> None:
        self._state_store = state_store_factory
        self._work_store = work_store
        self._model_store = model_store
        # The context windows a run would use; the run service owns them.
        self._windows_for = windows_for

    def _documents(
        self, config: Config, files: Iterable[Path], *, reconciled: bool
    ) -> Iterator[tuple[Path, DocumentState | None, DocumentStateView | None]]:
        """Yield ``(file, state, view)`` for every file of *files*.

        The one place a stored document becomes a view.  *reconciled* marks the stages the next
        run would read again, worked out exactly as the run works them out
        (:func:`~teachers_teammate.infrastructure.workflow.cache_service.stage_hashes`), so the
        queue and the next run agree on what is stale and what the teacher's work keeps; without
        it the view says what is stored.  A file the store has no result for yields ``None`` for
        both.
        """
        store = self._state_store(config)
        windows = self._windows_for(config) if reconciled else None
        current_privacy = functools.cache(lambda: privacy_fingerprint(config))
        for file in files:
            state = store.load_valid(file)
            if state is None:
                yield file, None, None
                continue
            if reconciled:
                view = reconciled_view(
                    file,
                    state,
                    self._work_store.load(state.source_hash),
                    expected_hashes(config, file, windows, self._model_store),
                )
            else:
                view = self._merged(to_view(file, state), state)
            yield file, state, self._sent_verdict(view, current_privacy)

    def queue_rows(self, config: Config, files: Sequence[Path]) -> list[QueueRow]:
        """Return what the queue shows for *files*, read from the store.

        One row per file, in the order given: the stage states the next run would find, and
        what the document has.  The texts themselves are not part of a row; an action that
        needs them reads the document (:meth:`load_view`), so the queue holds no copy that
        could drift from the store.
        """
        rows: list[QueueRow] = []
        for file, state, view in self._documents(config, files, reconciled=True):
            if state is None or view is None:
                rows.append(QueueRow.empty(file))
                continue
            stored = StoredStages(
                ocr=state.ocr_done,
                correction=state.correction_done,
                evaluation=state.evaluation_done,
            )
            rows.append(queue_row(view, stored))
        return rows

    def queue_row(self, config: Config, source: Path) -> QueueRow:
        """Return what the queue shows for one file, read from the store."""
        return self.queue_rows(config, [source])[0]

    def load_view(self, config: Config, source: Path) -> DocumentStateView | None:
        """Return the view of *source*, with the texts the document has now."""
        for _file, _state, view in self._documents(config, [source], reconciled=False):
            return view
        return None

    def check_document(self, config: Config, source: Path) -> CheckDocument | None:
        """Return everything the Check tab shows about *source*, or ``None`` without state.

        One query for the whole tab: the texts, the review aligned with them, the items the list
        numbers, the pages of the scan and how the results were sent.  The window asks for it
        once per document and once per stored edit, so nothing else assembles that state.
        """
        view = self.load_view(config, source)
        if view is None:
            return None
        return check_document_of(view)

    def recognised_files(self, config: Config, files: Collection[Path]) -> set[Path]:
        """Return the *files* whose recognised text is already stored (best effort)."""
        try:
            store = self._state_store(config)
            states = [(file, store.load_valid(file)) for file in files]
        except Exception:  # noqa: BLE001  # a lookup that fails only means "read it again"
            return set()
        return {file for file, state in states if state is not None and state.ocr_done}

    def worked_on_documents(
        self, config: Config, files: Collection[Path], *, run_proofreads: bool
    ) -> WorkedOn:
        """Return the documents of a run whose corrections or transcription the teacher worked on.

        Reads every file that was not read since it last changed, so call it from a background
        thread.  *run_proofreads* says whether the run makes a proofreading at all: without
        one it replaces nothing, so no stored proofreading needs a question and corrections that
        belong to no stored answer are not worth one.
        """
        from ..domain.freshness import correction_kept_over_settings  # noqa: PLC0415
        from ..domain.teacher_work import current_texts  # noqa: PLC0415

        store = self._state_store(config)
        expected_correction_hash = compute_correction_config_hash(config, self._windows_for(config))

        kept_stale: list[Path] = []
        unanswered: list[Path] = []
        unbound: list[Path] = []
        transcribed: list[Path] = []
        worked_on_documents: list[Path] = []
        for file in files:
            try:
                work = self._work_store.load(compute_file_hash_cached(file))
            except OSError:
                continue
            if work is None:
                continue
            if work.transcription is not None:
                transcribed.append(file)
            corrections = work.corrections
            worked_on = corrections is not None and corrections.worked_on
            state = store.load_valid(file)
            if state is None:
                # No stored result: the corrections part applies to nothing.
                if run_proofreads and worked_on:
                    unbound.append(file)
                continue
            # Only the proofreading's freshness is asked about, so the other two hashes are
            # the stored ones: they are never different, and the file is not read for them.
            flags = stage_hashes(
                state,
                work,
                ExpectedHashes(
                    ocr_config_hash=state.ocr_config_hash,
                    correction_config_hash=expected_correction_hash,
                    eval_config_hash=state.eval_config_hash,
                    correction_enabled=config.correction_enabled,
                    evaluation_enabled=config.evaluation_enabled,
                ),
            )
            applies = current_texts(machine_texts(state), work).corrections_work is not None
            if applies and worked_on:
                worked_on_documents.append(file)
            if run_proofreads and correction_kept_over_settings(flags):
                kept_stale.append(file)
                if corrections is not None and corrections.kept_under != expected_correction_hash:
                    unanswered.append(file)
            elif run_proofreads and worked_on and not applies:
                unbound.append(file)
        return WorkedOn(
            kept_stale=tuple(kept_stale),
            unanswered=tuple(unanswered),
            unbound=tuple(unbound),
            transcribed=tuple(transcribed),
            worked_on=tuple(worked_on_documents),
        )

    def _merged(self, view: DocumentStateView, state: DocumentState) -> DocumentStateView:
        """Return *view* with the teacher's texts over the machine's."""
        return merged_view(view, state, self._work_store.load(state.source_hash))

    @staticmethod
    def _sent_verdict(
        view: DocumentStateView, current_privacy: Callable[[], str]
    ) -> DocumentStateView:
        """Add whether each stored result was made with other privacy settings than today's.

        The comparison is by settings, not by text: working out the text would need a spaCy
        pass per document.  So a changed pattern that hides nothing new in a document still
        says "other privacy settings" — the wording says *settings*, not *text*.

        *current_privacy* is called only when the view has a stored result, so a file without
        one costs no fingerprint.
        """
        if not view.sent:
            return view
        current = current_privacy()
        return dataclasses.replace(
            view,
            sent={
                stage: dataclasses.replace(
                    status, other_privacy_settings=status.record.privacy != current
                )
                for stage, status in view.sent.items()
            },
        )
