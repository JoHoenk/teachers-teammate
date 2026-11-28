"""What the store says about the teacher's documents: the queue, the Check tab, the work.

One query per question, all of them read-only.  The texts a document has are never copied into
a row: an action that needs them asks for the view, so the queue cannot drift from the store.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Iterator, Sequence
import dataclasses
from dataclasses import dataclass, replace
import functools
import logging
from pathlib import Path

from ..cache_keys import correction_key, expected_hashes, privacy_key
from ..config import Config
from ..domain.context_budget import RunWindows
from ..domain.correction_policy import DEFAULT_LEVEL
from ..domain.correction_prompts import notes_block, parameters
from ..domain.document_state import DocumentState, machine_texts
from ..domain.formatted_text import TextFormat, normalize_text_format
from ..domain.freshness import (
    ExpectedHashes,
    correction_kept_over_settings,
    first_stale_stage_for_hashes,
    stage_hashes,
)
from ..domain.key_facts import KeyFacts
from ..domain.review_prompts import review_system
from ..domain.stages import Stage
from ..domain.teacher_work import current_texts
from ..infrastructure.state_repository import StateRepository, compute_file_hash_cached
from ..infrastructure.teacher_work_store import TeacherWorkStore
from ..settings_schema import DEFAULTS
from .anonymization import AnonymizationService
from .check_document import CheckDocument, check_document_of
from .correction_options import proofreading_prompts
from .data_flow import DataFlowService
from .document_view import DocumentStateView, to_view
from .merged_view import merged_view, reconciled_view
from .queue import QueueRow, StoredStages, queue_row
from .sent_text import (
    Anonymize,
    CorrectionInstructions,
    SentTexts,
    describe_sent_texts,
)

_logger = logging.getLogger(__name__)


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
        facts: KeyFacts,
        windows_for: Callable[[Config], RunWindows],
        data_flow: DataFlowService,
        anonymization: AnonymizationService,
    ) -> None:
        self._state_store = state_store_factory
        self._work_store = work_store
        self._facts = facts
        # The context windows a run would use; the run service owns them.
        self._windows_for = windows_for
        # Where each step takes place, and the shared anonymizer used to show what is sent.
        self._data_flow = data_flow
        self._anonymization = anonymization

    def _documents(
        self, config: Config, files: Iterable[Path], *, reconciled: bool
    ) -> Iterator[tuple[Path, DocumentState | None, DocumentStateView | None]]:
        """Yield ``(file, state, view)`` for every file of *files*.

        The one place a stored document becomes a view.  *reconciled* marks the stages the next
        run would read again, worked out exactly as the run works them out
        (:func:`~teachers_teammate.domain.freshness.stage_hashes`), so the
        queue and the next run agree on what is stale and what the teacher's work keeps; without
        it the view says what is stored.  A file the store has no result for yields ``None`` for
        both.
        """
        store = self._state_store(config)
        windows = self._windows_for(config) if reconciled else None
        current_privacy = functools.cache(lambda: privacy_key(config, self._facts))
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
                    expected_hashes(config, file, windows, self._facts),
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

    def load_view(self, config: Config, source: Path) -> DocumentStateView | None:
        """Return the view of *source*, with the texts the document has now."""
        for _file, _state, view in self._documents(config, [source], reconciled=False):
            return view
        return None

    def check_document(self, config: Config, source: Path) -> CheckDocument | None:
        """Return everything the Check tab shows about *source*, or ``None`` without state.

        One query for the whole tab: the texts, the review aligned with them, the items the list
        numbers, the pages of the scan and how the results were sent.  The window asks for it
        once per document and once per stored edit, so nothing else assembles that state.  With
        ``scan_marks`` off the scan marks nothing, whatever places the reading kept.
        """
        view = self.load_view(config, source)
        if view is None:
            return None
        if not config.scan_marks:
            view = replace(view, raw_layout=())
        return check_document_of(view)

    def recognised_files(self, config: Config, files: Collection[Path]) -> set[Path]:
        """Return the *files* whose stored text the next run would use instead of reading again.

        A reading made with other settings, or by an older reader, is not among them: the run
        reads that document again.  Best effort: a lookup that fails means "read it again".
        """
        try:
            store = self._state_store(config)
            windows = self._windows_for(config)
            found: set[Path] = set()
            for file in files:
                state = store.load_valid(file)
                if state is None or not state.ocr_done:
                    continue
                flags = stage_hashes(
                    state,
                    self._work_store.load(state.source_hash),
                    expected_hashes(config, file, windows, self._facts),
                )
                if first_stale_stage_for_hashes(flags) is not Stage.OCR:
                    found.add(file)
        except Exception:  # noqa: BLE001  # a lookup that fails only means "read it again"
            return set()
        return found

    def with_stored_transcription(self, files: Collection[Path]) -> set[Path]:
        """Return the *files* whose transcription the teacher stored: they need no reading."""
        found: set[Path] = set()
        for file in files:
            try:
                work = self._work_store.load(compute_file_hash_cached(file))
            except OSError:
                _logger.debug("Cannot read %s: no stored transcription to find", file.name)
                continue
            if work is not None and work.transcription is not None:
                found.add(file)
        return found

    def worked_on_documents(
        self, config: Config, files: Collection[Path], *, run_proofreads: bool
    ) -> WorkedOn:
        """Return the documents of a run whose corrections or transcription the teacher worked on.

        Reads every file that was not read since it last changed, so call it from a background
        thread.  *run_proofreads* says whether the run makes a proofreading at all: without
        one it replaces nothing, so no stored proofreading needs a question and corrections that
        belong to no stored answer are not worth one.
        """

        store = self._state_store(config)
        expected_correction_hash = correction_key(config, self._windows_for(config), self._facts)

        kept_stale: list[Path] = []
        unanswered: list[Path] = []
        unbound: list[Path] = []
        transcribed: list[Path] = []
        worked_on_documents: list[Path] = []
        for file in files:
            try:
                work = self._work_store.load(compute_file_hash_cached(file))
            except OSError:
                _logger.debug("Cannot read %s: nothing to ask about its work", file.name)
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

    def describe_sent_texts(self, config: Config, view: DocumentStateView) -> SentTexts:
        """Return what every step that leaves this computer receives, exactly as sent.

        Uses the shared anonymizer (and so its cache), so opening the dialog costs one spaCy
        pass per text and settings for the whole session — the same result a run sends.  The
        prompts are built with the code the stages use, so the dialog cannot drift from them.

        Args:
            config: The live settings; decides the steps and the prompts.
            view: The document as it is now: the texts a run would send, and how the results
                that are already stored were sent, so the dialog can say what really went out
                next to what would go next.
        """
        flow = self._data_flow.describe_data_flow(config)
        return describe_sent_texts(
            flow,
            raw_text=view.raw_text,
            correction_text=view.correction_text,
            anonymize=self._sent_text_anonymizer(config),
            preview_extracted=config.anonymization_enabled,
            correction=self._sent_correction(config),
            evaluation_prompt=self._sent_evaluation_prompt(
                config, normalize_text_format(view.raw_text_format)
            ),
            stored=view.sent,
        )

    def _sent_text_anonymizer(self, config: Config) -> Anonymize | None:
        """Return the anonymizer for this config — the shared, caching one — or ``None``.

        ``None`` when personal-info removal is off: nothing is hidden then, so the dialog
        must show the text as written.
        """

        if not config.anonymization_enabled:
            return None
        anon_config = config.anonymizer_config
        language = config.language
        return lambda text: self._anonymization.anonymize_text(text, language, anon_config)

    def _sent_correction(self, config: Config) -> CorrectionInstructions:
        """Return what the proofreading requests are told besides the text.

        The prompts come from the exchange and the lines from the functions that build the user
        message, so the dialog shows what a run really sends: the prompts are fixed, and the
        languages, the level and the notes are the rest of what the model is told.
        """
        lines = parameters(
            config.language,
            level=config.correction_level or DEFAULT_LEVEL,
            explanation_language=config.explanation_language,
        )
        return CorrectionInstructions(
            prompts=proofreading_prompts(config),
            notes=f"{lines}{notes_block(config.correction_notes)}",
        )

    def _sent_evaluation_prompt(self, config: Config, text_format: TextFormat) -> str:
        """Return the instructions the evaluator sends, or ``""`` when it sends none."""

        prompt = config.evaluate_prompt or DEFAULTS["evaluate_prompt"]
        return review_system(prompt, text_format)

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
