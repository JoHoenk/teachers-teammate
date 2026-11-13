"""Tests for turning a run's requests into samples (teachers_teammate.infrastructure.run_stats)."""

from __future__ import annotations

from teachers_teammate.domain.performance_estimate import SpeedStep
from teachers_teammate.infrastructure.ollama_utils import ChatStats
from teachers_teammate.infrastructure.run_stats import LOAD_THRESHOLD_S, RunStatsCollector
from teachers_teammate.infrastructure.speed_store import RunSample, SampleKey

_SERVER = "http://127.0.0.1:11434"
_NANOS = 1_000_000_000


def _stats(
    *,
    prompt_tokens: int = 1000,
    answer_tokens: int = 200,
    read_s: float = 0.5,
    write_s: float = 4.0,
    load_s: float = 0.0,
) -> ChatStats:
    """Return the statistics of one request that took the given times."""
    return ChatStats(
        done_reason="stop",
        prompt_eval_count=prompt_tokens,
        eval_count=answer_tokens,
        total_duration=round((read_s + write_s + load_s) * _NANOS),
        load_duration=round(load_s * _NANOS),
        prompt_eval_duration=round(read_s * _NANOS),
        eval_duration=round(write_s * _NANOS),
    )


def _collector() -> tuple[RunStatsCollector, list[tuple[SampleKey, RunSample]]]:
    """Return a collector and the list its sink appends to."""
    seen: list[tuple[SampleKey, RunSample]] = []
    return RunStatsCollector(_SERVER, lambda key, sample: seen.append((key, sample))), seen


def test_one_step_of_requests_becomes_one_sample() -> None:
    """
    Given  a proofreading step that sent two requests for one document
    When   the step ends
    Then   one sample carries the summed tokens and times, and the number of requests
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats(prompt_tokens=1000, answer_tokens=200, write_s=4.0))
    collector.record("gemma4:e4b", _stats(prompt_tokens=500, answer_tokens=100, write_s=2.0))

    sample = collector.finish(words=250, mode="review")

    assert sample is not None
    assert sample.read_tps == 1500 / 1.0
    assert sample.write_tps == 300 / 6.0
    assert sample.answer_tokens == 300
    assert sample.words == 250
    assert sample.pieces == 2
    assert len(seen) == 1
    key, stored = seen[0]
    assert key == SampleKey(_SERVER, "gemma4:e4b", SpeedStep.PROOFREADING, "review")
    assert stored == sample


def test_a_step_that_sent_no_request_gives_no_sample() -> None:
    """
    Given  a step that was marked as running but sent nothing (a result from the cache)
    When   the step ends
    Then   there is no sample and the sink is not called
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)

    assert collector.finish(words=250, mode="review") is None
    assert seen == []


def test_a_request_outside_a_step_is_dropped() -> None:
    """
    Given  a request that arrives while no step is marked (the model check, the speed test)
    When   it is recorded
    Then   it belongs to no sample: the next step starts with an empty buffer
    """
    collector, seen = _collector()
    collector.record("gemma4:e4b", _stats())

    assert collector.finish(words=250) is None
    assert seen == []


def test_a_new_step_forgets_what_the_previous_one_left() -> None:
    """
    Given  a step that sent a request and was never finished (it raised)
    When   the next step begins
    Then   its sample holds only its own request
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.READING)
    collector.record("qwen3:4b", _stats(prompt_tokens=1500, answer_tokens=600))
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats(prompt_tokens=1000, answer_tokens=200))

    sample = collector.finish(words=250, mode="review")

    assert sample is not None
    assert sample.answer_tokens == 200
    assert seen[0][0].tag == "gemma4:e4b"


def test_a_page_sample_carries_the_tokens_of_its_image() -> None:
    """
    Given  a text recognition step that read one page
    When   the step ends
    Then   the sample reports the request's tokens as the image's, and no mode
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.READING)
    collector.record("qwen3:4b", _stats(prompt_tokens=1500, answer_tokens=600))

    sample = collector.finish(words=350)

    assert sample is not None
    assert sample.image_tokens == 1500
    assert seen[0][0].mode == ""


def test_a_writing_sample_reports_no_image_tokens() -> None:
    """
    Given  a proofreading step
    When   the step ends
    Then   the sample has no image tokens: its request carried text, not a page
    """
    collector, _seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats())

    sample = collector.finish(words=250, mode="text")

    assert sample is not None
    assert sample.image_tokens == 0


def test_a_load_over_a_second_is_reported() -> None:
    """
    Given  a request that spent three seconds loading the model
    When   the step ends
    Then   the sample carries the load time, so the run can tell that the models swap
    """
    collector, _seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats(load_s=3.0))

    sample = collector.finish(words=250, mode="review")

    assert sample is not None
    assert sample.load_s == 3.0


def test_a_short_load_is_noise() -> None:
    """
    Given  a request whose load time is below the threshold
    When   the step ends
    Then   the sample reports no load: Ollama reports a few milliseconds for a model it just used
    """
    collector, _seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats(load_s=LOAD_THRESHOLD_S / 2))

    sample = collector.finish(words=250, mode="review")

    assert sample is not None
    assert sample.load_s == 0.0


def test_a_request_without_durations_gives_no_sample() -> None:
    """
    Given  a request the server reported no time for
    When   the step ends
    Then   there is no sample: a rate of zero would only dilute the median
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", ChatStats(done_reason="stop", eval_count=200))

    assert collector.finish(words=250, mode="review") is None
    assert seen == []


def test_a_sample_holds_numbers_only() -> None:
    """
    Given  a finished step
    When   its sample is inspected
    Then   every field is a number or a stamp: no page, no file name, no text
    """
    collector, _seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats())

    sample = collector.finish(words=250, mode="review")

    assert sample is not None
    for name, value in vars(sample).items():
        assert isinstance(value, (int, float, str)), name
        if isinstance(value, str):
            assert name == "at", name


def test_a_sink_that_cannot_write_does_not_fail_the_step() -> None:
    """
    Given  a sink whose write fails with a permission error
    When   a step ends
    Then   the sample is still returned and nothing is raised: statistics never fail a document
    """

    def broken(_key: SampleKey, _sample: RunSample) -> None:
        raise PermissionError("locked")

    collector = RunStatsCollector(_SERVER, broken)
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats())

    sample = collector.finish(words=250, mode="review")

    assert sample is not None


def test_the_server_is_stored_in_its_normalised_form() -> None:
    """
    Given  a run whose server address was typed without the scheme and with a slash
    When   a step ends
    Then   the sample is filed under the normalised address the lookups ask for
    """
    seen: list[tuple[SampleKey, RunSample]] = []
    collector = RunStatsCollector(
        "127.0.0.1:11434/", lambda key, sample: seen.append((key, sample))
    )
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats())

    collector.finish(words=250, mode="review")

    assert seen[0][0].server == "http://127.0.0.1:11434"


def test_a_discarded_step_gives_no_sample() -> None:
    """
    Given  a step whose requests were recorded
    When   the step is discarded and the next one ends without requests
    Then   nothing reaches the sink, and the discarded requests do not leak into the next step
    """
    collector, seen = _collector()
    collector.begin(SpeedStep.PROOFREADING)
    collector.record("gemma4:e4b", _stats())

    collector.discard()
    collector.begin(SpeedStep.REVIEW)

    assert collector.finish(words=250) is None
    assert seen == []
