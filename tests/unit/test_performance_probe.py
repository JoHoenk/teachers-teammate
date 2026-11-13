"""Unit tests for teachers_teammate.infrastructure.performance_probe."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from teachers_teammate.config import OcrConfig
from teachers_teammate.domain.performance_estimate import SpeedStep, StepOutcome
from teachers_teammate.exceptions import (
    OCRDeadlineError,
    OCRError,
    OllamaUnreachableError,
    RunStoppedError,
)
from teachers_teammate.infrastructure.ollama_utils import (
    PROOFREADING_NUM_CTX,
    ChatStats,
    LoadedModel,
    OllamaClient,
    ServerFacts,
)
from teachers_teammate.infrastructure.performance_probe import (
    MEASURE_ANSWER_TOKENS,
    MEASURE_TIMEOUT_S,
    WARMUP_ANSWER_TOKENS,
    PerformanceProbe,
    SpeedTestRequest,
    draw_test_page,
    page_size_px,
    sample_page_image,
    sample_paragraph,
)

_NS = 1e9


def _stats(
    *,
    prompt_tokens: int = 1000,
    prompt_ns: int = 1_000_000_000,
    answer_tokens: int = 200,
    answer_ns: int = 2_000_000_000,
    load_ns: int = 3_000_000_000,
) -> ChatStats:
    """Return statistics that read at 1000 and write at 100 tokens per second."""
    return ChatStats(
        done_reason="stop",
        prompt_eval_count=prompt_tokens,
        eval_count=answer_tokens,
        total_duration=prompt_ns + answer_ns + load_ns,
        load_duration=load_ns,
        prompt_eval_duration=prompt_ns,
        eval_duration=answer_ns,
    )


class _FakeClient:
    """A stand-in for :class:`OllamaClient` that records the requests the probe sends.

    Loading a model evicts the others unless *keep_loaded* is set, which is what a card too
    small for both models does.
    """

    def __init__(
        self,
        *,
        installed: tuple[str, ...] = ("gemma4:e4b", "glm-ocr:q8_0"),
        loaded: tuple[str, ...] = (),
        stats: ChatStats | None = None,
        error: Exception | None = None,
        keep_loaded: bool = False,
        vram_bytes: int = 4_000_000_000,
        version: str = "0.40.15",
        unreachable: str = "",
    ) -> None:
        self.installed = list(installed)
        self._unreachable = unreachable
        self._vram_bytes = vram_bytes
        self.loaded = [_loaded(name, vram_bytes=vram_bytes) for name in loaded]
        self.calls: list[dict] = []
        self._stats = stats if stats is not None else _stats()
        self._error = error
        self._keep_loaded = keep_loaded
        self._version = version

    def server_facts(self) -> ServerFacts:
        """Return the version, the digests and what is loaded right now."""
        return ServerFacts(
            version=self._version,
            digests={name: f"sha256:{name}" for name in self.installed},
            loaded=tuple(self.loaded),
        )

    def list_models(self) -> list[str]:
        """Return the installed models."""
        return list(self.installed)

    def list_models_or_error(self) -> tuple[list[str], str]:
        """Return the installed models, or the message of a server that cannot be reached."""
        if self._unreachable:
            return [], self._unreachable
        return list(self.installed), ""

    def loaded_models(self) -> tuple[LoadedModel, ...]:
        """Return the models in memory."""
        return tuple(self.loaded)

    def chat_with_stats(self, model: str, prompt: str, **kwargs) -> tuple[str, ChatStats]:
        """Record the request, load *model* and return the scripted statistics."""
        self.calls.append({"model": model, "prompt": prompt, **kwargs})
        if self._error is not None:
            raise self._error
        if self._keep_loaded:
            if not any(item.name == model for item in self.loaded):
                self.loaded.append(_loaded(model, vram_bytes=self._vram_bytes))
        else:
            self.loaded = [_loaded(model, vram_bytes=self._vram_bytes)]
        return "answer", self._stats


def _loaded(name: str, *, vram_bytes: int = 4_000_000_000) -> LoadedModel:
    """Return a model of which *vram_bytes* sit on the graphics card."""
    return LoadedModel(name=name, size_bytes=5_000_000_000, size_vram_bytes=vram_bytes)


def _request(
    *,
    reading_model: str = "glm-ocr:q8_0",
    writing_model: str = "gemma4:e4b",
    review_model: str = "",
    num_ctx: int = PROOFREADING_NUM_CTX,
) -> SpeedTestRequest:
    """Return a request for the two models the fake server has installed."""
    return SpeedTestRequest(
        reading_model=reading_model,
        writing_model=writing_model,
        review_model=review_model,
        num_ctx=num_ctx,
        # A small page keeps the test quick; the size does not change what is measured.
        ocr=OcrConfig(pdf_render_dpi=100),
    )


def _probe(client: _FakeClient, **kwargs) -> PerformanceProbe:
    """Return a probe over the fake client (a test double for the real one)."""
    return PerformanceProbe(cast(OllamaClient, client), **kwargs)


# ── the test inputs ────────────────────────────────────────────────────────


def test_test_paragraph_falls_back_to_english() -> None:
    """
    Given  a language the test has no paragraph for
    When   sample_paragraph() is called
    Then   the English paragraph is returned, and it is not empty
    """
    assert sample_paragraph("Klingon") == sample_paragraph("English")
    assert len(sample_paragraph("English").split()) > 100


def test_test_paragraph_picks_the_batch_language() -> None:
    """
    Given  the three languages the app is used for
    When   sample_paragraph() is called for each
    Then   each returns its own paragraph
    """
    german = sample_paragraph("German")
    french = sample_paragraph("French")

    assert german != french
    assert "Museum" in german
    assert "musée" in french


def test_page_size_px_is_a4_at_the_requested_resolution() -> None:
    """
    Given  a resolution of 300 DPI
    When   page_size_px() is called
    Then   the size is an A4 page in pixels
    """
    assert page_size_px(300) == (2480, 3508)


def test_draw_test_page_writes_a_page_of_the_requested_size(tmp_path: Path) -> None:
    """
    Given  a directory to draw in
    When   draw_test_page() is called at 100 DPI
    Then   the file exists and has the pixel size of an A4 page at that resolution
    """
    from PIL import Image  # noqa: PLC0415

    drawn = draw_test_page(tmp_path / "page.png", dpi=100)

    with Image.open(drawn) as image:
        assert image.size == page_size_px(100)


def test_test_page_image_passes_the_apps_preprocessing(tmp_path: Path) -> None:
    """
    Given  the OCR settings a run uses
    When   sample_page_image() is called
    Then   the drawn page and a preprocessed copy are returned, and the copy exists
    """
    drawn, processed = sample_page_image(tmp_path, ocr=OcrConfig(pdf_render_dpi=100))

    assert drawn.exists()
    assert processed.exists()
    assert processed != drawn


# ── the order of the requests ──────────────────────────────────────────────


def test_run_warms_each_model_up_before_measuring_it() -> None:
    """
    Given  a request for a reading and a writing model
    When   the probe runs
    Then   each model is asked twice: a one-token warm-up, then the measuring request
    """
    client = _FakeClient()

    _probe(client).run(_request())

    assert [call["model"] for call in client.calls] == [
        "glm-ocr:q8_0",
        "glm-ocr:q8_0",
        "gemma4:e4b",
        "gemma4:e4b",
    ]
    assert client.calls[0]["options"] == {"num_predict": WARMUP_ANSWER_TOKENS}
    assert client.calls[2]["options"] == {"num_predict": WARMUP_ANSWER_TOKENS}


def test_run_measures_the_reading_step_before_the_writing_step() -> None:
    """
    Given  a request for both a reading and a writing model
    When   the probe runs
    Then   the reading step comes first and both steps are measured
    """
    result = _probe(_FakeClient()).run(_request())

    assert [item.step for item in result.steps] == [SpeedStep.READING, SpeedStep.PROOFREADING]
    assert all(item.outcome is StepOutcome.MEASURED for item in result.steps)


def test_run_skips_a_step_whose_model_is_not_installed() -> None:
    """
    Given  a server that does not have the reading model
    When   the probe runs
    Then   the reading step is skipped without a request, and the writing step still runs
    """
    client = _FakeClient(installed=("gemma4:e4b",))

    result = _probe(client).run(_request())

    reading = result.step(SpeedStep.READING)
    assert reading is not None
    assert reading.outcome is StepOutcome.SKIPPED
    assert [call["model"] for call in client.calls] == ["gemma4:e4b", "gemma4:e4b"]


def test_run_measures_the_review_step_only_with_its_own_model() -> None:
    """
    Given  a request whose review model differs from the proofreading one
    When   the probe runs
    Then   a third step is measured for the review model
    """
    client = _FakeClient(installed=("gemma4:e4b", "gpt-oss:20b"))

    result = _probe(client).run(
        _request(reading_model="", writing_model="gemma4:e4b", review_model="gpt-oss:20b")
    )

    assert [item.step for item in result.steps] == [
        SpeedStep.PROOFREADING,
        SpeedStep.REVIEW,
    ]
    assert result.step(SpeedStep.REVIEW).model == "gpt-oss:20b"


def test_run_does_not_measure_the_review_twice_for_the_same_model() -> None:
    """
    Given  a request whose review model is the proofreading model
    When   the probe runs
    Then   only the proofreading step is measured
    """
    result = _probe(_FakeClient()).run(
        _request(reading_model="", writing_model="gemma4:e4b", review_model="gemma4:e4b")
    )

    assert [item.step for item in result.steps] == [SpeedStep.PROOFREADING]


# ── what the requests carry ────────────────────────────────────────────────


def test_measuring_request_caps_the_answer_and_the_time() -> None:
    """
    Given  a request for a writing model
    When   the probe runs
    Then   the measuring request caps the answer at MEASURE_ANSWER_TOKENS and the time at
           MEASURE_TIMEOUT_S
    """
    client = _FakeClient()

    _probe(client).run(_request(reading_model=""))

    measuring = client.calls[1]
    assert measuring["options"]["num_predict"] == MEASURE_ANSWER_TOKENS
    assert measuring["total_timeout"] == MEASURE_TIMEOUT_S


def test_writing_request_sends_the_apps_own_options() -> None:
    """
    Given  a request for a writing model at a window of 4096
    When   the probe runs
    Then   the measuring request carries the model's temperature, the window and the schema
    """
    client = _FakeClient()

    _probe(client).run(_request(reading_model="", num_ctx=4096))

    measuring = client.calls[1]
    assert measuring["options"]["num_ctx"] == 4096
    assert 0.0 <= measuring["options"]["temperature"] <= 1.0
    assert measuring["format"] is not None
    assert measuring["system"]


def test_reading_request_sends_the_page_image_and_the_ocr_prompt() -> None:
    """
    Given  a request for a reading model
    When   the probe runs
    Then   the measuring request carries one base64 image and the OCR prompt
    """
    client = _FakeClient()

    _probe(client).run(_request(writing_model=""))

    measuring = client.calls[1]
    assert len(measuring["images"]) == 1
    assert measuring["images"][0].startswith("iVBOR")  # a PNG, base64-encoded
    assert "English" in measuring["prompt"]


# ── what the measurements say ──────────────────────────────────────────────


def test_measured_speed_is_read_off_the_token_counts() -> None:
    """
    Given  a server that reads 1000 tokens in a second and writes 200 in two
    When   the probe runs
    Then   the reading step reports 1000 tokens per second read and 100 written
    """
    result = _probe(_FakeClient()).run(_request(writing_model=""))

    speed = result.step(SpeedStep.READING).speed
    assert speed is not None
    assert speed.read_tps == pytest.approx(1000.0)
    assert speed.write_tps == pytest.approx(100.0)


def test_reading_step_records_the_tokens_of_one_page_image() -> None:
    """
    Given  a server that reports 1000 tokens of request for the page
    When   the probe runs the reading step
    Then   the measured speed carries 1000 image tokens
    """
    result = _probe(_FakeClient()).run(_request(writing_model=""))

    assert result.step(SpeedStep.READING).speed.image_tokens == 1000


def test_writing_step_records_the_characters_per_token_of_the_request() -> None:
    """
    Given  a server that reports 1000 tokens of request for the paragraph
    When   the probe runs the writing step
    Then   the measured speed carries the characters per token of that request
    """
    client = _FakeClient()

    result = _probe(client).run(_request(reading_model=""))

    speed = result.step(SpeedStep.PROOFREADING).speed
    sent = len(client.calls[1]["system"]) + len(client.calls[1]["prompt"])
    assert speed.chars_per_token == pytest.approx(sent / 1000)


def test_load_time_comes_from_the_warm_up() -> None:
    """
    Given  a server that spends three seconds loading the model
    When   the probe runs
    Then   the measured speed carries three seconds of load time
    """
    result = _probe(_FakeClient()).run(_request(writing_model=""))

    assert result.step(SpeedStep.READING).speed.load_s == pytest.approx(3.0)


def test_load_time_is_unknown_for_a_model_that_was_already_loaded() -> None:
    """
    Given  a server that already holds the reading model in memory
    When   the probe runs
    Then   the load time is None rather than a zero that would claim an instant load
    """
    client = _FakeClient(loaded=("glm-ocr:q8_0",))

    result = _probe(client).run(_request(writing_model=""))

    assert result.step(SpeedStep.READING).speed.load_s is None


def test_share_on_the_card_comes_from_the_server_afterwards() -> None:
    """
    Given  a server that holds the model mostly on the graphics card
    When   the probe runs
    Then   the measured speed carries that share
    """
    result = _probe(_FakeClient()).run(_request(writing_model=""))

    assert result.step(SpeedStep.READING).speed.on_card == pytest.approx(0.8)


# ── what the probe warns about ─────────────────────────────────────────────


def test_another_model_in_memory_marks_the_result_shared() -> None:
    """
    Given  a server that holds a model the test does not measure
    When   the probe runs
    Then   the result is marked shared, because the times may be too long
    """
    client = _FakeClient(loaded=("mistral:latest",))

    result = _probe(client).run(_request())

    assert result.shared is True


def test_a_server_holding_only_the_tested_models_is_not_shared() -> None:
    """
    Given  a server that holds one of the models the test measures
    When   the probe runs
    Then   the result is not marked shared
    """
    client = _FakeClient(loaded=("gemma4:e4b",))

    result = _probe(client).run(_request())

    assert result.shared is False


def test_evicting_the_reading_model_marks_the_result_swapping() -> None:
    """
    Given  a card too small to hold both models
    When   the probe runs the reading step and then the writing step
    Then   the result is marked swapping, because every file pays both loads
    """
    client = _FakeClient(keep_loaded=False)

    result = _probe(client).run(_request())

    assert result.swaps is True


def test_a_card_that_holds_both_models_is_not_swapping() -> None:
    """
    Given  a card large enough to hold both models
    When   the probe runs both steps
    Then   the result is not marked swapping
    """
    client = _FakeClient(keep_loaded=True)

    result = _probe(client).run(_request())

    assert result.swaps is False


def test_a_share_of_zero_on_a_computer_with_a_card_is_reported() -> None:
    """
    Given  a computer with a graphics card and a server that keeps the model in main memory
    When   the probe runs
    Then   the measured speed says the card is unused and the result knows a card was found
    """
    client = _FakeClient(vram_bytes=0)

    result = _probe(client, gpu_found=True).run(_request(writing_model=""))

    assert result.gpu_found is True
    assert result.step(SpeedStep.READING).speed.on_card == 0.0


# ── what goes wrong ────────────────────────────────────────────────────────


def test_a_request_that_hits_the_time_limit_is_too_slow() -> None:
    """
    Given  a server whose request hits the measuring time limit
    When   the probe runs
    Then   the step is reported as too slow, with the server's message
    """
    client = _FakeClient(error=OCRDeadlineError("the answer took longer than 90 s"))

    result = _probe(client).run(_request(writing_model=""))

    step = result.step(SpeedStep.READING)
    assert step.outcome is StepOutcome.TOO_SLOW
    assert "90 s" in step.message
    assert step.speed is None


def test_a_request_that_fails_for_another_reason_is_failed() -> None:
    """
    Given  a server that refuses the request
    When   the probe runs
    Then   the step is reported as failed, with the server's message
    """
    client = _FakeClient(error=OCRError("the window is too small"))

    result = _probe(client).run(_request(writing_model=""))

    step = result.step(SpeedStep.READING)
    assert step.outcome is StepOutcome.FAILED
    assert "window" in step.message


def test_an_unreachable_server_ends_the_test_with_one_message() -> None:
    """
    Given  a server that cannot be reached
    When   the probe runs
    Then   the result carries one error line and no steps
    """
    client = _FakeClient(error=OllamaUnreachableError("cannot connect to Ollama"))

    result = _probe(client).run(_request())

    assert result.steps == ()
    assert "cannot connect" in result.error


def test_a_stop_before_the_test_raises() -> None:
    """
    Given  a stop that was already requested
    When   the probe runs
    Then   RunStoppedError is raised and no request is sent
    """
    import threading  # noqa: PLC0415

    event = threading.Event()
    event.set()
    client = _FakeClient()

    with pytest.raises(RunStoppedError):
        _probe(client, cancel_event=event).run(_request())

    assert client.calls == []


def test_a_stop_during_a_request_raises() -> None:
    """
    Given  a stop that arrives while a request is waiting
    When   the probe runs
    Then   RunStoppedError is raised rather than reported as a failed step
    """
    client = _FakeClient(error=RunStoppedError())

    with pytest.raises(RunStoppedError):
        _probe(client).run(_request())


def test_a_server_that_reports_no_token_counts_fails_the_step() -> None:
    """
    Given  a server that reports no token counts at all
    When   the probe runs
    Then   the step is reported as failed rather than with a speed of zero
    """
    client = _FakeClient(stats=ChatStats())

    result = _probe(client).run(_request(writing_model=""))

    step = result.step(SpeedStep.READING)
    assert step.outcome is StepOutcome.FAILED
    assert step.speed is None


def test_the_result_carries_the_server_facts_of_the_end() -> None:
    """
    Given  a server that reports its version and digests
    When   the probe runs
    Then   the result carries them, so a stored measurement can be told apart later
    """
    result = _probe(_FakeClient()).run(_request())

    assert result.facts.version == "0.40.15"
    assert result.facts.digest_of("gemma4:e4b") == "sha256:gemma4:e4b"


def test_the_result_records_whether_the_computer_runs_on_battery() -> None:
    """
    Given  a probe told the computer runs on battery power
    When   the probe runs
    Then   the result says so, because a battery slows the card down
    """
    result = _probe(_FakeClient(), on_battery=True).run(_request())

    assert result.on_battery is True


def test_a_bare_tag_loaded_as_latest_is_not_another_model() -> None:
    """
    Given  a test of `mistral` while the server reports `mistral:latest` in memory
    When   the probe runs
    Then   the result is not marked shared: it is the same model under its full name
    """
    client = _FakeClient(installed=("mistral:latest",), loaded=("mistral:latest",))

    result = _probe(client).run(_request(reading_model="", writing_model="mistral"))

    assert result.shared is False


def test_a_server_that_cannot_be_reached_gives_an_error_and_sends_nothing() -> None:
    """
    Given  a server that cannot be reached at all
    When   the probe runs
    Then   the result carries the error without the cross, has no steps, and no request was sent
    """
    client = _FakeClient(unreachable="✗ Cannot connect to Ollama at http://x — is it running?")

    result = _probe(client).run(_request())

    assert result.error.startswith("Cannot connect to Ollama")
    assert result.steps == ()
    assert client.calls == []
