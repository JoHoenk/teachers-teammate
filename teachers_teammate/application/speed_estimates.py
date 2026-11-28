"""The speed test and the time estimates the AI models page shows.

Two questions, one service:

* **How fast is this computer?**  :meth:`SpeedEstimateService.test_speed` runs the speed test
  (:mod:`~teachers_teammate.infrastructure.performance_probe`) and stores what it found.  It
  occupies the graphics card, so it runs only when the teacher asks for it, on a background
  thread.
* **How long will a run take?**  :meth:`SpeedEstimateService.speed_summary` turns a stored
  measurement into seconds per page per step, a band, and the time a class set takes.

The service owns the caches behind both, so nobody else has to remember to invalidate them:

* the **server facts** (version, model digests, what is loaded) per server URL.  They are what
  makes a stored measurement go out of date, and fetching them needs the network, so they are
  fetched in the background and :meth:`speed_summary` reads the cache only.  That is what makes
  ``cached_only=True`` safe on the GUI thread.
* the **measurements** themselves, in the speed store.

Nothing here formats a duration or a sentence: the GUI does that.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import functools
import logging
from statistics import median
import threading

from ..config import Config, OcrConfig
from ..domain.context_budget import BASE_NUM_CTX
from ..domain.correction_policy import CorrectionOutput, normalize_output_mode
from ..domain.data_location import ENGINE_OLLAMA, PROVIDER_OLLAMA
from ..domain.hardware_profile import HardwareDetection
from ..domain.model_catalog import AnswerLength, ModelCategory, find_spec, request_profile
from ..domain.model_fit import rate_model
from ..domain.ollama_address import normalize_ollama_url
from ..domain.performance_estimate import (
    TYPICAL_PAGE_WORDS,
    MachineFactors,
    ModelSpeed,
    SpeedBand,
    SpeedSource,
    SpeedStep,
    StepOutcome,
    TimeRange,
    answer_length_for,
    band,
    batch_time,
    card_not_used,
    content_review_time,
    machine_factor,
    median_factor,
    percentile,
    proofreading_time,
    reading_page_time,
)
from ..domain.proofreading.result import StepName
from ..domain.request_profile import RequestProfile
from ..domain.stages import Stage
from ..domain.system_facts import ServerFacts
from ..infrastructure.performance_probe import (
    PerformanceProbe,
    ProbeResult,
    ProgressFn,
    SpeedTestRequest,
)
from ..infrastructure.proofreading.corrector import exchange_cost
from ..infrastructure.speed_store import (
    RunSample,
    SampleKey,
    SampleStore,
    SpeedKey,
    SpeedRecord,
    SpeedStore,
    StepRecord,
    StoredSpeed,
    now_stamp,
)
from ..settings_schema import DEFAULTS, normalize_saved_ollama_url
from .ports import OllamaClientFactory, OllamaServer

_logger = logging.getLogger(__name__)

#: Files and pages of the example the panel shows: one class set of essays.
CLASS_SET_FILES = 30
CLASS_SET_PAGES = 2

#: Samples of one step before the run figures replace the catalog's answer length.  Two would
#: be one good document and one bad one; three is the first count whose middle means anything.
MIN_ANSWER_SAMPLES = 3

#: Samples from which the answer length uses the 10th and 90th percentile instead of the ends.
#: Below it the ends are all there is; above it one runaway answer no longer widens the range.
PERCENTILE_SAMPLES = 10


@dataclass(frozen=True)
class SpeedChoices:
    """The models the AI models page has chosen, as the test and the estimate need them.

    An empty model means the step does not run, or does not run on Ollama: text recognition with
    the built-in reader has no AI model to measure, and a step on an online service is not
    measured at all.
    """

    ollama_url: str = DEFAULTS["ollama_url"]
    reading_model: str = ""
    writing_model: str = ""
    review_model: str = ""
    """The model of the content review; empty when the review does not run on Ollama (or not at
    all).  A review that shares the proofreading model names it here as well."""
    correction_output: str = DEFAULTS["correction_output"]
    """``"review"`` or ``"text"``: how the answer of the proofreading is written, which decides
    how long it is."""
    language: str = "English"
    num_ctx: int = BASE_NUM_CTX
    ocr: OcrConfig = field(default_factory=OcrConfig)

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, object],
        *,
        effective_model: Callable[[str, str, str], str],
    ) -> SpeedChoices:
        """Return the choices *settings* describe, with the models a run would really use.

        Every step whose provider is Ollama is included, whether or not its tick is set: the
        speed test of the settings window measures the models, not the next run.  For the models
        a particular run uses, see :meth:`from_config`.

        Args:
            settings: The flat settings dict (``ocr.toml`` key names).
            effective_model: Resolves a provider and a possibly empty model to the model a run
                uses; :meth:`~teachers_teammate.application.providers.ProviderService.effective_model`
                is the one.
        """
        ocr = OcrConfig.from_settings(settings)
        return cls._of(
            ollama_url=normalize_saved_ollama_url(settings.get("ollama_url")),
            ocr=ocr,
            reading=True,
            correction=(
                str(settings.get("correction_provider") or DEFAULTS["correction_provider"]),
                str(settings.get("correction_model") or ""),
            ),
            evaluation=(
                str(settings.get("evaluate_provider") or DEFAULTS["evaluate_provider"]),
                str(settings.get("evaluate_model") or ""),
            ),
            correction_output=str(settings.get("correction_output") or ""),
            language=str(settings.get("language") or DEFAULTS["language"]),
            effective_model=effective_model,
        )

    @classmethod
    def from_config(
        cls,
        config: Config,
        *,
        effective_model: Callable[[str, str, str], str],
    ) -> SpeedChoices:
        """Return the choices of one run: only the steps *config* switches on.

        A run that only reads the text, or that proofreads without a content review, pays for
        those steps alone, so the time left must not count the others.
        """
        return cls._of(
            ollama_url=config.ollama_url,
            ocr=config.ocr,
            reading=config.recognition_enabled,
            correction=(
                (config.correction_provider, config.correction_model)
                if config.correction_enabled
                else None
            ),
            evaluation=(
                (config.evaluate_provider, config.evaluate_model)
                if config.evaluation_enabled
                else None
            ),
            correction_output=config.correction_output,
            language=config.language or DEFAULTS["language"],
            effective_model=effective_model,
        )

    @classmethod
    def _of(
        cls,
        *,
        ollama_url: str,
        ocr: OcrConfig,
        reading: bool,
        correction: tuple[str, str] | None,
        evaluation: tuple[str, str] | None,
        correction_output: str,
        language: str,
        effective_model: Callable[[str, str, str], str],
    ) -> SpeedChoices:
        """Build the choices; a step that is ``None``/``False`` or not on Ollama gets no model."""

        def ollama_model(choice: tuple[str, str] | None, task: Stage) -> str:
            if choice is None or choice[0] != PROVIDER_OLLAMA:
                return ""
            return effective_model(choice[0], choice[1], task)

        return cls(
            ollama_url=ollama_url,
            reading_model=ocr.model if reading and ocr.engine == ENGINE_OLLAMA else "",
            writing_model=ollama_model(correction, Stage.CORRECTION),
            review_model=ollama_model(evaluation, Stage.EVALUATION),
            correction_output=normalize_output_mode(correction_output),
            language=language,
            num_ctx=BASE_NUM_CTX,
            ocr=ocr,
        )

    def models(self) -> tuple[str, ...]:
        """Return the models the test asks the server about, without duplicates."""
        seen: list[str] = []
        for tag in (self.reading_model, self.writing_model, self.review_model):
            if tag and tag not in seen:
                seen.append(tag)
        return tuple(seen)

    def request(self) -> SpeedTestRequest:
        """Return the request the speed test sends for these choices."""
        return SpeedTestRequest(
            reading_model=self.reading_model,
            writing_model=self.writing_model,
            review_model=self.review_model,
            language=self.language,
            num_ctx=self.num_ctx,
            ocr=self.ocr,
        )


@dataclass(frozen=True)
class StepEstimate:
    """One row of the panel: a step, its model, and how long one page takes."""

    step: SpeedStep
    model: str
    per_page: TimeRange
    band: SpeedBand
    source: SpeedSource
    outcome: StepOutcome | None = None
    """What the measurement of this step became; ``None`` for an expected figure."""
    on_card: float | None = None
    """Share of the model that sat on the graphics card; ``None`` when the server did not say."""
    message: str = ""
    sample_count: int = 0
    """Samples of real runs behind this row; ``0`` when the figure comes from the speed test."""


@dataclass(frozen=True)
class UnmeasuredStep:
    """A step that runs here but was never measured: its row stays, with what to expect.

    A model chosen a moment ago has neither a stored test nor samples of real runs.  Leaving its
    row out would make the class set look shorter than it is, so the row is kept and says it was
    not measured.
    """

    step: SpeedStep
    model: str
    band: SpeedBand | None = None
    """The band the model is expected to reach on this computer; ``None`` when nothing says."""


@dataclass(frozen=True)
class SpeedSummary:
    """Everything the speed panel shows, worked out from the store."""

    steps: tuple[StepEstimate, ...] = ()
    unmeasured: tuple[UnmeasuredStep, ...] = ()
    """The steps that run here but have no measurement; their rows say so instead of vanishing."""
    class_set: TimeRange | None = None
    """How long a class set (:data:`CLASS_SET_FILES` files of :data:`CLASS_SET_PAGES` pages)
    takes, or ``None`` when a step has no estimate: a sum that leaves one out would understate
    the time."""
    measured_at: str = ""
    """When the measurement behind the rows was taken; ``""`` when nothing was measured."""
    on_battery: bool | None = None
    shared: bool = False
    """Another model was loaded when the test ran, so the times may be too long."""
    swaps: bool = False
    """The models evict each other, so every file pays both loads."""
    swap_load_s: float = 0.0
    """Seconds of model loading each file pays while the models evict each other."""
    out_of_date: bool = False
    """The model, the server version or the card changed since the measurement."""
    card_not_used: bool = False
    """Ollama ran a model on the processor although this computer has a graphics card."""
    too_slow: bool = False
    """A step hit the test's time limit, so its figure is a lower bound at best."""
    error: str = ""
    """One line when the server could not be reached at all."""
    sample_count: int = 0
    """How many samples of real runs the rows rest on; ``0`` when they come from the test."""
    sample_pages: bool = False
    """The count is pages, not documents: the run proofread nothing, so only text recognition
    was measured."""

    def step(self, step: SpeedStep) -> StepEstimate | None:
        """Return the row of *step*, or ``None`` when the step has no estimate."""
        return next((item for item in self.steps if item.step is step), None)

    def band_of(self, step: SpeedStep) -> SpeedBand | None:
        """Return the band of *step*, or ``None`` when the step has no estimate."""
        row = self.step(step)
        return None if row is None else row.band

    def unmeasured_step(self, step: SpeedStep) -> UnmeasuredStep | None:
        """Return the unmeasured row of *step*, or ``None`` when the step was measured."""
        return next((item for item in self.unmeasured if item.step is step), None)

    @property
    def per_page(self) -> TimeRange | None:
        """Return how long one page takes with every step that runs, or ``None``.

        The figure a run's time left is worked out from: a page pays every step, one after the
        other.
        """
        if not self.steps:
            return None
        total = TimeRange(0.0, 0.0)
        for row in self.steps:
            total = total + row.per_page
        return total


@dataclass
class _Rows:
    """The rows of one speed summary, with what the measured ones say about the whole run."""

    estimates: list[StepEstimate] = field(default_factory=list)
    unmeasured: list[UnmeasuredStep] = field(default_factory=list)
    load_s: list[float] = field(default_factory=list)
    """Seconds of model loading one file pays, one entry per distinct figure."""
    run_samples: list[RunSample] = field(default_factory=list)
    measured_at: str = ""
    on_battery: bool | None = None
    shared: bool = False
    swaps: bool = False
    out_of_date: bool = False
    card_unused: bool = False
    too_slow: bool = False
    documents: int = 0
    pages: int = 0

    def note(
        self,
        step: SpeedStep,
        speed: ModelSpeed,
        stored: StoredSpeed | None,
        samples: Sequence[RunSample],
        *,
        from_runs: bool,
        facts: ServerFacts | None,
        hardware: HardwareDetection | None,
    ) -> None:
        """Add what one measured step says about the run as a whole."""
        if step is SpeedStep.REVIEW:
            # Only the review step says whether the writing models evict each other.
            self.run_samples.extend(samples)
        if step is SpeedStep.READING:
            self.pages = max(self.pages, len(samples))
        else:
            self.documents = max(self.documents, len(samples))
        if from_runs:
            self.measured_at = max(self.measured_at, *(sample.at for sample in samples))
        # The load seconds belong to the speed, wherever it came from: a run whose models evict
        # each other pays them per file even when nothing was ever tested here.  The reading
        # model's load is left out: it is another model than the writing ones.
        if (
            step is not SpeedStep.READING
            and speed.load_s is not None
            and speed.load_s not in self.load_s
        ):
            self.load_s.append(speed.load_s)
        if stored is None:
            return
        self.measured_at = max(self.measured_at, stored.record.measured_at)
        self.shared = self.shared or stored.record.shared
        self.swaps = self.swaps or (step is not SpeedStep.READING and stored.record.swaps)
        # A sample carries no digest, so it cannot go out of date; only the test can.
        self.out_of_date = self.out_of_date or (
            not from_runs and facts is not None and not stored.current
        )
        self.card_unused = self.card_unused or card_not_used(
            speed.on_card, hardware is not None and hardware.gpu_name is not None
        )
        self.too_slow = self.too_slow or _outcome(stored.record, step) is StepOutcome.TOO_SLOW
        if self.on_battery is None:
            self.on_battery = stored.record.on_battery


class SpeedEstimateService:
    """Runs the speed test and turns stored measurements into estimates."""

    def __init__(
        self,
        *,
        effective_model: Callable[[str, str, str], str],
        ollama: OllamaServer,
        client_factory: OllamaClientFactory[object],
        store: SpeedStore | None = None,
        samples: SampleStore,
        probe_factory: Callable[..., PerformanceProbe],
    ) -> None:
        """Create the service.

        Args:
            effective_model: ``(provider, model, task)`` → the model a run would use;
                :meth:`choices` and :meth:`choices_for_config` need it, so the page tests what a
                run would use rather than what an empty field happens to mean.
            ollama: The server whose facts decide whether a stored measurement is out of date.
            client_factory: Builds the Ollama client of a URL and a cancel event.  The probe
                drives that client, so building one is one of the few things this layer cannot
                do itself.
            store: Where measurements live; the default store under the storage root.
            samples: Where the samples of real runs live: the one store of the process, so the
                estimates read what a run wrote.
            probe_factory: Builds the probe, given the page models it prepares a page with; a
                test replaces it.
        """
        self._effective_model = effective_model
        self._ollama = ollama
        self._client_factory = client_factory
        self._store = store or SpeedStore()
        self._samples = samples
        self._probe_factory = probe_factory

    # ── what the page measures ─────────────────────────────────────────────

    def choices(self, settings: Mapping[str, object]) -> SpeedChoices:
        """Return the models *settings* name, as the speed test and the estimate need them.

        The models are resolved the way a run resolves them, so the page tests what a run would
        use rather than what an empty field happens to mean.
        """
        return SpeedChoices.from_settings(settings, effective_model=self._effective_model)

    def choices_for_config(self, config: Config) -> SpeedChoices:
        """Return the models a run with *config* uses: only the steps that run, on Ollama.

        What the time left of a run is worked out from, so a run that only reads the text, or
        proofreads without a content review, is not charged for the steps it skips.
        """
        return SpeedChoices.from_config(config, effective_model=self._effective_model)

    # ── server facts ───────────────────────────────────────────────────────

    def cached_server_facts(self, ollama_url: str) -> ServerFacts | None:
        """Return what the server last answered about itself, or ``None`` until it was asked.

        No age limit: a version that is known beats none.  The panel asks for a new answer when
        it opens and the held one is older than ``SNAPSHOT_TTL_S``.  A server
        that did not answer gives empty facts, so every stored measurement is out of date rather
        than shown as current.
        """
        snapshot = self._ollama.cached_snapshot(ollama_url)
        return None if snapshot is None else snapshot.facts()

    # ── the test ───────────────────────────────────────────────────────────

    def test_speed(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
        cancel_event: threading.Event | None = None,
        on_progress: ProgressFn | None = None,
    ) -> ProbeResult:
        """Measure the models *choices* names and store the result.

        Occupies the graphics card for up to a minute and a half per step, so call it from a
        background thread.  A stopped test stores nothing.

        Raises:
            :exc:`~teachers_teammate.exceptions.RunStoppedError`: the teacher stopped the test.
        """
        url = normalize_ollama_url(choices.ollama_url)
        # Read before the probe asks anything: a pull or a restart while the test runs wins.
        generation = self._ollama.generation(url)
        probe = self._probe_factory(
            # The client gets the event too: it is what abandons a request that is waiting.
            self._client_factory(url, cancel_event=cancel_event),
            cancel_event=cancel_event,
            gpu_found=hardware is not None and hardware.gpu_name is not None,
            on_battery=_on_battery(),
            on_progress=on_progress,
        )
        result = probe.run(choices.request())
        if result.error:
            # A server that could not be reached says nothing: nothing to store.
            return result
        # The test's last answer becomes the app's: the records it makes and what the panel
        # compares them with come from one answer of the server, so a measurement taken now is
        # not shown as out of date by a digest read a minute ago.
        if result.snapshot is not None and not result.snapshot.unreachable:
            self._ollama.hold(url, result.snapshot, generation=generation)
        for record in self._records(url, result, hardware):
            self._store.save(record)
        return result

    def _records(
        self,
        url: str,
        result: ProbeResult,
        hardware: HardwareDetection | None,
    ) -> tuple[SpeedRecord, ...]:
        """Return the records *result* becomes: one per model, each with its own steps.

        A record is keyed by the model, so a run that measured two models leaves two records;
        the steps of a model are the steps that model ran.
        """
        by_model: dict[str, list[StepRecord]] = {}
        for item in result.steps:
            by_model.setdefault(item.model, []).append(
                StepRecord(item.step, item.outcome, speed=item.speed, message=item.message)
            )
        stamp = now_stamp()
        return tuple(
            SpeedRecord(
                key=SpeedKey(
                    server=url,
                    tag=tag,
                    digest=result.facts.digest_of(tag),
                    ollama_version=result.facts.version,
                    gpu=(hardware.gpu_name or "") if hardware is not None else "",
                ),
                measured_at=stamp,
                steps=tuple(steps),
                on_battery=result.on_battery,
                shared=result.shared,
                swaps=result.swaps,
            )
            for tag, steps in by_model.items()
        )

    # ── the estimate ───────────────────────────────────────────────────────

    def speed_summary(
        self,
        choices: SpeedChoices,
        *,
        hardware: HardwareDetection | None = None,
    ) -> SpeedSummary:
        """Return what the panel shows for *choices*.

        Never contacts the server: it reads what the server last answered, so it is safe on the
        GUI thread.  Until the server was asked, a measurement is shown without the out-of-date
        check.
        """
        url = normalize_ollama_url(choices.ollama_url)
        facts = self.cached_server_facts(url)
        rows = self._rows(choices, url=url, facts=facts, hardware=hardware)
        # A run that paid a model load for most of its documents is a run whose models evict
        # each other: that is what the teacher sees as "every file takes longer than the last".
        swaps = rows.swaps or _most_loaded(rows.run_samples)
        return SpeedSummary(
            steps=tuple(rows.estimates),
            unmeasured=tuple(rows.unmeasured),
            class_set=(
                None
                if rows.unmeasured
                else _class_set(rows.estimates, swaps=swaps, load_s=rows.load_s)
            ),
            measured_at=rows.measured_at,
            on_battery=rows.on_battery,
            shared=rows.shared,
            swaps=swaps,
            swap_load_s=sum(rows.load_s) if swaps else 0.0,
            out_of_date=rows.out_of_date,
            card_not_used=rows.card_unused,
            too_slow=rows.too_slow,
            sample_count=rows.documents or rows.pages,
            sample_pages=not rows.documents,
        )

    def _rows(
        self,
        choices: SpeedChoices,
        *,
        url: str,
        facts: ServerFacts | None,
        hardware: HardwareDetection | None,
    ) -> _Rows:
        """Return a row per step that runs, measured where there is a measurement to show."""
        # ``machine_factors`` reads the whole store, so it is worked out on first need and once.
        factors_of = functools.cache(self.machine_factors)
        rows = _Rows()
        for step, tag in _steps_of(choices):
            by_mode = self._samples.samples(url, tag, step)
            samples = [sample for group in by_mode.values() for sample in group]
            stored = self._store.lookup(self._lookup_key(url, tag, facts, hardware))
            speed = _sample_speed(samples)
            from_runs = speed is not None
            if speed is None and stored is not None:
                speed = _measured_speed(stored.record, step)
            if speed is None:
                # The row stays: a step that runs here but was never measured still takes time,
                # and a class set that left it out would promise a figure that is too short.
                rows.unmeasured.append(
                    UnmeasuredStep(
                        step=step,
                        model=tag,
                        band=_expected_band(tag, step, hardware, factors_of(url)),
                    )
                )
                continue
            rows.estimates.append(
                _estimate(
                    step,
                    tag,
                    speed,
                    stored,
                    by_mode,
                    corrections_only=choices.correction_output == CorrectionOutput.TEXT,
                )
            )
            rows.note(
                step, speed, stored, samples, from_runs=from_runs, facts=facts, hardware=hardware
            )
        return rows

    def _lookup_key(
        self,
        url: str,
        tag: str,
        facts: ServerFacts | None,
        hardware: HardwareDetection | None,
    ) -> SpeedKey:
        """Return the key to look *tag* up under, with what is known about the server."""
        return SpeedKey(
            server=url,
            tag=tag,
            digest="" if facts is None else facts.digest_of(tag),
            ollama_version="" if facts is None else facts.version,
            gpu=(hardware.gpu_name or "") if hardware is not None else "",
        )

    # ── one model at a time ────────────────────────────────────────────────

    def page_times(
        self,
        tags: Sequence[str],
        category: ModelCategory,
        ollama_url: str,
        *,
        hardware: HardwareDetection | None = None,
    ) -> dict[str, TimeRange]:
        """Return the measured seconds per page of *tags* for *category*, keyed by tag.

        A model that was never measured here is left out, and so is one whose measurement no
        longer fits the server: a figure that has moved on is worse than none.  One read of the
        store answers for the whole list, because a model list is filled in one go.
        """
        url = normalize_ollama_url(ollama_url)
        facts = self.cached_server_facts(url)
        step = SpeedStep.READING if category == "ocr" else SpeedStep.PROOFREADING
        keys = [self._lookup_key(url, tag, facts, hardware) for tag in tags]
        result: dict[str, TimeRange] = {}
        for tag, stored in self._store.lookup_many(keys).items():
            speed = stored.record.measured(step) if stored.current else None
            if speed is not None:
                result[tag] = _page_time(step, speed, tag)
        # One read of the samples answers for every tag of the list.
        modes_by_tag: dict[str, dict[str, tuple[RunSample, ...]]] = {}
        for key, group in self._samples.server_samples(url).items():
            if key.step is step:
                modes_by_tag.setdefault(key.tag, {})[key.mode] = group
        for tag in tags:
            by_mode = modes_by_tag.get(tag, {})
            samples = [sample for group in by_mode.values() for sample in group]
            speed = _sample_speed(samples)
            if speed is None:
                continue
            mode = _newest_mode(by_mode) if step is SpeedStep.PROOFREADING else ""
            answer = _run_answer_length(step, by_mode.get(mode, ()) if mode else samples)
            result[tag] = _page_time(
                step, speed, tag, answer=answer, corrections_only=mode == "text"
            )
        return result

    # ── the machine factor ─────────────────────────────────────────────────
    def machine_factors(self, ollama_url: str) -> MachineFactors:
        """Return how much slower this computer is than the reference machine, per rate.

        The median over every model measured here that has a reference speed: one model's
        figure can be off (a busy server, a model that does not use the card well), the middle
        of several cannot.  Both the speed test and real runs count, and the runs win where
        they exist, because they are what a run does.  Both ends are ``None`` while nothing
        was measured here.
        """
        url = normalize_ollama_url(ollama_url)
        speeds: dict[str, ModelSpeed] = {}
        for record in self._store.records():
            if record.key.server != url:
                continue
            speed = record.measured(_step_of(record.key.tag))
            if speed is not None:
                speeds[record.key.tag] = speed
        for tag, samples in _samples_by_tag(self._samples.server_samples(url)).items():
            speed = _sample_speed(samples)
            if speed is not None:
                speeds[tag] = speed
        writing: list[float] = []
        reading: list[float] = []
        for tag, speed in speeds.items():
            spec = find_spec(tag)
            reference = None if spec is None else spec.reference_speed
            if reference is None:
                continue
            write_factor = machine_factor(speed.write_tps, reference.write_tps)
            if write_factor is not None:
                writing.append(write_factor)
            if reference.read_tps is not None:
                read_factor = machine_factor(speed.read_tps, reference.read_tps)
                if read_factor is not None:
                    reading.append(read_factor)
        return MachineFactors(writing=median_factor(writing), reading=median_factor(reading))


def _estimate(
    step: SpeedStep,
    tag: str,
    speed: ModelSpeed,
    stored: StoredSpeed | None,
    by_mode: Mapping[str, tuple[RunSample, ...]],
    *,
    corrections_only: bool,
) -> StepEstimate:
    """Return the row of one measured step, with the answer length its own runs measured."""
    samples = [sample for group in by_mode.values() for sample in group]
    # The rates do not depend on the mode, the answer length does: only the samples of the mode
    # this run is set to say how long its answers are.
    mode = _proofreading_mode(corrections_only) if step is SpeedStep.PROOFREADING else ""
    answer = _run_answer_length(step, by_mode.get(mode, ()) if mode else samples)
    per_page = _page_time(step, speed, tag, answer=answer, corrections_only=corrections_only)
    return StepEstimate(
        step=step,
        model=tag,
        per_page=per_page,
        band=band(per_page.typical_s),
        source=SpeedSource.MEASURED,
        outcome=None if stored is None else _outcome(stored.record, step),
        on_card=speed.on_card,
        message="" if stored is None else _message(stored.record, step),
        sample_count=len(samples),
    )


def _steps_of(choices: SpeedChoices) -> tuple[tuple[SpeedStep, str], ...]:
    """Return ``(step, model)`` for every step that runs on Ollama."""
    steps: list[tuple[SpeedStep, str]] = []
    if choices.reading_model:
        steps.append((SpeedStep.READING, choices.reading_model))
    if choices.writing_model:
        steps.append((SpeedStep.PROOFREADING, choices.writing_model))
    if choices.review_model:
        steps.append((SpeedStep.REVIEW, choices.review_model))
    return tuple(steps)


def _page_time(
    step: SpeedStep,
    speed: ModelSpeed,
    tag: str,
    *,
    answer: AnswerLength | None = None,
    corrections_only: bool = False,
) -> TimeRange:
    """Return how long one page takes *tag* for *step*, from its measured *speed*.

    *answer* is the answer length the runs measured for the steps this run sends; ``None``
    keeps the exchange's own figures.  *corrections_only* is the teacher's *Corrections only*
    choice: step ① alone, so the page is read once instead of four times.
    """
    spec = find_spec(tag)
    category: ModelCategory = "ocr" if step is SpeedStep.READING else "general"
    if step is SpeedStep.READING:
        return reading_page_time(speed, answer or answer_length_for(spec, category))
    if step is SpeedStep.REVIEW:
        return content_review_time(speed, TYPICAL_PAGE_WORDS)
    profile = request_profile(tag)
    return proofreading_time(
        speed,
        TYPICAL_PAGE_WORDS,
        exchange_cost(profile.exchange),
        _proofreading_steps(profile, corrections_only=corrections_only),
        answer=answer,
    )


def _proofreading_mode(corrections_only: bool) -> str:
    """Return the sample mode a proofreading run writes: ``"text"`` or ``"exchange"``."""
    return "text" if corrections_only else "exchange"


def _proofreading_steps(profile: RequestProfile, *, corrections_only: bool) -> tuple[StepName, ...]:
    """Return the steps a proofreading run sends for *profile*.

    *Corrections only* stops after step ①; a model whose profile has no unclear check skips
    step ③.
    """
    if corrections_only:
        return ("correct",)
    if profile.unclear_check:
        return ("correct", "explain", "unclear")
    return ("correct", "explain")


def _sample_speed(samples: Sequence[RunSample]) -> ModelSpeed | None:
    """Return the speed the *samples* add up to, or ``None`` when they measured no rate.

    The median of the rates, not their mean: one sample from a server that was busy with
    something else is far off, and the middle of a window ignores it without the app having to
    ask the server what else it is running.
    """
    read_tps = median_factor([sample.read_tps for sample in samples])
    write_tps = median_factor([sample.write_tps for sample in samples])
    if read_tps is None or write_tps is None:
        return None
    loads = [sample.load_s for sample in samples if sample.load_s > 0]
    images = [sample.image_tokens for sample in samples if sample.image_tokens > 0]
    return ModelSpeed(
        read_tps=read_tps,
        write_tps=write_tps,
        load_s=median(loads) if loads else None,
        image_tokens=round(median(images)) if images else None,
    )


def _step_of(tag: str) -> SpeedStep:
    """Return the step that measures *tag*: a reading model reads, everything else writes."""
    spec = find_spec(tag)
    if spec is not None and spec.category == "ocr":
        return SpeedStep.READING
    return SpeedStep.PROOFREADING


def _samples_by_tag(by_key: Mapping[SampleKey, Sequence[RunSample]]) -> dict[str, list[RunSample]]:
    """Return the samples of *by_key* grouped by model, one step per model.

    A model that both reads and writes has samples of both steps, and their rates are not
    comparable: a page image is read once, an answer is written token by token.  The step the
    model is measured by is the one that counts.
    """
    grouped: dict[str, list[RunSample]] = {}
    for key, samples in by_key.items():
        if key.step is not _step_of(key.tag):
            continue
        grouped.setdefault(key.tag, []).extend(samples)
    return grouped


def _newest_mode(by_mode: Mapping[str, Sequence[RunSample]]) -> str:
    """Return the proofreading mode the last run used, or ``""`` when there is no sample."""
    newest = ""
    stamp = ""
    for mode, samples in by_mode.items():
        for sample in samples:
            if sample.at > stamp:
                stamp, newest = sample.at, mode
    return newest


def _answer_values(step: SpeedStep, samples: Sequence[RunSample]) -> list[float]:
    """Return the answer lengths *samples* measured, in the unit the step's answer uses.

    A reading model answers per page, a writing model per 100 words of student text.
    """
    if step is SpeedStep.READING:
        return [float(sample.answer_tokens) for sample in samples if sample.answer_tokens > 0]
    return [
        value for value in (sample.answer_per_100_words for sample in samples) if value is not None
    ]


def _run_answer_length(step: SpeedStep, samples: Sequence[RunSample]) -> AnswerLength | None:
    """Return the answer length the *samples* measured, or ``None`` before there are enough.

    From :data:`MIN_ANSWER_SAMPLES` on, the run figures replace the catalog's: they are this
    class's error level with this model and these instructions, which no test can know.  From
    :data:`PERCENTILE_SAMPLES` on the range is the 10th to the 90th percentile, so one runaway
    answer no longer widens it.
    """
    values = sorted(_answer_values(step, samples))
    if len(values) < MIN_ANSWER_SAMPLES:
        return None
    if len(values) >= PERCENTILE_SAMPLES:
        low, high = percentile(values, 10), percentile(values, 90)
    else:
        low, high = values[0], values[-1]
    return AnswerLength(low=round(low), typical=round(median(values)), high=round(high))


def _most_loaded(samples: Sequence[RunSample]) -> bool:
    """Return whether most of the *samples* paid a model load, so the models evict each other."""
    if not samples:
        return False
    loaded = sum(1 for sample in samples if sample.load_s > 0)
    return loaded * 2 > len(samples)


def _measured_speed(record: SpeedRecord, step: SpeedStep) -> ModelSpeed | None:
    """Return the speed *step* was measured at, or ``None``.

    A content review that shares the proofreading model was not measured on its own: the
    proofreading measurement is the same model on the same machine, so it serves.
    """
    speed = record.measured(step)
    if speed is None and step is SpeedStep.REVIEW:
        return record.measured(SpeedStep.PROOFREADING)
    return speed


def _expected_band(
    tag: str,
    step: SpeedStep,
    hardware: HardwareDetection | None,
    factors: MachineFactors,
) -> SpeedBand | None:
    """Return the band *tag* is expected to reach for *step* here, or ``None``.

    The same path the Add-ons list takes for its "(expected)" label: the catalog's reference
    speed, scaled by how much slower this computer is than the reference machine.  ``None``
    while this computer is unknown, and for a model the catalog does not know.
    """
    if hardware is None:
        return None
    category: ModelCategory = "ocr" if step is SpeedStep.READING else "general"
    rating = rate_model(
        tag,
        category,
        hardware.profile,
        system_memory_gb=hardware.system_memory_gb,
        card_gb=hardware.card_gb,
        machine_factor=factors.for_category(category),
    )
    return rating.speed


def _outcome(record: SpeedRecord, step: SpeedStep) -> StepOutcome | None:
    """Return what *step* of *record* became, or ``None`` when it did not run."""
    stored = record.step(step)
    return None if stored is None else stored.outcome


def _message(record: SpeedRecord, step: SpeedStep) -> str:
    """Return the message *step* of *record* carries, or ``""``."""
    stored = record.step(step)
    return "" if stored is None else stored.message


def _class_set(
    rows: list[StepEstimate], *, swaps: bool, load_s: Sequence[float]
) -> TimeRange | None:
    """Return how long a class set takes with *rows*, or ``None`` when there is no estimate."""
    if not rows:
        return None
    return batch_time(
        CLASS_SET_FILES,
        CLASS_SET_PAGES,
        [row.per_page for row in rows],
        swaps=swaps,
        load_s=load_s,
    )


def _on_battery() -> bool | None:
    """Return whether this computer runs on battery power, or ``None`` when unknown."""
    from ..infrastructure.power import on_battery  # noqa: PLC0415

    return on_battery()
