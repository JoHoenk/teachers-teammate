"""Comparing writing models on one text: the text, asking the models, and what they differ in.

The teacher picks a document they have read, ticks two to four writing models and runs them on the
same transcription.  This service is what the window asks: the text and its own proofreading, the
answers of the ticked models, and the comparison of what is stored.  Which models there are to
tick is ``compare_candidates.py``'s.

The comparison is a run of its own kind:

* **One text, one model at a time.**  Each model is asked with the teacher's own settings — level,
  explanation language, corrections only, notes, window, hiding names — and nothing but its
  provider and its model changes, so an answer is what a run with that model would give.  Its key
  is the proofreading key of those settings.
* **Nothing is asked twice.**  A stored answer of those settings costs no request, and neither
  does the document's own proofreading when its key is the model's.  Only *Run again* asks a model
  that answered, and its answer is stored beside the first.
* **A model it loaded is unloaded after it**, so the next one starts cold and the memory is free
  again.  A comparison counts as a run (``RunSession.running``) from its first model to its last,
  so the stored results cannot be deleted while it asks; it refuses to start while one is going.
* **The answer goes into the cache**, under ``<storage root>/compare``: it holds the student's
  text and can be made again by asking the same model.  A model that failed or was stopped stores
  nothing — the row says why, and the teacher can try again.
* **The teacher's verdicts on the changes are their own work**: a right or wrong option holds no
  text and can never be made again, so it lives in the data root (``OptionMarks``) and not in the
  cache with the answers it judges.

The teacher's own settings are written by the window, not here: Settings → AI models owns
``correction_provider`` and ``correction_model``, and a service that wrote them would be a second
owner of a page it does not show.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
import dataclasses
from dataclasses import dataclass
import logging
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING

from .. import __version__
from ..cache_keys import sent_record, with_task_text
from ..config import Config
from ..domain.correction_policy import CorrectionOutput
from ..domain.data_location import StageLocation, is_ollama
from ..domain.formatted_text import TextFormat, normalize_text_format
from ..domain.model_fit import Suitability
from ..domain.performance_estimate import TimeRange
from ..domain.proofreading.compared_result import ComparedResult
from ..domain.proofreading.model_comparison import Comparison, Option, Version, compare
from ..domain.proofreading.option_marks import OptionMark, Verdict, identity
from ..domain.proofreading.text_loss import words as count_words
from ..domain.stages import Stage
from ..domain.task_text import passage_parts
from ..domain.teacher_work import text_identity
from ..exceptions import RunStoppedError, StageError
from ..interfaces import LoggingReporter, Reporter
from ..messages import Message, detail, log_failure, message_of, model_not_compared
from .document_view import DocumentStateView
from .formatting import TextFormatter
from .ports import (
    CompareResults,
    OllamaServer,
    OptionMarks,
    ProofreadAnswer,
    ProofreadUnitBuilder,
    RunSession,
)
from .stored_results import RunInProgressError

if TYPE_CHECKING:
    from ..domain.key_facts import KeyFacts

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Asked:
    """What asking one model came to: its answer, how long it took, and which run it is."""

    answer: ProofreadAnswer
    seconds: float
    repeat: int


def _version_of(runs: Sequence[ComparedResult]) -> Version:
    """Return one model's stored answers as one version: the first, and its further runs."""
    first, *rest = sorted(runs, key=lambda result: result.repeat)
    return dataclasses.replace(first.version(), repeats=tuple(one.text for one in rest))


@dataclass(frozen=True)
class CompareSource:
    """The document a comparison is about: the text a run would send, and its own answer."""

    text: str
    """The transcription as stored, in its own notation: exactly what a model is asked about."""

    plain: str
    """Its plain text: what every answer is compared in, and what the results are stored under."""

    text_format: TextFormat
    """Notation of :attr:`text` (the corrector answers in the notation it was given)."""

    language: str
    """The document's language: what every model is told the text is."""

    text_sha: str
    """Identity of :attr:`plain`, the folder the stored answers of this text sit in."""

    words: int
    """How many words :attr:`plain` has, the figure a time is measured against."""

    stored: Version | None = None
    """The document's own proofreading as one version, or ``None`` when it has none.

    It holds the model's own text, before the teacher changed anything: what a comparison is about
    is what the models wrote, not what the teacher made of it."""

    task_marks: tuple[str, ...] = ()
    """The passages of the document that are printed text, not the student's writing.

    A comparison leaves them out as a run does, and they belong to its key, so an answer made
    without them is not the answer of this document."""

    not_task: tuple[str, ...] = ()
    """The lines the teacher proofreads after all, although a passage covers them."""

    task_text: tuple[str, ...] = ()
    """What :attr:`task_marks` really cover in :attr:`plain`, and no more: the part of the
    proofreading key that belongs to this document alone."""

    def own(self, key: str) -> bool:
        """Return whether the document's own proofreading is the answer of the settings *key*."""
        return self.stored is not None and self.stored.key == key


@dataclass(frozen=True)
class Candidate:
    """One model the comparison can ask, and what this computer makes of it."""

    provider: str
    """The provider of the model (``"ollama"``, ``"mistral"``, …)."""

    model: str
    """The model tag as the provider names it."""

    key: str
    """The proofreading key of this model's settings for this text.

    The identity of the answer: what a stored result is found by, and what decides whether the
    document's own proofreading already is this model's."""

    location: StageLocation
    """Where the answer is made: the privacy badge of the row."""

    suitability: Suitability
    """How well the model fits this computer, as the catalog and the detection rate it;
    ``UNKNOWN`` for a service elsewhere."""

    very_slow: bool = False
    """Whether a model rated slow runs very slowly here (most of it off the graphics card)."""

    estimated: TimeRange | None = None
    """How long this text takes with it, or ``None`` when this computer never measured it."""

    done: bool = False
    """Whether this model answered already: a stored result, or the document's own proofreading."""

    own: bool = False
    """Whether the document's own proofreading is this model's answer (it is :attr:`done` too)."""


@dataclass(frozen=True)
class ComparedRun:
    """What one model's attempt at the text came to."""

    candidate: Candidate
    """The model the attempt asked."""

    result: ComparedResult | None = None
    """The answer, with what it was made with; ``None`` when nothing was stored."""

    own: bool = False
    """The document's own proofreading is this model's answer, so nothing was asked."""

    stopped: bool = False
    """The teacher stopped the comparison before this model answered: nothing is stored."""

    failure: Message | None = None
    """Why this model could not be compared; the row says it and nothing is stored."""


@dataclass(frozen=True)
class JobHooks:
    """What the job that asks the models hands the comparison: its stop, its narrative, its rows."""

    stop_event: threading.Event | None = None
    """Set when the teacher pressed *Stop*."""

    reporter: Reporter | None = None
    """Where an answer's narrative goes; ``None`` logs it."""

    on_start: Callable[[Candidate], None] | None = None
    """Called with each model before it is asked."""

    on_answer: Callable[[ComparedRun], None] | None = None
    """Called with what each model's attempt came to, as soon as it is over."""


@dataclass(frozen=True)
class ComparisonParts:
    """What a comparison is built from: where it stores, what it asks, and what it reads.

    Grouped in one value because the composition root hands over the app's own and a test its
    fakes; none of them is a choice the service makes.
    """

    store: CompareResults
    """The stored answers of earlier comparisons."""

    option_marks: OptionMarks
    """The teacher's verdicts on the changes of an earlier comparison, in the data root."""

    unit_for: ProofreadUnitBuilder
    """Builds the proofreading unit of one model's settings."""

    documents: Callable[[Config, Path], DocumentStateView | None]
    """``(config, source)`` → the document as the window reads it, or ``None``."""

    key_of: Callable[[Config], str]
    """The proofreading key of a config on this computer, exactly as a run computes it."""

    facts: KeyFacts
    """What this computer adds to a stored answer: what was sent, and the exchange."""

    run: RunSession
    """The run in progress: a comparison counts as one and refuses while one is going."""

    ollama: OllamaServer
    """The AI server, for the models it holds in memory and the digest of a model."""

    reporter: Reporter | None = None
    """Where a proofreading's narrative goes; ``None`` logs it."""


class ModelComparisonService:
    """The text, the answers of the ticked models, and what the answers differ in."""

    def __init__(self, parts: ComparisonParts) -> None:
        self._store = parts.store
        self._option_marks = parts.option_marks
        self._unit_for = parts.unit_for
        self._documents = parts.documents
        self._key = parts.key_of
        self._facts = parts.facts
        self._run = parts.run
        self._ollama = parts.ollama
        self._reporter = parts.reporter or LoggingReporter()

    # ── the document ───────────────────────────────────────────────────────

    def source(self, config: Config, path: Path) -> CompareSource | None:
        """Return the text of *path* as the comparison reads it, or ``None`` when nothing was read.

        Never contacts a model and never probes this computer: the window calls this on the GUI
        thread, when it opens and when the document changes.
        """
        view = self._documents(config, path)
        if view is None or not view.raw_text.strip():
            return None
        text_format = normalize_text_format(view.raw_text_format)
        plain = TextFormatter.plain_text(view.raw_text, text_format)
        if not plain.strip():
            return None
        # A comparison leaves out what a run with these settings leaves out.
        marks = view.task_marks if config.skip_task_text else ()
        not_task = view.not_task if config.skip_task_text else ()
        return CompareSource(
            text=view.raw_text,
            plain=plain,
            text_format=text_format,
            language=config.language,
            text_sha=text_identity(plain),
            words=count_words(plain),
            stored=self._own_version(view, text_format),
            task_marks=marks,
            not_task=not_task,
            task_text=passage_parts(plain, marks, not_task),
        )

    @staticmethod
    def _own_version(view: DocumentStateView, text_format: TextFormat) -> Version | None:
        """Return the document's own proofreading as a version, or ``None`` without one."""
        if not (view.correction_done and view.correction_config_hash):
            return None
        # Only the machine's own text: a proofreading made from another transcription has none,
        # and the teacher's text is theirs, not an answer of the model.
        machine = view.machine_correction_text
        if not machine.strip():
            return None
        return Version(
            key=view.correction_config_hash,
            text=TextFormatter.plain_text(machine, text_format),
            review=view.correction_review,
        )

    # ── asking the models ──────────────────────────────────────────────────

    def run(
        self,
        config: Config,
        source: CompareSource,
        candidates: Sequence[Candidate],
        *,
        repeat: int = 0,
        hooks: JobHooks | None = None,
    ) -> tuple[ComparedRun, ...]:
        """Ask *candidates* about *source* one after another; a failure becomes a row's words.

        The whole job counts as a run, so nothing deletes the stored results between two models.
        A model that answered already is not asked: its stored answer, or the document's own
        proofreading, is what it said.  A stop ends the job between two models, and a model that
        was stopped stores nothing.

        Args:
            config: The live settings; only the provider and the model change per model.
            source: The text every model is asked about.
            candidates: The models to ask, in order.
            repeat: Which of each model's answers this is; above ``0`` it is stored beside the
                first one (:meth:`next_repeat`), so the window can say what of its changes is
                chance.
            hooks: The job's stop, its narrative and what it is told per model; ``None`` has
                none of them.

        Returns:
            One attempt per model that was reached, in order.

        Raises:
            RunInProgressError: a run is going, so the comparison must not start.
            RuntimeError: the provider of a candidate cannot be loaded here; the app says so.
        """
        if self._run.busy():
            raise RunInProgressError
        hooks = hooks or JobHooks()
        attempts: list[ComparedRun] = []
        with self._run.running():
            for candidate in candidates:
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    break
                if hooks.on_start is not None:
                    hooks.on_start(candidate)
                ran = self._ask(config, source, candidate, repeat, hooks)
                attempts.append(ran)
                if hooks.on_answer is not None:
                    hooks.on_answer(ran)
                if ran.stopped:
                    break
        return tuple(attempts)

    def _ask(
        self,
        config: Config,
        source: CompareSource,
        candidate: Candidate,
        repeat: int,
        hooks: JobHooks,
    ) -> ComparedRun:
        """Ask one model, unless it answered already, and store its answer.

        The model it loaded is unloaded afterwards, whether the answer came, failed or was
        stopped.
        """
        variant = self.variant(config, candidate)
        key = with_task_text(variant, self._key(variant), source.task_text)
        if source.own(key):
            return ComparedRun(candidate=candidate, own=True)
        if repeat == 0:
            stored = self._store.load(source.text_sha, key)
            if stored is not None:
                return ComparedRun(candidate=candidate, result=stored)
        before = self._loaded_models(config, candidate)
        started = time.monotonic()
        try:
            unit = self._unit_for(
                variant, reporter=hooks.reporter or self._reporter, stop_event=hooks.stop_event
            )
            answer = unit(
                source.text,
                source.text_format,
                passages=source.task_marks,
                not_task=source.not_task,
                stop_requested=None if hooks.stop_event is None else hooks.stop_event.is_set,
            )
        except RunStoppedError:
            return ComparedRun(candidate=candidate, stopped=True)
        except StageError as exc:
            log_failure(_logger, f"compare {candidate.model} on {source.text_sha[:12]}", exc)
            return ComparedRun(
                candidate=candidate, failure=model_not_compared(candidate.model, message_of(exc))
            )
        finally:
            self._unload_models_it_loaded(config, candidate, before)
        elapsed = time.monotonic() - started
        if answer.outcome.failed:
            # An answer the app cannot use is no answer: nothing is stored, and the row says why.
            reason = detail(answer.outcome.warning or "")
            return ComparedRun(
                candidate=candidate, failure=model_not_compared(candidate.model, reason)
            )
        result = self._compared_result(
            variant, source, candidate, key, _Asked(answer, elapsed, repeat)
        )
        self._store.save(result)
        return ComparedRun(candidate=candidate, result=result)

    def _compared_result(
        self,
        config: Config,
        source: CompareSource,
        candidate: Candidate,
        key: str,
        asked: _Asked,
    ) -> ComparedResult:
        """Return the answer of *candidate* as the record the store writes."""
        answer = asked.answer
        return ComparedResult(
            text_sha=source.text_sha,
            key=key,
            provider=candidate.provider,
            model=candidate.model,
            language=config.language,
            text=TextFormatter.plain_text(answer.text, source.text_format),
            review=answer.review,
            seconds=asked.seconds,
            fell_back=answer.outcome.fell_back,
            left_out=answer.outcome.left_out,
            warning=answer.stored_warning,
            sent=sent_record(
                config, Stage.CORRECTION, names_hidden=answer.names_hidden, facts=self._facts
            ),
            digest=self._digest(candidate, config.ollama_url),
            exchange=self._facts.exchange_identity(
                config.correction_profile,
                corrections_only=config.correction_output == CorrectionOutput.TEXT,
            ),
            app_version=__version__,
            created=time.time(),
            repeat=asked.repeat,
        )

    def _digest(self, candidate: Candidate, ollama_url: str) -> str:
        """Return the digest of *candidate* on its server; ``""`` for an online provider.

        Asked of the server as it is now, not read from an answer a few minutes old: a model
        pulled in a terminal since would otherwise be stored with the digest of the weights it
        no longer has.  One request per model, in a job that takes minutes.
        """
        if not is_ollama(candidate.provider):
            return ""
        return self._ollama.snapshot(ollama_url, max_age=0.0).digest_of(candidate.model)

    # ── what the models loaded ─────────────────────────────────────────────

    @staticmethod
    def variant(config: Config, candidate: Candidate) -> Config:
        """Return *config* on the candidate's provider and model, with nothing else changed."""
        return dataclasses.replace(
            config, correction_provider=candidate.provider, correction_model=candidate.model
        )

    def data_flow_config(self, config: Config, candidate: Candidate) -> Config:
        """Return the settings whose data flow says where *candidate* is asked: proofreading only.

        A comparison reads no page and writes no review, so only the text of the proofreading goes
        where the model is.
        """
        return dataclasses.replace(
            self.variant(config, candidate),
            recognition_enabled=False,
            correction_enabled=True,
            evaluation_enabled=False,
        )

    def _loaded_models(self, config: Config, candidate: Candidate) -> frozenset[str]:
        """Return the models the server held before the answer, or nothing for another service."""
        if not is_ollama(candidate.provider):
            return frozenset()
        try:
            return frozenset(self._ollama.activity(config.ollama_url).loaded_names())
        except Exception as exc:  # noqa: BLE001  # nothing to unload later; the answer still comes
            _logger.debug("Cannot read what the server holds: %s", exc)
            return frozenset()

    def _unload_models_it_loaded(
        self, config: Config, candidate: Candidate, before: frozenset[str]
    ) -> None:
        """Drop the models this answer loaded: the next one starts cold and the memory is free.

        Only the ones that were not in memory before: a model the teacher was already using stays
        loaded.
        """
        if not is_ollama(candidate.provider):
            return
        try:
            loaded = self._ollama.activity(config.ollama_url).loaded_names()
        except Exception as exc:  # noqa: BLE001  # the answer stands; only the memory stays taken
            _logger.warning("Cannot read what the server holds: %s", exc)
            return
        for name in loaded:
            if name not in before:
                self._ollama.unload(config.ollama_url, name)

    # ── the comparison ─────────────────────────────────────────────────────

    def comparison(self, source: CompareSource, keys: Collection[str]) -> Comparison:
        """Return what the stored answers of *source* made with the settings *keys* differ in.

        The document's own proofreading counts as an answer too, so the model the teacher already
        proofread with is compared with the ones they try beside it.  The teacher's verdicts come
        along, in the options and in the figures of each version.

        Args:
            source: The text every answer is about.
            keys: The proofreading keys of the models the window lists now.  An answer made with
                other settings stays stored but is not a column: the columns differ in the model
                and in nothing else, and an answer that could be named only by its key says
                nothing the teacher can read.
        """
        stored = self._store.results(source.text_sha)
        own_key = source.stored.key if source.stored is not None else ""
        answers: dict[str, list[ComparedResult]] = {}
        for result in stored.values():
            if result.key in keys:
                answers.setdefault(result.key, []).append(result)
        versions = [_version_of(runs) for key, runs in sorted(answers.items()) if key != own_key]
        if source.stored is not None:
            versions.append(source.stored)
        return compare(
            source.plain,
            versions,
            text_sha=source.text_sha,
            marks=self.marks(source),
        )

    def next_repeat(self, source: CompareSource, candidate: Candidate) -> int:
        """Return the run number the next answer of *candidate* about *source* is stored as.

        One past the highest stored, so an answer never replaces another one of the same model.
        """
        repeats = [
            result.repeat
            for result in self._store.results(source.text_sha).values()
            if result.key == candidate.key
        ]
        return max(repeats) + 1 if repeats else 0

    def marks(self, source: CompareSource) -> tuple[OptionMark, ...]:
        """Return the teacher's verdicts on the changes of *source*."""
        return self._option_marks.marks(source.text_sha)

    def set_mark(self, source: CompareSource, option: Option, verdict: Verdict | None) -> None:
        """Say what *verdict* the teacher gives one *option* of *source*'s comparison.

        ``None`` takes the mark back, so a change the teacher misjudged is unmarked again.  The
        mark is on the option — its span and the hash of its wording — so two models that wrote
        that wording are judged at once.

        Raises:
            OSError: the marks cannot be written, or the stored ones cannot be read now and would
                be lost; the window tells the teacher.
        """
        text_sha = source.text_sha
        judged = identity(text_sha, option.start, option.end, option.replacement)
        kept = [mark for mark in self._option_marks.marks(text_sha) if mark.identity != judged]
        if verdict is not None:
            kept.append(
                OptionMark(
                    text_sha=text_sha,
                    start=option.start,
                    end=option.end,
                    replacement_sha256=judged[3],
                    verdict=verdict,
                )
            )
        self._option_marks.save(text_sha, tuple(kept))
