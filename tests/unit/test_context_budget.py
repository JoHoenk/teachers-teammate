"""Unit tests for the context budget: how much text fits, and which window a computer gets."""

from __future__ import annotations

import dataclasses

import pytest

from teachers_teammate.domain import context_budget
from teachers_teammate.domain.context_budget import (
    BASE_NUM_CTX,
    CONTEXT_STEPS,
    EVALUATION_ANSWER_TOKENS,
    EVALUATION_NUM_CTX_CAP,
    PROOFREADING_NUM_CTX_CAP,
    REVIEW_ANSWER_FACTOR,
    ContextBudget,
    answers_with_the_same_text,
    memory_at_context_gb,
    automatic_num_ctx,
    requested_num_ctx,
    run_windows,
    window_from_setting,
)
from teachers_teammate.domain.model_catalog import (
    CARD_USABLE_SHARE,
    FULL_SPEED_SHARE,
    OllamaModelSpec,
    find_spec,
)


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize(
    ("num_ctx", "chars", "fits"),
    [
        (8192, 4096 * 3, True),  # exactly half of the window: request and answer fit
        (8192, 4096 * 3 + 3, False),  # one character more is one token more
        (8192, 100, True),
        (16384, 8192 * 3, True),
        (16384, 8192 * 3 + 3, False),
    ],
)
def test_a_request_may_take_half_of_the_window_when_the_answer_repeats_the_text(
    num_ctx: int, chars: int, fits: bool
) -> None:
    """
    Given  a request of *chars* characters and an answer as long as the request
    When   the budget of a *num_ctx* window is asked
    Then   it fits exactly when both halves stay inside the window
    """
    budget = ContextBudget(num_ctx)

    assert budget.request_fits(chars, answer_tokens=answers_with_the_same_text(num_ctx)) is fits


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize(
    ("num_ctx", "chars", "fits"),
    [
        (8192, (8192 - EVALUATION_ANSWER_TOKENS) * 3, True),
        (8192, (8192 - EVALUATION_ANSWER_TOKENS) * 3 + 3, False),
    ],
)
def test_a_short_fixed_answer_leaves_the_rest_of_the_window_to_the_request(
    num_ctx: int, chars: int, fits: bool
) -> None:
    """
    Given  a content review, whose answer is short and fixed (1536 tokens)
    When   the budget of an 8192-token window is asked
    Then   the request may take everything except that allowance — about twice what a
           proofreading request gets
    """
    budget = ContextBudget(num_ctx)

    assert budget.request_fits(chars, answer_tokens=EVALUATION_ANSWER_TOKENS) is fits
    # A request just over half of the window still fits, because the answer is short …
    over_half = (answers_with_the_same_text(num_ctx) + 10) * 3
    assert budget.request_fits(over_half, answer_tokens=EVALUATION_ANSWER_TOKENS)
    # … and would not fit if the answer had to be as long as the text again.
    assert not budget.request_fits(over_half, answer_tokens=answers_with_the_same_text(num_ctx))


@pytest.mark.use_case("Context_Budget")
def test_the_text_budget_takes_the_prompt_out_of_the_window_before_sharing_it() -> None:
    """
    Given  a proofreading request with a 2105-character system prompt and an answer as long as
           the text
    When   the maximum text length is computed for an 8192-token window
    Then   the prompt's own tokens are subtracted first, and only the rest is shared between the
           text and its answer
    """
    budget = ContextBudget(8192)

    overhead = 2105
    overhead_tokens = -(-overhead // 3)
    assert budget.max_text_chars(overhead, answer_factor=1.0) == int(
        (8192 - overhead_tokens) / 2 * 3
    )
    assert budget.max_text_chars(2105, answer_factor=1.0) < budget.max_text_chars(
        1097, answer_factor=1.0
    )
    # A content review: a short answer, so the text gets much more room.
    assert budget.max_text_chars(1097, answer_tokens=EVALUATION_ANSWER_TOKENS) == (
        (8192 - EVALUATION_ANSWER_TOKENS) * 3 - 1097
    )


@pytest.mark.use_case("Context_Budget")
def test_a_larger_overhead_lowers_the_text_budget_by_its_own_tokens_only() -> None:
    """
    Given  two proofreading requests whose fixed part differs by 300 characters
    When   their text budgets are computed for an 8192-token window
    Then   the longer prompt costs at most its own 100 tokens, not 100 tokens of text and 100
           more of the answer that would have gone with them
    """
    budget = ContextBudget(8192)

    short = budget.max_text_chars(2_000, answer_factor=REVIEW_ANSWER_FACTOR)
    long = budget.max_text_chars(2_300, answer_factor=REVIEW_ANSWER_FACTOR)

    assert 0 < short - long <= 100 * 3


@pytest.mark.use_case("Context_Budget")
def test_the_review_budget_of_the_real_prompt_is_a_page_and_a_half() -> None:
    """
    Given  the app's own review corrector at window 8192
    When   its text budget is asked for
    Then   it is between 2,000 and 3,000 characters: a page of 200 to 400 words goes out whole,
           and a text of 3,714 characters (the length that was cut off in the smoke run) does not
    """
    from teachers_teammate.infrastructure.correction import (  # noqa: PLC0415
        NativeOllamaCorrector,
    )
    from teachers_teammate.infrastructure.ollama_utils import OllamaClient  # noqa: PLC0415

    corrector = NativeOllamaCorrector(model="m", client=OllamaClient("http://capture.invalid"))

    assert 2_000 <= corrector.max_text_chars("German") <= 3_000


@pytest.mark.use_case("Context_Budget")
def test_a_text_of_a_page_and_a_half_is_split_at_the_review_budget() -> None:
    """
    Given  a text of 2,555 characters (the German page that passed) and one of 3,714 (the one
           whose answer was cut off)
    When   both are split at the review budget of window 8192
    Then   the shorter one is one piece and the longer one is several
    """
    from teachers_teammate.domain.text_chunks import split_for_budget  # noqa: PLC0415

    budget = ContextBudget(8192).max_text_chars(2_224, answer_factor=REVIEW_ANSWER_FACTOR)
    sentence = "Der Hund läuft schnell über die Wiese. "
    short = (sentence * 70)[:2_555]
    long = (sentence * 100)[:3_714]

    assert len(split_for_budget(short, budget)) == 1
    assert len(split_for_budget(long, budget)) > 1


@pytest.mark.use_case("Context_Budget")
def test_a_budget_never_returns_a_negative_text_length() -> None:
    """
    Given  an overhead longer than the whole window
    When   the maximum text length is computed
    Then   it is 0, not a negative number
    """
    assert ContextBudget(8192).max_text_chars(100_000, answer_factor=1.0) == 0


@pytest.mark.use_case("Context_Budget")
def test_the_memory_at_a_context_window_grows_with_the_measured_rate() -> None:
    """
    Given  a model of 5 GB with a measured 1.2 GB per additional 8192 tokens, and one without
           that measurement
    When   its memory need at 8192, 16384 and 32768 tokens is computed
    Then   the measured model grows by its rate per step, the unmeasured one not at all
    """
    unmeasured = find_spec("gemma4:e4b")
    assert unmeasured is not None
    measured = dataclasses.replace(unmeasured, context_gb_per_8k=1.2)

    assert memory_at_context_gb(measured, 8192) == pytest.approx(5.0)
    assert memory_at_context_gb(measured, 16384) == pytest.approx(6.2)
    assert memory_at_context_gb(measured, 32768) == pytest.approx(8.6)
    assert memory_at_context_gb(unmeasured, 32768) == pytest.approx(5.0)
    assert memory_at_context_gb(None, 32768) is None


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize("step", CONTEXT_STEPS)
def test_the_window_grows_only_while_the_model_stays_at_full_speed(step: int) -> None:
    """
    Given  a model with a measured context growth on a card exactly large enough for *step*
    When   the proofreading window is asked
    Then   it is that step (the largest one that still fits at full speed)
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=1.0)
    need = memory_at_context_gb(spec, step)
    assert need is not None
    card = need / FULL_SPEED_SHARE

    chosen = automatic_num_ctx(
        spec, card, cap=PROOFREADING_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
    )

    assert chosen == min(step, PROOFREADING_NUM_CTX_CAP)


@pytest.mark.use_case("Context_Budget")
def test_a_window_that_would_slow_the_model_down_is_not_chosen() -> None:
    """
    Given  the same model on the largest card that still holds the 8192-token window at full
           speed, and 1 GB less
    When   the proofreading and the content-review windows are asked
    Then   both stay at 8192 — a larger window must never turn "Works" into "Slower"
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=4.0)
    base = memory_at_context_gb(spec, BASE_NUM_CTX)
    assert base is not None

    assert automatic_num_ctx(
        spec,
        base / FULL_SPEED_SHARE,
        cap=PROOFREADING_NUM_CTX_CAP,
        full_speed_share=FULL_SPEED_SHARE,
    ) == (BASE_NUM_CTX)
    assert automatic_num_ctx(
        spec, base / FULL_SPEED_SHARE, cap=EVALUATION_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
    ) == (BASE_NUM_CTX)


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize("card_gb", [None, 0.0])
def test_without_a_card_the_window_stays_at_the_base_step(card_gb: float | None) -> None:
    """
    Given  a computer without a graphics card, and a remote server (no card to see)
    When   the proofreading and the content-review windows are asked
    Then   both are the base step
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=0.5)

    assert (
        automatic_num_ctx(
            spec, card_gb, cap=PROOFREADING_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
        )
        == BASE_NUM_CTX
    )
    assert (
        automatic_num_ctx(
            spec, card_gb, cap=EVALUATION_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
        )
        == BASE_NUM_CTX
    )


@pytest.mark.use_case("Context_Budget")
def test_an_unmeasured_or_custom_model_keeps_the_base_step() -> None:
    """
    Given  a catalogued model without a measurement and a model the user added (None)
    When   a large card asks for a larger window
    Then   both stay at 8192 — nothing is guessed
    """
    unmeasured = _spec(memory_gb=4.0, context_gb_per_8k=None)

    assert (
        automatic_num_ctx(
            unmeasured, 100.0, cap=PROOFREADING_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
        )
        == BASE_NUM_CTX
    )
    assert (
        automatic_num_ctx(
            None, 100.0, cap=PROOFREADING_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE
        )
        == BASE_NUM_CTX
    )


@pytest.mark.use_case("Context_Budget")
def test_content_review_may_use_a_larger_window_than_proofreading() -> None:
    """
    Given  a model whose context growth fits a 32768-token window on the card
    When   both windows are asked, with a proofreading cap of 16384
    Then   proofreading stops at that cap and content review uses the full step

    The cap is passed explicitly: the shipped one is the base window (CW7), and this test is
    about the ladder, not about the cap the app ships.
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=0.5)
    need = memory_at_context_gb(spec, 32768)
    assert need is not None
    card = need / FULL_SPEED_SHARE

    assert automatic_num_ctx(spec, card, cap=16384, full_speed_share=FULL_SPEED_SHARE) == 16384
    assert (
        automatic_num_ctx(spec, card, cap=EVALUATION_NUM_CTX_CAP, full_speed_share=FULL_SPEED_SHARE)
        == 32768
    )


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("auto", BASE_NUM_CTX),
        ("16384", 16384),
        ("32768", 16384),  # capped for proofreading
        ("4096", BASE_NUM_CTX),  # not on the ladder → auto
        ("nonsense", BASE_NUM_CTX),
    ],
)
def test_a_fixed_setting_overrides_the_automatic_rule(setting: str, expected: int) -> None:
    """
    Given  the *proofreading_context* setting
    When   the window is resolved for a computer without a card, with a cap of 16384
    Then   a value from the ladder is used as it is (capped), anything else falls back to auto

    The cap is passed explicitly: the shipped one is the base window (CW7), and this test is
    about the setting, not about the cap the app ships.
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=0.5)

    chosen = requested_num_ctx(
        setting,
        spec=spec,
        card_gb=None,
        cap=16384,
        full_speed_share=FULL_SPEED_SHARE,
    )

    assert chosen == expected


@pytest.mark.use_case("Context_Budget")
@pytest.mark.parametrize(
    ("card_gb", "expected"),
    [
        (6.8, (BASE_NUM_CTX, BASE_NUM_CTX)),  # an 8 GB card: the larger window would slow it
        (13.6, (16384, 32768)),  # a 16 GB card holds both steps
    ],
)
def test_the_run_window_follows_the_size_of_the_graphics_card(
    monkeypatch: pytest.MonkeyPatch, card_gb: float, expected: tuple[int, int]
) -> None:
    """
    Given  a writing model whose context growth needs more room per step
    When   the windows of a run are resolved on a small and on a large card
    Then   the small card keeps the base window and the large one gets the larger steps

    The proofreading cap is raised to 16384 for this test: the shipped one is the base window
    (CW7), and this test is about the ladder, not about the cap the app ships.
    """
    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)
    spec = _spec(memory_gb=5.0, context_gb_per_8k=3.0)

    windows = run_windows(
        "auto",
        proofreading_spec=spec,
        evaluation_spec=spec,
        card_gb=card_gb,
        same_model=False,
        full_speed_share=FULL_SPEED_SHARE,
    )

    assert (windows.proofreading, windows.evaluation) == expected


@pytest.mark.use_case("Context_Budget")
def test_two_stages_that_share_a_model_use_the_smaller_window() -> None:
    """
    Given  a card on which content review would get a larger window than proofreading
    When   both stages use the same model
    Then   both use the smaller window, so Ollama does not reload the model per file
    """
    spec = _spec(memory_gb=4.0, context_gb_per_8k=0.5)

    separate = run_windows(
        "auto",
        proofreading_spec=spec,
        evaluation_spec=spec,
        card_gb=13.6,
        same_model=False,
        full_speed_share=FULL_SPEED_SHARE,
    )
    shared = run_windows(
        "auto",
        proofreading_spec=spec,
        evaluation_spec=spec,
        card_gb=13.6,
        same_model=True,
        full_speed_share=FULL_SPEED_SHARE,
    )

    assert separate.evaluation > separate.proofreading
    assert shared.proofreading == shared.evaluation == separate.proofreading


@pytest.mark.use_case("Context_Budget")
def test_a_measured_model_grows_on_a_large_card_and_not_on_a_small_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  a writing model whose context growth was measured, on an 8 GB and on a 16 GB card
    When   the windows of a run are resolved for both cards
    Then   the small card keeps 8192 and the large one proofreads at 16384, and a run that
           uses the same model for both stages gets one window for both

    The proofreading cap is raised to 16384 for this test: the shipped one is the base window
    (CW7), and this test is about the ladder, not about the cap the app ships.
    """
    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)
    spec = _spec(memory_gb=5.0, context_gb_per_8k=3.0)

    def windows(card_gb: float, *, same_model: bool = False):
        return run_windows(
            "auto",
            proofreading_spec=spec,
            evaluation_spec=spec,
            card_gb=card_gb,
            same_model=same_model,
            full_speed_share=FULL_SPEED_SHARE,
        )

    assert windows(8.0 * CARD_USABLE_SHARE).proofreading == BASE_NUM_CTX
    assert windows(16.0 * CARD_USABLE_SHARE).proofreading == 16384
    shared = windows(16.0 * CARD_USABLE_SHARE, same_model=True)
    assert shared.proofreading == shared.evaluation


def _spec(*, memory_gb: float, context_gb_per_8k: float | None) -> OllamaModelSpec:
    """Return a throwaway catalog entry with a known context growth."""
    return OllamaModelSpec(
        tag="test:1b",
        category="general",
        memory_gb=memory_gb,
        download_gb=1.0,
        note="",
        context_gb_per_8k=context_gb_per_8k,
    )


@pytest.mark.use_case("Context_Budget")
def test_each_stage_is_sized_from_its_own_model() -> None:
    """
    Given  a proofreading model whose context growth is unmeasured and a larger review model
           whose growth is measured, on a card that holds the larger window
    When   the windows of a run are resolved
    Then   proofreading keeps the base window and content review gets its own, larger one
    """
    unmeasured = _spec(memory_gb=4.0, context_gb_per_8k=None)
    measured = _spec(memory_gb=4.0, context_gb_per_8k=0.5)

    windows = run_windows(
        "auto",
        proofreading_spec=unmeasured,
        evaluation_spec=measured,
        card_gb=100.0,
        same_model=False,
        full_speed_share=FULL_SPEED_SHARE,
    )

    assert windows.proofreading == BASE_NUM_CTX
    assert windows.evaluation == EVALUATION_NUM_CTX_CAP


@pytest.mark.use_case("Context_Budget")
def test_a_review_answer_is_budgeted_longer_than_the_text() -> None:
    """
    Given  the 8192-token window
    When   the text budget is worked out for a review answer and for an answer as long as the text
    Then   the review leaves room for a longer answer, so its text budget is smaller
    """
    budget = ContextBudget(8192)

    review = budget.max_text_chars(0, answer_factor=REVIEW_ANSWER_FACTOR)
    same_length = budget.max_text_chars(0, answer_factor=1.0)

    assert REVIEW_ANSWER_FACTOR > 1.0
    assert review < same_length
    assert review == int(int(8192 / (1.0 + REVIEW_ANSWER_FACTOR)) * 3)


@pytest.mark.use_case("Context_Budget")
def test_settings_above_the_proofreading_cap_give_the_same_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Given  two pinned settings that are both above the proofreading cap
    When   the window the settings determine is worked out
    Then   both give the capped window, so they are the same cache key

    The cap is raised to 16384 for this test: the shipped one is the base window (CW7), and
    this test is about the cache key, not about the cap the app ships.
    """
    monkeypatch.setattr(context_budget, "PROOFREADING_NUM_CTX_CAP", 16384)

    assert window_from_setting("16384") == window_from_setting("32768")
    assert window_from_setting("32768") == 16384
    assert window_from_setting("auto") == BASE_NUM_CTX


@pytest.mark.use_case("Context_Budget")
def test_proofreading_is_capped_at_the_base_window() -> None:
    """
    Given  the constants the app ships
    When   the proofreading cap is read
    Then   it is the base window: a larger one changes only with a gate run at 16384, because a
           long text then goes out in one request and the answer is twice as long
    """
    assert PROOFREADING_NUM_CTX_CAP == BASE_NUM_CTX
    assert window_from_setting("16384") == BASE_NUM_CTX
    assert window_from_setting("32768") == BASE_NUM_CTX
