"""The teacher's writes: their transcription, their corrections, their marks.

Two stores, two owners.  The cache holds the machine's results and is written by the run; the
teacher's work holds what they changed and decided, and is written here — the window is its only
writer.  So an edit no longer overwrites a machine text: it stores the teacher's own, and every
reader merges the two (:func:`~teachers_teammate.application.merged_view.merged_view`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
import logging
from pathlib import Path

from ..config import Config
from ..domain.context_budget import RunWindows
from ..domain.formatted_text import normalize_text_format
from ..domain.stages import Stage, normalize_stage
from ..domain.teacher_work import (
    TeacherWork,
    current_texts,
    kept_over,
    with_checked,
    with_corrected,
    with_looked_at,
    with_transcription,
    without_corrections,
    without_work_for_reading,
)
from ..infrastructure.onnx.store import ModelStore
from ..infrastructure.stage_builder import resolve_run_windows
from ..infrastructure.state_repository import (
    StateRepository,
    compute_file_hash_cached,
    machine_texts,
)
from ..infrastructure.teacher_work_store import TeacherWorkStore, WorkTooNewError
from ..infrastructure.workflow.cache_service import (
    ExpectedHashes,
    compute_correction_config_hash,
    expected_hashes,
)
from .document_view import DocumentStateView
from .merged_view import reconciled_view

_logger = logging.getLogger(__name__)


class WorkNotSavedError(Exception):
    """The teacher's work could not be stored, so what they just did is not kept.

    *too_new* says why: the file was written by a newer version of the app, which this one
    leaves alone; otherwise the file could not be read or written.
    """

    def __init__(self, name: str, *, too_new: bool) -> None:
        super().__init__(name)
        self.name = name
        self.too_new = too_new


#: What an explicit re-run of a stage takes with it from the teacher's work.
_PART_DROP: dict[Stage, Callable[[TeacherWork], TeacherWork]] = {
    Stage.CORRECTION: without_corrections,
    Stage.OCR: without_work_for_reading,
}


class DocumentCommands:
    """The write operations of the window: the teacher's work, and the cache's stages."""

    def __init__(
        self,
        *,
        state_store_factory: Callable[[Config], StateRepository],
        windows_for: Callable[[Config], RunWindows] | None = None,
        model_store: ModelStore | None = None,
        work_store: TeacherWorkStore | None = None,
    ) -> None:
        self._state_store_factory = state_store_factory
        #: The context windows a run with a config uses on this computer, so a hash written
        #: here matches the one the run writes.  Without it only the settings are known.
        self._windows_for = windows_for or (lambda config: resolve_run_windows(config, None))
        #: Where the page models are: the automatic preparation's cache key depends on them.
        self._model_store = model_store
        #: The teacher's work: the same store the runs read, so both see one folder.
        self._work_store = work_store or TeacherWorkStore()

    # ── The teacher's texts and decisions ─────────────────────────────────

    def edit_transcription(
        self, config: Config, *, source: Path, text: str, text_format: str = "plain"
    ) -> DocumentStateView | None:
        """Store *text* as the teacher's transcription of *source*.

        A proofreading is made from the transcription, so storing a new one resets the cache's
        correction stage: the answer no longer applies to the text the document has.  The work
        is stored first: if that fails nothing is reset, and if the reset fails the stored
        proofreading is still not taken for current (it was made from another text).

        Returns:
            The document as it is now, or ``None`` when it has no state.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        store = self._state_store_factory(config)
        state = store.load_valid(source)
        if state is None:
            return None
        machine = machine_texts(state)
        work = self._update(
            state.source_hash,
            source.name,
            lambda work: with_transcription(
                work, text, normalize_text_format(text_format), machine
            ),
        )
        if state.correction_done:
            try:
                state = store.reset_from_stage(source, state, Stage.CORRECTION)
            except OSError as exc:
                _logger.warning("Could not reset the proofreading of %s: %s", source.name, exc)
        return reconciled_view(source, state, work, self._expected(config, source))

    def edit_corrected_text(
        self, config: Config, *, source: Path, text: str
    ) -> DocumentStateView | None:
        """Store *text* as the teacher's corrections of *source*.

        The corrections are bound to the stored answer, so a document without one has nothing
        to bind to and the edit is refused.

        Returns:
            The document as it is now, or ``None`` when it has no state or nothing to bind to.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        store = self._state_store_factory(config)
        state = store.load_valid(source)
        if state is None:
            return None
        machine = machine_texts(state)
        if not current_texts(machine, self._work_store.load(state.source_hash)).proofreading_id:
            return None
        work = self._update(
            state.source_hash,
            source.name,
            lambda work: with_corrected(work, text, current_texts(machine, work)),
        )
        return reconciled_view(source, state, work, self._expected(config, source))

    def set_checked(
        self, config: Config, *, source: Path, checked: bool
    ) -> DocumentStateView | None:
        """Mark *source* as checked, or take the mark back.

        *Checked* marks what the document has now: with a proofreading it is the corrections
        that are checked, without one the transcription — which is then stored as a copy, so
        the check names the exact text it was given.

        Returns:
            The document as it is now, or ``None`` when it has no state.

        Raises:
            WorkNotSavedError: the work could not be stored.
        """
        store = self._state_store_factory(config)
        state = store.load_valid(source)
        if state is None:
            return None
        machine = machine_texts(state)
        work = self._update(
            state.source_hash,
            source.name,
            lambda work: with_checked(work, checked, current_texts(machine, work)),
        )
        return reconciled_view(source, state, work, self._expected(config, source))

    def mark_looked_at(self, config: Config, *, source: Path, keys: Collection[str]) -> None:
        """Record that the teacher looked at the items *keys* of *source*.

        Stored at once (a few KB, written atomically) and never undone.  Looking at an item is
        not work a run has to keep, so it does not make the answer one to keep.  A failure is
        logged and not reported: nothing the teacher did is lost, the item is just not counted.
        """
        store = self._state_store_factory(config)
        state = store.load_valid(source)
        if state is None:
            return
        machine = machine_texts(state)
        try:
            self._update(
                state.source_hash,
                source.name,
                lambda work: with_looked_at(work, keys, current_texts(machine, work)),
            )
        except WorkNotSavedError:
            pass  # already logged

    def keep_teacher_work(self, config: Config, *, files: Collection[Path]) -> None:
        """Record that the teacher keeps their corrections over the settings of this run.

        The answer is stamped with the correction hash it was kept over, so the same settings
        never ask again.  A document whose work could not be stored is asked about again.
        """
        expected = compute_correction_config_hash(config, self._windows_for(config))
        store = self._state_store_factory(config)
        for file in files:
            state = store.load_valid(file)
            if state is None:
                continue
            machine = machine_texts(state)
            try:
                self._update(
                    state.source_hash,
                    file.name,
                    lambda work, machine=machine: kept_over(
                        work, expected, current_texts(machine, work)
                    ),
                )
            except WorkNotSavedError:
                pass  # already logged

    def drop_unbound_corrections(self, config: Config, *, files: Collection[Path]) -> None:
        """Drop the corrections of *files* that belong to no stored proofreading.

        The teacher was told that a new proofreading will not contain them and went on, so they
        are not something to ask about again.  A part that does apply is left alone.
        """
        store = self._state_store_factory(config)
        for file in files:
            try:
                source_hash = compute_file_hash_cached(file)
            except OSError:
                continue
            state = store.load_valid(file)
            machine = machine_texts(state) if state is not None else None

            def change(work: TeacherWork, machine=machine) -> TeacherWork:
                applies = (
                    machine is not None
                    and current_texts(machine, work).corrections_work is not None
                )
                return work if applies else without_corrections(work)

            try:
                self._work_store.update(source_hash, file.name, change)
            except (WorkTooNewError, OSError) as exc:
                _logger.warning("Could not drop the corrections of %s: %s", file.name, exc)

    # ── The cache's stages ────────────────────────────────────────────────

    def invalidate_from_stage(
        self,
        config: Config,
        *,
        files: Sequence[Path],
        stage: str,
    ) -> tuple[str, ...]:
        """Invalidate the state of *files* from *stage* onwards; return what could not be reset.

        The caller names the files: the run that follows processes exactly these, so the
        invalidation cannot reach a document the teacher did not pick.  Each failure names
        its file and says why.
        """
        return self._invalidate_from_stage(
            stage=stage,
            files=files,
            store=self._state_store_factory(config),
            work_store=self._work_store,
        )

    # ── Private helpers ───────────────────────────────────────────────────

    def _update(
        self,
        source_hash: str,
        name: str,
        change: Callable[[TeacherWork], TeacherWork],
    ) -> TeacherWork:
        """Apply *change* to the work of *source_hash*.

        Raises:
            WorkNotSavedError: the work could not be stored (logged here).
        """
        try:
            return self._work_store.update(source_hash, name, change)
        except WorkTooNewError as exc:
            _logger.warning("Could not save the teacher's work for %s: %s", name, exc)
            raise WorkNotSavedError(name, too_new=True) from exc
        except OSError as exc:
            _logger.warning("Could not save the teacher's work for %s: %s", name, exc)
            raise WorkNotSavedError(name, too_new=False) from exc

    def _expected(self, config: Config, source: Path) -> ExpectedHashes:
        """Return the stage hashes the settings of *config* ask for, for *source*."""
        return expected_hashes(config, source, self._windows_for(config), self._model_store)

    @staticmethod
    def _invalidate_from_stage(
        *,
        stage: str,
        files: Sequence[Path],
        store: StateRepository,
        work_store: TeacherWorkStore | None = None,
    ) -> tuple[str, ...]:
        stage_name = normalize_stage(stage)
        failures: list[str] = []
        for file in files:
            # The work goes first: it is what the teacher confirmed to lose.  If it cannot be
            # dropped nothing is reset, so the document stays as it was.
            if not DocumentCommands._drop_parts(work_store, file, stage_name):
                failures.append(f"{file.name}: your changes could not be removed")
                continue
            try:
                store.invalidate_from_stage(file, stage_name)
            except OSError as exc:
                failures.append(f"{file.name}: {exc}")

        return tuple(failures)

    @staticmethod
    def _drop_parts(work_store: TeacherWorkStore | None, file: Path, stage: str) -> bool:
        """Drop the parts of the teacher's work that an explicit re-run of *stage* takes.

        Returns:
            ``False`` when the work could not be dropped.
        """
        change = _PART_DROP.get(stage)
        if work_store is None or change is None:
            return True
        try:
            work_store.update(compute_file_hash_cached(file), file.name, change)
        except (WorkTooNewError, OSError) as exc:
            _logger.warning("Could not drop the work of %s: %s", file.name, exc)
            return False
        return True
