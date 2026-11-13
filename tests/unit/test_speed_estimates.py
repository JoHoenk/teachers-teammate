"""Unit tests for teachers_teammate.application.speed_estimates."""

from __future__ import annotations

from collections.abc import Sequence
import dataclasses
import threading
from pathlib import Path
from typing import cast

import pytest

from teachers_teammate.application.speed_estimates import (
    CLASS_SET_FILES,
    CLASS_SET_PAGES,
    SpeedChoices,
    SpeedEstimateService,
    SpeedSource,
)
from teachers_teammate.domain.model_catalog import HardwareDetection, DetectionReason
from teachers_teammate.domain.performance_estimate import (
    AnswerLength,
    ModelSpeed,
    SpeedBand,
    SpeedStep,
    StepOutcome,
    TYPICAL_PAGE_WORDS,
    proofreading_time,
)
from teachers_teammate.infrastructure.ollama_utils import (
    PROOFREADING_NUM_CTX,
    ServerFacts,
)
from teachers_teammate.infrastructure.performance_probe import (
    PerformanceProbe,
    ProbeResult,
    StepResult,
)
from teachers_teammate.infrastructure.speed_store import (
    RunSample,
    SampleKey,
    SampleStore,
    SpeedKey,
    SpeedRecord,
    SpeedStore,
    StepRecord,
)

_URL = "http://127.0.0.1:11434"


def _speed(*, write_tps: float = 100.0, on_card: float | None = 0.8) -> ModelSpeed:
    """Return a measured speed of 1000 tokens read and *write_tps* written per second."""
    return ModelSpeed(
        read_tps=1000.0,
        write_tps=write_tps,
        load_s=3.0,
        on_card=on_card,
        image_tokens=1500,
        chars_per_token=4.2,
    )


def _hardware(*, gpu: str | None = "AMD Radeon RX 9070 XT") -> HardwareDetection:
    """Return a detection of a computer with (or without) a graphics card."""
    return HardwareDetection(
        profile="16gb",
        reason=DetectionReason.GPU_DETECTED if gpu else DetectionReason.NO_GPU_FOUND,
        gpu_name=gpu,
        memory_gb=16.0 if gpu else None,
    )


def _choices(
    *,
    reading_model: str = "glm-ocr:q8_0",
    writing_model: str = "gemma4:e4b",
    review_model: str = "",
    language: str = "English",
    num_ctx: int = PROOFREADING_NUM_CTX,
) -> SpeedChoices:
    """Return the choices of a page that reads and proofreads on Ollama."""
    return SpeedChoices(
        ollama_url=_URL,
        reading_model=reading_model,
        writing_model=writing_model,
        review_model=review_model,
        language=language,
        num_ctx=num_ctx,
    )


def _record(  # noqa: PLR0913  # a record is many independent facts, all keyword-only
    *,
    tag: str = "gemma4:e4b",
    digest: str = "sha256:aaa",
    version: str = "0.40.15",
    gpu: str = "AMD Radeon RX 9070 XT",
    steps: tuple[StepRecord, ...] | None = None,
    measured_at: str = "2026-11-13T10:00:00+00:00",
    shared: bool = False,
    swaps: bool = False,
    on_battery: bool | None = False,
) -> SpeedRecord:
    """Return a stored measurement of *tag*."""
    return SpeedRecord(
        key=SpeedKey(server=_URL, tag=tag, digest=digest, ollama_version=version, gpu=gpu),
        measured_at=measured_at,
        steps=steps
        if steps is not None
        else (
            StepRecord(SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed()),
            StepRecord(SpeedStep.READING, StepOutcome.MEASURED, speed=_speed()),
        ),
        on_battery=on_battery,
        shared=shared,
        swaps=swaps,
    )


def _sample(
    *,
    read_tps: float = 1000.0,
    write_tps: float = 100.0,
    load_s: float = 0.0,
    image_tokens: int = 0,
    answer_tokens: int = 1000,
    words: int = TYPICAL_PAGE_WORDS,
    at: str = "2026-11-13T10:00:00+00:00",
) -> RunSample:
    """Return one sample of a run: a page read, or a document proofread."""
    return RunSample(
        read_tps=read_tps,
        write_tps=write_tps,
        load_s=load_s,
        image_tokens=image_tokens,
        answer_tokens=answer_tokens,
        words=words,
        pieces=1,
        at=at,
    )


def _add_samples(
    samples: SampleStore,
    step: SpeedStep,
    values: Sequence[RunSample],
    *,
    tag: str = "gemma4:e4b",
    mode: str = "review",
) -> None:
    """Store *values* as samples of *tag* for *step*."""
    for sample in values:
        samples.add(SampleKey(server=_URL, tag=tag, step=step, mode=mode), sample)


class _FakeClient:
    """A stand-in for :class:`OllamaClient` that answers with fixed server facts."""

    def __init__(self, facts: ServerFacts) -> None:
        self.facts = facts
        self.calls = 0

    def server_facts(self) -> ServerFacts:
        """Return the scripted facts and count the call."""
        self.calls += 1
        return self.facts


class _FakeProbe:
    """A stand-in for :class:`PerformanceProbe` that returns a scripted result."""

    def __init__(self, result: ProbeResult) -> None:
        self.result = result
        self.requests: list[object] = []

    def run(self, request: object) -> ProbeResult:
        """Record the request and return the scripted result."""
        self.requests.append(request)
        return self.result


def _service(
    tmp_path: Path,
    *,
    facts: ServerFacts | None = None,
    probe: _FakeProbe | None = None,
    samples: SampleStore | None = None,
) -> tuple[SpeedEstimateService, SpeedStore, _FakeClient]:
    """Return a service over a temporary store, with a fake server.

    Both stores live under *tmp_path*, so a test never reads the samples of the machine it runs
    on.
    """
    store = SpeedStore(tmp_path / "measurements.json")
    client = _FakeClient(facts if facts is not None else ServerFacts())
    service = SpeedEstimateService(
        store=store,
        samples=samples if samples is not None else SampleStore(tmp_path / "samples.json"),
        client_factory=lambda _url, _cancel: client,
        probe_factory=(
            (lambda *_a, **_k: cast(PerformanceProbe, probe)) if probe is not None else None
        ),
    )
    return service, store, client


# ── the choices ────────────────────────────────────────────────────────────


def test_choices_name_the_models_a_run_would_use() -> None:
    """
    Given  settings whose model fields are empty
    When   SpeedChoices.from_settings() resolves them
    Then   the models a run would use are named, not the empty fields
    """
    settings = {
        "ollama_url": _URL,
        "ocr_engine": "ollama",
        "ocr_model": "glm-ocr:q8_0",
        "correction_provider": "ollama",
        "correction_model": "",
        "evaluate_provider": "ollama",
        "evaluate_model": "",
        "language": "German",
    }

    choices = SpeedChoices.from_settings(
        settings, effective_model=lambda _p, _m, task: f"default-{task}"
    )

    assert choices.writing_model == "default-correction"
    assert choices.review_model == "default-evaluation"
    assert choices.reading_model == "glm-ocr:q8_0"
    assert choices.language == "German"


def test_choices_leave_out_a_step_that_does_not_run_on_ollama() -> None:
    """
    Given  settings whose proofreading runs at an online service
    When   SpeedChoices.from_settings() resolves them
    Then   the proofreading model is empty, because there is nothing on this computer to measure
    """
    settings = {
        "ocr_engine": "tesseract",
        "correction_provider": "mistral",
        "correction_model": "mistral-large-latest",
        "evaluate_provider": "ollama",
        "evaluate_model": "gemma4:e4b",
    }

    choices = SpeedChoices.from_settings(settings, effective_model=lambda _p, model, _t: model)

    assert choices.reading_model == ""
    assert choices.writing_model == ""
    assert choices.review_model == "gemma4:e4b"


def test_choices_list_each_model_once() -> None:
    """
    Given  choices whose review model is the proofreading model
    When   the models are listed
    Then   the model appears once
    """
    choices = _choices(review_model="gemma4:e4b")

    assert choices.models() == ("glm-ocr:q8_0", "gemma4:e4b")


def test_choices_of_a_run_leave_out_the_steps_that_are_switched_off(tmp_path: Path) -> None:
    """
    Given  a run that only recognises text (proofreading and review switched off)
    When   SpeedChoices.from_config() resolves it
    Then   only the reading model is named, so the time left is not charged for the other steps
    """
    from tests.conftest import make_config  # noqa: PLC0415

    config = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="glm-ocr:q8_0",
        recognition_enabled=True,
        correction_enabled=False,
        correction_provider="ollama",
        evaluation_enabled=False,
        evaluate_provider="ollama",
    )

    choices = SpeedChoices.from_config(config, effective_model=lambda _p, m, _t: m or "default")

    assert choices.reading_model == "glm-ocr:q8_0"
    assert choices.writing_model == ""
    assert choices.review_model == ""


def test_choices_of_a_run_without_recognition_have_no_reading_model(tmp_path: Path) -> None:
    """
    Given  a run with text recognition switched off and proofreading on
    When   SpeedChoices.from_config() resolves it
    Then   the reading model is empty and the proofreading model is named
    """
    from tests.conftest import make_config  # noqa: PLC0415

    config = make_config(
        tmp_path,
        ocr_engine="ollama",
        ocr_model="glm-ocr:q8_0",
        recognition_enabled=False,
        correction_enabled=True,
        correction_provider="ollama",
        correction_model="gemma4:e4b",
    )

    choices = SpeedChoices.from_config(config, effective_model=lambda _p, m, _t: m or "default")

    assert choices.reading_model == ""
    assert choices.writing_model == "gemma4:e4b"


def test_choices_carry_the_proofreading_output_mode() -> None:
    """
    Given  settings that ask for corrections only, and settings with a typo in the mode
    When   SpeedChoices.from_settings() resolves them
    Then   the first says "text" and the second falls back to "review", as everywhere else
    """
    text = SpeedChoices.from_settings(
        {"correction_output": "text"}, effective_model=lambda _p, m, _t: m
    )
    typo = SpeedChoices.from_settings(
        {"correction_output": "texxt"}, effective_model=lambda _p, m, _t: m
    )

    assert text.correction_output == "text"
    assert typo.correction_output == "review"


# ── the test ───────────────────────────────────────────────────────────────


def test_the_test_stores_one_record_per_model(tmp_path: Path) -> None:
    """
    Given  a probe that measured a reading and a writing model
    When   the speed test runs
    Then   each model has its own record, keyed by its digest and the card
    """
    facts = ServerFacts(
        version="0.40.15",
        digests={"glm-ocr:q8_0": "sha256:ocr", "gemma4:e4b": "sha256:write"},
    )
    probe = _FakeProbe(
        ProbeResult(
            steps=(
                StepResult(SpeedStep.READING, "glm-ocr:q8_0", StepOutcome.MEASURED, _speed()),
                StepResult(SpeedStep.PROOFREADING, "gemma4:e4b", StepOutcome.MEASURED, _speed()),
            ),
            facts=facts,
        )
    )
    service, store, _client = _service(tmp_path, probe=probe)

    service.test_speed(_choices(), hardware=_hardware())

    records = {item.key.tag: item for item in store.records()}
    assert set(records) == {"glm-ocr:q8_0", "gemma4:e4b"}
    assert records["gemma4:e4b"].key.digest == "sha256:write"
    assert records["gemma4:e4b"].key.ollama_version == "0.40.15"
    assert records["gemma4:e4b"].key.gpu == "AMD Radeon RX 9070 XT"
    assert records["gemma4:e4b"].measured(SpeedStep.PROOFREADING) == _speed()


def test_the_test_sends_the_choices_to_the_probe(tmp_path: Path) -> None:
    """
    Given  choices with a language and a window
    When   the speed test runs
    Then   the probe is asked for exactly those models, that language and that window
    """
    probe = _FakeProbe(ProbeResult())
    service, _store, _client = _service(tmp_path, probe=probe)

    service.test_speed(_choices(language="French", num_ctx=4096))

    request = probe.requests[0]
    assert request.reading_model == "glm-ocr:q8_0"
    assert request.writing_model == "gemma4:e4b"
    assert request.language == "French"
    assert request.num_ctx == 4096


def test_a_test_that_measured_nothing_stores_nothing(tmp_path: Path) -> None:
    """
    Given  a probe that could not reach the server
    When   the speed test runs
    Then   the store stays empty
    """
    probe = _FakeProbe(ProbeResult(error="cannot connect to Ollama"))
    service, store, _client = _service(tmp_path, probe=probe)

    service.test_speed(_choices())

    assert store.records() == ()


def test_the_test_caches_the_server_facts(tmp_path: Path) -> None:
    """
    Given  a probe that reported the server's version and digests
    When   the speed test runs
    Then   the facts are cached, so the panel needs no second request
    """
    facts = ServerFacts(version="0.40.15", digests={"gemma4:e4b": "sha256:aaa"})
    probe = _FakeProbe(
        ProbeResult(
            steps=(
                StepResult(SpeedStep.PROOFREADING, "gemma4:e4b", StepOutcome.MEASURED, _speed()),
            ),
            facts=facts,
        )
    )
    service, _store, _client = _service(tmp_path, probe=probe)

    service.test_speed(_choices())

    assert service.cached_server_facts(_URL) == facts


def test_a_stopped_test_stores_nothing(tmp_path: Path) -> None:
    """
    Given  a stop that was already requested
    When   the speed test runs
    Then   RunStoppedError comes out of the probe and the store stays empty
    """
    from teachers_teammate.exceptions import RunStoppedError  # noqa: PLC0415

    class _StoppingProbe:
        def run(self, _request: object) -> ProbeResult:
            raise RunStoppedError

    store = SpeedStore(tmp_path / "measurements.json")
    service = SpeedEstimateService(
        store=store,
        client_factory=lambda _url, _cancel: _FakeClient(ServerFacts()),
        probe_factory=lambda *_a, **_k: cast(PerformanceProbe, _StoppingProbe()),
    )
    event = threading.Event()
    event.set()

    with pytest.raises(RunStoppedError):
        service.test_speed(_choices(), cancel_event=event)

    assert store.records() == ()


def test_the_client_of_the_test_gets_the_stop_event(tmp_path: Path) -> None:
    """
    Given  a speed test started with a cancel event
    When   the service builds the client for the server
    Then   the client gets the same event, so a stop reaches a request that is waiting
    """
    seen: list[threading.Event | None] = []

    def client_factory(_url: str, cancel: threading.Event | None) -> object:
        seen.append(cancel)
        return _FakeClient(ServerFacts())

    service = SpeedEstimateService(
        store=SpeedStore(tmp_path / "measurements.json"),
        samples=SampleStore(tmp_path / "samples.json"),
        client_factory=client_factory,
        probe_factory=lambda *_a, **_k: cast(PerformanceProbe, _FakeProbe(ProbeResult())),
    )
    event = threading.Event()

    service.test_speed(_choices(), cancel_event=event)

    assert seen == [event]


def test_a_test_that_could_not_reach_the_server_stores_nothing(tmp_path: Path) -> None:
    """
    Given  a stored measurement and a test whose result carries an error
    When   the speed test runs
    Then   the stored measurement is unchanged and the empty server facts are not cached
    """
    probe = _FakeProbe(ProbeResult(error="Cannot connect to Ollama"))
    service, store, _client = _service(tmp_path, probe=probe)
    store.save(_record())

    result = service.test_speed(_choices())

    assert result.error
    assert len(store.records()) == 1
    assert service.cached_server_facts(_URL) is None


# ── the summary ────────────────────────────────────────────────────────────


def test_the_summary_reports_a_measured_step_per_page(tmp_path: Path) -> None:
    """
    Given  a stored measurement of the proofreading model
    When   the summary is asked for
    Then   the proofreading row carries a range of seconds per page and its band
    """
    service, store, _client = _service(tmp_path)
    store.save(
        _record(
            steps=(
                StepRecord(
                    SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed(write_tps=1000.0)
                ),
            )
        )
    )

    summary = service.speed_summary(_choices(reading_model=""))

    row = summary.step(SpeedStep.PROOFREADING)
    assert row is not None
    assert row.model == "gemma4:e4b"
    assert row.source is SpeedSource.MEASURED
    assert row.per_page.low_s > 0
    assert row.per_page.high_s > row.per_page.low_s
    assert row.band is SpeedBand.FAST


def test_the_summary_has_no_row_for_a_step_that_was_not_measured(tmp_path: Path) -> None:
    """
    Given  a stored measurement of the writing model only
    When   the summary is asked for both steps
    Then   only the proofreading row is there
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())

    summary = service.speed_summary(_choices())

    assert summary.step(SpeedStep.READING) is None
    assert summary.step(SpeedStep.PROOFREADING) is not None


def test_a_review_that_shares_the_proofreading_model_has_its_own_row(tmp_path: Path) -> None:
    """
    Given  a stored measurement of the proofreading model and a review that names the same model
    When   the summary is asked for
    Then   the review row is there, from the same measurement
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())

    summary = service.speed_summary(_choices(reading_model="", review_model="gemma4:e4b"))

    review = summary.step(SpeedStep.REVIEW)
    assert review is not None
    assert review.model == "gemma4:e4b"


def test_a_review_that_does_not_run_on_ollama_has_no_row(tmp_path: Path) -> None:
    """
    Given  a stored measurement of the proofreading model and a review at an online service
           (the choices name no review model)
    When   the summary is asked for
    Then   there is no review row, and the page time is the proofreading alone
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())

    summary = service.speed_summary(_choices(reading_model="", review_model=""))

    assert summary.step(SpeedStep.REVIEW) is None
    proofreading = summary.step(SpeedStep.PROOFREADING)
    assert proofreading is not None
    assert summary.per_page == proofreading.per_page


def test_the_summary_reports_the_class_set(tmp_path: Path) -> None:
    """
    Given  a stored measurement of both models
    When   the summary is asked for
    Then   the class set is the sum of both steps over every page of every file
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())
    store.save(
        _record(
            tag="glm-ocr:q8_0",
            steps=(StepRecord(SpeedStep.READING, StepOutcome.MEASURED, speed=_speed()),),
        )
    )

    summary = service.speed_summary(_choices())

    per_page = sum(row.per_page.low_s for row in summary.steps)
    assert summary.class_set.low_s == pytest.approx(per_page * CLASS_SET_PAGES * CLASS_SET_FILES)


def test_swapping_models_lengthen_the_class_set(tmp_path: Path) -> None:
    """
    Given  a measurement that found the models evicting each other
    When   the summary is asked for
    Then   the class set is longer than the same run without swapping
    """
    service, store, _client = _service(tmp_path)
    store.save(_record(swaps=True))
    swapping = service.speed_summary(_choices()).class_set

    store.save(_record(swaps=False))
    staying = service.speed_summary(_choices()).class_set

    assert swapping.low_s > staying.low_s


def test_the_summary_carries_the_date_and_the_flags(tmp_path: Path) -> None:
    """
    Given  a measurement taken on battery power while another model was loaded
    When   the summary is asked for
    Then   it carries the date, the battery flag and the shared flag
    """
    service, store, _client = _service(tmp_path)
    store.save(_record(shared=True, on_battery=True))

    summary = service.speed_summary(_choices())

    assert summary.measured_at == "2026-11-13T10:00:00+00:00"
    assert summary.on_battery is True
    assert summary.shared is True


def test_a_changed_digest_makes_the_summary_out_of_date(tmp_path: Path) -> None:
    """
    Given  a measurement of a model whose digest has since changed
    When   the summary is asked for with the current facts
    Then   it is marked out of date, so the panel can say so
    """
    service, store, client = _service(
        tmp_path, facts=ServerFacts(version="0.40.15", digests={"gemma4:e4b": "sha256:new"})
    )
    store.save(_record(digest="sha256:old"))
    service.refresh_server_facts(_URL)

    summary = service.speed_summary(_choices(reading_model=""))

    assert summary.out_of_date is True
    assert summary.step(SpeedStep.PROOFREADING) is not None


def test_unknown_server_facts_do_not_mark_a_measurement_out_of_date(tmp_path: Path) -> None:
    """
    Given  a measurement and no cached server facts yet
    When   the summary is asked for
    Then   it is not marked out of date, because nothing says it is
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())

    summary = service.speed_summary(_choices(reading_model=""))

    assert summary.out_of_date is False


def test_cached_only_never_contacts_the_server(tmp_path: Path) -> None:
    """
    Given  a service whose server facts were never fetched
    When   the summary is asked for with ``cached_only``
    Then   the server is not asked, so the GUI thread never waits
    """
    service, store, client = _service(tmp_path)
    store.save(_record())

    service.speed_summary(_choices(), cached_only=True)

    assert client.calls == 0


def test_the_summary_asks_the_server_when_it_may(tmp_path: Path) -> None:
    """
    Given  a service with a reachable server
    When   the summary is asked for without ``cached_only``
    Then   the server facts are fetched once
    """
    service, store, client = _service(tmp_path)
    store.save(_record())

    service.speed_summary(_choices(), cached_only=False)

    assert client.calls == 1


def test_a_share_of_zero_on_a_computer_with_a_card_is_reported(tmp_path: Path) -> None:
    """
    Given  a measurement that found the model in main memory on a computer with a card
    When   the summary is asked for
    Then   it says the card was not used
    """
    service, store, _client = _service(tmp_path)
    store.save(
        _record(
            steps=(
                StepRecord(SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed(on_card=0.0)),
            )
        )
    )

    summary = service.speed_summary(_choices(reading_model=""), hardware=_hardware())

    assert summary.card_not_used is True


def test_a_share_of_zero_without_a_card_is_not_reported(tmp_path: Path) -> None:
    """
    Given  a measurement that found the model in main memory on a computer without a card
    When   the summary is asked for
    Then   it says nothing about the card, because there is none to use
    """
    service, store, _client = _service(tmp_path)
    store.save(
        _record(
            steps=(
                StepRecord(SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed(on_card=0.0)),
            )
        )
    )

    summary = service.speed_summary(_choices(reading_model=""), hardware=_hardware(gpu=None))

    assert summary.card_not_used is False


def test_a_step_that_hit_the_time_limit_is_reported(tmp_path: Path) -> None:
    """
    Given  a measurement whose reading step hit the test's time limit
    When   the summary is asked for
    Then   it says a step was too slow, and the row carries the server's message
    """
    service, store, _client = _service(tmp_path)
    store.save(
        _record(
            tag="glm-ocr:q8_0",
            steps=(
                StepRecord(
                    SpeedStep.READING,
                    StepOutcome.TOO_SLOW,
                    speed=_speed(),
                    message="the answer took longer than 90 s",
                ),
            ),
        )
    )

    summary = service.speed_summary(_choices(writing_model=""))

    assert summary.too_slow is True
    assert "90 s" in summary.step(SpeedStep.READING).message


def test_an_empty_store_gives_an_empty_summary(tmp_path: Path) -> None:
    """
    Given  a store with no measurement
    When   the summary is asked for
    Then   it has no rows and no class set, so the panel shows nothing rather than a guess
    """
    service, _store, _client = _service(tmp_path)

    summary = service.speed_summary(_choices())

    assert summary.steps == ()
    assert summary.class_set is None
    assert summary.measured_at == ""


# ── what real runs measured ────────────────────────────────────────────────


def test_three_samples_replace_the_catalog_answer_length(tmp_path: Path) -> None:
    """
    Given  three samples of the proofreading step whose answers are shorter than the catalog's
    When   the summary is asked for
    Then   the per-page range is shorter than the catalog's figure, because the run knows this
           class's error level and no test can
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(), _sample()])
    below = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample()])
    at = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    assert below is not None
    assert at is not None
    assert below.sample_count == 2
    assert at.sample_count == 3
    assert at.per_page.typical_s < below.per_page.typical_s


def test_ten_samples_narrow_the_range_to_the_percentiles(tmp_path: Path) -> None:
    """
    Given  nine samples with one answer length and a tenth whose answer is far longer
    When   the summary is asked for
    Then   the high end is below the runaway answer, which the ends would have taken
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    runaway = _sample(answer_tokens=25000)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample() for _ in range(8)] + [runaway])
    nine = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample()])
    ten = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    assert nine is not None
    assert ten is not None
    assert nine.per_page.high_s == pytest.approx(
        proofreading_time(
            ModelSpeed(read_tps=1000.0, write_tps=100.0),
            TYPICAL_PAGE_WORDS,
            AnswerLength(low=400, typical=400, high=10000),
        ).high_s
    )
    assert ten.per_page.high_s < nine.per_page.high_s


def test_text_mode_is_estimated_with_the_short_text_mode_answer(tmp_path: Path) -> None:
    """
    Given  a measurement and no samples, and choices that ask for corrections only
    When   the summary is asked for under each mode
    Then   the text mode estimate is far below the review mode one: the answer is about as long
           as the text, not as long as a review
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())

    review = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)
    text = service.speed_summary(
        dataclasses.replace(_choices(reading_model=""), correction_output="text")
    ).step(SpeedStep.PROOFREADING)

    assert review is not None
    assert text is not None
    assert text.per_page.typical_s < review.per_page.typical_s / 3


def test_text_mode_uses_the_answer_length_its_own_samples_measured(tmp_path: Path) -> None:
    """
    Given  three text-mode samples whose answers are twice the text-mode constant, and three
           review-mode samples with much longer answers
    When   the summary is asked for under the text mode
    Then   the estimate follows the text-mode samples, not the constant and not the review ones
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(answer_tokens=1000)] * 3, mode="review")
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(answer_tokens=700)] * 3, mode="text")
    choices = dataclasses.replace(_choices(reading_model=""), correction_output="text")

    row = service.speed_summary(choices).step(SpeedStep.PROOFREADING)

    expected = proofreading_time(
        ModelSpeed(read_tps=1000.0, write_tps=100.0),
        TYPICAL_PAGE_WORDS,
        AnswerLength(low=1, typical=1, high=1),
        "text",
        text_answer=AnswerLength(low=280, typical=280, high=280),
    )
    assert row is not None
    assert row.per_page.typical_s == pytest.approx(expected.typical_s, rel=0.05)


def test_review_mode_ignores_the_samples_of_text_mode(tmp_path: Path) -> None:
    """
    Given  only text-mode samples (short answers) and choices in review mode
    When   the summary is asked for
    Then   the estimate keeps the catalog's review answer length: short text-mode answers say
           nothing about a review
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(answer_tokens=350)] * 4, mode="text")
    only_text = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(answer_tokens=350)] * 4, mode="review")
    with_review = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    assert only_text is not None
    assert with_review is not None
    assert only_text.per_page.typical_s > with_review.per_page.typical_s


def test_the_rate_is_the_median_of_the_samples(tmp_path: Path) -> None:
    """
    Given  three samples of which one came from a server that was busy with something else
    When   the summary is asked for
    Then   the figure is the one the two quiet samples give, not the mean of all three
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(), _sample(), _sample(write_tps=10.0)])
    busy = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    quiet_samples = SampleStore(tmp_path / "quiet.json")
    quiet_service, _store, _client = _service(tmp_path, samples=quiet_samples)
    _add_samples(quiet_samples, SpeedStep.PROOFREADING, [_sample(), _sample(), _sample()])
    quiet = quiet_service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    assert busy is not None
    assert quiet is not None
    assert busy.per_page.typical_s == pytest.approx(quiet.per_page.typical_s)


def test_samples_win_over_a_stored_measurement(tmp_path: Path) -> None:
    """
    Given  a speed test that measured the writing model and samples of real runs that are slower
    When   the summary is asked for
    Then   the row reports the runs' figure and counts them, because that is what a run does
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, store, _client = _service(tmp_path, samples=samples)
    store.save(
        _record(
            steps=(
                StepRecord(
                    SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed(write_tps=1000.0)
                ),
            )
        )
    )
    tested = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(), _sample(), _sample()])
    ran = service.speed_summary(_choices(reading_model="")).step(SpeedStep.PROOFREADING)

    assert tested is not None
    assert ran is not None
    assert tested.sample_count == 0
    assert ran.sample_count == 3
    assert ran.per_page.typical_s > tested.per_page.typical_s


def test_most_samples_paying_a_load_marks_the_models_as_swapping(tmp_path: Path) -> None:
    """
    Given  three samples of which two paid a model load
    When   the summary is asked for
    Then   it says the models evict each other, and how many seconds of loading a file pays
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(
        samples,
        SpeedStep.PROOFREADING,
        [_sample(load_s=3.0), _sample(load_s=3.0), _sample()],
    )

    summary = service.speed_summary(_choices(reading_model=""))

    assert summary.swaps is True
    assert summary.swap_load_s == pytest.approx(3.0)


def test_a_minority_of_loads_does_not_mark_the_models_as_swapping(tmp_path: Path) -> None:
    """
    Given  three samples of which one paid a model load
    When   the summary is asked for
    Then   the models are not reported as evicting each other, because they mostly stay loaded
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(
        samples,
        SpeedStep.PROOFREADING,
        [_sample(load_s=3.0), _sample(), _sample()],
    )

    summary = service.speed_summary(_choices(reading_model=""))

    assert summary.swaps is False
    assert summary.swap_load_s == 0.0


def test_the_summary_counts_the_documents_the_samples_came_from(tmp_path: Path) -> None:
    """
    Given  three samples of the proofreading step
    When   the summary is asked for
    Then   it counts three documents, so the panel can say where its figure comes from
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample(), _sample(), _sample()])

    summary = service.speed_summary(_choices(reading_model=""))

    assert summary.sample_count == 3
    assert summary.sample_pages is False


def test_the_summary_counts_pages_when_only_text_recognition_ran(tmp_path: Path) -> None:
    """
    Given  three samples of the reading step and none of the proofreading step
    When   the summary is asked for
    Then   it counts three pages, because no document went through the writing model
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(
        samples,
        SpeedStep.READING,
        [_sample(image_tokens=1500) for _ in range(3)],
        tag="glm-ocr:q8_0",
        mode="",
    )

    summary = service.speed_summary(_choices())

    assert summary.sample_count == 3
    assert summary.sample_pages is True


def test_the_summary_adds_up_the_steps_per_page(tmp_path: Path) -> None:
    """
    Given  a stored measurement of both models
    When   the summary is asked for
    Then   one page takes the sum of the rows, because a page pays every step in turn
    """
    service, store, _client = _service(tmp_path)
    store.save(_record())
    store.save(
        _record(
            tag="glm-ocr:q8_0",
            steps=(StepRecord(SpeedStep.READING, StepOutcome.MEASURED, speed=_speed()),),
        )
    )

    summary = service.speed_summary(_choices())

    assert summary.per_page is not None
    assert summary.per_page.low_s == pytest.approx(sum(row.per_page.low_s for row in summary.steps))
    assert summary.per_page.high_s == pytest.approx(
        sum(row.per_page.high_s for row in summary.steps)
    )


# ── the machine factor ─────────────────────────────────────────────────────


def test_the_machine_factor_compares_a_measured_model_with_its_reference(tmp_path: Path) -> None:
    """
    Given  a measurement of a catalogued model at half its reference speed
    When   the machine factor is asked for
    Then   it is about a half
    """
    from teachers_teammate.domain.model_catalog import find_spec  # noqa: PLC0415

    spec = find_spec("gemma4:e4b")
    assert spec is not None and spec.reference_speed is not None
    service, store, _client = _service(tmp_path)
    store.save(
        _record(
            steps=(
                StepRecord(
                    SpeedStep.PROOFREADING,
                    StepOutcome.MEASURED,
                    speed=_speed(write_tps=spec.reference_speed.write_tps / 2),
                ),
            )
        )
    )

    assert service.machine_factors(_URL).writing == pytest.approx(0.5)


def test_the_machine_factor_is_unknown_without_a_measurement(tmp_path: Path) -> None:
    """
    Given  a store with no measurement
    When   the machine factors are asked for
    Then   both are ``None``, so a caller cannot mistake them for "the same speed"
    """
    service, _store, _client = _service(tmp_path)

    factors = service.machine_factors(_URL)

    assert factors.writing is None
    assert factors.reading is None


def test_the_machine_factor_ignores_another_server(tmp_path: Path) -> None:
    """
    Given  a measurement taken on another server
    When   the machine factors are asked for this one
    Then   they are ``None``, because another machine's speed says nothing about this one
    """
    service, store, _client = _service(tmp_path)
    store.save(
        SpeedRecord(
            key=SpeedKey(server="http://192.168.1.20:11434", tag="gemma4:e4b"),
            measured_at="2026-11-13T10:00:00+00:00",
            steps=(StepRecord(SpeedStep.PROOFREADING, StepOutcome.MEASURED, speed=_speed()),),
        )
    )

    assert service.machine_factors(_URL).writing is None


def test_the_machine_factor_is_the_median_of_several_measured_models(tmp_path: Path) -> None:
    """
    Given  three measured models at half, half and a tenth of their reference speed
    When   the machine factors are asked for
    Then   the middle one counts, so one model that does not use the card well cannot move it
    """
    from teachers_teammate.domain.model_catalog import find_spec  # noqa: PLC0415

    service, store, _client = _service(tmp_path)
    for tag, share in (("qwen3:4b", 0.5), ("gemma4:e4b", 0.5), ("gemma4:12b", 0.1)):
        spec = find_spec(tag)
        assert spec is not None and spec.reference_speed is not None
        store.save(
            _record(
                tag=tag,
                steps=(
                    StepRecord(
                        SpeedStep.PROOFREADING,
                        StepOutcome.MEASURED,
                        speed=_speed(write_tps=spec.reference_speed.write_tps * share),
                    ),
                ),
            )
        )

    assert service.machine_factors(_URL).writing == pytest.approx(0.5)


def test_the_machine_factor_comes_from_real_runs_too(tmp_path: Path) -> None:
    """
    Given  samples of real runs of a catalogued model and no speed test
    When   the machine factors are asked for
    Then   the runs give the factor, because they are what a run does
    """
    from teachers_teammate.domain.model_catalog import find_spec  # noqa: PLC0415

    spec = find_spec("gemma4:e4b")
    assert spec is not None and spec.reference_speed is not None
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(
        samples,
        SpeedStep.PROOFREADING,
        [_sample(write_tps=spec.reference_speed.write_tps / 2) for _ in range(3)],
    )

    assert service.machine_factors(_URL).writing == pytest.approx(0.5)


# ── one model at a time, for the model lists' tooltips ─────────────────────


def test_page_times_answer_for_the_measured_models_only(tmp_path: Path) -> None:
    """
    Given  a measurement of one proofreading model, with the server's facts known
    When   the page times of that model and of an unmeasured one are asked for
    Then   only the measured model gets a figure, and it is the time per page of its step
    """
    facts = ServerFacts(version="0.40.15", digests={"gemma4:e4b": "sha256:aaa"})
    service, store, _client = _service(tmp_path, facts=facts)
    store.save(_record())
    service.refresh_server_facts(_URL)

    times = service.page_times(["gemma4:e4b", "qwen3:4b"], "general", _URL, hardware=_hardware())

    assert set(times) == {"gemma4:e4b"}
    assert times["gemma4:e4b"].low_s > 0
    assert times["gemma4:e4b"].high_s > times["gemma4:e4b"].low_s


def test_page_times_leave_out_a_measurement_that_no_longer_fits(tmp_path: Path) -> None:
    """
    Given  a measurement taken with another version of the model
    When   the page times are asked for with the server's current facts
    Then   the model is left out, because a figure that has moved on is worse than none
    """
    facts = ServerFacts(version="0.40.15", digests={"gemma4:e4b": "sha256:new"})
    service, store, _client = _service(tmp_path, facts=facts)
    store.save(_record(digest="sha256:old"))
    service.refresh_server_facts(_URL)

    assert service.page_times(["gemma4:e4b"], "general", _URL, hardware=_hardware()) == {}


def test_page_times_use_the_reading_step_for_a_reading_model(tmp_path: Path) -> None:
    """
    Given  a measurement of a reading model
    When   its page time is asked for as a reading model
    Then   the figure comes from the reading step, which is the one that reads a page image
    """
    facts = ServerFacts(version="0.40.15", digests={"glm-ocr:q8_0": "sha256:aaa"})
    service, store, _client = _service(tmp_path, facts=facts)
    store.save(_record(tag="glm-ocr:q8_0"))
    service.refresh_server_facts(_URL)

    times = service.page_times(["glm-ocr:q8_0"], "ocr", _URL, hardware=_hardware())

    assert times["glm-ocr:q8_0"].low_s > 0


def test_the_probe_is_told_whether_a_card_was_found(tmp_path: Path) -> None:
    """
    Given  a computer with a graphics card
    When   the speed test builds its probe
    Then   the probe knows a card was found, so a share of 0 can be reported
    """
    seen: dict[str, object] = {}

    def factory(_client: object, **kwargs: object) -> PerformanceProbe:
        seen.update(kwargs)
        return cast(PerformanceProbe, _FakeProbe(ProbeResult()))

    store = SpeedStore(tmp_path / "measurements.json")
    service = SpeedEstimateService(
        store=store,
        client_factory=lambda _url, _cancel: _FakeClient(ServerFacts()),
        probe_factory=factory,
    )

    service.test_speed(_choices(), hardware=_hardware())

    assert seen["gpu_found"] is True


def test_page_times_of_a_long_list_read_the_samples_once(tmp_path: Path, monkeypatch) -> None:
    """
    Given  a model list of twenty tags and samples for one of them
    When   the page times are asked for
    Then   the samples file is read once, not once per tag, and the sampled tag has its time
    """
    samples = SampleStore(tmp_path / "samples.json")
    service, _store, _client = _service(tmp_path, samples=samples)
    _add_samples(samples, SpeedStep.PROOFREADING, [_sample()] * 3)
    reads: list[int] = []
    original = samples.server_samples
    monkeypatch.setattr(samples, "server_samples", lambda url: reads.append(1) or original(url))
    monkeypatch.setattr(
        samples,
        "samples",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("one read per tag")),
    )
    tags = ["gemma4:e4b"] + [f"model{index}:7b" for index in range(19)]

    times = service.page_times(tags, "general", _URL)

    assert len(reads) == 1
    assert list(times) == ["gemma4:e4b"]
