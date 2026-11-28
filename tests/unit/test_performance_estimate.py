"""Tests for the time estimate: page times, batch times, bands and expected bands."""

from __future__ import annotations

import pytest

from teachers_teammate.domain.model_catalog import (
    OLLAMA_CATALOG,
    AnswerLength,
    OllamaModelSpec,
    ReferenceSpeed,
    find_spec,
)
from teachers_teammate.domain.performance_estimate import (
    CONTENT_REVIEW_ANSWER,
    DEFAULT_CHARS_PER_TOKEN,
    DEFAULT_IMAGE_TOKENS,
    FAST_LIMIT_S,
    OK_LIMIT_S,
    SLOW_LIMIT_S,
    TYPICAL_PAGE_WORDS,
    MachineFactors,
    ModelSpeed,
    SpeedBand,
    TimeRange,
    answer_length_for,
    answer_tokens,
    band,
    batch_time,
    card_not_used,
    content_review_time,
    default_answer_length,
    expected_band,
    expected_speed,
    fit_answer_curve,
    machine_factor,
    median_factor,
    proofreading_time,
    reading_page_time,
)
from teachers_teammate.domain.proofreading.result import DEFAULT_EXCHANGE_COST, StepName

_ANSWER = AnswerLength(low=500, typical=1000, high=1500)

_ALL_STEPS: tuple[StepName, ...] = ("correct", "explain", "unclear")


def _speed(**overrides: object) -> ModelSpeed:
    """Return a measured speed of 1000 read and 100 write tokens per second."""
    values: dict[str, object] = {"read_tps": 1000.0, "write_tps": 100.0}
    values.update(overrides)
    return ModelSpeed(**values)  # ty: ignore[invalid-argument-type]


class TestReadingPageTime:
    """Given a measured reading model, When one page is estimated, Then both ends are returned."""

    def test_image_and_answer_are_added(self) -> None:
        """Given 1000 image tokens and 100 tok/s, When a page is estimated, Then 1 s + answer."""
        speed = _speed(image_tokens=1000)

        result = reading_page_time(speed, _ANSWER)

        assert result.low_s == pytest.approx(1.0 + 5.0)
        assert result.high_s == pytest.approx(1.0 + 15.0)

    def test_unmeasured_image_uses_the_default(self) -> None:
        """Given no measured image tokens, When a page is estimated, Then the default is used."""
        speed = _speed()

        result = reading_page_time(speed, _ANSWER)

        assert result.low_s == pytest.approx(DEFAULT_IMAGE_TOKENS / 1000.0 + 5.0)


class TestProofreadingTime:
    """Given a measured writing model, When a text is estimated, Then reading and writing add up."""

    def test_the_measured_answer_length_is_used(self) -> None:
        """Given the tokens a run answered with, When a text is estimated, Then its range is used."""
        speed = _speed(chars_per_token=1000.0)
        measured = AnswerLength(low=300, typical=350, high=400)

        result = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, _ALL_STEPS, answer=measured)

        # 100 words x 6 chars x 4 copies = 2400 chars = 2.4 tokens at 1000 chars per token.
        assert result.low_s == pytest.approx(0.0024 + 3.0)
        assert result.high_s == pytest.approx(0.0024 + 4.0)

    def test_without_a_measurement_the_exchange_curves_are_summed(self) -> None:
        """Given no measured length, When a text is estimated, Then the steps' answers add up."""
        speed = _speed(chars_per_token=1000.0)

        result = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, _ALL_STEPS)

        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, _ALL_STEPS, 100)
        assert result.low_s == pytest.approx(0.0024 + tokens.low / 100.0)
        assert result.high_s == pytest.approx(0.0024 + tokens.high / 100.0)

    def test_every_step_reads_the_text_again(self) -> None:
        """Given the three steps, When a text is estimated, Then step ② reads it twice."""
        speed = _speed(chars_per_token=1000.0)

        full = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, _ALL_STEPS)
        once = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, ("correct",))

        assert full.low_s > once.low_s

    def test_corrections_only_reads_the_text_once(self) -> None:
        """Given step ① alone, When a text is estimated, Then one copy and one answer count."""
        speed = _speed(chars_per_token=1000.0)

        result = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, ("correct",))

        # 600 chars = 0.6 tokens of request; step ① answers with its own curve.
        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, ("correct",), 100)
        assert result.low_s == pytest.approx(0.0006 + tokens.low / 100.0)
        assert result.high_s == pytest.approx(0.0006 + tokens.high / 100.0)

    def test_overhead_characters_are_read_too(self) -> None:
        """Given fixed instructions, When a text is estimated, Then their tokens are read."""
        speed = _speed(chars_per_token=1000.0)

        result = proofreading_time(
            speed, 0, DEFAULT_EXCHANGE_COST, ("correct",), overhead_chars=2000
        )

        # 2000 chars at 1000 chars per token = 2 tokens, read at 1000 tokens per second; the
        # answer of step ① is there whatever the text's length.
        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, ("correct",), 0)
        assert result.low_s == pytest.approx(0.002 + tokens.low / 100.0)

    def test_unmeasured_characters_per_token_uses_the_default(self) -> None:
        """Given no measured characters per token, When estimated, Then the default applies."""
        speed = _speed()

        result = proofreading_time(speed, 100, DEFAULT_EXCHANGE_COST, ("correct",))

        expected_read = 600 / DEFAULT_CHARS_PER_TOKEN / 1000.0
        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, ("correct",), 100)
        assert result.low_s == pytest.approx(expected_read + tokens.low / 100.0)


class TestAnswerTokens:
    """Given the exchange's curves, When a text is summed, Then a part per text and a part per word."""

    def test_the_three_answers_of_a_page_are_the_measured_ones(self) -> None:
        """Given a page of 250 words, When summed, Then the answers are 660 tokens per 100 words."""
        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, _ALL_STEPS, TYPICAL_PAGE_WORDS)

        assert tokens.typical == 1650
        assert tokens.typical / TYPICAL_PAGE_WORDS * 100 == pytest.approx(660, abs=1)

    def test_a_short_text_does_not_pay_the_page_rate(self) -> None:
        """Given 100 and 500 words, When summed, Then fewer words cost more per 100 words."""
        short = answer_tokens(DEFAULT_EXCHANGE_COST, _ALL_STEPS, 100).typical
        long = answer_tokens(DEFAULT_EXCHANGE_COST, _ALL_STEPS, 500).typical

        assert short / 100 > long / 500
        # The fixed part is paid once per text, so 100 words cost more than a fifth of 500.
        assert short > long / 5

    def test_a_step_that_is_not_sent_adds_nothing(self) -> None:
        """Given step ① alone, When summed, Then only its own curve counts."""
        tokens = answer_tokens(DEFAULT_EXCHANGE_COST, ("correct",), 100)

        assert tokens == AnswerLength(low=117, typical=136, high=154)


class TestFitAnswerCurve:
    """Given the answer lengths a run measured, When they are fitted, Then a line through them."""

    def test_the_line_goes_through_the_runs(self) -> None:
        """Given answers of 450 + 4.8 per word, When fitted, Then that line comes back."""
        curve = fit_answer_curve([(250, 1650), (500, 2850), (125, 1050)])

        assert curve is not None
        assert curve.fixed == pytest.approx(450)
        assert curve.per_word == pytest.approx(4.8)
        assert curve.low == pytest.approx(1.0)
        assert curve.high == pytest.approx(1.0)

    def test_one_text_length_says_nothing_about_the_fixed_part(self) -> None:
        """Given answers of one length only, When fitted, Then there is no curve."""
        assert fit_answer_curve([(250, 1650), (250, 1700)]) is None
        assert fit_answer_curve([(250, 1650)]) is None
        assert fit_answer_curve([]) is None

    def test_the_range_is_what_the_runs_answered_against_the_line(self) -> None:
        """Given one run far above its line, When fitted, Then the range reaches it."""
        curve = fit_answer_curve([(100, 1000), (300, 2000), (500, 4200)])

        assert curve is not None
        assert curve.per_word == pytest.approx(8)
        assert curve.low == pytest.approx(5 / 6, abs=0.001)
        assert curve.high == pytest.approx(1.25, abs=0.001)

    def test_a_line_below_zero_goes_through_zero_instead(self) -> None:
        """Given answers whose line crosses zero, When fitted, Then it keeps a part per word."""
        curve = fit_answer_curve([(180, 220), (260, 330), (340, 450)])

        assert curve is not None
        assert curve.fixed == 0.0
        assert curve.per_word == pytest.approx(
            (180 * 220 + 260 * 330 + 340 * 450) / (180**2 + 260**2 + 340**2)
        )
        assert curve.tokens(600) > curve.tokens(100) * 5

    def test_answers_that_shrink_with_the_text_keep_the_flat_middle(self) -> None:
        """Given longer texts with shorter answers, When fitted, Then no part per word."""
        curve = fit_answer_curve([(100, 600), (300, 400), (500, 200)])

        assert curve is not None
        assert curve.per_word == 0.0
        assert curve.fixed == pytest.approx(400)


class TestContentReviewTime:
    """Given a measured model, When a content review is estimated, Then its own constant applies."""

    def test_uses_the_content_review_constant(self) -> None:
        """Given 100 words, When a review is estimated, Then CONTENT_REVIEW_ANSWER applies."""
        speed = _speed(chars_per_token=1000.0)

        result = content_review_time(speed, 100)

        assert result.low_s == pytest.approx(0.0006 + CONTENT_REVIEW_ANSWER.low / 100.0)
        assert result.high_s == pytest.approx(0.0006 + CONTENT_REVIEW_ANSWER.high / 100.0)


class TestBatchTime:
    """Given per-page step times, When a run is estimated, Then files and pages multiply."""

    def test_steps_multiply_with_pages_and_files(self) -> None:
        """Given two steps of 10 s and 20 s, When 3 files of 2 pages, Then 180 s."""
        result = batch_time(3, 2, [TimeRange(10.0, 10.0), TimeRange(20.0, 20.0)])

        assert result.low_s == pytest.approx(180.0)
        assert result.high_s == pytest.approx(180.0)

    def test_swaps_add_every_load_time_per_file(self) -> None:
        """Given two models that swap, When 3 files, Then each file pays both load times."""
        result = batch_time(3, 1, [TimeRange(10.0, 10.0)], swaps=True, load_s=[5.0, 7.0])

        assert result.low_s == pytest.approx(3 * (10.0 + 12.0))

    def test_load_times_are_ignored_without_swaps(self) -> None:
        """Given load times but no swaps, When estimated, Then the load times are not counted."""
        result = batch_time(2, 1, [TimeRange(10.0, 10.0)], load_s=[5.0])

        assert result.low_s == pytest.approx(20.0)

    def test_no_files_gives_no_time(self) -> None:
        """Given no files, When a run is estimated, Then the range is empty."""
        result = batch_time(0, 2, [TimeRange(10.0, 20.0)])

        assert result == TimeRange(0.0, 0.0)

    def test_a_further_page_pays_only_its_own_part(self) -> None:
        """Given 30 s for a first page and 10 s for each further, When 2 files of 3, Then 100 s."""
        result = batch_time(2, 3, [TimeRange(30.0, 30.0)], further=[TimeRange(10.0, 10.0)])

        assert result == TimeRange(100.0, 100.0)


class TestBand:
    """Given seconds per page, When the band is asked for, Then the limits are inclusive."""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, SpeedBand.FAST),
            (FAST_LIMIT_S, SpeedBand.FAST),
            (FAST_LIMIT_S + 0.1, SpeedBand.OK),
            (OK_LIMIT_S, SpeedBand.OK),
            (OK_LIMIT_S + 0.1, SpeedBand.SLOW),
            (SLOW_LIMIT_S, SpeedBand.SLOW),
            (SLOW_LIMIT_S + 0.1, SpeedBand.VERY_SLOW),
        ],
    )
    def test_limits(self, seconds: float, expected: SpeedBand) -> None:
        """Given a number of seconds, When banded, Then the documented limit decides."""
        assert band(seconds) is expected


class TestCardNotUsed:
    """Given a share on the card, When the flag is asked for, Then only a zero counts."""

    def test_zero_share_with_a_card_found(self) -> None:
        """Given a share of 0 and a card, When asked, Then the card was not used."""
        assert card_not_used(0.0, gpu_found=True) is True

    def test_zero_share_without_a_card(self) -> None:
        """Given a share of 0 and no card, When asked, Then nothing is wrong."""
        assert card_not_used(0.0, gpu_found=False) is False

    @pytest.mark.parametrize("share", [0.5, 0.97, 1.0, None])
    def test_other_shares_are_no_evidence(self, share: float | None) -> None:
        """Given a share between 0 and 1 or none, When asked, Then it is only a hint."""
        assert card_not_used(share, gpu_found=True) is False


class TestAnswerLengths:
    """Given the catalog, When an answer length is needed, Then the median of its kind serves."""

    def test_measured_model_keeps_its_own(self) -> None:
        """Given a catalogued model with a figure, When asked, Then its own figure is returned."""
        spec = find_spec("gemma4:e4b")

        assert spec is not None
        assert answer_length_for(spec, "general") == spec.answer_length

    def test_unmeasured_model_gets_the_median_of_its_kind(self) -> None:
        """Given a model without a figure, When asked, Then the median of its kind is returned."""
        spec = find_spec("mistral:latest")

        assert spec is not None
        assert spec.answer_length is None
        assert answer_length_for(spec, "general") == default_answer_length("general")

    def test_median_is_between_the_measured_figures(self) -> None:
        """Given the catalogued writing models, When the median is taken, Then it lies inside."""
        measured = [
            spec.answer_length
            for spec in OLLAMA_CATALOG
            if spec.category == "general" and spec.answer_length is not None
        ]

        result = default_answer_length("general")

        assert min(item.low for item in measured) <= result.low
        assert result.low <= result.typical <= result.high
        assert result.high <= max(item.high for item in measured)

    def test_reading_models_fall_back_to_a_constant(self) -> None:
        """Given no measured reading model, When asked, Then the reading constant is returned."""
        assert default_answer_length("ocr").typical > 0


class TestMachineFactor:
    """Given a measured and a reference rate, When the factor is asked for, Then it is a ratio."""

    def test_half_speed(self) -> None:
        """Given 48 tok/s here and 96 on the reference machine, When asked, Then 0.5."""
        assert machine_factor(48.0, 96.0) == pytest.approx(0.5)

    @pytest.mark.parametrize(("measured", "reference"), [(0.0, 96.0), (48.0, 0.0)])
    def test_missing_figure_gives_none(self, measured: float, reference: float) -> None:
        """Given a zero on either side, When asked, Then the factor is unknown."""
        assert machine_factor(measured, reference) is None

    def test_median_of_several(self) -> None:
        """Given three factors, When the median is taken, Then the middle one wins."""
        assert median_factor([0.4, 0.5, 0.9]) == pytest.approx(0.5)

    def test_median_of_none(self) -> None:
        """Given no factor, When the median is taken, Then it is unknown."""
        assert median_factor([]) is None


class TestMachineFactors:
    """Given a factor per rate, When one is asked for a task, Then the right one is used."""

    def test_a_reading_model_uses_the_reading_factor(self) -> None:
        """Given both factors, When a reading model is rated, Then the reading one counts."""
        factors = MachineFactors(writing=0.5, reading=0.25)

        assert factors.for_category("ocr") == pytest.approx(0.25)

    def test_a_writing_model_uses_the_writing_factor(self) -> None:
        """Given both factors, When a writing model is rated, Then the writing one counts."""
        factors = MachineFactors(writing=0.5, reading=0.25)

        assert factors.for_category("general") == pytest.approx(0.5)

    def test_without_a_reading_factor_the_writing_one_serves(self) -> None:
        """Given no measured reading model, When a reading model is rated, Then writing serves."""
        factors = MachineFactors(writing=0.5)

        assert factors.for_category("ocr") == pytest.approx(0.5)

    def test_without_any_factor_there_is_none(self) -> None:
        """Given nothing measured here, When a model is rated, Then there is no factor."""
        assert MachineFactors().for_category("ocr") is None
        assert MachineFactors().for_category("general") is None


class TestExpectedBand:
    """Given a reference speed and a factor, When a band is asked for, Then it is a band."""

    def _spec(self, **overrides: object) -> OllamaModelSpec:
        values: dict[str, object] = {
            "tag": "test:1b",
            "category": "general",
            "memory_gb": 4.0,
            "download_gb": 2.0,
            "note": "",
            "answer_length": AnswerLength(low=500, typical=1000, high=1500),
            "reference_speed": ReferenceSpeed(write_tps=100.0, read_tps=1000.0),
        }
        values.update(overrides)
        return OllamaModelSpec(**values)  # ty: ignore[invalid-argument-type]

    def test_expected_speed_scales_both_rates(self) -> None:
        """Given a factor of 0.5, When the expected speed is asked for, Then both rates halve."""
        speed = expected_speed(self._spec(), 0.5)

        assert speed is not None
        assert speed.write_tps == pytest.approx(50.0)
        assert speed.read_tps == pytest.approx(500.0)

    def test_reading_rate_falls_back_to_the_writing_rate(self) -> None:
        """Given a reference speed without a reading rate, When asked, Then writing serves."""
        spec = self._spec(reference_speed=ReferenceSpeed(write_tps=100.0))

        speed = expected_speed(spec, 1.0)

        assert speed is not None
        assert speed.read_tps == pytest.approx(100.0)

    def test_no_reference_speed_gives_no_band(self) -> None:
        """Given a model nobody measured, When a band is asked for, Then there is none."""
        assert expected_band(self._spec(reference_speed=None), 0.5) is None

    def test_no_factor_gives_no_band(self) -> None:
        """Given no measured model on this computer, When a band is asked for, Then none."""
        assert expected_band(self._spec(), None) is None

    def test_a_slow_machine_gives_a_slower_band(self) -> None:
        """Given a factor of 0.05, When a band is asked for, Then it is worse than at 1.0."""
        spec = self._spec()

        fast = expected_band(spec, 1.0)
        slow = expected_band(spec, 0.05)

        assert fast is not None and slow is not None
        assert list(SpeedBand).index(slow) > list(SpeedBand).index(fast)

    def test_reading_model_uses_the_page_formula(self) -> None:
        """Given a reading model, When a band is asked for, Then the page formula applies."""
        spec = self._spec(category="ocr")

        assert expected_band(spec, 1.0) is not None
